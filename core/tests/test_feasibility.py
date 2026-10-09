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
    assert neonatal["verdict_text"] == "This question is expressible but cannot yet be answered reliably."
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
        ("counts", "database analyst"), ("test query", "database analyst")]
    answer, values, question, counts, probe = requests
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
    text = feasibility.markdown(neonatal)
    assert text.startswith("# Neonatal low mean pressure\n\nThis question is expressible but cannot yet be answered reliably.")
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
    assert [q["verdict"] for q in found["questions"]] == ["not_yet", "not_yet", "answerable"]
    assert found["question_coverage"] == {"answered": 1, "total": 3}
    data = rolemap.read_saved_map(saved)
    columns = sum(len(c) for c in rolemap.all_views().values())
    structural = found["structural_coverage"]
    assert (structural["all_parts"], structural["all_columns"]) == (len(rolemap.all_views()), columns)
    assert structural["parts"] == len(data["roles"]) and 0 < structural["columns"] < columns
    text = feasibility.programme_markdown(found)
    assert "One of the three questions has every requirement checked against the database." in text
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
    assert "This question can be answered from the hospital schema as it stands." in report.read_text()
    assert "reconciles a sample of anaesthetics" in report.read_text()
