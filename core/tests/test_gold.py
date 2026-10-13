"""The gold scorer: a proposal against what an expert bound, graded by what decides it, as counts."""
import json
import tempfile
from pathlib import Path

import pytest

from schemalyser import datadict, describe, gold, propose
from schemalyser.catalogue import Catalogue
from schemalyser.extract import decode

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
GOLD = FIXTURES / "gold" / "invented.json"
DATE = "2026-10-13"


def test_bindings_are_parsed_and_compared_as_replacements_are_written():
    assert gold.parse("PERSON_MASTER") == ("PERSON_MASTER", None, ())
    assert gold.parse("VISIT.PERSON_KEY via ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY then THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY") == \
        ("VISIT", "PERSON_KEY", ((("ANAES_RECORD", "CASE_KEY", "THEATRE_CASE", "CASE_KEY"),), (("THEATRE_CASE", "VISIT_KEY", "VISIT", "VISIT_KEY"),)))
    # A candidate as map.json writes it reads the same.
    assert gold.parse_from("VISIT.PERSON_KEY, by ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY, then THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY") == \
        gold.parse("VISIT.PERSON_KEY via ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY then THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY")
    with pytest.raises(gold.GoldError):
        gold.parse("not a binding at all")
    with pytest.raises(gold.GoldError):
        gold.parse("A.b via nonsense")


def test_the_invented_proposal_scores_against_the_invented_gold_in_counts_only():
    dictionary = datadict.load(FIXTURES / "dictionary" / "invented-dictionary.csv", FIXTURES / "dictionary" / "invented-tables.csv")
    catalogue = Catalogue.from_csv(decode((FIXTURES / "invented-catalogue.csv").read_bytes()))
    out = Path(tempfile.mkdtemp()) / "draft"
    propose.propose_map(dictionary, catalogue, out, date=DATE)
    held = gold.load_gold(GOLD)
    found = gold.score(held, gold.read_map(out))
    counts = found["counts"]
    assert counts["documented"]["exact"] == 16 and sum(counts["documented"].values()) == 16
    assert counts["corroborated"]["exact"] == 1 and not found["misses"] and not found["unscored"]
    lines = gold.summary(found)
    assert lines[0].startswith("documented: 16 bindings, 16 exact") and not any("PERSON" in line for line in lines)


def test_a_wrong_or_empty_binding_is_counted_by_its_outcome_and_a_hospital_only_guess_is_told_apart(tmp_path):
    data = {"roles": {"role_patient": {"rows": {"binding": {"table": "PERSON_MASTER"}, "candidates": []},
                                       "columns": {"birth_date": {"binding": {"table": "PERSON_MASTER", "column": "BIRTH_WEIGHT_G", "path": []},
                                                                  "candidates": [{"from": "PERSON_MASTER.BIRTH_TS"}]},
                                                   "death_date": {"binding": None, "candidates": []},
                                                   "is_test": {"binding": {"table": "PERSON_MASTER", "column": "GIVEN_NAME", "path": []}, "candidates": []}}}}}
    held = {"format": gold.FORMAT, "bindings": {
        "role_patient rows": {"gold": "PERSON_MASTER", "grade": "documented"},
        "role_patient.birth_date": {"gold": "PERSON_MASTER.BIRTH_TS", "grade": "documented"},
        "role_patient.death_date": {"gold": "PERSON_MASTER_2.DEATH_TS via PERSON_MASTER.PERSON_KEY = PERSON_MASTER_2.PERSON_KEY", "grade": "corroborated"},
        "role_patient.is_test": {"gold": None, "grade": "hospital-only"},
        "role_reading.value": {"gold": "OBS_READING.READ_VALUE", "grade": "documented"}}, "facts": ["ANAES_RECORD.VISIT_KEY"]}
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(held))
    found = gold.score(gold.load_gold(path), data, facts=[])
    c = found["counts"]
    assert (c["documented"]["exact"], c["documented"]["listed"], c["corroborated"]["empty"], c["hospital-only"]["guessed"]) == (1, 1, 1, 1)
    assert found["unscored"] == ["role_reading.value"] and found["facts"] == {"wanted": 1, "asked": 0, "missing": ["ANAES_RECORD.VISIT_KEY"]}
    assert [m["about"] for m in found["misses"]] == ["role_patient.birth_date", "role_patient.death_date", "role_patient.is_test"]
    assert any(line.startswith("hospital-only: 1 bindings, 0 withheld as they should be, 1 guessed") for line in gold.summary(found))
    with pytest.raises(gold.GoldError, match="grade"):
        path.write_text(json.dumps({"format": gold.FORMAT, "bindings": {"x rows": {"gold": "T", "grade": "made up"}}}))
        gold.load_gold(path)


def test_the_command_scores_a_saved_schema_and_names_nothing_unless_asked(capsys):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary((FIXTURES / "dictionary" / "invented-dictionary.csv").read_bytes(), (FIXTURES / "dictionary" / "invented-tables.csv").read_bytes(),
                      {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    folder = Path(tempfile.mkdtemp())
    files = s.folder_files(date=DATE)
    import zipfile
    saved = folder / "schema.schemalyser.zip"
    with zipfile.ZipFile(saved, "w") as out:
        for name, data in files.items():
            out.writestr(name, data)
    assert gold.main([str(GOLD), str(saved)]) == 0
    text = capsys.readouterr().out
    assert "documented: 16 bindings, 16 exact" in text and "PERSON_MASTER" not in text
    assert gold.main([str(GOLD), str(saved), "--names"]) == 0
