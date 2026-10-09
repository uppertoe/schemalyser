"""Corrections on screen 1: the richer bindings in plain forms, the check that tests each on invented rows, and the
probe that tests it once against the database.

Each form is made on the invented dictionary's proposed map. Its SQL is run on the invented world's own shadow in
DuckDB, as the page's other queries are, so that it is known to run and to give the contract's columns. The check is
then shown to pass a sound correction, to catch a link that repeats readings, and to refuse a time window with no
key. A correction that fails is kept only with a reason, which the saved schema records and its check reports. The probe of
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
    "trim": {"form": "derived", "about": "role_drug.unit", "table": "DRUG_GIVEN", "column": "DOSE_UNIT_CAT", "derive": {"form": "trim"}},
    "filter": {"form": "filter", "about": "role_anaesthetic rows", "table": "THEATRE_CASE", "column": "CASE_STATUS_CAT", "values": ["2"]},
    "path": {"form": "path", "about": "role_anaesthetic.patient_key", "column": "PERSON_KEY",
             "steps": [{"from": "CASE_KEY", "table": "THEATRE_CASE", "to": "CASE_KEY"}, {"from": "VISIT_KEY", "table": "VISIT", "to": "VISIT_KEY"}]},
    "pair": {"form": "pair", "about": "role_anaesthetic.patient_key", "column": "PERSON_KEY",
             "steps": [{"from": "CASE_KEY", "table": "THEATRE_CASE", "to": "CASE_KEY", "also": [["VISIT_KEY", "VISIT_KEY"]]}]},
    "window": {"form": "window", "about": "role_reading.anaesthetic_key", "table": "OBS_SHEET", "column": "VISIT_KEY",
               "key": "VISIT_KEY", "before": 15, "after": 15},
    "joined": {"form": "joined", "about": "role_drug.route", "on_table": "DRUG_GIVEN", "on_column": "ROUTE_CAT", "table": "LK_ROUTE",
               "link": "ROUTE_CAT", "text": "LABEL", "order": "LABEL", "separator": " "},
    "codes": {"form": "codes", "about": "role_reading.kind", "chosen": {"52": "map_arterial", "51": "map_cuff"}},
}
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
    window = proposed.correction_preview(SOUND["window"])
    assert window["sentence"] == ("A reading belongs to the anaesthetic whose VISIT_KEY it shares (OBS_SHEET.VISIT_KEY = "
                                  "ANAES_RECORD.VISIT_KEY), if its time of the reading lies between the anaesthetic's start and stop, "
                                  "allowing 15 minutes either side.")
    assert "DATEADD(minute, -15, w" in window["sql"] and "IS NULL OR t0.READ_TS <= DATEADD(minute, 15, w" in window["sql"]
    assert "AND t0.OBS_TYPE_KEY" not in window["sql"]
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
    found = s.correction_check(SOUND["window"])
    assert found["passed"]
    assert found["sentence"].startswith("This change keeps the hospital schema whole: all 11 parts of the record run on made-up rows and give the rows "
                                        "they should, every identifying column is unique, every flag is filled, and the "
                                        "invented newborns of the test audit give the expected answer.")
    assert any("outside the anaesthetic's window" in n for n in found["notes"])


def test_the_check_catches_a_link_that_repeats_readings_and_a_window_too_wide(proposed):
    found = proposed.correction_check(DOUBLING)
    assert not found["passed"]
    assert any(re.fullmatch(r"In Readings charted during an anaesthetic, [\d,]+ made-up readings appear twice, each linked to a "
                            r"second anaesthetic, so a reading no longer links to exactly one anaesthetic\.", p) for p in found["problems"])
    # Each finding names the binding that it concerns, so that the page can link it to its row.
    assert all(found["about"][p] == "role_reading.anaesthetic_key" for p in found["problems"] if "readings appear twice" in p)
    assert not any("role_" in p or "contract" in p for p in found["problems"] + found["notes"])
    wide = dict(SOUND["window"], before=240, after=240)
    assert not proposed.correction_check(wide)["passed"]


def test_a_broken_view_is_reported_by_name(proposed):
    trial = proposed._clone()
    trial.data["roles"]["role_anaesthetic"]["columns"]["patient_key"]["binding"]["path"] = []
    found = corrections.run_check(trial)
    assert "In Anaesthetics, the dictionary does not hold the column PERSON_KEY of ANAES_RECORD." in found["problems"]
    assert found["about"]["In Anaesthetics, the dictionary does not hold the column PERSON_KEY of ANAES_RECORD."] == "role_anaesthetic rows"
    # The shadow makes every column that a binding names, so the view runs there, and the dictionary is what refuses it.


def test_a_window_with_no_key_and_names_not_in_the_dictionary_are_refused(proposed):
    with pytest.raises(describe.DescribeError, match="Rule 3 of the record"):
        proposed.correction_check({"form": "window", "about": "role_reading.anaesthetic_key", "before": 15, "after": 15})
    with pytest.raises(describe.DescribeError, match="Rule 3 of the record"):
        proposed.correction_preview(dict(SOUND["window"], key=""))
    with pytest.raises(describe.DescribeError, match="applies only to the anaesthetic's identifier"):
        proposed.correction_preview(dict(SOUND["window"], about="role_reading.reading_time"))
    with pytest.raises(describe.DescribeError, match="holds no column THEATRE_CASE.NO_SUCH"):
        proposed.correction_preview(dict(SOUND["flag"], column="NO_SUCH"))
    with pytest.raises(describe.DescribeError, match="does not suit it"):
        proposed.correction_preview(dict(SOUND["scale"], about="role_patient.is_test"))
    with pytest.raises(describe.DescribeError, match="whole number of minutes"):
        proposed.correction_preview(dict(SOUND["window"], before="ten"))
    with pytest.raises(describe.DescribeError, match="starts from the table that holds this part"):
        proposed.correction_preview(dict(SOUND["path"], steps=[{"start": "VISIT", "from": "VISIT_KEY", "table": "THEATRE_CASE", "to": "VISIT_KEY"}]))
    # Once the tables and columns query is pasted, every name must be in its result as well.
    from test_describe import tables_result
    s = sitting()
    s.read_tables(tables_result(leave_out=("LK_ROUTE",)), record=False)
    with pytest.raises(describe.DescribeError, match="result of the tables and columns query holds no column LK_ROUTE"):
        s.correction_preview(SOUND["joined"])


def test_a_failing_correction_is_kept_only_with_a_reason_and_the_saved_schema_records_and_reports_it():
    s = sitting()
    s.correction_keep(SOUND["window"], date=DATE)
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
    for name in ("window", "filter", "flag"):
        s.correction_keep(SOUND[name], date=DATE)
    for about, kind, columns in (("role_reading.anaesthetic_key", "link", ["anaesthetics", "with_rows", "without_rows"]),
                                 ("role_anaesthetic rows", "filter", ["rows_read", "passing"]),
                                 ("role_anaesthetic_detail.is_emergency", "flag", ["ones", "zeros", "empty"])):
        probe = s.probe_query(about, 2024, "6. Confirm each binding")
        assert probe["kind"] == kind
        if kind == "link":
            assert "INTO   #cohort" in probe["sql"] and "DATEADD(minute, -15, w." in probe["sql"]
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
    with pytest.raises(rolemap.MapError, match="time window"):
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
