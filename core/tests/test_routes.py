"""A step gives way to an alternative whose tables and columns the catalogue holds, and the checklist says which and why."""
import csv
import io
import json
import shutil
from pathlib import Path

from schemalyser import boundary, routes
from schemalyser.catalogue import Catalogue

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
FULL = (FIXTURES / "invented-catalogue.csv").read_text()
EFFECT = ("This route cannot leave out test people, because the catalogue holds no column that marks them, "
          "so a test person may enter the answer and be counted.")


def _without(text, table=None, column=None):
    """The invented catalogue without a table, or without one column of a table."""
    rows = list(csv.reader(io.StringIO(text)))
    kept = [rows[0]] + [r for r in rows[1:] if r[1] != table and (r[1], r[2]) != column]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(kept)
    return out.getvalue()


def _conversion(folder):
    """A copy of the invented conversion whose person step offers an alternative with an effect on the answer."""
    shutil.copytree(FIXTURES / "conversion", folder)
    shutil.copy(FIXTURES / "routes" / "person_all_people.sql", folder / "person_all_people.sql")
    steps = json.loads((folder / "conversion.json").read_text())
    for step in steps:
        if step["file"] == "person.sql":
            step["alternatives"] = [{"file": "person_all_people.sql", "effect": EFFECT}]
    (folder / "conversion.json").write_text(json.dumps(steps))
    return folder


def test_a_full_catalogue_keeps_every_step_as_written():
    assert routes.choose(FIXTURES / "conversion", Catalogue.from_csv(FULL)) == []


def test_a_missing_table_or_column_gives_way_to_the_first_alternative_that_fits(tmp_path):
    conversion = _conversion(tmp_path / "conversion")
    catalogue = Catalogue.from_csv(_without(_without(FULL, table="THEATRE_CASE"), column=("PERSON_MASTER", "TEST_PERSON_FLAG")))
    chosen = {c["step"]: c for c in routes.choose(conversion, catalogue)}
    assert chosen["visit_detail_through_case.sql"]["chosen"] == "visit_detail.sql"
    assert chosen["visit_detail_through_case.sql"]["missing"] == ["THEATRE_CASE"]
    assert chosen["person.sql"]["chosen"] == "person_all_people.sql"
    assert chosen["person.sql"]["missing"] == ["PERSON_MASTER.TEST_PERSON_FLAG"] and chosen["person.sql"]["effect"] == EFFECT
    # A step without an alternative that fits stays as written.
    assert "procedure_occurrence_surgery.sql" not in chosen
    routes.apply(conversion, list(chosen.values()))
    steps = {s["table"] + ":" + s["file"] for s in json.loads((conversion / "conversion.json").read_text())}
    assert "visit_detail:visit_detail.sql" in steps and "person:person_all_people.sql" in steps
    assert routes.chosen(conversion)[0]["step"] in chosen


def test_the_checklist_says_which_route_it_takes_and_asks_nothing_about_an_absent_table(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "catalogue.csv").write_text(_without(_without(FULL, table="THEATRE_CASE"), column=("PERSON_MASTER", "TEST_PERSON_FLAG")))
    shutil.copy(FIXTURES / "invented-site-rules.json", state / "site-rules.json")
    _conversion(state / "conversion")
    (state / "targets").mkdir()
    shutil.copy(FIXTURES / "targets" / "neonatal_low_mean_pressure.sql", state / "targets")
    outputs, facts = boundary.produce(state, FIXTURES / "requests")
    name = "neonatal_low_mean_pressure"
    rows = {r["question_id"]: r for r in csv.DictReader(io.StringIO(outputs[f"targets/{name}/checklist.csv"]))}
    route = rows["route-person.sql"]
    assert route["status"] == "partly" and route["phase"] == "source" and EFFECT in route["evidence_in_hand"]
    assert ("Because PERSON_MASTER.TEST_PERSON_FLAG is not visible to this login, Schemalyser takes the patients from "
            "PERSON_MASTER for now.") in route["evidence_in_hand"] and ".sql" not in route["evidence_in_hand"]
    readiness = outputs[f"targets/{name}/readiness.txt"]
    assert "Because PERSON_MASTER.TEST_PERSON_FLAG is not visible to this login" in readiness
    target = facts["targets"][0]
    assert any("takes the patients from PERSON_MASTER" in sentence for sentence in target["routes"])
    # No item of the first phase asks about the table or the column that the catalogue does not hold.
    for row in rows.values():
        if row["phase"] == "source" and not row["question_id"].startswith("route-"):
            assert "TEST_PERSON_FLAG" not in row["question"] and "THEATRE_CASE" not in row["question"], row["question_id"]
    # Where a table that the catalogue does not hold is still read by a step that has no alternative, its item asks for
    # a decision about the step, not for evidence that the table exists.
    absent = [r for r in rows.values() if r["question_id"] == "table-THEATRE_CASE"]
    for row in absent:
        assert "which the catalogue does not hold" in row["question"] and row["mechanism"] == "a decision"
    # The draft follows the route.
    assert "TEST_PERSON_FLAG" not in (target["draft"] or "")


def test_the_invented_conversion_with_its_full_catalogue_is_unchanged(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "catalogue.csv").write_text(FULL)
    shutil.copytree(FIXTURES / "conversion", state / "conversion")
    (state / "targets").mkdir()
    shutil.copy(FIXTURES / "targets" / "neonatal_low_mean_pressure.sql", state / "targets")
    outputs, facts = boundary.produce(state, FIXTURES / "requests")
    assert not facts["targets"][0]["routes"]
    assert "route-" not in outputs["targets/neonatal_low_mean_pressure/checklist.csv"]



def test_an_alternative_with_its_own_route_record_brings_it_and_the_step_keeps_its_own(tmp_path):
    conversion = _conversion(tmp_path / "conversion")
    steps = json.loads((conversion / "conversion.json").read_text())
    person = next(s for s in steps if s["file"] == "person.sql")
    own = {k: person[k] for k in ("route", "reference", "reason", "review")}
    record = {"route": "direct", "reference": "an invented reference", "reason": "The test needs a route of its own.",
              "review": {"by": "a tester", "on": "2026-10-09"}}
    person["alternatives"] = [{"file": "person_all_people.sql", "effect": EFFECT, **record}]
    (conversion / "conversion.json").write_text(json.dumps(steps))
    catalogue = Catalogue.from_csv(_without(FULL, column=("PERSON_MASTER", "TEST_PERSON_FLAG")))
    routes.apply(conversion, routes.choose(conversion, catalogue))
    after = next(s for s in json.loads((conversion / "conversion.json").read_text()) if s["table"] == "person")
    assert after["file"] == "person_all_people.sql" and after["reference"] == "an invented reference"
    assert after["alternatives"][0] == {"file": "person.sql", **own}


def test_an_alternative_over_the_roles_is_never_chosen_by_the_catalogue(tmp_path):
    conversion = tmp_path / "conversion"
    shutil.copytree(FIXTURES / "conversion", conversion)
    catalogue = Catalogue.from_csv(_without(FULL, table="DRUG_DEF"))
    chosen = routes.choose(conversion, catalogue)
    assert "drug_exposure_infusion.sql" not in {c["step"] for c in chosen}
