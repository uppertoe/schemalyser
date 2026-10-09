"""Runs the whole pipeline over a "world": a catalogue, some requests and a stand-in database.

A world is a folder holding

    catalogue.csv        the tables and columns, in the layout of the catalogue query
    requests/            the .sql files to analyse
    site-rules.json      optional
    design.sql           optional DuckDB statements that write designed values into the stand-in database
    planted-values.txt   optional strings that must never appear in any output

The harness plays every role in turn. It analyses the requests, plans the checks, builds a
stand-in database and answers the checks from it, analyses again with those answers, builds the
sandbox and runs the requests in it. It prints a scorecard, and writes it to scorecard.txt in the
world, so that a change to the tool can be judged by what it does to the numbers.

    python -m schemalyser.harness WORLD [--rows 600] [--dialect tsql] [--spans] [--fanout]

With --spans the checks include the spans between pairs of date columns, which the check script
leaves out by default. With --fanout they include the fanout checks, which count how many rows hold
each key value of a column that refers to another table, and the stand-in database is given a skewed
design, DESIGNED_FANOUT, for every such join from a column that does not key its own table: most
parents have one child and a few have many. The scorecard then gives, for each join, the
distribution that the check results asked for and the one that the sandbox built.
"""
import argparse
import csv
import io
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb

from . import roles as meanings
from . import tuning as tunable
from . import vocabulary as v
from .catalogue import Catalogue
from .extract import VALUE_KINDS, VALUE_OPERATORS, analyse_request, decode
from .rules import SiteRules
from .sandbox import (DATE_TYPES, FANOUT_BANDS, FANOUT_LABELS, KINDS, LAYOUT, MAXIMUM_DEFINITIONS,
                      MAXIMUM_VALUES, MINIMUM_COUNT, RUN_ORDER, SAMPLED_KINDS, UNCOUNTABLE_TYPES, Checks, Sandbox,
                      _definitions, _kind, _listable, _matches, _never_listed)
from .translate import Unreadable, Unsupported, to_duckdb
from .tuning import BANDS

LIMITS = {"@minimum_count": MINIMUM_COUNT, "@maximum_values": MAXIMUM_VALUES,
          "@maximum_definitions": MAXIMUM_DEFINITIONS}
# The skewed design of the stand-in database with --fanout: the share of parents in each band of child rows.
DESIGNED_FANOUT = (("1 row", 70), ("2 rows", 18), ("3 to 5 rows", 9), ("6 to 10 rows", 3))



# The checks that the stand-in world answers.
#
# plan chooses, from the findings of the requests, the checks whose answers shape the sandbox: the size of each table,
# the values of a short column that a request compares with a constant, the years of a date column, and, when asked
# for, the spans between two date columns and the fan-out of a join. Each check is one SELECT that returns counts
# alone, built from catalogue names and fixed text, and script writes them as one T-SQL script, which the SQL Server
# harness runs to show that the two engines give the same rows.

# The script is sparing with a large table. It reads a sample of one for the kinds of check that a sample
# can answer, and leaves out the kinds that need every row. The analyst can change the three numbers.
LARGE_TABLE_ROWS = 100_000_000
SAMPLE_PERCENT = 1
MINUTES_ALLOWED = 30
# The place of the sample's percentage in a statement. T-SQL takes no variable there, so the script
# writes the number in before it runs the statement.
PERCENT = "<<percent>>"
INSERT = "INSERT INTO #schemalyser_checks"
# Pairs of roles whose columns, in one table, are counted by a spans check: the first comes before the second.
SPAN_ROLES = (("anaesthetic_start", "anaesthetic_stop"), ("admission_time", "discharge_time"),
              ("placement_time", "removal_time"))


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


def _name(catalogue, table):
    entry = catalogue.table(table)
    return f"{_bracket(entry.schema)}.{_bracket(entry.name)}" if entry.schema else _bracket(entry.name)


def _source(catalogue, table, sampled=False):
    """A table as a statement reads it: the whole of it, or a sample of its pages."""
    return _name(catalogue, table) + (f" TABLESAMPLE SYSTEM ({PERCENT} PERCENT)" if sampled else "")


def _countable(column):
    return _kind(column) not in UNCOUNTABLE_TYPES


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


# The analysis of a world's requests, whose pack the sandbox is built from.

def _csv(header, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


class Analysis:
    """Holds the catalogue and the site rules, takes requests one at a time, and writes the pack."""

    def __init__(self, catalogue_csv, rules_json=None, dialect="tsql", checks_csv=None):
        self.dialect = dialect
        self.rules = SiteRules.from_json(rules_json)
        self.catalogue, self.held_back = Catalogue.from_csv(catalogue_csv).without(self.rules.local_table_patterns)
        self._requests = {}
        self.summary = {}
        # What the site rules say each column means, for the sandbox to use.
        self.roles = meanings.from_rules(self.rules, self.catalogue)
        # The overrides of the tunable parameters that the site rules give, and the date that means "still in place".
        self.tuning = tunable.accept(self.rules.tuning)
        self.still_in_place = tunable.sentinel(self.rules)
        self.checks = Checks.from_csv(checks_csv, self.catalogue, self.rules) if checks_csv else None
        self._confirmed = self.checks.confirmed(self.catalogue) if self.checks else {}

    def add_request(self, name, sql):
        self._requests[name] = analyse_request(sql, self.catalogue, self.held_back, self.dialect, self._confirmed)

    def check_script(self, include_spans=False, include_fanout=False):
        """The T-SQL script that asks the database what the findings leave open.

        include_spans adds the checks that count the time between two date columns of one table, and
        include_fanout the checks that count how many rows hold each key value of a column that refers
        to another table. Both are off by default, because the script's wording was approved before
        those checks existed.
        """
        planned = self.planned_checks(include_spans=include_spans, include_fanout=include_fanout)
        return script(planned, self.catalogue, v.CHECK_SCRIPT)

    def planned_checks(self, include_years=True, include_spans=False, include_fanout=False):
        findings = set().union(*(r.findings for r in self._requests.values())) if self._requests else set()
        unique = {key: stats["unique"] for key, stats in self.checks.columns.items()} if self.checks else {}
        return plan(self.catalogue, self.rules, findings, include_years, include_spans, include_fanout, unique)

    def checked_tables(self):
        """The definition tables that a planned check reads for labels, which the sandbox must also build.

        Every name comes from the catalogue, through the site rules' definition keys.
        """
        return sorted({c.definition[0] for c in self.planned_checks() if c.kind == "values" and c.definition})

    def request_index(self):
        """Which file each request number refers to. Shown on the page; never part of the pack."""
        return list(enumerate(sorted(self._requests), start=1))

    def pack(self):
        using = defaultdict(set)          # finding -> request numbers
        per_request = []
        totals = Counter()
        for number, name in self.request_index():
            result = self._requests[name]
            for finding in result.findings:
                using[finding].add(number)
            elements = sorted({f"{f[1]}.{f[2]}" for f in result.findings if f[0] == "column"})
            per_request.append([number, result.statements, sum(result.unresolved.values()), " ".join(elements)])
            totals["files"] += 1
            totals["files_not_fully_read"] += v.not_fully_read(result.unresolved)
            # The files that each reason applies to, so that the coverage says the reason that it knows.
            for kind in v.HIDES_SQL:
                totals[f"files_{kind}"] += bool(result.unresolved.get(kind))
            totals["statements"] += result.statements
            totals.update(result.unresolved)

        roles = defaultdict(lambda: defaultdict(set))   # (table, column) -> role -> request numbers
        for finding, numbers in using.items():
            if finding[0] == "column":
                roles[finding[1:3]][finding[3]] |= numbers
        elements = []
        for (table, column), by_role in sorted(roles.items()):
            data_type = self.catalogue.table(table).column(column).data_type
            everyone = set().union(*by_role.values())
            elements.append([table, column, data_type, len(everyone)] + [len(by_role[r]) for r in v.ROLES])

        def rows(kind):
            return sorted(list(f[1:]) + [len(n)] for f, n in using.items() if f[0] == kind)

        self.summary = {
            "files": totals["files"],
            "notFullyRead": totals["files_not_fully_read"],
            "tables": len({f[1] for f in using if f[0] == "table"}),
            "columns": len(elements),
            "joins": sum(1 for f in using if f[0] == "join"),
            "filters": sum(1 for f in using if f[0] == "filter"),
            "derivations": sum(1 for f in using if f[0] == "derivation"),
            "unread": [[v.UNRESOLVED_LABELS[c], totals[c]] for c in v.UNRESOLVED if totals[c]],
        }
        self.summary["sentences"] = [
            v.files_sentence(self.summary["files"], self.summary["notFullyRead"],
                             {kind: totals[f"files_{kind}"] for kind in v.HIDES_SQL}),
            v.found_sentence(*(self.summary[k] for k in ("tables", "columns", "joins", "filters", "derivations"))),
        ]
        coverage = list(self.summary["sentences"])
        coverage += [f"{label} {count}" for label, count in self.summary["unread"]] or [v.NOTHING_UNREAD]

        return {
            "elements.csv": _csv(["table", "column", "data_type", "requests", *v.ROLES], elements),
            "joins.csv": _csv(["left_table", "left_column", "right_table", "right_column", "join_kind", "requests"],
                              rows("join")),
            "filters.csv": _csv(["table", "column", "operator", "value_kind", "value", "requests"], rows("filter")),
            "derivations.csv": _csv(["expression", "columns", "requests"], rows("derivation")),
            "comparisons.csv": _csv(["left_table", "left_column", "operator", "right_table", "right_column", "requests"],
                                    rows("comparison")),
            "requests.csv": _csv(["request", "statements", "unresolved", "elements"], per_request),
            "coverage.txt": "\n".join(coverage) + "\n",
            **({"checks.csv": self.checks.to_csv()} if self.checks else {}),
            # The definition tables that the check script reads, so that the sandbox builds them as well.
            **({"checked.csv": _csv(["table"], [[t] for t in checked])} if (checked := self.checked_tables()) else {}),
            **({"roles.csv": meanings.to_csv(self.roles)} if self.roles else {}),
            **({"tuning.csv": tunable.to_csv(self.tuning, self.still_in_place)}
               if self.tuning or self.still_in_place else {}),
        }


class World:
    def __init__(self, catalogue, requests, rules=None, design=None, planted=None, dialect="tsql"):
        self.catalogue_path, self.requests_path = Path(catalogue), Path(requests)
        self.rules_path = Path(rules) if rules else None
        self.design_path = Path(design) if design else None
        self.planted_path = Path(planted) if planted else None
        self.dialect = dialect

    @classmethod
    def from_folder(cls, folder, dialect="tsql"):
        folder = Path(folder)
        optional = lambda name: folder / name if (folder / name).exists() else None  # noqa: E731
        return cls(folder / "catalogue.csv", folder / "requests", optional("site-rules.json"),
                   optional("design.sql"), optional("planted-values.txt"), dialect)

    def catalogue_text(self):
        return decode(self.catalogue_path.read_bytes())

    def request_files(self):
        return sorted(self.requests_path.rglob("*.sql"))

    def planted(self):
        if not self.planted_path:
            return []
        return [line for line in self.planted_path.read_text().splitlines() if line.strip()]

    def analysis(self, checks_csv=None):
        result = Analysis(self.catalogue_text(), self.rules_path.read_text() if self.rules_path else None,
                          dialect=self.dialect, checks_csv=checks_csv)
        for path in self.request_files():
            result.add_request(path.relative_to(self.requests_path).as_posix(), decode(path.read_bytes()))
        return result

    def truth(self, analysis, rows=600, fanout=False):
        """The stand-in database: the generator's tables, with the designed values written over them.

        With fanout, every join that a fanout check counts, from a column that does not key its own table,
        is given the skewed design DESIGNED_FANOUT.
        """
        designed = {}
        if fanout:
            catalogue = analysis.catalogue
            for check in analysis.planned_checks(include_fanout=True):
                first = catalogue.table(check.table).first_column() if check.kind == "fanout" else None
                if first is not None and not (first.name == check.column and first.nullable is False):
                    designed[(check.table, check.column, *check.parent)] = list(DESIGNED_FANOUT)
        sandbox = Sandbox(Catalogue.from_csv(self.catalogue_text()), inventory_zip(analysis), designed)
        sandbox.build(rows)
        if self.design_path:
            for statement in self.design_path.read_text().split(";\n"):
                if statement.strip():
                    try:
                        sandbox.con.execute(statement)
                    except duckdb.CatalogException:
                        pass    # the statement designs a table or column that these requests did not call for
        return sandbox.con


def inventory_zip(analysis):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in analysis.pack().items():
            archive.writestr(name, text)
    return out.getvalue()


def run_checks(analysis, con, include_spans=False, include_fanout=False):
    """Answers each planned check as the script would. Returns the rows in the script's layout, and the failures."""
    def run(sql):
        for name, number in LIMITS.items():
            sql = sql.replace(name, str(number))
        for statement in to_duckdb(sql + ";"):
            # SQL Server divides whole numbers into a whole number; DuckDB needs // to do the same.
            cursor = con.execute(statement.replace("/ 10) * 10", "// 10) * 10"))
        return cursor

    rows, failed = [], []
    for check in analysis.planned_checks(include_years=True, include_spans=include_spans, include_fanout=include_fanout):
        try:
            if check.kind == "values" and run(check.guard(analysis.catalogue)).fetchone()[0] > LIMITS[check.limit()]:
                continue
            cursor = run(check.select(analysis.catalogue))
        except Exception as error:  # the harness records the failure and carries on
            failed.append((check.kind, check.table, check.column, type(error).__name__))
            continue
        names = [n.strip() for n in check.columns().strip("()").split(",")]
        for values in cursor.fetchall():
            row = dict.fromkeys(LAYOUT, "")
            row.update({n: "" if value is None else str(value) for n, value in zip(names, values)})
            rows.append([row[n] for n in LAYOUT])
    return sorted(rows), failed


def checks_csv(rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(LAYOUT)
    writer.writerows(rows)
    return out.getvalue()


def fanout_built(con, catalogue, child, column):
    """The bands of a fanout check, as the sandbox holds them: (band, number of key values), unrounded."""
    sql = Check("fanout", child, column, parent=("P", "K")).select(catalogue)
    sql = sql.replace("HAVING COUNT_BIG(*) >= @minimum_count", "").replace("(g.n / 10) * 10", "g.n")
    return {row[3]: row[5] for row in con.execute(to_duckdb(sql + ";")[0]).fetchall()}


def scorecard(world, rows=600, include_spans=False, include_fanout=False):
    lines = []
    say = lines.append

    first = world.analysis()
    pack = first.pack()
    say(f"requests: {len(world.request_files())}")
    say("")
    say("ANALYSIS")
    say(pack["coverage.txt"].rstrip())

    planned = first.planned_checks(include_years=True, include_spans=include_spans, include_fanout=include_fanout)
    kinds = Counter(c.kind for c in planned)
    say("")
    say("CHECKS")
    say("planned: " + ", ".join(f"{kinds[k]} {k}" for k in KINDS if kinds[k]))
    con = world.truth(first, rows, include_fanout)
    answers, failed = run_checks(first, con, include_spans, include_fanout)
    answered = Counter(row[0] for row in answers)
    say("answered rows: " + ", ".join(f"{answered[k]} {k}" for k in KINDS if answered[k]))
    say(f"checks the stand-in database could not answer: {len(failed)}")
    for item in failed:
        say("  " + " ".join(item))

    text = checks_csv(answers)
    second = world.analysis(text)
    second_pack = second.pack()
    confirmed = [r for r in csv.DictReader(io.StringIO(second_pack["filters.csv"])) if r["value"]]
    say(f"filter values confirmed: {len(confirmed)} in {len({(r['table'], r['column']) for r in confirmed})} columns")

    say("")
    say("SANDBOX")
    for label, analysis in (("without check results", first), ("with check results", second)):
        sandbox = Sandbox(Catalogue.from_csv(world.catalogue_text()), inventory_zip(analysis))
        built = sandbox.build(rows)
        outcomes, reasons = Counter(), {}
        for path in world.request_files():
            name = path.relative_to(world.requests_path).as_posix()
            result = sandbox.run(decode(path.read_bytes()))
            if result["status"] != "ok":
                outcomes[v.OUTCOME_NOT_RUN] += 1
                reasons[name] = result["status"] + (": " + result["message"][:110] if result.get("message") else "")
            else:
                outcomes[v.OUTCOME_ROWS if result["anyRows"] else v.OUTCOME_NO_ROWS] += 1
        say(f"{label}: {built['tables']} tables, {built['rows']:,} rows; " + ", ".join(
            f"{outcomes[o]} {o}" for o in (v.OUTCOME_ROWS, v.OUTCOME_NO_ROWS, v.OUTCOME_NOT_RUN)))
        not_applied = built["rolesNotApplied"]
        last = sandbox
    say(f"roles that could not be applied, with check results: {len(not_applied)}")
    for item in not_applied:
        say(f"  {item['table']}.{item['column']}: {item['role']} ({item['reason']})")
    if include_fanout and second.checks:
        say("fanout, asked by the check results and built in the sandbox with them, as shares of key values:")
        provenance = {(f["child_table"], f["child_column"]): f["provenance"] for f in last.fanout}
        for (child, column, parent, key), asked in sorted(second.checks.fanout.items()):
            built_bands = fanout_built(last.con, second.catalogue, child, column)
            shares = []
            for bands in (dict(asked), built_bands):
                total = sum(bands.values()) or 1
                shares.append(" ".join(f"{round(100 * bands.get(label, 0) / total)}" for label in FANOUT_LABELS))
            say(f"  {child}.{column} -> {parent}.{key}: asked {shares[0]}; built {shares[1]} "
                f"({provenance.get((child, column), 'not a parent-child join in the sandbox')})")
    say("requests that could not be run:")
    for name, reason in sorted(reasons.items()):
        say(f"  {name}: {reason}")

    say("")
    say("PLANTED VALUES")
    outputs = "\n".join([*pack.values(), *second_pack.values(), first.check_script(), text]).lower()
    leaked = [value for value in world.planted() if value.lower() in outputs]
    say(f"planted values checked: {len(world.planted())}; found in an output: {len(leaked)}")
    for value in leaked:
        say(f"  LEAKED: {value}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.harness")
    parser.add_argument("world", type=Path)
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--dialect", default="tsql")
    parser.add_argument("--spans", action="store_true", help="include the spans checks")
    parser.add_argument("--fanout", action="store_true", help="include the fanout checks, over a skewed stand-in database")
    args = parser.parse_args()
    card = scorecard(World.from_folder(args.world, args.dialect), args.rows, args.spans, args.fanout)
    (args.world / "scorecard.txt").write_text(card)
    print(card, end="")


if __name__ == "__main__":
    main()
