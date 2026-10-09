"""Tests for the faults that the realistic world exposed."""
import csv
import io
import json
import sys
from pathlib import Path

from schemalyser.catalogue import Catalogue
from schemalyser.harness import Analysis
from schemalyser.sandbox import Sandbox
from schemalyser.statements import drop_old_hints, separate
from schemalyser.translate import to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_text()


def pack_for(sql):
    analysis = Analysis(CATALOGUE)
    analysis.add_request("one.sql", sql)
    return analysis.pack(), analysis


def rows(pack, name):
    return list(csv.DictReader(io.StringIO(pack[name])))


def test_statements_without_semicolons_are_read():
    sql = """SELECT tc.CASE_KEY INTO #c FROM THEATRE_CASE tc WHERE tc.EMERGENCY_FLAG = 'Y'
CREATE CLUSTERED INDEX ix ON #c (CASE_KEY)
SELECT ar.ANAES_KEY FROM #c c JOIN ANAES_RECORD ar ON ar.CASE_KEY = c.CASE_KEY"""
    pack, analysis = pack_for(sql)
    assert analysis._requests["one.sql"].parsed
    joins = {(r["left_table"], r["right_table"]) for r in rows(pack, "joins.csv")}
    assert ("ANAES_RECORD", "THEATRE_CASE") in joins
    # A statement that already has its semicolons is left alone, and so are UNION and INSERT ... SELECT.
    for text in ("SELECT 1; SELECT 2", "SELECT 1 UNION ALL SELECT 2", "INSERT INTO #t (a) SELECT 1",
                 "WITH a AS (SELECT 1 AS x) SELECT x FROM a", "UPDATE t SET a = 1", "SELECT a FROM t WITH (NOLOCK)"):
        assert separate(text) == text


def test_table_hints_without_with_are_read():
    assert drop_old_hints("FROM THEATRE_CASE tc (nolock) JOIN X x (NOLOCK) ON 1 = 1").count("(") == 0
    assert drop_old_hints("FROM T t WITH (NOLOCK)") == "FROM T t WITH (NOLOCK)"
    assert drop_old_hints("SELECT COUNT(nolock) FROM T") == "SELECT COUNT(nolock) FROM T"
    untouched = "SELECT 'from T x (nolock)' AS s FROM T x -- from T a (nolock)"
    assert drop_old_hints(untouched) == untouched
    pack, _ = pack_for("SELECT tc.CASE_KEY FROM THEATRE_CASE tc (nolock) WHERE tc.CASE_STATUS_CAT = 2")
    assert [r["column"] for r in rows(pack, "filters.csv")] == ["CASE_STATUS_CAT"]


def test_a_comparison_of_a_computed_value_is_not_a_filter_on_its_column():
    pack, _ = pack_for("""SELECT tc.SERVICE_CAT FROM THEATRE_CASE tc
        WHERE DATENAME(dw, tc.CASE_DATE) NOT IN ('Saturday', 'Sunday') AND tc.EMERGENCY_FLAG NOT LIKE 'N%'
        GROUP BY tc.SERVICE_CAT HAVING COUNT(DISTINCT tc.CASE_KEY) >= 10""")
    assert {(r["column"], r["operator"]) for r in rows(pack, "filters.csv")} == {("EMERGENCY_FLAG", "NOT LIKE")}


def test_comparisons_between_columns_are_recorded_in_order():
    pack, _ = pack_for("""SELECT r.READ_VALUE FROM ANAES_RECORD ar
        JOIN OBS_SHEET s ON s.ANAES_KEY = ar.ANAES_KEY JOIN OBS_READING r ON r.SHEET_KEY = s.SHEET_KEY
        WHERE r.READ_TS BETWEEN ar.ANAES_START_TS AND ar.ANAES_STOP_TS""")
    found = {(r["left_table"], r["left_column"], r["operator"], r["right_table"], r["right_column"])
             for r in rows(pack, "comparisons.csv")}
    assert found == {("ANAES_RECORD", "ANAES_START_TS", "<=", "OBS_READING", "READ_TS"),
                     ("OBS_READING", "READ_TS", "<=", "ANAES_RECORD", "ANAES_STOP_TS")}


def test_a_reading_falls_inside_its_anaesthetic_in_the_sandbox():
    sql = """SELECT COUNT(*) AS inside FROM ANAES_RECORD ar
        JOIN OBS_SHEET s ON s.ANAES_KEY = ar.ANAES_KEY JOIN OBS_READING r ON r.SHEET_KEY = s.SHEET_KEY
        WHERE r.READ_TS BETWEEN ar.ANAES_START_TS AND ar.ANAES_STOP_TS"""
    _, analysis = pack_for(sql)
    sandbox = Sandbox(Catalogue.from_csv(CATALOGUE), make_checks.inventory_zip(analysis))
    sandbox.build(200)
    assert json.loads(json.dumps(sandbox.run(sql)))["rows"] == [["200"]]


def test_the_year_counts_are_planned_for_date_columns_the_requests_use():
    analysis = make_checks.analysis()
    years = [c for c in analysis.planned_checks() if c.kind == "years"]
    assert ("THEATRE_CASE", "CASE_DATE") in {(c.table, c.column) for c in years}
    assert not [c for c in analysis.planned_checks(include_years=False) if c.kind == "years"]
    assert "GROUP BY YEAR([CASE_DATE])" in analysis.check_script()


def test_more_tsql_forms_are_translated():
    (xml,) = to_duckdb("""SELECT STUFF((SELECT ', ' + pd.PROC_LABEL FROM PROC_DEF pd
        FOR XML PATH(''), TYPE).value('.', 'nvarchar(max)'), 1, 2, '') AS all_procs""")
    assert "STRING_AGG" in xml and "XML" not in xml
    sql = "SELECT 1 FROM THEATRE_CASE tc WHERE tc.CASE_DATE >= '01-Jan-2017' AND tc.CASE_DATE < '20190101' AND tc.CASE_KEY = '20201231'"
    (dates,) = to_duckdb(sql, date_columns={"CASE_DATE"})
    # A string is read as a date only where it is compared with a date column.
    assert "'2017-01-01'" in dates and "'2019-01-01'" in dates and "'20201231'" in dates
    (xml_semicolon,) = to_duckdb("SELECT STUFF((SELECT '; ' + pd.PROC_LABEL FROM PROC_DEF pd ORDER BY pd.PROC_KEY FOR XML PATH('')), 1, 2, '') AS x")
    assert "ORDER BY" in xml_semicolon
    (rounded,) = to_duckdb("SELECT DATEADD(DAY, DATEDIFF(DAY, 0, tc.CASE_DATE), 0) FROM THEATRE_CASE tc")
    assert "CAST('1900-01-01' AS TIMESTAMP) + " in rounded


def test_the_fan_out_of_each_join_reports_where_it_came_from():
    from schemalyser import tuning
    joins = [(("VISIT", "PERSON_KEY"), ("PERSON_MASTER", "PERSON_KEY")), (("VISIT", "WARD_KEY"), ("WARD_DEF", "WARD_KEY")),
             (("DRUG_GIVEN", "ANAES_KEY"), ("ANAES_RECORD", "ANAES_KEY"))]
    rows = tuning.fanout_provenance(joins, applied={("VISIT", "PERSON_KEY")},
                                    given={("VISIT", "PERSON_KEY"), ("DRUG_GIVEN", "ANAES_KEY")})
    assert [r["provenance"] for r in rows] == ["from check results", "uniform (no check results)",
                                              "uniform (the check results could not be applied)"]
    rows = tuning.fanout_provenance(joins, applied=set(), given=set(), derived={("VISIT", "WARD_KEY")})
    assert rows[1]["provenance"] == "from check results on other joins"
    assert set(rows[0]) == {"child_table", "child_column", "parent_table", "parent_column", "provenance"}
