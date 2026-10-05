"""Tests for the findings of the correctness and security reviews of 4 October 2026."""
import csv
import io
import json
import sys
from pathlib import Path

import pytest

from schemalyser import Analysis, browser
from schemalyser.catalogue import Catalogue, CatalogueError
from schemalyser.checks import LAYOUT, Checks, ChecksError, normalise, plan
from schemalyser.rules import SiteRules
from schemalyser.statements import separate
from schemalyser.translate import Unsupported, to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_text()
HEADER = "TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,DATA_TYPE,CHARACTER_MAXIMUM_LENGTH,NUMERIC_PRECISION,NUMERIC_SCALE,IS_NULLABLE\n"


def analyse(sql, checks_csv=None, catalogue=CATALOGUE):
    analysis = Analysis(catalogue, checks_csv=checks_csv)
    analysis.add_request("one.sql", sql)
    return analysis


def rows(analysis, name):
    return list(csv.DictReader(io.StringIO(analysis.pack()[name])))


def filters(sql):
    return {(r["table"], r["column"], r["operator"]) for r in rows(analyse(sql), "filters.csv")}


# --- correctness ---------------------------------------------------------------------------------

@pytest.mark.parametrize("opening", ["SET NOCOUNT ON", "DECLARE @d date = '2020-01-01'", "SET ANSI_NULLS ON\nSET NOCOUNT ON"])
def test_a_request_that_opens_without_semicolons_is_still_read(opening):
    analysis = analyse(opening + "\nSELECT tc.CASE_KEY FROM THEATRE_CASE tc WHERE tc.CASE_STATUS_CAT = 2")
    assert ("THEATRE_CASE", "CASE_STATUS_CAT", "=") in {(r["table"], r["column"], r["operator"])
                                                         for r in rows(analysis, "filters.csv")}


def test_one_impossible_request_does_not_end_the_run():
    analysis = Analysis(CATALOGUE)
    analysis.add_request("deep.sql", "SELECT " + "(" * 400 + "1" + ")" * 400)
    analysis.add_request("long.sql", "SELECT tc.SERVICE_CAT" + " + tc.SERVICE_CAT" * 3000 + " FROM THEATRE_CASE tc")
    analysis.add_request("huge.sql", "SELECT 1 FROM THEATRE_CASE tc WHERE tc.SERVICE_CAT = '1E1000000'")
    analysis.add_request("fine.sql", "SELECT tc.CASE_KEY FROM THEATRE_CASE tc")
    assert "THEATRE_CASE,CASE_KEY" in analysis.pack()["elements.csv"]
    assert normalise("1E1000000", numeric=True) == "1E1000000"


def test_not_is_applied_to_every_kind_of_comparison():
    found = filters("""SELECT 1 FROM PERSON_MASTER p WHERE NOT p.SEX_CAT = 1 AND NOT (p.TEST_PERSON_FLAG IN ('Y'))
                       AND NOT NOT p.RECORD_NO = 'x' AND NOT p.BIRTH_TS < '2020-01-01'""")
    assert found == {("PERSON_MASTER", "SEX_CAT", "<>"), ("PERSON_MASTER", "TEST_PERSON_FLAG", "NOT IN"),
                     ("PERSON_MASTER", "RECORD_NO", "="), ("PERSON_MASTER", "BIRTH_TS", ">=")}


def test_insert_without_a_column_list_fills_columns_in_declared_order():
    found = filters("""CREATE TABLE #t (SEX_CAT int, CASE_STATUS_CAT int);
        INSERT INTO #t SELECT tc.CASE_STATUS_CAT, p.SEX_CAT FROM THEATRE_CASE tc JOIN PERSON_MASTER p ON p.PERSON_KEY = tc.PERSON_KEY;
        SELECT 1 FROM #t t WHERE t.SEX_CAT = 2""")
    assert found == {("THEATRE_CASE", "CASE_STATUS_CAT", "=")}


def test_a_temp_table_made_again_forgets_its_old_columns():
    found = filters("""SELECT p.PERSON_KEY AS X, p.SEX_CAT AS Y INTO #t FROM PERSON_MASTER p;
        DROP TABLE #t;
        SELECT v.VISIT_KEY AS X, v.VISIT_KIND_CAT AS Y INTO #t FROM VISIT v;
        SELECT 1 FROM #t t WHERE t.Y = 5""")
    assert found == {("VISIT", "VISIT_KIND_CAT", "=")}


def test_update_and_delete_are_analysed():
    analysis = analyse("""UPDATE t SET t.CASE_KEY = '1' FROM #t t JOIN THEATRE_CASE tc ON tc.CASE_KEY = t.CASE_KEY
        JOIN ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY WHERE tc.CASE_STATUS_CAT = 2;
        DELETE FROM VISIT WHERE VISIT_KIND_CAT = 9""")
    assert {(r["table"], r["column"]) for r in rows(analysis, "filters.csv")} == {
        ("THEATRE_CASE", "CASE_STATUS_CAT"), ("VISIT", "VISIT_KIND_CAT")}
    assert rows(analysis, "joins.csv")[0]["left_table"] == "ANAES_RECORD"
    assert "References to tables that are not in the catalogue" not in analysis.pack()["coverage.txt"]


def test_a_union_column_is_attributed_only_when_every_branch_agrees():
    differs = filters("""WITH u AS (SELECT p.SEX_CAT AS v FROM PERSON_MASTER p UNION ALL SELECT tc.SERVICE_CAT FROM THEATRE_CASE tc)
        SELECT 1 FROM u WHERE u.v = 1""")
    agrees = filters("""WITH u AS (SELECT p.SEX_CAT AS v FROM PERSON_MASTER p UNION ALL SELECT q.SEX_CAT FROM PERSON_MASTER q)
        SELECT 1 FROM u WHERE u.v = 1""")
    assert differs == set() and agrees == {("PERSON_MASTER", "SEX_CAT", "=")}


def test_text_values_are_compared_as_text_and_numbers_as_numbers():
    assert normalise("01") == "01" and normalise(" y ") == "Y"
    assert normalise("1.0", numeric=True) == "1" == normalise("1", numeric=True) and normalise("-0", numeric=True) == "0"
    checks = ",".join(LAYOUT) + "\nvalues,THEATRE_CASE,EMERGENCY_FLAG,Y,,20,,,\nvalues,THEATRE_CASE,CASE_STATUS_CAT,2,,20,,,\n"
    analysis = analyse("SELECT 1 FROM THEATRE_CASE tc WHERE tc.EMERGENCY_FLAG = 'y' AND tc.CASE_STATUS_CAT = 2.0", checks)
    assert {(r["column"], r["value"]) for r in rows(analysis, "filters.csv")} == {("EMERGENCY_FLAG", "Y"), ("CASE_STATUS_CAT", "2")}


def test_the_catalogue_leaves_out_what_it_cannot_use_safely():
    text = HEADER + "\n".join([
        "dbo,GOOD,A,1,int,,10,0,NO",
        "dbo,TWICE,A,1,int,,10,0,NO", "other,TWICE,B,1,int,,10,0,NO",
        "dbo,SHORT",
        "dbo]; EXEC xp_cmdshell 'whoami'; --,INJECT,A,1,int,,10,0,NO",
        "dbo,BAD'NAME,A,1,int,,10,0,NO", 'dbo,OK,Q"uote,1,int,,10,0,NO',
        "dbo,GOOD,B,2,=cmd|' /C calc'!A0,,,,YES",
    ]) + "\n"
    catalogue = Catalogue.from_csv(text)
    assert {t.name for t in catalogue.tables()} == {"GOOD"}
    assert catalogue.table("GOOD").column("B").data_type == ""
    with pytest.raises(CatalogueError):
        Catalogue.from_csv("a,b\n1,2\n")
    assert browser.start(b"dbo,PAT\n") == "catalogue"


def test_statement_separation_leaves_names_and_cursors_alone():
    for text in ("SELECT [SET], [USE] FROM t", "SELECT t.print FROM t", "DECLARE c CURSOR FOR SELECT 1"):
        assert separate(text) == text


def test_select_that_assigns_a_variable_is_refused_by_the_sandbox():
    with pytest.raises(Unsupported):
        to_duckdb("DECLARE @n int; SELECT @n = COUNT(*) FROM THEATRE_CASE")


def test_a_long_condition_is_analysed_in_reasonable_time():
    import time
    sql = "SELECT 1 FROM THEATRE_CASE tc WHERE " + " OR ".join(f"tc.SERVICE_CAT = {i}" for i in range(3000))
    started = time.perf_counter()
    analyse(sql)
    assert time.perf_counter() - started < 5


# --- security ------------------------------------------------------------------------------------

def test_names_cannot_carry_sql_into_the_check_script():
    from schemalyser.checks import Check, _bracket, _text
    assert _bracket("a]b") == "[a]]b]" and _text("a'b") == "'a''b'"
    script = make_checks.analysis().check_script()
    for line in script.splitlines():
        if line.strip().startswith("EXEC sys.sp_executesql"):
            assert line.count("'") % 2 == 0


def test_the_checks_never_list_keys_to_people():
    rules = SiteRules(person_tables=["STAFF_MASTER"], person_key_columns=["PERSON_KEY"])
    analysis = analyse("""SELECT 1 FROM ANAES_STAFF st JOIN STAFF_MASTER sm ON sm.STAFF_KEY = st.STAFF_KEY
        CROSS JOIN VISIT v WHERE st.STAFF_KEY = 'S1' AND v.PERSON_KEY = 'P1' AND st.ROLE_CAT = 1""")
    findings = set().union(*(r.findings for r in analysis._requests.values()))
    listed = {(c.table, c.column) for c in plan(analysis.catalogue, rules, findings) if c.kind == "values"}
    assert listed == {("ANAES_STAFF", "ROLE_CAT")}


def test_check_results_are_held_to_the_rules_of_the_script():
    catalogue = Catalogue.from_csv(CATALOGUE)
    lines = [
        "values,THEATRE_CASE,EMERGENCY_FLAG,Y,,20,,,",                    # accepted
        "values,PROC_DEF,PROC_LABEL,Dr Quartermaine,MRN 9900112,20,,,",   # a long text column named like a label
        "values,THEATRE_CASE,EMERGENCY_FLAG,N,,7,,,",                     # a count under ten
        "values,THEATRE_CASE,EMERGENCY_FLAG,Q,,25,,,",                    # a count that was not rounded
        "values,THEATRE_CASE,ROOM_KEY,=HYPERLINK(1),,20,,,",              # a spreadsheet formula
        "values,STAFF_MASTER,STAFF_KEY,S1,,20,,,",                        # a table of people
    ]
    checks = Checks.from_csv(",".join(LAYOUT) + "\n" + "\n".join(lines) + "\n", catalogue, SiteRules(person_tables=["STAFF_MASTER"]))
    assert checks.values == {("THEATRE_CASE", "EMERGENCY_FLAG"): [("Y", "", 20)]}
    too_many = "\n".join(f"values,THEATRE_CASE,ROOM_KEY,R{i},,20,,," for i in range(201))
    assert not Checks.from_csv(",".join(LAYOUT) + "\n" + too_many + "\n", catalogue, SiteRules()).values
    with pytest.raises(ChecksError):
        Checks.from_csv(",".join(LAYOUT) + "\nrows,THEATRE_CASE,,,,²,,,\n", catalogue, SiteRules())


@pytest.fixture()
def sandbox():
    analysis = make_checks.analysis()
    assert browser.sandbox_start(CATALOGUE.encode(), make_checks.inventory_zip(analysis)) == "ok"
    browser.sandbox_build(100)
    return browser


def test_a_past_request_leaves_nothing_behind_in_the_sandbox(sandbox):
    sandbox.sandbox_requests_begin()
    sandbox.sandbox_request("a.sql", b"""DECLARE @mrn varchar(20) = '9900112';
        CREATE TABLE staff_Fenwick (n varchar(50));
        INSERT INTO PERSON_MASTER (GIVEN_NAME) VALUES ('Wilhelmina');
        SELECT * INTO #c FROM PERSON_MASTER;
        SELECT COUNT(*) FROM #c""")
    assert json.loads(sandbox.sandbox_requests_finish())["outcomes"] == [[1, "returned rows"]]
    for probe in ("SELECT name FROM duckdb_variables()", "SELECT table_name FROM duckdb_tables() WHERE table_name ILIKE '%fenwick%'",
                  "SELECT 1 FROM PERSON_MASTER WHERE GIVEN_NAME = 'Wilhelmina'"):
        assert json.loads(sandbox.sandbox_run(probe))["rows"] == [], probe
    assert json.loads(sandbox.sandbox_run("DECLARE @x int = 3; SELECT @x AS x"))["rows"] == [["3"]]
    assert json.loads(sandbox.sandbox_run("SELECT getvariable('var_x') AS x"))["rows"] == [[None]]
    browser.clear()
    assert not browser._outcomes


def test_the_sandbox_database_cannot_reach_outside_itself(sandbox):
    for sql in ("SELECT * FROM read_csv('http://example.com/x.csv')", "SELECT * FROM read_csv('/etc/passwd')",
                "COPY PERSON_MASTER TO 'out.csv'", "ATTACH 'other.db' AS o", "INSTALL httpfs", "SET enable_external_access = true"):
        result = sandbox._sandbox.con.execute("SELECT 1").fetchall() and None
        with pytest.raises(Exception):
            sandbox._sandbox.con.execute(sql)


def test_joined_date_columns_stay_dates_in_the_sandbox():
    analysis = analyse("SELECT 1 FROM THEATRE_CASE tc JOIN VISIT v ON v.ADMIT_TS = tc.CASE_DATE")
    assert browser.sandbox_start(CATALOGUE.encode(), make_checks.inventory_zip(analysis)) == "ok"
    browser.sandbox_build(50)
    result = json.loads(browser.sandbox_run("SELECT DATEADD(day, 1, tc.CASE_DATE) AS d FROM THEATRE_CASE tc"))
    assert result["status"] == "ok" and result["count"] == 50
