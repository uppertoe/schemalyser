"""Tests for the data dictionary of a conversion."""
import csv
import json
import re
import sys
from pathlib import Path

import pytest

from schemalyser import convert, dictionary
from schemalyser.catalogue import Catalogue

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
CLARITY = ROOT / "etl" / "clarity"
CLARITY_CATALOGUE = ROOT / "reference" / "worlds" / "clarity" / "catalogue.csv"
# A few public concepts, as an Athena download would give them, so that the names can be tested without one.
CONCEPTS = [("3004249", "Systolic blood pressure", "Measurement", "LOINC", "S"),
            ("8876", "millimeter mercury column", "Unit", "UCUM", "S"),
            ("4174669", "General anesthesia", "Procedure", "SNOMED", "S"),
            ("9529", "kilogram", "Unit", "UCUM", "S")]


def _vocabulary(folder):
    folder.mkdir(exist_ok=True)
    lines = ["concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code\t"
             "valid_start_date\tvalid_end_date\tinvalid_reason"]
    lines += [f"{c}\t{n}\t{d}\t{v}\tClinical\t{s}\tx\t19700101\t20991231\t" for c, n, d, v, s in CONCEPTS]
    (folder / "CONCEPT.csv").write_text("\n".join(lines) + "\n")
    return folder


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return dictionary.build(CONVERSION, _vocabulary(tmp_path_factory.mktemp("athena")))


def test_the_dictionary_covers_the_layers_tables_and_the_core_tables_a_query_joins_to(built):
    tables = built["tables"]
    assert list(tables)[:3] == ["person", "visit_occurrence", "death"]
    assert {"visit_detail", "procedure_occurrence", "measurement", "observation", "drug_exposure", "device_exposure",
            "anaesthetic", "anaesthetic_phase"} <= set(tables)
    assert "condition_occurrence" not in tables and tables["death"]["kind"] == "core"
    assert tables["anaesthetic"]["kind"] == "custom" and tables["person"]["kind"] == "core"
    # Every field of a custom table is described, with its rule from tables.json.
    defined = {t["name"]: t for t in convert.read_tables(CONVERSION)}
    for name in ("anaesthetic", "anaesthetic_phase"):
        assert set(tables[name]["fields"]) == {f["name"] for f in defined[name]["fields"]}
        for field in defined[name]["fields"]:
            assert tables[name]["fields"][field["name"]]["rule"] == field["description"]


def test_each_field_says_how_each_step_fills_it(built):
    fields = built["tables"]["measurement"]["fields"]
    by_step = {w["step"]: w["fill"] for w in fields["measurement_concept_id"]["written_by"]}
    assert by_step["measurement_asa.sql"] == {"kind": "constant", "value": "4199571"}
    assert by_step["measurement_blood_pressure_through_anaesthetic.sql"]["kind"] == "mapping"
    assert by_step["measurement.sql"]["kind"] == "first_of"
    assert {w["fill"]["kind"] for w in fields["measurement_datetime"]["written_by"]} == {"source", "omop"}
    assert fields["measurement_event_id"]["written_by"][0]["fill"] == {"kind": "omop", "field": "procedure_occurrence.procedure_occurrence_id"}
    age = built["tables"]["anaesthetic"]["fields"]["age_days"]["written_by"][0]["fill"]
    assert age["kind"] == "computed" and "person.birth_datetime" in age["fields"]
    assert "measurement_time" in built["tables"]["measurement"]["not_written"]


def test_concept_fields_list_the_concepts_they_can_hold_with_their_names(built):
    fields = built["tables"]["measurement"]["fields"]
    assert {"3004249", "3012888", "4199571", "0"} <= set(fields["measurement_concept_id"]["concepts"])
    assert fields["measurement_concept_id"]["open"] == []
    # The anaesthetic table takes its type from the anaesthetic's own procedure row only.
    kinds = built["tables"]["anaesthetic"]["fields"]["anaesthesia_type_concept_id"]
    assert set(kinds["concepts"]) == {"4174669", "4100052", "4219502", "4249997", "0"} and kinds["open"] == []
    # The core's operations come from a vocabulary that is proposed from labels, so that field stays open.
    assert built["tables"]["procedure_occurrence"]["fields"]["procedure_concept_id"]["open"] == ["derived"]
    assert built["concepts"]["3004249"]["name"] == "Systolic blood pressure"
    assert built["concepts"]["3004249"]["written_in"] == ["measurement.measurement_concept_id"]
    assert built["concepts"]["0"]["name"] == "No matching concept"


def test_the_units_of_each_value_are_given_by_concept(built):
    units = built["units"]["measurement"]["by_concept"]
    assert units["3004249"] == ["8876"] and units["3025315"] == ["9529"]
    assert "9373" not in built["tables"]["measurement"]["fields"]["unit_concept_id"]["concepts"]
    assert len({u for found in units.values() for u in found}) > 1


def test_the_links_to_an_anaesthetic_are_documented(built):
    roots = set(built["anaesthetic_roots"])
    assert roots == {"procedure_occurrence.procedure_occurrence_id", "visit_detail.visit_detail_id"}
    ids = built["identifiers"]
    assert ids["measurement.measurement_event_id"] == ids["anaesthetic.anaesthetic_id"] == ["procedure_occurrence.procedure_occurrence_id"]
    assert ids["anaesthetic_phase.anaesthetic_id"] == ["procedure_occurrence.procedure_occurrence_id"]
    assert ids["measurement.visit_detail_id"] == ids["anaesthetic.visit_detail_id"] == ["visit_detail.visit_detail_id"]
    assert ids["measurement.person_id"] == ["person.person_id"]
    text = dictionary.markdown(built)
    assert "measurement.meas_event_field_concept_id holds the concept 1147082" in text


def test_the_conventions_are_those_the_steps_show(built):
    text = "\n".join(built["conventions"])
    assert ("Where the step measurement.sql finds no mapping row, it writes the concept 0 in measurement.measurement_concept_id "
            "and keeps the source's own identifier in measurement.measurement_source_value.") in text
    assert "The step measurement.sql writes only the source rows that a flag in the source does not mark to be left out" in text
    assert "The step device_exposure.sql leaves device_exposure.device_exposure_end_datetime empty" in text
    assert "The step drug_exposure_infusion.sql writes one row for each period at one rate" in text
    assert "The step measurement_blood_pressure_through_anaesthetic.sql writes a row only where the source's code has a mapping row" in text
    # An anaesthetic with a start and no stop is written, with an empty end.
    assert ("The step visit_detail_through_case.sql writes a row only where the source gives a start and no end before it, "
            "and it leaves the end empty where the source gives none.") in text
    # A reading points at its anaesthetic through the event field only within the window, whose margin one mapping row sets.
    assert ("The step measurement.sql fills measurement.measurement_event_id only for a row of an anaesthetic's own record that "
            "was taken from the anaesthetic's start to its end, with a margin of 15 minutes either side, or from the start less "
            "that margin where the anaesthetic has no end. The mapping row under SITE_SETTING sets the margin") in text
    assert "The step observation_anaesthesia_events.sql fills observation.observation_event_id only for a row" in text


def _allowed(folder, built):
    """Every word that the dictionary may hold: OMOP and custom names, concept attributes, vocabulary names, step file names,
    the conversion's own descriptions and text constants, and the fixed wording."""
    words = set()

    def add(text):
        words.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", str(text)))

    for table, fields in convert.cdm_fields().items():
        add(table)
        add(" ".join(f for f, _, _ in fields))
    with open(convert.FIELDS, newline="") as f:
        add(" ".join(row["concept_domain"] for row in csv.DictReader(f)))
    for path in folder.glob("*.sql"):
        add(" ".join(re.findall(r"'(SITE_[A-Z_]+)'", path.read_text())))
    for table in convert.read_tables(folder):
        add(json.dumps(table))
    for step in json.loads((folder / "conversion.json").read_text()):
        add(step["file"])
    with open(folder / "source_to_concept_map.csv", newline="") as f:
        # The vocabularies, and the numbers that the mapping rows lead to, such as the margin of the event link in minutes.
        add(" ".join(row["source_vocabulary_id"] + " " + row["target_concept_id"] for row in csv.DictReader(f)))
    add(" ".join(e["vocabulary"] for e in json.loads((folder / "derived_mappings.json").read_text())))
    for concept, about in built["concepts"].items():
        add(" ".join([concept, about["name"], about["domain"], about["vocabulary"], about["standard"]]))
    for path in folder.glob("*.sql"):
        # Text that a step writes as the value of a column, and the numbers it writes, are the conversion's own.
        add(" ".join(re.findall(r"'([^']*)'\s+AS\s", path.read_text())))
        add(" ".join(re.findall(r"\b(\d+)\b", path.read_text())))
    add(json.dumps(dictionary.WORDING))
    add("format wording Schemalyser data dictionary steps file table layer tables kind layers description primary_key fields "
        "not_written type domain rule written_by step fill text value concepts open open_vocabularies units value_field "
        "concept_field by_concept identifiers anaesthetic_roots links root conventions name vocabulary standard written_in "
        "vocabularies proposed true false null cdm core custom anaesthesia derived constant numbered source mapping omop "
        "first_of one_of computed nothing field parts integer float datetime date varchar BIGINT DOUBLE VARCHAR TIMESTAMP DATE none "
        "omop table")
    return {w.lower() for w in words}


def _forbidden(folder, catalogue):
    names = set()
    for table in Catalogue.from_csv(catalogue.read_text()).tables():
        names.add(table.name)
        names.update(column.name for column in table.columns.values())
    codes, descriptions = set(), set()
    with open(folder / "source_to_concept_map.csv", newline="") as f:
        for row in csv.DictReader(f):
            if not row["source_code"].isdigit():
                codes.add(row["source_code"])
            descriptions.add(row["source_code_description"])
    return names, codes, descriptions


@pytest.mark.parametrize("which", ["fixtures", "clarity"])
def test_the_dictionary_holds_only_allowed_words_and_nothing_from_the_source(which, built):
    if which == "fixtures":
        folder, catalogue, found = CONVERSION, FIXTURES / "invented-catalogue.csv", built
    else:
        if not (CLARITY / "conversion.json").exists() or not CLARITY_CATALOGUE.exists():
            pytest.skip("the private conversion is not present")
        folder, catalogue, found = CLARITY, CLARITY_CATALOGUE, dictionary.build(CLARITY)
    text = json.dumps(found) + "\n" + dictionary.markdown(found)
    allowed = _allowed(folder, found)
    unknown = {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text) if w.lower() not in allowed}
    assert not unknown, sorted(unknown)
    names, codes, descriptions = _forbidden(folder, catalogue)
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_$#@]*", text))
    # Source names are written in capitals; the dictionary's own words that match one in lower case are OMOP field names.
    assert not {name for name in names if name in tokens and not name.islower()}
    # A text code is written only where a step itself writes it as the value of a column, as with the anaesthetic's own row.
    own = {t for path in folder.glob("*.sql") for t in re.findall(r"'([^']*)'\s+AS\s", path.read_text())}
    assert not {code for code in codes - own if code in tokens}
    concept_names = " ".join(c["name"] for c in found["concepts"].values()) + json.dumps(convert.read_tables(folder))
    assert not {d for d in descriptions if len(d) > 2 and re.search(rf"(?<!\w){re.escape(d)}(?!\w)", text)
                and not re.search(rf"(?<!\w){re.escape(d)}(?!\w)", concept_names)}
    assert "--" not in dictionary.markdown(found)       # no step comment is carried across


def test_a_dictionary_that_would_hold_a_source_name_is_refused(tmp_path):
    import shutil
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    # A custom table's rule that names a source column must not reach the dictionary.
    tables = json.loads((folder / "tables.json").read_text())
    tables[0]["fields"][1]["description"] = "The person is taken from PERSON_KEY."
    (folder / "tables.json").write_text(json.dumps(tables))
    with pytest.raises(dictionary.DictionaryError):
        dictionary.build(folder)


def test_the_command_writes_both_forms(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["schemalyser.dictionary", str(CONVERSION), "--out", str(out)])
    assert dictionary.main() == 0
    assert json.loads((out / "dictionary.json").read_text())["tables"]
    text = (out / "dictionary.md").read_text()
    assert text.startswith("# Data dictionary of the conversion") and "### measurement" in text
    assert dictionary.load(out)["format"] == 1


def test_the_wording_is_calm_and_complete():
    texts = [v for v in dictionary.WORDING.values() if isinstance(v, str)]
    texts += [t for v in dictionary.WORDING.values() if isinstance(v, (dict, list)) for t in (v.values() if isinstance(v, dict) else v)]
    for text in texts:
        assert "?" not in text and "!" not in text and "  " not in text, text
