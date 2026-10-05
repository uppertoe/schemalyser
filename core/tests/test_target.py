import csv
import io
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from schemalyser import convert, harness, questions, target
from schemalyser.catalogue import Catalogue

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CONVERSION = FIXTURES / "conversion"
TARGETS = FIXTURES / "targets"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

CHECKS = (FIXTURES / "invented-checks.csv").read_text()
PROFILE = (FIXTURES / "profile" / "invented-core-profile.csv").read_text()
PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]
RULES = json.loads((FIXTURES / "invented-site-rules.json").read_text())
INFANT = (TARGETS / "infant_low_pressure.sql").read_text()
AIRWAY = (TARGETS / "airway_by_anaesthesia_type.sql").read_text()
FROM_ANAESTHETIC = (TARGETS / "infant_low_pressure_from_anaesthetic.sql").read_text()
NEONATAL = (TARGETS / "neonatal_low_mean_pressure.sql").read_text()


def checklist(sql=INFANT, checks=None, profile=None, world=None, conversion=CONVERSION):
    return target.checklist(world or make_checks.WORLD, conversion, sql, checks, profile)


def by_id(rows):
    return {row["question_id"]: row for row in rows}


def files(traced):
    return [step.file for step in traced["steps"]]


@pytest.fixture(scope="module")
def former(tmp_path_factory):
    """The invented conversion as it stood before the clinical lead made the two better supported alternatives the steps,
    with visit_detail.sql and measurement_blood_pressure.sql as the steps, so that the tests can show an open join."""
    return _switched(tmp_path_factory.mktemp("former"))


@pytest.fixture(scope="module")
def situations():
    found = {}
    for name, sql in (("infant", INFANT), ("airway", AIRWAY), ("anaesthetic", FROM_ANAESTHETIC)):
        for label, checks, profile in (("bare", None, None), ("checks", CHECKS, None), ("both", CHECKS, PROFILE)):
            found[(name, label)] = checklist(sql, checks, profile)
    return found


# Reading the target query.

def test_the_query_s_fields_and_concepts_are_read_through_its_aliases():
    read = target.read_target(INFANT)
    assert read["tables"] == ["measurement", "person", "procedure_occurrence"]
    assert ("person", "birth_datetime") in read["fields"]
    assert ("measurement", "measurement_event_id") in read["fields"]
    assert ("procedure_occurrence", "procedure_end_datetime") in read["fields"]
    assert read["concepts"] == {("measurement", "measurement_concept_id"): ["3004249"],
                                ("procedure_occurrence", "procedure_concept_id"): ["4174669"]}
    airway = target.read_target(AIRWAY)
    assert airway["concepts"][("procedure_occurrence", "procedure_concept_id")] == ["4100052", "4174669", "4219502"]
    assert airway["concepts"][("device_exposure", "device_concept_id")] == ["4097216", "4106029"]


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM omop.person; SELECT 2 FROM omop.person",
    "DELETE FROM omop.person",
    "SELECT * INTO #x FROM omop.person",
    "SELECT * FROM PERSON_MASTER",
    "SELECT p.no_such_field FROM omop.person p",
    "this is not sql (",
])
def test_anything_but_one_select_on_omop_tables_is_refused(sql):
    with pytest.raises(target.TargetError):
        target.read_target(sql)


# The trace.

def test_the_trace_for_the_infant_query_picks_the_blood_pressure_step_and_its_upstream_steps():
    _, traced = checklist()
    picked = files(traced)
    assert "measurement_blood_pressure_through_anaesthetic.sql" in picked
    for absent in ("measurement.sql", "measurement_asa.sql", "drug_exposure.sql", "drug_exposure_infusion.sql",
                   "procedure_occurrence_surgery.sql", "procedure_occurrence_events.sql", "device_exposure.sql",
                   "measurement_blood_pressure.sql", "visit_detail.sql"):
        assert absent not in picked
    for upstream in ("procedure_occurrence_anaesthetic.sql", "visit_detail_through_case.sql", "visit_occurrence.sql", "person.sql"):
        assert upstream in picked
    layers = {step.file: step.layer for step in traced["steps"]}
    assert layers["visit_occurrence.sql"] == layers["person.sql"] == "core"
    assert layers["measurement_blood_pressure_through_anaesthetic.sql"] == layers["visit_detail_through_case.sql"] == "anaesthesia"
    assert traced["possible"] == []


def test_a_different_query_picks_different_steps():
    _, traced = checklist(AIRWAY)
    picked = files(traced)
    assert "device_exposure.sql" in picked and "procedure_occurrence_anaesthetic.sql" in picked
    assert "measurement_blood_pressure_through_anaesthetic.sql" not in picked and "measurement.sql" not in picked
    assert "visit_occurrence.sql" in picked


def test_a_step_that_cannot_be_ruled_out_is_kept_as_possible():
    # The operations take their concept from a vocabulary whose rows are only proposed from labels.
    sql = "SELECT COUNT(*) AS n FROM omop.procedure_occurrence po WHERE po.procedure_concept_id = 4174669"
    rows, traced = checklist(sql)
    assert "procedure_occurrence_anaesthetic.sql" in files(traced)
    assert [step.file for step, _, _ in traced["possible"]] == ["procedure_occurrence_surgery.sql"]
    row = by_id(rows)["step-procedure_occurrence_surgery.sql"]
    assert row["kind"] == "codes" and row["blocking"] == "no" and "SITE_PROC" in row["evidence_in_hand"]
    assert "could not rule out" in target.readiness(rows, traced)


# The checklist.

@pytest.mark.parametrize("label", ["bare", "checks", "both"])
def test_the_checklist_is_produced_with_and_without_checks_and_profile(situations, label):
    rows, traced = situations[("infant", label)]
    read = list(csv.DictReader(io.StringIO(target.to_csv(rows))))
    assert tuple(read[0]) == target.LAYOUT
    assert {row["kind"] for row in read} == set(target.KINDS)
    for row in read:
        assert row["status"] in questions.STATUSES and row["blocking"] in ("yes", "no")
        assert row["who"] in target.WHO and row["mechanism"] in target.MECHANISMS
        assert row["currently_from"] in target.SOURCES
    assert len({row["question_id"] for row in read}) == len(read)
    text = target.readiness(rows, traced)
    # With the check results and the core profile, no blocking item of the infant query remains open.
    assert ("not yet ready" in text) == (label != "both") and "measurement_blood_pressure_through_anaesthetic.sql" in text


def test_the_statuses_follow_the_evidence_in_hand(situations, former):
    bare = by_id(situations[("infant", "bare")][0])
    both = by_id(situations[("infant", "both")][0])
    # No sample query links an anaesthetic to its visit through the anaesthetic's own visit key, which the former step did.
    join = by_id(checklist(conversion=former)[0])["relationship-ANAES_RECORD.VISIT_KEY=VISIT.VISIT_KEY"]
    assert join["status"] == "open" and join["blocking"] == "yes" and "None of the" in join["evidence_in_hand"]
    # The step as it now stands reaches the visit through the theatre case, as the sample queries do.
    assert "relationship-ANAES_RECORD.VISIT_KEY=VISIT.VISIT_KEY" not in bare
    assert bare["relationship-ANAES_RECORD.CASE_KEY=THEATRE_CASE.CASE_KEY"]["status"] in ("answered", "partly")
    assert both["relationship-ANAES_RECORD.CASE_KEY=THEATRE_CASE.CASE_KEY"]["status"] == "answered"
    assert bare["relationship-OBS_READING.SHEET_KEY=OBS_SHEET.SHEET_KEY"]["status"] == "answered"
    # An optional join does not block.
    assert bare["relationship-ANAES_RECORD.ANAES_KEY=ANAES_STAFF.ANAES_KEY"]["blocking"] == "no"
    # The systolic concept has its mapping row; the check results then confirm the source holds its code.
    assert bare["codes-measurement.measurement_concept_id-3004249"]["status"] == "partly"
    assert both["codes-measurement.measurement_concept_id-3004249"]["status"] == "answered"
    assert both["codes-procedure_occurrence.procedure_concept_id-4174669"]["status"] == "partly"
    # The core rows follow the core profile.
    assert bare["core-person.birth_datetime"]["status"] == "open"
    assert both["core-person.birth_datetime"]["status"] == "answered"
    # The invented core profile was run on the conversion as it now stands, so it holds the match rate for
    # THEATRE_CASE.VISIT_KEY and not for the source key of the former step, which it no longer joins on.
    earlier = by_id(checklist(checks=CHECKS, profile=PROFILE, conversion=former)[0])
    assert both["C-source-value-visit_occurrence.visit_source_value"]["status"] == "answered"
    assert earlier["C-source-value-visit_occurrence.visit_source_value"]["status"] == "partly"
    assert "For 2 of those source keys" in earlier["C-source-value-visit_occurrence.visit_source_value"]["evidence_in_hand"]
    assert both["C-source-value-visit_occurrence.visit_source_value"]["blocking"] == "yes"
    # The meaning of the pressure and the timing of the anaesthetic do not block.
    assert bare["meaning-measurement.value_as_number"]["blocking"] == "no"
    assert {"B-tuning-ages", "B-tuning-anaesthetic_duration"} <= set(bare)
    assert "B-tuning-measurement_before" not in bare and "B-tuning-end_tidal_co2" not in bare
    assert all(row["blocking"] == "no" for row in bare.values() if row["kind"] in ("timing", "meaning", "filter"))


def test_a_name_missing_from_the_catalogue_blocks(tmp_path):
    lines = (FIXTURES / "invented-catalogue.csv").read_text().splitlines()
    kept = [line for line in lines if ",OBS_SHEET," not in line and ",ANAES_RECORD,ANAES_KIND_CAT," not in line]
    (tmp_path / "catalogue.csv").write_text("\n".join(kept) + "\n")
    world = harness.World(tmp_path / "catalogue.csv", FIXTURES / "requests", FIXTURES / "invented-site-rules.json")
    rows = by_id(checklist(world=world)[0])
    assert rows["table-OBS_SHEET"]["status"] == "open" and rows["table-OBS_SHEET"]["blocking"] == "yes"
    assert rows["column-ANAES_RECORD.ANAES_KIND_CAT"]["status"] == "open"


def test_no_value_from_the_check_results_appears(situations):
    marked, n = [], 0
    for row in csv.reader(io.StringIO(CHECKS)):
        if row and row[0] == "values":
            n += 1
            row[3], row[4] = f"QVAL{n}X", (f"QLABEL{n}X" if row[4] else "")
        marked.append(row)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(marked)
    for sql in (INFANT, AIRWAY, FROM_ANAESTHETIC):
        rows, traced = checklist(sql, out.getvalue(), PROFILE)
        text = target.to_csv(rows) + target.readiness(rows, traced)
        assert "QVAL" not in text and "QLABEL" not in text
    for key, (rows, traced) in situations.items():
        text = target.to_csv(rows) + target.readiness(rows, traced)
        for row in csv.reader(io.StringIO(CHECKS)):
            if row and row[0] == "values" and row[4]:
                assert row[4] not in text


def test_no_planted_value_appears(situations):
    for rows, traced in situations.values():
        text = (target.to_csv(rows) + target.readiness(rows, traced)).lower()
        assert not [value for value in PLANTED if value.lower() in text]


def _allowed():
    """Every word that the output may hold: the catalogue, the rules, the CDM field list, the conversion's own
    files and vocabularies, and the fixed wording of this module and of the register."""
    words = set()

    def add(text):
        words.update(w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(text)))

    catalogue = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
    for table in catalogue.tables():
        add(table.name)
        add(table.schema or "")
        for column in table.columns.values():
            add(column.name)
    add(json.dumps(RULES))
    for table, fields in convert.cdm_fields().items():
        add(table)
        for field, _, _ in fields:
            add(field)
    for step in json.loads((CONVERSION / "conversion.json").read_text()):
        add(step["file"] + " " + " ".join(convert.alternatives(step)))
    add(" ".join(entry["intent"] for entry in json.loads((CONVERSION / target.INTENTS_FILE).read_text())))
    for table in convert.read_tables(CONVERSION):
        add(table["name"] + " " + " ".join(field["name"] for field in table["fields"]))
    # The public names of the concepts that the conversion uses are the conversion's own file.
    add((CONVERSION / "concept_names.csv").read_text())
    with open(CONVERSION / "source_to_concept_map.csv", newline="") as f:
        add(" ".join(row["source_vocabulary_id"] for row in csv.DictReader(f)))
    add(" ".join(e["vocabulary"] for e in json.loads((CONVERSION / "derived_mappings.json").read_text())))
    for path in CONVERSION.glob("*.sql"):
        add(" ".join(re.findall(r"'(SITE_[A-Z_]+)'", path.read_text())))
    add(json.dumps(target.WORDING))
    add(" ".join(target.KINDS + target.BLOCKING + target.LAYOUT + target.MECHANISMS + target.SOURCES))
    for register in (questions.WORDING, questions.IN_HAND, questions.NOUNS, questions.SOURCE_PHRASES,
                     questions.TUNING_GROUPS):
        add(json.dumps(register))
    from schemalyser import tuning
    add(" ".join(p.key for p in tuning.PARAMETERS))
    add("B tuning C source value step codes table column relationship filter meaning core")
    # The plain queries that the checklist offers: their comments, the fixed words of T-SQL that they use,
    # the bands, and the kinds of check and the names of the columns of their results.
    from schemalyser import checks as checking
    add(json.dumps(checking.PLAIN_WORDING))
    add("SELECT TOP AS FROM WHERE IS NOT NULL GROUP BY HAVING COUNT_BIG CAST nvarchar REPLACE CHAR MAX DISTINCT WITH "
        "NOLOCK TABLESAMPLE SYSTEM PERCENT UNION ALL CASE WHEN THEN ELSE END DATEDIFF_BIG minute YEAR AND OR IN "
        "VALUES LEFT JOIN ON sys tables partitions SUM SCHEMA_ID schema_id object_id index_id schema_name table_name "
        "g k n d x y m b s p t N")
    add(" ".join(checking.BAND_LABELS + checking.FANOUT_LABELS + checking.LAYOUT + checking.KINDS + checking.SKIPPED_REASONS))
    add(" ".join(target.QUERY_STATES))
    # The plain queries of the core profile: their comments, the profile's own layout and categories, and the
    # fixed words of T-SQL and of SQL Server's records that they use.
    from schemalyser import profile as core_profile
    add(json.dumps(core_profile.PLAIN_WORDING) + json.dumps(core_profile.CATEGORIES) + " ".join(core_profile.LAYOUT))
    add("CONVERT GETDATE SCHEMA_ID TYPE_NAME system_type_id objects columns name type key_field LOWER INFORMATION_SCHEMA "
        "TABLES COLUMNS TABLE_NAME TABLE_SCHEMA COLUMN_NAME DATA_TYPE CHARACTER_MAXIMUM_LENGTH kind size cdm f c x o "
        "U V varchar nvarchar datetime datetime2 int integer date max EXISTS CROSS APPLY hit bigint LEN LIKE Y")
    return words


def test_every_word_in_the_output_is_on_the_allowlist(situations):
    allowed = _allowed()
    for rows, traced in situations.values():
        text = target.to_csv(rows) + target.readiness(rows, traced)
        unknown = {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if w.lower() not in allowed}
        assert not unknown, sorted(unknown)


# Ticking off.

def test_a_sample_query_that_makes_the_join_ticks_the_relationship_off(tmp_path, former):
    rows, traced = checklist(conversion=former)
    before = by_id(rows)["relationship-ANAES_RECORD.VISIT_KEY=VISIT.VISIT_KEY"]
    assert before["status"] == "open" and before["blocking"] == "yes"
    counted = target.counts(rows)

    requests = tmp_path / "requests"
    shutil.copytree(FIXTURES / "requests", requests)
    (requests / "2025" / "anaesthetics_by_ward.sql").write_text(
        "SELECT v.WARD_KEY, COUNT(*) AS anaesthetics\n"
        "FROM ANAES_RECORD ar\n"
        "JOIN VISIT v ON CAST(v.VISIT_KEY AS varchar(50)) = ar.VISIT_KEY\n"
        "GROUP BY v.WARD_KEY;\n")
    world = harness.World(FIXTURES / "invented-catalogue.csv", requests, FIXTURES / "invented-site-rules.json")
    rows, traced = checklist(world=world, conversion=former)
    after = by_id(rows)["relationship-ANAES_RECORD.VISIT_KEY=VISIT.VISIT_KEY"]
    assert after["status"] == "answered" and "1 of the 16 sample queries" in after["evidence_in_hand"]
    assert target.counts(rows)["answered"] > counted["answered"]
    assert target.counts(rows)["open"] == counted["open"] - 1


def test_a_mapping_row_ticks_the_codes_row_off(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    path = folder / "source_to_concept_map.csv"
    lines = path.read_text().splitlines()
    systolic = [line for line in lines if ",SITE_OBS_SYSTOLIC," in line]
    path.write_text("\n".join(line for line in lines if line not in systolic) + "\n")

    rows, traced = checklist(checks=CHECKS, profile=PROFILE, conversion=folder)
    before = by_id(rows)["codes-measurement.measurement_concept_id-3004249"]
    assert before["status"] == "open" and before["blocking"] == "yes"
    assert "SITE_OBS_SYSTOLIC" in before["evidence_in_hand"]
    assert "measurement_blood_pressure_through_anaesthetic.sql" not in files(traced)
    counted = target.counts(rows)

    path.write_text(path.read_text() + "\n".join(systolic) + "\n")
    rows, traced = checklist(checks=CHECKS, profile=PROFILE, conversion=folder)
    after = by_id(rows)["codes-measurement.measurement_concept_id-3004249"]
    assert after["status"] == "answered"
    assert "measurement_blood_pressure_through_anaesthetic.sql" in files(traced)
    assert target.counts(rows)["answered"] > counted["answered"]


# Running the query.

@pytest.fixture(scope="module")
def results():
    return {name: target.run(make_checks.WORLD, CONVERSION, sql, rows=500)
            for name, sql in (("infant", INFANT), ("airway", AIRWAY), ("anaesthetic", FROM_ANAESTHETIC))}


def test_the_infant_query_runs_on_the_synthetic_rows(results):
    result = results["infant"]
    assert result["columns"] == ["anaesthetics", "with_low_systolic"]
    (anaesthetics, low), = result["rows"]
    assert anaesthetics > 0
    assert 0 <= low <= anaesthetics


def test_the_airway_query_runs_on_the_synthetic_rows(results):
    result = results["airway"]
    assert result["columns"] == ["anaesthesia_type_concept_id", "airway_concept_id", "anaesthetics", "devices"]
    assert result["rows"]
    assert {row[0] for row in result["rows"]} <= {4174669, 4100052, 4219502}
    assert {row[1] for row in result["rows"]} <= {4097216, 4106029}
    assert all(0 < row[2] <= row[3] for row in result["rows"])


def test_the_query_is_rewritten_for_the_published_schema():
    text = target.published(INFANT)
    assert "[$(AnaesPubSchemaName)].[procedure_occurrence]" in text
    assert "[$(AnaesPubSchemaName)].[person]" in text
    assert "omop" not in text.lower().replace("anaespubschemaname", "")


def test_the_command_writes_the_checklist_and_the_readiness(tmp_path, monkeypatch):
    world = tmp_path / "world"
    world.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", world / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", world / "site-rules.json")
    shutil.copytree(FIXTURES / "requests", world / "requests")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["schemalyser.target", str(world), str(CONVERSION), str(TARGETS / "infant_low_pressure.sql"),
                                      "--checks", str(FIXTURES / "invented-checks.csv"), "--out", str(out)])
    target.main()
    assert (out / "checklist.csv").read_text().startswith(",".join(target.LAYOUT))
    assert "Readiness of the shadow database" in (out / "readiness.txt").read_text()
    assert not (out / "result.csv").exists()


# The wording.

def test_the_wording_is_calm_and_complete():
    texts = []
    for key, entry in target.WORDING.items():
        if key in ("in_hand", "readiness", "item", "how", "who", "query", "facts"):
            texts += list(entry.values())
        elif key != "nouns":
            assert entry["who"] in target.WHO and entry["mechanism"] in target.MECHANISMS
            for part in ("question", "decides", "evidence_needed"):
                assert entry[part].endswith(".") and entry[part][0].isupper(), entry[part]
                texts.append(entry[part])
    texts += [line for value in target.DRAFT_WORDING.values() for line in (value if isinstance(value, list) else [value])]
    for text in texts:
        assert "?" not in text and "!" not in text and "  " not in text, text


# The custom tables.

def test_a_target_query_may_read_the_custom_tables_of_its_conversion():
    custom = target.custom_fields(CONVERSION)
    read = target.read_target(FROM_ANAESTHETIC, custom)
    assert read["tables"] == ["anaesthetic", "measurement"]
    assert ("anaesthetic", "age_years") in read["fields"]
    assert read["concepts"] == {("anaesthetic", "anaesthesia_type_concept_id"): ["4174669"],
                                ("measurement", "measurement_concept_id"): ["3004249"]}
    with pytest.raises(target.TargetError):
        target.read_target(FROM_ANAESTHETIC)                    # without the conversion, the table is unknown
    with pytest.raises(target.TargetError):
        target.read_target("SELECT a.shoe_size FROM omop.anaesthetic a", custom)


def test_the_trace_runs_through_the_derived_step_to_the_steps_beneath_it():
    rows, traced = checklist(FROM_ANAESTHETIC)
    _, infant = checklist(INFANT)
    # The same anaesthesia and core steps as the question asked of the CDM tables, with the derived step above them.
    assert files(traced) == files(infant) + ["anaesthetic.sql"]
    assert {step.file: step.layer for step in traced["steps"]}["anaesthetic.sql"] == "derived"
    # The weight and the ASA class are not read, so the steps that write them are not needed.
    assert "measurement.sql" not in files(traced) and "measurement_asa.sql" not in files(traced)
    # The concept is credited to the step that writes it, and the core field that the derived step reads is listed.
    found = by_id(rows)
    assert "codes-procedure_occurrence.procedure_concept_id-4174669" in found
    assert not any(key.startswith("codes-anaesthetic.") for key in found)
    assert "core-person.birth_datetime" in found
    assert "1 step of the derived layer: anaesthetic.sql" in target.readiness(rows, traced)
    # A query that reads the weight rests on the measurement step as well.
    _, weighed = checklist("SELECT COUNT(*) AS n FROM omop.anaesthetic a WHERE a.weight_kg > 10")
    assert "measurement.sql" in files(weighed) and "measurement_asa.sql" not in files(weighed)


def test_both_forms_of_the_infant_question_give_the_same_answer(results):
    infant, anaesthetic = results["infant"], results["anaesthetic"]
    assert anaesthetic["failures"] == [] and anaesthetic["columns"] == infant["columns"]
    (total, low), = anaesthetic["rows"]
    assert anaesthetic["rows"] == infant["rows"] and total > 0 and low > 0
    assert "[$(AnaesPubSchemaName)].[anaesthetic]" in anaesthetic["published"]


# The source-side draft.

def test_the_source_draft_gives_the_same_answer_as_the_omop_query(results):
    from schemalyser.translate import to_duckdb
    converted = results["infant"]["conversion"]
    for name, sql in (("infant", INFANT), ("anaesthetic", FROM_ANAESTHETIC), ("airway", AIRWAY)):
        draft = target.source_draft(CONVERSION, sql, converted.sandbox.catalogue, target_name=name, blank=False)
        assert "omop." not in draft.lower() and "source_to_concept_map" not in draft
        found = converted.con.execute(to_duckdb(draft, converted.sandbox.date_columns)[0]).fetchall()
        assert found == results[name]["rows"], name


def test_the_source_draft_numbers_each_assumption_on_the_line_it_affects():
    draft = target.source_draft(CONVERSION, INFANT, Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text()))
    header = [line for line in draft.splitlines() if re.match(r"--   \d+\. ", line)]
    numbers = [int(re.match(r"--   (\d+)\.", line).group(1)) for line in header]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) > 10
    text = "\n".join(header)
    assert "The query pairs each row with the rows in which OBS_READING.SHEET_KEY equals OBS_SHEET.SHEET_KEY." in text
    assert "The query leaves out the rows in which OBS_READING.ACCEPTED_FLAG holds 'N'." in text
    assert "The code 5 in OBS_READING.OBS_TYPE_KEY means systolic part of a charted blood pressure, and the query reads it as the concept 3004249." in text
    assert "The code 1 in ANAES_RECORD.ANAES_KIND_CAT means general, and the query reads it as the concept 4174669." in text
    assert "For each ANAES_STAFF.ANAES_KEY, the query takes the row that comes first by ANAES_STAFF.SEQ as the one that counts." in text
    # Every number is marked on at least one line of the query, and no marker is left behind.
    body = draft.split("\nWITH\n", 1)[1]
    marked = {int(n) for found in re.findall(r"-- assumptions? ([\d, and]+)$", body, re.M) for n in re.findall(r"\d+", found)}
    assert marked == set(numbers) and target.MARK not in draft
    systolic = next(n for n, line in zip(numbers, header) if "concept 3004249" in line)
    assert re.search(rf"m\.measurement_concept_id = 3004249  -- assumptions? .*\b{systolic}\b", body)
    # The mapping rows are written into the query, and each step carries its own comment.
    assert "('5', 'SITE_OBS_SYSTOLIC', 'Systolic part of a charted blood pressure', 3004249)" in body
    assert "PERSON: one row for each person who is not a test person." in body


def test_the_command_writes_the_source_draft(tmp_path, monkeypatch):
    world = tmp_path / "world"
    world.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", world / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", world / "site-rules.json")
    shutil.copytree(FIXTURES / "requests", world / "requests")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["schemalyser.target", str(world), str(CONVERSION), str(TARGETS / "infant_low_pressure_from_anaesthetic.sql"),
                                      "--draft", "--out", str(out)])
    target.main()
    text = (out / "source_draft.sql").read_text()
    assert text.startswith("-- This draft names the tables and local codes of the hospital's database, "
                           "so it is for use inside the hospital only.\n-- DRAFT for the analytics team to correct.")


# The review of a target query against the data dictionary.

@pytest.fixture(scope="module")
def data_dictionary():
    from schemalyser import dictionary
    return dictionary.build(CONVERSION)


@pytest.mark.parametrize("name", sorted(path.name for path in TARGETS.glob("*.sql")))
def test_the_review_finds_nothing_in_the_target_queries(data_dictionary, name):
    assert target.review((TARGETS / name).read_text(), data_dictionary) == []


FAULTY = {
    "concept_never": ("SELECT COUNT(*) AS n FROM omop.measurement m WHERE m.measurement_concept_id = 3027019",
                      "The query compares measurement.measurement_concept_id with the concept 3027019, which the conversion never "
                      "writes in that field or in any other, so the comparison finds no rows."),
    "concept_elsewhere": ("SELECT COUNT(*) AS n FROM omop.observation o WHERE o.observation_concept_id = 3004249",
                          "The query compares observation.observation_concept_id with the concept 3004249, which the conversion "
                          "writes only in measurement.measurement_concept_id, so the comparison finds no rows in observation."),
    "link": ("SELECT COUNT(*) AS n FROM omop.anaesthetic a JOIN omop.measurement m ON m.person_id = a.person_id "
             "AND m.measurement_datetime BETWEEN a.start_datetime AND a.end_datetime WHERE m.measurement_concept_id = 3004249",
             "The query relates measurement to anaesthetic through person_id and measurement_datetime, and a row of measurement "
             "belongs to an anaesthetic only through the documented links, which are measurement.measurement_event_id and "
             "measurement.visit_detail_id."),
    "units": ("SELECT COUNT(*) AS n FROM omop.measurement m WHERE m.value_as_number < 60",
              "The query compares measurement.value_as_number with a number, and the conversion writes that field in more than "
              "one unit (8541, 8554, 8876, 9529 and no unit) for the rows that the query keeps, so the comparison mixes values in "
              "different units unless the query also restricts measurement.unit_concept_id."),
    "table": ("SELECT COUNT(*) AS n FROM omop.note x",
              "The query reads note, which the data dictionary does not describe, because neither the anaesthesia layer nor the "
              "derived layer writes it."),
    "field": ("SELECT COUNT(*) AS n FROM omop.measurement m WHERE m.range_low IS NOT NULL",
              "The query reads measurement.range_low, which no step of the conversion writes, so the field is empty in every row "
              "that the dictionary describes."),
    "custom": ("SELECT a.lowest_systolic FROM omop.anaesthetic a",
               "The query reads anaesthetic.lowest_systolic, which tables.json does not define for the custom table anaesthetic."),
}


@pytest.mark.parametrize("kind", sorted(FAULTY))
def test_the_review_finds_each_kind_of_fault(data_dictionary, kind):
    sql, finding = FAULTY[kind]
    assert target.review(sql, data_dictionary) == [finding]


def test_the_review_accepts_what_the_dictionary_documents(data_dictionary):
    # One concept of one unit, a link through the visit detail, and a unit restricted by the query itself.
    for sql in ("SELECT COUNT(*) AS n FROM omop.measurement m WHERE m.measurement_concept_id = 3004249 AND m.value_as_number < 60",
                "SELECT COUNT(*) AS n FROM omop.measurement m WHERE m.unit_concept_id = 8876 AND m.value_as_number < 60",
                "SELECT COUNT(*) AS n FROM omop.anaesthetic a JOIN omop.measurement m ON m.visit_detail_id = a.visit_detail_id "
                "AND m.measurement_datetime BETWEEN a.start_datetime AND a.end_datetime"):
        assert target.review(sql, data_dictionary) == [], sql
    with pytest.raises(target.TargetError):
        target.review("DELETE FROM omop.measurement", data_dictionary)


def test_the_review_wording_is_calm_and_complete():
    for text in list(target.REVIEW_WORDING.values()) + [target.REVIEW_EMPTY]:
        assert "?" not in text and "!" not in text and "  " not in text, text
    for key in ("unknown_table", "table", "not_cdm", "custom", "field", "concept_never", "concept_elsewhere", "link", "units"):
        assert target.REVIEW_WORDING[key].startswith("The query ") and target.REVIEW_WORDING[key].endswith(".")


def test_the_command_writes_the_review(tmp_path, monkeypatch):
    world = tmp_path / "world"
    world.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", world / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", world / "site-rules.json")
    shutil.copytree(FIXTURES / "requests", world / "requests")
    query = tmp_path / "faulty.sql"
    query.write_text(FAULTY["units"][0])
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["schemalyser.target", str(world), str(CONVERSION), str(query), "--review", "--out", str(out)])
    target.main()
    # The command adds the two findings that advise and do not block, here that the query could also count people.
    assert (out / "review.txt").read_text() == FAULTY["units"][1] + "\n" + target.REVIEW_WORDING["advise_people"] + "\n"


# The planted scenarios.

INFANT_SCENARIOS = ["infant_systolic_cases", "age_by_calendar_date"]


@pytest.fixture(scope="module")
def infant_runs():
    found = {}
    for name, sql in (("infant", INFANT), ("anaesthetic", FROM_ANAESTHETIC)):
        for label, scenarios in (("without", []), ("with", INFANT_SCENARIOS)):
            found[(name, label)] = target.run(make_checks.WORLD, CONVERSION, sql, rows=400, scenarios=scenarios)
    return found


def test_both_infant_queries_count_exactly_the_planted_infant_cases(infant_runs):
    # Planted: an infant with a reading of 50 during the anaesthetic (counted, and low), a five-year-old (left out),
    # an infant whose reading came after the end (counted, not low), an infant whose reading was not accepted (counted,
    # not low), and four children of whom two are under one year by calendar date, each with a reading of 55 (counted, low).
    for name in ("infant", "anaesthetic"):
        (before_total, before_low), = infant_runs[(name, "without")]["rows"]
        (after_total, after_low), = infant_runs[(name, "with")]["rows"]
        assert (after_total - before_total, after_low - before_low) == (5, 3), name
        assert infant_runs[(name, "with")]["failures"] == []
    assert infant_runs[("infant", "with")]["rows"] == infant_runs[("anaesthetic", "with")]["rows"]


def test_the_readiness_says_how_the_planted_scenarios_bearing_on_the_query_fared(infant_runs):
    rows, traced = checklist(FROM_ANAESTHETIC)
    summary = infant_runs[("anaesthetic", "with")]["scenarios"]
    assert summary["names"] == sorted(INFANT_SCENARIOS) and summary["met"] == summary["expectations"] == 9
    text = target.readiness(rows, traced, summary)
    assert text.rstrip().endswith("2 of the conversion's planted scenarios bear on the tables that this query reads, "
                                  "and the run met all 9 of their expectations.")
    assert "planted" not in target.readiness(rows, traced)
    assert "None of the conversion's planted scenarios" in target.readiness(rows, traced, infant_runs[("anaesthetic", "without")]["scenarios"])
    unmet = dict(summary, met=7)
    assert "did not meet 2 of their 9 expectations" in target.readiness(rows, traced, unmet)


NEONATAL_SCENARIOS = ["neonatal_mean_pressure_minutes", "neonatal_mean_pressure_who_counts"]
NEONATAL_BANDS = ["no mean pressure recorded", "none", "under 5 minutes", "5 to 14 minutes", "15 minutes or more"]


@pytest.fixture(scope="module")
def neonatal_runs():
    return {label: target.run(make_checks.WORLD, CONVERSION, NEONATAL, rows=400, scenarios=scenarios)
            for label, scenarios in (("without", []), ("with", NEONATAL_SCENARIOS))}


def test_the_neonatal_query_moves_each_band_by_exactly_the_planted_cases(neonatal_runs):
    # The planted cases, by the query's rules. Ten arterial readings below 40 a minute apart stand for ten minutes, with a
    # death 30 days, 90 days or 91 days after the date of the start: three anaesthetics in 5 to 14 minutes, two of them
    # with a death within 90 days. A low cuff reading followed by the next 20 minutes later stands for the five minutes of
    # the cap: 5 to 14 minutes. Low readings before the start and after the end, a low last reading of an anaesthetic with
    # no recorded stop, a cuff mean of 35 beside an arterial mean of 50, and a low mean that the source did not accept:
    # four anaesthetics with none. A low last reading two minutes before the end, and the child aged 27 days with three
    # low minutes: under 5 minutes. The child aged 28 days is left out. A charted pressure without a mean: no mean
    # pressure recorded. A neonate with two anaesthetics who died within 90 days of both: one anaesthetic under 5
    # minutes and one of 5 to 14 minutes, each counted with the death.
    implied = {"no mean pressure recorded": (1, 0), "none": (4, 0), "under 5 minutes": (3, 1),
               "5 to 14 minutes": (5, 3), "15 minutes or more": (0, 0)}
    without, planted = neonatal_runs["without"], neonatal_runs["with"]
    assert without["failures"] == [] and planted["failures"] == []
    assert without["columns"] == ["minutes_below_40", "anaesthetics", "died_within_90_days", "children",
                                  "children_died_within_90_days"]
    assert [row[0] for row in without["rows"]] == NEONATAL_BANDS == [row[0] for row in planted["rows"]]
    before = {band: (int(n), int(died)) for band, n, died, _, _ in without["rows"]}
    after = {band: (int(n), int(died)) for band, n, died, _, _ in planted["rows"]}
    assert {band: (after[band][0] - before[band][0], after[band][1] - before[band][1]) for band in NEONATAL_BANDS} == implied
    # Each planted anaesthetic belongs to a child of its own in its band, the neonate with two anaesthetics falling in
    # two bands, so the children move by the same numbers as the anaesthetics.
    children_before = {band: (int(c), int(d)) for band, _, _, c, d in without["rows"]}
    children_after = {band: (int(c), int(d)) for band, _, _, c, d in planted["rows"]}
    assert {band: (children_after[band][0] - children_before[band][0], children_after[band][1] - children_before[band][1])
            for band in NEONATAL_BANDS} == implied
    assert all(int(c) <= int(n) and int(cd) <= int(d) for _, n, d, c, cd in planted["rows"])
    # The generated rows alone already reach the bands below 40, so that the result is not degenerate.
    assert sum(n for band, (n, _) in before.items() if band not in ("no mean pressure recorded", "none")) > 0
    summary = planted["scenarios"]
    assert summary["names"] == NEONATAL_SCENARIOS and summary["met"] == summary["expectations"] == 32


def test_the_neonatal_query_s_source_draft_and_published_form_hold_its_rules(neonatal_runs):
    from schemalyser.translate import to_duckdb
    planted = neonatal_runs["with"]
    converted = planted["conversion"]
    draft = target.source_draft(CONVERSION, NEONATAL, converted.sandbox.catalogue, target_name="neonatal_low_mean_pressure.sql",
                                blank=False)
    assert "omop." not in draft.lower() and "LEAD(" in draft
    assert converted.con.execute(to_duckdb(draft, converted.sandbox.date_columns)[0]).fetchall() == planted["rows"]
    # By default the draft ends with an outer SELECT that leaves blank every count from 1 to 4, and only those.
    safe = target.source_draft(CONVERSION, NEONATAL, converted.sandbox.catalogue, target_name="neonatal_low_mean_pressure.sql")
    blanked = converted.con.execute(to_duckdb(safe, converted.sandbox.date_columns)[0]).fetchall()
    assert blanked == [tuple(None if i and v is not None and 1 <= v <= 4 else v for i, v in enumerate(row)) for row in planted["rows"]]
    assert any(None in row for row in blanked) and "may be removed where the audit's approval allows exact small numbers" in safe
    # The question's own lines carry only the assumptions about its concepts, and none about the expressions it names.
    header = "\n".join(line for line in draft.splitlines() if re.match(r"--   \d+\. ", line))
    assert "neonatal." not in header and "low." not in header and "banded." not in header
    assert "[$(AnaesPubSchemaName)].[measurement]" in planted["published"] and "LEAD(" in planted["published"]
    read = target.read_target(NEONATAL, target.custom_fields(CONVERSION))
    assert read["concepts"][("measurement", "measurement_concept_id")] == ["21490852", "21492241"]
    rows, traced = checklist(NEONATAL)
    assert "measurement_mean_pressure_calculated.sql" not in files(traced) and "measurement.sql" in files(traced)
    assert by_id(rows)["codes-measurement.measurement_concept_id-21490852"]["status"] in ("partly", "answered")


# What each join is meant to do, the routes that the sample queries take, and the alternatives of a step.

INVENTED = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
VISIT_JOIN = "relationship-ANAES_RECORD.VISIT_KEY=VISIT.VISIT_KEY"
SHEET_JOIN = "relationship-OBS_SHEET.VISIT_KEY=VISIT.VISIT_KEY"
CASE_JOIN = "relationship-ANAES_RECORD.CASE_KEY=THEATRE_CASE.CASE_KEY"


def test_every_join_of_the_invented_conversion_has_an_intent():
    # Each join that a step or an alternative makes between source columns, in any layer.
    intents = target.read_intents(CONVERSION)
    made = target.joins_made(CONVERSION, INVENTED)
    assert made and {layer for makers in made.values() for _, layer in makers} >= {"core", "anaesthesia"}
    missing = sorted(sorted(pair) for pair in made if pair not in intents["join"])
    assert not missing, missing
    # Nothing is described that the conversion does not make.
    assert not [sorted(pair) for pair in intents["join"] if pair not in made]


def test_every_relationship_item_carries_its_intent(situations):
    for rows, _ in situations.values():
        for row in rows:
            assert set(target.LAYOUT) <= set(row)
            if row["kind"] == "relationship":
                assert row["intent"].startswith("This join ") and row["intent"].endswith("."), row["question_id"]
    rows = by_id(situations[("infant", "bare")][0])
    assert rows[CASE_JOIN]["intent"] == "This join finds the theatre case that the anaesthetic was given for."
    assert rows["filter-OBS_READING.ACCEPTED_FLAG"]["intent"].startswith("This filter leaves out")
    assert rows["codes-measurement.measurement_concept_id-3004249"]["intent"].startswith("These mapping rows")
    assert all(row["intent"] == "" for row in rows.values() if row["kind"] in ("table", "column", "core", "timing"))


INTENT_FAULTS = {
    "a line break": {"join": ["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"], "intent": "This join finds\nthe visit."},
    "a variable": {"join": ["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"], "intent": "This join finds $(the) visit."},
    "a question": {"join": ["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"], "intent": "Does this join find the visit?"},
    "no full stop": {"join": ["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"], "intent": "This join finds the visit"},
    "an unknown column": {"join": ["ANAES_RECORD.NO_SUCH_KEY", "VISIT.VISIT_KEY"], "intent": "This join finds the visit."},
    "an unknown table": {"filter": "NO_SUCH_TABLE.VISIT_KEY", "intent": "This filter keeps the visits."},
    "a name that is not plain": {"filter": "VISIT.[VISIT_KEY]", "intent": "This filter keeps the visits."},
    "an unknown vocabulary": {"vocabulary": "SITE_NOTHING", "intent": "These mapping rows map nothing."},
    "two kinds": {"join": ["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"], "filter": "VISIT.ADMIT_TS", "intent": "This join finds the visit."},
    "one column": {"join": ["VISIT.VISIT_KEY", "VISIT.VISIT_KEY"], "intent": "This join finds the visit."},
    "a repeated join": {"join": ["VISIT.VISIT_KEY", "ANAES_RECORD.VISIT_KEY"], "intent": "This join finds the visit again."},
}


@pytest.mark.parametrize("fault", sorted(INTENT_FAULTS))
def test_an_intent_that_breaks_a_rule_is_refused(tmp_path, fault):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    entries = json.loads((folder / target.INTENTS_FILE).read_text())
    (folder / target.INTENTS_FILE).write_text(json.dumps(entries + [INTENT_FAULTS[fault]]))
    with pytest.raises(target.TargetError):
        target.read_intents(folder)
    with pytest.raises(target.TargetError):
        checklist(conversion=folder)


def test_an_open_join_gives_the_route_that_the_sample_queries_take(former):
    rows, traced = checklist(conversion=former)
    found = by_id(rows)
    visit = found[VISIT_JOIN]
    assert visit["status"] == "open"
    assert visit["route"] == "ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY (6); THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY (1)"
    assert ("The sample queries reach VISIT from ANAES_RECORD through THEATRE_CASE, by ANAES_RECORD.CASE_KEY = "
            "THEATRE_CASE.CASE_KEY (6 queries) and THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY (1 query).") in visit["evidence_in_hand"]
    assert found[SHEET_JOIN]["route"] == "OBS_SHEET.VISIT_KEY = THEATRE_CASE.VISIT_KEY (1); THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY (1)"
    # Where the sample queries do not connect the two tables, the item says so, and how many requests were not read in full.
    staff = found["relationship-ANAES_RECORD.ANAES_KEY=ANAES_STAFF.ANAES_KEY"]
    assert staff["route"] == ""
    assert "The sample queries do not connect ANAES_RECORD and ANAES_STAFF at all, through any route of up to 3 joins." in staff["evidence_in_hand"]
    assert "2 of the 15 sample queries could not be read in full" in staff["evidence_in_hand"]
    # An answered join has no route.
    assert all(row["route"] == "" for row in rows if row["status"] == "answered")


def test_the_route_prefers_the_shortest_and_then_the_better_supported(tmp_path):
    requests = tmp_path / "requests"
    requests.mkdir()
    (requests / "a.sql").write_text("SELECT 1 FROM ANAES_RECORD ar JOIN OBS_SHEET s ON s.ANAES_KEY = ar.ANAES_KEY "
                                    "JOIN VISIT v ON v.VISIT_KEY = s.VISIT_KEY;\n")
    for name in ("b", "c"):
        (requests / f"{name}.sql").write_text("SELECT 1 FROM ANAES_RECORD ar JOIN THEATRE_CASE tc ON tc.CASE_KEY = ar.CASE_KEY "
                                              "JOIN VISIT v ON v.VISIT_KEY = tc.VISIT_KEY;\n")
    (requests / "d.sql").write_text("SELECT 1 FROM ANAES_RECORD ar JOIN ANAES_EVENT e ON e.ANAES_KEY = ar.ANAES_KEY "
                                    "JOIN DRUG_GIVEN g ON g.ANAES_KEY = e.ANAES_KEY JOIN VISIT v ON v.VISIT_KEY = g.VISIT_KEY;\n")
    world = harness.World(FIXTURES / "invented-catalogue.csv", requests, FIXTURES / "invented-site-rules.json")
    analysis = world.analysis()
    evidence = target._Evidence(world, analysis.catalogue, analysis.held_back)
    route = evidence.route("ANAES_RECORD", "VISIT")
    assert [(a[0], b[0], n) for a, b, n in route] == [("ANAES_RECORD", "THEATRE_CASE", 2), ("THEATRE_CASE", "VISIT", 2)]
    assert evidence.route("ANAES_RECORD", "VISIT", bound=1) is None
    assert evidence.route("ANAES_RECORD", "WARD_DEF") is None


def test_the_checklist_weighs_a_step_against_its_alternatives(situations, former):
    # As the steps now stand, each is at least as well supported as its alternative.
    text = target.readiness(*situations[("infant", "bare")])
    assert ("The sample queries make 3 of the 4 joins that visit_detail_through_case.sql makes as written. The sample queries "
            "make 1 of the 3 joins that visit_detail.sql, an alternative to visit_detail_through_case.sql, makes. The sample "
            "queries support visit_detail_through_case.sql as written at least as well as any of its alternatives.") in text.replace("\n", " ")
    assert "making" not in text
    # As the steps stood before, the checklist found each alternative better supported, and said what to do.
    rows, traced = checklist(conversion=former)
    visit = by_id(rows)[VISIT_JOIN]
    assert ("The sample queries make 1 of the 3 joins that visit_detail.sql makes as written. The sample queries make 3 of the "
            "4 joins that visit_detail_through_case.sql, an alternative to visit_detail.sql, makes. The sample queries support "
            "the alternative visit_detail_through_case.sql better than visit_detail.sql as written.") in visit["evidence_in_hand"]
    assert "measurement_blood_pressure_through_anaesthetic.sql" in by_id(rows)[SHEET_JOIN]["evidence_in_hand"]
    text = target.readiness(rows, traced)
    assert "The sample queries make 2 of the 2 joins that measurement_blood_pressure_through_anaesthetic.sql" in text
    # The statement ends with what to do about each open join that a better supported alternative avoids.
    assert text.rstrip().endswith(
        "If visit_detail_through_case.sql makes the same rows as visit_detail.sql, the clinical lead can settle the join of "
        "ANAES_RECORD.VISIT_KEY to VISIT.VISIT_KEY by making visit_detail_through_case.sql the step in conversion.json, because "
        "the sample queries support it better.\n"
        "If measurement_blood_pressure_through_anaesthetic.sql makes the same rows as measurement_blood_pressure.sql, the "
        "clinical lead can settle the join of OBS_SHEET.VISIT_KEY to VISIT.VISIT_KEY by making "
        "measurement_blood_pressure_through_anaesthetic.sql the step in conversion.json, because the sample queries support it better.")


def test_without_an_alternative_the_readiness_suggests_the_route(tmp_path, former):
    folder = tmp_path / "conversion"
    shutil.copytree(former, folder)
    steps = json.loads((folder / "conversion.json").read_text())
    (folder / "conversion.json").write_text(json.dumps([{k: v for k, v in s.items() if k != "alternatives"} for s in steps]))
    rows, traced = checklist(conversion=folder)
    text = target.readiness(rows, traced)
    assert "alternative" not in text
    assert ("If the route that the sample queries take through THEATRE_CASE links the same rows, the clinical lead can settle "
            "the join of ANAES_RECORD.VISIT_KEY to VISIT.VISIT_KEY by changing visit_detail.sql to follow that route.") in text


def _switched(tmp_path):
    """The invented conversion with each step's first alternative made the step, as a person would edit conversion.json.

    The clinical lead has already made the better supported alternatives the steps, so this gives the conversion as it was before."""
    folder = tmp_path / "switched"
    shutil.copytree(CONVERSION, folder)
    steps = json.loads((folder / "conversion.json").read_text())
    for step in steps:
        if step.get("alternatives"):
            step["file"], step["alternatives"] = step["alternatives"][0], [step["file"]]
    (folder / "conversion.json").write_text(json.dumps(steps, indent=1))
    return folder


def test_making_the_alternatives_the_steps_settled_the_two_open_joins(former):
    rows, _ = checklist(conversion=former)
    before, counted = by_id(rows), target.counts(rows)
    assert before[VISIT_JOIN]["status"] == before[SHEET_JOIN]["status"] == "open"
    rows, traced = checklist()
    after = by_id(rows)
    assert "visit_detail_through_case.sql" in files(traced) and "visit_detail.sql" not in files(traced)
    # The two joins that no sample query made are no longer needed, and the joins that replace them are made by the sample queries.
    assert VISIT_JOIN not in after and SHEET_JOIN not in after
    for key in ("relationship-ANAES_RECORD.CASE_KEY=THEATRE_CASE.CASE_KEY", "relationship-THEATRE_CASE.VISIT_KEY=VISIT.VISIT_KEY",
                "relationship-ANAES_RECORD.ANAES_KEY=OBS_SHEET.ANAES_KEY"):
        assert after[key]["status"] == "answered" and after[key]["blocking"] == "yes", key
    assert not [row for row in rows if row["kind"] == "relationship" and row["blocking"] == "yes" and row["status"] == "open"]
    now = target.counts(rows)
    assert now["open"] == counted["open"] - 2 and now["answered"] > counted["answered"]
    text = target.readiness(rows, traced)
    assert "at least as well as any of its alternatives" in text and "making" not in text


def _anaesthetics(conversion):
    from schemalyser.translate import to_duckdb
    found = conversion.con.execute(to_duckdb(INFANT, conversion.sandbox.date_columns)[0]).fetchall()
    return conversion.count("visit_detail"), conversion.count("anaesthetic"), found


def test_running_with_the_alternatives_passes_the_gates_and_finds_the_same_anaesthetics():
    names = ["visit_detail.sql", "measurement_blood_pressure.sql"]
    plain, plain_report = convert.run(make_checks.WORLD, CONVERSION, rows=400, scenarios=[])
    tried, tried_report = convert.run(make_checks.WORLD, CONVERSION, rows=400, scenarios=[], alternatives=names)
    assert convert.failures(plain_report) == convert.failures(tried_report) == []
    assert all(gate["rows"] == 0 for gate in tried_report["gates"])
    # The stand-in database gives every anaesthetic a theatre case in its own hospital visit, so both routes find the same rows.
    assert _anaesthetics(tried) == _anaesthetics(plain) and _anaesthetics(plain)[0] > 0
    with pytest.raises(ValueError):
        convert.run(make_checks.WORLD, CONVERSION, rows=50, scenarios=[], alternatives=["visit_detail_through_case.sql"])


def test_the_review_advises_counting_people_and_warns_of_a_row_for_each_record():
    from schemalyser import dictionary as dictionaries
    built = dictionaries.build(CONVERSION)
    w = target.REVIEW_WORDING
    assert w["advise_people"] in target.review(INFANT, built, advice=True)
    assert w["advise_people"] not in target.review(INFANT, built)
    neonatal = target.review(NEONATAL, built, advice=True)
    assert w["advise_people"] not in neonatal and w["advise_rows"] not in neonatal
    listing = target.review("SELECT p.person_id, p.year_of_birth FROM omop.person AS p", built, advice=True)
    assert w["advise_rows"] in listing and w["advise_people"] not in listing
