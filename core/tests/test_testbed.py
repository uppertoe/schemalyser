"""Tests for the OMOP testbed, run once on the invented world at a small size."""
import json
from pathlib import Path

import duckdb
import pytest

from schemalyser import convert, testbed

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    out = tmp_path_factory.mktemp("testbed")
    testbed.run("fixtures", out, rows=60)
    return out, json.loads((out / "report.json").read_text())


def test_the_report_has_every_section_and_names_its_versions(ran):
    out, report = ran
    assert list(report) == list(testbed.SECTIONS)
    versions = report["versions"]
    assert versions["cdm"] == "5.4" and versions["duckdb"] == duckdb.__version__
    assert versions["tool"].startswith("schemalyser ") and "sample subset" in versions["vocabulary"]
    assert report["world"]["folder"] == "fixtures" and len(report["world"]["conversion_sha256"]) == 64
    assert (out / "report.md").read_text().startswith("# OMOP testbed report")
    # The report names no folder of this machine.
    assert str(FIXTURES.parent) not in (out / "report.json").read_text()


def test_the_run_passes_on_the_invented_world(ran):
    _, report = ran
    assert report["summary"]["outcome"] == "passed", [c for c in report["checks"] if c["passed"] is False]
    assert all(step["status"] == "ok" for step in report["steps"])
    assert all(gate["outcome"] == "passed" for gate in report["release"]["gates"])
    assert all(count["error"] is None for count in report["release"]["counts"])
    assert report["release"]["written"] and report["release"]["carries_every_step"]


def test_every_scenario_passes_against_the_rows_its_scenario_file_states(ran):
    _, report = ran
    written = {s["name"]: s for s in convert.read_scenarios(FIXTURES / "conversion")}
    assert {s["name"] for s in report["scenarios"]} == set(written)
    for scenario in report["scenarios"]:
        assert scenario["outcome"] == "passed", scenario
        # Each expected row is the one that scenario.json states, never one taken from the run.
        stated = [convert.shown_rows([item["result"]]) for item in written[scenario["name"]]["expectations"]]
        assert [item["expected"] for item in scenario["expectations"]] == stated
    listed_twice = next(s for s in report["scenarios"] if s["name"] == "anaesthetic_listed_twice")
    assert listed_twice["gate_failed_as_intended"]


def test_the_reconciliation_sums_agree(ran):
    _, report = ran
    reconciliation = report["reconciliation"]
    traced = [e for e in reconciliation["steps"] if e["traced"]]
    assert len(traced) >= 18
    for e in traced:
        assert e["source_rows"] == e["reached"] + sum(d["rows"] for d in e["dropped"]), e["step"]
        assert e["target_rows"] == e["written"] and e["start_agrees"], e["step"]
        assert e["target_rows"] == e["reached"] + e["extra_target_rows"], e["step"]
    for table in reconciliation["target_tables"]:
        assert table["rows_from_steps"] == table["rows_held"], table
    totals = reconciliation["totals"]
    assert totals["source_rows"] == totals["reached"] + totals["dropped"]
    assert totals["all_sums_agree"] and totals["steps_with_unexpected_several_rows"] == []
    # The blood pressure step writes two rows for most charted pressures, as testbed.json allows.
    pressure = next(e for e in traced if e["step"] == "measurement_blood_pressure_through_anaesthetic.sql")
    assert pressure["source_rows_with_several_target_rows"] > 0 and pressure["as_expected"]
    # The test patient is left out of PERSON by the step's own condition, and the report says so.
    person = next(e for e in traced if e["step"] == "person.sql")
    assert [d["rows"] for d in person["dropped"]] == [1] and "TEST_PERSON_FLAG" in person["dropped"][0]["by"]


def test_the_reconciliation_says_which_steps_it_traced_and_which_it_could_not(ran):
    _, report = ran
    coverage = report["reconciliation"]["coverage"]
    steps = report["reconciliation"]["steps"]
    assert coverage["outcome"] == "passed" and coverage["unexplained"]["count"] == 0
    assert coverage["accounted"]["count"] + coverage["fan_out_confirmed"]["count"] + coverage["not_traced"]["count"] == len(steps)
    assert {item["step"] for item in coverage["not_traced"]["steps"]} == {e["step"] for e in steps if not e["traced"]}
    assert all(item["reason"] for item in coverage["not_traced"]["steps"])
    assert "measurement_blood_pressure_through_anaesthetic.sql" in {item["step"] for item in coverage["fan_out_confirmed"]["steps"]}
    sentence = next(s for s in report["summary"]["sentences"] if s.startswith("The reconciliation traced"))
    k = coverage["not_traced"]["count"]
    assert sentence.startswith(f"The reconciliation traced {coverage['traced']} of {len(steps)} steps and accounted for every excluded row in them; "
                               f"{k} steps could not be traced (")
    assert sentence.endswith("so their rows are not reconciled.")


def test_an_unexplained_discrepancy_fails_the_reconciliation():
    entry = {"step": "a.sql", "traced": True, "start_agrees": True, "sums_agree": True, "matches_the_run": True,
             "source_rows_with_several_target_rows": 2, "as_expected": False, "expected": "at most one target row for each source row"}
    found = testbed.coverage([entry, {"step": "b.sql", "traced": False, "reason": "the step reads no table"}], [])
    assert found["outcome"] == "failed" and found["unexplained"]["items"][0]["step"] == "a.sql"
    sentence = testbed.coverage_sentences(found, {"source_rows": 3, "reached": 3, "dropped": 0, "target_rows": 5})[0]
    assert "in 0 of them; 1 discrepancy is unexplained (a.sql); 1 step could not be traced (b.sql), so its rows are not reconciled." in sentence
    assert testbed.judge([{"check": "the reconciliation found no unexplained discrepancy", "passed": False}], "fast") != "passed"


def test_the_fast_profile_passes_without_the_dashboard_or_sql_server(ran):
    _, report = ran
    assert report["summary"]["profile"] == "fast"
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["the Data Quality Dashboard ran"]["passed"] is None
    assert checks["release equivalence"]["state"] == "not run on SQL Server" and checks["release equivalence"]["passed"] is None


SUMMARY = ["OMOP objects compared: 41; identical: 41; different: 0.",
           "Steps that failed or wrote a different number of rows: 0.",
           "Gates passed on SQL Server: 6 of 6; gates whose outcome differs from DuckDB's or did not pass: 0.",
           "Reasons that the DuckDB run itself was not clean: 0.",
           "Expectations of the planted scenarios not met on both engines: 0 of 23."]


def test_release_equivalence_reads_the_harness_summary():
    assert testbed.release_equivalence(None)["state"] == "not run on SQL Server"
    assert testbed.release_equivalence({"exit_code": 0, "summary": SUMMARY})["state"] == "passed"
    differs = [line.replace("identical: 41; different: 0", "identical: 39; different: 2") for line in SUMMARY]
    assert testbed.release_equivalence({"exit_code": 1, "summary": differs})["state"] == "failed"
    stopped = [line.replace("rows: 0.", "rows: 1.") if line.startswith("Steps") else
               line.replace("0 of 23", "0 of 0") for line in SUMMARY]
    assert testbed.release_equivalence({"exit_code": 1, "summary": stopped})["state"] == "failed"
    assert testbed.release_equivalence({"status": "not run", "reason": "no container", "summary": []})["state"] == "failed"


def test_the_full_profile_requires_the_dashboard_and_release_equivalence():
    clean = [{"check": "every step ran cleanly", "passed": True}]
    ran = {"check": "the Data Quality Dashboard ran", "passed": True}
    missing = {"check": "the Data Quality Dashboard ran", "passed": False}
    equal = testbed.release_equivalence({"exit_code": 0, "summary": SUMMARY})
    unrun = testbed.release_equivalence(None)
    assert testbed.judge(clean + [dict(missing, passed=None), unrun], "fast") == "passed"
    assert testbed.judge(clean + [missing, equal], "full") == "failed: the Data Quality Dashboard did not run"
    assert testbed.judge(clean + [ran, unrun], "full") == "failed: release equivalence was not run on SQL Server"
    assert testbed.judge(clean + [ran, equal], "full") == "passed"


def test_the_dashboard_results_are_counted_by_category(tmp_path):
    path = tmp_path / "dqd_results.json"
    path.write_text(json.dumps({"CheckResults": [
        {"category": "Conformance", "failed": 0, "passed": 1, "isError": 0, "notApplicable": 0},
        {"category": "Conformance", "failed": 1, "passed": 0, "isError": 0, "notApplicable": 0},
        {"category": "Plausibility", "failed": 1, "passed": 0, "isError": 1, "notApplicable": 0},
        {"category": "Completeness", "failed": 0, "passed": 0, "isError": 0, "notApplicable": 1}]}))
    found = testbed.dqd_results(path)
    assert (found["checks"], found["passed"], found["failed"], found["could_not_run"], found["not_applicable"]) == (4, 1, 1, 1, 1)
    assert found["by_category"]["Conformance"] == {"checks": 2, "passed": 1, "failed": 1, "could_not_run": 0, "not_applicable": 0}


def test_the_inputs_for_the_dashboard_are_written_and_it_is_marked_not_run(ran):
    out, report = ran
    assert report["dqd"]["status"] == "not run"
    con = duckdb.connect(str(out / report["dqd"]["duckdb_file"]), read_only=True)
    person = con.execute("SELECT COUNT(*) FROM cdm.person").fetchone()[0]
    assert person == next(s for s in report["steps"] if s["file"] == "person.sql")["rows"]
    assert con.execute("SELECT COUNT(*) FROM cdm.concept").fetchone()[0] > 0
    con.close()
    assert (out / "csv" / "person.csv").exists() and (out / "dqd" / "run_dqd.R").exists()
    assert "DROP SCHEMA IF EXISTS testbed CASCADE;" in (out / "csv" / "load_postgresql.sql").read_text()


def test_a_trace_puts_each_row_left_out_down_to_its_join_or_condition():
    con = duckdb.connect()
    con.execute('CREATE TABLE "SRC" (k INTEGER, flag VARCHAR)')
    con.execute("INSERT INTO \"SRC\" VALUES (1, 'Y'), (2, 'N'), (3, 'Y')")
    con.execute("CREATE TABLE \"OTHER\" (k INTEGER)")
    con.execute("INSERT INTO \"OTHER\" VALUES (1), (1), (2)")
    found = testbed.trace(con, frozenset(), "SELECT s.k AS person_id FROM SRC s JOIN OTHER o ON o.k = s.k WHERE s.flag = 'Y'", 2)
    assert found["traced"] and found["source_rows"] == 3 and found["reached"] == 1 and found["target_rows"] == 2
    assert [d["rows"] for d in found["dropped"]] == [1, 1]
    assert found["source_rows_with_several_target_rows"] == 1 and found["matches_the_run"]
