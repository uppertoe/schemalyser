"""The checks: questions about the database that only someone with access can answer.

The tool plans the checks from its findings, writes them as one T-SQL script, and reads the
results back from a CSV file in a fixed layout. Every check returns counts. A value is listed
only for a short text or whole-number column with few distinct values, and only when enough rows
hold it. The script is built from catalogue names and fixed text, so it carries nothing from the
requests. The same rules are applied again when the results are read, so that a results file
cannot bring in anything the script would not have returned.

Each check also has a plain form, Check.plain: one static SELECT that the checklist offers beside the
item it answers, safe by construction, whose result can be pasted back and read under the same rules.
size_query reads the size of each table from the server's own records, and offer decides from those
sizes whether a check is offered exactly, through a sample, or not at all.
"""
import csv
import io
import re
from dataclasses import dataclass

from . import roles as meanings
from decimal import Decimal
from fnmatch import fnmatchcase

LAYOUT = ("check_kind", "table_name", "column_name", "value", "label",
          "row_count", "distinct_count", "null_count", "is_unique")
KINDS = ("column", "rows", "values", "years", "spans", "fanout", "skipped", "sampled", "ran", "error")
# The kinds of check whose plain query can find nothing. Each such query adds one constant row of the kind ran,
# so that a query that has run and found nothing is recorded as asked and answered with nothing.
RAN_KINDS = ("values", "years", "spans", "fanout")
# The kinds of check that the script runs, in the order in which it runs them: the cheapest and the most
# useful first, so that the time allowed is spent on them, and the two that must read every row of a
# table last.
RUN_ORDER = ("rows", "values", "spans", "years", "column", "fanout")
# The script is sparing with a large table. It reads a sample of one for the kinds of check that a sample
# can answer, and leaves out the kinds that need every row. The analyst can change the three numbers.
LARGE_TABLE_ROWS = 100_000_000
SAMPLE_PERCENT = 1
MINUTES_ALLOWED = 30
SAMPLED_KINDS = ("values", "years", "spans")
# The reasons for which a check is recorded as skipped: the script's time ran out, the table is large, or
# (from the plain size query) the server keeps no record of the table's size, as for a view.
UNRECORDED = "unrecorded"
SKIPPED_REASONS = ("time", "size", UNRECORDED)
# The plain form of each check, which the checklist offers one at a time. A table with more rows than
# PLAIN_EXACT_ROWS is large: a values, years or spans check reads a sample of it of about PLAIN_SAMPLE_ROWS
# rows, and a column or fanout check, which needs every row, is not offered at all.
PLAIN_EXACT_ROWS = 10_000_000
PLAIN_SAMPLE_ROWS = 5_000_000
# The seed of every sample, so that two reads of a sample within one query read the same pages.
SAMPLE_SEED = 20261005
# The states in which the checklist offers a check: ready as an exact query or a sampled one, waiting for the
# size of a table, waiting for a count of a table whose size the server does not record, or not offered
# because the table is large.
OFFER_STATES = ("exact", "sampled", "sizes", "count", "large", "unsampled")
# The place of the sample's percentage in a statement. T-SQL takes no variable there, so the script
# writes the number in before it runs the statement.
PERCENT = "<<percent>>"
MINIMUM_COUNT = 10
MAXIMUM_VALUES = 200
MAXIMUM_DEFINITIONS = 5000
MAXIMUM_TEXT_LENGTH = 70
MAXIMUM_LABEL_LENGTH = 200
VALUE_OPERATORS = ("=", "<>", "IN", "NOT IN")
VALUE_KINDS = ("number", "string")
WHOLE_NUMBER_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BIT"}
SCALED_NUMBER_TYPES = {"NUMERIC", "DECIMAL"}
OTHER_NUMBER_TYPES = {"FLOAT", "REAL", "MONEY", "SMALLMONEY", "DOUBLE"}
TEXT_TYPES = {"CHAR", "VARCHAR", "NCHAR", "NVARCHAR"}
DATE_TYPES = {"DATE", "DATETIME", "DATETIME2", "SMALLDATETIME", "DATETIMEOFFSET"}
# Types that SQL Server cannot group or count distinctly.
UNCOUNTABLE_TYPES = {"TEXT", "NTEXT", "IMAGE", "XML", "VARBINARY", "BINARY", "SQL_VARIANT", "GEOGRAPHY",
                     "GEOMETRY", "HIERARCHYID", "TIMESTAMP", "ROWVERSION"}
# A column whose name suggests free text or a person is never listed, whatever its contents.
NEVER_LISTED = ("*NAME*", "*COMMENT*", "*ADDR*", "*TEXT*", "*NOTE*", "*DESC*", "*EMAIL*", "*PHONE*")
# A value that a spreadsheet would read as a formula is never accepted.
FORMULA_STARTS = ("=", "+", "@", "\t", "\r")
INSERT = "INSERT INTO #schemalyser_checks"
# The bands of a spans check: the minutes from the first date column to the second, from (inclusive)
# and to (exclusive). The labels are the only values that a spans result may hold.
BANDS = (("less than zero minutes", None, 0), ("under 15 minutes", 0, 15), ("15 to 29 minutes", 15, 30),
         ("30 to 59 minutes", 30, 60), ("60 to 119 minutes", 60, 120), ("120 to 239 minutes", 120, 240),
         ("240 to 479 minutes", 240, 480), ("8 to 23 hours", 480, 1440), ("1 to 2 days", 1440, 4320),
         ("3 to 6 days", 4320, 10080), ("a week or more", 10080, None))
BAND_LABELS = tuple(label for label, _, _ in BANDS)
# Pairs of roles whose columns, in one table, are counted by a spans check: the first comes before the second.
SPAN_ROLES = (("anaesthetic_start", "anaesthetic_stop"), ("admission_time", "discharge_time"),
              ("placement_time", "removal_time"))
# The bands of a fanout check: how many rows of the child table hold one key value, from (inclusive) and
# to (exclusive). The labels are the only values that a fanout result may hold.
FANOUT_BANDS = (("1 row", 1, 2), ("2 rows", 2, 3), ("3 to 5 rows", 3, 6), ("6 to 10 rows", 6, 11),
                ("11 or more rows", 11, None))
FANOUT_LABELS = tuple(label for label, _, _ in FANOUT_BANDS)
# Lines that SQL Server prints among the results as information, and that a saved results file may hold.
# Such a line is passed over when the results are read; any other line out of place refuses the file.
SERVER_MESSAGES = ("Warning: Null value is eliminated by an aggregate or other SET operation.",)
# The count of rows that SQL Server reports after a query, which pasted text from sqlcmd may hold.
ROWS_AFFECTED = re.compile(r"\(\d+ rows? affected\)")


class ChecksError(ValueError):
    """The check results file does not have the expected layout."""


def _bracket(name):
    """A name inside square brackets. The catalogue accepts only plain names; this is a second defence."""
    return "[" + name.replace("]", "]]") + "]"


def _text(name):
    """A name inside single quotes."""
    return "'" + name.replace("'", "''") + "'"


@dataclass(frozen=True)
class Check:
    kind: str                  # column, rows, values, years, spans or fanout
    table: str
    column: str = ""
    definition: tuple = ()     # (definition table, key column, label column) for a definition key
    later: str = ""            # for a spans check, the second date column, which comes after the first
    parent: tuple = ()         # for a fanout check, (parent table, parent column) that the column refers to

    def limit(self):
        return "@maximum_definitions" if self.definition else "@maximum_values"

    def size(self, catalogue):
        """A SELECT giving the number of rows that the server records for the table, without reading the table.

        It gives no number for a view or for a table that the server does not know.
        """
        return (f"SELECT SUM(p.rows) FROM sys.partitions AS p WHERE p.object_id = "
                f"OBJECT_ID(N{_text(_name(catalogue, self.table))}) AND p.index_id IN (0, 1)")

    def guard(self, catalogue, sampled=False):
        """For a values check: a SELECT giving the number of distinct values, counted no further than the limit."""
        c = _bracket(self.column)
        return (f"SELECT COUNT_BIG(*) FROM (SELECT DISTINCT TOP ({self.limit()} + 1) {c} "
                f"FROM {_source(catalogue, self.table, sampled)} WHERE {c} IS NOT NULL) AS x")

    def select(self, catalogue, sampled=False):
        """The SELECT that answers the check, as T-SQL.

        With sampled, the SELECT reads a sample of the table's pages and scales each count up to the whole
        table. The least count then applies to the rows of the sample, so a sampled check lists less.
        """
        t = _source(catalogue, self.table, sampled)
        c = _bracket(self.column)
        table, column = _text(self.table), _text(self.column)
        rounded = "(COUNT_BIG(*) / 10) * 10"
        counted = "((g.n * 100 / @sample_percent) / 10) * 10" if sampled else "(g.n / 10) * 10"
        if self.kind == "rows":
            return f"SELECT 'rows', {table}, {rounded} FROM {t}"
        if self.kind == "column":
            return (f"SELECT 'column', {table}, {column}, {rounded}, "
                    f"(COUNT_BIG(DISTINCT {c}) / 10) * 10, ((COUNT_BIG(*) - COUNT_BIG({c})) / 10) * 10, "
                    f"CASE WHEN COUNT_BIG({c}) > 0 AND COUNT_BIG({c}) = COUNT_BIG(DISTINCT {c}) THEN 'Y' ELSE 'N' END "
                    f"FROM {t}")
        if self.kind == "years":
            # The rows of a date column are counted by year. A year held by too few rows is left out.
            return (f"SELECT 'years', {table}, {column}, CAST(g.y AS nvarchar(200)), NULL, {counted} "
                    f"FROM (SELECT YEAR({c}) AS y, COUNT_BIG(*) AS n FROM {t} WHERE {c} IS NOT NULL "
                    f"GROUP BY YEAR({c}) HAVING COUNT_BIG(*) >= @minimum_count) AS g")
        if self.kind == "spans":
            # The rows are counted by the minutes from the first column to the second, in fixed bands.
            # The second column's name goes in the label field. A band held by too few rows is left out.
            # DATEDIFF_BIG cannot overflow where a far-off date stands for "not yet"; a server older than
            # SQL Server 2016 lacks it, and the check then fails alone and is recorded as an error.
            later = _bracket(self.later)
            bands = " ".join(f"WHEN d.m < {high} THEN {_text(label)}" for label, _, high in BANDS if high is not None)
            return (f"SELECT 'spans', {table}, {column}, g.b, {_text(self.later)}, {counted} "
                    f"FROM (SELECT CASE {bands} ELSE {_text(BANDS[-1][0])} END AS b, COUNT_BIG(*) AS n "
                    f"FROM (SELECT DATEDIFF_BIG(minute, {c}, {later}) AS m FROM {t} "
                    f"WHERE {c} IS NOT NULL AND {later} IS NOT NULL) AS d "
                    f"GROUP BY CASE {bands} ELSE {_text(BANDS[-1][0])} END HAVING COUNT_BIG(*) >= @minimum_count) AS g")
        if self.kind == "fanout":
            # The key values of the column are counted by the number of rows that hold each one, in fixed
            # bands. Only the band and the number of key values in it are returned, never a key value. The
            # parent's table and column go in the label field. A band held by too few key values is left out.
            bands = " ".join(f"WHEN k.c < {high} THEN {_text(label)}" for label, _, high in FANOUT_BANDS
                             if high is not None)
            band = f"CASE {bands} ELSE {_text(FANOUT_BANDS[-1][0])} END"
            return (f"SELECT 'fanout', {table}, {column}, g.b, {_text('.'.join(self.parent))}, (g.n / 10) * 10 "
                    f"FROM (SELECT {band} AS b, COUNT_BIG(*) AS n "
                    f"FROM (SELECT COUNT_BIG(*) AS c FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}) AS k "
                    f"GROUP BY {band} HAVING COUNT_BIG(*) >= @minimum_count) AS g")
        label = "NULL"
        if self.definition:
            # The rows are counted first and the label is looked up afterwards, so that a
            # definition table with several rows for one key cannot multiply the counts.
            definition_table, key, label_column = self.definition
            label = (f"(SELECT MAX(CAST(d.{_bracket(label_column)} AS nvarchar(200))) "
                     f"FROM {_name(catalogue, definition_table)} AS d WHERE d.{_bracket(key)} = g.k)")
        return (f"SELECT 'values', {table}, {column}, CAST(g.k AS nvarchar(200)), {label}, {counted} "
                f"FROM (SELECT {c} AS k, COUNT_BIG(*) AS n FROM {t} WHERE {c} IS NOT NULL "
                f"GROUP BY {c} HAVING COUNT_BIG(*) >= @minimum_count) AS g")

    def columns(self):
        if self.kind == "rows":
            return "(check_kind, table_name, row_count)"
        if self.kind == "column":
            return "(check_kind, table_name, column_name, row_count, distinct_count, null_count, is_unique)"
        return "(check_kind, table_name, column_name, value, label, row_count)"

    def statement(self, catalogue, sampled=False):
        """The check as the script runs it: one statement, guarded where it lists values."""
        insert = f"{INSERT} {self.columns()} {self.select(catalogue, sampled)}"
        if self.kind == "values":
            return f"IF ({self.guard(catalogue, sampled)}) <= {self.limit()} {insert}"
        return insert

    def maximum(self):
        """The most values that a values check may list for this column."""
        return MAXIMUM_DEFINITIONS if self.definition else MAXIMUM_VALUES

    def key(self):
        """A short identifier of the check, made of fixed words and catalogue names."""
        names = f"{self.table}.{self.column}" if self.column else self.table
        if self.later:
            names += f"-{self.later}"
        if self.parent:
            names += f"-{'.'.join(self.parent)}"
        return f"{self.kind}:{names}"

    def tables(self):
        """The tables that the check reads: its own, and for a definition key the definition table."""
        return [self.table] + ([self.definition[0]] if self.definition else [])

    def plain(self, catalogue, percent=None, bounded=False):
        """The check as one plain SELECT that a person can read and run alone, as T-SQL over several short lines.

        It returns the nine columns of LAYOUT under their own names, so that its result can be pasted back
        and read by Checks.from_csv. It holds no variable, no dynamic SQL, no setting and no temporary
        table: the least count, the rounding and the limit on values are written into it as numbers, and
        each table is read WITH (NOLOCK). With percent, it reads that share of the table's pages, scales
        each count up to the whole table, applies the least count to the rows of the sample, and adds one
        row of the kind sampled that records the percentage. It gives the same rows as the script's exact
        form on the same data, apart from that sampling.
        """
        source = _name(catalogue, self.table) + (sample_clause(percent) if percent else "") \
            + " WITH (NOLOCK)"
        c = _bracket(self.column)
        least = MINIMUM_COUNT
        counted = scaled("g.n", percent) if percent else "(g.n / 10) * 10"
        given = {"check_kind": _text(self.kind), "table_name": _text(self.table)}
        if self.column:
            given["column_name"] = _text(self.column)
        comment = [PLAIN_WORDING[self.kind].format(table=self.table, column=f"{self.table}.{self.column}",
                                                   later=f"{self.table}.{self.later}", least=least,
                                                   most=f"{self.maximum():,}",
                                                   parent=".".join(self.parent))]
        if percent:
            comment.append(PLAIN_WORDING["sampled"].format(percent=percent_text(percent), table=self.table,
                                                           rows=f"{PLAIN_SAMPLE_ROWS:,}"))
        elif self.kind == "rows" and bounded:
            comment.append(PLAIN_WORDING["bounded"].format(table=self.table, most=f"{PLAIN_EXACT_ROWS + 10:,}"))
        elif self.kind == "rows":
            comment.append(PLAIN_WORDING["whole"].format(table=self.table))
        comment.append(PLAIN_WORDING["safe"])
        top = ""
        if self.kind == "rows" and bounded:
            # A table with no recorded size, such as a view, is counted only up to just past the limit, and a
            # count past it comes back as a skipped rows check for its size, so that no query reads all of it.
            over = f"COUNT_BIG(*) > {PLAIN_EXACT_ROWS}"
            given.update({"check_kind": f"CASE WHEN {over} THEN 'skipped' ELSE 'rows' END",
                          "value": f"CASE WHEN {over} THEN 'rows' END",
                          "label": f"CASE WHEN {over} THEN 'size' END",
                          "row_count": f"CASE WHEN {over} THEN NULL ELSE (COUNT_BIG(*) / 10) * 10 END"})
            body = [f"FROM (SELECT TOP ({PLAIN_EXACT_ROWS + 10}) 1 AS x", f"      FROM {source}) AS b"]
        elif self.kind == "rows":
            given["row_count"] = "(COUNT_BIG(*) / 10) * 10"
            body = [f"FROM {source}"]
        elif self.kind == "column":
            given.update({"row_count": "(COUNT_BIG(*) / 10) * 10",
                          "distinct_count": f"(COUNT_BIG(DISTINCT {c}) / 10) * 10",
                          "null_count": f"((COUNT_BIG(*) - COUNT_BIG({c})) / 10) * 10",
                          "is_unique": f"CASE WHEN COUNT_BIG({c}) > 0 AND COUNT_BIG({c}) = COUNT_BIG(DISTINCT {c}) "
                                       f"THEN 'Y' ELSE 'N' END"})
            body = [f"FROM {source}"]
        elif self.kind == "years":
            given.update({"value": "CAST(g.y AS nvarchar(200))", "row_count": counted})
            body = [f"FROM (SELECT YEAR({c}) AS y, COUNT_BIG(*) AS n",
                    f"      FROM {source}",
                    f"      WHERE {c} IS NOT NULL",
                    f"      GROUP BY YEAR({c})",
                    f"      HAVING COUNT_BIG(*) >= {least}) AS g"]
        elif self.kind in ("spans", "fanout"):
            # The rows, or the key values, are put in fixed bands first, and the bands are then counted. A
            # band held by too few rows or key values is left out. A fanout check never returns a key value.
            if self.kind == "spans":
                bands, label = BANDS, _text(self.later)
                measure = (f"            FROM (SELECT DATEDIFF_BIG(minute, {c}, {_bracket(self.later)}) AS m",
                           f"                  FROM {source}",
                           f"                  WHERE {c} IS NOT NULL AND {_bracket(self.later)} IS NOT NULL) AS d) AS s")
            else:
                bands, label = FANOUT_BANDS, _text(".".join(self.parent))
                measure = (f"            FROM (SELECT COUNT_BIG(*) AS m",
                           f"                  FROM {source}",
                           f"                  WHERE {c} IS NOT NULL",
                           f"                  GROUP BY {c}) AS d) AS s")
            whens = [f"WHEN d.m < {high} THEN {_text(name)}" for name, _, high in bands if high is not None]
            case = ["      FROM (SELECT CASE " + whens[0]] + [" " * 24 + w for w in whens[1:]] \
                + [" " * 24 + f"ELSE {_text(bands[-1][0])} END AS b"]
            given.update({"value": "g.b", "label": label,
                          "row_count": counted if self.kind == "spans" else "(g.n / 10) * 10"})
            body = ["FROM (SELECT s.b, COUNT_BIG(*) AS n"] + case + list(measure) + [
                "      GROUP BY s.b",
                f"      HAVING COUNT_BIG(*) >= {least}) AS g"]
        else:
            # A values check lists nothing when the column holds more distinct values than the limit, as the
            # script's guard does, and TOP stops it from ever returning a longer list.
            limit = self.maximum()
            value = "CAST(g.k AS nvarchar(200))"
            if _kind(catalogue.table(self.table).column(self.column)) in TEXT_TYPES:
                value = _cleaned(value)
            given.update({"value": value, "row_count": counted})
            if self.definition:
                # The rows are counted first and the label is looked up afterwards, so that a definition
                # table with several rows for one key cannot multiply the counts.
                definition_table, key, label_column = self.definition
                given["label"] = (f"REPLACE(REPLACE(REPLACE(REPLACE(\n"
                                  f"           (SELECT MAX(CAST(d.{_bracket(label_column)} AS nvarchar(200)))\n"
                                  f"            FROM {_name(catalogue, definition_table)} AS d WITH (NOLOCK)\n"
                                  f"            WHERE d.{_bracket(key)} = g.k),\n"
                                  f"           ',', ' '), '\"', ''''), CHAR(13), ' '), CHAR(10), ' ')")
            top = f"TOP ({limit + 1}) "
            body = [f"FROM (SELECT {c} AS k, COUNT_BIG(*) AS n",
                    f"      FROM {source}",
                    f"      WHERE {c} IS NOT NULL",
                    f"      GROUP BY {c}",
                    f"      HAVING COUNT_BIG(*) >= {least}) AS g",
                    f"WHERE (SELECT COUNT_BIG(*) FROM (SELECT DISTINCT TOP ({limit + 1}) {c}",
                    f"                                 FROM {source}",
                    f"                                 WHERE {c} IS NOT NULL) AS x) <= {limit}"]
        lines = _comment(comment) + _select(given, top) + body
        if percent:
            marker = {"check_kind": "'sampled'", "table_name": _text(self.table), "column_name": _text(self.column),
                      "value": _text(self.kind), "row_count": str(max(1, round(percent)))}
            lines += ["UNION ALL", "SELECT " + ", ".join(marker.get(name, "NULL") for name in LAYOUT)]
        elif self.kind in RAN_KINDS:
            # One constant row that records that the query ran, so that a query that finds nothing leaves a trace.
            marker = {"check_kind": "'ran'", "table_name": _text(self.table), "column_name": _text(self.column),
                      "value": _text(self.kind)}
            if self.later or self.parent:
                marker["label"] = _text(self.later or ".".join(self.parent))
            lines += ["UNION ALL", "SELECT " + ", ".join(marker.get(name, "NULL") for name in LAYOUT)]
        return "\n".join(lines) + ";"


def _name(catalogue, table):
    entry = catalogue.table(table)
    return f"{_bracket(entry.schema)}.{_bracket(entry.name)}" if entry.schema else _bracket(entry.name)


# The comments at the head of each plain query. Each is filled with catalogue names and fixed numbers only.
PLAIN_WORDING = {
    "rows": "This query counts the rows of {table}, rounded down to the nearest ten.",
    "whole": "It reads the whole of {table}, because SQL Server keeps no record of its size.",
    "bounded": "SQL Server keeps no record of the size of {table}, as for a view, so the query reads at most {most} of "
               "its rows. If the table holds more, the query says only that it is large, with a skipped row.",
    "column": "This query counts the rows of {table}, and the distinct and the empty values of {column}, each rounded "
              "down to the nearest ten.",
    "values": "This query lists each value of {column} that at least {least} rows hold, with its count rounded down "
              "to the nearest ten. It lists nothing if the column holds more than {most} values.",
    "years": "This query counts the rows of {column} by year, rounded down to the nearest ten, and leaves out any "
             "year that fewer than {least} rows hold.",
    "spans": "This query counts the rows of {table} by the minutes from {column} to {later}, in fixed bands, rounded "
             "down to the nearest ten, and leaves out any band that fewer than {least} rows fall in.",
    "fanout": "This query counts the values of {column} by the number of rows that hold each one, in fixed bands, "
              "rounded down to the nearest ten. It never returns a value itself, and it leaves out any band that "
              "fewer than {least} values fall in.",
    "sampled": "Because {table} is large, the query reads about {percent} per cent of its pages, a sample of about "
               "{rows} rows, and its counts are estimates scaled up from that sample. The least count of ten applies "
               "to the rows of the sample, and the sampled row at the end records the percentage, rounded to a whole number.",
    "safe": "It only reads. WITH (NOLOCK) means that it takes no row locks, but it holds a schema lock while it runs, "
            "so it should not run during the nightly load.",
    "sizes": "This query reads from SQL Server's own records the number of rows in each table below, rounded down "
             "to the nearest ten, without reading any of the tables. A name for which the server keeps no record, "
             "such as a view, comes back as skipped and unrecorded.",
}
PLAIN_WIDTH = 100


def _comment(sentences):
    import textwrap
    return [f"-- {line}" for sentence in sentences for line in textwrap.wrap(sentence, PLAIN_WIDTH - 3)]


def _cleaned(expression):
    """An expression with commas, double quotes and line breaks replaced, as the script cleans its results."""
    return (f"REPLACE(REPLACE(REPLACE(REPLACE({expression}, ',', ' '), '\"', ''''), CHAR(13), ' '), "
            f"CHAR(10), ' ')")


def _select(given, top=""):
    """The SELECT list of a plain query: the nine columns of LAYOUT under their own names, NULL where not given."""
    items = [f"{given.get(name, 'NULL')} AS {name}" for name in LAYOUT]
    lines = [f"SELECT {top}{items[0]}"]
    for item in items[1:]:
        if "\n" in item or "\n" in lines[-1] or len(lines[-1]) + len(item) + 2 > PLAIN_WIDTH:
            lines[-1] += ","
            lines.append("       " + item)
        else:
            lines[-1] += ", " + item
    return lines


def size_query(catalogue, tables):
    """One plain SELECT that reads the recorded number of rows of each table from SQL Server's own records.

    It reads sys.tables and sys.partitions for a literal list of catalogue tables, with each table's schema
    where the catalogue gives one, and never reads a table itself. A table that the server records comes
    back as a rows result rounded down to ten; a name for which it keeps no record, such as a view, comes
    back as a skipped rows check with the reason unrecorded, so that the checklist can offer to count it.
    """
    names = sorted({catalogue.table(t).name: catalogue.table(t) for t in tables}.items())
    if not names:
        return ""
    pairs = [f"({_unicode(entry.schema or '')}, {_unicode(entry.name)})" for _, entry in names]
    unnamed = any(not entry.schema for _, entry in names)
    schema = "(n.schema_name = N'' OR t.schema_id = SCHEMA_ID(n.schema_name))" if unnamed \
        else "t.schema_id = SCHEMA_ID(n.schema_name)"
    unrecorded = "SUM(p.rows) IS NULL"
    given = {"check_kind": f"CASE WHEN {unrecorded} THEN 'skipped' ELSE 'rows' END",
             "table_name": "n.table_name",
             "value": f"CASE WHEN {unrecorded} THEN 'rows' END",
             "label": f"CASE WHEN {unrecorded} THEN '{UNRECORDED}' END",
             "row_count": "(SUM(p.rows) / 10) * 10"}
    lines = _comment([PLAIN_WORDING["sizes"]]) + _select(given)
    for i, pair in enumerate(pairs):
        lines.append(("FROM (VALUES " if i == 0 else " " * 13) + pair
                     + ("," if i < len(pairs) - 1 else ") AS n (schema_name, table_name)"))
    lines += ["LEFT JOIN sys.tables AS t",
              f"       ON t.name = n.table_name AND {schema}",
              "LEFT JOIN sys.partitions AS p",
              "       ON p.object_id = t.object_id AND p.index_id IN (0, 1)",
              "GROUP BY n.table_name;"]
    return "\n".join(lines)


def _unicode(text):
    return "N" + _text(text)


def held(check, checks):
    """Whether the check results already hold an answer to a check, including a sampled one."""
    if checks is None:
        return False
    up = lambda *names: tuple(n.upper() for n in names)  # noqa: E731
    sampled = {up(kind, table, column) for kind, table, column, _ in checks.sampled}
    if check.kind in SAMPLED_KINDS and up(check.kind, check.table, check.column) in sampled:
        return True
    if ran_empty(check, checks) is not None:
        return True
    if check.kind == "rows":
        return check.table.upper() in {t.upper() for t in checks.rows}
    if check.kind == "spans":
        return up(check.table, check.column, check.later) in {up(*k) for k in checks.spans}
    if check.kind == "fanout":
        return up(check.table, check.column, *check.parent) in {up(*k) for k in checks.fanout}
    found = {"column": checks.columns, "values": checks.values, "years": checks.years}[check.kind]
    return up(check.table, check.column) in {up(*k) for k in found}


def ran_empty(check, checks):
    """Whether the plain query of a check has run, as True where it found nothing and False where it found something.

    Returns None where the check results hold no record that the query ran.
    """
    if checks is None or check.kind not in RAN_KINDS:
        return None
    up = lambda *names: tuple(n.upper() for n in names)  # noqa: E731
    other = check.later or ".".join(check.parent)
    if up(check.kind, check.table, check.column, other) not in {up(*item) for item in checks.ran}:
        return None
    if check.kind == "spans":
        found = up(check.table, check.column, check.later) in {up(*k) for k in checks.spans}
    elif check.kind == "fanout":
        found = up(check.table, check.column, *check.parent) in {up(*k) for k in checks.fanout}
    else:
        found = up(check.table, check.column) in {up(*k) for k in getattr(checks, check.kind)}
    return not found


def size_of(table, checks):
    """The number of rows that the check results give for a table, or None."""
    if checks is None:
        return None
    return next((count for name, count in checks.rows.items() if name.upper() == table.upper()), None)


def too_large(table, checks):
    """Whether a count of a table with no recorded size found more rows than a plain query may read."""
    return checks is not None and any(kind == "rows" and name.upper() == table.upper() and reason == "size"
                                      for kind, name, _, reason in checks.skipped)


def unrecorded(table, checks):
    """Whether the check results say that the server keeps no record of a table's size."""
    return checks is not None and any(kind == "rows" and name.upper() == table.upper() and reason == UNRECORDED
                                      for kind, name, _, reason in checks.skipped)


def sample_percent(rows, sample_rows=None):
    """The percentage of a large table's pages that holds about PLAIN_SAMPLE_ROWS rows, at any size of table.

    It is a fraction where it must be, with four significant figures, so that six billion rows give a sample of
    about five million and not sixty million, and it is never more than 100.
    """
    wanted = PLAIN_SAMPLE_ROWS if sample_rows is None else sample_rows
    percent = min(100.0, wanted * 100 / max(rows, 1))
    return float(f"{percent:.4g}")


def percent_text(percent):
    """A percentage as a literal for TABLESAMPLE, without a trailing zero or an exponent."""
    text = f"{percent:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def sample_clause(percent):
    """The literal sampling clause: a percentage of the pages, read with the same seed in every part of a query,
    so that the guard and the main query read the same pages."""
    return f" TABLESAMPLE SYSTEM ({percent_text(percent)} PERCENT) REPEATABLE ({SAMPLE_SEED})"


def scaled(count, percent):
    """A count from a sample scaled up to the whole table, as a whole number rounded down to ten."""
    return f"(CAST({count} * 100.0 / {percent_text(percent)} AS bigint) / 10) * 10"


def offer(check, catalogue, checks, exact_rows=None, sample_rows=None):
    """How the checklist offers one check, as (state, [(check, plain SELECT)]).

    The state is one of OFFER_STATES. A check on a table whose size the check results do not give waits
    for the size query ("sizes"). Where the server keeps no record of a table's size, the check waits for
    a count of that table, which is offered in its place ("count"). A table at or under exact_rows
    (PLAIN_EXACT_ROWS unless given) gets the exact query; a larger one gets a sampled query for a values,
    years or spans check and no query for a column or fanout check ("large").
    """
    limit = PLAIN_EXACT_ROWS if exact_rows is None else exact_rows
    sizes = {table: size_of(table, checks) for table in check.tables()}
    waiting = [t for t, size in sizes.items() if size is None and not unrecorded(t, checks) and not too_large(t, checks)]
    if waiting:
        return "sizes", []
    if any(size is None and too_large(t, checks) for t, size in sizes.items()):
        # A large table with no recorded size, such as a view, cannot be read in part with TABLESAMPLE.
        return "unsampled", []
    counts = [t for t, size in sizes.items() if size is None]
    if counts:
        return "count", [(Check("rows", t), Check("rows", t).plain(catalogue, bounded=True)) for t in counts]
    if any(size > limit for t, size in sizes.items() if t != check.table):
        return "large", []
    size = sizes[check.table]
    if size <= limit:
        return "exact", [(check, check.plain(catalogue))]
    if check.kind in SAMPLED_KINDS:
        return "sampled", [(check, check.plain(catalogue, sample_percent(size, sample_rows)))]
    return "large", []


def _source(catalogue, table, sampled=False):
    """A table as a statement reads it: the whole of it, or a sample of its pages."""
    return _name(catalogue, table) + (f" TABLESAMPLE SYSTEM ({PERCENT} PERCENT)" if sampled else "")


def _kind(column):
    return column.data_type.strip().upper()


def _countable(column):
    return _kind(column) not in UNCOUNTABLE_TYPES


def is_numeric(column):
    return _kind(column) in WHOLE_NUMBER_TYPES | SCALED_NUMBER_TYPES | OTHER_NUMBER_TYPES


def _matches(name, patterns):
    return any(fnmatchcase(name.upper(), pattern.upper()) for pattern in patterns)


def _listable(column, never_listed):
    """Whether a column's values may be listed: short text or whole numbers, and not named like free text."""
    if _matches(column.name, never_listed):
        return False
    kind = _kind(column)
    if kind in WHOLE_NUMBER_TYPES:
        return True
    if kind in SCALED_NUMBER_TYPES:
        return column.scale == 0
    if kind in TEXT_TYPES:
        return column.max_length is not None and 0 < column.max_length <= MAXIMUM_TEXT_LENGTH
    return False


def _definitions(catalogue, rules):
    """The definition keys in the site rules: (table or "", column) -> (definition table, key, label)."""
    people = {name.upper() for name in rules.person_tables}
    found = {}
    for rule in rules.definition_keys:
        table = catalogue.table(rule.get("definitionTable", ""))
        column = rule.get("column", "")
        key = rule.get("keyColumn", column)
        label = rule.get("labelColumn", "")
        if table and table.name.upper() not in people and table.column(key) and table.column(label):
            # A rule applies to one table when it names one, and otherwise to the column wherever it appears.
            found[(rule.get("table", "").upper(), column.upper())] = (
                table.name, table.column(key).name, table.column(label).name)
    return found


def _never_listed(rules):
    return NEVER_LISTED + tuple(rules.never_list_columns) + tuple(rules.person_key_columns)


def _span_pairs(catalogue, rules, findings, people, person_keys):
    """Pairs of date columns in one table, earlier first, that the requests compare or the roles pair up.

    A pair from the roles is counted only in a table that the requests use.
    """
    used = {finding[1] for finding in findings if finding[0] == "table"}
    from_roles = {}
    by_role = {}
    for role in meanings.from_rules(rules, catalogue):
        by_role.setdefault(role.role, []).append(role)
    for first, second in SPAN_ROLES:
        for a in by_role.get(first, []):
            for b in by_role.get(second, []):
                if a.table == b.table and a.column != b.column and a.table in used:
                    from_roles[(a.table, a.column, b.column)] = True
    compared = set()
    for finding in findings:
        if finding[0] == "comparison" and finding[1] == finding[4] and finding[2] != finding[5]:
            entry = catalogue.table(finding[1])
            a, b = entry.column(finding[2]), entry.column(finding[5])
            compared.add((entry.name, a.name, b.name))
    pairs = set(from_roles)
    for table, a, b in sorted(compared):
        # Where the requests compare the two columns both ways, the order of the roles or of the names decides.
        reverse = (table, b, a)
        if reverse in from_roles or (reverse in compared and b < a):
            continue
        pairs.add((table, a, b))
    found = set()
    never = tuple(rules.person_key_columns)
    for table, a, b in pairs:
        entry = catalogue.table(table)
        if table.upper() in people or any((table, c) in person_keys or _matches(c, never) for c in (a, b)):
            continue
        if _kind(entry.column(a)) in DATE_TYPES and _kind(entry.column(b)) in DATE_TYPES:
            found.add(Check("spans", table, a, later=b))
    return found


def _row_key(catalogue, table, column):
    """Whether the catalogue shows a column to key its table's rows: the first column, which may not be empty."""
    entry = catalogue.table(table)
    first = entry.first_column() if entry else None
    return first is not None and first.name == column and first.nullable is False


def _fanout_joins(catalogue, rules, findings, people, person_keys, listed, definitions, unique):
    """The joins from a child column to a parent's unique key, as fanout checks.

    The parent's column is unique where the check results say so, or, without a result for it, where
    the catalogue shows it to key its table's rows. A column whose check results show it unique is no
    child, a column whose values are listed is a category rather than a key, and a definition key is
    left to its values check.
    """
    never = tuple(rules.person_key_columns)
    found = set()
    for finding in findings:
        if finding[0] != "join":
            continue
        for child, parent in ((finding[1:3], finding[3:5]), (finding[3:5], finding[1:3])):
            if child == parent or child[0].upper() in people or parent[0].upper() in people:
                continue
            if any(key in person_keys or _matches(key[1], never) for key in (child, parent)):
                continue
            if child in listed or definitions.get((child[0].upper(), child[1].upper())) \
                    or definitions.get(("", child[1].upper())):
                continue
            if not _countable(catalogue.table(child[0]).column(child[1])) or unique.get(child) is True:
                continue
            if unique.get(parent, _row_key(catalogue, *parent)) is True:
                found.add(Check("fanout", child[0], child[1], parent=tuple(parent)))
    return found


def plan(catalogue, rules, findings, include_years=False, include_spans=False, include_fanout=False, unique=None):
    """The checks that the findings call for, in a stable order.

    include_spans adds the spans checks, and include_fanout the fanout checks. Both are off by
    default, because the script's wording was approved before those kinds of check existed. unique
    gives, for (table, column), whether check results already in hand show the column to be unique.
    """
    people = {name.upper() for name in rules.person_tables}
    never_listed = _never_listed(rules)
    definitions = _definitions(catalogue, rules)
    # A column that the requests join to a table describing people is a key to a person, and is never listed.
    person_keys = set()
    for finding in findings:
        if finding[0] == "join":
            left, right = finding[1:3], finding[3:5]
            if left[0].upper() in people:
                person_keys.add(right)
            if right[0].upper() in people:
                person_keys.add(left)

    checks = set()
    counted = set()
    for finding in findings:
        if finding[0] == "join":
            for table, column in (finding[1:3], finding[3:5]):
                if _countable(catalogue.table(table).column(column)):
                    checks.add(Check("column", table, column))
                    counted.add(table)
        elif finding[0] == "filter":
            _, table, column, operator, value_kind, _ = finding
            entry = catalogue.table(table).column(column)
            if (operator in VALUE_OPERATORS and value_kind in VALUE_KINDS and table.upper() not in people
                    and (table, column) not in person_keys and _listable(entry, never_listed)):
                definition = definitions.get((table.upper(), column.upper())) or definitions.get(("", column.upper()), ())
                checks.add(Check("values", table, column, definition))
    if include_years:
        used = set()
        for finding in findings:
            if finding[0] == "filter":
                used.add(finding[1:3])
            elif finding[0] == "comparison":
                used.update((finding[1:3], finding[4:6]))
            elif finding[0] == "derivation":
                used.update(tuple(name.split(".", 1)) for name in finding[2].split())
        for table, column in used:
            if _kind(catalogue.table(table).column(column)) in DATE_TYPES and table.upper() not in people:
                checks.add(Check("years", table, column))
    if include_spans:
        checks |= _span_pairs(catalogue, rules, findings, people, person_keys)
    if include_fanout:
        listed = {(c.table, c.column) for c in checks if c.kind == "values"}
        checks |= _fanout_joins(catalogue, rules, findings, people, person_keys, listed, definitions, unique or {})
    for finding in findings:
        # A column check already returns the number of rows, so a table needs its own count only without one.
        if finding[0] == "table" and finding[1] not in counted:
            checks.add(Check("rows", finding[1]))
    return sorted(checks, key=lambda c: (RUN_ORDER.index(c.kind), c.table, c.column, c.later, c.parent))


def _clean(column):
    # Commas, quotes and line breaks are replaced, so that the saved CSV keeps nine fields in every row.
    return (f"REPLACE(REPLACE(REPLACE(REPLACE({column}, ',', ' '), '\"', ''''), CHAR(13), ' '), CHAR(10), ' ') "
            f"AS {column}")


def script(checks, catalogue, wording):
    """The whole check script. Its comments come from the fixed wording."""
    header = list(wording["header"])
    for kind in ("spans", "fanout"):
        if any(check.kind == kind for check in checks):
            # The wording about these checks goes before the closing request to run the script.
            header[-1:-1] = wording[f"{kind}_header"]
    lines = [f"-- {line}" for line in header]
    # Without ANSI_WARNINGS, SQL Server does not print a warning among the results when an aggregate
    # passes over empty values, which would otherwise land in the saved CSV file.
    # The script reads without taking locks, gives way where it would otherwise wait long for one, and is
    # the statement that SQL Server ends if two ever block each other.
    lines += ["", "SET NOCOUNT ON;", "SET ANSI_WARNINGS OFF;",
              "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;", "SET LOCK_TIMEOUT 10000;",
              "SET DEADLOCK_PRIORITY LOW;", "",
              f"-- {wording['settings']}",
              f"DECLARE @large_table_rows bigint = {LARGE_TABLE_ROWS};",
              f"DECLARE @sample_percent int = {SAMPLE_PERCENT};",
              f"DECLARE @minutes_allowed int = {MINUTES_ALLOWED};", "",
              f"DECLARE @minimum_count int = {MINIMUM_COUNT};",
              f"DECLARE @maximum_values int = {MAXIMUM_VALUES};",
              f"DECLARE @maximum_definitions int = {MAXIMUM_DEFINITIONS};",
              "DECLARE @limits nvarchar(200) = N'@minimum_count int, @maximum_values int, "
              "@maximum_definitions int, @sample_percent int';",
              "IF @sample_percent IS NULL OR @sample_percent NOT BETWEEN 1 AND 100 SET @sample_percent = 1;",
              "DECLARE @deadline datetime2 = DATEADD(minute, @minutes_allowed, SYSDATETIME());",
              "DECLARE @size bigint;",
              "DECLARE @sampled nvarchar(max);",
              "",
              "DROP TABLE IF EXISTS #schemalyser_checks;",
              "CREATE TABLE #schemalyser_checks (",
              "    check_kind varchar(10), table_name varchar(128), column_name varchar(128),",
              "    value nvarchar(200), label nvarchar(200),",
              "    row_count bigint, distinct_count bigint, null_count bigint, is_unique varchar(1));"]
    limits = "@limits, @minimum_count, @maximum_values, @maximum_definitions, @sample_percent;"
    previous = None
    for check in checks:
        if check.kind != previous:
            lines += ["", f"-- {wording[check.kind]}"]
            previous = check.kind
        named = _text(check.table) + (f", {_text(check.column)}" if check.column else "")
        named_columns = "(check_kind, table_name" + (", column_name" if check.column else "")
        note = f"{INSERT} {named_columns}, value, label) VALUES ('skipped', {named}, {_text(check.kind)}, "
        exact = ["EXEC sys.sp_executesql N'" + check.statement(catalogue).replace("'", "''") + "',",
                 "     " + limits]
        lines += ["BEGIN TRY", f"    SET @size = ({check.size(catalogue)});"]
        if check.kind == "rows":
            # The server's own record of the number of rows costs nothing to read, so it is used wherever there is one.
            lines += ["    IF @size IS NOT NULL",
                      f"        {INSERT} (check_kind, table_name, row_count) VALUES ('rows', {named}, (@size / 10) * 10);",
                      "    ELSE IF SYSDATETIME() >= @deadline", f"        {note}'time');",
                      "    ELSE"] + ["        " + line for line in exact]
        else:
            lines += ["    IF SYSDATETIME() >= @deadline", f"        {note}'time');",
                      "    ELSE IF @size > @large_table_rows"]
            if check.kind in SAMPLED_KINDS:
                sampled = check.statement(catalogue, sampled=True).replace("'", "''")
                lines += ["    BEGIN",
                          f"        SET @sampled = REPLACE(N'{sampled}', N'{PERCENT}', CAST(@sample_percent AS nvarchar(3)));",
                          "        EXEC sys.sp_executesql @sampled, " + limits,
                          f"        {INSERT} {named_columns}, value, row_count) VALUES ('sampled', {named}, "
                          f"{_text(check.kind)}, @sample_percent);",
                          "    END"]
            else:
                lines.append(f"        {note}'size');")
            lines += ["    ELSE"] + ["        " + line for line in exact]
        lines += ["END TRY BEGIN CATCH",
                  f"    {INSERT} {named_columns}, row_count) VALUES ('error', {named}, ERROR_NUMBER());",
                  "END CATCH;"]
    lines += ["", f"-- {wording['results']}",
              "SELECT check_kind, table_name, column_name,",
              f"       {_clean('value')},",
              f"       {_clean('label')},",
              "       row_count, distinct_count, null_count, is_unique",
              "FROM #schemalyser_checks",
              "ORDER BY check_kind, table_name, column_name, value;",
              "", "DROP TABLE #schemalyser_checks;", ""]
    return "\n".join(lines)


def normalise(value, numeric=False):
    """The form in which a literal in a request is compared with a value in the check results.

    A numeric column compares as a number, so that 1 and 1.0 agree. A text column compares as
    text, so that '01' and '1' stay different.
    """
    text = str(value).strip()
    if numeric:
        try:
            number = Decimal(text)
            if number.is_finite():
                return format(number.normalize() + 0, "f")
        except ArithmeticError:
            pass
    return text.upper()


def _number(text):
    text = (text or "").strip()
    if text in ("", "NULL"):
        return None
    if not text.isdecimal():
        raise ChecksError
    return int(text)


def _acceptable(text, limit):
    return 0 < len(text) <= limit and not text.startswith(FORMULA_STARTS) and "\n" not in text


class Checks:
    """The accepted rows of a check results file, with names in the catalogue's own spelling."""

    def __init__(self):
        self.rows = {}        # table -> row count
        self.columns = {}     # (table, column) -> {"rows", "distinct", "nulls", "unique"}
        self.values = {}      # (table, column) -> [(value, label, count)]
        self.years = {}       # (table, column) -> [(year, count)]
        self.spans = {}       # (table, first column, second column) -> [(band, count)]
        self.fanout = {}      # (child table, child column, parent table, parent column) -> [(band, keys)]
        self.errors = 0
        self.failed = []      # (table, column or "", the server's error number) for each check that met an error
        self.skipped = []     # (kind of check, table, column or "", "time", "size" or "unrecorded")
        self.sampled = []     # (kind of check, table, column, the percentage of the table that was read)
        self.ran = []         # (kind of check, table, column, second column or parent as TABLE.COLUMN, or ""), each a
                              # plain query that has run, whatever it found
        self.assumed_headers = False
        self.read = 0         # the rows read, apart from headers and messages from the server
        self.accepted = 0     # the rows kept, after the limits on values were applied

    @classmethod
    def from_csv(cls, text, catalogue, rules, limit_values=True):
        """Reads check results, keeping only rows that the script could have returned.

        limit_values is turned off when reading results that were already accepted once, where the
        site rules that set each column's limit are no longer to hand.
        """
        checks = cls()
        reader = csv.reader(io.StringIO(text))
        people = {name.upper() for name in rules.person_tables}
        never_listed = _never_listed(rules)
        definitions = _definitions(catalogue, rules)
        first = True
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) == 1 and row[0].strip() in SERVER_MESSAGES:
                continue    # a message that SQL Server printed among the results, not a result
            if len(row) != len(LAYOUT):
                raise ChecksError
            if first:
                first = False
                if tuple(cell.strip().lower() for cell in row) == LAYOUT:
                    continue
                checks.assumed_headers = True
            kind, table, column, value, label, row_count, distinct_count, null_count, unique = (c.strip() for c in row)
            if kind not in KINDS:
                raise ChecksError
            checks.read += 1
            entry = catalogue.table(table)
            if entry is None:
                continue
            field = entry.column(column) if column and column != "NULL" else None
            count = _number(row_count) or 0
            if kind == "error":
                # The check is named by its table and column alone, and the server's error number is a whole number.
                if row_count.isdecimal() and len(row_count) <= 10 and (field is not None or not column or column == "NULL"):
                    failure = (entry.name, field.name if field is not None else "", int(row_count))
                    if failure not in checks.failed:
                        checks.failed.append(failure)
                        checks.errors += 1
                        checks.accepted += 1
                else:
                    checks.errors += 1
            elif kind == "rows":
                checks.rows[entry.name] = count
                checks.accepted += 1
            elif kind == "skipped":
                # The kind of check is in the value field, and the reason, one of three fixed words, in the label field.
                if value in RUN_ORDER and label in SKIPPED_REASONS:
                    checks.skipped.append((value, entry.name, field.name if field is not None else "", label))
                    checks.accepted += 1
            elif kind == "ran":
                # The kind of check is in the value field; a spans check's second column, or a fanout check's parent, in the label.
                other = ""
                if value == "spans" and field is not None:
                    later = entry.column(label) if label and label != "NULL" else None
                    other = later.name if later is not None and _kind(later) in DATE_TYPES else None
                elif value == "fanout" and field is not None:
                    parent_name, _, parent_column = label.partition(".")
                    parent = catalogue.table(parent_name) if parent_column else None
                    found_column = parent.column(parent_column) if parent is not None else None
                    other = f"{parent.name}.{found_column.name}" if found_column is not None else None
                if value in RAN_KINDS and field is not None and other is not None:
                    checks.ran.append((value, entry.name, field.name, other))
                    checks.accepted += 1
            elif kind == "sampled":
                if value in SAMPLED_KINDS and field is not None and 1 <= count <= 100:
                    checks.sampled.append((value, entry.name, field.name, count))
                    checks.accepted += 1
            elif field is None:
                continue
            elif kind == "column":
                checks.columns[(entry.name, field.name)] = {
                    "rows": count, "distinct": _number(distinct_count) or 0,
                    "nulls": _number(null_count) or 0, "unique": unique.upper() == "Y"}
                # A column check also gives the size of its table.
                checks.rows.setdefault(entry.name, count)
                checks.accepted += 1
            elif entry.name.upper() in people or count < MINIMUM_COUNT or count % 10:
                # The script never returns a value for a table of people, a count under ten, or a count not rounded.
                continue
            elif kind == "years":
                if value.isdecimal() and 1900 <= int(value) <= 2200 and _kind(field) in DATE_TYPES:
                    checks.years.setdefault((entry.name, field.name), []).append((int(value), count))
                    checks.accepted += 1
            elif kind == "spans":
                # The second column is in the label field, and the band must be one of the fixed labels.
                later = entry.column(label) if label and label != "NULL" else None
                if (later is not None and later.name != field.name and value in BAND_LABELS
                        and _kind(field) in DATE_TYPES and _kind(later) in DATE_TYPES
                        and not any(_matches(c.name, rules.person_key_columns) for c in (field, later))):
                    found = checks.spans.setdefault((entry.name, field.name, later.name), [])
                    if value not in {band for band, _ in found}:
                        found.append((value, count))
                        checks.accepted += 1
            elif kind == "fanout":
                # The parent's table and column are in the label field, and the band must be one of the fixed labels.
                parent_name, _, parent_column = label.partition(".")
                parent = catalogue.table(parent_name) if parent_column else None
                other = parent.column(parent_column) if parent is not None else None
                if (other is not None and value in FANOUT_LABELS and parent.name.upper() not in people
                        and (parent.name, other.name) != (entry.name, field.name)
                        and not any(_matches(c.name, rules.person_key_columns) for c in (field, other))):
                    found = checks.fanout.setdefault((entry.name, field.name, parent.name, other.name), [])
                    if value not in {band for band, _ in found}:
                        found.append((value, count))
                        checks.accepted += 1
            elif _listable(field, never_listed) and _acceptable(value, MAXIMUM_TEXT_LENGTH):
                label = "" if label == "NULL" or not _acceptable(label, MAXIMUM_LABEL_LENGTH) else label
                checks.values.setdefault((entry.name, field.name), []).append((value, label, count))
                checks.accepted += 1
        if first:
            raise ChecksError
        if limit_values:
            for key in list(checks.values):
                defined = (key[0].upper(), key[1].upper()) in definitions or ("", key[1].upper()) in definitions
                if len(checks.values[key]) > (MAXIMUM_DEFINITIONS if defined else MAXIMUM_VALUES):
                    checks.accepted -= len(checks.values[key])
                    del checks.values[key]
        return checks

    @classmethod
    def from_pasted(cls, text, catalogue, rules):
        """Reads results pasted from a results grid or a CSV file, under exactly the rules of from_csv.

        Text that holds a tab is read as tab-separated, as SQL Server Management Studio copies its grid;
        any other text is read as CSV. A header row, wherever it falls, is passed over, so that the results
        of several queries can be pasted together. Raises ChecksError when no row has the nine columns.
        """
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if "\t" in text:
            rows = [line.split("\t") for line in text.split("\n")]
        else:
            rows = list(csv.reader(io.StringIO(text)))
        kept = [row for row in rows if row and any(cell.strip() for cell in row)
                and tuple(cell.strip().lower() for cell in row) != LAYOUT
                and not (len(row) == 1 and ROWS_AFFECTED.fullmatch(row[0].strip()))
                and not all(re.fullmatch(r"-*", cell.strip()) for cell in row)]   # the line under sqlcmd's headers
        if not kept:
            raise ChecksError
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows([LAYOUT, *kept])
        return cls.from_csv(out.getvalue(), catalogue, rules)

    def merged(self, later):
        """These results with later ones added, where a later result for the same check replaces the earlier one.

        A check's result is replaced as a whole: the values of a column, the years of a column, the bands of
        a spans or fanout check, a column check or the size of a table. A sampled or skipped record of the
        same check goes with it, so that an exact result replaces a sampled one and the reverse.
        """
        found = Checks()
        found.assumed_headers = self.assumed_headers
        up = lambda *names: tuple(n.upper() for n in names)  # noqa: E731
        answered = set()        # (kind, TABLE, COLUMN) of each check that the later results answer
        answered |= {("rows", up(t)[0], "") for t in later.rows}
        answered |= {("column",) + up(*k) for k in later.columns}
        for kind in ("values", "years"):
            answered |= {(kind,) + up(*k) for k in getattr(later, kind)}
        answered |= {("spans",) + up(*k[:2]) for k in later.spans}
        answered |= {("fanout",) + up(*k[:2]) for k in later.fanout}
        answered |= {(kind,) + up(t, c) for kind, t, c, _ in later.sampled + later.skipped + later.ran}
        for name in ("columns", "values", "years", "spans", "fanout"):
            mine, theirs = getattr(self, name), getattr(later, name)
            later_keys = {up(*k) for k in theirs}
            combined = {k: v for k, v in mine.items() if up(*k) not in later_keys}
            combined.update(theirs)
            setattr(found, name, combined)
        found.rows = {t: n for t, n in self.rows.items() if t.upper() not in {x.upper() for x in later.rows}}
        found.rows.update(later.rows)
        for name in ("sampled", "skipped", "ran"):
            earlier = [item for item in getattr(self, name) if (item[0],) + up(item[1], item[2]) not in answered]
            setattr(found, name, earlier + [item for item in getattr(later, name) if item not in earlier])
        # An error row names only the table and the column, so any later result for the same table and column,
        # other than a skipped check, replaces an earlier failure; a later failure of the same check replaces it too.
        succeeded = {up(t, "") for t in later.rows} | {up(*k[:2]) for k in later.columns}
        for name in ("values", "years", "spans", "fanout"):
            succeeded |= {up(*k[:2]) for k in getattr(later, name)}
        succeeded |= {up(t, c) for _, t, c, _ in later.sampled + later.ran}
        again = {up(t, c) for t, c, _ in later.failed}
        kept = [item for item in self.failed if up(item[0], item[1]) not in succeeded | again]
        found.failed = kept + [item for item in later.failed if item not in kept]
        # Failures that could not be named by a column of the catalogue are counted, as before, but not kept.
        unnamed = max(self.errors - len(self.failed), 0) + max(later.errors - len(later.failed), 0)
        found.errors = unnamed + len(found.failed)
        return found

    def confirmed(self, catalogue):
        """For each column, the listed values keyed by their normalised form. An ambiguous key is left out."""
        result = {}
        for (table, column), listed in self.values.items():
            numeric = is_numeric(catalogue.table(table).column(column))
            keyed, clashed = {}, set()
            for value, _, _ in listed:
                key = normalise(value, numeric)
                if key in keyed and keyed[key] != value:
                    clashed.add(key)
                keyed[key] = value
            result[(table.upper(), column.upper())] = {k: val for k, val in keyed.items() if k not in clashed}
        return result

    def to_csv(self):
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(LAYOUT)
        for (table, column), stats in sorted(self.columns.items()):
            writer.writerow(["column", table, column, "", "", stats["rows"], stats["distinct"], stats["nulls"],
                             "Y" if stats["unique"] else "N"])
        for table, count in sorted(self.rows.items()):
            writer.writerow(["rows", table, "", "", "", count, "", "", ""])
        for (table, column), listed in sorted(self.values.items()):
            for value, label, count in sorted(listed):
                writer.writerow(["values", table, column, value, label, count, "", "", ""])
        for (table, column), listed in sorted(self.years.items()):
            for year, count in sorted(listed):
                writer.writerow(["years", table, column, year, "", count, "", "", ""])
        for (table, column, later), listed in sorted(self.spans.items()):
            for band, count in sorted(listed, key=lambda item: BAND_LABELS.index(item[0])):
                writer.writerow(["spans", table, column, band, later, count, "", "", ""])
        for (table, column, parent, key), listed in sorted(self.fanout.items()):
            for band, count in sorted(listed, key=lambda item: FANOUT_LABELS.index(item[0])):
                writer.writerow(["fanout", table, column, band, f"{parent}.{key}", count, "", "", ""])
        for kind, table, column, reason in sorted(set(self.skipped)):
            writer.writerow(["skipped", table, column, kind, reason, "", "", "", ""])
        for kind, table, column, other in sorted(set(self.ran)):
            writer.writerow(["ran", table, column, kind, other, "", "", "", ""])
        for kind, table, column, percent in sorted(set(self.sampled)):
            writer.writerow(["sampled", table, column, kind, "", percent, "", "", ""])
        for table, column, number in sorted(set(self.failed)):
            writer.writerow(["error", table, column, "", "", number, "", "", ""])
        return out.getvalue()
