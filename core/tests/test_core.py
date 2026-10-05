import csv
import io
import json
import re
from pathlib import Path

import pytest

from schemalyser import Analysis
from schemalyser import vocabulary as v

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REQUESTS = FIXTURES / "requests"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]

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
    wording = " ".join([*v.UNRESOLVED_LABELS.values(), v.NOTHING_UNREAD, v.files_sentence(2, 1), v.found_sentence(2, 2, 2, 2, 2)])
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


def test_the_browser_functions_give_the_same_pack_and_refuse_a_bad_catalogue(pack):
    import zipfile
    from schemalyser import browser
    assert browser.start(b"a,b\n1,2\n") == "catalogue"
    assert browser.start(b"\xef\xbb\xbf" + (FIXTURES / "invented-catalogue.csv").read_bytes()) == "ok"
    for path in sorted(REQUESTS.rglob("*.sql")):
        browser.add(path.relative_to(REQUESTS).as_posix(), path.read_bytes())
    result = json.loads(browser.finish())
    assert result["pack"] == pack
    assert result["summary"]["files"] == 15 and result["summary"]["notFullyRead"] == 2
    assert result["summary"]["sentences"][0] == "Schemalyser has read 15 files. It was not able to read 2 of them in full, because each holds a part that Schemalyser could not parse, SQL that is built as text when it runs, a call to a stored procedure, a statement of a kind that Schemalyser does not analyse, or a query whose columns Schemalyser could not match to their tables."
    assert pack["coverage.txt"].splitlines()[:2] == result["summary"]["sentences"]
    archive = zipfile.ZipFile(io.BytesIO(browser.pack_zip()))
    assert {n: archive.read(n).decode() for n in archive.namelist()} == pack
    assert browser.pack_zip() == browser.pack_zip()


def test_a_catalogue_without_headers_is_accepted_in_the_order_of_the_query(pack):
    from schemalyser import browser
    headed = (FIXTURES / "invented-catalogue.csv").read_text()
    assert browser.start(headed.split("\n", 1)[1].encode()) == "ok-no-headers"
    for path in sorted(REQUESTS.rglob("*.sql")):
        browser.add(path.relative_to(REQUESTS).as_posix(), path.read_bytes())
    assert json.loads(browser.finish())["pack"] == pack
