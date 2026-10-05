"""Tests for running a conversion to OMOP over the sandbox."""
import csv
import io
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from schemalyser import convert

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


@pytest.fixture(scope="module")
def ran():
    # The generated rows alone, which the tests below describe. The planted scenarios are tested on their own.
    return convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=400, scenarios=[])


@pytest.fixture(scope="module")
def planted():
    return convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=400)


def test_the_cdm_definitions_are_complete():
    tables = convert.cdm_fields()
    assert len(tables) == 39 and sum(len(fields) for fields in tables.values()) == 432
    assert [name for name, _, _ in tables["person"]][:3] == ["person_id", "gender_concept_id", "year_of_birth"]


def test_every_step_runs_and_writes_rows(ran):
    _, report = ran
    assert [(s["table"], s["status"]) for s in report["steps"]] == [(table, "ok") for table in (
        # the core, which stands in for the hospital's own OMOP database
        "person", "provider", "care_site", "visit_occurrence", "procedure_occurrence", "condition_occurrence", "drug_exposure",
        "death", "observation_period", "cdm_source",
        # the anaesthesia layer, which runs after it
        "visit_detail", "procedure_occurrence", "procedure_occurrence", "measurement", "measurement", "measurement",
        "measurement", "observation", "drug_exposure", "device_exposure",
        # the derived layer, which builds the custom tables from the OMOP rows
        "anaesthetic", "anaesthetic_phase")]
    # The calculated mean pressure is switched off by default, so its step writes no row.
    files = [step["file"] for step in json.loads((FIXTURES / "conversion" / "conversion.json").read_text())]
    assert all((s["rows"] == 0) == (name == "measurement_mean_pressure_calculated.sql") for s, name in zip(report["steps"], files))
    assert report["problems"] == []


def test_the_written_tables_hold_together(ran):
    conversion, _ = ran
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    # Test people are left out, and sex is mapped to standard concepts.
    assert one("SELECT COUNT(*) FROM omop.person") < 400
    assert {r[0] for r in conversion.con.execute("SELECT DISTINCT gender_concept_id FROM omop.person").fetchall()} == {8507, 8532}
    # Every visit, visit detail and procedure belongs to a person and a visit that were written.
    assert one("SELECT COUNT(*) FROM omop.visit_occurrence v LEFT JOIN omop.person p USING (person_id) WHERE p.person_id IS NULL") == 0
    assert one("""SELECT COUNT(*) FROM omop.procedure_occurrence po
                  LEFT JOIN omop.visit_detail vd USING (visit_detail_id)
                  WHERE po.visit_detail_id IS NOT NULL AND vd.visit_detail_id IS NULL""") == 0
    # The core knows nothing of the anaesthetic, so its own rows carry a visit and no visit detail.
    assert one("SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_datetime IS NULL AND visit_detail_id IS NOT NULL") == 0
    assert one("SELECT COUNT(*) FROM omop.drug_exposure WHERE sig IS NULL AND visit_detail_id IS NOT NULL") == 0
    # An anaesthetic ends after it starts, and nobody is anaesthetised before they are born.
    assert one("SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_end_datetime < procedure_datetime") == 0
    assert one("""SELECT COUNT(*) FROM omop.procedure_occurrence po JOIN omop.person p USING (person_id)
                  WHERE po.procedure_datetime < p.birth_datetime""") == 0


def test_the_export_is_one_csv_for_each_table_in_the_published_order(ran):
    conversion, _ = ran
    files = conversion.export()
    assert set(files) == {name + ".csv" for name in (
        "person", "provider", "care_site", "visit_occurrence", "visit_detail", "procedure_occurrence", "measurement", "observation",
        "condition_occurrence", "drug_exposure", "device_exposure", "death", "observation_period", "cdm_source", "source_to_concept_map",
        "anaesthetic", "anaesthetic_phase")}
    header = next(csv.reader(io.StringIO(files["person.csv"])))
    assert header == [name for name, _, _ in convert.cdm_fields()["person"]]


def test_a_step_that_breaks_the_cdm_is_reported(ran):
    conversion, _ = ran
    assert conversion.step("person", "SELECT 1 AS person_id, 2 AS shoe_size")["status"] == "unknown-fields"
    assert conversion.step("no_such_table", "SELECT 1 AS x")["status"] == "unknown-table"
    assert conversion.step("person", "CREATE PROCEDURE p AS BEGIN SELECT 1 END")["status"] == "unsupported"
    before = conversion.count("person")
    assert conversion.step("person", "SELECT -1 AS person_id")["status"] == "ok"
    assert any(p.startswith("person.gender_concept_id") for p in conversion.problems())
    conversion.con.execute("DELETE FROM omop.person WHERE person_id = -1")
    # Text longer than the model allows is reported, because the sandbox would not refuse it and the real database would.
    conversion.con.execute("UPDATE omop.source_to_concept_map SET source_vocabulary_id = 'A_NAME_OF_TWENTY_ONE_' WHERE source_code = '1' AND source_vocabulary_id = 'SITE_SEX'")
    assert any(p.startswith("source_to_concept_map.source_vocabulary_id: 1 rows hold text longer than the 20") for p in conversion.problems())
    conversion.con.execute("UPDATE omop.source_to_concept_map SET source_vocabulary_id = 'SITE_SEX' WHERE source_vocabulary_id = 'A_NAME_OF_TWENTY_ONE_'")
    assert conversion.problems() == []
    assert conversion.count("person") == before


def test_a_join_through_a_cast_is_a_join():
    from schemalyser.catalogue import Catalogue
    from schemalyser.extract import analyse_request
    catalogue = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
    findings = analyse_request(
        "SELECT ar.ANAES_KEY FROM ANAES_RECORD ar JOIN VISIT v ON CAST(v.VISIT_KEY AS varchar(50)) = CAST(ar.VISIT_KEY AS varchar(50))",
        catalogue, []).findings
    assert ("join", "ANAES_RECORD", "VISIT_KEY", "VISIT", "VISIT_KEY", "inner") in findings


def test_the_analysis_follows_a_key_through_an_omop_table():
    text = convert.as_request([
        ("person", "SELECT pm.PERSON_KEY AS person_source_value FROM PERSON_MASTER pm"),
        ("visit_occurrence", "SELECT v.VISIT_KEY AS visit_source_value FROM VISIT v "
                             "JOIN omop.person p ON p.person_source_value = v.PERSON_KEY")])
    from schemalyser.catalogue import Catalogue
    from schemalyser.extract import analyse_request
    catalogue = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
    joins = [f for f in analyse_request(text, catalogue, []).findings if f[0] == "join"]
    assert joins == [("join", "PERSON_MASTER", "PERSON_KEY", "VISIT", "PERSON_KEY", "inner")]


def test_the_postgresql_script_creates_every_table_and_loads_the_exported_files(ran):
    conversion, _ = ran
    script = convert.postgresql_script(conversion.export(), custom=convert.read_tables(FIXTURES / "conversion"))
    assert script.count("CREATE TABLE IF NOT EXISTS cdm.") == 39 + 2
    assert '"person_id" BIGINT NOT NULL' in script and '"birth_datetime" TIMESTAMP,' in script
    assert '"phase_name" varchar(50) NOT NULL' in script
    assert script.count("\\copy cdm.") == 17 and "FROM 'person.csv' WITH (FORMAT csv, HEADER true)" in script


def test_the_postgresql_script_types_hold_the_largest_identifier_the_run_wrote(ran):
    conversion, _ = ran
    files = conversion.export()
    script = convert.postgresql_script(files, custom=convert.read_tables(FIXTURES / "conversion"))
    # No column is typed as a 32-bit integer, and every integer field of the CDM and the custom tables is a BIGINT.
    assert not re.search(r'" (integer|int|int4)\b', script, re.IGNORECASE)
    types = {(table, field): kind for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS cdm\.(\w+) \((.*?)\n\);", script, re.DOTALL)
             for field, kind in re.findall(r'^  "([a-z_0-9]+)" ([A-Za-z]+)', body, re.MULTILINE)}
    largest = 0
    for name, text in files.items():
        rows = list(csv.reader(io.StringIO(text)))
        for column, field in enumerate(rows[0]):
            values = [int(row[column]) for row in rows[1:] if re.fullmatch(r"-?\d+", row[column] or "")]
            if values and types.get((name[:-len(".csv")], field)) == "BIGINT":
                largest = max(largest, max(values))
    assert largest > convert.IDENTIFIER_OFFSET > 2 ** 31 - 1     # the anaesthesia layer's identifiers lie beyond integer
    assert largest <= 2 ** 63 - 1                                # and within BIGINT
    for row in convert._definitions(convert.read_tables(FIXTURES / "conversion")):
        if row["datatype"] == "integer":
            assert types[(row["table"], row["field"])] == "BIGINT", (row["table"], row["field"])


def test_measurements_are_mapped_and_written_in_metric_units(ran):
    conversion, _ = ran
    rows = dict((concept, (unit, low, high)) for concept, unit, low, high in conversion.con.execute(
        "SELECT measurement_concept_id, MIN(unit_concept_id), MIN(value_as_number), MAX(value_as_number) "
        "FROM omop.measurement GROUP BY 1").fetchall())
    # What has a mapping row, the ASA class, and the concept 0 for a reading of the anaesthetic whose variable has none.
    assert set(rows) == {3025315, 3027018, 40762499, 3004249, 3012888, 4199571, 21492241, 21490852, 0}
    unit, lightest, heaviest = rows[3025315]
    assert unit == 9529 and 0.3 < lightest and heaviest < 150   # ounces at the source, kilograms here
    # The mean pressures from the cuff and from an arterial line, each in mmHg and within the generator's bounds.
    for concept in (21492241, 21490852):
        unit, low, high = rows[concept]
        assert unit == 8876 and 20 <= low and high <= 120
    assert min(rows[concept][1] for concept in (21492241, 21490852)) < 40
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    assert one("""SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id <> 4199571
                  AND (visit_detail_id IS NULL OR value_as_number IS NULL)""") == 0
    assert one("SELECT COUNT(*) FROM omop.measurement m LEFT JOIN omop.person p USING (person_id) WHERE p.person_id IS NULL") == 0


def test_concepts_are_checked_against_the_vocabulary(ran, tmp_path):
    conversion, _ = ran
    header = "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code"
    concepts = ["8507\tMALE\tGender\tGender\tGender\tS\tM", "8532\tFEMALE\tGender\tGender\tGender\tS\tF",
                "9201\tInpatient Visit\tVisit\tVisit\tVisit\tS\tIP", "32817\tEHR\tType Concept\tType Concept\tType Concept\tS\tEHR",
                "3027018\tHeart rate\tMeasurement\tLOINC\tClinical Observation\tS\t8867-4",
                "3025315\tBody weight\tObservation\tLOINC\tClinical Observation\t\t29463-7",
                "8541\tper minute\tUnit\tUCUM\tUnit\tS\t/min", "8554\tpercent\tUnit\tUCUM\tUnit\tS\t%",
                "9529\tkilogram\tUnit\tUCUM\tUnit\tS\tkg"]
    path = tmp_path / "CONCEPT.csv"
    path.write_text("\n".join([header] + concepts) + "\n")
    found = conversion.concept_problems(path)
    assert any(f.startswith("person.race_concept_id:") and "concept 0" in f for f in found)
    assert any(f.startswith("measurement.measurement_concept_id = 40762499: this concept is not in the vocabulary") for f in found)
    assert any("3025315 (Body weight): this is not a standard concept" in f for f in found)
    assert any("3025315 (Body weight): its domain is Observation, and the field expects Measurement" in f for f in found)
    assert not any("3027018" in f or "= 8507" in f or "= 32817" in f or "= 9529" in f for f in found)


def test_every_event_falls_inside_its_person_s_observation_period(ran):
    conversion, _ = ran
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    assert one("SELECT COUNT(*) FROM omop.observation_period") == one("SELECT COUNT(*) FROM omop.person")
    assert one("""SELECT COUNT(*) FROM omop.procedure_occurrence po JOIN omop.observation_period op USING (person_id)
                  WHERE po.procedure_date NOT BETWEEN op.observation_period_start_date AND op.observation_period_end_date""") == 0
    assert one("""SELECT COUNT(*) FROM omop.measurement m JOIN omop.observation_period op USING (person_id)
                  WHERE m.measurement_date NOT BETWEEN op.observation_period_start_date AND op.observation_period_end_date""") == 0


def test_timed_values_fall_around_the_anaesthetic_they_belong_to(ran):
    conversion, _ = ran
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    joined = ("FROM omop.measurement m JOIN omop.procedure_occurrence po "
              "ON po.visit_detail_id = m.visit_detail_id AND po.procedure_source_value = 'ANAESTHETIC'")
    # A heart rate is recorded while the anaesthetic runs, and a weight in the day before it starts.
    assert one(f"""SELECT COUNT(*) {joined} WHERE m.measurement_concept_id = 3027018
                   AND m.measurement_datetime NOT BETWEEN po.procedure_datetime AND po.procedure_end_datetime""") == 0
    assert one(f"""SELECT COUNT(*) {joined} WHERE m.measurement_concept_id = 3025315
                   AND NOT (m.measurement_datetime < po.procedure_datetime
                            AND m.measurement_datetime >= po.procedure_datetime - INTERVAL 1 DAY)""") == 0


def test_a_charted_blood_pressure_becomes_a_systolic_and_a_diastolic_row(ran):
    conversion, _ = ran
    pairs = conversion.con.execute("""
        SELECT s.value_as_number, d.value_as_number, s.value_source_value
        FROM omop.measurement s JOIN omop.measurement d
          ON d.person_id = s.person_id AND d.measurement_datetime = s.measurement_datetime
         AND d.value_source_value = s.value_source_value
        WHERE s.measurement_concept_id = 3004249 AND d.measurement_concept_id = 3012888""").fetchall()
    assert pairs
    for systolic, diastolic, text in pairs:
        assert text == f"{int(systolic)}/{int(diastolic)}" and systolic > diastolic
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    assert one("SELECT COUNT(*) FROM (SELECT measurement_id FROM omop.measurement GROUP BY 1 HAVING COUNT(*) > 1)") == 0
    assert one("SELECT COUNT(DISTINCT unit_concept_id) FROM omop.measurement WHERE measurement_concept_id IN (3004249, 3012888)") == 1


def test_only_doses_that_were_given_become_drug_exposures(ran):
    conversion, _ = ran
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    # Every given dose of a person who was written to PERSON, and no other dose.
    # A single dose has a quantity and no rate text. An infusion period is the other way about.
    assert one("SELECT COUNT(*) FROM omop.drug_exposure WHERE sig IS NULL") == one("""
        SELECT COUNT(*) FROM DRUG_GIVEN g JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(g.VISIT_KEY AS VARCHAR)
        WHERE g.ACTION_CAT = 1 AND g.GIVEN_TS IS NOT NULL""")
    assert one("SELECT COUNT(*) FROM omop.drug_exposure") < one("SELECT COUNT(*) FROM DRUG_GIVEN")
    assert one("""SELECT COUNT(*) FROM omop.drug_exposure WHERE sig IS NULL
                  AND (quantity IS NULL OR visit_occurrence_id IS NULL OR drug_source_value IS NULL)""") == 0
    assert one("SELECT COUNT(*) FROM omop.drug_exposure WHERE route_concept_id = 0") == 0
    # A dose is given while the anaesthetic runs.
    assert one("""SELECT COUNT(*) FROM omop.drug_exposure d WHERE NOT EXISTS (
                    SELECT 1 FROM omop.visit_detail vd WHERE vd.person_id = d.person_id
                    AND d.drug_exposure_start_datetime BETWEEN vd.visit_detail_start_datetime AND vd.visit_detail_end_datetime)""") == 0
    # Without a vocabulary no medicine can be matched by name, so each is written as not mapped.
    assert one("SELECT COUNT(*) FROM omop.drug_exposure WHERE drug_concept_id <> 0") == 0


def _small_vocabulary(folder):
    concept = ["concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason",
               "753626\tpropofol\tDrug\tRxNorm\tIngredient\tS\t8782\t19700101\t20991231\t",
               "1125315\tacetaminophen\tDrug\tRxNorm\tIngredient\tS\t161\t19700101\t20991231\t",
               "900001\tparacetamol\tDrug\tAMT\tAU Substance\t\tX1\t19700101\t20991231\t",
               "900002\tketamine\tDrug\tRxNorm\tIngredient\tS\tX2\t19700101\t20991231\t",
               "900003\tketamine\tDrug\tRxNorm Extension\tIngredient\tS\tX3\t19700101\t20991231\t",
               "900004\tpropofol\tProcedure\tSNOMED\tProcedure\tS\tX4\t19700101\t20991231\t",
               "4070719\tTonsillectomy\tProcedure\tSNOMED\tProcedure\tS\t173422009\t19700101\t20991231\t",
               "900005\tUmbilical hernia without obstruction or gangrene\tCondition\tICD10\tICD10 code\t\tK42.9\t19700101\t20991231\t",
               "4245842\tUmbilical hernia\tCondition\tSNOMED\tDisorder\tS\t396347007\t19700101\t20991231\t"]
    relationship = ["concept_id_1\tconcept_id_2\trelationship_id\tvalid_start_date\tvalid_end_date\tinvalid_reason",
                    "900001\t1125315\tMaps to\t19700101\t20991231\t", "900005\t4245842\tMaps to\t19700101\t20991231\t"]
    synonym = ["concept_id\tconcept_synonym_name\tlanguage_concept_id", "1125315\tAPAP\t4180186"]
    (folder / "CONCEPT.csv").write_text("\n".join(concept) + "\n")
    (folder / "CONCEPT_RELATIONSHIP.csv").write_text("\n".join(relationship) + "\n")
    (folder / "CONCEPT_SYNONYM.csv").write_text("\n".join(synonym) + "\n")
    return folder


def test_labels_are_matched_to_concepts_by_name(tmp_path):
    from schemalyser import mapping
    labels = [("1", "PROPOFOL"), ("2", "Paracetamol"), ("3", "apap"), ("4", "KETAMINE"), ("5", "GLUCOSE 5%"), ("6", "")]
    matched, unmatched = mapping.propose(labels, _small_vocabulary(tmp_path))
    assert matched["1"][0] == 753626                      # by name, and not the procedure of the same name
    assert matched["2"][0] == 1125315                     # an Australian name, through the concept it maps to
    assert matched["3"][0] == 1125315                     # by synonym
    assert [(code, reason) for code, _, reason in unmatched] == [
        ("4", "more than one concept has this name"), ("5", "no concept has this name")]
    rows = mapping.rows(labels, matched, "SITE_DRUG")
    assert ["1", 0, "SITE_DRUG", "PROPOFOL", 753626, "RxNorm", "1970-01-01", "2099-12-31", None] in rows and len(rows) == 3


def test_with_a_vocabulary_the_medicines_are_mapped_by_name(tmp_path):
    conversion, report = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=400, vocabulary=_small_vocabulary(tmp_path))
    derived = {item["vocabulary"]: item for item in report["derived"]}
    assert set(derived) == {"SITE_DRUG", "SITE_PROC", "SITE_DIAGNOSIS"}
    assert derived["SITE_DRUG"]["matched"] > 0 and "KETAMINE" in derived["SITE_DRUG"]["unmatched"]
    assert derived["SITE_PROC"]["matched"] > 0 and derived["SITE_DIAGNOSIS"]["matched"] > 0
    # An operation is matched by its name, and a diagnosis by its ICD-10 code through the concept it maps to.
    operations = dict(conversion.con.execute(
        "SELECT procedure_source_value, MIN(procedure_concept_id) FROM omop.procedure_occurrence WHERE procedure_datetime IS NULL GROUP BY 1").fetchall())
    assert operations["TONSILLECTOMY"] == 4070719 and all(c in (0, 4070719) for c in operations.values())
    diagnoses = dict(conversion.con.execute(
        "SELECT condition_source_value, MIN(condition_concept_id) FROM omop.condition_occurrence GROUP BY 1").fetchall())
    assert diagnoses["K42.9"] == 4245842 and diagnoses["J35.3"] == 0
    # The list of vocabularies is read for the version only, and is never exported.
    assert "vocabulary.csv" not in conversion.export()
    mapped = dict(conversion.con.execute(
        "SELECT drug_source_value, MIN(drug_concept_id) FROM omop.drug_exposure GROUP BY 1").fetchall())
    expected = {"PROPOFOL": 753626, "PARACETAMOL": 1125315}
    assert set(expected) & set(mapped)
    for name, concept in mapped.items():
        assert concept == expected.get(name, 0)      # every other medicine, ketamine among them, stays unmapped


def _first_anaesthetic(conversion):
    # The anaesthetic's key, and the key of the hospital visit that the layer found for it.
    return conversion.con.execute("""SELECT ar.ANAES_KEY, CAST(vo.visit_source_value AS BIGINT) FROM ANAES_RECORD ar
        JOIN omop.visit_detail vd ON vd.visit_detail_source_value = ar.ANAES_KEY
        JOIN omop.visit_occurrence vo ON vo.visit_occurrence_id = vd.visit_occurrence_id ORDER BY 1 LIMIT 1""").fetchone()


def test_an_infusion_becomes_one_row_for_each_period_at_one_rate(ran):
    conversion, _ = ran
    con = conversion.con
    anaesthetic, visit = _first_anaesthetic(conversion)
    before = con.execute("SELECT MAX(drug_exposure_id) FROM omop.drug_exposure").fetchone()[0]
    # Started at ten, the rate changed at half past, and stopped at eleven.
    for key, time, action in ((990001, "10:00", 6), (990002, "10:30", 6), (990003, "11:00", 8)):
        con.execute("INSERT INTO DRUG_GIVEN (GIVEN_KEY, VISIT_KEY, ANAES_KEY, DRUG_KEY, GIVEN_TS, DOSE_AMT, ACTION_CAT) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [key, visit, anaesthetic, "TESTDRUG", f"2031-01-01 {time}:00", 5, action])
    try:
        sql = (FIXTURES / "conversion" / "drug_exposure_infusion.sql").read_text()
        assert conversion.step("drug_exposure", sql, "anaesthesia")["status"] == "ok"
        periods = con.execute("""SELECT strftime(drug_exposure_start_datetime, '%H:%M'), strftime(drug_exposure_end_datetime, '%H:%M')
            FROM omop.drug_exposure WHERE drug_exposure_id > ? AND year(drug_exposure_start_date) = 2031 ORDER BY 1""", [before]).fetchall()
        assert periods == [("10:00", "10:30"), ("10:30", "11:00")]
    finally:
        con.execute("DELETE FROM omop.drug_exposure WHERE drug_exposure_id > ?", [before])
        con.execute("DELETE FROM DRUG_GIVEN WHERE DRUG_KEY = 'TESTDRUG'")
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    # Every infusion period that the run itself wrote ends at or after its start.
    assert one("SELECT COUNT(*) FROM omop.drug_exposure WHERE sig IS NOT NULL") > 0
    assert one("SELECT COUNT(*) FROM omop.drug_exposure WHERE drug_exposure_end_datetime < drug_exposure_start_datetime") == 0
    assert one("SELECT COUNT(*) FROM (SELECT drug_exposure_id FROM omop.drug_exposure GROUP BY 1 HAVING COUNT(*) > 1)") == 0


def test_a_device_that_is_still_in_place_has_no_end(ran):
    conversion, _ = ran
    con = conversion.con
    anaesthetic, _ = _first_anaesthetic(conversion)
    before = con.execute("SELECT COUNT(*) FROM omop.device_exposure").fetchone()[0]
    assert before > 0
    assert {r[0] for r in con.execute("SELECT DISTINCT device_concept_id FROM omop.device_exposure").fetchall()} <= {4097216, 4106029, 4219637}
    assert con.execute("SELECT COUNT(*) FROM omop.device_exposure WHERE device_exposure_end_datetime < device_exposure_start_datetime").fetchone()[0] == 0
    con.execute("INSERT INTO AIRWAY_DEVICE (DEVICE_KEY, ANAES_KEY, DEVICE_KIND_KEY, PLACED_TS, REMOVED_TS) VALUES (990001, ?, '3040200001', '2031-01-01 10:00:00', '2157-11-19 00:00:00')",
                [anaesthetic])
    con.execute("DELETE FROM omop.device_exposure")
    try:
        assert conversion.step("device_exposure", (FIXTURES / "conversion" / "device_exposure.sql").read_text(), "anaesthesia")["status"] == "ok"
        assert con.execute("SELECT COUNT(*) FROM omop.device_exposure").fetchone()[0] == before + 1
        assert con.execute("""SELECT device_exposure_end_date, device_exposure_end_datetime FROM omop.device_exposure
                              WHERE year(device_exposure_start_date) = 2031""").fetchall() == [(None, None)]
    finally:
        con.execute("DELETE FROM AIRWAY_DEVICE WHERE DEVICE_KEY = 990001")
        con.execute("DELETE FROM omop.device_exposure WHERE year(device_exposure_start_date) = 2031")


def test_the_anaesthetic_takes_the_concept_of_its_kind_and_carries_its_asa_class(ran):
    conversion, _ = ran
    con = conversion.con
    concepts = dict(con.execute("""SELECT ar.ANAES_KIND_CAT, MIN(po.procedure_concept_id) FROM omop.procedure_occurrence po
        JOIN ANAES_RECORD ar ON ar.ANAES_KEY = (SELECT visit_detail_source_value FROM omop.visit_detail vd WHERE vd.visit_detail_id = po.visit_detail_id)
        WHERE po.procedure_source_value = 'ANAESTHETIC' GROUP BY 1""").fetchall())
    assert concepts[1] == 4174669 and concepts[2] == 4100052 and concepts[4] == 4219502
    assert concepts[3] == 4249997                      # a kind with no mapping row falls back to the general concept
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    # One ASA class for each anaesthetic, as a concept, tied to that anaesthetic.
    assert one("SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 4199571") == one(
        "SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_source_value = 'ANAESTHETIC'")
    assert one("""SELECT COUNT(*) FROM omop.measurement m JOIN omop.procedure_occurrence po ON po.procedure_occurrence_id = m.measurement_event_id
                  WHERE m.measurement_concept_id = 4199571 AND m.meas_event_field_concept_id = 1147082
                    AND po.procedure_source_value = 'ANAESTHETIC' AND m.value_as_concept_id IN (4186042, 4184967, 4186043, 4211334, 4186044)""") == one(
        "SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 4199571")
    # Every measurement that points at an anaesthetic points at its own, and it was taken within the margin of 15
    # minutes either side of the anaesthetic. A reading of the anaesthetic's record outside that window points at none.
    assert one("""SELECT COUNT(*) FROM omop.measurement m LEFT JOIN omop.procedure_occurrence po ON po.procedure_occurrence_id = m.measurement_event_id
                  WHERE m.measurement_event_id IS NOT NULL
                    AND (po.procedure_source_value IS DISTINCT FROM 'ANAESTHETIC' OR po.visit_detail_id <> m.visit_detail_id
                         OR m.measurement_datetime < po.procedure_datetime - INTERVAL 15 MINUTE
                         OR m.measurement_datetime > po.procedure_end_datetime + INTERVAL 15 MINUTE)""") == 0
    outside = """FROM omop.measurement m JOIN omop.procedure_occurrence po ON po.visit_detail_id = m.visit_detail_id
                 AND po.procedure_source_value = 'ANAESTHETIC'
                 WHERE m.measurement_datetime NOT BETWEEN po.procedure_datetime - INTERVAL 15 MINUTE
                                                      AND po.procedure_end_datetime + INTERVAL 15 MINUTE"""
    assert one(f"SELECT COUNT(*) {outside}") > 0
    assert one(f"SELECT COUNT(*) {outside} AND m.measurement_event_id IS NOT NULL") == 0


def test_anaesthesia_events_go_to_the_table_their_concept_belongs_in(ran):
    conversion, _ = ran
    con = conversion.con
    assert {r[0] for r in con.execute("SELECT DISTINCT observation_concept_id FROM omop.observation").fetchall()} == {4161949, 4160029}
    events = con.execute("""SELECT procedure_concept_id, COUNT(*) FROM omop.procedure_occurrence
                            WHERE procedure_source_value <> 'ANAESTHETIC' AND procedure_datetime IS NOT NULL GROUP BY 1""").fetchall()
    assert [concept for concept, _ in events] == [4013354]
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    assert one("SELECT COUNT(*) FROM omop.observation") + events[0][1] == one("""
        SELECT COUNT(*) FROM ANAES_EVENT ev JOIN omop.visit_detail vd ON vd.visit_detail_source_value = ev.ANAES_KEY WHERE ev.EVENT_TS IS NOT NULL""")
    assert one("SELECT COUNT(*) FROM (SELECT procedure_occurrence_id FROM omop.procedure_occurrence GROUP BY 1 HAVING COUNT(*) > 1)") == 0
    assert one("SELECT COUNT(*) FROM omop.observation WHERE observation_event_id IS NULL OR obs_event_field_concept_id <> 1147082") == 0


def test_the_checks_list_the_values_of_every_column_the_conversion_maps():
    import json
    folder = FIXTURES / "conversion"
    steps = [(s["table"], (folder / s["file"]).read_text()) for s in json.loads((folder / "conversion.json").read_text())]
    text = convert.as_request(steps, {"EVENT_TYPE_KEY": ("EVENT_TYPE_DEF", "EVENT_TYPE_KEY")})
    lookups = [line for line in text.splitlines() if line.startswith("SELECT 1 AS mapped")]
    assert "SELECT 1 AS mapped FROM [DRUG_GIVEN] AS x WHERE x.[ROUTE_CAT] IN ('A');" in lookups
    assert "SELECT 1 AS mapped FROM [ANAES_EVENT] AS x JOIN [EVENT_TYPE_DEF] AS d ON d.[EVENT_TYPE_KEY] = x.[EVENT_TYPE_KEY] WHERE x.[EVENT_TYPE_KEY] IN ('A');" in lookups
    # A column reached through a derived table is found as well, as the blood pressure step reaches the observation type.
    assert any("[OBS_READING]" in line and "[OBS_TYPE_KEY]" in line for line in lookups)
    assert not any("source_to_concept_map" in line.lower() for line in lookups)


def test_coded_labels_are_matched_through_the_vocabulary_they_are_coded_in(tmp_path):
    from schemalyser import mapping
    matched, unmatched = mapping.propose([("D1", "k42.9"), ("D2", "Z99.9")], _small_vocabulary(tmp_path), "Condition", coded_in="ICD10")
    assert matched == {"D1": (4245842, "Umbilical hernia", "SNOMED")}
    assert unmatched == [("D2", "Z99.9", "no concept has this code")]


def test_staff_and_wards_are_written_without_names_of_people(ran):
    conversion, _ = ran
    con = conversion.con
    files = conversion.export()
    names = [r[0] for r in con.execute("SELECT DISTINCT STAFF_LABEL FROM STAFF_MASTER WHERE STAFF_LABEL IS NOT NULL").fetchall()]
    assert names and "STAFF_LABEL" not in files["provider.csv"] and "provider_name" in files["provider.csv"].splitlines()[0]
    assert con.execute("SELECT COUNT(provider_name) FROM omop.provider").fetchone()[0] == 0
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    assert one("SELECT COUNT(*) FROM omop.provider") == one("SELECT COUNT(DISTINCT STAFF_KEY) FROM STAFF_MASTER")
    # Each anaesthetic carries the first member of staff listed for it, on its visit detail and on its procedure row.
    assert one("SELECT COUNT(*) FROM omop.visit_detail WHERE provider_id IS NOT NULL") > 0
    assert one("""SELECT COUNT(*) FROM omop.procedure_occurrence po JOIN omop.visit_detail vd USING (visit_detail_id)
                  WHERE po.procedure_source_value = 'ANAESTHETIC' AND po.provider_id IS DISTINCT FROM vd.provider_id""") == 0
    assert one("SELECT COUNT(*) FROM omop.visit_detail vd LEFT JOIN omop.provider p USING (provider_id) WHERE vd.provider_id IS NOT NULL AND p.provider_id IS NULL") == 0
    assert one("SELECT COUNT(*) FROM omop.visit_occurrence WHERE care_site_id IS NOT NULL") > 0
    assert one("SELECT COUNT(*) FROM omop.visit_occurrence vo LEFT JOIN omop.care_site cs USING (care_site_id) WHERE vo.care_site_id IS NOT NULL AND cs.care_site_id IS NULL") == 0


def test_operations_diagnoses_and_deaths(ran):
    conversion, _ = ran
    con = conversion.con
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    # Only the operations of cases that were carried out, each on the day of its anaesthetic.
    assert one("SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_datetime IS NULL") == one("""
        SELECT COUNT(*) FROM CASE_PROC cp JOIN THEATRE_CASE tc ON tc.CASE_KEY = cp.CASE_KEY
        JOIN omop.person p ON p.person_source_value = tc.PERSON_KEY WHERE tc.CASE_STATUS_CAT = 2 AND tc.CASE_DATE IS NOT NULL""")
    assert one("SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_datetime IS NULL") < one("SELECT COUNT(*) FROM CASE_PROC")
    assert one("""SELECT COUNT(*) FROM omop.procedure_occurrence s WHERE s.procedure_datetime IS NULL AND NOT EXISTS (
                    SELECT 1 FROM omop.procedure_occurrence a WHERE a.person_id = s.person_id
                    AND a.procedure_source_value = 'ANAESTHETIC' AND a.procedure_date = s.procedure_date)""") == 0
    # A diagnosis belongs to a visit and is dated to its start.
    assert one("SELECT COUNT(*) FROM omop.condition_occurrence") > 0
    assert one("""SELECT COUNT(*) FROM omop.condition_occurrence c JOIN omop.visit_occurrence vo USING (visit_occurrence_id)
                  WHERE c.condition_start_date <> vo.visit_start_date OR c.person_id <> vo.person_id""") == 0
    # Nearly everyone is alive, and nobody dies before they are born.
    deaths, people = one("SELECT COUNT(*) FROM omop.death"), one("SELECT COUNT(*) FROM omop.person")
    assert 0 < deaths < people / 10
    assert one("SELECT COUNT(*) FROM omop.death d JOIN omop.person p USING (person_id) WHERE d.death_datetime < p.birth_datetime") == 0
    assert one("SELECT COUNT(*) FROM (SELECT person_id FROM omop.death GROUP BY 1 HAVING COUNT(*) > 1)") == 0
    # The database describes itself in one row.
    assert con.execute("SELECT cdm_version, cdm_version_concept_id, vocabulary_version FROM omop.cdm_source").fetchall() == [("5.4", 756265, "not loaded")]


def test_every_step_has_a_layer_and_the_anaesthesia_layer_leaves_the_core_tables_alone():
    import json
    steps = json.loads((FIXTURES / "conversion" / "conversion.json").read_text())
    assert convert.layer_problems(steps) == []
    assert {s["layer"] for s in steps} == {"core", "anaesthesia", "derived"}
    # The anaesthesia layer finds its people and visits in the OMOP tables, and does not read the source table of people.
    for step in steps:
        if step["layer"] == "anaesthesia":
            sql = (FIXTURES / "conversion" / step["file"]).read_text().upper()
            assert "PERSON_MASTER" not in sql, step["file"]
    assert convert.layer_problems([{"table": "person", "file": "x.sql", "layer": "anaesthesia"}]) == [
        "x.sql: an anaesthesia step may not write person, which belongs to the core"]
    assert convert.layer_problems([{"table": "measurement", "file": "a.sql", "layer": "anaesthesia"}, {"table": "person", "file": "b.sql", "layer": "core"}]) == [
        "a core step follows an anaesthesia step: the core is refreshed first, and knows nothing of the anaesthesia layer"]
    assert convert.layer_problems([{"table": "measurement", "file": "y.sql"}]) == [
        "y.sql: the step names no layer, and must be one of core, anaesthesia or derived"]


def test_anaesthesia_rows_are_numbered_in_a_range_of_their_own(ran):
    conversion, _ = ran
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    offset = convert.IDENTIFIER_OFFSET
    # The core's operations and single doses stay below the offset, and every anaesthetic, event and infusion sits above it.
    assert one("SELECT MAX(procedure_occurrence_id) FROM omop.procedure_occurrence WHERE procedure_datetime IS NULL") < offset
    assert one("SELECT MIN(procedure_occurrence_id) FROM omop.procedure_occurrence WHERE procedure_datetime IS NOT NULL") == offset + 1
    assert one("SELECT MAX(drug_exposure_id) FROM omop.drug_exposure WHERE sig IS NULL") < offset
    assert one("SELECT MIN(drug_exposure_id) FROM omop.drug_exposure WHERE sig IS NOT NULL") == offset + 1
    # A later step for the same table numbers on from the highest identifier the layer has written, and not from the core's.
    anaesthetics = one("SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_source_value = 'ANAESTHETIC'")
    assert one("SELECT MAX(procedure_occurrence_id) FROM omop.procedure_occurrence WHERE procedure_source_value = 'ANAESTHETIC'") == offset + anaesthetics
    assert one("""SELECT MIN(procedure_occurrence_id) FROM omop.procedure_occurrence
                  WHERE procedure_source_value <> 'ANAESTHETIC' AND procedure_datetime IS NOT NULL""") == offset + anaesthetics + 1
    for table, key in (("procedure_occurrence", "procedure_occurrence_id"), ("drug_exposure", "drug_exposure_id"),
                       ("measurement", "measurement_id"), ("visit_detail", "visit_detail_id")):
        assert one(f"SELECT COUNT(*) FROM (SELECT {key} FROM omop.{table} GROUP BY 1 HAVING COUNT(*) > 1)") == 0
    # A reference from one anaesthesia row to another is read back from the table, and so carries the final identifier.
    assert one("SELECT COUNT(*) FROM omop.measurement WHERE measurement_event_id <= ? OR visit_detail_id <= ?".replace("?", str(offset))) == 0
    assert one("""SELECT COUNT(*) FROM omop.measurement m JOIN omop.procedure_occurrence po ON po.procedure_occurrence_id = m.measurement_event_id
                  JOIN omop.visit_detail vd ON vd.visit_detail_id = m.visit_detail_id AND vd.visit_detail_id = po.visit_detail_id""") == one(
        "SELECT COUNT(measurement_event_id) FROM omop.measurement")
    assert one("SELECT COUNT(*) FROM omop.observation o LEFT JOIN omop.procedure_occurrence po ON po.procedure_occurrence_id = o.observation_event_id "
               "WHERE po.procedure_occurrence_id IS NULL") == 0


def test_the_quality_gates_pass_and_each_one_can_fail(ran):
    conversion, report = ran
    con = conversion.con
    assert len(report["gates"]) == 9 and all(gate["rows"] == 0 for gate in report["gates"]), report["gates"]
    rows = lambda: {g["gate"][:3]: g["rows"] for g in conversion.gates(FIXTURES / "conversion" / "gates")}  # noqa: E731
    # An anaesthetic for a person the core does not hold, which ends before it starts.
    con.execute("""INSERT INTO omop.visit_detail (visit_detail_id, person_id, visit_detail_concept_id, visit_detail_start_date,
                   visit_detail_start_datetime, visit_detail_end_date, visit_detail_end_datetime, visit_detail_type_concept_id, visit_occurrence_id)
                   VALUES (999999, -5, 9201, '2031-01-02', '2031-01-02 10:00:00', '2031-01-02', '2031-01-02 09:00:00', 32817, -5)""")
    # A procedure that repeats an identifier, and an anaesthetic outside every observation period.
    con.execute("""INSERT INTO omop.procedure_occurrence (procedure_occurrence_id, person_id, procedure_concept_id, procedure_date,
                   procedure_type_concept_id, procedure_source_value)
                   SELECT MIN(procedure_occurrence_id), MIN(person_id), 4249997, DATE '1950-01-01', 32817, 'ANAESTHETIC' FROM omop.procedure_occurrence""")
    con.execute("""INSERT INTO omop.measurement (measurement_id, person_id, measurement_concept_id, measurement_date, measurement_type_concept_id, measurement_event_id)
                   VALUES (-1, 1, 3027018, '2031-01-02', 32817, -777)""")
    try:
        assert rows() == {"010": 1, "020": 1, "030": 1, "040": 1, "050": 1, "060": 0, "070": 0, "080": 0, "090": 0}
    finally:
        con.execute("DELETE FROM omop.visit_detail WHERE visit_detail_id = 999999")
        con.execute("DELETE FROM omop.procedure_occurrence WHERE procedure_date = DATE '1950-01-01'")
        con.execute("DELETE FROM omop.measurement WHERE measurement_id = -1")
    assert all(count == 0 for count in rows().values())


def test_a_run_that_is_not_clean_is_a_failure_and_the_command_exits_with_1(ran, tmp_path, monkeypatch):
    _, report = ran
    assert convert.failures(report) == []
    broken = dict(report, steps=report["steps"] + [{"table": "measurement", "status": "database-error", "rows": 0}],
                  gates=report["gates"] + [{"gate": "x.sql", "rows": None}, {"gate": "y.sql", "rows": 2}],
                  problems=["measurement.measurement_id: 1 values of the primary key are repeated"])
    assert convert.failures(broken) == ["step 23 (measurement): database-error", "gate x.sql: could not be run",
                                        "gate y.sql: failed, with 2 rows",
                                        "problem: measurement.measurement_id: 1 values of the primary key are repeated"]
    world = tmp_path / "world"
    world.mkdir()
    (world / "requests").mkdir()
    (world / "catalogue.csv").write_text("")
    for outcome, code in ((report, 0), (broken, 1)):
        monkeypatch.setattr(convert, "run", lambda *args, outcome=outcome, **kwargs: (ran[0], outcome))
        monkeypatch.setattr(sys, "argv", ["convert", str(world), str(FIXTURES / "conversion"), "--out", str(tmp_path / "out")])
        assert convert.main() == code


def test_what_could_not_be_read_or_derived_is_reported(ran, tmp_path):
    conversion, _ = ran
    problems = []
    convert.as_request([("measurement", "SELECT FROM WHERE ((("), ("person", "SELECT 1 AS person_id")], problems=problems)
    assert len(problems) == 1 and problems[0].startswith("measurement: the analysis could not read this step as one SELECT")
    summary = convert.derive_mappings(conversion, [
        {"vocabulary": "SITE_NOTHING", "table": "NO_SUCH_TABLE", "code": "A", "label": "B"},
        {"vocabulary": "SITE_HALF", "table": "DRUG_DEF", "code": "DRUG_KEY", "label": "NO_SUCH_COLUMN"}], tmp_path)
    assert [item["problem"] for item in summary] == [
        "SITE_NOTHING: the runner has derived no mapping rows, because the catalogue does not hold NO_SUCH_TABLE.",
        "SITE_HALF: the runner has derived no mapping rows, because the catalogue does not hold DRUG_DEF.NO_SUCH_COLUMN."]


def test_the_sandbox_can_be_built_from_a_hospital_s_own_check_results(monkeypatch):
    def no_truth(*args, **kwargs):
        raise AssertionError("the stand-in database was read although check results were given")

    monkeypatch.setattr(make_checks.WORLD, "truth", no_truth)
    conversion, report = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=200, checks=FIXTURES / "invented-checks.csv")
    assert all(step["status"] == "ok" for step in report["steps"]), report["steps"]
    assert conversion.count("visit_detail") > 0
    text = (FIXTURES / "invented-checks.csv").read_text()
    _, again = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=200, checks=text)
    assert [step["rows"] for step in again["steps"]] == [step["rows"] for step in report["steps"]]


# Each variant below alters the stand-in core after the core steps and before the anaesthesia steps, as a real core might
# differ from the guess that the core steps make. Each must either work or fail loudly, and never pass over empty tables.

def _variant(change):
    return convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=300, between=change)


def test_a_core_that_hashes_its_visit_source_value_fails_loudly():
    conversion, report = _variant(lambda c: c.con.execute("UPDATE omop.visit_occurrence SET visit_source_value = md5(visit_source_value)"))
    assert conversion.count("visit_detail") == 0 and all(step["status"] == "ok" for step in report["steps"])
    gates = {gate["gate"][:3]: gate["rows"] for gate in report["gates"]}
    assert gates["060"] == 1 and gates["070"] == 1
    # The planted scenarios find no anaesthetic either, so their expectations are not met, as they should not be.
    assert {failure[:8] for failure in convert.failures(report)} == {"gate 060", "gate 070", "scenario"}


def _core_visit_details(first):
    return lambda c: c.con.execute(f"""
        INSERT INTO omop.visit_detail (visit_detail_id, person_id, visit_detail_concept_id, visit_detail_start_date,
            visit_detail_end_date, visit_detail_type_concept_id, visit_occurrence_id, visit_detail_source_value)
        SELECT {first} + ROW_NUMBER() OVER (ORDER BY visit_occurrence_id), person_id, 9201, visit_start_date, visit_end_date,
               32817, visit_occurrence_id, 'WARD STAY ' || visit_occurrence_id
        FROM omop.visit_occurrence ORDER BY visit_occurrence_id LIMIT 20""")


def test_a_core_that_already_holds_visit_details_works_below_the_offset_and_fails_loudly_above_it():
    offset = convert.IDENTIFIER_OFFSET
    conversion, report = _variant(_core_visit_details(offset - 21))
    assert convert.failures(report) == []
    assert conversion.con.execute("SELECT COUNT(*) FROM omop.visit_detail WHERE visit_detail_id > ?", [offset]).fetchone()[0] > 0
    conversion, report = _variant(_core_visit_details(offset - 5))
    failures = convert.failures(report)
    assert any(f.startswith("problem: visit_detail.visit_detail_id: the core already holds the identifier") for f in failures)
    assert any(f.startswith("gate 030") for f in failures)


def test_a_core_of_cdm_5_3_fails_loudly():
    dropped = {"procedure_occurrence": ("procedure_end_date", "procedure_end_datetime"),
               "measurement": ("measurement_event_id", "meas_event_field_concept_id"),
               "observation": ("observation_event_id", "obs_event_field_concept_id")}

    def older(conversion):
        for table, fields in dropped.items():
            for field in fields:
                conversion.con.execute(f"ALTER TABLE omop.{table} DROP COLUMN {field}")

    _, report = _variant(older)
    failures = convert.failures(report)
    assert "problem: procedure_occurrence: the core's table lacks procedure_end_date, procedure_end_datetime, which the published views of CDM 5.4 read." in failures
    assert any(f.startswith("problem: measurement: the core's table lacks measurement_event_id") for f in failures)
    assert any(f.endswith("(procedure_occurrence): database-error") for f in failures)


def test_a_core_whose_identifiers_sit_just_below_the_int_limit_works():
    top = 2 ** 31 - 1 - 10_000

    def crowded(conversion):
        for table, fields in convert.cdm_fields().items():
            names = {name for name, _, _ in fields}
            moved = [f'"{key}" = "{key}" + {top}' for key in ("person_id", "visit_occurrence_id", "procedure_occurrence_id", "drug_exposure_id")
                     if key in names]
            if moved and conversion.count(table):
                conversion.con.execute(f"UPDATE omop.{table} SET {', '.join(moved)}")

    conversion, report = _variant(crowded)
    assert convert.failures(report) == []
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    assert one("SELECT MIN(visit_occurrence_id) FROM omop.visit_detail") > top
    assert one("SELECT MIN(procedure_occurrence_id) FROM omop.procedure_occurrence WHERE procedure_source_value = 'ANAESTHETIC'") == convert.IDENTIFIER_OFFSET + 1


# The derived layer and its custom tables.

def test_a_step_s_alternatives_are_checked_with_the_layers():
    step = {"table": "visit_detail", "file": "a.sql", "layer": "anaesthesia"}
    assert convert.layer_problems([dict(step, alternatives=["b.sql", {"file": "c.sql", "table": "visit_detail"}])]) == []
    for offered in ("b.sql", ["a.sql"], ["b.sql", "b.sql"], ["../b.sql"], [{"file": "b.sql", "table": "measurement"}],
                    [{"file": "b.sql", "layer": "core"}], [{"file": "b.sql", "where": "x"}], [3]):
        assert convert.layer_problems([dict(step, alternatives=offered)]), offered
    steps = [dict(step, alternatives=["b.sql"]), {"table": "measurement", "file": "m.sql", "layer": "anaesthesia"}]
    assert convert.alternatives(steps[0]) == ["b.sql"] and convert.alternatives(steps[1]) == []
    assert [s["file"] for s in convert.with_alternatives(steps, ["b.sql"])] == ["b.sql", "m.sql"]
    assert convert.with_alternatives(steps, None) == steps
    with pytest.raises(ValueError):
        convert.with_alternatives(steps, ["m.sql"])


def test_the_command_runs_a_named_alternative(tmp_path, monkeypatch):
    world = tmp_path / "world"
    world.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", world / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", world / "site-rules.json")
    shutil.copytree(FIXTURES / "requests", world / "requests")
    monkeypatch.setattr(sys, "argv", ["convert", str(world), str(FIXTURES / "conversion"), "--out", str(tmp_path / "out"),
                                      "--rows", "200", "--no-scenarios", "--alternative", "visit_detail.sql"])
    assert convert.main() == 0
    monkeypatch.setattr(sys, "argv", ["convert", str(world), str(FIXTURES / "conversion"), "--out", str(tmp_path / "out"),
                                      "--rows", "200", "--alternative", "no_such_step.sql"])
    with pytest.raises(SystemExit):
        convert.main()


def test_the_derived_layer_runs_last_and_writes_only_custom_tables():
    custom = {"anaesthetic", "anaesthetic_phase"}
    good = [{"table": "person", "file": "a.sql", "layer": "core"}, {"table": "measurement", "file": "b.sql", "layer": "anaesthesia"},
            {"table": "anaesthetic", "file": "c.sql", "layer": "derived"}]
    assert convert.layer_problems(good, custom) == []
    assert convert.layer_problems([good[2], good[1]], custom) == [
        "b.sql: this anaesthesia step follows a derived step, and the layers run in the order core, anaesthesia, derived."]
    assert convert.layer_problems([good[2], good[0]], custom) == [
        "a.sql: this core step follows a derived step, and the layers run in the order core, anaesthesia, derived."]
    assert convert.layer_problems([{"table": "measurement", "file": "d.sql", "layer": "derived"}], custom) == [
        "d.sql: a derived step writes a custom table that tables.json defines, and measurement is not one of them."]
    assert convert.layer_problems([{"table": "anaesthetic", "file": "e.sql", "layer": "anaesthesia"}], custom) == [
        "e.sql: only a derived step may write anaesthetic, because it is a custom table."]


def test_the_runner_refuses_steps_in_the_wrong_order(tmp_path):
    import json
    import shutil
    folder = tmp_path / "conversion"
    shutil.copytree(FIXTURES / "conversion", folder)
    steps = json.loads((folder / "conversion.json").read_text())
    (folder / "conversion.json").write_text(json.dumps(steps[:10] + steps[-2:] + steps[10:-2]))
    with pytest.raises(ValueError, match="follows a derived step"):
        convert.run(make_checks.WORLD, folder, rows=50)


def _tables(tmp_path, change):
    import copy
    import json
    tables = json.loads((FIXTURES / "conversion" / "tables.json").read_text())
    tables = copy.deepcopy(tables)
    change(tables)
    folder = tmp_path / "hostile"
    folder.mkdir(exist_ok=True)
    (folder / "tables.json").write_text(json.dumps(tables))
    return folder


HOSTILE_TABLES = [
    lambda t: t[0].update(name="person"),                                       # a table of CDM 5.4
    lambda t: t[0].update(name="MEASUREMENT"),
    lambda t: t[0].update(name="anaesthetic]; DROP TABLE dbo.person; --"),
    lambda t: t[0].update(name="a b"),
    lambda t: t[0].update(name="1anaesthetic"),
    lambda t: t[0].update(name="x" * 64),
    lambda t: t[1].update(name="anaesthetic"),                                  # two tables of one name
    lambda t: t[0].update(description="One line.\n:!! ls"),
    lambda t: t[0].update(description="The schema is $(OmopSchemaName)."),
    lambda t: t[0].update(description=""),
    lambda t: t[0].update(extra=1),
    lambda t: t[0]["fields"][0].update(name="anaesthetic_id]] BIGINT); DROP TABLE x; --"),
    lambda t: t[0]["fields"][1].update(name="anaesthetic_id"),                  # a field named twice
    lambda t: t[0]["fields"][0].update(type="BIGINT; DROP TABLE x"),
    lambda t: t[0]["fields"][0].update(type="varchar(max)"),
    lambda t: t[0]["fields"][0].update(type="varchar(9000)"),
    lambda t: t[0]["fields"][0].update(type="nvarchar(10)"),
    lambda t: t[0]["fields"][0].update(required="yes"),
    lambda t: t[0]["fields"][0].update(required=False),                         # a primary key must be required
    lambda t: t[0]["fields"][1].update(primary_key=True),                       # two primary keys
    lambda t: t[0]["fields"][2].update(description="A rule\r\nGO\nDROP TABLE x"),
    lambda t: t[0].update(fields=[]),
    lambda t: t.append("anaesthetic"),
]


@pytest.mark.parametrize("change", HOSTILE_TABLES)
def test_a_tables_file_that_breaks_a_rule_is_refused(tmp_path, change):
    with pytest.raises(convert.TablesError):
        convert.read_tables(_tables(tmp_path, change))


def test_the_custom_tables_are_read_from_the_folder():
    tables = convert.read_tables(FIXTURES / "conversion")
    assert [table["name"] for table in tables] == ["anaesthetic", "anaesthetic_phase"]
    assert [f["name"] for f in tables[0]["fields"]][:3] == ["anaesthetic_id", "person_id", "visit_occurrence_id"]
    assert all(f["description"].endswith(".") for table in tables for f in table["fields"])
    # The realistic world's conversion defines the same tables, because its derived steps read only OMOP tables.
    private = FIXTURES.parent / "etl" / "clarity"
    if private.exists():
        assert convert.read_tables(private) == tables
        for name in ("anaesthetic.sql", "anaesthetic_phase.sql"):
            assert (private / name).read_text() == (FIXTURES / "conversion" / name).read_text()


def _completed(birth, start):
    """Whole years and months from a birth to a start, by calendar date, counted as a person would count them."""
    months = (start.year - birth.year) * 12 + start.month - birth.month - (1 if start.day < birth.day else 0)
    years = start.year - birth.year - (1 if (start.month, start.day) < (birth.month, birth.day) else 0)
    return years, months


def _age(birth, start):
    """The anaesthetic step's own expressions for age_years and age_months, run in the sandbox on one birth and one start."""
    import duckdb
    import sqlglot
    from schemalyser.translate import to_duckdb
    step = sqlglot.parse_one((FIXTURES / "conversion" / "anaesthetic.sql").read_text(), dialect="tsql")
    found = {p.alias: p.unalias().sql(dialect="tsql") for p in step.expressions if p.alias in ("age_years", "age_months")}
    sql = (f"SELECT {found['age_years']} AS age_years, {found['age_months']} AS age_months "
           f"FROM (SELECT CAST('{start}' AS datetime2) AS procedure_datetime) po "
           f"CROSS JOIN (SELECT CAST('{birth}' AS datetime2) AS birth_datetime) p")
    return duckdb.connect().execute(to_duckdb(sql)[0]).fetchone()


@pytest.mark.parametrize("birth, start, years, months", [
    ("2023-05-10 22:00", "2024-05-09 23:59", 0, 11),     # the day before the first birthday
    ("2023-05-10 22:00", "2024-05-10 00:01", 1, 12),     # the first birthday, before the hour of birth
    ("2023-05-10", "2023-06-09", 0, 0),                  # a day short of a month
    ("2023-01-31", "2023-02-28", 0, 0),                  # a month without the day of birth
    ("2023-01-31", "2023-03-01", 0, 1),
    ("2020-02-29", "2021-02-28", 0, 11),                 # born on 29 February: one year old on 1 March
    ("2020-02-29", "2021-03-01", 1, 12),
    ("2020-02-29", "2024-02-29", 4, 48),                 # and on the birthday itself in a leap year
    ("2019-12-31", "2020-01-01", 0, 0),                  # a year boundary is not a year
])
def test_age_is_counted_in_completed_years_and_months_by_calendar_date(birth, start, years, months):
    assert _age(birth, start) == (years, months)
    from datetime import datetime
    assert _completed(datetime.fromisoformat(birth), datetime.fromisoformat(start)) == (years, months)


def test_the_anaesthetic_table_holds_one_row_for_each_anaesthetic(ran):
    conversion, _ = ran
    one = lambda sql: conversion.con.execute(sql).fetchone()[0]  # noqa: E731
    anaesthetics = one("SELECT COUNT(*) FROM omop.procedure_occurrence WHERE procedure_source_value = 'ANAESTHETIC'")
    assert one("SELECT COUNT(*) FROM omop.anaesthetic") == one("SELECT COUNT(DISTINCT anaesthetic_id) FROM omop.anaesthetic") == anaesthetics > 0
    # Each row repeats its procedure row, and its age and duration follow from it.
    assert one("""SELECT COUNT(*) FROM omop.anaesthetic a JOIN omop.procedure_occurrence po ON po.procedure_occurrence_id = a.anaesthetic_id
                  JOIN omop.person p ON p.person_id = a.person_id
                  WHERE po.procedure_concept_id = a.anaesthesia_type_concept_id AND po.visit_detail_id = a.visit_detail_id
                    AND po.procedure_datetime = a.start_datetime AND a.duration_minutes >= 0
                    AND a.age_days = date_diff('day', p.birth_datetime, a.start_datetime)""") == anaesthetics
    # The ASA class is the anaesthetic's own, and the weight is the latest in kilograms from a week before the start to the end.
    assert one("""SELECT COUNT(*) FROM omop.anaesthetic a JOIN omop.measurement m ON m.measurement_event_id = a.anaesthetic_id
                  WHERE m.measurement_concept_id = 4199571 AND m.value_as_concept_id = a.asa_class_concept_id""") == anaesthetics
    weighed = one("SELECT COUNT(weight_kg) FROM omop.anaesthetic")
    assert weighed > 0 and weighed == one("""SELECT COUNT(DISTINCT a.anaesthetic_id) FROM omop.anaesthetic a JOIN omop.measurement w
        ON w.person_id = a.person_id AND w.measurement_concept_id = 3025315 AND w.unit_concept_id = 9529
       AND w.measurement_datetime BETWEEN a.start_datetime - INTERVAL 7 DAY AND a.end_datetime""")
    assert one("""SELECT COUNT(*) FROM omop.anaesthetic a WHERE a.weight_kg IS NOT NULL AND a.weight_kg <> (
                    SELECT w.value_as_number FROM omop.measurement w WHERE w.person_id = a.person_id AND w.measurement_concept_id = 3025315
                    AND w.measurement_datetime BETWEEN a.start_datetime - INTERVAL 7 DAY AND a.end_datetime
                    ORDER BY w.measurement_datetime DESC, w.measurement_id DESC LIMIT 1)""") == 0
    # The completed years and months follow the calendar date, as a person would count them.
    for birth, start, years, months in conversion.con.execute(
            "SELECT p.birth_datetime, a.start_datetime, a.age_years, a.age_months FROM omop.anaesthetic a "
            "JOIN omop.person p ON p.person_id = a.person_id").fetchall():
        assert (years, months) == _completed(birth, start)
    # One phase, the whole anaesthetic, for each anaesthetic.
    assert one("SELECT COUNT(*) FROM omop.anaesthetic_phase WHERE phase_name = 'whole anaesthetic' AND phase_sequence = 1") == anaesthetics
    assert one("SELECT COUNT(DISTINCT phase_rule) FROM omop.anaesthetic_phase") == 1


def test_the_custom_tables_are_checked_like_the_cdm_s_and_their_gates_can_fail(ran):
    conversion, _ = ran
    con = conversion.con
    assert conversion.step("anaesthetic", "SELECT 1 AS anaesthetic_id, 2 AS shoe_size", "derived")["status"] == "unknown-fields"
    assert conversion.step("anaesthetic", "SELECT 1 AS person_id", "derived")["status"] == "no-identifier"
    gates = lambda: {g["gate"][:3]: g["rows"] for g in conversion.gates(FIXTURES / "conversion" / "gates")}  # noqa: E731
    first = con.execute("SELECT MIN(anaesthetic_id) FROM omop.anaesthetic").fetchone()[0]
    # A second row for one anaesthetic repeats the primary key, and a phase with a name too long for its field.
    con.execute("INSERT INTO omop.anaesthetic SELECT * FROM omop.anaesthetic WHERE anaesthetic_id = ?", [first])
    con.execute("""INSERT INTO omop.anaesthetic_phase (anaesthetic_phase_id, anaesthetic_id, phase_sequence, phase_name, start_datetime, phase_rule, phase_rule_version)
                   VALUES (-1, -1, 1, ?, TIMESTAMP '2031-01-01', 'none', 1)""", ["x" * 51])
    try:
        found = conversion.problems()
        assert "anaesthetic.anaesthetic_id: 1 values of the primary key are repeated" in found
        assert "anaesthetic_phase.phase_name: 1 rows hold text longer than the 50 characters the field allows" in found
        assert gates()["080"] == 1 and gates()["090"] == 1
    finally:
        con.execute("DELETE FROM omop.anaesthetic_phase WHERE anaesthetic_phase_id = -1")
        con.execute("DELETE FROM omop.anaesthetic WHERE anaesthetic_id = ? AND rowid = (SELECT MAX(rowid) FROM omop.anaesthetic WHERE anaesthetic_id = ?)",
                    [first, first])
    con.execute("UPDATE omop.anaesthetic SET person_id = NULL WHERE anaesthetic_id = ?", [first])
    try:
        assert "anaesthetic.person_id: 1 rows have no value in a required field" in conversion.problems()
    finally:
        con.execute("UPDATE omop.anaesthetic SET person_id = (SELECT person_id FROM omop.procedure_occurrence WHERE procedure_occurrence_id = ?) "
                    "WHERE anaesthetic_id = ?", [first, first])
    assert conversion.problems() == [] and all(count == 0 for count in gates().values())


# Every reading of the anaesthetic.

def test_every_accepted_numeric_reading_of_the_anaesthetic_is_written_mapped_or_not(ran):
    conversion, report = ran
    con = conversion.con
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    unmapped = report["unmapped"]
    assert unmapped == {"rows": one("SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 0"),
                        "variables": one("SELECT COUNT(DISTINCT measurement_source_value) FROM omop.measurement WHERE measurement_concept_id = 0")}
    assert unmapped["rows"] > 0 and unmapped["variables"] > 0
    # Each unmapped row belongs to an anaesthetic, keeps its observation type, and holds a number.
    assert one("""SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 0
                  AND (measurement_event_id IS NULL OR measurement_source_value IS NULL OR value_as_number IS NULL OR unit_concept_id <> 0)""") == 0
    # Every accepted numeric reading on an anaesthetic's sheet is written once, and a reading that was not accepted is not.
    sheet = """FROM OBS_READING r JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY
               JOIN omop.visit_occurrence vo ON vo.visit_source_value = CAST(s.VISIT_KEY AS VARCHAR)
               JOIN omop.visit_detail vd ON vd.visit_detail_source_value = CAST(s.ANAES_KEY AS VARCHAR)
               WHERE TRY_CAST(r.READ_VALUE AS DOUBLE) IS NOT NULL AND r.READ_TS IS NOT NULL"""
    written = """SELECT COUNT(*) FROM omop.measurement m WHERE m.visit_detail_id IS NOT NULL
                 AND m.measurement_concept_id NOT IN (4199571, 3004249, 3012888)"""
    assert one(written) == one(f"SELECT COUNT(*) {sheet} AND COALESCE(r.ACCEPTED_FLAG, 'Y') <> 'N'")
    assert one(f"SELECT COUNT(*) {sheet} AND r.ACCEPTED_FLAG = 'N'") > 0
    # A text value without a mapping row that gives it a meaning is not written, and the charted pressure is split as before.
    assert one("SELECT COUNT(*) FROM omop.measurement WHERE measurement_source_value = '5' AND measurement_concept_id NOT IN (3004249, 3012888)") == 0


def test_a_mapping_row_gives_unmapped_rows_their_concept_on_the_next_run(ran):
    from schemalyser.translate import to_duckdb
    conversion, _ = ran
    con = conversion.con
    variable, rows = con.execute("""SELECT measurement_source_value, COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 0
                                    GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1""").fetchone()
    sql = to_duckdb((FIXTURES / "conversion" / "measurement.sql").read_text(), conversion.sandbox.date_columns)[0]
    before = con.execute(f"SELECT COUNT(*) FROM ({sql}) AS step").fetchone()[0]
    con.execute("INSERT INTO omop.source_to_concept_map (source_code, source_concept_id, source_vocabulary_id, target_concept_id, "
                "target_vocabulary_id, valid_start_date, valid_end_date) VALUES (?, 0, 'SITE_OBS', 3024171, 'LOINC', DATE '1970-01-01', DATE '2099-12-31')",
                [variable])
    try:
        after = con.execute(f"SELECT measurement_concept_id, COUNT(*) FROM ({sql}) AS step WHERE measurement_source_value = ? GROUP BY 1",
                            [variable]).fetchall()
        # The anaesthetic's readings of that variable now take its concept, and its readings elsewhere join them.
        assert after[0][0] == 3024171 and len(after) == 1 and after[0][1] >= rows
        assert con.execute(f"SELECT COUNT(*) FROM ({sql}) AS step").fetchone()[0] == before + after[0][1] - rows
    finally:
        con.execute("DELETE FROM omop.source_to_concept_map WHERE source_vocabulary_id = 'SITE_OBS' AND source_code = ?", [variable])
    worklist = list(csv.reader(io.StringIO(conversion.unmapped_worklist())))
    assert worklist[0] == ["measurement_source_value", "rows", "anaesthetics"] and worklist[1][:2] == [variable, str(rows)]


# Planted scenarios.

DEFAULT_SCENARIOS = {"age_by_calendar_date", "airway_still_in_place", "anaesthetic_across_midnight", "anaesthetic_without_stop",
                     "cuff_reading_during_arterial_line",
                     "anaesthetic_without_theatre_case", "event_link_margin",
                     "infant_systolic_cases", "infusion_never_stopped", "infusion_rate_changes_twice", "long_anaesthetic",
                     "malformed_blood_pressure", "neonatal_mean_pressure_minutes", "neonatal_mean_pressure_who_counts",
                     "reading_entered_twice", "stop_before_start", "test_patient",
                     "two_anaesthetics_one_stay", "weight_after_anaesthetic"}
PRIVATE = FIXTURES.parent / "etl" / "clarity"


def test_every_planted_scenario_meets_its_expectations_and_the_gates_still_pass(planted):
    _, report = planted
    assert {s["name"] for s in report["scenarios"]} == DEFAULT_SCENARIOS
    for scenario in report["scenarios"]:
        assert scenario["error"] is None and scenario["planted"] > 0, scenario["name"]
        assert scenario["expectations"] and all(item["met"] for item in scenario["expectations"]), scenario
    assert all(gate["rows"] == 0 for gate in report["gates"]) and convert.failures(report) == []
    long_one = next(s for s in report["scenarios"] if s["name"] == "long_anaesthetic")
    assert long_one["planted"] > 3000


def test_the_run_counts_the_anaesthetics_that_it_leaves_out(ran, planted):
    # The anaesthetic whose stop is before its start, and the one without a theatre case, are left out and counted, by number only.
    names = ["010_anaesthetics_whose_stop_is_before_their_start.sql", "020_anaesthetics_without_a_hospital_visit.sql"]
    before = {count["name"]: count["rows"] for count in ran[1]["counts"]}
    after = {count["name"]: count["rows"] for count in planted[1]["counts"]}
    assert list(before) == list(after) == names
    assert all(count["error"] is None for count in planted[1]["counts"])
    assert after[names[0]] == before[names[0]] + 1
    # The scenarios add an anaesthetic without a theatre case and an anaesthetic of a test patient.
    assert after[names[1]] == before[names[1]] + 2
    sentence = convert.report_count(planted[1]["counts"][0]["says"], 1234)
    assert sentence == ("The number of anaesthetics that the anaesthesia layer has left out because the recorded stop is "
                        "before the recorded start is 1,234.")


def test_a_count_that_breaks_a_rule_is_refused_and_one_that_fails_fails_the_run(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(FIXTURES / "conversion", folder)
    path = folder / "counts" / "030_extra.sql"
    path.write_text("-- This sentence has no place for the number.\nSELECT COUNT(*) AS n FROM ANAES_RECORD ar\n")
    with pytest.raises(ValueError):
        convert.read_counts(folder)
    with pytest.raises(ValueError):
        convert.run(make_checks.WORLD, folder, rows=50, scenarios=[])
    path.write_text("-- The number of keys is {count}.\nSELECT ar.ANAES_KEY AS n FROM ANAES_RECORD ar\n")
    _, report = convert.run(make_checks.WORLD, folder, rows=100, scenarios=[])
    assert any(f.startswith("count 030_extra.sql: could not be run") for f in convert.failures(report))


def test_an_anaesthetic_without_a_stop_is_written_with_an_empty_end(planted):
    conversion, _ = planted
    one = lambda sql: conversion.con.execute(sql).fetchall()  # noqa: E731
    # Every anaesthetic that is written, and its whole phase, has an end where the source records a stop.
    assert one("""SELECT COUNT(*) FROM omop.visit_detail vd JOIN omop.anaesthetic a ON a.visit_detail_id = vd.visit_detail_id
                  WHERE (vd.visit_detail_end_datetime IS NULL) <> (a.end_datetime IS NULL)""") == [(0,)]
    assert one("SELECT COUNT(*) FROM omop.visit_detail WHERE visit_detail_end_date IS NULL") == [(0,)]
    # One anaesthetic without a stop is planted for this case, and one for the neonatal question.
    assert one("SELECT COUNT(*) FROM omop.visit_detail WHERE visit_detail_end_datetime IS NULL") == [(2,)]


def test_a_scenario_that_exists_to_make_a_gate_fail_runs_only_when_named():
    scenarios = convert.read_scenarios(FIXTURES / "conversion")
    marked = [s for s in scenarios if s["fails_gate"]]
    assert [s["name"] for s in marked] == ["anaesthetic_listed_twice"]
    assert "anaesthetic_listed_twice" not in {s["name"] for s in convert.chosen_scenarios(FIXTURES / "conversion")}
    _, report = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=200, scenarios=["anaesthetic_listed_twice"])
    (scenario,) = report["scenarios"]
    assert all(item["met"] for item in scenario["expectations"])
    failed = {gate["gate"] for gate in report["gates"] if gate["rows"]}
    assert marked[0]["fails_gate"] in failed
    assert any(f.startswith("gate 030_identifiers_are_not_repeated.sql: failed") for f in convert.failures(report))


def test_no_scenario_is_planted_when_none_is_asked_for():
    _, report = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=200, scenarios=[])
    assert report["scenarios"] == [] and convert.failures(report) == []
    with pytest.raises(convert.ScenarioError):
        convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=200, scenarios=["no_such_scenario"])


def test_the_sandbox_never_generates_an_identifier_in_the_range_kept_for_planted_rows():
    conversion, _ = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=400, scenarios=[])
    low, high = convert.SCENARIO_IDENTIFIERS
    for table in conversion.sandbox.tables:
        for (column,) in conversion.con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'main' AND table_name = ?", [table]).fetchall():
            found = conversion.con.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE TRY_CAST(CAST("{column}" AS VARCHAR) AS DOUBLE) BETWEEN {low} AND {high}').fetchone()[0]
            assert found == 0, (table, column)


def test_an_expectation_that_is_not_met_fails_the_run_and_the_command(tmp_path, monkeypatch):
    folder = tmp_path / "conversion"
    shutil.copytree(FIXTURES / "conversion", folder)
    for path in (folder / "scenarios").iterdir():
        if path.name != "reading_entered_twice":
            shutil.rmtree(path)
    spec = json.loads((folder / "scenarios" / "reading_entered_twice" / "scenario.json").read_text())
    spec["expectations"][0]["result"] = [1, 1]
    (folder / "scenarios" / "reading_entered_twice" / "scenario.json").write_text(json.dumps(spec))
    _, report = convert.run(make_checks.WORLD, folder, rows=200)
    (item,) = report["scenarios"][0]["expectations"]
    assert not item["met"] and item["found"] == "2, 1" and item["expected"] == "1, 1"
    assert convert.failures(report) == [f"scenario reading_entered_twice: not met: {item['says']}"]
    world = tmp_path / "world"
    world.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", world / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", world / "site-rules.json")
    shutil.copytree(FIXTURES / "requests", world / "requests")
    monkeypatch.setattr(sys, "argv", ["convert", str(world), str(folder), "--out", str(tmp_path / "out"), "--rows", "200"])
    assert convert.main() == 1


@pytest.mark.parametrize("rows, message", [
    ("DELETE FROM OBS_READING;", "only INSERT statements"),
    ("INSERT INTO omop.person (person_id) VALUES (990000001);", "only INSERT statements"),
    ("INSERT INTO OBS_SHEET (SHEET_KEY) VALUES ('123456789');", "outside the range"),
])
def test_rows_that_break_a_rule_are_refused(tmp_path, rows, message):
    folder = tmp_path / "conversion"
    shutil.copytree(FIXTURES / "conversion", folder)
    (folder / "scenarios" / "test_patient" / "rows.sql").write_text(rows)
    with pytest.raises(convert.ScenarioError, match=message):
        convert.read_scenarios(folder)


@pytest.mark.parametrize("rows", [
    "INSERT INTO OBS_SHEET (SHEET_KEY) SELECT a FROM OPENDATASOURCE('SQLNCLI', 'Data Source=elsewhere').db.dbo.t;",
    "INSERT INTO OBS_SHEET (SHEET_KEY) SELECT * FROM OPENQUERY(elsewhere, 'SELECT 1');",
    "INSERT INTO db.dbo.OBS_SHEET (SHEET_KEY) VALUES ('990000001');",
    "INSERT INTO server.db.dbo.OBS_SHEET (SHEET_KEY) VALUES ('990000001');",
    "INSERT INTO dbo.OBS_SHEET (SHEET_KEY) VALUES ('990000001');",
    "INSERT INTO OBS_SHEET (SHEET_KEY) VALUES (SUSER_SNAME());",
    "INSERT INTO OBS_SHEET (SHEET_KEY) SELECT CAST(person_id AS varchar(20)) FROM omop.person;",
    "INSERT INTO OBS_SHEET (SHEET_KEY) VALUES ((SELECT TOP 1 person_source_value FROM omop.person));",
    "INSERT INTO OBS_SHEET (SHEET_KEY) VALUES ('a\n:!! rm -rf /\n');",
    "INSERT INTO OBS_SHEET (SHEET_KEY) VALUES ('a\nGO\nDROP TABLE OBS_SHEET\n');",
    "INSERT INTO OBS_SHEET (SHEET_KEY) VALUES ('$(SourcePrefix)');",
    "INSERT INTO OBS_SHEET VALUES ('990000001');",
    "INSERT INTO OBS_SHEET (SHEET_KEY) SELECT SHEET_KEY FROM OBS_SHEET;",
    "INSERT INTO OBS_SHEET (SHEET_KEY) SELECT CAST(k.n AS varchar(10)) FROM (VALUES (0)) AS k(n) JOIN OBS_SHEET s ON 1 = 1;",
])
def test_rows_that_are_not_plain_inserts_of_literals_are_refused(tmp_path, rows):
    folder = tmp_path / "conversion"
    shutil.copytree(FIXTURES / "conversion", folder)
    (folder / "scenarios" / "test_patient" / "rows.sql").write_text(rows)
    with pytest.raises(convert.ScenarioError):
        convert.read_scenarios(folder)


def test_rows_into_a_table_that_the_catalogue_does_not_name_are_not_planted(tmp_path):
    from types import SimpleNamespace
    from schemalyser.catalogue import Catalogue
    catalogue = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
    conversion = SimpleNamespace(sandbox=SimpleNamespace(tables={"OBS_SHEET": None, "NOT_IN_CATALOGUE": None}, catalogue=catalogue),
                                 planted_from={})
    (table,) = convert._scenario_tables("INSERT INTO NOT_IN_CATALOGUE (A) VALUES (1);", "here")
    with pytest.raises(convert.ScenarioError, match="does not hold"):
        convert.plant(conversion, {"tables": [table], "rows": ""})


@pytest.mark.parametrize("query", [
    "SELECT COUNT(*) AS n FROM omop.person p WHERE p.person_id = @id",
    "SELECT COUNT(*) AS n FROM OPENQUERY(elsewhere, 'SELECT 1') AS x",
    "SELECT COUNT(*) AS n INTO omop.copy FROM omop.person",
    "SELECT COUNT(*) AS n FROM srv.db.omop.person",
    "EXEC('SELECT 1')",
])
def test_an_expectation_is_one_select_as_a_gate_is(query):
    with pytest.raises(convert.ScenarioError):
        convert.check_expectation_query(query)


def test_an_expectation_reads_only_the_omop_tables():
    with pytest.raises(convert.ScenarioError):
        convert.check_expectation_query("SELECT COUNT(*) FROM OBS_READING")
    with pytest.raises(convert.ScenarioError):
        convert.check_expectation_query("SELECT 1 AS a; SELECT 2 AS b")
    convert.check_expectation_query("SELECT COUNT(*) AS n FROM omop.measurement m")


@pytest.mark.skipif(not (PRIVATE / "scenarios").is_dir(), reason="the private conversion is not present")
def test_the_private_twin_holds_the_same_scenarios_with_the_same_expectations():
    ours = sorted(p.name for p in (FIXTURES / "conversion" / "scenarios").iterdir())
    assert ours == sorted(p.name for p in (PRIVATE / "scenarios").iterdir())
    for name in ours:
        assert (PRIVATE / "scenarios" / name / "scenario.json").read_text() == \
            (FIXTURES / "conversion" / "scenarios" / name / "scenario.json").read_text(), name


def test_the_public_scenarios_hold_no_name_from_the_private_world():
    private_catalogue = FIXTURES.parent / "reference" / "worlds" / "clarity" / "catalogue.csv"
    if not private_catalogue.exists():
        pytest.skip("the private world is not present")
    names = {cell.upper() for line in private_catalogue.read_text().splitlines()[1:] for cell in line.split(",")[1:3]}
    names -= {cell.upper() for line in (FIXTURES / "invented-catalogue.csv").read_text().splitlines()[1:] for cell in line.split(",")[1:3]}
    for path in (FIXTURES / "conversion" / "scenarios").rglob("*"):
        if path.is_file():
            words = set(re.findall(r"[A-Z_][A-Z0-9_]{3,}", path.read_text()))
            assert not words & names, (path, words & names)


# The mean pressure that the conversion can calculate from a charted pressure, which is switched off by default.

def _with_calculated_mean(tmp_path, target):
    folder = tmp_path / f"calculated_{target}"
    shutil.copytree(FIXTURES / "conversion", folder)
    path = folder / "source_to_concept_map.csv"
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    assert [row["target_concept_id"] for row in rows if row["source_code"] == "CALCULATED_MEAN_PRESSURE"] == ["0"]
    for row in rows:
        if row["source_code"] == "CALCULATED_MEAN_PRESSURE":
            row["target_concept_id"] = target
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(out.getvalue())
    return convert.run(make_checks.WORLD, folder, rows=300, scenarios=[])


def test_the_calculated_mean_pressure_is_written_only_when_its_setting_switches_it_on(tmp_path, ran):
    conversion, report = ran
    assert conversion.con.execute("SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 3027598").fetchone()[0] == 0
    assert any(s["rows"] == 0 and s["status"] == "ok" for s in report["steps"])
    off, off_report = _with_calculated_mean(tmp_path, "0")
    on, on_report = _with_calculated_mean(tmp_path, "1")
    assert convert.failures(off_report) == [] and convert.failures(on_report) == []
    assert off.con.execute("SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 3027598").fetchone()[0] == 0
    # Switched on, every charted pressure that gives both a systolic and a diastolic row gives one calculated mean, with the
    # same time, visit detail and link to the anaesthetic as its systolic row, and nothing else changes.
    pairs = on.con.execute("""
        SELECT s.measurement_datetime, s.visit_detail_id, s.measurement_event_id, s.value_source_value,
               ROUND(d.value_as_number + (s.value_as_number - d.value_as_number) / 3.0, 3) AS mean
        FROM   omop.measurement s
               JOIN omop.measurement d ON d.measurement_concept_id = 3012888 AND d.measurement_datetime = s.measurement_datetime
                AND d.visit_detail_id = s.visit_detail_id AND d.value_source_value = s.value_source_value
                AND d.measurement_source_value = s.measurement_source_value
        WHERE  s.measurement_concept_id = 3004249 ORDER BY ALL""").fetchall()
    means = on.con.execute("""
        SELECT measurement_datetime, visit_detail_id, measurement_event_id, value_source_value, value_as_number
        FROM   omop.measurement WHERE measurement_concept_id = 3027598 ORDER BY ALL""").fetchall()
    assert means and sorted(set(means)) == sorted(set(pairs)) and len(means) <= len(pairs)
    assert on.con.execute("SELECT DISTINCT measurement_type_concept_id, unit_concept_id FROM omop.measurement "
                          "WHERE measurement_concept_id = 3027598").fetchall() == [(32882, 8876)]
    assert on.con.execute("SELECT COUNT(*) FROM omop.measurement WHERE measurement_concept_id = 3027598 "
                          "AND measurement_event_id IS NOT NULL").fetchone()[0] > 0
    others = "SELECT measurement_concept_id, COUNT(*) FROM omop.measurement WHERE measurement_concept_id <> 3027598 GROUP BY 1 ORDER BY 1"
    assert on.con.execute(others).fetchall() == off.con.execute(others).fetchall()
    assert all(gate["rows"] == 0 for gate in on_report["gates"])
