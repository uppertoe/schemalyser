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
    assert found["by_category"]["Conformance"] == {"checks": 2, "passed": 1, "failed": 1, "could_not_run": 0, "did_not_finish": 0,
                                                   "not_applicable": 0}


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


def test_the_dashboard_failures_are_set_against_the_expectations(tmp_path):
    expectations = testbed.read_dqd_expectations(FIXTURES / "dqd-expectations.json")
    assert expectations and all(e["reason"].endswith(".") for e in expectations)
    path = tmp_path / "dqd_results.json"
    path.write_text(json.dumps({"CheckResults": [
        {"checkName": "measurePersonCompleteness", "cdmTableName": "DRUG_ERA", "category": "Completeness", "failed": 1,
         "isError": 0, "notApplicable": 0, "numViolatedRows": 5, "numDenominatorRows": 5},
        {"checkName": "isRequired", "cdmTableName": "COHORT", "cdmFieldName": "COHORT_DEFINITION_ID", "category": "Conformance",
         "failed": 0, "isError": 1, "notApplicable": 0},
        {"checkName": "plausibleValueLow", "cdmTableName": "MEASUREMENT", "cdmFieldName": "VALUE_AS_NUMBER",
         "category": "Plausibility", "failed": 1, "isError": 0, "notApplicable": 0, "numViolatedRows": 2, "numDenominatorRows": 9}]}))
    found = testbed.dqd_results(path, expectations)
    assert (found["expected_failures"], found["unexpected_failures"]) == (2, 1)
    unexpected = [f for f in found["failures"] if not f["expected"]]
    assert [testbed._failure_name(f) for f in unexpected] == ["plausibleValueLow on MEASUREMENT.VALUE_AS_NUMBER"]
    assert "DRUG_ERA" in next(f["reason"] for f in found["failures"] if f["table"] == "DRUG_ERA")
    # Without expectations, every failure is unexpected.
    assert testbed.dqd_results(path)["unexpected_failures"] == 3
    clean = [{"check": "every step ran cleanly", "passed": True}, {"check": "the Data Quality Dashboard ran", "passed": True},
             testbed.release_equivalence({"exit_code": 0, "summary": SUMMARY})]
    permitted = {"check": "the Data Quality Dashboard reported no failure that dqd-expectations.json does not permit"}
    assert testbed.judge(clean + [dict(permitted, passed=True, detail=[])], "full") == "passed"
    outcome = testbed.judge(clean + [dict(permitted, passed=False, detail=["plausibleValueLow on MEASUREMENT.VALUE_AS_NUMBER"])], "full")
    assert outcome == ("failed: the Data Quality Dashboard reported 1 failure that dqd-expectations.json does not permit "
                       "(plausibleValueLow on MEASUREMENT.VALUE_AS_NUMBER)")


def _harness_summary():
    return {"versions": {"sqlserver": "Developer Edition, version 16", "duckdb": duckdb.__version__, "summary_format": 1},
            "release": {"status": "committed", "message": None},
            "steps": [{"file": "person.sql", "sqlserver": "ok", "sqlserver_rows": 9, "duckdb": "ok", "duckdb_rows": 9, "agree": True}],
            "tables": [{"object": "dbo.person", "duckdb_rows": 9, "sqlserver_rows": 9, "duckdb_checksum": "a" * 64,
                        "sqlserver_checksum": "a" * 64, "agree": True, "error": None},
                       {"object": "anaes_pub.measurement", "duckdb_rows": 30, "sqlserver_rows": 30, "duckdb_checksum": "b" * 64,
                        "sqlserver_checksum": "b" * 64, "agree": True, "error": None}],
            "scenarios": [{"scenario": "late_reading", "duckdb": "met", "sqlserver": "met", "agree": True,
                           "expectations": [{"says": "One reading is kept.", "duckdb": "met", "sqlserver": "met"}]}],
            "gates": [], "duckdb_failures": [], "exit_code": 0}


def test_release_equivalence_reads_the_harness_summary_json(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps(_harness_summary()))
    read = testbed.read_harness_summary(tmp_path)
    passed = testbed.release_equivalence({"exit_code": 0, "summary": [], "summary_json": read})
    assert passed["state"] == "passed" and passed["source"] == "summary.json" and "all 2 OMOP objects" in passed["detail"]
    differs = _harness_summary()
    differs["tables"][1].update(sqlserver_checksum="c" * 64, agree=False)
    failed = testbed.release_equivalence({"exit_code": 1, "summary": SUMMARY, "summary_json": differs})
    # The structured summary decides, even where the printed one says that every object matched.
    assert failed["state"] == "failed" and "anaes_pub.measurement" in failed["detail"]
    unmatched = _harness_summary()
    unmatched["scenarios"][0].update(sqlserver="not met", agree=False)
    assert "late_reading" in testbed.release_equivalence({"exit_code": 1, "summary_json": unmatched})["detail"]
    assert testbed.release_equivalence({"exit_code": 0, "summary_json": dict(_harness_summary(), scenarios=[])})["state"] == "failed"
    # Without summary.json, the check reads the printed summary and says so.
    assert testbed.read_harness_summary(tmp_path / "missing") is None
    fallback = testbed.release_equivalence({"exit_code": 0, "summary": SUMMARY, "summary_json": None})
    assert fallback["state"] == "passed" and fallback["source"] == "the printed summary"
    assert fallback["detail"].startswith("The harness wrote no summary.json, so the check read its printed summary instead.")


# The vocabulary option.

def _download(folder, version="v5.0 29-AUG-26"):
    """A small download in the layout of Athena's: the sample's concepts, a few more rows, and the files the testbed reads."""
    from schemalyser import testbed as t
    t._vocabulary(folder.parent / "sample")
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("CONCEPT", "CONCEPT_SYNONYM"):
        (folder / f"{name}.csv").write_text((folder.parent / "sample" / "vocabulary" / f"{name}.csv").read_text())
    relationship = (folder.parent / "sample" / "vocabulary" / "CONCEPT_RELATIONSHIP.csv").read_text()
    (folder / "CONCEPT_RELATIONSHIP.csv").write_text(relationship + "45542411\t45542411\tMapped from\t19700101\t20991231\t\n")
    (folder / "VOCABULARY.csv").write_text("vocabulary_id\tvocabulary_name\tvocabulary_reference\tvocabulary_version\tvocabulary_concept_id\n"
                                           f"None\tOMOP Standardized Vocabularies\tOMOP generated\t{version}\t44819096\n"
                                           "RxNorm\tRxNorm (NLM)\thttp://www.nlm.nih.gov/research/umls/rxnorm\tRxNorm 20260601\t44819104\n")
    (folder / "DOMAIN.csv").write_text("domain_id\tdomain_name\tdomain_concept_id\nDrug\tDrug\t13\n")
    (folder / "CONCEPT_CLASS.csv").write_text("concept_class_id\tconcept_class_name\tconcept_class_concept_id\nIngredient\tIngredient\t44819247\n")
    (folder / "RELATIONSHIP.csv").write_text("relationship_id\trelationship_name\tis_hierarchical\tdefines_ancestry\treverse_relationship_id\t"
                                             "relationship_concept_id\nMaps to\tNon-standard to Standard map (OMOP)\t0\t0\tMapped from\t44818977\n")
    return folder


def test_the_profile_chooses_the_vocabulary_and_the_full_profile_refuses_a_missing_download(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEMALYSER_ATHENA", str(tmp_path / "absent"))
    assert testbed.choose_vocabulary("fast") == "sample"
    assert testbed.choose_vocabulary("full", "sample") == "sample"
    with pytest.raises(testbed.VocabularyRefused, match="pass --vocabulary sample"):
        testbed.choose_vocabulary("full")
    with pytest.raises(testbed.VocabularyRefused):
        testbed.choose_vocabulary("fast", "athena")
    monkeypatch.setenv("SCHEMALYSER_ATHENA", str(_download(tmp_path / "athena")))
    assert testbed.choose_vocabulary("full") == "athena"


def test_the_release_is_read_from_the_download_and_its_working_copy_is_built_once(tmp_path):
    folder = _download(tmp_path / "athena")
    assert testbed.athena_release(folder) == {"name": "OMOP Standardized Vocabularies", "version": "v5.0 29-AUG-26", "date": "2026-08-29"}
    copy, built = testbed.athena_working_copy(folder, tmp_path / "copies")
    assert built["built"] and built["rows"]["CONCEPT"] == 5
    # Only the 'Maps to' rows are kept.
    assert built["rows"]["CONCEPT_RELATIONSHIP"] == 1
    again, reused = testbed.athena_working_copy(folder, tmp_path / "copies")
    assert again == copy and not reused["built"] and reused["rows"] == built["rows"]
    # The lookups read the working copy as they read the download's own files.
    from schemalyser import mapping
    matched, _ = mapping.propose([("7", "paracetamol")], copy)
    assert matched["7"][0] == 1125315
    coded, _ = mapping.propose([("9", "K42.9")], copy, "Condition", coded_in="ICD10")
    assert coded["9"][0] == 4245842


def test_a_fast_run_with_the_athena_vocabulary_records_its_release(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEMALYSER_ATHENA", str(_download(tmp_path / "athena")))
    report = testbed.run("fixtures", tmp_path / "out", rows=60, vocabulary_choice="athena")
    assert report["summary"]["outcome"] == "passed", [c for c in report["checks"] if c["passed"] is False]
    assert report["versions"]["vocabulary"] == "the Athena release v5.0 29-AUG-26, of 2026-08-29"
    assert report["versions"]["vocabulary_release"]["date"] == "2026-08-29"
    assert report["build"]["vocabulary"] == "athena" and report["build"]["athena"]["working_copy"]["rows"]["CONCEPT"] == 5
    # The CDM holds only the concepts that its tables name, and the derived mappings match those of the sample.
    assert 0 < report["build"]["vocabulary_rows"]["concept"] <= 5
    assert sum(item["matched"] for item in report["build"]["derived_mappings"]) > 0
    # The licensed download is never copied into the output folder, and the report names no folder of this machine.
    assert not list((tmp_path / "out").rglob("CONCEPT.csv"))
    assert str(tmp_path) not in (tmp_path / "out" / "report.json").read_text()


def test_the_dashboard_reads_a_matching_release_where_it_already_is():
    def database(held):
        return lambda sql: held.get(sql.split(" FROM ")[1].split(".")[0])
    release = {"version": "v5.0 29-AUG-26"}
    assert testbed.dashboard_vocabulary(database({"cdm": "v5.0 29-AUG-26"}), "athena", release)["schema"] == "cdm"
    other = testbed.dashboard_vocabulary(database({"cdm": "v5.0 01-JAN-25"}), "athena", release)
    assert other["schema"] == testbed.VOCABULARY_SCHEMA and not other["reused"]
    kept = testbed.dashboard_vocabulary(database({"cdm": "v5.0 01-JAN-25", testbed.VOCABULARY_SCHEMA: "v5.0 29-AUG-26"}), "athena", release)
    assert kept["schema"] == testbed.VOCABULARY_SCHEMA and kept["reused"]
    assert testbed.dashboard_vocabulary(database({}), "sample", None)["schema"] == "cdm"
    script = testbed.vocabulary_load_script(Path("/somewhere/athena"))
    assert script.count("\\copy ") == len(testbed.DASHBOARD_TABLES) and "DROP SCHEMA IF EXISTS testbed_vocabulary CASCADE;" in script


# The per-check time limit.

def test_a_check_stopped_by_the_time_limit_is_reported_as_did_not_finish(tmp_path):
    path = tmp_path / "dqd_results.json"
    path.write_text(json.dumps({"CheckResults": [
        {"checkName": "plausibleValueLow", "cdmTableName": "MEASUREMENT", "cdmFieldName": "MEASUREMENT_CONCEPT_ID",
         "conceptId": "3027018", "unitConceptId": "8541", "category": "Plausibility", "failed": 0, "isError": 1, "notApplicable": 0,
         "error": "org.postgresql.util.PSQLException: ERROR: canceling statement due to statement timeout"},
        {"checkName": "isRequired", "cdmTableName": "COHORT", "cdmFieldName": "COHORT_DEFINITION_ID", "category": "Conformance",
         "failed": 0, "isError": 1, "notApplicable": 0, "error": "ERROR: relation \"testbed.cohort\" does not exist"}]}))
    found = testbed.dqd_results(path)
    assert (found["did_not_finish"], found["could_not_run"]) == (1, 1)
    stopped = next(f for f in found["failures"] if f["outcome"] == "did not finish")
    assert testbed._failure_name(stopped) == ("plausibleValueLow on MEASUREMENT.MEASUREMENT_CONCEPT_ID for the concept 3027018 "
                                              "in the unit 8541")
    assert not stopped["expected"] and found["unexpected_failures"] == 2


def test_the_dashboard_script_sets_the_per_check_time_limit(tmp_path):
    testbed.write_dqd_script(tmp_path, "cdm")
    script = (tmp_path / "dqd" / "run_dqd.R").read_text()
    assert "statement_timeout" in script and 'Sys.getenv("DQD_CHECK_MILLISECONDS"' in script
    assert 'vocabDatabaseSchema = "cdm"' in script


def test_a_sleep_of_the_machine_during_the_dashboard_is_measured():
    assert testbed.paused_seconds(3600.4, 3600.0) == 0
    assert testbed.paused_seconds(3437 + 280.0, 280.0) == 3437
