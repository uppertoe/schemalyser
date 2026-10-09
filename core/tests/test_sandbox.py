"""The synthetic sandbox, built from a catalogue and the pack of a world's requests, and run as the page's practice
database once was and as the testbed's stand-in database still is.

The tests that ran through the earlier pages' bridge now run on the sandbox itself, since the bridge holds only the
functions of Describe the record.
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from schemalyser import first_ask
from schemalyser import vocabulary as v
from schemalyser.catalogue import Catalogue, CatalogueError
from schemalyser.extract import decode
from schemalyser.harness import Analysis
from schemalyser.sandbox import InventoryError, Sandbox
from schemalyser.translate import Unsupported, Unreadable, to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REQUESTS = FIXTURES / "requests"
CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_bytes()


@pytest.fixture(scope="module")
def inventory():
    analysis = Analysis(CATALOGUE.decode())
    for path in sorted(REQUESTS.rglob("*.sql")):
        analysis.add_request(path.relative_to(REQUESTS).as_posix(), path.read_text())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in analysis.pack().items():
            archive.writestr(name, text)
    return out.getvalue()


@pytest.fixture(scope="module")
def sandbox(inventory):
    return Sandbox(Catalogue.from_csv(CATALOGUE.decode()), inventory)


@pytest.fixture(scope="module")
def built(sandbox):
    return json.loads(json.dumps(sandbox.build(500)))


def _run(sandbox, sql):
    """A query's result, as the page's worker once passed it on."""
    return json.loads(json.dumps(sandbox.run(sql)))


def test_the_sandbox_builds_every_table_in_the_inventory(built):
    assert built == {"tables": 20, "rows": 10000, "hasValues": False,
                     "sentence": "Schemalyser has built 20 tables containing 10,000 rows.", "rolesNotApplied": []}


def test_joined_columns_share_values_so_joins_find_rows(built, sandbox):
    result = _run(sandbox, 
        "SELECT COUNT(*) AS n FROM THEATRE_CASE tc JOIN ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY "
        "JOIN PERSON_MASTER pm ON pm.PERSON_KEY = tc.PERSON_KEY")
    assert result["status"] == "ok" and result["rows"] == [["500"]]


def test_tsql_habits_are_translated(built, sandbox):
    sql = """
    DECLARE @from datetime = '2023-01-01';
    IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;
    SELECT TOP 5 tc.CASE_KEY, tc.VISIT_KEY INTO #c FROM [RPT].[dbo].[THEATRE_CASE] tc WITH (NOLOCK) WHERE tc.CASE_DATE >= @from;
    GO
    SELECT c.CASE_KEY, w.READ_TS FROM #c c
    OUTER APPLY (SELECT TOP 1 r.READ_TS FROM OBS_SHEET s JOIN OBS_READING r ON r.SHEET_KEY = s.SHEET_KEY
                 WHERE s.VISIT_KEY = c.VISIT_KEY ORDER BY r.READ_TS DESC) w;
    """
    result = _run(sandbox, sql)
    assert result["status"] == "ok", result
    assert result["columns"] == ["CASE_KEY", "READ_TS"] and result["count"] == 5
    # A second run starts clean, as a new session would.
    assert _run(sandbox, sql)["status"] == "ok"


def test_a_division_of_whole_numbers_gives_a_whole_number_as_sql_server_does(built, sandbox):
    # The sandbox answers as SQL Server would, so a count rounded down to ten is a whole number ending in 0.
    result = _run(sandbox,
        "SELECT (COUNT(*) / 10) * 10 AS n, CASE WHEN COUNT_BIG(*) >= 10 THEN (COUNT_BIG(*) / 10) * 10 END AS m, "
        "COUNT(*) / 2.0 AS half FROM (SELECT TOP 156 CASE_KEY FROM THEATRE_CASE) AS t")
    assert result["status"] == "ok" and result["rows"] == [["150", "150", "78.0"]], result


def test_the_first_query_reads_sql_servers_own_records(built, sandbox):
    # The first query reads INFORMATION_SCHEMA and sys.partitions, of which the sandbox keeps a copy, so that it
    # returns there what SQL Server would.
    catalogue = Catalogue.from_csv(CATALOGUE.decode())
    listed = _run(sandbox, first_ask.query(["VISIT"]))
    assert listed["status"] == "ok", listed
    assert listed["rows"][0][:3] == ["dbo", "VISIT", catalogue.table("VISIT").first_column().name]
    assert {row[-1] for row in listed["rows"]} == {"500"}
    assert all(row[4] == catalogue.table("VISIT").column(row[2]).data_type.lower() for row in listed["rows"])


def test_what_cannot_be_run_is_reported_by_kind(built, sandbox):
    assert _run(sandbox, "CREATE PROCEDURE p AS BEGIN SELECT 1 END")["status"] == "unsupported"
    assert _run(sandbox, "EXEC sp_executesql N'SELECT 1'")["status"] == "unsupported"
    assert _run(sandbox, "SELECT FROM WHERE")["status"] == "unreadable"
    result = _run(sandbox, "SELECT NO_SUCH_COLUMN FROM THEATRE_CASE")
    assert result["status"] == "database-error" and result["message"]


def test_the_past_requests_are_scored(built, sandbox):
    outcomes = [sandbox.outcome(decode(path.read_bytes())) for path in sorted(REQUESTS.rglob("*.sql"))]
    assert len(outcomes) == 15
    assert outcomes.count(v.OUTCOME_NOT_RUN) <= 5, outcomes
    counts = [outcomes.count(kind) for kind in (v.OUTCOME_ROWS, v.OUTCOME_NO_ROWS, v.OUTCOME_NOT_RUN)]
    assert v.requests_sentence(len(outcomes), *counts).startswith("Of 15 requests, ")


def test_a_file_that_is_not_an_inventory_is_refused():
    with pytest.raises(InventoryError):
        Sandbox(Catalogue.from_csv(CATALOGUE.decode()), b"not a zip")
    with pytest.raises(CatalogueError):
        Catalogue.from_csv("a,b\n")


def test_not_like_stays_negated_when_like_ignores_case():
    from schemalyser.translate import to_duckdb
    import duckdb
    [statement] = to_duckdb("SELECT 1 AS found WHERE 'Propofol' LIKE 'PROP%' AND 'Propofol' NOT LIKE '%INFUSION%'")
    assert duckdb.sql(statement).fetchall() == [(1,)]
    [statement] = to_duckdb("SELECT 1 AS found WHERE 'Propofol infusion' NOT LIKE '%INFUSION%'")
    assert duckdb.sql(statement).fetchall() == []


def test_a_float_without_a_size_is_double_precision_and_a_real_is_not():
    import duckdb
    [statement] = to_duckdb("SELECT ROUND(TRY_CAST('282.8' AS float) * 0.028349523125, 3)")
    assert duckdb.sql(statement).fetchall() == [(8.017,)]
    [statement] = to_duckdb("SELECT CAST(1.1 AS float(53)) AS a, CAST(1.1 AS float(10)) AS b, CAST(1.1 AS real) AS c, "
                            "'real' AS [real]")
    assert "DOUBLE" in statement and statement.count("REAL") == 2
    a, b, c, text = duckdb.sql(statement).fetchone()
    assert a == 1.1 and b != 1.1 and c != 1.1 and text == "real"
    statements = to_duckdb("DECLARE @t TABLE (a float, b real); DECLARE @x float = 0.1; SELECT @x * 3")
    assert statements[0].endswith("(a DOUBLE, b REAL)") and "AS DOUBLE" in statements[1]


SMALL_CATALOGUE = """TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,DATA_TYPE,CHARACTER_MAXIMUM_LENGTH,NUMERIC_PRECISION,NUMERIC_SCALE,IS_NULLABLE
dbo,EPISODE,OTHER_ID,4,int,,10,0,NO
dbo,EPISODE,EPISODE_ID,1,int,,10,0,NO
dbo,EPISODE,FLAG,2,varchar,1,,,YES
dbo,EPISODE,SHORT_CODE,3,varchar,2,,,YES
dbo,LOOSE,LOOSE_ID,1,int,,10,0,YES
dbo,NARROW,NARROW_CODE,1,varchar,2,,,NO
"""


def small_sandbox(rows):
    analysis = Analysis(SMALL_CATALOGUE)
    analysis.add_request("one.sql", "SELECT EPISODE_ID, FLAG, SHORT_CODE, OTHER_ID FROM EPISODE")
    analysis.add_request("two.sql", "SELECT LOOSE_ID FROM LOOSE")
    analysis.add_request("three.sql", "SELECT NARROW_CODE FROM NARROW")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in analysis.pack().items():
            archive.writestr(name, text)
    sandbox = Sandbox(Catalogue.from_csv(SMALL_CATALOGUE), out.getvalue())
    sandbox.build(rows)
    return sandbox


def test_the_catalogue_keeps_each_column_position_and_whether_it_may_be_empty():
    episode = Catalogue.from_csv(SMALL_CATALOGUE).table("EPISODE")
    assert episode.first_column().name == "EPISODE_ID"
    assert episode.column("EPISODE_ID").nullable is False and episode.column("FLAG").nullable is True
    assert episode.column("OTHER_ID").position == 4


def test_text_filler_fits_its_column_and_a_row_key_is_unique():
    sandbox = small_sandbox(300)
    one = lambda sql: sandbox.con.execute(sql).fetchone()  # noqa: E731
    assert one("SELECT MAX(length(FLAG)), MAX(length(SHORT_CODE)) FROM EPISODE") == (1, 2)
    assert one("SELECT MAX(length(NARROW_CODE)) FROM NARROW") == (2,)
    # The first column of a table, where it may not be empty, keys the rows, so ordering by it has no ties.
    assert one("SELECT COUNT(DISTINCT EPISODE_ID), COUNT(*) FROM EPISODE") == (300, 300)
    # A column that may be empty, or that is not the first, keeps the filler.
    assert one("SELECT COUNT(DISTINCT LOOSE_ID) FROM LOOSE")[0] < 300
    assert one("SELECT COUNT(DISTINCT OTHER_ID) FROM EPISODE")[0] < 300
    result = sandbox.run("SELECT EPISODE_ID, ROW_NUMBER() OVER (ORDER BY EPISODE_ID) AS n FROM EPISODE")
    assert result["status"] == "ok" and all(int(key) == int(n) for key, n in result["rows"])


def test_the_tables_that_the_audits_steps_read_are_built_as_well(inventory):
    # A table that no SQL file reads, but that the audit's steps read, is built, so that the first query finds it.
    catalogue = Catalogue.from_csv(CATALOGUE.decode())
    assert "AIRWAY_DEVICE" not in Sandbox(catalogue, inventory).tables
    sandbox = Sandbox(catalogue, inventory, also={"AIRWAY_DEVICE", "NOT_IN_THE_CATALOGUE"})
    assert "AIRWAY_DEVICE" in sandbox.tables and "NOT_IN_THE_CATALOGUE" not in sandbox.tables
    sandbox.build(50)
    result = sandbox.run(first_ask.query(["AIRWAY_DEVICE"]))
    assert result["status"] == "ok" and result["rows"][0][1] == "AIRWAY_DEVICE", result


# Carried from the reviews of 4 October 2026: the sandbox keeps nothing of a past request and reaches nothing outside
# itself.

FIXTURES_FOR_CHECKS = Path(__file__).resolve().parents[2] / "fixtures"
sys.path.insert(0, str(FIXTURES_FOR_CHECKS))
import make_checks  # noqa: E402


@pytest.fixture()
def small_world():
    sandbox = Sandbox(Catalogue.from_csv(CATALOGUE.decode()), make_checks.inventory_zip(make_checks.analysis()))
    sandbox.build(100)
    return sandbox


def test_a_past_request_leaves_nothing_behind_in_the_sandbox(small_world):
    sandbox = small_world
    assert sandbox.outcome("""DECLARE @mrn varchar(20) = '9900112';
        CREATE TABLE staff_Fenwick (n varchar(50));
        INSERT INTO PERSON_MASTER (GIVEN_NAME) VALUES ('Wilhelmina');
        SELECT * INTO #c FROM PERSON_MASTER;
        SELECT COUNT(*) FROM #c""") == v.OUTCOME_ROWS
    for probe in ("SELECT name FROM duckdb_variables()", "SELECT table_name FROM duckdb_tables() WHERE table_name ILIKE '%fenwick%'",
                  "SELECT 1 FROM PERSON_MASTER WHERE GIVEN_NAME = 'Wilhelmina'"):
        assert _run(sandbox, probe)["rows"] == [], probe
    assert _run(sandbox, "DECLARE @x int = 3; SELECT @x AS x")["rows"] == [["3"]]
    assert _run(sandbox, "SELECT getvariable('var_x') AS x")["rows"] == [[None]]


def test_the_sandbox_database_cannot_reach_outside_itself(small_world):
    for sql in ("SELECT * FROM read_csv('http://example.com/x.csv')", "SELECT * FROM read_csv('/etc/passwd')",
                "COPY PERSON_MASTER TO 'out.csv'", "ATTACH 'other.db' AS o", "INSTALL httpfs", "SET enable_external_access = true"):
        with pytest.raises(Exception):
            small_world.con.execute(sql)


def test_joined_date_columns_stay_dates_in_the_sandbox():
    analysis = Analysis(CATALOGUE.decode())
    analysis.add_request("one.sql", "SELECT 1 FROM THEATRE_CASE tc JOIN VISIT v ON v.ADMIT_TS = tc.CASE_DATE")
    sandbox = Sandbox(Catalogue.from_csv(CATALOGUE.decode()), make_checks.inventory_zip(analysis))
    sandbox.build(50)
    result = _run(sandbox, "SELECT DATEADD(day, 1, tc.CASE_DATE) AS d FROM THEATRE_CASE tc")
    assert result["status"] == "ok" and result["count"] == 50
