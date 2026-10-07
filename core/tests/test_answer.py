"""What an answer depends on, facts that a person confirmed, the specification, the check of a hand-written query,
and a result that is safe by default."""
import csv
import io
import json
import shutil
import sys
from pathlib import Path

import pytest
import sqlglot

from schemalyser import Analysis, boundary, browser, convert, facts, harness, target
from schemalyser.catalogue import Catalogue

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
NEONATAL = (FIXTURES / "targets" / "neonatal_low_mean_pressure.sql").read_text()
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
CATALOGUE = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
REALISTIC = ROOT / "reference" / "worlds" / "clarity"
REALISTIC_CONVERSION = ROOT / "etl" / "clarity"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


@pytest.fixture(scope="module")
def neonatal():
    return target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, name="neonatal_low_mean_pressure")


# What the answer depends on.

def test_the_answer_depends_only_on_what_its_columns_and_conditions_reach(neonatal):
    found = target.answer_dependencies(CONVERSION, NEONATAL, CATALOGUE)
    assert {"ANAES_RECORD", "OBS_READING", "OBS_SHEET", "PERSON_MASTER_2"} <= found["tables"]
    assert "WARD_DEF" not in found["tables"] and "STAFF_MASTER" not in found["tables"]
    assert "SITE_OBS" in found["vocabularies"] and "SITE_VISIT_DETAIL" not in found["vocabularies"]
    assert ("visit_occurrence.sql", "omop.care_site") in found["not_needed"]
    assert ("visit_detail_through_case.sql", "omop.provider") in found["not_needed"]
    assert frozenset({"OBS_READING.SHEET_KEY", "OBS_SHEET.SHEET_KEY"}) in found["joins"]
    # The checklist uses it: what the answer does not depend on no longer blocks the first phase.
    rows, traced = neonatal
    phases = {row["question_id"]: row["phase"] for row in rows}
    for key in ("codes-SITE_VISIT_DETAIL", "relationship-VISIT.WARD_KEY=WARD_DEF.WARD_KEY", "table-WARD_DEF"):
        assert phases[key] == "unneeded", key
    assert traced["dependencies"] == found
    assert "does not depend on" in target.readiness(rows, traced)
    assert target.stage_counts(rows, "source")["total"] < target.stage_counts(rows)["total"]


@pytest.mark.skipif(not (REALISTIC / "catalogue.csv").exists() or not (REALISTIC_CONVERSION / "conversion.json").exists(),
                    reason="the private realistic world is not present")
def test_on_the_realistic_world_the_codes_that_the_answer_never_reads_no_longer_block():
    world = harness.World.from_folder(REALISTIC)
    rows, _ = target.checklist(world, REALISTIC_CONVERSION, NEONATAL)
    phases = {row["question_id"]: row["phase"] for row in rows}
    assert phases["codes-SITE_PROV_TYPE"] == "unneeded" and phases["codes-SITE_VISIT_DETAIL"] == "unneeded"
    unneeded_joins = [row for row in rows if row["kind"] == "relationship" and row["phase"] == "unneeded"]
    assert len(unneeded_joins) >= 2
    assert not any(row["blocking"] == "yes" and row["status"] != "answered" for row in unneeded_joins
                   if row["phase"] == "source")


# Facts that a person confirmed.

def test_facts_are_checked_against_the_catalogue_and_a_later_fact_replaces_an_earlier_one():
    join = {"kind": "join", "left": "obs_sheet.visit_key", "right": "VISIT.VISIT_KEY", "answer": "yes", "date": "2026-10-05",
            "who": "A colleague"}
    found = facts.check(join, CATALOGUE)
    assert found["left"] == "OBS_SHEET.VISIT_KEY" and found["who"] == "A colleague"
    for bad in ({**join, "left": "SECRET.VISIT_KEY"}, {**join, "answer": "maybe"}, {**join, "date": "5 October"},
                {**join, "who": "two\nlines"}, {**join, "kind": "guess"},
                {**join, "answer": "no", "instead": ["OBS_SHEET.SHEET_KEY", "WARD_DEF.WARD_KEY"]},
                {"kind": "codes", "vocabulary": "SITE_OBS", "concept": 21490852, "codes": ["=HYPERLINK(1)"], "date": "2026-10-05"},
                {"kind": "codes", "vocabulary": "SITE OBS;", "concept": 21490852, "codes": ["52"], "date": "2026-10-05"},
                {"kind": "codes", "vocabulary": "SITE_OBS", "concept": "x", "codes": ["52"], "date": "2026-10-05"}):
        with pytest.raises(facts.FactsError):
            facts.check(bad, CATALOGUE)
    held = facts.Facts().with_fact(found).with_fact(facts.check({**join, "answer": "no"}, CATALOGUE))
    assert len(held.items) == 1 and held.join("VISIT.VISIT_KEY", "OBS_SHEET.VISIT_KEY")["answer"] == "no"
    again = facts.Facts.from_json(held.to_json(), CATALOGUE)
    assert again.items == held.items
    with pytest.raises(facts.FactsError):
        facts.Facts.from_json('{"facts": [{"kind": "join"}]}', CATALOGUE)


def test_codes_become_the_sites_mapping_rows_and_the_sites_row_wins_a_clash(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    held = facts.Facts().with_fact(facts.check(
        {"kind": "codes", "vocabulary": "SITE_OBS", "concept": 21490852, "codes": ["52", "77"], "date": "2026-10-05"}, CATALOGUE))
    held = held.with_fact(facts.check(
        {"kind": "codes", "vocabulary": "SITE_OBS", "concept": 3027018, "codes": ["51"], "date": "2026-10-05"}, CATALOGUE))
    (folder / facts.SITE_MAPPINGS).write_text(held.site_mappings())
    rows = convert.mapping_dicts(folder)
    by_code = {(r["source_vocabulary_id"], r["source_code"]): r["target_concept_id"] for r in rows}
    # The site adds the code 77, and its row for 51 replaces the conversion's own.
    assert by_code[("SITE_OBS", "77")] == "21490852" and by_code[("SITE_OBS", "51")] == "3027018"
    assert sum(1 for r in rows if (r["source_vocabulary_id"], r["source_code"]) == ("SITE_OBS", "51")) == 1
    assert by_code[("SITE_OBS", "8")] == "3027018"      # the conversion's other rows stay


def test_a_persons_answer_settles_an_item_and_a_no_says_which_step_must_change(neonatal):
    rows, traced = neonatal
    questions = traced["questions"]
    assert questions.startswith("Questions about neonatal_low_mean_pressure for a colleague")
    assert "by joining on OBS_SHEET.VISIT_KEY = VISIT.VISIT_KEY. Is that right?" in questions
    assert "!" not in questions
    given = {"facts": [
        {"kind": "join", "left": "OBS_SHEET.VISIT_KEY", "right": "VISIT.VISIT_KEY", "answer": "yes", "date": "2026-10-05",
         "who": "Dr Zanzibar Quill"},
        {"kind": "join", "left": "PERSON_MASTER.PERSON_KEY", "right": "PERSON_MASTER_2.PERSON_KEY", "answer": "no",
         "instead": ["PERSON_MASTER.RECORD_NO", "PERSON_MASTER_2.PERSON_KEY"], "date": "2026-10-06"}]}
    found, traced = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, facts_text=json.dumps(given), name="n")
    by_id = {row["question_id"]: row for row in found}
    yes = by_id["relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"]
    assert yes["status"] == "answered" and yes["currently_from"] == "a person"
    assert "You confirmed on 2026-10-05 that OBS_SHEET.VISIT_KEY matches VISIT.VISIT_KEY." in yes["evidence_in_hand"]
    no = by_id["relationship-PERSON_MASTER.PERSON_KEY=PERSON_MASTER_2.PERSON_KEY"]
    assert no["status"] == "open" and "the clinician will change it after the meeting" in no["evidence_in_hand"]
    assert "PERSON_MASTER.RECORD_NO matches PERSON_MASTER_2.PERSON_KEY instead" in no["evidence_in_hand"]
    # Who answered is kept only in facts.json.
    assert "Zanzibar" not in target.to_csv(found) + target.readiness(found, traced) + traced["questions"]
    assert "OBS_SHEET.VISIT_KEY joins to VISIT.VISIT_KEY" not in traced["questions"]


def test_codes_given_by_a_person_settle_the_concept(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    given = facts.Facts().with_fact(facts.check({"kind": "codes", "vocabulary": "SITE_OBS", "concept": 21490852, "codes": ["52"],
                                                 "date": "2026-10-05"}, CATALOGUE))
    (folder / facts.SITE_MAPPINGS).write_text(given.site_mappings())
    rows, traced = target.checklist(make_checks.WORLD, folder, NEONATAL, facts_text=given.to_json(), name="n")
    item = {row["question_id"]: row for row in rows}["codes-measurement.measurement_concept_id-21490852"]
    assert item["status"] == "answered" and "You chose on 2026-10-05 1 local code that mean this" in item["evidence_in_hand"]
    assert "the concept 21490852" not in traced["questions"]


# The specification, the check of a hand-written query, and a result that is safe by default.

def test_the_specification_states_the_rules_what_the_answer_rests_on_and_the_cases(neonatal):
    rows, traced = neonatal
    text = target.specification(CONVERSION, NEONATAL, rows, traced, CATALOGUE, "neonatal_low_mean_pressure")
    for heading in ("1. The question", "4. How the tables are joined", "5. What is left out", "6. The local codes",
                    "7. The result", "9. Decisions for the clinicians", "10. Cases to check the query against"):
        assert heading in text
    assert "A neonate is a child whose age at the start of the anaesthetic" in text and "age_days" not in text
    assert "OBS_READING.SHEET_KEY matches OBS_SHEET.SHEET_KEY" in text
    assert "OBS_SHEET.VISIT_KEY matches VISIT.VISIT_KEY" in text and ": not yet confirmed." in text
    assert "COALESCE(OBS_READING.ACCEPTED_FLAG, 'Y') <> 'N'" in text
    # A pasted result lists code 52, but nobody has chosen it, so what it means is still the folder's assumption, as the
    # checklist says.
    assert "In OBS_READING.OBS_TYPE_KEY, the code 52 is assumed to mean a mean arterial pressure measured through an arterial line" in text
    assert "the code 8 means" not in text and "WARD_DEF" not in text.split("10. Cases")[0]
    assert "python -m" not in text and "for use inside the hospital only" in text
    # What the audit cannot see from the database is always stated among what is not yet settled.
    unsettled = text.split("8. What is not yet settled")[1].split("9. Decisions")[0]
    assert "register of deaths" in unsettled and "gestational age in PERSON_MASTER_2.GEST_WEEKS" in unsettled
    assert "reconcile ten to twenty anaesthetics against their charts" in unsettled


def test_each_specification_carries_only_what_its_own_question_depends_on():
    airway_sql = (FIXTURES / "targets" / "airway_by_anaesthesia_type.sql").read_text()
    rows, traced = target.checklist(make_checks.WORLD, CONVERSION, airway_sql, CHECKS, name="airway_by_anaesthesia_type")
    text = target.specification(CONVERSION, airway_sql, rows, traced, CATALOGUE, "airway_by_anaesthesia_type")
    sections = {heading: text.split(heading)[1].split("\n\n", 2)[1] for heading in
                ("1. The question", "3. Where each part", "8. What is not yet settled", "9. Decisions for the clinicians")}
    # The question is stated in plain words and in this database's terms, and names nothing of the shared model.
    assert sections["1. The question"].startswith("Which airway devices are placed during each type of anaesthetic?")
    assert "PROCEDURE_OCCURRENCE" not in text and "DEVICE_EXPOSURE" not in text and "AIRWAY_DEVICE" in sections["1. The question"]
    # No section is empty.
    assert "The kind of device comes from AIRWAY_DEVICE.DEVICE_KIND_KEY." in sections["3. Where each part"]
    # The anaesthetic and its own record come from the same column, so the specification says so in one line.
    assert sections["3. Where each part"].count("from ANAES_RECORD.ANAES_KEY.") == 2, sections["3. Where each part"]
    assert "The anaesthetic itself comes from ANAES_RECORD.ANAES_KEY." in sections["3. Where each part"]
    assert "The anaesthetic's own record" not in sections["3. Where each part"]
    # Deaths, gestation and the pressure decisions belong to the pressure questions.
    assert "register of deaths" not in text and "gestational age" not in text and "arterial line" not in text
    assert sections["9. Decisions for the clinicians"].startswith("None of the decisions for the clinicians can change the answer")
    assert target.decisions_for(airway_sql, CONVERSION) == []
    assert target.decisions_for(NEONATAL, CONVERSION) == list(target.DECISIONS)
    assert target.question_title(airway_sql) == "Which airway devices are placed during each type of anaesthetic?"


def test_a_hand_written_query_is_checked_against_the_target_on_the_synthetic_rows():
    world = make_checks.WORLD
    draft = target.source_draft(CONVERSION, NEONATAL, CATALOGUE, blank=False)
    same = target.check_query(world, CONVERSION, NEONATAL, draft, rows=300)
    assert same["agree"] and same["differs"] == [] and same["columns"][-1] == "children_died_within_90_days"
    assert "neonatal_mean_pressure_minutes" in same["scenarios"]
    # A query that blanks the small counts is accepted.
    blanked = target.check_query(world, CONVERSION, NEONATAL, target.source_draft(CONVERSION, NEONATAL, CATALOGUE), rows=300)
    assert blanked["agree"]
    # A query with a different rule for a neonate does not agree, and both tables are shown.
    wrong = target.check_query(world, CONVERSION, NEONATAL, draft.replace("age_days < 28", "age_days < 29"), rows=300)
    assert not wrong["agree"] and {side for side, _ in wrong["differs"]} == {"target", "query"}
    with pytest.raises(target.TargetError):
        target.check_query(world, CONVERSION, NEONATAL, "SELECT * FROM NO_SUCH_TABLE", rows=300)


def test_the_counts_of_a_result_are_found_from_the_query_and_small_ones_are_blanked():
    tree = sqlglot.parse_one(NEONATAL, dialect="tsql")
    assert target.count_columns(tree) == ["anaesthetics", "died_within_90_days", "children", "children_died_within_90_days"]
    assert target.count_columns(sqlglot.parse_one("SELECT a.x, SUM(a.minutes) AS total FROM t a GROUP BY a.x", dialect="tsql")) == []
    safe = target.blanked(NEONATAL)
    assert "CASE WHEN r.children BETWEEN 1 AND 4 THEN NULL ELSE r.children END AS children" in safe
    assert "r.minutes_below_40," in safe and "ORDER BY\n  r.result_order" in safe
    assert target.blanked("SELECT a.x FROM t a") == "SELECT a.x FROM t a"


# The boundary and the page.

def _state(folder, facts_text=None):
    folder.mkdir()
    for name, source in (("catalogue.csv", "invented-catalogue.csv"), ("site-rules.json", "invented-site-rules.json"),
                         ("checks.csv", "invented-checks.csv")):
        shutil.copy(FIXTURES / source, folder / name)
    shutil.copytree(CONVERSION, folder / "conversion")
    (folder / "targets").mkdir()
    shutil.copy(FIXTURES / "targets" / "neonatal_low_mean_pressure.sql", folder / "targets")
    if facts_text is not None:
        (folder / "facts.json").write_text(facts_text)
    return folder


def test_the_boundary_reads_facts_and_writes_the_specification_only_when_asked(tmp_path):
    given = json.dumps({"facts": [{"kind": "join", "left": "OBS_SHEET.VISIT_KEY", "right": "VISIT.VISIT_KEY", "answer": "yes",
                                   "date": "2026-10-05", "who": "Dr Zanzibar Quill"}]})
    state = _state(tmp_path / "state", given)
    outputs, facts_found = boundary.produce(state, FIXTURES / "requests")
    checklist = outputs["targets/neonatal_low_mean_pressure/checklist.csv"]
    assert "You confirmed on 2026-10-05" in checklist
    assert not any("specification" in name for name in outputs)
    assert all("Zanzibar" not in text for text in outputs.values())
    (state / "boundary.json").write_text(json.dumps({"writeSpecification": True}))
    outputs, _ = boundary.produce(state, FIXTURES / "requests")
    assert "10. Cases to check the query against" in outputs["targets/neonatal_low_mean_pressure/specification.txt"]
    (state / "facts.json").write_text('{"facts": [{"kind": "join", "left": "SECRET.X", "right": "VISIT.VISIT_KEY", '
                                      '"answer": "yes", "date": "2026-10-05"}]}')
    with pytest.raises(boundary.BoundaryError):
        boundary.produce(state, FIXTURES / "requests")


def test_the_page_records_a_persons_answer_and_saves_it_with_the_state(monkeypatch, tmp_path):
    import zipfile
    state = _state(tmp_path / "state")
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser.clear()
    browser._analysis = Analysis((FIXTURES / "invented-catalogue.csv").read_text(), (FIXTURES / "invented-site-rules.json").read_text())
    browser.boundary_begin()
    for kind, folder, prefix in (("state", state, ""), ("requests", FIXTURES / "requests", "")):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            browser.boundary_put(kind, prefix + path.relative_to(folder).as_posix(), path.read_bytes())
    first = json.loads(browser.boundary_run())
    neonatal = first["targets"][0]
    assert neonatal["questions"] and neonatal["specification"].startswith("Specification of neonatal_low_mean_pressure")
    asked = next(row for row in neonatal["rows"] if row["ask"] and row["ask"]["kind"] == "join")
    assert asked["ask"]["tables"] and asked["stage"] == "source"
    assert json.loads(browser.fact_add(json.dumps({"kind": "join", "left": "SECRET.X", "right": "VISIT.VISIT_KEY",
                                                   "answer": "yes", "date": "2026-10-05"}))) == {"ok": False}
    fact = {"kind": "join", "left": asked["ask"]["left"], "right": asked["ask"]["right"], "answer": "yes", "date": "2026-10-05",
            "who": "Dr Zanzibar Quill"}
    result = json.loads(browser.fact_add(json.dumps(fact)))
    assert result["ok"]
    after = {row["id"]: row for row in result["boundary"]["targets"][0]["rows"]}
    assert after[asked["id"]]["status"] == "answered" and after[asked["id"]]["fact"] == "yes"
    codes = {"kind": "codes", "vocabulary": "SITE_OBS", "concept": "21490852", "codes": ["52"], "date": "2026-10-05"}
    assert json.loads(browser.fact_add(json.dumps(codes)))["ok"]
    saved = zipfile.ZipFile(io.BytesIO(browser.state_zip()))
    assert {"facts.json", "conversion/site_mappings.csv", "catalogue.csv", "checks.csv"} <= set(saved.namelist())
    assert "Zanzibar" in saved.read("facts.json").decode() and "52" in saved.read("conversion/site_mappings.csv").decode()
    assert "Zanzibar" not in json.dumps(result["boundary"])
    # An answer can be withdrawn: the fact leaves the page's facts and facts.json, and the question is asked again.
    answered = next(row for row in result["boundary"]["targets"][0]["rows"] if row["id"] == asked["id"])
    assert answered["withdraw"] == [{"kind": "join", "left": asked["ask"]["left"], "right": asked["ask"]["right"]}]
    withdrawn = json.loads(browser.fact_withdraw(json.dumps(answered["withdraw"])))
    again = {row["id"]: row for row in withdrawn["boundary"]["targets"][0]["rows"]}
    assert withdrawn["ok"] and again[asked["id"]]["status"] != "answered" and again[asked["id"]]["ask"]
    saved = zipfile.ZipFile(io.BytesIO(browser.state_zip()))
    assert "Zanzibar" not in saved.read("facts.json").decode() and "52" in saved.read("conversion/site_mappings.csv").decode()
    assert json.loads(browser.fact_withdraw("[]")) == {"ok": False}
    browser.clear()


def test_the_note_on_the_page_is_the_note_in_the_documents():
    import re
    note = (ROOT / "docs" / "first-ask-note.md").read_text().split("---\n", 1)[1].strip()
    strings = (ROOT / "site" / "src" / "strings.ts").read_text()
    found = re.search(r"const NOTE = `(.*?)`;", strings, re.S).group(1)
    assert found == note
    for claim in ("schema lock", "about five million rows", "invented examples", "[approval reference]", "OMOP anaesthesia layer"):
        assert claim in note
