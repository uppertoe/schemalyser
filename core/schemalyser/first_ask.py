"""The first ask: one query of names and sizes, so that a project can start without a catalogue export.

Without a catalogue, the page reads the chosen SQL files only to list the names of the tables that they read
in FROM and JOIN, and writes one query for the analyst: every column of each of those tables that exists, in
the nine fields of the catalogue query, with the size of the table from SQL Server's own records, rounded
down to ten. The query reads only the server's own records and never a table.

This is the one place where a string from a request reaches an output, so it is held tightly. A name is
used only if it is a plain name by the catalogue's own rule; temporary tables, table variables, common table
expressions, aliases, and anything in a comment or a string are never names of tables to the parser and are
left out; at most MAXIMUM_NAMES names are used; and the query exists only on the page, for the person who
already holds the SQL. It is never written into any file, other than by the user's own copy. Once its result
is pasted, every later output takes its names from that result, which is read as a catalogue under the
catalogue's own rules.
"""
import csv
import io
import re
from collections import Counter

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from .catalogue import NAME, QUERY_ORDER
from .extract import _is_base_table, _names_something_else
from .statements import drop_old_hints
from .translate import OMOP_SCHEMA

# The most names that the first query asks about. Above it, the names that the most files read are kept,
# with ties in alphabetical order, and the page says how many were left out.
MAXIMUM_NAMES = 500
SIZE_COLUMN = "TABLE_ROWS"
LAYOUT = QUERY_ORDER + (SIZE_COLUMN,)
WIDTH = 100
WORDING = {
    "comment": "This is the tables and columns query. It lists every column of each table below that exists, with the "
               "number of rows in the table from SQL Server's own records, rounded down to the nearest ten. It reads only "
               "the server's own records and no row of any table. The database analyst runs it, selects the whole results grid, copies "
               "it with its headers, and pastes it into the box below the query on the page.",
}


# The data dictionary query: every column of every table and view in the database, in the ten fields of the tables and
# columns query, then whether the column is in its table's primary key and the description that the database holds for
# it. Its result is the data dictionary made from the database, which the dictionary loader reads by these headings.
KEY_COLUMN = "IS_PRIMARY_KEY"
DESCRIPTION_COLUMN = "DESCRIPTION"
DATABASE_LAYOUT = LAYOUT + (KEY_COLUMN, DESCRIPTION_COLUMN)
DATABASE_WORDING = {
    "comment": "This is the data dictionary query. It lists every column of every table and view in this database, with "
               "its data type, whether it is part of its table's primary key, the number of rows in the table from SQL "
               "Server's own records, rounded down to the nearest ten, and the description that the database holds for "
               "the column, which is empty where it holds none. It reads only the database's own records of its tables "
               "and never a row of any table. It may take a minute on a large database, and it needs no special "
               "permission beyond reading the database.",
}


def database_query():
    """The data dictionary query, as T-SQL text. It names no table of the hospital's and takes no input."""
    import textwrap
    lines = [f"-- {line}" for line in textwrap.wrap(DATABASE_WORDING["comment"], WIDTH - 3)]
    named = "OBJECT_ID(QUOTENAME(c.TABLE_SCHEMA) + N'.' + QUOTENAME(c.TABLE_NAME))"
    lines += [
        "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.ORDINAL_POSITION, c.DATA_TYPE,",
        "       c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE,",
        f"       (s.row_count / 10) * 10 AS {SIZE_COLUMN},",
        f"       CASE WHEN k.COLUMN_NAME IS NULL THEN 'NO' ELSE 'YES' END AS {KEY_COLUMN},",
        "       REPLACE(REPLACE(REPLACE(CAST(e.value AS nvarchar(4000)), CHAR(13), N' '), CHAR(10), N' '), CHAR(9), N' ')",
        f"           AS {DESCRIPTION_COLUMN}",
        "FROM INFORMATION_SCHEMA.COLUMNS AS c",
        "LEFT JOIN (SELECT p.object_id, SUM(p.rows) AS row_count",
        "           FROM sys.partitions AS p",
        "           WHERE p.index_id IN (0, 1)",
        "           GROUP BY p.object_id) AS s",
        f"       ON s.object_id = {named}",
        "LEFT JOIN (SELECT u.TABLE_SCHEMA, u.TABLE_NAME, u.COLUMN_NAME",
        "           FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS t",
        "           JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS u",
        "             ON u.CONSTRAINT_SCHEMA = t.CONSTRAINT_SCHEMA AND u.CONSTRAINT_NAME = t.CONSTRAINT_NAME",
        "            AND u.TABLE_SCHEMA = t.TABLE_SCHEMA AND u.TABLE_NAME = t.TABLE_NAME",
        "           WHERE t.CONSTRAINT_TYPE = 'PRIMARY KEY') AS k",
        "       ON k.TABLE_SCHEMA = c.TABLE_SCHEMA AND k.TABLE_NAME = c.TABLE_NAME AND k.COLUMN_NAME = c.COLUMN_NAME",
        "LEFT JOIN sys.extended_properties AS e",
        "       ON e.class = 1 AND e.name = N'MS_Description'",
        f"      AND e.major_id = {named}",
        f"      AND e.minor_id = COLUMNPROPERTY({named}, c.COLUMN_NAME, 'ColumnId')",
        "ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION;",
    ]
    return "\n".join(lines)


def database_rows(text):
    """The rows of the data dictionary query's result, each as the twelve values of DATABASE_LAYOUT in that order.

    The result may be pasted from the results grid, which separates its values by tabs, or saved as a file, as a CSV
    or with tabs. The first row must be the headings, in any order. SQL Server Management Studio does not quote a value
    that holds a comma when it saves a CSV, so a row with more values than headings has its extra values joined back
    into the description, which is the last column the query returns. NULL is read as empty, and the lines that SQL
    Server adds about the rows affected and the time of completion are left out. Raises FirstAskError.
    """
    text = (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        raise FirstAskError("no rows")
    delimiter = "\t" if "\t" in lines[0] else ","
    if delimiter == "\t":
        rows = [line.split("\t") for line in lines]
    else:
        rows = list(csv.reader(io.StringIO("\n".join(lines))))
    head = [cell.strip().upper() for cell in rows[0]]
    if not set(DATABASE_LAYOUT) <= set(head):
        raise FirstAskError("headings")
    at = [head.index(name) for name in DATABASE_LAYOUT]
    last = head.index(DESCRIPTION_COLUMN) == len(head) - 1
    kept = []
    for row in rows[1:]:
        if len(row) > len(head) and last:
            row = row[:len(head) - 1] + [delimiter.join(row[len(head) - 1:])]
        cells = [cell.strip() for cell in row]
        if len(cells) == 1 and re.fullmatch(r"\(\d+ rows? affected\)|Completion time:.*", cells[0]):
            continue
        if all(re.fullmatch(r"-*", cell) for cell in cells):
            continue    # the line that some of SQL Server's tools print under the headings
        cells += [""] * (len(head) - len(cells))
        kept.append(["" if cells[i] == "NULL" else cells[i] for i in at])
    if not kept:
        raise FirstAskError("no rows")
    return kept


def _plain(name):
    return isinstance(name, str) and len(name) <= 128 and bool(NAME.match(name)) and "\n" not in name


def names_in(sql):
    """The plain names of the stored tables that one request reads in FROM and JOIN, as a set.

    The parser reads comments and strings as such, so nothing in them is ever a table. A temporary table,
    a table variable, a common table expression and an alias are left out, as is a name that is not plain.
    """
    try:
        statements = sqlglot.parse(drop_old_hints(sql), read="tsql", error_level=ErrorLevel.IGNORE)
    except (SqlglotError, RecursionError, ValueError):
        return set()
    found = set()
    for statement in statements:
        if statement is None:
            continue
        ctes = {cte.alias.upper() for cte in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            if not isinstance(table.parent, (exp.From, exp.Join)) or _names_something_else(table):
                continue
            if not _is_base_table(table) or table.name.startswith("@") or table.name.upper() in ctes:
                continue
            if (table.db or "").lower() == OMOP_SCHEMA:
                continue    # a conversion step's reading of an OMOP table, which is no source table
            if _plain(table.name):
                found.add(table.name)
    return found


class Names:
    """The table names of a set of requests, counted by the files that read each one."""

    def __init__(self):
        self.files = Counter()
        self.spelling = {}

    def add(self, sql):
        for name in names_in(sql):
            self.spelling.setdefault(name.upper(), name)
            self.files[name.upper()] += 1

    def chosen(self):
        """The names to ask about, at most MAXIMUM_NAMES, and the number left out above the cap."""
        ranked = sorted(self.files, key=lambda key: (-self.files[key], key))
        kept = sorted((self.spelling[key] for key in ranked[:MAXIMUM_NAMES]), key=str.upper)
        return kept, max(0, len(ranked) - MAXIMUM_NAMES)


def doubt(asked, held, rules=None):
    """Whether the result suggests the wrong database, the wrong schema or a login with narrow rights.

    "most" when more than half of the tables asked about did not come back; "lookups" when the site rules name
    lookup tables among those asked about and none of them came back; otherwise "". Names are compared without case.
    """
    asked = {str(n).upper() for n in asked}
    held = {str(n).upper() for n in held}
    if not asked:
        return ""
    if len(asked - held) * 2 > len(asked):
        return "most"
    lookups = {str(k.get("definitionTable", "")).upper() for k in getattr(rules, "definition_keys", None) or []
               if isinstance(k, dict)} & asked
    if lookups and not lookups & held:
        return "lookups"
    return ""


def query(names):
    """The first query, for a literal list of plain names, as T-SQL text. Any name that is not plain is dropped."""
    import textwrap
    names = sorted({n for n in names if _plain(n)}, key=str.upper)[:MAXIMUM_NAMES]
    if not names:
        return ""
    lines = [f"-- {line}" for line in textwrap.wrap(WORDING["comment"], WIDTH - 3)]
    lines += ["SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.ORDINAL_POSITION, c.DATA_TYPE,",
              "       c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE,",
              "       (SELECT (SUM(p.rows) / 10) * 10",
              "        FROM sys.partitions AS p",
              "        WHERE p.object_id = OBJECT_ID(QUOTENAME(c.TABLE_SCHEMA) + N'.' + QUOTENAME(c.TABLE_NAME))",
              f"          AND p.index_id IN (0, 1)) AS {SIZE_COLUMN}",
              "FROM INFORMATION_SCHEMA.COLUMNS AS c"]
    quoted = ["N'" + n.replace("'", "''") + "'" for n in names]
    rows, line = [], "WHERE c.TABLE_NAME IN ("
    for i, item in enumerate(quoted):
        piece = item + ("," if i < len(quoted) - 1 else ")")
        if len(line) + len(piece) + 1 > WIDTH and line.strip() not in ("WHERE c.TABLE_NAME IN (",):
            rows.append(line.rstrip())
            line = " " * 23
        line += piece + " "
    rows.append(line.rstrip())
    lines += rows
    lines.append("ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION;")
    return "\n".join(lines)


class FirstAskError(ValueError):
    """The pasted text is not the result of the first query."""


def _rows(text):
    text = (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "\t" in text:
        rows = [[cell.strip() for cell in line.split("\t")] for line in text.split("\n")]
    else:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]
    kept = []
    for row in rows:
        if not row or not any(row) or (len(row) == 1 and re.fullmatch(r"\(\d+ rows? affected\)", row[0])):
            continue
        if tuple(cell.upper() for cell in row) == LAYOUT or all(re.fullmatch(r"-*", cell) for cell in row):
            continue    # a header row, or the line that sqlcmd prints under it
        if len(row) != len(LAYOUT):
            raise FirstAskError("a row does not have ten values")
        kept.append(["" if cell == "NULL" else cell for cell in row])
    if not kept:
        raise FirstAskError("no rows")
    return kept
