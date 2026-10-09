"""The findings of a request (extract.py), read through the analysis of a world's requests in harness.py, with the
catalogue, the separation of statements and the translation that the reading rests on.

Carried from the earlier design's tests of the analyser (B8): the analysis now serves only the synthetic world, whose
sandbox is built from its pack, and release.py's list of the source columns that the steps read.
"""
import csv
import io
import json
import re
from pathlib import Path

import pytest

from schemalyser import vocabulary as v
from schemalyser.catalogue import Catalogue, CatalogueError
from schemalyser.extract import normalise
from schemalyser.harness import Analysis
from schemalyser.sandbox import LAYOUT
from schemalyser.statements import separate
from schemalyser.translate import Unsupported, to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REQUESTS = FIXTURES / "requests"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]
CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_text()
HEADER = "TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,DATA_TYPE,CHARACTER_MAXIMUM_LENGTH,NUMERIC_PRECISION,NUMERIC_SCALE,IS_NULLABLE\n"

HEADER_WORDS = {
    "table", "column", "data_type", "requests", "left_table", "left_column", "right_table",
    "right_column", "join_kind", "operator", "value_kind", "value", "expression", "columns",
    "request", "statements", "unresolved", "elements", "files", "not", "fully", "read",
}


def build():
    analysis = Analysis((FIXTURES / "invented-catalogue.csv").read_text())
    for path in sorted(REQUESTS.rglob("*.sql")):
        analysis.add_request(path.relative_to(REQUESTS).as_posix(), path.read_text())
    return analysis

@pytest.fixture(scope="module")
def analysis():
    return build()

@pytest.fixture(scope="module")
def pack(analysis):
    return analysis.pack()

def rows(pack, name):
    return list(csv.DictReader(io.StringIO(pack[name])))


# A world's requests.

def test_each_request_finds_the_expected_tables_and_columns(analysis, pack):
    numbers = {name: number for number, name in analysis.request_index()}
    by_number = {int(r["request"]): set(r["elements"].split()) for r in rows(pack, "requests.csv")}
    for name, want in EXPECTED.items():
        elements = by_number[numbers[name]]
        result = analysis._requests[name]
        tables = {f[1] for f in result.findings if f[0] == "table"}
        assert tables == set(want["tables"]), name
        assert result.parsed != bool(want.get("expectFailure")), name
        if "columns" in want:
            assert set(want["columns"]) <= elements, name


def test_no_planted_value_reaches_any_output(analysis, pack):
    text = "\n".join(pack.values()).lower()
    for value in PLANTED:
        assert value.lower() not in text, value


def test_every_word_written_is_on_the_allowlist(analysis, pack):
    allowed = analysis.catalogue.names() | {w.upper() for w in v.WORDS}
    allowed |= {w.upper() for w in HEADER_WORDS | set(v.ROLES) | set(v.JOIN_KINDS) | set(v.VALUE_KINDS) | set(v.UNRESOLVED)}
    allowed |= {t.upper() for t in analysis.catalogue.data_types()}
    allowed |= {w.strip("<>").upper() for w in v.PLACEHOLDERS.values()}
    wording = " ".join([*v.UNRESOLVED_LABELS.values(), v.NOTHING_UNREAD, v.files_sentence(2, 1), v.files_sentence(2, 2, {k: 1 for k in v.HIDES_SQL}),
                        v.files_sentence(2, 2, {k: 2 for k in v.HIDES_SQL}), v.found_sentence(2, 2, 2, 2, 2)])
    allowed |= {w.upper() for w in re.findall(r"[A-Za-z_]+", wording)}
    for name, text in pack.items():
        for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text):
            assert word.upper() in allowed, f"{word!r} in {name}"


def test_numbers_appear_only_as_counts(analysis, pack):
    n_requests = len(analysis.request_index())
    for row in rows(pack, "derivations.csv"):
        assert not re.search(r"(?<!\w)\d", row["expression"]), row["expression"]
    for row in rows(pack, "filters.csv"):
        assert row["value"] == ""
    for name in ("elements.csv", "joins.csv", "filters.csv", "derivations.csv"):
        for row in rows(pack, name):
            assert 1 <= int(row["requests"]) <= n_requests


def test_the_request_index_is_not_in_the_pack(analysis, pack):
    text = "\n".join(pack.values())
    for _, name in analysis.request_index():
        assert name not in text


def test_two_runs_give_identical_packs(pack):
    assert build().pack() == pack


def test_joins_filters_and_derivations_are_found(pack):
    joins = {(r["left_table"], r["left_column"], r["right_table"], r["right_column"], r["join_kind"])
             for r in rows(pack, "joins.csv")}
    assert ("ANAES_RECORD", "CASE_KEY", "THEATRE_CASE", "CASE_KEY", "inner") in joins
    assert ("THEATRE_CASE", "SERVICE_CAT", "LK_SERVICE", "SERVICE_CAT", "left") in joins
    filters = {(r["table"], r["column"], r["operator"], r["value_kind"]) for r in rows(pack, "filters.csv")}
    assert ("THEATRE_CASE", "CASE_STATUS_CAT", "NOT IN", "number") in filters
    assert ("OBS_READING", "OBS_TYPE_KEY", "IN", "string") in filters
    assert ("PERSON_MASTER", "RECORD_NO", "IN", "string") in filters
    derivations = {r["expression"] for r in rows(pack, "derivations.csv")}
    assert "DATEDIFF(MINUTE, ANAES_RECORD.ANAES_START_TS, ANAES_RECORD.ANAES_STOP_TS)" in derivations


def test_held_back_tables_are_not_written():
    rules = json.dumps({"localTablePatterns": ["LK_%"]})
    analysis = Analysis((FIXTURES / "invented-catalogue.csv").read_text(), rules)
    for path in sorted(REQUESTS.rglob("*.sql")):
        analysis.add_request(path.relative_to(REQUESTS).as_posix(), path.read_text())
    pack = analysis.pack()
    assert "LK_" not in "\n".join(pack[n] for n in pack if n != "coverage.txt")
    assert v.UNRESOLVED_LABELS["local_table_held_back"] in pack["coverage.txt"]


def test_fixtures_use_only_the_invented_catalogue(pack):
    assert v.UNRESOLVED_LABELS["table_not_in_catalogue"] not in pack["coverage.txt"]


def test_columns_are_traced_through_temp_tables_and_ctes(pack):
    joins = {(r["left_table"], r["left_column"], r["right_table"], r["right_column"]) for r in rows(pack, "joins.csv")}
    # multi_batch_temp.sql joins ANAES_EVENT to a temp table that was filled from ANAES_RECORD.
    assert ("ANAES_EVENT", "ANAES_KEY", "ANAES_RECORD", "ANAES_KEY") in joins
    filters = {(r["table"], r["column"], r["operator"]) for r in rows(pack, "filters.csv")}
    # risk_grade_by_age.sql filters a CTE column that comes from ANAES_RECORD.
    assert ("ANAES_RECORD", "RISK_GRADE_CAT", "IN") in filters


def test_a_byte_order_mark_does_not_stop_a_file_being_read():
    analysis = Analysis((FIXTURES / "invented-catalogue.csv").read_text())
    analysis.add_request("bom.sql", "\ufeffSELECT CASE_KEY FROM THEATRE_CASE")
    assert "THEATRE_CASE,CASE_KEY" in analysis.pack()["elements.csv"]


# A single request.

def analyse(sql, checks_csv=None, catalogue=CATALOGUE):
    analysis = Analysis(catalogue, checks_csv=checks_csv)
    analysis.add_request("one.sql", sql)
    return analysis

def filters(sql):
    return {(r["table"], r["column"], r["operator"]) for r in rows(analyse(sql).pack(), "filters.csv")}


@pytest.mark.parametrize("opening", ["SET NOCOUNT ON", "DECLARE @d date = '2020-01-01'", "SET ANSI_NULLS ON\nSET NOCOUNT ON"])
def test_a_request_that_opens_without_semicolons_is_still_read(opening):
    analysis = analyse(opening + "\nSELECT tc.CASE_KEY FROM THEATRE_CASE tc WHERE tc.CASE_STATUS_CAT = 2")
    assert ("THEATRE_CASE", "CASE_STATUS_CAT", "=") in {(r["table"], r["column"], r["operator"])
                                                         for r in rows(analysis.pack(), "filters.csv")}


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
    assert {(r["table"], r["column"]) for r in rows(analysis.pack(), "filters.csv")} == {
        ("THEATRE_CASE", "CASE_STATUS_CAT"), ("VISIT", "VISIT_KIND_CAT")}
    assert rows(analysis.pack(), "joins.csv")[0]["left_table"] == "ANAES_RECORD"
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
    assert {(r["column"], r["value"]) for r in rows(analysis.pack(), "filters.csv")} == {("EMERGENCY_FLAG", "Y"), ("CASE_STATUS_CAT", "2")}


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
