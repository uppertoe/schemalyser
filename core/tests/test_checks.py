import csv
import io
import json
import sys
from pathlib import Path

import pytest

from schemalyser import browser
from schemalyser.catalogue import Catalogue
from schemalyser.checks import (LARGE_TABLE_ROWS, LAYOUT, MINUTES_ALLOWED, RUN_ORDER, SAMPLE_PERCENT, SAMPLED_KINDS,
                                Checks, ChecksError)
from schemalyser.rules import SiteRules

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REQUESTS = FIXTURES / "requests"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]
CHECKS = (FIXTURES / "invented-checks.csv").read_text()


@pytest.fixture(scope="module")
def without():
    return make_checks.analysis()


@pytest.fixture(scope="module")
def with_checks():
    return make_checks.analysis(CHECKS)


def rows(pack, name):
    return list(csv.DictReader(io.StringIO(pack[name])))


def test_the_script_asks_only_about_catalogue_names_and_carries_nothing_from_the_requests(without):
    script = without.check_script()
    assert script.count("BEGIN TRY") == len(without.planned_checks()) > 20
    assert "FROM [dbo].[THEATRE_CASE]" in script
    # Each check runs through sp_executesql, so that a missing table or column fails that check alone.
    # A check that a sample can answer has a second form, for a large table.
    sampled = sum(1 for c in without.planned_checks() if c.kind in SAMPLED_KINDS)
    assert script.count("EXEC sys.sp_executesql") == script.count("BEGIN TRY") + sampled
    assert "DROP TABLE IF EXISTS #schemalyser_checks;" in script
    # No values check is planned for a long text column or for one named like free text.
    listed = {(c.table, c.column) for c in without.planned_checks() if c.kind == "values"}
    assert ("PERSON_MASTER", "RECORD_NO") in listed          # short text: the limits in the script protect it
    assert ("DRUG_DEF", "DRUG_LABEL") not in listed and ("OBS_READING", "READ_VALUE") not in listed
    for value in PLANTED:
        assert value.lower() not in script.lower(), value
    # No values check is planned for a table that the site rules mark as describing people.
    assert not [c for c in without.planned_checks() if c.kind == "values" and c.table == "STAFF_MASTER"]
    # A definition key is joined to its definition table for a label.
    assert "FROM [dbo].[OBS_TYPE_DEF] AS d WHERE d.[OBS_TYPE_KEY] = g.k" in script
    assert "@maximum_definitions + 1) [OBS_TYPE_KEY]" in script


def test_the_invented_check_results_are_up_to_date(without):
    produced = make_checks.run_checks(without, make_checks.truth(without))
    assert [list(LAYOUT)] + produced == list(csv.reader(io.StringIO(CHECKS)))


def test_identifiers_of_people_are_not_listed():
    # The record numbers are unique, so the script lists none of them.
    assert "RECORD_NO,99" not in CHECKS
    for value in PLANTED:
        assert value.lower() not in CHECKS.lower(), value


def test_confirmed_values_are_written_and_unconfirmed_ones_stay_blank(with_checks):
    pack = with_checks.pack()
    filters = {(r["table"], r["column"], r["operator"], r["value"]) for r in rows(pack, "filters.csv")}
    assert ("THEATRE_CASE", "CASE_STATUS_CAT", "NOT IN", "4") in filters
    assert ("THEATRE_CASE", "CASE_STATUS_CAT", "NOT IN", "6") in filters
    assert ("OBS_READING", "OBS_TYPE_KEY", "IN", "30417") in filters
    assert ("THEATRE_CASE", "EMERGENCY_FLAG", "=", "Y") in filters
    # The record numbers in the requests are not in the check results, so they are not written.
    assert {f[3] for f in filters if f[:2] == ("PERSON_MASTER", "RECORD_NO")} == {""}
    text = "\n".join(pack.values()).lower()
    for value in PLANTED:
        assert value.lower() not in text, value
    assert "checks.csv" in pack


def test_every_value_written_comes_from_the_check_results(with_checks):
    listed = {(t, c, value) for (t, c), values in with_checks.checks.values.items() for value, _, _ in values}
    for row in rows(with_checks.pack(), "filters.csv"):
        if row["value"]:
            assert (row["table"], row["column"], row["value"]) in listed


def test_a_file_in_the_wrong_layout_is_refused(without):
    catalogue, rules = without.catalogue, SiteRules()
    with pytest.raises(ChecksError):
        Checks.from_csv("a,b,c\n1,2,3\n", catalogue, rules)
    with pytest.raises(ChecksError):
        Checks.from_csv(",".join(LAYOUT) + "\nsample,THEATRE_CASE,,,,1,,,\n", catalogue, rules)
    with pytest.raises(ChecksError):
        Checks.from_csv("", catalogue, rules)
    # Rows about tables outside the catalogue, or about people, are left out.
    people = SiteRules(person_tables=["THEATRE_CASE"])
    text = ",".join(LAYOUT) + "\nrows,NOT_A_TABLE,,,,10,,,\nvalues,THEATRE_CASE,EMERGENCY_FLAG,Y,,10,,,\n"
    assert not Checks.from_csv(text, catalogue, people).values and not Checks.from_csv(text, catalogue, people).rows
    # A file saved without headers is read by position.
    headless = "\n".join(CHECKS.splitlines()[1:])
    assert Checks.from_csv(headless, catalogue, rules).values == Checks.from_csv(CHECKS, catalogue, rules).values


def test_the_sandbox_uses_the_check_results_and_more_requests_return_rows(without, with_checks):
    catalogue = (FIXTURES / "invented-catalogue.csv").read_bytes()
    counts = {}
    for label, analysis in (("without", without), ("with", with_checks)):
        assert browser.sandbox_start(catalogue, make_checks.inventory_zip(analysis)) == "ok"
        built = json.loads(browser.sandbox_build(500))
        assert built["hasValues"] == (label == "with")
        browser.sandbox_requests_begin()
        for path in sorted(REQUESTS.rglob("*.sql")):
            browser.sandbox_request(path.relative_to(REQUESTS).as_posix(), path.read_bytes())
        result = json.loads(browser.sandbox_requests_finish())
        counts[label] = [o for _, o in result["outcomes"]].count("returned rows")
        print(label, result["sentence"])
    assert counts["with"] > counts["without"]


# The spans checks: how long from one date column to another in the same table.

from schemalyser import Analysis, checks as checking  # noqa: E402

CATALOGUE_TEXT = (FIXTURES / "invented-catalogue.csv").read_text()
RULES_TEXT = (FIXTURES / "invented-site-rules.json").read_text()


def spans_of(analysis):
    return {(c.table, c.column, c.later) for c in analysis.planned_checks(include_spans=True) if c.kind == "spans"}


def test_spans_are_planned_only_when_asked_for_and_never_about_people(without):
    assert not [c for c in without.planned_checks() if c.kind == "spans"]
    assert "spans" not in without.check_script() and "DATEDIFF_BIG" not in without.check_script()
    # The site rules pair the start and the stop of the anaesthetic, and admission and discharge.
    assert spans_of(without) == {("ANAES_RECORD", "ANAES_START_TS", "ANAES_STOP_TS"), ("VISIT", "ADMIT_TS", "DISCH_TS")}
    # A comparison between two date columns of one table is counted too, earlier column first.
    analysis = Analysis(CATALOGUE_TEXT)
    analysis.add_request("one.sql", "SELECT ar.ANAES_KEY FROM ANAES_RECORD ar WHERE ar.ANAES_STOP_TS > ar.ANAES_START_TS")
    assert spans_of(analysis) == {("ANAES_RECORD", "ANAES_START_TS", "ANAES_STOP_TS")}
    # Never for a table of people, nor for a column that the rules mark as a key to a person.
    for rules in ('{"personTables": ["ANAES_RECORD"]}', '{"personKeyColumns": ["ANAES_STOP_TS"]}'):
        analysis = Analysis(CATALOGUE_TEXT, rules)
        analysis.add_request("one.sql", "SELECT ar.ANAES_KEY FROM ANAES_RECORD ar WHERE ar.ANAES_STOP_TS > ar.ANAES_START_TS")
        assert spans_of(analysis) == set()


def test_the_script_counts_the_spans_in_fixed_bands(without):
    script = without.check_script(include_spans=True)
    planned = without.planned_checks(include_spans=True)
    assert script.count("BEGIN TRY") == len(planned)
    assert script.count("EXEC sys.sp_executesql") == len(planned) + sum(1 for c in planned if c.kind in SAMPLED_KINDS)
    assert "-- These checks count, for each pair of date columns in one table" in script
    statement = next(line for line in script.splitlines()
                     if "''spans'', ''ANAES_RECORD''" in line and "TABLESAMPLE" not in line)
    assert statement.startswith("        EXEC sys.sp_executesql N'INSERT INTO #schemalyser_checks "
                                "(check_kind, table_name, column_name, value, label, row_count) SELECT ''spans'', "
                                "''ANAES_RECORD'', ''ANAES_START_TS'', g.b, ''ANAES_STOP_TS'', (g.n / 10) * 10 FROM ")
    assert "DATEDIFF_BIG(minute, [ANAES_START_TS], [ANAES_STOP_TS]) AS m FROM [dbo].[ANAES_RECORD] " in statement
    assert "WHEN d.m < 0 THEN ''less than zero minutes''" in statement and "ELSE ''a week or more'' END" in statement
    assert statement.endswith("HAVING COUNT_BIG(*) >= @minimum_count) AS g',")
    for label in checking.BAND_LABELS:
        assert f"''{label}''" in statement
    # The header gains the wording about spans before its last line, and the script without them is unchanged.
    from schemalyser.vocabulary import CHECK_SCRIPT
    header = CHECK_SCRIPT["header"]
    expected = header[:-1] + CHECK_SCRIPT["spans_header"] + header[-1:]
    assert script.split("\n\n")[0] == "\n".join("-- " + line for line in expected)
    assert without.check_script().split("\n\n")[0] == "\n".join("-- " + line for line in header)


def test_spans_results_are_read_and_hostile_rows_are_left_out(without):
    catalogue, rules = without.catalogue, SiteRules()
    head = ",".join(LAYOUT) + "\n"
    good = ("spans,ANAES_RECORD,ANAES_START_TS,120 to 239 minutes,ANAES_STOP_TS,50,,,\n"
            "spans,ANAES_RECORD,ANAES_START_TS,a week or more,ANAES_STOP_TS,10,,,\n")
    read = Checks.from_csv(head + good, catalogue, rules)
    assert read.spans == {("ANAES_RECORD", "ANAES_START_TS", "ANAES_STOP_TS"): [("120 to 239 minutes", 50),
                                                                               ("a week or more", 10)]}
    assert Checks.from_csv(read.to_csv(), catalogue, rules).spans == read.spans
    hostile = ("spans,ANAES_RECORD,ANAES_START_TS,about three hours,ANAES_STOP_TS,50,,,\n"      # a band not in the list
               "spans,ANAES_RECORD,ANAES_START_TS,=HYPERLINK(1),ANAES_STOP_TS,50,,,\n"          # a formula
               "spans,ANAES_RECORD,ANAES_START_TS,under 15 minutes,NOT_A_COLUMN,50,,,\n"       # not in the catalogue
               "spans,ANAES_RECORD,ANAES_START_TS,under 15 minutes,=1+1,50,,,\n"
               "spans,ANAES_RECORD,ANAES_START_TS,under 15 minutes,RISK_GRADE_CAT,50,,,\n"     # not a date column
               "spans,ANAES_RECORD,ANAES_START_TS,under 15 minutes,ANAES_START_TS,50,,,\n"     # the same column
               "spans,ANAES_RECORD,ANAES_START_TS,under 15 minutes,ANAES_STOP_TS,15,,,\n"      # not rounded
               "spans,ANAES_RECORD,ANAES_START_TS,15 to 29 minutes,ANAES_STOP_TS,0,,,\n"       # under the minimum
               "spans,NOT_A_TABLE,ANAES_START_TS,under 15 minutes,ANAES_STOP_TS,50,,,\n")
    assert Checks.from_csv(head + hostile, catalogue, rules).spans == {}
    people = SiteRules(person_tables=["ANAES_RECORD"])
    assert Checks.from_csv(head + good, catalogue, people).spans == {}
    with pytest.raises(ChecksError):
        Checks.from_csv(head + "spans,ANAES_RECORD,ANAES_START_TS,under 15 minutes,ANAES_STOP_TS,1e3,,,\n", catalogue, rules)


def test_spans_in_the_check_results_set_the_durations_of_the_anaesthetics():
    from schemalyser.catalogue import Catalogue
    from schemalyser.sandbox import Sandbox
    spans = ("spans,ANAES_RECORD,ANAES_START_TS,240 to 479 minutes,ANAES_STOP_TS,300,,,\n"
             "spans,ANAES_RECORD,ANAES_START_TS,less than zero minutes,ANAES_STOP_TS,20,,,\n")
    analysis = Analysis(CATALOGUE_TEXT, RULES_TEXT, checks_csv=CHECKS + spans)
    for path in sorted(REQUESTS.rglob("*.sql")):
        analysis.add_request(path.relative_to(REQUESTS).as_posix(), path.read_text())
    assert "spans,ANAES_RECORD" in analysis.pack()["checks.csv"]
    sandbox = Sandbox(Catalogue.from_csv(CATALOGUE_TEXT), make_checks.inventory_zip(analysis))
    sandbox.build(300)
    low, high = sandbox.con.execute("SELECT MIN(date_diff('minute', ANAES_START_TS, ANAES_STOP_TS)), "
                                    "MAX(date_diff('minute', ANAES_START_TS, ANAES_STOP_TS)) FROM ANAES_RECORD").fetchone()
    assert 240 <= low and high < 480
    row = next(r for r in sandbox.tuning.report() if r["key"] == "anaesthetic_durations_minutes")
    assert row["provenance"] == "check results"


# The fanout checks: how many rows hold each key value of a column that refers to another table.

def fanout_of(analysis):
    return {(c.table, c.column, c.parent) for c in analysis.planned_checks(include_fanout=True) if c.kind == "fanout"}


def test_fanout_is_planned_only_when_asked_for_and_never_about_people(without):
    assert not [c for c in without.planned_checks() if c.kind == "fanout"]
    assert "fanout" not in without.check_script() and "AS c FROM" not in without.check_script()
    planned = fanout_of(without)
    assert ("VISIT", "PERSON_KEY", ("PERSON_MASTER", "PERSON_KEY")) in planned
    assert ("ANAES_RECORD", "CASE_KEY", ("THEATRE_CASE", "CASE_KEY")) in planned
    # Never towards a column that is not its table's key, nor from a category column whose values are listed.
    assert not [p for p in planned if p[2] == ("THEATRE_CASE", "VISIT_KEY")]
    assert not [p for p in planned if p[0] == "THEATRE_CASE" and p[1] == "CASE_STATUS_CAT"]
    # Never for a table of people, in either place, nor for a key to a person.
    assert not [p for p in planned if "STAFF_MASTER" in (p[0], p[2][0])]
    sql = "SELECT v.VISIT_KEY FROM VISIT v JOIN PERSON_MASTER p ON p.PERSON_KEY = v.PERSON_KEY"
    for rules, expected in (("{}", 1), ('{"personTables": ["PERSON_MASTER"]}', 0), ('{"personTables": ["VISIT"]}', 0),
                            ('{"personKeyColumns": ["PERSON_KEY"]}', 0)):
        analysis = Analysis(CATALOGUE_TEXT, rules)
        analysis.add_request("one.sql", sql)
        assert len(fanout_of(analysis)) == expected, rules
    # Where check results show the parent's column not to be unique, or the child's to be unique, there is no
    # check from that child; a column that the results show to be unique may be a parent in turn.
    head = ",".join(LAYOUT) + "\n"
    for line, expected in (("column,PERSON_MASTER,PERSON_KEY,,,600,300,0,N\n", set()),
                           ("column,VISIT,PERSON_KEY,,,600,600,0,Y\n",
                            {("PERSON_MASTER", "PERSON_KEY", ("VISIT", "PERSON_KEY"))})):
        analysis = Analysis(CATALOGUE_TEXT, checks_csv=head + line)
        analysis.add_request("one.sql", sql)
        assert fanout_of(analysis) == expected


def test_the_script_counts_the_key_values_in_fixed_bands_and_never_returns_one(without):
    script = without.check_script(include_fanout=True)
    planned = without.planned_checks(include_fanout=True)
    assert script.count("BEGIN TRY") == len(planned)
    assert script.count("EXEC sys.sp_executesql") == len(planned) + sum(1 for c in planned if c.kind in SAMPLED_KINDS)
    assert "-- These checks count, for each column that the requests join to the key of another table" in script
    statement = next(line for line in script.splitlines() if "''fanout'', ''VISIT'', ''PERSON_KEY''" in line)
    assert statement == (
        "        EXEC sys.sp_executesql N'INSERT INTO #schemalyser_checks (check_kind, table_name, column_name, value, label, "
        "row_count) SELECT ''fanout'', ''VISIT'', ''PERSON_KEY'', g.b, ''PERSON_MASTER.PERSON_KEY'', (g.n / 10) * 10 "
        "FROM (SELECT CASE WHEN k.c < 2 THEN ''1 row'' WHEN k.c < 3 THEN ''2 rows'' WHEN k.c < 6 THEN ''3 to 5 rows'' "
        "WHEN k.c < 11 THEN ''6 to 10 rows'' ELSE ''11 or more rows'' END AS b, COUNT_BIG(*) AS n "
        "FROM (SELECT COUNT_BIG(*) AS c FROM [dbo].[VISIT] WHERE [PERSON_KEY] IS NOT NULL GROUP BY [PERSON_KEY]) AS k "
        "GROUP BY CASE WHEN k.c < 2 THEN ''1 row'' WHEN k.c < 3 THEN ''2 rows'' WHEN k.c < 6 THEN ''3 to 5 rows'' "
        "WHEN k.c < 11 THEN ''6 to 10 rows'' ELSE ''11 or more rows'' END HAVING COUNT_BIG(*) >= @minimum_count) AS g',")
    # The header gains the wording about fanout before its last line; without it the script is as approved.
    from schemalyser.vocabulary import CHECK_SCRIPT
    header = CHECK_SCRIPT["header"]
    expected = header[:-1] + CHECK_SCRIPT["spans_header"] + CHECK_SCRIPT["fanout_header"] + header[-1:]
    both = without.check_script(include_spans=True, include_fanout=True)
    assert both.split("\n\n")[0] == "\n".join("-- " + line for line in expected)
    assert without.check_script() == without.check_script(include_fanout=False)


def test_fanout_results_are_read_and_hostile_rows_are_left_out(without):
    catalogue, rules = without.catalogue, SiteRules()
    head = ",".join(LAYOUT) + "\n"
    good = ("fanout,VISIT,PERSON_KEY,1 row,PERSON_MASTER.PERSON_KEY,400,,,\n"
            "fanout,VISIT,PERSON_KEY,3 to 5 rows,PERSON_MASTER.PERSON_KEY,30,,,\n")
    read = Checks.from_csv(head + good, catalogue, rules)
    key = ("VISIT", "PERSON_KEY", "PERSON_MASTER", "PERSON_KEY")
    assert read.fanout == {key: [("1 row", 400), ("3 to 5 rows", 30)]}
    assert Checks.from_csv(read.to_csv(), catalogue, rules).fanout == read.fanout
    hostile = ("fanout,VISIT,PERSON_KEY,about two,PERSON_MASTER.PERSON_KEY,50,,,\n"          # a band not in the list
               "fanout,VISIT,PERSON_KEY,9900001,PERSON_MASTER.PERSON_KEY,50,,,\n"            # a key value
               "fanout,VISIT,PERSON_KEY,=HYPERLINK(1),PERSON_MASTER.PERSON_KEY,50,,,\n"      # a formula
               "fanout,VISIT,PERSON_KEY,2 rows,PERSON_MASTER.NO_SUCH_COLUMN,50,,,\n"         # not in the catalogue
               "fanout,VISIT,PERSON_KEY,2 rows,NOT_A_TABLE.PERSON_KEY,50,,,\n"
               "fanout,VISIT,PERSON_KEY,2 rows,PERSON_MASTER,50,,,\n"                        # no parent column
               "fanout,VISIT,PERSON_KEY,2 rows,=1+1,50,,,\n"
               "fanout,VISIT,PERSON_KEY,2 rows,VISIT.PERSON_KEY,50,,,\n"                     # the column itself
               "fanout,VISIT,NO_SUCH_COLUMN,2 rows,PERSON_MASTER.PERSON_KEY,50,,,\n"
               "fanout,NOT_A_TABLE,PERSON_KEY,2 rows,PERSON_MASTER.PERSON_KEY,50,,,\n"
               "fanout,VISIT,PERSON_KEY,2 rows,PERSON_MASTER.PERSON_KEY,15,,,\n"             # not rounded
               "fanout,VISIT,PERSON_KEY,6 to 10 rows,PERSON_MASTER.PERSON_KEY,0,,,\n")       # under the minimum
    assert Checks.from_csv(head + hostile, catalogue, rules).fanout == {}
    for people in (SiteRules(person_tables=["VISIT"]), SiteRules(person_tables=["PERSON_MASTER"]),
                   SiteRules(person_key_columns=["PERSON_KEY"])):
        assert Checks.from_csv(head + good, catalogue, people).fanout == {}
    with pytest.raises(ChecksError):
        Checks.from_csv(head + "fanout,VISIT,PERSON_KEY,2 rows,PERSON_MASTER.PERSON_KEY,4.5,,,\n", catalogue, rules)


def test_a_message_from_sql_server_among_the_results_is_passed_over(without):
    catalogue, rules = without.catalogue, SiteRules()
    # The script turns off the warning, and the reader passes it over where an older script printed it.
    assert "\nSET ANSI_WARNINGS OFF;\n" in without.check_script()
    warning = "Warning: Null value is eliminated by an aggregate or other SET operation.\n"
    lines = CHECKS.splitlines(keepends=True)
    text = "".join(lines[:3]) + warning + "".join(lines[3:]) + warning
    assert Checks.from_csv(text, catalogue, rules).values == Checks.from_csv(CHECKS, catalogue, rules).values
    assert Checks.from_csv(warning + "".join(lines[1:]), catalogue, rules).values  # before results without a header
    for other in ("Warning: something else happened.\n", "Msg 208, Level 16, State 1\n",
                  "Warning: Null value is eliminated by an aggregate or other SET operation. Or not.\n"):
        with pytest.raises(ChecksError):
            Checks.from_csv("".join(lines[:3]) + other + "".join(lines[3:]), catalogue, rules)


def test_a_definition_table_that_a_check_reads_is_built_in_the_sandbox():
    from schemalyser import harness
    # The request compares the definition key with a value but never reads the definition table itself.
    analysis = Analysis(CATALOGUE_TEXT, RULES_TEXT)
    analysis.add_request("one.sql", "SELECT r.READ_VALUE FROM OBS_READING r WHERE r.OBS_TYPE_KEY = '14'")
    assert "OBS_TYPE_DEF" not in analysis.pack()["elements.csv"]
    assert analysis.pack()["checked.csv"] == "table\nOBS_TYPE_DEF\n"
    from schemalyser.catalogue import Catalogue
    from schemalyser.sandbox import Sandbox
    sandbox = Sandbox(Catalogue.from_csv(CATALOGUE_TEXT), make_checks.inventory_zip(analysis))
    assert sandbox.tables == ["OBS_READING", "OBS_TYPE_DEF"]
    sandbox.build(800)
    rows, failed = harness.run_checks(analysis, sandbox.con)
    assert failed == [] and [r for r in rows if r[0] == "values" and r[2] == "OBS_TYPE_KEY"]
    # Without definition keys in the site rules there is nothing more to build.
    plain = Analysis(CATALOGUE_TEXT)
    plain.add_request("one.sql", "SELECT r.READ_VALUE FROM OBS_READING r WHERE r.OBS_TYPE_KEY = '14'")
    assert "checked.csv" not in plain.pack()


def test_the_script_spares_the_server(without):
    script = without.check_script(include_spans=True, include_fanout=True)
    planned = without.planned_checks(include_spans=True, include_fanout=True)
    # It reads without taking locks, gives way where it would wait for one, and is the one to lose a deadlock.
    for setting in ("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;", "SET LOCK_TIMEOUT 10000;",
                    "SET DEADLOCK_PRIORITY LOW;"):
        assert f"\n{setting}\n" in script
    # It writes nothing but its own temporary table.
    for word in ("UPDATE ", "DELETE ", "MERGE ", "ALTER ", "TRUNCATE ", "CREATE INDEX", "INTO [", "INTO dbo"):
        assert word not in script, word
    assert script.count("CREATE TABLE") == 1 and "CREATE TABLE #schemalyser_checks" in script
    # The analyst can change three numbers, and each check looks at the time and at the size of its table first.
    assert f"DECLARE @large_table_rows bigint = {LARGE_TABLE_ROWS};" in script
    assert f"DECLARE @sample_percent int = {SAMPLE_PERCENT};" in script
    assert f"DECLARE @minutes_allowed int = {MINUTES_ALLOWED};" in script
    assert script.count("SYSDATETIME() >= @deadline") == len(planned)
    assert script.count("FROM sys.partitions AS p WHERE p.object_id = OBJECT_ID(N'[dbo].[") == len(planned)
    # The cheapest and most useful kinds run first, and the two that need every row of a table run last.
    kinds = [c.kind for c in planned]
    assert kinds == sorted(kinds, key=RUN_ORDER.index) and set(kinds[-20:]) <= {"column", "fanout"}
    # On a large table a values, years or spans check reads a sample, and a column or fanout check is left out.
    for check in planned:
        named = f"'{check.table}', '{check.column}'" if check.column else f"'{check.table}'"
        left_out = f"VALUES ('skipped', {named}, '{check.kind}', 'size');"
        if check.kind in SAMPLED_KINDS:
            assert f"VALUES ('sampled', {named}, '{check.kind}', @sample_percent);" in script
            assert left_out not in script
            assert "TABLESAMPLE SYSTEM (<<percent>> PERCENT)" in check.select(without.catalogue, sampled=True)
            assert "((g.n * 100 / @sample_percent) / 10) * 10" in check.select(without.catalogue, sampled=True)
            assert "TABLESAMPLE" not in check.select(without.catalogue)
        elif check.kind in ("column", "fanout"):
            assert left_out in script
    # The percentage is written into the statement as a whole number, because T-SQL takes no variable there.
    assert "N'<<percent>>', CAST(@sample_percent AS nvarchar(3)));" in script
    assert "IF @sample_percent IS NULL OR @sample_percent NOT BETWEEN 1 AND 100 SET @sample_percent = 1;" in script
    for value in PLANTED:
        assert value.lower() not in script.lower(), value


def test_a_row_count_comes_from_the_servers_own_records():
    analysis = Analysis(CATALOGUE_TEXT, RULES_TEXT)
    analysis.add_request("one.sql", "SELECT c.CASE_KEY FROM THEATRE_CASE c")
    script = analysis.check_script()
    assert [c.kind for c in analysis.planned_checks()] == ["rows"]
    assert "IF @size IS NOT NULL" in script
    assert "VALUES ('rows', 'THEATRE_CASE', (@size / 10) * 10);" in script
    # Where the server records no number of rows, as for a view, the script counts them.
    assert "SELECT ''rows'', ''THEATRE_CASE'', (COUNT_BIG(*) / 10) * 10 FROM [dbo].[THEATRE_CASE]" in script


def test_skipped_and_sampled_rows_are_read_and_kept():
    catalogue, rules = Catalogue.from_csv(CATALOGUE_TEXT), SiteRules.from_json(RULES_TEXT)
    extra = ("skipped,OBS_READING,SHEET_KEY,fanout,size,,,,\n"
             "skipped,OBS_READING,SHEET_KEY,column,time,,,,\n"
             "skipped,THEATRE_CASE,,rows,time,,,,\n"
             "sampled,OBS_READING,OBS_TYPE_KEY,values,,1,,,\n"
             # Rows that the script could not have written are left out: an unknown reason, an unknown kind of
             # check, a kind that is never sampled, and a percentage that is no percentage.
             "skipped,OBS_READING,SHEET_KEY,fanout,because I said so,,,,\n"
             "skipped,OBS_READING,SHEET_KEY,DROP TABLE,size,,,,\n"
             "sampled,OBS_READING,SHEET_KEY,fanout,,1,,,\n"
             "sampled,OBS_READING,OBS_TYPE_KEY,values,,500,,,\n")
    checks = Checks.from_csv(CHECKS + extra, catalogue, rules)
    assert sorted(checks.skipped) == [("column", "OBS_READING", "SHEET_KEY", "time"),
                                      ("fanout", "OBS_READING", "SHEET_KEY", "size"),
                                      ("rows", "THEATRE_CASE", "", "time")]
    assert checks.sampled == [("values", "OBS_READING", "OBS_TYPE_KEY", 1)]
    assert checks.values == Checks.from_csv(CHECKS, catalogue, rules).values
    again = Checks.from_csv(checks.to_csv(), catalogue, rules, limit_values=False)
    assert sorted(again.skipped) == sorted(checks.skipped) and again.sampled == checks.sampled
    from schemalyser import vocabulary
    sentence = vocabulary.checks_skipped_sentence(2, 1)
    assert "left out 2 checks because its time had run out" in sentence and "left out 1 check because" in sentence
    assert vocabulary.checks_skipped_sentence(0, 0) == ""
