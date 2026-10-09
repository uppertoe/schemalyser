"""The three named summaries of section 4 of docs/contract.md, each written alone by its exporter in summaries.py.

Every input is planted with names that stand for confidential material, in the way that test_workspace plants a
reference: table and column names, a code, descriptions, file names and a path. Each test runs the real command or
function over the planted input and asserts that no planted name reaches the summary's files.
"""
import json
import re
import shutil
from pathlib import Path

import pytest

from schemalyser import compare, rolemap, summaries

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PLAIN = FIXTURES / "compare" / "reference-plain"
DBT = FIXTURES / "compare" / "reference-dbt"
CONVERSION = FIXTURES / "conversion"
DICTIONARY = FIXTURES / "dictionary" / "invented-dictionary.csv"
DICTIONARY_TABLES = FIXTURES / "dictionary" / "invented-tables.csv"
PLANTED = re.compile(rb"planted", re.IGNORECASE)


def _renamed(text, names):
    """text with every name of the invented reference given the planted prefix."""
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", f"PLANTED_{name}", text)
    return text


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    """The invented reference with every source table and column planted, a planted code and description in a
    comment, a planted file that does not parse, a planted model that is not read, and the whole kept under a planted
    path, with a dictionary that is planted in the same way."""
    root = tmp_path_factory.mktemp("PLANTED_PATH_HOSPITAL")
    sources = compare.lineage(DBT)["sources"]
    names = set(sources) | {c for columns in sources.values() for c in columns}
    for kind, source in (("plain", PLAIN), ("dbt", DBT)):
        folder = root / f"PLANTED_FOLDER_{kind}"
        shutil.copytree(source, folder)
        for path in folder.rglob("*.sql"):
            path.write_text("-- PLANTED_DESCRIPTION of the PLANTED_CODE_X1 rows\n" + _renamed(path.read_text(), names))
        for path in folder.rglob("*.yml"):
            path.write_text(_renamed(path.read_text(), names))
        models = folder / "models" / "omop" if kind == "dbt" else folder
        (models / "planted_broken_file.sql").write_text("SELECT PLANTED_BROKEN_COLUMN FROM WHERE ((( GROUP\n")
        if kind == "dbt":
            (models / "planted_loop_file.sql").write_text("{% for x in [1] %} select {{ x }} as PLANTED_LOOP {% endfor %}\n")
    for name, source in (("dictionary.csv", DICTIONARY), ("tables.csv", DICTIONARY_TABLES)):
        lines = _renamed(source.read_text(), names).splitlines()
        (root / name).write_text("\n".join(lines[:1] + [line + " PLANTED_DESCRIPTION" if not line.endswith('"')
                                                         else line[:-1] + ' PLANTED_DESCRIPTION"' for line in lines[1:]])
                                 + "\n")
    return root


def _clean(*paths):
    for path in paths:
        assert path.is_file(), path
        assert not PLANTED.search(path.read_bytes()), path


def test_the_reference_run_summary_names_no_planted_name(planted, capsys):
    for kind in ("plain", "dbt"):
        out = planted / f"theirs_{kind}.json"
        assert compare.main(["reference", str(planted / f"PLANTED_FOLDER_{kind}"), "--out", str(out)]) == 0
        printed = capsys.readouterr().out
        # The lineage itself names the planted tables, so the test is not vacuous.
        assert PLANTED.search(out.read_bytes())
        json_file, md_file = planted / f"theirs_{kind}-run-summary.json", planted / f"theirs_{kind}-run-summary.md"
        _clean(json_file, md_file)
        assert not PLANTED.search(printed.split("The run's counts")[0].encode())
        data = json.loads(json_file.read_text())
        assert data["format"] == "schemalyser-reference-run-summary" and data["version"] == 1 and data["kind"] == kind
        counts = data["counts"]
        assert counts["files_not_parsed"] == 1 and counts["omop_tables"] == 6
        assert counts["files_not_read"] == (1 if kind == "dbt" else 0)
        assert counts["files_read"] == counts["files_parsed"] + counts["files_not_read"] + counts["files_not_parsed"]


def test_the_comparison_summary_names_no_planted_name_and_equals_the_reports_counts(planted, capsys):
    assert compare.main(["reference", str(planted / "PLANTED_FOLDER_plain"), "--out", str(planted / "theirs.json")]) == 0
    assert compare.main(["reference", str(CONVERSION), "--out", str(planted / "ours.json")]) == 0
    out = planted / "PLANTED_REPORT"
    assert compare.main(["report", "--ours", str(planted / "ours.json"), "--theirs", str(planted / "theirs.json"),
                         "--out", str(out)]) == 0
    capsys.readouterr()
    assert PLANTED.search((out / "report.md").read_bytes()) and PLANTED.search((out / "report.json").read_bytes())
    _clean(out / "comparison-summary.json", out / "comparison-summary.md")
    summary = json.loads((out / "comparison-summary.json").read_text())
    report = json.loads((out / "report.json").read_text())
    assert summary["format"] == "schemalyser-comparison-summary" and summary["version"] == 1
    assert summary["counts"] == report["summary"]["counts"]
    assert set(summary) == {"format", "version", "note", "counts"}
    # The detail stays in the report and only there.
    assert "detail" in report and "Detail" not in (out / "comparison-summary.md").read_text()


def test_the_transplant_run_summary_names_no_planted_name(planted, capsys):
    lineage = planted / "theirs_dbt.json"
    assert compare.main(["reference", str(planted / "PLANTED_FOLDER_dbt"), "--out", str(lineage)]) == 0
    out = planted / "PLANTED_CONVERSION"
    assert compare.main(["transplant", "--lineage", str(lineage), "--dictionary", str(planted / "dictionary.csv"),
                         "--tables", str(planted / "tables.csv"), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert PLANTED.search((out / "person.sql").read_bytes()) and PLANTED.search((out / "catalogue.csv").read_bytes())
    _clean(out / "transplant-run-summary.json", out / "transplant-run-summary.md")
    data = json.loads((out / "transplant-run-summary.json").read_text())
    report = json.loads((out / "transplant-report.json").read_text())
    assert data["format"] == "schemalyser-transplant-run-summary" and data["counts"] == report["counts"]
    assert data["counts"]["written"] == 6 and data["date"] == report["date"]
    assert not PLANTED.search(printed.encode())


def _proposal(answer=None, confidence="high", replacement="", candidates=(), **more):
    item = {"status": "proposed", "from": "PLANTED_TABLE.PLANTED_COLUMN", "says": "PLANTED_DESCRIPTION",
            "question": "Is PLANTED_TABLE.PLANTED_COLUMN right?", "confidence": confidence,
            "binding": {"table": "PLANTED_TABLE", "column": "PLANTED_COLUMN", "filter": "= 'PLANTED_CODE'"},
            "candidates": [{"from": c, "says": "PLANTED_DESCRIPTION"} for c in candidates], **more}
    if answer:
        item["confirmation"] = {"answer": answer, "replacement": replacement, "note": "PLANTED_NOTE"}
    return item


def test_the_scoreboard_summary_names_no_planted_name(tmp_path):
    data = {"roles": {
        "role_patient": {"rows": _proposal("yes"),
                         "columns": {"patient_key": _proposal("no", replacement="PLANTED_OTHER.PLANTED_KEY",
                                                              candidates=["PLANTED_OTHER.PLANTED_KEY"]),
                                     "birth_date": _proposal("no", replacement="PLANTED_ELSEWHERE.PLANTED_BORN"),
                                     "is_test": _proposal("not sure", confidence="low",
                                                          proposed_from="a reference conversion")}},
        "role_reading": {"rows": _proposal(confidence="medium"),
                         "columns": {"value": _proposal("yes"), "kind": _proposal(confidence="none")}}},
        "kinds": {"PLANTED_CODE": "PLANTED_DESCRIPTION"}}
    board = rolemap.scoreboard(data)
    json_file, md_file = summaries.write_scoreboard(board, tmp_path / "PLANTED_FOLDER")
    _clean(json_file, md_file)
    written = json.loads(json_file.read_text())
    assert written["format"] == "schemalyser-scoreboard-summary" and written["overall"] == board["overall"]
    assert set(written["parts"]) == {"role_patient", "role_reading"} and written["nothing"] == {"count": 1, "chosen": 0}
    text = md_file.read_text()
    assert "Across the hospital schema, the page made 6 proposals." in text and "`role_patient`" in text
    assert "?" not in text and "!" not in text


@pytest.mark.parametrize("name, counters", [
    ("comparison", {"counts": {**dict.fromkeys(summaries.COMPARISON["counts"], 1), "table": "PLANTED_TABLE"}}),
    ("comparison", {"counts": {**dict.fromkeys(summaries.COMPARISON["counts"], 1), "agreeing": "PLANTED_TABLE"}}),
    ("transplant", {"date": "PLANTED_DATE", "counts": dict.fromkeys(summaries.TRANSPLANT_RUN["counts"], 0)}),
    ("transplant", {"date": "2026-10-09", "counts": dict.fromkeys(summaries.TRANSPLANT_RUN["counts"], 0),
                    "file": "planted.sql"}),
    ("reference", {"kind": "PLANTED_KIND", "counts": dict.fromkeys(summaries.REFERENCE_RUN["counts"], 0), "errors": {}}),
    ("reference", {"kind": "plain", "counts": dict.fromkeys(summaries.REFERENCE_RUN["counts"], 0),
                   "errors": {"PLANTED_ERROR": {"files": 1}}}),
    ("scoreboard", {"overall": dict.fromkeys(summaries.FARED, 0), "parts": {"PLANTED_PART": dict.fromkeys(summaries.FARED, 0)},
                    "categories": {}, "reference": dict.fromkeys(summaries.FARED, 0), "levels": {},
                    "nothing": {"count": 0, "chosen": 0}}),
])
def test_an_unexpected_string_or_field_is_refused_and_nothing_is_written(tmp_path, name, counters):
    with pytest.raises(summaries.SummaryRefused) as refused:
        summaries.write(name, counters, tmp_path)
    assert not PLANTED.search(str(refused.value).encode())
    assert list(tmp_path.iterdir()) == []


def test_a_report_whose_summary_holds_a_string_is_refused(tmp_path):
    report = compare.compare(compare.lineage(CONVERSION), compare.lineage(PLAIN))
    report["summary"]["counts"]["first_table"] = "PLANTED_TABLE"
    with pytest.raises(summaries.SummaryRefused, match="allowlist"):
        summaries.write_comparison(report, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_each_value_kind_is_checked():
    counts = dict.fromkeys(summaries.TRANSPLANT_RUN["counts"], 0)
    for bad in (-1, 1.5, True, float("nan"), None, "3"):
        with pytest.raises(summaries.SummaryRefused):
            summaries.checked("transplant", {"date": "2026-10-09", "counts": {**counts, "written": bad}})
    for bad in ("2026-13-40", "9 October 2026", 20261009):
        with pytest.raises(summaries.SummaryRefused):
            summaries.checked("transplant", {"date": bad, "counts": counts})
    with pytest.raises(summaries.SummaryRefused, match="lacks written"):
        summaries.checked("transplant", {"date": "2026-10-09", "counts": {k: 0 for k in counts if k != "written"}})
    assert summaries.checked("transplant", {"date": "2026-10-09", "counts": counts})["counts"] == counts
