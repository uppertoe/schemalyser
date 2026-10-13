"""The acceptance test of a hospital schema's migration: a schema made and validated under an older contract is
opened under a newer one that added two fields to the techniques part, a regional block's theatre case and the block's
note. The case is proposed from the dictionary the file holds with no query sent to the hospital's database, the note,
which the invented hospital does not record, is reported apart as needing evidence, every validated answer stays, and
the new version keeps its lineage. The same file opens the same way through the browser's bridge."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_describe import DATE, fresh  # noqa: E402

from schemalyser import browser, describe, rolemap

VALIDATED = ["role_patient rows", "role_patient.patient_key", "role_patient.birth_date", "role_anaesthetic rows",
             "role_anaesthetic.anaesthetic_key", "role_anaesthetic.patient_key", "role_technique rows",
             "role_technique.technique_key", "role_technique.anaesthetic_key"]


def newer_contract():
    """The contract as a later version of the static page carries it: the techniques part gains the theatre case of a
    block and the text of the block's procedure note."""
    model = json.loads(json.dumps(rolemap.contract()))
    technique = next(v for v in model["views"] if v["name"] == "role_technique")
    technique["columns"].append({"name": "theatre_case", "title": "the theatre case", "type": "key", "needed_by": [],
                                 "meaning": "The surgical case during which the block was done.",
                                 "holds": "The anaesthetic record, which names its surgical case.",
                                 "look_for": ["surgical case", "case"]})
    technique["columns"].append({"name": "block_note", "title": "the block's note", "type": "text", "needed_by": [],
                                 "meaning": "The text of the procedure note written for a regional block.",
                                 "holds": "A procedure note linked to the anaesthetic.",
                                 "look_for": ["procedure note", "block note", "note text"]})
    return model


def queries_offered(sitting):
    return sum(1 for e in sitting.log.entries() if e["kind"] == "query offered")


def answered(data):
    """The binding and the person's answer of every validated column, as the test expects them to be kept."""
    found = {}
    for about in VALIDATED:
        name, _, column = about.partition(".")
        item = data["roles"][name.split(" ")[0]]["rows"] if about.endswith(" rows") else data["roles"][name]["columns"][column]
        found[about] = (json.dumps(item.get("binding"), sort_keys=True), item.get("confirmation", {}).get("answer"))
    return found


def test_a_validated_schema_opens_under_a_newer_contract_proposes_the_added_field_without_a_query_and_keeps_every_answer(monkeypatch):
    # Version 1, made and validated under the older contract.
    s = fresh()
    for about in VALIDATED:
        s.confirm(about, "yes", date=DATE, actor="Dr A Clinician")
    kept = answered(s.data)
    assert all(answer == "yes" for _, answer in kept.values())
    v1 = s.save(date=DATE)
    v1_id = json.loads(v1["settings.json"])["schema_id"]
    v1_zip = s.save_zip(DATE)
    queries_in_v1 = queries_offered(s)

    # The static page moves to the newer contract, and the saved file is opened under it.
    newer = newer_contract()
    monkeypatch.setattr(rolemap, "contract", lambda: newer)
    again = describe.Describe()
    again.version = "test"
    restored = again.restore(v1)
    assert restored["map"] and restored["contract_changed"] == ["role_technique"]
    report = restored["proposed_for_contract"]
    assert report["columns_added"] == ["role_technique.theatre_case", "role_technique.block_note"]
    assert report["proposed"] == ["role_technique.theatre_case"] and report["unproposed"] == ["role_technique.block_note"]
    technique = again.data["roles"]["role_technique"]["columns"]
    assert technique["theatre_case"]["binding"]["table"] == "ANAES_RECORD" and technique["theatre_case"]["binding"]["column"] == "CASE_KEY"
    assert technique["theatre_case"]["status"] == "proposed" and "confirmation" not in technique["theatre_case"]
    assert technique["block_note"]["binding"] is None and "nothing" in technique["block_note"]["from"]
    # No query was sent to the hospital's database: the proposal rests on the dictionary the file holds.
    assert queries_offered(again) == queries_in_v1 and not again.queries
    # Every validated answer is as it was recorded.
    assert answered(again.data) == kept
    # What changed, what was proposed and what needs evidence are reported apart, in sentences a person reads.
    assert len(restored["contract_report"]) == 3
    assert "Techniques" in restored["contract_report"][0] and "no query sent" in restored["contract_report"][1]
    assert "nothing that fits" in restored["contract_report"][2] and "note" in restored["contract_report"][2]
    assert again.log.latest("proposal extended")["payload"]["unproposed"] == ["role_technique.block_note"]
    # The evidence recorded for the changed part no longer counts until it is checked again.
    assert any("contract" in str(found.get("reasons")) for found in again.stale_evidence()
               if str(found.get("subject", "")).startswith("role_technique"))

    # The clinician answers the two fields, and the new version keeps its lineage and every earlier answer.
    again.confirm("role_technique.theatre_case", "yes", date=DATE, actor="Dr A Clinician")
    again.confirm("role_technique.block_note", "no", note="The hospital writes no procedure note for a block", date=DATE, actor="Dr A Clinician")
    v2 = again.save(date=DATE)
    settings = json.loads(v2["settings.json"])
    assert settings["parent_id"] == v1_id and v1_id in settings["lineage"] and settings["schema_id"] != v1_id
    assert answered(json.loads(v2["map/map.json"])) == kept
    saved = json.loads(v2["map/map.json"])["roles"]["role_technique"]["columns"]
    assert saved["theatre_case"]["confirmation"]["answer"] == "yes" and saved["block_note"]["confirmation"]["answer"] == "no"

    # Opened again under the same contract, nothing is proposed and nothing is reported.
    third = describe.Describe()
    third.version = "test"
    opened = third.restore(v2)
    assert opened["contract_changed"] == [] and opened["proposed_for_contract"]["proposed"] == [] and opened["contract_report"] == []
    assert third.log.latest("proposal extended") == again.log.latest("proposal extended")

    # The browser's bridge opens version 1 under the newer contract the same way.
    browser.describe_begin("test")
    reply = json.loads(browser.describe_schema_open(v1_zip))
    assert reply["ok"]
    bridged = reply["restored"]
    assert bridged["proposed_for_contract"]["proposed"] == ["role_technique.theatre_case"]
    assert bridged["proposed_for_contract"]["unproposed"] == ["role_technique.block_note"]
    assert bridged["contract_report"] == restored["contract_report"]
