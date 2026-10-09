"""Corrections on screen 1: the richer bindings in plain forms, the check that tests each on invented rows, and the
probe that tests it once against the database.

Each form is made on the invented dictionary's proposed map. Its SQL is run on the invented world's own shadow in
DuckDB, as the page's other queries are, so that it is known to run and to give the contract's columns. The check is
then shown to pass a sound correction and to catch a link that repeats readings. No form attributes a row to an
anaesthetic by a time window: that is a question's logic over the roles, and the form is refused. A correction that fails is kept only with a reason, which the saved schema records and its check reports. The probe of
a kept correction runs on the invented world and is read back.
"""
import csv
import io
import json
import re
import sys
from pathlib import Path

import pytest

from schemalyser import corrections, describe, rolemap
from schemalyser.translate import to_duckdb

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
DICTIONARY = FIXTURES / "dictionary" / "invented-dictionary.csv"
TABLES = FIXTURES / "dictionary" / "invented-tables.csv"
DATE = "2026-10-07"

SOUND = {
    "column": {"form": "column", "about": "role_anaesthetic.patient_key", "table": "THEATRE_CASE", "column": "PERSON_KEY"},
    "flag": {"form": "derived", "about": "role_anaesthetic_detail.is_emergency", "table": "THEATRE_CASE",
             "column": "EMERGENCY_FLAG", "derive": {"form": "flag", "values": ["Y"]}},
    "scale": {"form": "derived", "about": "role_patient_detail.birth_weight_grams", "table": "PERSON_MASTER_2",
              "column": "BIRTH_WEIGHT_G", "derive": {"form": "scale", "factor": 1000, "offset": 0}},
    "date": {"form": "derived", "about": "role_patient.birth_date", "table": "PERSON_MASTER", "column": "BIRTH_TS",
             "derive": {"form": "date"}},
    # The unit of a dose is now a local key of the mapping view of units, which no derived form may give, so the trim is
    # tried on the staff member's key.
    "trim": {"form": "derived", "about": "role_staff.person_key", "table": "ANAES_STAFF", "column": "STAFF_KEY", "derive": {"form": "trim"}},
    "filter": {"form": "filter", "about": "role_anaesthetic rows", "table": "THEATRE_CASE", "column": "CASE_STATUS_CAT", "values": ["2"]},
    "path": {"form": "path", "about": "role_anaesthetic.patient_key", "column": "PERSON_KEY",
             "steps": [{"from": "CASE_KEY", "table": "THEATRE_CASE", "to": "CASE_KEY"}, {"from": "VISIT_KEY", "table": "VISIT", "to": "VISIT_KEY"}]},
    "pair": {"form": "pair", "about": "role_anaesthetic.patient_key", "column": "PERSON_KEY",
             "steps": [{"from": "CASE_KEY", "table": "THEATRE_CASE", "to": "CASE_KEY", "also": [["VISIT_KEY", "VISIT_KEY"]]}]},
    # The route of a drug is now a kind, so rows joined into one text are tried on the size of a device, which is text.
    "joined": {"form": "joined", "about": "role_device.size", "on_table": "AIRWAY_DEVICE", "on_column": "ANAES_KEY", "table": "ANAES_EVENT",
               "link": "ANAES_KEY", "text": "EVENT_TYPE_KEY", "order": "SEQ", "separator": " "},
    "codes": {"form": "codes", "about": "role_reading.kind", "chosen": {"52": "map_arterial", "51": "map_cuff"}},
}
# The window form that once attributed a reading to an anaesthetic by its time, which is now refused.
WINDOW = {"form": "window", "about": "role_reading.anaesthetic_key", "table": "OBS_SHEET", "column": "VISIT_KEY",
          "key": "VISIT_KEY", "before": 15, "after": 15}
# The reading's link to its anaesthetic through its sheet, as the proposer finds it, kept as a correction of its own.
LINK = {"form": "path", "about": "role_reading.anaesthetic_key", "column": "ANAES_KEY",
        "steps": [{"from": "SHEET_KEY", "table": "OBS_SHEET", "to": "SHEET_KEY"}]}
# A link through the encounter alone: two anaesthetics in one admission share it, so a reading reaches both.
DOUBLING = {"form": "path", "about": "role_reading.anaesthetic_key", "column": "ANAES_KEY",
            "steps": [{"from": "SHEET_KEY", "table": "OBS_SHEET", "to": "SHEET_KEY"}, {"from": "VISIT_KEY", "table": "ANAES_RECORD", "to": "VISIT_KEY"}]}


def sitting():
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    return s


@pytest.fixture(scope="module")
def proposed():
    return sitting()


@pytest.fixture(scope="module")
def world():
    sys.path.insert(0, str(FIXTURES))
    import make_checks
    from schemalyser import convert
    converted, _ = convert.run(make_checks.WORLD, FIXTURES / "conversion", 200)
    return converted


def run(world, sql):
    sql = re.sub(r"ISNULL\(a\.anaesthetic_key, 0\)", "a.anaesthetic_key", sql)
    sql = re.sub(r">= 10 THEN", ">= 0 THEN", sql)
    statements = [s for s in to_duckdb(sql, world.sandbox.date_columns) if not s.upper().startswith("ALTER TABLE")]
    try:
        for statement in statements:
            cursor = world.con.execute(statement)
            rows = cursor.fetchall()
            columns = [c[0] for c in cursor.description]
    finally:
        world.con.execute("DROP TABLE IF EXISTS temp_cohort")
    return columns, rows


def grid(columns, rows):
    return "\n".join("\t".join("NULL" if v is None else str(v) for v in row) for row in [columns, *rows]) + "\n"


@pytest.mark.parametrize("name", sorted(SOUND))
def test_each_form_writes_a_view_that_runs_on_the_invented_world_and_says_what_it_means(proposed, world, name):
    preview = proposed.correction_preview(SOUND[name])
    sentence = preview["sentence"]
    assert sentence.endswith(".") and "?" not in sentence and "!" not in sentence and len(sentence) <= 400
    rolemap.check_view(preview["sql"], preview["view"], proposed.dictionary)
    columns, rows = run(world, preview["sql"])
    assert columns == rolemap.all_views()[preview["view"]]
    assert rows, f"{name} gives no rows on the invented world"


def test_the_sentences_and_the_sql_say_what_each_form_does(proposed):
    assert "STRING_AGG(" in proposed.correction_preview(SOUND["joined"])["sql"]
    assert "WHERE  CAST(t2.CASE_STATUS_CAT AS varchar(254)) IN ('2')" in proposed.correction_preview(SOUND["filter"])["sql"]
    pair = proposed.correction_preview(SOUND["pair"])["sql"]
    assert "ON t1.CASE_KEY = t0.CASE_KEY AND t1.VISIT_KEY = t0.VISIT_KEY" in pair
    assert "TRY_CAST(t0.BIRTH_WEIGHT_G AS float) * 1000" in proposed.correction_preview(SOUND["scale"])["sql"]
    flag = proposed.correction_preview(SOUND["flag"])
    assert "CASE WHEN t1.EMERGENCY_FLAG IS NULL THEN NULL WHEN CAST(t1.EMERGENCY_FLAG AS varchar(254)) IN ('Y') THEN 1 ELSE 0 END" in flag["sql"]


@pytest.mark.parametrize("name", sorted(SOUND))
def test_the_check_passes_a_sound_correction(proposed, name):
    found = proposed.correction_check(SOUND[name])
    assert found["passed"], found["problems"]
    assert not found["problems"]
    assert found["views"] == len(proposed.data["roles"])


def test_the_check_says_the_model_is_whole_when_nothing_is_wrong():
    s = sitting()
    for name in ("trim", "joined"):
        s.correction_keep(SOUND[name], date=DATE)
    # role_operation reaches its anaesthetic from the theatre case's side, which repeats an operation wherever two
    # anaesthetics share one case, so the proposal's own fragile link is the one problem left.
    now = s.check_model()
    assert [p for p in now["problems"] if not p.startswith("In Procedures done under an anaesthetic, ")] == []
    s.data["roles"].pop("role_operation")
    found = s.correction_check(LINK)
    assert found["passed"]
    assert found["sentence"].startswith("This change keeps the hospital schema whole: all 12 parts of the record run on made-up rows and give the rows "
                                        "they should, every identifying column is unique, every flag is filled, and the "
                                        "invented newborns of the test audit give the expected answer.")


def test_the_check_catches_a_link_that_repeats_readings(proposed):
    found = proposed.correction_check(DOUBLING)
    assert not found["passed"]
    assert any(re.fullmatch(r"In Readings charted during an anaesthetic, [\d,]+ made-up readings appear twice, each linked to a "
                            r"second anaesthetic, so a reading no longer links to exactly one anaesthetic\.", p) for p in found["problems"])
    # Each finding names the binding that it concerns, so that the page can link it to its row.
    assert all(found["about"][p] == "role_reading.anaesthetic_key" for p in found["problems"] if "readings appear twice" in p)
    assert not any("role_" in p or "contract" in p for p in found["problems"] + found["notes"])


def test_a_broken_view_is_reported_by_name(proposed):
    trial = proposed._clone()
    trial.data["roles"]["role_anaesthetic"]["columns"]["patient_key"]["binding"]["path"] = []
    found = corrections.run_check(trial)
    assert "In Anaesthetics, the dictionary does not hold the column PERSON_KEY of ANAES_RECORD." in found["problems"]
    assert found["about"]["In Anaesthetics, the dictionary does not hold the column PERSON_KEY of ANAES_RECORD."] == "role_anaesthetic rows"
    # The shadow makes every column that a binding names, so the view runs there, and the dictionary is what refuses it.


def test_a_time_window_and_names_not_in_the_dictionary_are_refused(proposed):
    # Attributing a row to an anaesthetic by its time is a question's logic over the roles, and no form offers it.
    with pytest.raises(describe.DescribeError, match="does not know a correction of the kind window"):
        proposed.correction_check(WINDOW)
    assert "window" not in corrections.FORMS
    with pytest.raises(describe.DescribeError, match="holds no column THEATRE_CASE.NO_SUCH"):
        proposed.correction_preview(dict(SOUND["flag"], column="NO_SUCH"))
    with pytest.raises(describe.DescribeError, match="does not suit it"):
        proposed.correction_preview(dict(SOUND["scale"], about="role_patient.is_test"))
    with pytest.raises(describe.DescribeError, match="starts from the table that holds this part"):
        proposed.correction_preview(dict(SOUND["path"], steps=[{"start": "VISIT", "from": "VISIT_KEY", "table": "THEATRE_CASE", "to": "VISIT_KEY"}]))
    # Once the tables and columns query is pasted, every name must be in its result as well.
    from test_describe import tables_result
    s = sitting()
    s.read_tables(tables_result(leave_out=("ANAES_EVENT",)), record=False)
    with pytest.raises(describe.DescribeError, match="result of the tables and columns query holds no column ANAES_EVENT"):
        s.correction_preview(SOUND["joined"])


def test_a_failing_correction_is_kept_only_with_a_reason_and_the_saved_schema_records_and_reports_it():
    s = sitting()
    s.correction_keep(LINK, date=DATE)
    with pytest.raises(describe.DescribeError, match="Keep it although the test fails"):
        s.correction_keep(DOUBLING, date=DATE)
    with pytest.raises(describe.DescribeError, match="Keep it although the test fails"):
        s.correction_keep(DOUBLING, although=True, reason="  ", date=DATE)
    kept = s.correction_keep(DOUBLING, although=True, reason="The team says that a visit holds one anaesthetic here.", date=DATE)
    assert kept == {"kept": "role_reading.anaesthetic_key", "probe": "link"}
    item = s.data["roles"]["role_reading"]["columns"]["anaesthetic_key"]
    assert item["status"] == "person" and item["confirmation"]["check"].startswith("failed: In Readings charted during an anaesthetic, ")
    assert item["confirmation"]["reason"] == "The team says that a visit holds one anaesthetic here."
    files = s.folder_files(date=DATE)
    rows = list(csv.DictReader(io.StringIO(files["confirmations.csv"].decode())))
    assert [r["test"][:6] for r in rows] == ["passed", "failed"]
    assert json.loads(rows[1]["correction"]) == DOUBLING and rows[1]["reason"].startswith("The team says")
    journal = json.loads(files["journal.json"])["entries"]
    entries = [e["payload"] for e in journal if e["kind"] == "correction kept"]
    assert [e["passed"] for e in entries] == [True, False] and entries[1]["reason"].startswith("The team says")
    # Each correction names the test run on made-up rows that checked it, which the journal holds as an entry of its own.
    runs = {e["payload"]["run"] for e in journal if e["kind"] == "test run"}
    assert entries[0]["run"] in runs and entries[1]["run"] in runs
    # map.json, with the new forms, is still a map that the checker reads.
    folder = s._work_folder()
    rolemap.read_map(folder, s.dictionary)
    # A new sitting restores the saved schema, rebuilds the same map and reports the binding kept although it failed.
    again = describe.Describe()
    again.version = "test"
    again.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes())
    again.restore({k: v for k, v in files.items()})
    checked = again.check()
    assert checked["same"], checked["differences"]
    assert [f["about"] for f in checked["failing"]] == ["role_reading.anaesthetic_key"]
    assert checked["failing"][0]["reason"].startswith("The team says")


def test_the_probes_run_on_the_invented_world_and_are_read_back(world):
    from test_describe import tables_result
    s = sitting()
    s.read_tables(tables_result({"OBS_READING": 25_000_000}), record=False)
    for correction in (LINK, SOUND["filter"], SOUND["flag"]):
        s.correction_keep(correction, date=DATE)
    for about, kind, columns in (("role_reading.anaesthetic_key", "link", ["anaesthetics", "with_rows", "without_rows"]),
                                 ("role_anaesthetic rows", "filter", ["rows_read", "passing"]),
                                 ("role_anaesthetic_detail.is_emergency", "flag", ["ones", "zeros", "empty"])):
        probe = s.probe_query(about, 2024, "6. Confirm each binding")
        assert probe["kind"] == kind
        if kind == "link":
            assert "INTO   #cohort" in probe["sql"] and "DATEADD(minute" not in probe["sql"]
        got_columns, rows = run(world, probe["sql"])
        assert got_columns == columns and len(rows) == 1
        receipt = s.read_probe(about, grid(got_columns, rows))
        assert receipt["rows"] == 1 and receipt["findings"]
    files = s.folder_files(date=DATE)
    assert any(p.startswith("queries/") and "probe-role_reading-anaesthetic_key" in p for p in files)
    assert any(p.startswith("results/") and "probe-role_anaesthetic-rows" in p for p in files)
    with pytest.raises(describe.DescribeError, match="no test query"):
        s.probe_query("role_patient.birth_date", 2024)


def test_the_values_query_offers_the_commonest_values_of_a_column(world):
    s = sitting()
    found = s.values_query("role_anaesthetic rows", "THEATRE_CASE", "CASE_STATUS_CAT", 2024, "6. Confirm each binding")
    assert not found["script"] and "TOP (50)" in found["sql"]
    columns, rows = run(world, found["sql"])
    assert columns == ["value", "rows"] and rows
    read = s.read_values(found["name"], grid(columns, rows))
    assert read["values"][0]["value"]


def test_a_map_json_with_a_broken_form_is_refused():
    with pytest.raises(rolemap.MapError, match="a step of a path"):
        corrections.check_shape({"table": "A", "column": "B", "path": [["A", "B", "C"]]}, "role_x.y")
    # A binding saved with a time window, as the removed form wrote one, is refused when the map is read.
    with pytest.raises(rolemap.MapError, match="never attributes a row to an anaesthetic by a time window"):
        corrections.check_shape({"table": "A", "column": "B", "path": [], "window": {"table": "C"}}, "role_x.y")
    with pytest.raises(rolemap.MapError, match="derived value"):
        corrections.check_shape({"table": "A", "column": "B", "path": [], "derive": {"form": "sql"}}, "role_x.y")


def test_an_alternative_column_or_table_is_checked_and_its_check_recorded_when_kept():
    s = sitting()
    # An alternative column as the page lists it, with its link, goes through the same check as any other correction.
    chosen = {"form": "column", "about": "role_anaesthetic.patient_key",
              "replacement": "THEATRE_CASE.PERSON_KEY, by ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY"}
    preview = s.correction_preview(chosen)
    assert preview["sentence"].startswith("The patient's identifier in Anaesthetics is THEATRE_CASE.PERSON_KEY, reached by matching "
                                          "ANAES_RECORD.CASE_KEY to THEATRE_CASE.CASE_KEY")
    assert s.correction_check(chosen)["passed"]
    s.correction_keep(chosen, date=DATE)
    # A different table for the rows of a part proposes that part again, and is checked in the same way.
    table = s.data["roles"]["role_stay"]["rows"]["binding"]["table"]
    rows = {"form": "rows", "about": "role_stay rows", "table": table}
    assert s.correction_preview(rows)["sentence"].startswith(f"The rows of Hospital stays come from {table}, one row for each")
    s.correction_keep(rows, date=DATE)
    assert s.data["roles"]["role_stay"]["rows"]["status"] == "person"
    files = s.folder_files(date=DATE)
    found = list(csv.DictReader(io.StringIO(files["confirmations.csv"].decode())))
    assert [(r["attribute"], r["test"][:6]) for r in found] == [("role_anaesthetic.patient_key", "passed"), ("role_stay rows", "passed")]
    with pytest.raises(describe.DescribeError, match="TABLE.COLUMN"):
        s.correction_preview({"form": "column", "about": "role_anaesthetic.patient_key", "replacement": "nothing here"})


# Version 1.1: the concept translations, several pathways to one part, and the rule of the filter.

CONCEPTS = [
    {"code": "CEPHAZOLIN", "description": "an invented description", "concept_id": 9100001, "status": "mapped", "provenance": "a person"},
    {"code": "FLUCLOXACILLIN", "concept_id": 9100002, "status": "ambiguous", "provenance": "a reference conversion"},
    {"code": "FLUCLOXACILLIN", "concept_id": 9100003, "status": "ambiguous", "provenance": "a reference conversion"},
    {"code": "OXYGEN", "concept_id": 0, "status": "unmapped", "provenance": "the hospital's own conversion"}]


def test_the_concepts_and_a_further_pathway_compile_check_save_and_restore():
    s = sitting()
    found = s.translate_concepts("map_drug_concept", CONCEPTS, date=DATE, actor="the invented clinician")
    assert found["statuses"] == {"mapped": 1, "unmapped": 1, "ambiguous": 2} and found["codes"] == 3
    # The part gives each listed code as its opaque key, and every other code as the key unlisted; the mapping view
    # names no code at all.
    rows = s.concept_rows("map_drug_concept")
    keys = {r["local_key"] for r in rows}
    assert len(keys) == 3 and not keys & {"CEPHAZOLIN", "FLUCLOXACILLIN", "OXYGEN"}
    mapping = rolemap.mapping_sql("map_drug_concept", rows)
    assert "CEPHAZOLIN" not in mapping and all(k in mapping for k in keys)
    drug = s.view_sql("role_drug")
    assert all(k in drug for k in keys) and "THEN 'unlisted'" in drug and "'administration' AS source_kind" in drug
    # A further pathway to the drugs, read from another table with a source kind of its own: the view is the union of
    # the two, each row marked, and its keys given as text so that the two tables' keys unite.
    s.add_pathway("role_drug", "OBS_READING", "charted_value", "charted", date=DATE, actor="the invented analyst")
    drug = s.view_sql("role_drug")
    assert drug.count("\nUNION ALL\n") == 1 and "'charted_value' AS source_kind" in drug and "CAST(t0.GIVEN_KEY AS varchar(254))" in drug
    rolemap.check_view(drug, "role_drug", s.dictionary)
    checked = corrections.run_check(s)
    assert not [p for p in checked["problems"] if p.startswith("In Drugs given")], checked["problems"]
    # The evidence of each pathway is kept apart, and the translation has dimensions of its own.
    assert s.dimensions["bindings"]["role_drug@charted rows"]["confirmed"]["by"] == "the invented analyst"
    assert not (s.dimensions["bindings"].get("role_drug rows") or {}).get("confirmed")
    assert s.dimensions["translations"]["map_drug_concept"]["confirmed"]["by"] == "the invented clinician"
    s.choose_source_kind("role_drug", "order", date=DATE)
    assert "'order' AS source_kind" in s.view_sql("role_drug")
    with pytest.raises(describe.DescribeError, match="kinds of source record"):
        s.choose_source_kind("role_drug", "DRUG_GIVEN", date=DATE)
    with pytest.raises(describe.DescribeError, match="part that records events"):
        s.add_pathway("role_staff", "ANAES_STAFF", "procedure_log", "second", date=DATE)
    with pytest.raises(describe.DescribeError, match="mapped, unmapped or ambiguous"):
        s.translate_concepts("map_drug_concept", [dict(CONCEPTS[0], status="maybe")], date=DATE)
    # The journal records the translation, and a saved and restored schema gives the same view, keys and all, and is
    # rebuilt from its dictionary and its journal to the same map.
    files = s.folder_files(date=DATE)
    journal = json.loads(files["journal.json"])["entries"]
    assert [e["payload"]["mapping"] for e in journal if e["kind"] == "concepts translated"] == ["map_drug_concept"]
    saved = json.loads(files["map/map.json"])
    assert saved["concepts"]["views"]["map_drug_concept"]["rows"][0]["description"] == "an invented description"
    assert saved["roles"]["role_drug"]["pathways"][0]["source_kind"] == "charted_value"
    again = describe.Describe()
    again.version = "test"
    again.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes())
    again.restore(files)
    assert again.view_sql("role_drug") == s.view_sql("role_drug") and again.concept_rows("map_drug_concept") == rows
    rebuilt = again.check()
    assert rebuilt["same"], rebuilt["differences"]


def test_the_page_offers_no_time_window_and_says_what_a_filter_may_do():
    strings = (ROOT / "site" / "src" / "describe-strings.ts").read_text(encoding="utf-8")
    forms = (ROOT / "site" / "src" / "describe-corrections.ts").read_text(encoding="utf-8")
    assert "window:" not in strings.split("forms: {")[1].split("}")[0] and "'window'" not in forms
    filter_what = strings.split("formWhat: {")[1].split("filter:")[1].split("\n")[0]
    assert "kind of record" in filter_what and "clinical" in filter_what
    assert "kind of record" in corrections.__doc__ and "clinical meaning" in corrections.__doc__
