"""The first ask: one query of names and sizes, so that a project can start without a catalogue export.

Without a catalogue, the page reads the chosen SQL files only to list the names of the tables that they read
in FROM and JOIN, and writes one query for the analyst: every column of each of those tables that exists, in
the nine fields of the catalogue query, with the size of the table from SQL Server's own records, rounded
down to ten. The query reads only the server's own records and never a table.

This is the one place where a string from a request reaches an output, so it is held tightly. A name is
used only if it is a plain name by the catalogue's own rule; temporary tables, table variables, common table
expressions, aliases, and anything in a comment or a string are never names of tables to the parser and are
left out; at most MAXIMUM_NAMES names are used; and the query exists only on the page, for the person who
already holds the SQL. It is never written into the inventory pack, the boundary's outputs, the provenance
or any file, other than by the user's own copy. Once its result is pasted, every later output takes its
names from that result, which is read as a catalogue and as check results under their own rules.
"""
import csv
import io
import re
from collections import Counter

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from .catalogue import NAME, QUERY_ORDER, Catalogue, CatalogueError
from .checks import LAYOUT as CHECKS_LAYOUT, Checks, ChecksError
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
    "comment": "This query lists every column of each table below that exists, with the size of the table from "
               "SQL Server's own records, rounded down to the nearest ten. It reads only the server's own records "
               "and no table. Please run it, copy the whole results grid with its headers, and paste it into the page.",
}


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


def read(text, rules, earlier_checks=None):
    """Reads the pasted result of the first query, as (catalogue text, check results text, facts).

    The first nine values of each row are read as the catalogue, under the catalogue's own rules, and the
    sizes as rows results, under the rules of the check results; earlier check results, where given as text,
    are kept, with these sizes in place of theirs. Raises FirstAskError when the text is not the result.
    """
    rows = _rows(text)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(QUERY_ORDER)
    writer.writerows(row[:len(QUERY_ORDER)] for row in rows)
    catalogue_text = out.getvalue()
    try:
        catalogue = Catalogue.from_csv(catalogue_text)
    except CatalogueError as error:
        raise FirstAskError("the catalogue fields could not be read") from error
    if not list(catalogue.tables()):
        raise FirstAskError("no table could be accepted")
    sizes = {}
    for row in rows:
        table, count = row[1], row[-1]
        if catalogue.table(table) is not None and count.isdecimal():
            sizes[catalogue.table(table).name] = int(count)
    checks_out = io.StringIO()
    writer = csv.writer(checks_out, lineterminator="\n")
    writer.writerow(CHECKS_LAYOUT)
    writer.writerows(["rows", table, "", "", "", count, "", "", ""] for table, count in sorted(sizes.items()))
    # A table whose size came back empty has no record of its size that this account can read, as for a view, so
    # the table sizes query would add nothing: it is recorded as unrecorded at once, and counted only up to a limit.
    unsized = sorted({entry.name for entry in catalogue.tables()} - set(sizes))
    writer.writerows(["skipped", table, "", "rows", "unrecorded", "", "", "", ""] for table in unsized)
    try:
        checks = Checks.from_csv(checks_out.getvalue(), catalogue, rules)
        if earlier_checks:
            checks = Checks.from_csv(earlier_checks, catalogue, rules).merged(checks)
    except ChecksError as error:
        raise FirstAskError("the sizes could not be read") from error
    facts = {"tables": len(list(catalogue.tables())), "columns": sum(len(t.columns) for t in catalogue.tables()),
             "sized": len(checks.rows), "rows": len(rows)}
    return catalogue_text, checks.to_csv(), facts
