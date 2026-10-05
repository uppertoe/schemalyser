import csv
import io
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from schemalyser import convert, harness, questions
from schemalyser import realistic, roles, tuning
from schemalyser import vocabulary as v
from schemalyser.catalogue import Catalogue

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CONVERSION = FIXTURES / "conversion"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

CHECKS = (FIXTURES / "invented-checks.csv").read_text()
PROFILE = (FIXTURES / "profile" / "invented-core-profile.csv").read_text()
PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]
RULES = json.loads((FIXTURES / "invented-site-rules.json").read_text())
# A spans result from the start to the stop of the anaesthetic, which the invented checks do not hold.
SPANS = "spans,ANAES_RECORD,ANAES_START_TS,60 to 119 minutes,ANAES_STOP_TS,120,,,\n"


def run(checks=None, profile=None, world=None, conversion=CONVERSION, **options):
    return questions.questions(world or make_checks.WORLD, conversion, checks, profile, **options)


def by_id(rows):
    return {row["question_id"]: row for row in rows}


def world_with_rules(tmp_path, rules):
    path = tmp_path / "site-rules.json"
    path.write_text(json.dumps(rules))
    return harness.World(FIXTURES / "invented-catalogue.csv", FIXTURES / "requests", path)


def conversion_copy(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    return folder


@pytest.fixture(scope="module")
def situations():
    return {"bare": run(), "checks": run(CHECKS), "both": run(CHECKS, PROFILE)}


# The file and its layout.

@pytest.mark.parametrize("name", ["bare", "checks", "both"])
def test_the_file_is_produced_for_the_invented_world(situations, name):
    rows = situations[name]
    text = questions.to_csv(rows)
    read = list(csv.DictReader(io.StringIO(text)))
    assert tuple(read[0]) == questions.LAYOUT
    assert len(read) == len(rows) > 40
    assert len({row["question_id"] for row in read}) == len(read)
    for row in read:
        assert row["status"] in questions.STATUSES
        assert row["who"] in questions.WHO
        assert row["mechanism"] in questions.MECHANISMS
        assert row["currently_from"] in questions.SOURCES
        assert row["stage"] in questions.STAGES
        assert all(row[k] for k in ("question", "decides", "evidence_needed", "evidence_in_hand"))
    # Every stage has questions, and the output is the same from one run to the next.
    assert {row["stage"] for row in read} == set(questions.STAGES)
    assert text == questions.to_csv(run(*{"bare": (), "checks": (CHECKS,), "both": (CHECKS, PROFILE)}[name]))


def test_the_register_covers_what_the_plan_asks_for(situations):
    ids = set(by_id(situations["bare"]))
    assert {"A-catalogue", "A-checks-values", "A-checks-spans", "A-check-errors", "B-repeat-anaesthetics",
            "B-roles-unused", "C-cdm-version", "C-visit-detail", "C-observation-periods", "E-setting-omop_schema",
            "E-setting-source_prefix", "E-setting-identifier_type", "E-setting-on_failure", "E-how-run"} <= ids
    assert {f"A-unread-{kind}" for kind in v.UNRESOLVED} <= ids
    # Each core field that the anaesthesia steps join on, and each table that the layer adds to.
    assert {"C-source-value-visit_occurrence.visit_source_value", "C-source-value-provider.provider_source_value"} <= ids
    assert {"C-domain-measurement", "C-domain-drug_exposure", "C-domain-device_exposure"} <= ids
    # One row for each mapping vocabulary that the anaesthesia steps read, found in the step SQL.
    assert {"D-mapping-SITE_OBS", "D-mapping-SITE_ROUTE", "D-mapping-SITE_DRUG_RUNNING", "D-mapping-SITE_RISK_GRADE",
            "D-mapping-SITE_PROCEDURE"} <= ids
    assert "D-mapping-SITE_SEX" not in ids     # a vocabulary that only the core layer reads


def test_every_release_setting_has_a_row(situations):
    from schemalyser.release import SETTINGS
    ids = by_id(situations["bare"])
    for key in SETTINGS:
        assert f"E-setting-{key}" in ids


def test_every_tunable_parameter_is_in_a_row(situations):
    rows = [r for r in situations["bare"] if r["question_id"].startswith("B-tuning-")]
    named = " ".join(r["evidence_in_hand"] for r in rows)
    for item in tuning.table():
        assert re.search(rf"\b{item['key']}\b", named), item["key"]
    assert re.search(rf"\b{tuning.STILL_IN_PLACE_VALUE}\b", named)
    # Each parameter is in one group only.
    grouped = [key for members in questions.TUNING_GROUPS.values() for key in members]
    assert len(grouped) == len(set(grouped))


def test_the_spans_evidence_names_the_pair_from_the_roles(situations):
    rows = by_id(situations["bare"])
    assert "from ANAES_RECORD.ANAES_START_TS to ANAES_RECORD.ANAES_STOP_TS" in rows["B-tuning-anaesthetic_duration"]["evidence_needed"]
    assert "from AIRWAY_DEVICE.PLACED_TS to AIRWAY_DEVICE.REMOVED_TS" in rows["B-tuning-removal"]["evidence_needed"]
    assert "from VISIT.ADMIT_TS to VISIT.DISCH_TS" in rows["B-tuning-discharge"]["evidence_needed"]
    assert rows["B-tuning-anaesthetic_duration"]["mechanism"] == "check script"
    assert rows["B-repeat-anaesthetics"]["status"] == "open"
    assert rows["B-repeat-anaesthetics"]["mechanism"] == "check script"


# Statuses follow the evidence.

def test_check_results_answer_stage_a_questions(situations):
    bare, checked = by_id(situations["bare"]), by_id(situations["checks"])
    assert bare["A-checks-values"]["status"] == "open"
    assert checked["A-checks-values"]["status"] in ("partly", "answered")
    assert checked["A-checks-values"]["currently_from"] == "check results"
    assert bare["A-catalogue"]["status"] == "answered"
    # The invented requests hold one part that is not SQL and one statement that builds SQL as text.
    assert bare["A-unread-parse_error"]["status"] == bare["A-unread-dynamic_sql"]["status"] == "open"
    assert bare["A-unread-opaque_statement"]["status"] == "answered"


def test_a_tuning_override_in_the_rules_answers_its_group(tmp_path):
    rules = dict(RULES, tuning={"anaesthetic_durations_minutes": [45, 60, 90], "shortest_anaesthetic_minutes": 40,
                                "end_tidal_co2_mean": 40})
    before, after = by_id(run()), by_id(run(world=world_with_rules(tmp_path, rules)))
    assert before["B-tuning-anaesthetic_duration"]["status"] == "open"
    assert after["B-tuning-anaesthetic_duration"]["status"] == "answered"
    assert after["B-tuning-anaesthetic_duration"]["currently_from"] == "site rules"
    assert after["B-tuning-end_tidal_co2"]["status"] == "partly"
    assert after["B-tuning-end_tidal_co2"]["currently_from"] == "invented default"


def test_spans_in_the_check_results_set_the_duration():
    rows = by_id(run(CHECKS + SPANS))
    duration = rows["B-tuning-anaesthetic_duration"]
    # The durations come from the check results; the shortest anaesthetic is still invented.
    assert duration["status"] == "partly"
    assert "anaesthetic_durations_minutes from the check results" in duration["evidence_in_hand"]
    assert "shortest_anaesthetic_minutes from an invented default" in duration["evidence_in_hand"]


def test_a_mapping_row_for_each_listed_value_answers_its_vocabulary(tmp_path, situations):
    before = by_id(situations["checks"])["D-mapping-SITE_OBS"]
    assert before["status"] == "partly"
    assert "OBS_READING.OBS_TYPE_KEY" in before["question"]
    folder = conversion_copy(tmp_path)
    listed = [row[3] for row in csv.reader(io.StringIO(CHECKS)) if row[:3] == ["values", "OBS_READING", "OBS_TYPE_KEY"]]
    with open(folder / "source_to_concept_map.csv", "a", newline="") as f:
        for code in listed:
            f.write(f"{code},0,SITE_OBS,Invented,0,None,1970-01-01,2099-12-31,\n")
    after = by_id(run(CHECKS, conversion=folder))["D-mapping-SITE_OBS"]
    assert after["status"] == "answered"
    assert "Each of them has a mapping row" in after["evidence_in_hand"]
    # Without check results nothing can be answered, whatever the mapping rows say.
    assert by_id(run(conversion=folder))["D-mapping-SITE_OBS"]["status"] == "partly"


def test_a_vocabulary_without_rows_is_open(situations):
    rows = by_id(situations["checks"])
    assert rows["D-mapping-SITE_VISIT_DETAIL"]["status"] == "open"
    assert "derived_mappings.json" in rows["D-mapping-SITE_DRUG"]["evidence_in_hand"]


def test_the_profile_answers_the_guesses_about_the_core(situations):
    bare, profiled = by_id(situations["checks"]), by_id(situations["both"])
    for key in ("C-cdm-version", "C-visit-detail", "E-setting-identifier_type", "E-setting-omop_schema", "E-setting-source_prefix"):
        assert bare[key]["status"] == "open" and bare[key]["currently_from"] == "a guess", key
        assert profiled[key]["status"] == "answered" and profiled[key]["currently_from"] == "core profile", key
    # The invented profile measures every source key that the steps join to visit_source_value, THEATRE_CASE.VISIT_KEY
    # among them, so the field is answered.
    source_value = "C-source-value-visit_occurrence.visit_source_value"
    assert bare[source_value]["status"] == "open" and bare[source_value]["currently_from"] == "a guess"
    assert profiled[source_value]["status"] == "answered" and profiled[source_value]["currently_from"] == "core profile"
    assert "98 per cent" in profiled[source_value]["evidence_in_hand"]
    # The core already carries the type concept that the layer writes to DRUG_EXPOSURE, so that stays partly answered.
    assert profiled["C-domain-drug_exposure"]["status"] == "partly"
    assert profiled["C-domain-measurement"]["status"] == "answered"
    # A decision that no profile can make stays open.
    assert profiled["E-setting-on_failure"]["status"] == "open"
    assert profiled["E-setting-on_failure"]["mechanism"] == "a decision"


def test_the_other_profiles_are_read_without_failing():
    sparse = by_id(run(CHECKS, (FIXTURES / "profile" / "invented-sparse-profile.csv").read_text()))
    general = by_id(run(None, (FIXTURES / "profile" / "invented-general-profile.csv").read_text()))
    assert sparse["E-setting-source_prefix"]["status"] != "answered"
    assert general["C-source-value-visit_occurrence.visit_source_value"]["status"] == "open"
    assert general["C-cdm-version"]["status"] == "partly"


def test_a_damaged_profile_is_refused():
    with pytest.raises(questions.QuestionsError):
        run(None, "CDM_TABLE,person,Y,abc,int,3\n")


def test_release_json_records_a_decision(tmp_path, situations):
    folder = conversion_copy(tmp_path)
    (folder / "release.json").write_text(json.dumps({"on_failure": "keep", "omop_schema": "cdm"}))
    rows = by_id(run(conversion=folder))
    assert rows["E-setting-on_failure"]["status"] == "answered"
    assert rows["E-setting-on_failure"]["currently_from"] == "release settings"
    assert rows["E-setting-omop_schema"]["status"] == "partly"
    # The value of a setting is never written, only that release.json sets it.
    assert "cdm" not in rows["E-setting-omop_schema"]["evidence_in_hand"].split()


def test_built_sandbox_lists_roles_it_could_not_apply(tmp_path):
    rules = dict(RULES, roles=[r for r in RULES["roles"] if r["role"] != "anaesthetic_stop"])
    rows = by_id(run(world=world_with_rules(tmp_path, rules), build=True, rows=200))
    not_applied = [r for r in rows.values() if r["question_id"].startswith("B-role-")]
    assert not_applied
    assert all(r["status"] == "open" and r["who"] == "clinical lead" for r in not_applied)
    # Without the stop of the anaesthetic, the evidence first needs the clinical lead.
    duration = rows["B-tuning-anaesthetic_duration"]
    assert duration["who"] == "clinical lead" and "their roles in the site rules" in duration["evidence_needed"]


# Nothing leaks.

def test_no_value_from_the_check_results_appears(tmp_path):
    marked, n = [], 0
    for row in csv.reader(io.StringIO(CHECKS)):
        if row and row[0] == "values":
            n += 1
            row[3], row[4] = f"QVAL{n}X", (f"QLABEL{n}X" if row[4] else "")
        marked.append(row)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(marked)
    text = questions.to_csv(run(out.getvalue() + SPANS, PROFILE))
    assert "QVAL" not in text and "QLABEL" not in text
    original = questions.to_csv(run(CHECKS, PROFILE))
    for row in csv.reader(io.StringIO(CHECKS)):
        if row and row[0] == "values" and row[4]:
            assert row[4] not in original
    # The fixed band labels of a spans result are not written either.
    assert "60 to 119 minutes" not in text


def test_no_planted_value_appears(situations):
    for rows in situations.values():
        text = questions.to_csv(rows).lower()
        assert not [value for value in PLANTED if value.lower() in text]


def _allowed():
    """Every word that the output may hold: the catalogue, the rules, the OMOP field list, the conversion's own
    steps, vocabularies and settings, and the fixed register."""
    words = set()

    def add(text):
        words.update(w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(text)))

    catalogue = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
    for table in catalogue.tables():
        add(table.name)
        for column in table.columns.values():
            add(column.name)
    add(json.dumps(RULES))
    for table, fields in convert.cdm_fields().items():
        add(table)
        for field, _, _ in fields:
            add(field)
    for step in json.loads((CONVERSION / "conversion.json").read_text()):
        add(step["file"])
    add(" ".join(questions.vocabularies(CONVERSION)))
    add("derived_mappings.json release.json source_to_concept_map.csv")
    from schemalyser.release import SETTINGS
    add(" ".join(SETTINGS))
    for register in (questions.WORDING, questions.IN_HAND, questions.NOUNS, questions.SOURCE_PHRASES,
                     questions.REASON_PHRASES, questions.STAGES, questions.TUNING_GROUPS, questions.SUMMARY):
        add(json.dumps(register))
    add(" ".join(questions.LAYOUT + questions.STATUSES + questions.WHO + questions.MECHANISMS + questions.SOURCES))
    add(json.dumps(tuning.table()))
    add(tuning.STILL_IN_PLACE_VALUE)
    add(" ".join(v.UNRESOLVED) + " " + " ".join(roles.ROLES) + " " + " ".join(realistic.REASONS))
    return words


def test_every_word_in_the_output_is_on_the_allowlist(situations, tmp_path):
    allowed = _allowed()
    extra = by_id(run(world=world_with_rules(tmp_path, dict(RULES, roles=[r for r in RULES["roles"]
                                                                          if r["role"] != "anaesthetic_stop"])),
                      build=True, rows=200)).values()
    for rows in [*situations.values(), list(extra)]:
        text = questions.to_csv(rows)
        unknown = {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if w.lower() not in allowed}
        assert not unknown, sorted(unknown)


# The wording.

def test_the_wording_is_calm_and_complete():
    texts = []
    for entry in questions.WORDING.values():
        texts += [value for key, value in entry.items() if key not in ("who", "mechanism")]
    texts += list(questions.IN_HAND.values())
    for text in texts:
        assert "?" not in text and "!" not in text, text
        assert "  " not in text, text
    for entry in questions.WORDING.values():
        assert entry["who"] in questions.WHO and entry["mechanism"] in questions.MECHANISMS
        for key in ("question", "decides", "evidence_needed"):
            assert entry[key] == "{description}" or entry[key].endswith("."), entry[key]
            assert entry[key][0].isupper() or entry[key][0] == "{", entry[key]


# The summary and the command.

def test_the_summary_counts_by_stage_status_and_who(situations):
    rows = situations["both"]
    text = questions.summary(rows)
    assert text.startswith(questions.SUMMARY["title"])
    assert f"questions: {len(rows)} " in text
    for stage in questions.STAGES:
        assert f"stage {stage}, " in text
    waiting = sum(1 for r in rows if r["status"] != "answered")
    assert sum(int(n) for n in re.findall(r"(?:team|lead) (\d+)", text.splitlines()[-1])) == waiting


def test_the_command_writes_the_file(tmp_path, monkeypatch, capsys):
    world = tmp_path / "world"
    world.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", world / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", world / "site-rules.json")
    shutil.copytree(FIXTURES / "requests", world / "requests")
    out = tmp_path / "questions.csv"
    monkeypatch.setattr(sys, "argv", ["schemalyser.questions", str(world), str(CONVERSION),
                                      "--checks", str(FIXTURES / "invented-checks.csv"),
                                      "--profile", str(FIXTURES / "profile" / "invented-core-profile.csv"),
                                      "--out", str(out)])
    questions.main()
    assert out.read_text() == questions.to_csv(run(CHECKS, PROFILE))
    assert capsys.readouterr().out == questions.summary(run(CHECKS, PROFILE))
