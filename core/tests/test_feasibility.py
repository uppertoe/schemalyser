"""Whether a question can be answered from a saved hospital schema, on the invented world only.

The saved schema is made as screen 1 makes it: the invented dictionary is proposed, some columns are confirmed and some
are not, the hospital's codes are chosen for the two mean pressures, a test query of one link is pasted from the
production database, and two of the three counts are judged to look right, so that the patients' part alone reaches
the state checked against the database.
"""
import json
import re

import pytest

from schemalyser import describe, feasibility, rolemap
from test_describe import DATE, DICTIONARY, TABLES, tables_result

AUDIT = rolemap.AUDIT.read_text(encoding="utf-8")
CONFIRMED = ["role_patient rows", "role_patient.patient_key", "role_patient.birth_date", "role_patient.is_test",
             "role_anaesthetic rows", "role_anaesthetic.anaesthetic_key", "role_anaesthetic.patient_key",
             "role_anaesthetic.start_time", "role_anaesthetic.stop_time", "role_reading rows", "role_reading.anaesthetic_key",
             "role_reading.kind", "role_reading.reading_time", "role_reading.value"]
COUNTS = {"coverage_by_year": "start_year\tanaesthetics\twith_patient\twith_birth_date\twith_death_date\ttest_patients\twith_stop\tstop_before_start\n"
                              "2024\t1200\t1190\t1180\t20\t10\t1150\t0\n",
          "repeated_keys": "role_view\tkeys_repeated\trows_held\nrole_patient\t0\t0\n"}
LINK = "role_reading.anaesthetic_key -> role_anaesthetic.anaesthetic_key"
PATIENT_LINK = "role_anaesthetic.patient_key -> role_patient.patient_key"


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    s.set_settings("production", 2024, "Australia/Sydney", True)
    s.tables_query()
    s.read_tables(tables_result())
    for about in CONFIRMED:
        s.confirm(about, "yes", date=DATE)
    s.confirm("role_reading.accepted", "not sure", date=DATE)
    s.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff"}, DATE)
    s.probe_query("role_reading.anaesthetic_key", 2024)
    s.read_probe("role_reading.anaesthetic_key", "anaesthetics\twith_rows\twithout_rows\n1200\t1100\t100\n")
    for query in s.count_queries(2024, "8. Run the counts"):
        if query["name"] in COUNTS:
            s.read_count(query["name"], COUNTS[query["name"]], DATE)
            s.judge_count(query["name"], "yes", "", DATE)
    path = tmp_path_factory.mktemp("schema") / "hospital-schema.schemalyser.zip"
    path.write_bytes(s.save_zip(DATE))
    return path


@pytest.fixture(scope="module")
def schema(saved):
    return feasibility.Schema.load(saved)


@pytest.fixture(scope="module")
def neonatal(schema):
    return feasibility.assess(schema, AUDIT, "neonatal_low_mean_pressure.sql")


def test_the_requirements_of_the_neonatal_audit_are_read_from_its_sql():
    needs = feasibility.requirements(AUDIT, "neonatal_low_mean_pressure.sql")
    assert needs["question"].startswith("Among neonates who had an anaesthetic") and needs["question"].endswith("?")
    assert needs["parts"] == ["role_patient", "role_anaesthetic", "role_reading"]
    assert set(needs["columns"]) == {f"{v}.{c}" for v, cs in rolemap.views().items() for c in cs} - {
        "role_reading.reading_key", "role_reading.value_text"}
    assert [l["id"] for l in needs["links"]] == [PATIENT_LINK, LINK]
    assert [k["kind"] for k in needs["kinds"]] == ["map_arterial", "map_cuff"]
    assert not needs["gaps"]
    # The time arithmetic, with its columns in the order of the arguments, and whether it measures time on the clock.
    times = {(t["form"], t["unit"]): t for t in needs["time"]}
    assert times[("diff", "day")]["columns"] == ["role_patient.birth_date", "role_anaesthetic.start_time"]
    assert times[("diff", "second")]["columns"] == ["role_reading.reading_time", "role_anaesthetic.stop_time"]
    assert times[("diff", "second")]["clock"] and not times[("add", "day")]["clock"]
    tested = {c for item in needs["conditions"] for c in item["columns"]}
    assert {"role_patient.is_test", "role_reading.accepted", "role_reading.kind", "role_reading.value"} <= tested
    assert [o["name"] for o in needs["named_outcomes"]] == ["death_within_days"]
    assert "role_patient.death_date" in {c for o in needs["outcomes"] for c in o["columns"]}


def test_the_neonatal_audit_gives_the_state_of_each_requirement_from_the_saved_schema(neonatal):
    states = {r["id"]: r["state"] for r in neonatal["states"]}
    assert neonatal["verdict_text"] == (
        "This question is expressible but cannot yet be answered reliably. Not every requirement of the question is yet "
        "mapped and checked against the database, and the coverage of the recording pathways has not been assessed.")
    # The two claims stay apart: neither is established, and each is a field of its own.
    assert neonatal["claims"] == {"requirements_checked": False, "pathway_coverage_assessed": False}
    assert [(c["part"], c["state"]) for c in neonatal["coverage"]["parts"]] == [
        (view, feasibility.COVERAGE_NOT_ASSESSED) for view in ("role_patient", "role_anaesthetic", "role_reading")]
    assert neonatal["coverage"]["period"] == {"from": "2024-01-01", "to": "2024-12-31"}
    assert states["role_patient"] == states["role_patient.birth_date"] == feasibility.CHECKED
    assert states["role_patient.death_date"] == feasibility.PROPOSED
    # A flag confirmed whose codes are not yet translated, and a column marked not sure, are not confirmed.
    assert states["role_patient.is_test"] == states["role_reading.accepted"] == feasibility.PROPOSED
    assert states["role_anaesthetic"] == states["role_reading.value"] == feasibility.CONFIRMED
    # The link whose test query was counted on the production database is checked; the other is only confirmed.
    assert states[LINK] == feasibility.CHECKED and states[PATIENT_LINK] == feasibility.CONFIRMED
    assert states["role_reading.kind = map_arterial"] == states["role_reading.kind = map_cuff"] == feasibility.CONFIRMED
    assert feasibility.VALIDATED not in states.values()
    # The dimensions stay apart in the JSON.
    rows = {r["id"]: r for r in json.loads(feasibility.to_json(neonatal))["states"]}
    birth = rows["role_patient.birth_date"]["evidence"]
    assert birth["confirmation"] == {"status": "person", "answer": "yes", "confirmed": True, "date": DATE, "by": "a person", "asked": None}
    assert birth["present"] == "present" and birth["part"]["checked"] == DATE and birth["part"]["validated"] is None
    assert birth["part"]["measured"]["with_patient"] == 99
    assert rows["role_patient.is_test"]["evidence"]["translation"] == {"form": "flag", "translated": False}
    assert rows["role_reading.accepted"]["evidence"]["confirmation"]["answer"] == "not sure"
    probe = rows[LINK]["evidence"]["probe"]
    assert probe["real"] and probe["database"] == "production"
    assert probe["figures"] == {"anaesthetics": 1200, "with_rows": 1100, "without_rows": 100}
    assert rows[LINK]["evidence"]["test"]["passed"] and rows[PATIENT_LINK]["evidence"]["probe"] is None
    kind = rows["role_reading.kind = map_arterial"]["evidence"]
    assert (kind["codes"], kind["chosen"], kind["date"], kind["by"]) == (1, True, DATE, "a person")


def test_the_neonatal_audit_asks_for_the_smallest_investigation_of_each_gap(neonatal):
    requests = neonatal["requests"]
    assert [(r["form"], r["role"]) for r in requests] == [
        ("answer", "database analyst"), ("values", "database analyst"), ("question", "clinician"),
        ("counts", "database analyst"), ("test query", "database analyst"), ("pathway coverage", "clinician")]
    answer, values, question, counts, probe, coverage = requests
    assert answer["moves"] == ["role_patient.death_date"] and answer["step"] == "6. Confirm each column"
    assert answer["question"] == ("The page proposes PERSON_MASTER_2.DEATH_TS as the date of death in Patients. Is that right, "
                                  "and if not, which column holds it?")
    assert values["moves"] == ["role_patient.is_test"] and "TEST_PERSON_FLAG" in values["sql"] and "TOP (50)" in values["sql"]
    assert question["question"].startswith("The page proposes OBS_READING.ACCEPTED_FLAG as the accepted or rejected")
    assert "Copy the questions at step 6" in question["says"]
    # One request moves every requirement that the readings count would check, and asks only for the count not yet judged.
    assert counts["id"] == "counts:readings_by_kind" and "#cohort" in counts["sql"] and "with_needed_kind" in counts["sql"]
    assert {"role_anaesthetic", "role_reading.value", "role_reading.kind = map_cuff", "role_reading.kind = map_arterial"} <= set(counts["moves"])
    assert "role_patient.birth_date" not in counts["moves"]
    assert probe["moves"] == [PATIENT_LINK] and "without_rows" in probe["sql"]
    # The assessment of the recording pathways is asked for apart from every requirement, and the import can take it.
    assert coverage["moves"] == coverage["parts"] == ["role_patient", "role_anaesthetic", "role_reading"]
    assert coverage["period"] == {"from": "2024-01-01", "to": "2024-12-31"} and coverage["queries"] == []
    assert [c["name"] for c in coverage["expects"][0]["columns"]] == ["part", "period_from", "period_to", "pathways_found",
                                                                       "pathways_mapped", "note"]
    text = feasibility.markdown(neonatal)
    assert text.startswith("# Neonatal low mean pressure\n\nThis question is expressible but cannot yet be answered reliably.")
    assert "## What has not yet been established" in text and "What is missing" not in text and "answerable" not in text
    assert "## The coverage of the recording pathways" in text
    assert "- No person has assessed the recording pathways of Readings charted during an anaesthetic for this period." in text
    assert "| The link from Readings charted during an anaesthetic to Anaesthetics | Checked against the database |" in text
    assert "1. **For the database analyst.** This request concerns the date of death in Patients." in text
    assert "Three requirements are proposed, not confirmed: the date of death in Patients" in text
    # The prose of the report keeps to the page's vocabulary.
    prose = re.sub(r"```sql.*?```", "", text, flags=re.S)
    prose = re.sub(r"`[^`]*`", "", prose)
    assert not re.search(r"\b(map|maps|binding|bindings|view|views)\b", prose, re.I)
    assert "not available" not in prose and "!" not in prose


def test_a_question_that_names_what_the_role_model_does_not_describe_is_a_model_gap(schema):
    sql = ("-- How many anaesthetics had a pressure measured by Doppler.\n"
           "SELECT a.anaesthetic_key, r.unit\nFROM role_anaesthetic a\n"
           "JOIN role_reading r ON r.anaesthetic_key = a.anaesthetic_key\nWHERE r.kind = 'map_doppler'")
    found = feasibility.assess(schema, sql, "doppler.sql")
    gaps = {r["id"]: r for r in found["states"] if r["state"] == feasibility.NOT_DESCRIBED}
    assert set(gaps) == {"gap:column:role_reading.unit", "gap:kind:role_reading.kind=map_doppler"}
    assert gaps["gap:column:role_reading.unit"]["title"] == "A column named unit in Readings charted during an anaesthetic"
    assert found["verdict_text"] == "This question needs parts the role model does not yet describe."
    assert found["requests"][0]["role"] == "clinician" and found["requests"][0]["form"] == "model"
    assert "role_reading.unit" not in found["requirements"]["columns"]


QUESTIONS = {
    "a_neonatal.sql": AUDIT,
    "b_deaths.sql": "-- How many patients who are not test patients have died.\n"
                    "SELECT COUNT(*) AS died FROM role_patient p WHERE p.death_date IS NOT NULL AND p.is_test = 0",
    "c_births.sql": "-- How many patients were born from 2020.\n"
                    "SELECT COUNT(*) AS born FROM role_patient p WHERE p.birth_date >= '2020-01-01'",
}


def test_the_programme_orders_what_holds_back_the_most_questions_first(schema, saved, tmp_path):
    found = feasibility.programme(schema, list(QUESTIONS.items()))
    blocking = found["blocking"]
    assert [(r["id"], r["count"]) for r in blocking[:2]] == [("role_patient.death_date", 2), ("role_patient.is_test", 2)]
    assert all(r["count"] == 1 for r in blocking[2:])
    assert "role_patient.birth_date" not in {r["id"] for r in blocking}
    assert {r["id"]: r["count"] for r in found["requirements"]}["role_patient.birth_date"] == 2
    assert [q["verdict"] for q in found["questions"]] == ["not_yet", "not_yet", "requirements_checked"]
    assert found["question_coverage"] == {"answered": 1, "total": 3}
    assert found["pathway_coverage"] == {"assessed": 0, "total": 3}
    data = rolemap.read_saved_map(saved)
    columns = sum(len(c) for c in rolemap.all_views().values())
    structural = found["structural_coverage"]
    assert (structural["all_parts"], structural["all_columns"]) == (len(rolemap.all_views()), columns)
    assert structural["parts"] == len(data["roles"]) and 0 < structural["columns"] < columns
    text = feasibility.programme_markdown(found)
    assert "One of the three questions has every requirement checked against the database." in text
    assert "None of the three questions has the coverage of its recording pathways assessed for its period" in text
    # The command line writes the same report from a folder of questions.
    folder = tmp_path / "questions"
    folder.mkdir()
    for name, sql in QUESTIONS.items():
        (folder / name).write_text(sql, encoding="utf-8")
    out = tmp_path / "programme.json"
    assert feasibility.main(["programme", str(saved), str(folder), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["question_coverage"] == {"answered": 1, "total": 3}
    report = tmp_path / "report.md"
    assert feasibility.main(["report", str(saved), str(folder / "c_births.sql"), "--out", str(report)]) == 0
    assert ("Every requirement of this question is mapped and checked against the database; the coverage of the recording "
            "pathways has not been assessed.") in report.read_text()
    assert "reconciles a sample of anaesthetics" in report.read_text()


# The capabilities that a question names, and the mapping views that they need.

NAMED = """-- How much blood did each anaesthetic need, and what was the principal diagnosis of its stay?
-- capability: transfusion
-- capability: principal_diagnosis
SELECT a.anaesthetic_key, a.start_time
FROM   role_anaesthetic a
"""


def test_a_question_s_requirements_are_resolved_through_the_capabilities_it_names(schema):
    needs = feasibility.requirements(NAMED, "blood.sql")
    assert [c["name"] for c in needs["capabilities"]] == ["transfusion", "principal_diagnosis"]
    # The capability's own requirements join the question's, the weight among them, and the mapping view of diagnoses.
    assert {"role_fluid.volume_ml", "role_fluid.volume_form", "role_anaesthetic_detail.weight_kg", "role_diagnosis.diagnosis"} <= set(needs["columns"])
    assert "role_fluid.kind = red_cells" in {k["id"] for k in needs["kinds"]}
    assert needs["mapping_views"] == ["map_diagnosis_concept"] and "map_diagnosis_concept" not in needs["parts"]
    assert needs["needed_by"]["role_anaesthetic_detail.weight_kg"] == ["transfusion"]
    found = feasibility.assess(schema, NAMED, "blood.sql")
    states = {c["name"]: c["state"] for c in found["capabilities"]}
    # The invented hospital records no fluids, and translates no diagnosis, so neither capability is yet supported:
    # an ordinary outcome, with the evidence requests that would change it.
    assert states == {"transfusion": feasibility.NOT_MAPPED, "principal_diagnosis": feasibility.NOT_MAPPED}
    row = next(r for r in found["states"] if r["id"] == "map_diagnosis_concept")
    assert row["form"] == "mapping" and row["capabilities"] == ["principal_diagnosis"]
    assert row["recorded"] == feasibility.WORDING["mapping_none"]
    assert any(r["form"] == "concept translation" and r["moves"] == ["map_diagnosis_concept"] for r in found["requests"])
    text = feasibility.markdown(found)
    assert "## The capabilities that the question names" in text and "- transfusion, version 1:" in text and "!" not in text
    # The report's shape is otherwise unchanged.
    assert {"verdict", "states", "requests", "requirements"} <= set(found)


def test_a_capability_that_the_catalogue_does_not_hold_is_a_gap_of_the_role_model(schema):
    sql = "-- capability: minutes_of_unicorns\nSELECT a.anaesthetic_key FROM role_anaesthetic a\n"
    found = feasibility.assess(schema, sql, "unknown.sql")
    assert found["verdict"] == "model"
    assert [c["state"] for c in found["capabilities"]] == [feasibility.NOT_DESCRIBED]
    assert any(r["id"] == "gap:capability:minutes_of_unicorns" for r in found["states"])


def test_a_translation_by_a_person_moves_a_mapping_view_and_one_from_a_reference_is_only_proposed(tmp_path):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    s.translate_concepts("map_drug_concept", [{"code": "CEPHAZOLIN", "concept_id": 9100001, "status": "mapped",
                                               "provenance": "a reference conversion"}], date=DATE)
    s.translate_concepts("map_diagnosis_concept", [{"code": "Q36.9", "concept_id": 9100201, "status": "mapped", "provenance": "a person"},
                                                   {"code": "S42.4", "concept_id": 0, "status": "unmapped", "provenance": "a person"}],
                         date=DATE)
    path = tmp_path / "schema.zip"
    path.write_bytes(s.save_zip(DATE))
    schema = feasibility.Schema.load(path)
    sql = "-- capability: principal_diagnosis\n-- capability: exposure_intervals\nSELECT a.anaesthetic_key FROM role_anaesthetic a\n"
    found = feasibility.assess(schema, sql, "two.sql")
    rows = {r["id"]: r for r in found["states"]}
    assert rows["map_diagnosis_concept"]["state"] == feasibility.CONFIRMED
    assert rows["map_diagnosis_concept"]["evidence"]["statuses"] == {"mapped": 1, "unmapped": 1, "ambiguous": 0}
    assert rows["map_drug_concept"]["state"] == feasibility.PROPOSED
    assert "No person has yet confirmed any of the concepts." in rows["map_drug_concept"]["recorded"]
    # A capability declared with no SQL yet is resolved like any other, and its requirements say what it would read.
    exposure = next(c for c in found["capabilities"] if c["name"] == "exposure_intervals")
    assert {"role_drug.order_key", "role_drug.amends_key", "map_drug_concept", "map_unit_concept"} <= set(exposure["requirements"])


# A16: the second claim, which only a person's assessment of the recording pathways establishes.

BIRTHS = QUESTIONS["c_births.sql"]
ASSESSED = "part\tperiod_from\tperiod_to\tpathways_found\tpathways_mapped\nrole_patient\t{start}\t{end}\t{found}\t{mapped}\n"


def _assessed(saved, start="2024-01-01", end="2024-12-31", found=1, mapped=1):
    """The saved schema with a person's assessment of the patients' recording pathways taken back through the import."""
    files = feasibility.Schema.load(saved).files
    s = describe.Describe()
    s.version = "test"
    s.restore(files)
    request = next(r for r in feasibility.assess(feasibility.Schema(files), BIRTHS, "births.sql")["requests"]
                   if r["form"] == "pathway coverage")
    imported = s.import_evidence(request, ASSESSED.format(start=start, end=end, found=found, mapped=mapped), "Dr C", date=DATE)
    return feasibility.Schema(imported["files"]), s


def test_every_requirement_checked_is_never_stated_as_more_than_the_first_claim(schema):
    found = feasibility.assess(schema, BIRTHS, "births.sql")
    assert found["verdict"] == "requirements_checked"
    assert found["claims"] == {"requirements_checked": True, "pathway_coverage_assessed": False}
    assert found["verdict_text"] == ("Every requirement of this question is mapped and checked against the database; the coverage "
                                     "of the recording pathways has not been assessed.")
    request = next(r for r in found["requests"] if r["form"] == "pathway coverage")
    assert request["moves"] == ["role_patient"] and "python -m schemalyser.describe import-evidence" in request["says"]


def test_an_assessment_of_the_recording_pathways_establishes_the_second_claim_and_leaves_the_first_alone(saved):
    schema, _ = _assessed(saved)
    found = feasibility.assess(schema, BIRTHS, "births.sql")
    assert found["verdict"] == "requirements_and_coverage"
    assert found["claims"] == {"requirements_checked": True, "pathway_coverage_assessed": True}
    assert found["verdict_text"] == ("Every requirement of this question is mapped and checked against the database, and a person "
                                     "has assessed the coverage of the recording pathways of every part it reads over the "
                                     "question's period, and found every pathway mapped.")
    part = found["coverage"]["parts"][0]
    assert part["state"] == feasibility.COVERAGE_ASSESSED and part["assessments"][0]["by"] == "Dr C"
    assert not any(r["form"] == "pathway coverage" for r in found["requests"])
    # The first claim's states are what they were: the assessment moves no requirement.
    before = feasibility.assess(feasibility.Schema.load(saved), BIRTHS, "births.sql")
    assert [(r["id"], r["state"]) for r in found["states"]] == [(r["id"], r["state"]) for r in before["states"]]
    text = feasibility.markdown(found)
    assert "- Dr C assessed the recording pathways of Patients on 7 October 2026, for 2024-01-01 to 2024-12-31, and found one pathway, all of which the hospital schema has mapped." in text
    # The neonatal audit reads two more parts, which no one has assessed, so its second claim does not hold.
    neonatal = feasibility.assess(schema, AUDIT, "neonatal.sql")
    assert neonatal["claims"]["pathway_coverage_assessed"] is False
    assert neonatal["verdict_text"].endswith("the coverage of the recording pathways has not been established for Anaesthetics "
                                             "and Readings charted during an anaesthetic over the question's period.")
    assert next(r for r in neonatal["requests"] if r["form"] == "pathway coverage")["moves"] == ["role_anaesthetic", "role_reading"]


def test_an_assessment_is_read_for_the_question_s_period_and_says_what_it_found(saved):
    # A pathway found and not mapped is stated as such, and the second claim does not hold.
    gap, _ = _assessed(saved, found=2, mapped=1)
    found = feasibility.assess(gap, BIRTHS, "births.sql")
    assert found["verdict"] == "requirements_checked" and found["coverage"]["parts"][0]["state"] == feasibility.COVERAGE_GAP
    assert found["verdict_text"].endswith("a person's assessment of the recording pathways found a pathway that the hospital schema "
                                          "has not mapped, in Patients.")
    assert "and found two pathways, of which the hospital schema has mapped one." in feasibility.markdown(found)
    # An assessment of half the year covers a question of that half, and not a question of the whole year.
    half, _ = _assessed(saved, end="2024-06-30")
    assert feasibility.assess(half, BIRTHS, "births.sql", ("2024-01-01", "2024-06-30"))["claims"]["pathway_coverage_assessed"]
    whole = feasibility.assess(half, BIRTHS, "births.sql")
    assert whole["coverage"]["parts"][0]["state"] == feasibility.COVERAGE_PARTLY and not whole["claims"]["pathway_coverage_assessed"]
    assert feasibility.assess(half, BIRTHS, "births.sql", ("2023-01-01", "2023-12-31"))["coverage"]["parts"][0]["state"] == \
        feasibility.COVERAGE_NOT_ASSESSED
    # A change to the pathways that the hospital schema maps for the part leaves the assessment stale.
    _, s = _assessed(saved)
    s.confirm("role_patient rows", "no", "PERSON_MASTER_2", date=DATE)
    stale = feasibility.assess(feasibility.Schema(s.save(DATE)), BIRTHS, "births.sql")
    assert stale["coverage"]["parts"][0]["state"] == feasibility.COVERAGE_STALE
    assert stale["coverage"]["parts"][0]["assessments"][0]["stale"] == ["the pathways changed"]
