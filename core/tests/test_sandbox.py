import io
import json
import zipfile
from pathlib import Path

import pytest

from schemalyser import Analysis, browser
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
def built(inventory):
    assert browser.sandbox_start(CATALOGUE, inventory) == "ok"
    return json.loads(browser.sandbox_build(500))


def test_the_sandbox_builds_every_table_in_the_inventory(built):
    assert built == {"tables": 20, "rows": 10000, "hasValues": False,
                     "sentence": "Schemalyser has built 20 tables containing 10,000 rows.", "rolesNotApplied": []}


def test_joined_columns_share_values_so_joins_find_rows(built):
    result = json.loads(browser.sandbox_run(
        "SELECT COUNT(*) AS n FROM THEATRE_CASE tc JOIN ANAES_RECORD ar ON ar.CASE_KEY = tc.CASE_KEY "
        "JOIN PERSON_MASTER pm ON pm.PERSON_KEY = tc.PERSON_KEY"))
    assert result["status"] == "ok" and result["rows"] == [["500"]]


def test_tsql_habits_are_translated(built):
    sql = """
    DECLARE @from datetime = '2023-01-01';
    IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;
    SELECT TOP 5 tc.CASE_KEY, tc.VISIT_KEY INTO #c FROM [RPT].[dbo].[THEATRE_CASE] tc WITH (NOLOCK) WHERE tc.CASE_DATE >= @from;
    GO
    SELECT c.CASE_KEY, w.READ_TS FROM #c c
    OUTER APPLY (SELECT TOP 1 r.READ_TS FROM OBS_SHEET s JOIN OBS_READING r ON r.SHEET_KEY = s.SHEET_KEY
                 WHERE s.VISIT_KEY = c.VISIT_KEY ORDER BY r.READ_TS DESC) w;
    """
    result = json.loads(browser.sandbox_run(sql))
    assert result["status"] == "ok", result
    assert result["columns"] == ["CASE_KEY", "READ_TS"] and result["count"] == 5
    # A second run starts clean, as a new session would.
    assert json.loads(browser.sandbox_run(sql))["status"] == "ok"


def test_what_cannot_be_run_is_reported_by_kind(built):
    assert json.loads(browser.sandbox_run("CREATE PROCEDURE p AS BEGIN SELECT 1 END"))["status"] == "unsupported"
    assert json.loads(browser.sandbox_run("EXEC sp_executesql N'SELECT 1'"))["status"] == "unsupported"
    assert json.loads(browser.sandbox_run("SELECT FROM WHERE"))["status"] == "unreadable"
    result = json.loads(browser.sandbox_run("SELECT NO_SUCH_COLUMN FROM THEATRE_CASE"))
    assert result["status"] == "database-error" and result["message"]


def test_the_past_requests_are_scored(built):
    browser.sandbox_requests_begin()
    for path in sorted(REQUESTS.rglob("*.sql")):
        browser.sandbox_request(path.relative_to(REQUESTS).as_posix(), path.read_bytes())
    result = json.loads(browser.sandbox_requests_finish())
    outcomes = [o for _, o in result["outcomes"]]
    assert len(outcomes) == 15
    assert outcomes.count("could not be run") <= 5, result
    assert result["sentence"].startswith("Of 15 requests, ")
    print(result["sentence"])
    print([(n, o) for (n, o), (_, name) in zip(result["outcomes"], result["index"])], [name for _, name in result["index"]])


def test_a_file_that_is_not_an_inventory_is_refused():
    assert browser.sandbox_start(CATALOGUE, b"not a zip") == "inventory"
    assert browser.sandbox_start(b"a,b\n", b"not a zip") == "catalogue"


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
    from schemalyser.catalogue import Catalogue
    from schemalyser.sandbox import Sandbox
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
    from schemalyser.catalogue import Catalogue
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
