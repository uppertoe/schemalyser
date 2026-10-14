"""The check of the contract's shape: the contract that ships passes with its roles document, and each rule names
what breaks it."""
import copy
import json

from schemalyser import contractcheck, rolemap


def shipped():
    return json.loads(json.dumps(rolemap.contract()))


def roles_text():
    return contractcheck.ROLES.read_text(encoding="utf-8")


def test_the_contract_that_ships_keeps_every_rule_and_its_roles_document_is_in_step():
    assert contractcheck.check_contract(shipped(), roles_text()) == []


def test_the_command_passes_the_shipped_contract(capsys):
    assert contractcheck.main([]) == 0
    assert "keeps every rule" in capsys.readouterr().out


def broken(change):
    model = shipped()
    change(model)
    return contractcheck.check_contract(model)


def view(model, name):
    return next(v for v in model["views"] if v["name"] == name)


def test_a_column_without_words_to_look_for_or_with_a_generic_word_is_named():
    found = broken(lambda m: view(m, "role_patient")["columns"].append(
        {"name": "given_name", "title": "the given name", "type": "text", "meaning": "The given name.", "holds": "The patient record.", "look_for": []}))
    assert found == ["The column given_name of role_patient gives no words to look for, so no proposer can find it."]
    found = broken(lambda m: view(m, "role_patient")["columns"].append(
        {"name": "given_name", "title": "the given name", "type": "text", "meaning": "The given name.", "holds": "The patient record.", "look_for": ["name"]}))
    assert found == ["The column given_name of role_patient looks for name, which alone would match half of any dictionary."]


def test_a_kind_column_names_a_vocabulary_that_exists_and_a_vocabulary_has_kinds():
    found = broken(lambda m: view(m, "role_technique")["columns"].append(
        {"name": "side", "title": "the side", "type": "kind", "vocabulary": "sides", "meaning": "The side.", "holds": "The block form.", "look_for": ["side of the block"]}))
    assert found == ["The column side of role_technique names the vocabulary sides, which the contract does not have."]
    found = broken(lambda m: m["vocabularies"].update({"sides": []}))
    assert found == ["The vocabulary sides has no kinds."]
    found = broken(lambda m: m["vocabularies"].update({"sides": [{"kind": "left", "meaning": "Left."}]}))
    assert found == ["The vocabulary sides is named by no column of any part."]


def test_a_link_points_at_the_key_of_a_part_that_exists():
    found = broken(lambda m: view(m, "role_technique")["links"].append({"column": "anaesthetic_key", "to": "role_case.case_key"}))
    assert found == ["The part role_technique links to role_case.case_key, which no part has."]
    found = broken(lambda m: view(m, "role_technique")["links"].append({"column": "anaesthetic_key", "to": "role_anaesthetic.start_time"}))
    assert found == ["The part role_technique links to role_anaesthetic.start_time, which is not the key of role_anaesthetic."]


def test_a_part_needs_a_name_a_status_a_key_and_words_to_look_for():
    def add(m):
        m["views"].append({"name": "role_case", "title": "Booked cases", "status": "sketch", "description": "Cases.",
                           "one_row_per": "case", "holds": "The booking.", "look_for": [], "key": ["case_key"], "links": [],
                           "columns": [{"name": "case_key", "title": "the case", "type": "key", "meaning": "The case.", "holds": "The booking.", "look_for": ["id"]}]})
    found = broken(add)
    assert "The part role_case has a status that is not one of contract, draft." in found
    assert "The part role_case gives no words to look for, so no proposer can find it." in found
    assert "The column case_key of role_case looks for id, which alone would match half of any dictionary." in found


def test_the_roles_document_is_checked_against_the_contract():
    model = shipped()
    text = roles_text()
    assert contractcheck.check_roles(model, text.replace("### role_note:", "### role_notes:")) == [
        "The roles document has no section for role_note.",
        "The roles document has a section for role_notes, which the contract does not have."]
    model["vocabularies"]["laterality"].append({"kind": "unknown", "meaning": "Not given."})
    assert contractcheck.check_roles(model, text) == [
        "The roles document lists the kinds of laterality as left, right, bilateral, midline, and the contract as left, right, bilateral, midline, unknown."]


def test_the_contract_has_no_required_flag_and_admission_decides_each_gate():
    from schemalyser import contract as model_reader
    model = shipped()
    assert all("required" not in v for v in model["views"])
    found = broken(lambda m: view(m, "role_patient").update({"required": True}))
    assert found == ["The part role_patient carries required, which the contract no longer uses: its status says what every gate may do with it."]
    for purpose in model_reader.PURPOSES:
        assert model_reader.admission("role_patient", purpose, model) == model_reader.ADMITTED
    assert model_reader.admission("role_fluid", "describe", model) == model_reader.ADMITTED
    for purpose in ("question", "compile", "conversion"):
        assert model_reader.admission("role_fluid", purpose, model) == model_reader.REFUSED
    view(model, "role_fluid")["status"] = "rehearsed"
    assert model_reader.admission("role_fluid", "question", model) == model_reader.CEILING
    assert model_reader.admission("role_nothing", "question", model) == model_reader.REFUSED
    import pytest
    with pytest.raises(ValueError):
        model_reader.admission("role_patient", "publishing", model)
    # The compiler reaches exactly the parts the contract admits for compilation.
    from schemalyser import compiler
    admitted = {v["name"] for v in model["views"] if model_reader.admission(v["name"], "compile", model) == model_reader.ADMITTED}
    assert admitted == set(compiler.REACHED)
