"""The question package's reader: the hand-written package for the neonatal audit is accepted, each rule names what
breaks it in a sentence, and the parts are exactly the parts the SQL reads."""
import json
from pathlib import Path

import pytest

from schemalyser import contract as model_reader
from schemalyser import question, rolemap

FIXTURE = Path(__file__).resolve().parent.parent.parent / "fixtures" / "packages" / "neonatal-low-mean-pressure.question.json"
AUDIT = Path(question.__file__).parent / "rolemodel" / "neonatal_low_mean_pressure.sql"


def package():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def failed(change, model=None):
    held = package()
    change(held)
    found = question.check(json.dumps(held), model)
    return found["failed"], question.sentences(found)


def test_the_hand_written_package_for_the_neonatal_audit_is_accepted_and_its_sql_is_the_audit_s():
    found = question.check(FIXTURE.read_text(encoding="utf-8"))
    assert found["failed"] == [] and all(r["passed"] for r in found["rules"])
    assert question.read(FIXTURE)["title"].startswith("Minutes of low mean arterial pressure")
    assert package()["question_sql"] == AUDIT.read_text(encoding="utf-8")
    assert "status" not in package()
    assert [p["name"] for p in package()["parts"]] == question.parts_read(package()["question_sql"])[0]
    assert question.validate(package(), question.schema()) == []


def test_the_command_prints_every_failed_rule_and_accepts_the_fixture(tmp_path, capsys):
    assert question.main(["check", str(FIXTURE)]) == 0
    assert "keeps every rule of schemalyser-question/1" in capsys.readouterr().out
    broken = tmp_path / "broken.json"
    held = package()
    held["status"] = "drafted"
    held["parts"] = held["parts"][:2]
    broken.write_text(json.dumps(held), encoding="utf-8")
    assert question.main(["check", str(broken)]) == 1
    out = capsys.readouterr().out
    assert "The package breaks the rule that the package has the fields of schemalyser-question/1 and no other: status: is not a field of the package." in out
    assert "keeps every rule" not in out
    broken.write_text("not json", encoding="utf-8")
    assert question.main(["check", str(broken)]) == 1
    assert "holds one JSON object" in capsys.readouterr().out
    assert question.main(["check"]) == 2
    assert question.main(["check", str(tmp_path / "missing.json")]) == 2


def test_each_rule_of_the_shape_names_what_breaks_it():
    assert question.check("[1, 2]")["failed"] == ["json"]
    assert question.check("{")["failed"] == ["json"]
    ids, says = failed(lambda p: p.pop("title"))
    assert ids == ["schema"] and says == ["The package breaks the rule that the package has the fields of schemalyser-question/1 and no other: title: is missing."]
    ids, says = failed(lambda p: p.update({"format": "schemalyser-question/2"}))
    assert ids == ["format"] and says == ['The package breaks the rule that the format is schemalyser-question/1: format: is not "schemalyser-question/1".']
    ids, says = failed(lambda p: p["decisions"][0].update({"date": "14 October 2026"}))
    assert ids == ["schema"] and "decisions[0].date: does not match" in says[0]
    ids, says = failed(lambda p: p["decisions"][0].update({"date": "2026-13-40"}))
    assert ids == ["dates"] and says == ["The package breaks the rule that every date and time reads: decision 1 is dated 2026-13-40, which is not a date written as YYYY-MM-DD."]
    ids, says = failed(lambda p: p["produced"].update({"at": "yesterday"}))
    assert ids == ["dates"] and "produced.at is yesterday" in says[0]
    ids, says = failed(lambda p: p["parts"][0].update({"admission": "allowed"}))
    assert ids == ["schema"] and 'parts[0].admission: is not one of "admitted", "admitted with a ceiling", "refused"' in says[0]
    ids, says = failed(lambda p: p.update({"title": "Two\nlines"}))
    assert ids == ["schema"]
    ids, says = failed(lambda p: p["statement"].update({"cohort": ""}))
    assert ids == ["schema"] and says == ["The package breaks the rule that the package has the fields of schemalyser-question/1 and no other: statement.cohort: is empty."]
    ids, says = failed(lambda p: p["parts"][0].update({"version": True}))
    assert ids == ["schema"] and "parts[0].version: is not an integer" in says[0]
    ids, says = failed(lambda p: p["decisions"][0].update({"value": 28, "unit": 3}))
    assert ids == ["schema"] and "decisions[0].unit: is not a string or null" in says[0]


def test_the_parts_are_exactly_the_parts_the_sql_reads_at_the_contract_s_versions_and_admissions():
    ids, says = failed(lambda p: p["parts"].pop(0))
    assert ids == ["parts"] and says == ["The package breaks the rule that the parts are exactly the parts the SQL reads: the SQL reads role_anaesthetic, which the parts do not list."]
    ids, says = failed(lambda p: p["parts"].append({"name": "role_lab", "version": 1, "admission": "refused"}))
    assert ids == ["parts"] and says == ["The package breaks the rule that the parts are exactly the parts the SQL reads: the parts list role_lab, which the SQL does not read."]
    ids, says = failed(lambda p: p["parts"].append({"name": "role_booking", "version": 1, "admission": "admitted"}))
    assert ids == ["parts"] and "role_booking is not a part of the contract" in says[0]
    ids, says = failed(lambda p: p["parts"].append(dict(p["parts"][0])))
    assert ids == ["parts"] and "role_anaesthetic is listed more than once" in says[0]
    ids, says = failed(lambda p: p["parts"][0].update({"version": 2}))
    assert ids == ["part_version"] and says == ["The package breaks the rule that each part is at its version in the contract: role_anaesthetic is at version 1 in the contract, and the package names version 2."]
    ids, says = failed(lambda p: p["parts"][0].update({"admission": "refused"}))
    assert ids == ["admission"] and says == ["The package breaks the rule that each part's admission is what the contract gives it for a question: the contract gives role_anaesthetic the admission 'admitted' for a question, and the package says 'refused'."]
    ids, says = failed(lambda p: p.update({"question_sql": "SELECT * FROM role_patient WHERE"}))
    assert ids == ["parts"] and "cannot be read as one statement" in says[0]
    ids, says = failed(lambda p: p.update({"contract_version": "0.9"}))
    assert ids == ["contract_version"] and says == ["The package breaks the rule that the package was written against the contract that Schemalyser holds: the package names contract version 0.9, and Schemalyser holds 1.1."]
    # The model may draft against a draft part, marked refused as the contract gives it: the package is well formed.
    held = package()
    held["question_sql"] = "SELECT l.patient_key, COUNT(*) AS n FROM role_lab l JOIN map_lab_concept m ON m.local_key = l.test GROUP BY l.patient_key"
    held["parts"] = [{"name": "map_lab_concept", "version": 1, "admission": "admitted"}, {"name": "role_lab", "version": 1, "admission": "refused"}]
    found = question.check(json.dumps(held))
    assert found["failed"] == [], question.sentences(found)
    assert question.parts_read(held["question_sql"])[0] == ["map_lab_concept", "role_lab"]
    # A rehearsed part is admitted with a ceiling, and the package records that.
    ceiling = package()
    ceiling["question_sql"] = "SELECT f.anaesthetic_key, SUM(f.volume_ml) AS ml FROM role_fluid f GROUP BY f.anaesthetic_key"
    ceiling["parts"] = [{"name": "role_fluid", "version": 1, "admission": "admitted with a ceiling"}]
    assert question.check(json.dumps(ceiling))["failed"] == []
    # Under a contract that promotes the part, the recorded admission is out of date and the rule says so.
    promoted = json.loads(json.dumps(rolemap.contract()))
    next(v for v in promoted["views"] if v["name"] == "role_lab")["status"] = "contract"
    found = question.check(json.dumps(held), promoted)
    assert found["failed"] == ["admission"]
    # A common table expression is not a part.
    assert question.parts_read("WITH c AS (SELECT * FROM role_patient) SELECT * FROM c")[0] == ["role_patient"]
    assert question.parts_read("")[0] == []


PLANTED = json.loads((Path(question.__file__).parent / "rolemodel" / "capabilities" / "planted.json").read_text(encoding="utf-8"))
COMPOSED = {"name": "hypoxaemia_burden", "version": 1, "parameters": PLANTED["hypoxaemia_burden"]["parameters"]}
OVER_CAPABILITY = ("-- The hypoxaemia burden of each anaesthetic, from the catalogue.\n"
                   "SELECT h.anaesthetic_key, h.age_days, h.threshold, h.readings, h.minutes_beyond FROM hypoxaemia_burden h\n")


def composed_package(sql=OVER_CAPABILITY, capabilities=(COMPOSED,)):
    held = package()
    held["capabilities"] = list(capabilities)
    held["question_sql"] = sql
    held["parts"] = [{"name": n, "version": 1, "admission": "admitted"} for n in ("role_anaesthetic", "role_patient", "role_reading")]
    return held


def test_capabilities_are_in_the_catalogue_at_their_version_with_their_parameters():
    assert question.check(json.dumps(composed_package()))["failed"] == []
    ids, says = failed(lambda p: p["capabilities"].append({**COMPOSED, "name": "minutes_above_everything"}))
    assert ids == ["capabilities"] and says[0].startswith("The package breaks the rule that each capability is in the catalogue at its version, with its parameters, and its SQL can be filled: minutes_above_everything is not a capability of the catalogue.")
    ids, says = failed(lambda p: p["capabilities"].append({**COMPOSED, "version": 2}))
    assert ids == ["capabilities"] and "is at version 1 in the catalogue, and the package names version 2" in says[0]
    ids, says = failed(lambda p: p["capabilities"].append({**COMPOSED, "parameters": {"kinds": [["spo2", 1]], "limit": 40}}))
    assert ids == ["capabilities"]
    assert "hypoxaemia_burden has no parameter named limit" in says[0]
    assert "hypoxaemia_burden is given no value for direction, reading_stands_minutes, threshold_by_age_band, window_from_minutes, window_until_minutes" in says[1]
    # A capability the catalogue declares without SQL cannot be composed, and a value of the wrong type is refused.
    ids, says = failed(lambda p: p["capabilities"].append({"name": "transfusion", "version": 1, "parameters": {}}))
    assert ids == ["capabilities"] and "transfusion: the catalogue entry names no SQL" in says[0]
    ids, says = failed(lambda p: p["capabilities"].append({**COMPOSED, "parameters": {**COMPOSED["parameters"], "direction": "sideways"}}))
    assert ids == ["capabilities"] and "one of below, above" in says[0]


def test_a_package_composes_a_capability_as_a_common_table_expression_that_the_policy_accepts_and_gives_the_held_out_answer():
    from schemalyser import rolepolicy, roleshadow
    from schemalyser.translate import to_duckdb
    held = composed_package()
    found = question.check(json.dumps(held))
    assert found["failed"] == [], question.sentences(found)
    expanded = found["sql"]
    assert expanded == question.expand(held)
    # The capability is a common table expression named after it, its own steps carried under its name, and the
    # question's leading comment is kept.
    assert expanded.startswith("-- The hypoxaemia burden of each anaesthetic, from the catalogue.\n")
    assert "hypoxaemia_burden AS (" in expanded and "hypoxaemia_burden__anaesthetic AS (" in expanded
    assert "FROM hypoxaemia_burden AS h" in expanded or "FROM hypoxaemia_burden h" in expanded
    assert question.parts_read(expanded)[0] == ["role_anaesthetic", "role_patient", "role_reading"]
    assert question.parts_read(held["question_sql"], composed=["hypoxaemia_burden"])[0] == []
    checked = rolepolicy.check(expanded, ["hypoxaemia_burden"])
    assert checked["failed"] == [], checked
    # On the planted rows, the composed question gives the capability's held-out answer.
    expected = json.loads((Path(question.__file__).resolve().parents[2] / "fixtures" / "held-out" / "capabilities" / "hypoxaemia_burden" / "expected.json").read_text())
    con = roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=PLANTED["hypoxaemia_burden"]["rows"])
    statements = to_duckdb(expanded)
    assert len(statements) == 1
    cursor = con.execute(statements[0])
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    assert columns == expected["columns"]

    def comparable(v):
        return round(float(v), 6) if isinstance(v, (int, float)) else v
    assert sorted([comparable(v) for v in row] for row in rows) == sorted([comparable(v) for v in row] for row in expected["rows"])
    # The parts a composed package reads are the capability's, and a package that lists them differently is refused.
    short = composed_package()
    short["parts"] = short["parts"][:1]
    assert question.check(json.dumps(short))["failed"] == ["parts"]
    # A question's own common table expressions keep their place after the capability's.
    own = composed_package("WITH kept AS (SELECT h.anaesthetic_key, h.minutes_beyond FROM hypoxaemia_burden h WHERE h.minutes_beyond IS NOT NULL)\n"
                           "SELECT COUNT(*) AS anaesthetics, SUM(k.minutes_beyond) AS minutes FROM kept k\n")
    found = question.check(json.dumps(own))
    assert found["failed"] == [] and "kept AS (" in found["sql"] and rolepolicy.check(found["sql"])["failed"] == []
    # The build reads the composed SQL from a package file.
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "composed.question.json"
        path.write_text(json.dumps(own), encoding="utf-8")
        sql, decisions, read = question.sql_and_decisions(path)
        assert sql == found["sql"] and len(decisions["decisions"]) == 9 and read["title"] == own["title"]


def test_the_package_the_service_writes_over_a_capability_is_accepted():
    from schemalyser import rolepolicy
    fixture = FIXTURE.parent / "ask-fake-backend-capability.question.json"
    found = question.check(fixture.read_text(encoding="utf-8"))
    assert found["failed"] == [], question.sentences(found)
    held = found["package"]
    assert [c["name"] for c in held["capabilities"]] == ["hypoxaemia_burden"]
    assert [p["name"] for p in held["parts"]] == ["role_anaesthetic", "role_patient", "role_reading"]
    assert "PERCENTILE_CONT" in held["question_sql"]
    assert rolepolicy.check(found["sql"], ["hypoxaemia_burden"])["failed"] == []


def test_a_proposal_keeps_the_contract_s_shape_or_the_checker_s_sentence_names_what_it_lacks():
    column = {"kind": "column", "part": "role_technique", "because": "The question needs the block's theatre case.",
              "shape": {"name": "theatre_case", "title": "the theatre case", "type": "key", "meaning": "The surgical case during which the block was done.",
                        "holds": "The anaesthetic record, which names its surgical case.", "look_for": ["surgical case"]}}
    held = package()
    held["proposals"] = [column]
    assert question.check(json.dumps(held))["failed"] == []
    ids, says = failed(lambda p: p["proposals"].append({**column, "shape": {**column["shape"], "look_for": []}}))
    assert ids == ["proposals"] and says == ["The package breaks the rule that each proposal keeps the contract's shape: proposal 1: The column theatre_case of role_technique gives no words to look for, so no proposer can find it."]
    ids, says = failed(lambda p: p["proposals"].append({**column, "part": "role_booking"}))
    assert ids == ["proposals"] and "a column would join role_booking, which the contract does not have" in says[0]
    ids, says = failed(lambda p: p["proposals"].append({**column, "shape": {**column["shape"], "name": "technique_key"}}))
    assert ids == ["proposals"] and "role_technique already has a column named technique_key" in says[0]
    part = {"kind": "part", "part": "role_booking", "because": "The question needs the booked cases.",
            "shape": {"name": "role_booking", "title": "Booked cases", "description": "One row for each booked case.", "one_row_per": "booked case",
                      "holds": "The theatre booking.", "look_for": ["theatre booking", "booked case"], "key": ["booking_key"], "links": [],
                      "columns": [{"name": "booking_key", "title": "the booking", "type": "key", "meaning": "The booking's own key.",
                                   "holds": "The booking table's key.", "look_for": ["booking identifier"]}]}}
    held = package()
    held["proposals"] = [part]
    assert question.check(json.dumps(held))["failed"] == []
    ids, says = failed(lambda p: p["proposals"].append({**part, "part": "role_patient", "shape": {**part["shape"], "name": "role_patient"}}))
    assert ids == ["proposals"] and "the contract already has a part named role_patient" in says[0]
    ids, says = failed(lambda p: p["proposals"].append({**part, "shape": {**part["shape"], "name": "role_bookings"}}))
    assert ids == ["proposals"] and "the shape is named role_bookings, and the proposal role_booking" in says[0]
    ids, says = failed(lambda p: p["proposals"].append({**part, "kind": "table"}))
    assert ids == ["schema"] and 'proposals[0].kind: is not one of "column", "part"' in says[0]


def test_the_examinable_object_names_its_oracle_or_says_why_it_has_none():
    ids, says = failed(lambda p: p["examinable"].pop("because"))
    assert ids == ["examinable"] and says == ["The package breaks the rule that the examinable object names its oracle or says why it has none: the examinable says the oracle is none and does not say why."]
    ids, says = failed(lambda p: p.update({"examinable": {"cohort": {"grain": "anaesthetic"}}}))
    assert ids == ["examinable"] and "has no outcome" in says[0] and "has no result" in says[1]
    structured = {
        "cohort": {"grain": "anaesthetic", "age_days": [0, 27], "exclude": ["test_patient", "no_start", "stop_before_start"]},
        "outcome": {"part": "role_reading", "kinds": ["map_arterial", "map_cuff"], "flags": {"accepted": 1},
                    "window": {"from": "start", "to": "stop"},
                    "measure": {"name": "minutes_beyond_threshold", "version": 1,
                                "parameters": {"kind": "map_arterial", "direction": "below", "threshold": 40, "window": "anaesthetic"}},
                    "duplicates": "lowest", "absent": "null"},
        "result": {"statistic": "count", "over": "anaesthetic", "by": [{"band": "under 5", "minutes": [0, 5]}], "columns": ["band", "anaesthetics"]}}
    held = package()
    held["examinable"] = structured
    assert question.check(json.dumps(held))["failed"] == []
    ids, says = failed(lambda p: p.update({"examinable": {**structured, "outcome": {**structured["outcome"], "measure": "average"}}}))
    assert ids == ["schema"] and "examinable.outcome.measure: is none of the shapes it may have" in says[0]
    ids, says = failed(lambda p: p.update({"examinable": {**structured, "outcome": {**structured["outcome"], "measure": {"name": "nothing", "version": 1, "parameters": {}}}}}))
    assert ids == ["examinable"] and "nothing is not a capability of the catalogue" in says[0]
    ids, says = failed(lambda p: p.update({"examinable": {**structured, "outcome": {**structured["outcome"], "part": "role_booking"}}}))
    assert ids == ["examinable"] and "the outcome's part role_booking is not a part of the contract" in says[0]
    ids, says = failed(lambda p: p.update({"examinable": {**structured, "cohort": {"grain": "episode"}}}))
    assert ids == ["schema"]
    held = package()
    del held["examinable"]
    assert question.check(json.dumps(held))["failed"] == []


def test_the_validator_covers_the_subset_the_schema_is_written_in():
    assert question.validate(1, {"type": "integer"}) == []
    assert question.validate(True, {"type": "integer"}) == [("the package", "is not an integer")]
    assert question.validate(1.5, {"type": "number", "maximum": 1}) == [("", "is above 1")]
    assert question.validate("ab", {"type": "string", "minLength": 3}) == [("", "is shorter than 3 characters")]
    assert question.validate([1, 2, 3], {"type": "array", "maxItems": 2}) == [("", "holds more than 2 items")]
    assert question.validate({"a": 1}, {"type": "object", "additionalProperties": {"type": "string"}}) == [("a", "is not a string")]
    assert question.validate({"a": 1}, {"type": "object", "properties": {}, "additionalProperties": False}) == [("a", "is not a field of the package")]
    assert question.validate(None, {"type": ["string", "null"]}) == []
    assert question.validate("x", {"anyOf": [{"const": "y"}, {"type": "integer"}]}) == [("the package", "is none of the shapes it may have")]
    # The schema itself stays inside the subset both readers implement.
    allowed = {"$schema", "title", "description", "type", "enum", "const", "properties", "required", "additionalProperties",
               "items", "minItems", "maxItems", "minLength", "pattern", "minimum", "maximum", "anyOf", "$ref", "$defs"}

    def keywords(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in ("properties", "$defs"):
                    yield key
                    yield from keywords(value)
                else:
                    for child in value.values():
                        yield from keywords(child)
        elif isinstance(node, list):
            for item in node:
                yield from keywords(item)
    assert set(keywords(question.schema())) <= allowed


def test_a_mapping_view_is_admitted_for_a_question_as_a_contract_part_is():
    assert model_reader.admission("map_drug_concept", "question") == model_reader.ADMITTED
    assert model_reader.admission("map_drug_concept", "compile") == model_reader.ADMITTED


def test_the_package_the_service_s_fake_backend_writes_is_accepted():
    """The Go service's fake backend produces this package from its settled reply, committed as a fixture by the Go
    test; the Python reader accepts it, with its parts as the service worked them out and the draft part refused."""
    fixture = FIXTURE.parent / "ask-fake-backend.question.json"
    found = question.check(fixture.read_text(encoding="utf-8"))
    assert found["failed"] == [], question.sentences(found)
    held = found["package"]
    assert held["produced"]["model"] == "fake" and "status" not in held and "hash" not in held
    assert [(p["name"], p["admission"]) for p in held["parts"]] == [
        ("role_anaesthetic", "admitted"), ("role_fluid", "admitted with a ceiling"), ("role_patient", "admitted")]
    assert all(p["version"] == model_reader.part_versions()[p["name"]] for p in held["parts"])
    assert held["decisions"][0]["value"] == 28 and held["decisions"][0]["unit"] == "days" and held["decisions"][1].get("unit") is None
    assert held["decisions"][0]["by"] == "dr@example.test" and held["decisions"][0]["date"] == "2026-10-14"
