"""The check of the contract's shape: the contract that ships passes with its roles document, and each rule names
what breaks it."""
import copy
import json
import shutil

import pytest

from schemalyser import contract as model_reader
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


def test_the_shipped_contract_is_at_the_versions_its_lock_file_holds():
    lock = model_reader.read_lock()
    assert lock == model_reader.lock(shipped())
    assert contractcheck.check_contract(shipped(), roles_text(), lock) == []
    assert all(isinstance(v, int) and v >= 1 for v in model_reader.part_versions().values())


def test_a_part_s_words_leave_its_hash_alone_and_a_changed_definition_needs_a_raised_version():
    before = rolemap.part_hashes()
    lock = model_reader.read_lock()
    worded = shipped()
    patient = view(worded, "role_patient")
    patient["description"] += " Reworded."
    patient["title"] = "The patients"
    patient["look_for"].append("person master")
    patient["columns"][1]["meaning"] += " Reworded."
    patient["columns"][1]["look_for"].append("born on")
    patient["columns"][1]["needed_by"] = []
    patient["columns"][1]["avoid"] = ["death"]
    view(worded, "role_fluid")["status"] = "contract"
    worded["vocabularies"]["technique"][0]["meaning"] = "Reworded."
    assert rolemap.part_hashes(worded) == before
    assert contractcheck.check_contract(worded, lock=lock) == []
    # A renamed column is a change to what the part is, and the lock file holds it to version 1.
    renamed = shipped()
    column = next(c for c in view(renamed, "role_patient")["columns"] if c["name"] == "death_date")
    column["name"] = "date_of_death"
    assert rolemap.part_hashes(renamed)["role_patient"] != before["role_patient"]
    assert {k: v for k, v in rolemap.part_hashes(renamed).items() if k != "role_patient"} == {k: v for k, v in before.items() if k != "role_patient"}
    assert contractcheck.check_contract(renamed, lock=lock) == [
        "The part role_patient has changed in what it is and its version is still 1: raise it to 2."]
    assert contractcheck.check_contract(renamed, lock=lock, published=False) == [
        "The part role_patient has changed in what it is and its version is still 1: raise it to 2."]
    view(renamed, "role_patient")["version"] = 2
    assert contractcheck.check_contract(renamed, lock=lock) == [
        "The part role_patient has changed to version 2: write the lock file with --write-lock."]
    # A candidate that is not yet published, as the try command reads one, is held to the raised version alone.
    assert contractcheck.check_contract(renamed, lock=lock, published=False) == []
    view(renamed, "role_patient")["version"] = 0
    assert "The part role_patient has no version, or its version is not a whole number from 1." in contractcheck.check_contract(renamed, lock=lock)
    # A kind's optionality, its vocabulary or its type is part of the definition as well.
    for change in (lambda c: c.update({"optional": True}), lambda c: c.update({"type": "text"})):
        altered = shipped()
        change(next(c for c in view(altered, "role_patient")["columns"] if c["name"] == "death_date"))
        assert rolemap.part_hashes(altered)["role_patient"] != before["role_patient"]
    # A new part is written to the lock file before it is published, and a version is never lowered.
    added = shipped()
    added["views"].append({**copy.deepcopy(view(added, "role_fluid")), "name": "role_fluid_detail", "version": 1})
    assert "The part role_fluid_detail is not in the lock file: write it there with --write-lock once the part is as it should be." in contractcheck.check_contract(added, lock=lock)
    lowered = shipped()
    view(lowered, "role_patient")["version"] = 1
    higher = json.loads(json.dumps(lock))
    higher["parts"]["role_patient"]["version"] = 2
    assert contractcheck.check_contract(lowered, lock=higher) == [
        "The part role_patient has version 1, and the lock file has 2: a version is never lowered."]


def test_the_command_writes_the_lock_file_and_never_hides_a_change_without_a_raised_version(tmp_path, capsys):
    contract = tmp_path / "contract.json"
    shutil.copy(contractcheck.CONTRACT, contract)
    lock = tmp_path / "parts.lock.json"
    # With no lock file yet, every part is unwritten, and --write-lock writes them.
    assert contractcheck.main([str(contract), "--lock", str(lock)]) == 1
    assert "is not in the lock file" in capsys.readouterr().out
    assert contractcheck.main([str(contract), "--lock", str(lock), "--write-lock"]) == 0
    assert json.loads(lock.read_text()) == model_reader.read_lock()
    # A column renamed without a raised version is refused, and the lock file is left as it was.
    model = json.loads(contract.read_text())
    next(c for c in view(model, "role_patient")["columns"] if c["name"] == "death_date")["name"] = "date_of_death"
    contract.write_text(json.dumps(model), encoding="utf-8")
    held = lock.read_text()
    assert contractcheck.main([str(contract), "--lock", str(lock), "--write-lock"]) == 1
    assert "raise it to 2" in capsys.readouterr().out and lock.read_text() == held
    with pytest.raises(ValueError):
        contractcheck.write_lock(model, lock)
    # With the version raised, --write-lock records the new definition.
    view(model, "role_patient")["version"] = 2
    contract.write_text(json.dumps(model), encoding="utf-8")
    assert contractcheck.main([str(contract), "--lock", str(lock), "--write-lock"]) == 0
    out = capsys.readouterr().out
    assert "is written from the contract" in out and "Every part is at the version the lock file holds" in out
    assert json.loads(lock.read_text())["parts"]["role_patient"]["version"] == 2
    assert contractcheck.main([str(contract), "--lock", str(lock)]) == 0
