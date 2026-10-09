"""The roles, the maps, the role-level shadow and the standard counts.

An audit written once against the role views must give each planted neonate its expected minutes with no map at all,
must give the same answer through each world's map as the OMOP target gives through the conversion, and a map that
silently stops reaching the readings of earlier years must be caught by the counts, which the audit alone never does.
"""
import io
import json
import re
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import sqlglot

from schemalyser import harness, rolemap
from schemalyser.translate import to_duckdb

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
MAP = FIXTURES / "map"
CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_text()
TARGET = (FIXTURES / "targets" / "neonatal_low_mean_pressure.sql").read_text()
REALISTIC = ROOT / "reference" / "worlds" / "clarity"
REALISTIC_CONVERSION = ROOT / "etl" / "clarity"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

BANDS = ["no mean pressure recorded", "none", "under 5 minutes", "5 to 14 minutes", "15 minutes or more"]
# What the planted neonates imply for each band: anaesthetics, and those after which the child died within 90 days.
IMPLIED = {"no mean pressure recorded": (1, 0), "none": (4, 0), "under 5 minutes": (3, 1), "5 to 14 minutes": (5, 3),
           "15 minutes or more": (0, 0)}
# The target's own per-anaesthetic minutes, keyed by the source key of the anaesthetic, for comparing anaesthetic by anaesthetic.
TARGET_DETAIL = ("SELECT vd.visit_detail_source_value AS anaesthetic_key, l.minutes_below_40 FROM neonatal n "
                 "JOIN omop.anaesthetic a ON a.anaesthetic_id = n.anaesthetic_id "
                 "JOIN omop.visit_detail vd ON vd.visit_detail_id = a.visit_detail_id "
                 "LEFT JOIN low l ON l.anaesthetic_id = n.anaesthetic_id")


def _key(value):
    """A key as text, whether the world holds it as text or as a number."""
    return str(int(value)) if isinstance(value, (int, float)) or type(value).__name__ == "Decimal" else str(value)


def _target_detail(con, date_columns):
    tree = sqlglot.parse_one(TARGET, dialect="tsql")
    final = sqlglot.parse_one(TARGET_DETAIL, dialect="tsql")
    final.set("with_" if "with_" in final.arg_types else "with", (tree.args.get("with_") or tree.args.get("with")).copy())
    rows = con.execute(to_duckdb(final.sql(dialect="tsql"), date_columns)[0]).fetchall()
    return {_key(k): None if m is None else round(float(m), 6) for k, m in rows}


def _role_detail(run):
    return {_key(k): None if m is None else round(m, 6) for k, (m, _) in rolemap.per_anaesthetic(run).items()}


def _bands(rows):
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


@pytest.fixture(scope="module")
def role_runs():
    return {label: rolemap.duckdb_runner(rolemap.role_shadow(seed=1, anaesthetics=400, with_planted=planted))
            for label, planted in (("without", False), ("with", True))}


@pytest.fixture(scope="module")
def invented():
    roles_map = rolemap.read_map(MAP, CATALOGUE)
    return rolemap.hospital_run(make_checks.WORLD, CONVERSION, roles_map, rows=500, target_sql=TARGET)


@pytest.fixture(scope="module")
def realistic():
    if not (REALISTIC / "map" / "map.json").exists():
        pytest.skip("the realistic world is private and is not present here")
    roles_map = rolemap.read_map(REALISTIC / "map", (REALISTIC / "catalogue.csv").read_text())
    return rolemap.hospital_run(harness.World.from_folder(REALISTIC), REALISTIC_CONVERSION, roles_map, rows=50, target_sql=TARGET)


# The contract and the maps.

def test_the_contract_holds_three_role_views_and_the_kinds_of_mean_pressure():
    assert rolemap.views() == {
        "role_patient": ["patient_key", "birth_date", "death_date", "is_test"],
        "role_anaesthetic": ["anaesthetic_key", "patient_key", "start_time", "stop_time"],
        "role_reading": ["anaesthetic_key", "kind", "reading_time", "value", "accepted", "reading_key", "value_text"]}
    assert {"map_arterial", "map_cuff"} <= set(rolemap.kinds())
    rolemap.check_audit(rolemap.AUDIT.read_text())


def test_the_contract_is_version_one_and_marks_its_further_views_as_drafts():
    model = rolemap.contract()
    # Version 1.1 is additive: the three parts of version 1 are unchanged, and the mapping views, the source kinds and
    # the capability catalogue sit beside them. The unit stay gave way to transfers, and techniques joined the drafts.
    assert model["version"] == "1.1"
    statuses = rolemap.statuses()
    assert {name for name, status in statuses.items() if status == "contract"} == set(rolemap.views())
    assert len([s for s in statuses.values() if s == "draft"]) == 15 and set(statuses.values()) == {"contract", "draft"}
    # A reading is identified by its own key, because two readings may share their anaesthetic, kind and time, and it
    # still belongs in the view only through its anaesthetic.
    reading = next(v for v in model["views"] if v["name"] == "role_reading")
    assert reading["key"] == ["reading_key"] and rolemap.anchors(reading) == {"anaesthetic_key"}
    assert "daylight saving" in model["rules"][1] and "value_text" in model["rules"][7]
    roles = (rolemap.MODEL / "roles.md").read_text()
    assert "## How each role relates to OMOP" in roles and roles.count("draft, not yet used by any audit") == 15


def test_the_count_of_empty_values_gives_each_reason_apart():
    con = rolemap.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra={"role_reading": [
        ["A1", "map_cuff", "2024-01-01 10:00:00", 50.0, 1, "R1", None],
        ["A1", "map_cuff", "2024-01-01 10:05:00", None, 1, "R2", None],
        ["A1", "map_cuff", "2024-01-01 10:05:00", None, 1, "R3", "cuff off"],
        ["A1", "map_cuff", "2024-01-01 10:10:00", 12.0, 0, "R4", None]]})
    run = rolemap.duckdb_runner(con)
    columns, rows = run(rolemap.count_queries(1, 1)["empty_values_by_reason"]["sql"])
    assert columns == ["kind", "readings", "not_held", "not_a_number", "not_accepted"]
    assert [tuple(row) for row in rows] == [("map_cuff", 4, 1, 1, 1)]
    # Two readings at the same moment are two rows, and their own keys tell them apart.
    _, repeated = run(rolemap.count_queries(1, 1)["repeated_keys"]["sql"])
    assert all(row[1] == 0 for row in repeated if row[0] == "role_reading")


def test_the_invented_map_is_checked_against_the_catalogue_and_lists_its_open_items():
    roles_map = rolemap.read_map(MAP, CATALOGUE)
    assert roles_map["tables"]["role_reading"] == ["OBS_READING", "OBS_SHEET"]
    items = rolemap.open_items(roles_map)
    assert items and all(item["question"].startswith("Please confirm whether") for item in items)
    # Nothing in the invented map is confirmed yet, so every binding is open, and so is the map's own question. The map
    # supplies the staff and the diagnoses as well, and leaves the fluids and the laboratory results unsupplied, because
    # the invented world records neither. The diagnoses carry their own key, their source kind, the time each was
    # entered and the row it amends, in place of the hospital's code, classification and name, so they have ten.
    assert len(items) == 3 + 13 + 2 + 1 + 9 + 10
    assert {"role_staff", "role_diagnosis"} <= set(roles_map["views"]) and not {"role_fluid", "role_lab"} & set(roles_map["views"])
    assert {"kind map_arterial", "role_anaesthetic.patient_key"} <= {item["about"] for item in items}


@pytest.mark.parametrize("view, sql, says", [
    ("role_patient", "SELECT pm.PERSON_KEY AS patient_key, pm.BIRTH_TS AS birth_date, pm.BIRTH_TS AS death_date, 0 AS is_test "
                     "FROM PERSON_MASTER pm; SELECT 1", "exactly one statement"),
    ("role_patient", "SELECT pm.PERSON_KEY AS patient_key, pm.BIRTH_TS AS birth_date FROM PERSON_MASTER pm", "the contract asks"),
    ("role_patient", "SELECT p.PERSON_KEY AS patient_key, p.BIRTH_TS AS birth_date, p.BIRTH_TS AS death_date, 0 AS is_test "
                     "FROM NO_SUCH_TABLE p", "does not hold the table"),
    ("role_patient", "SELECT pm.PERSON_KEY AS patient_key, pm.NO_SUCH AS birth_date, pm.BIRTH_TS AS death_date, 0 AS is_test "
                     "FROM PERSON_MASTER pm", "does not hold the column NO_SUCH"),
    ("role_patient", "WITH x AS (SELECT pm.PERSON_KEY FROM PERSON_MASTER pm) SELECT x.PERSON_KEY AS patient_key, "
                     "NULL AS birth_date, NULL AS death_date, 0 AS is_test FROM x", "may not begin with WITH"),
    ("role_patient", "SELECT PERSON_KEY AS patient_key, BIRTH_TS AS birth_date, DEATH_TS AS death_date, 0 AS is_test "
                     "FROM PERSON_MASTER pm JOIN PERSON_MASTER_2 pm2 ON pm2.PERSON_KEY = pm.PERSON_KEY", "names no table"),
    ("role_patient", "SELECT pm.PERSON_KEY AS patient_key, pm.BIRTH_TS AS birth_date, pm.BIRTH_TS AS death_date, @x AS is_test "
                     "FROM PERSON_MASTER pm", "variable"),
    ("role_reading", "SELECT r.anaesthetic_key, r.kind, r.reading_time, r.value, r.accepted, r.reading_key, r.value_text "
                     "FROM role_reading r", "not role_reading"),
])
def test_a_role_view_that_breaks_a_rule_is_refused(view, sql, says):
    from schemalyser.catalogue import Catalogue
    with pytest.raises(rolemap.MapError, match=says):
        rolemap.check_view(sql, view, Catalogue.from_csv(CATALOGUE))


def test_the_compiled_audit_is_one_statement_with_the_role_views_read_without_locks():
    roles_map = rolemap.read_map(MAP, CATALOGUE)
    compiled = rolemap.compile_query(rolemap.AUDIT.read_text(), roles_map, blank=True)
    trees = [tree for tree in sqlglot.parse(compiled, dialect="tsql") if tree is not None]
    assert len(trees) == 1 and isinstance(trees[0], sqlglot.exp.Select)
    names = [cte.alias for cte in trees[0].find_all(sqlglot.exp.CTE)]
    assert names[:3] == ["role_patient", "role_anaesthetic", "role_reading"] and "neonatal" in names and "result" in names
    code = "\n".join(line for line in compiled.splitlines() if not line.lstrip().startswith("--"))
    assert code.count("WITH (NOLOCK)") == 7 and "BETWEEN 1 AND 4 THEN NULL" in code
    assert compiled.startswith("-- Among neonates") and "for use inside the hospital only" in compiled
    # Every count compiles in the same way, and no count can return a group that fewer than ten rows hold.
    for item in rolemap.count_queries().values():
        assert "% 10" in rolemap.compile_query(item["sql"], roles_map)


# A. The role-level shadow, with no map at all.

def test_the_audit_gives_each_planted_neonate_its_minutes_and_death_on_the_role_level_shadow(role_runs):
    found = rolemap.per_anaesthetic(role_runs["with"])
    for case in rolemap.planted()["expectations"]:
        if not case["counted"]:
            assert case["anaesthetic_key"] not in found
            continue
        minutes, died = found[case["anaesthetic_key"]]
        assert (minutes, died) == (case["minutes_below_40"], case["died_within_90_days"]), case["says"]


def test_the_planted_neonates_move_each_band_by_exactly_what_they_imply(role_runs):
    before = rolemap.result(role_runs["without"])
    after = rolemap.result(role_runs["with"])
    assert [row[0] for row in before["rows"]] == BANDS == [row[0] for row in after["rows"]]
    b, a = _bands(before["rows"]), _bands(after["rows"])
    assert {band: (a[band][0] - b[band][0], a[band][1] - b[band][1]) for band in BANDS} == IMPLIED
    # The implied moves follow from the expectations themselves.
    implied = {band: [0, 0] for band in BANDS}
    for case in rolemap.planted()["expectations"]:
        if case["counted"]:
            implied[case["band"]][0] += 1
            implied[case["band"]][1] += case["died_within_90_days"]
    assert {band: tuple(n) for band, n in implied.items()} == IMPLIED
    # The generated rows reach every band below 40, and the healthy shadow raises no cliff and flags no year.
    assert all(b[band][0] > 0 for band in BANDS[2:])
    assert after["flagged_years"] == {} and not any("falls from" in f for f in after["findings"])
    assert all(row["anaesthetics"] >= 10 and row["anaesthetics"] % 10 == 0 for row in after["coverage"])


# B. Each world, through its map, against the existing target through the conversion.

def test_the_invented_map_gives_the_target_s_answer_anaesthetic_by_anaesthetic(invented):
    found, target = invented["result"], invented["target"]
    assert target["failures"] == []
    assert found["rows"] == target["rows"]
    converted = invented["conversion"]
    assert _role_detail(invented["run"]) == _target_detail(converted.con, converted.sandbox.date_columns)
    assert found["flagged_years"] == {}


def test_the_realistic_map_gives_the_target_s_answer_anaesthetic_by_anaesthetic(realistic):
    found, target = realistic["result"], realistic["target"]
    assert target["failures"] == []
    assert found["rows"] == target["rows"]
    converted = realistic["conversion"]
    assert _role_detail(realistic["run"]) == _target_detail(converted.con, converted.sandbox.date_columns)


@pytest.mark.parametrize("which", ["invented", "realistic"])
def test_each_map_reads_the_planted_source_rows_as_the_planted_role_rows(which, request):
    done = request.getfixturevalue(which)
    run = done["run"]
    cases = rolemap.planted()
    keys = sorted({row[0] for row in cases["role_anaesthetic"]["rows"]})
    for view, key_column in (("role_anaesthetic", "anaesthetic_key"), ("role_reading", "anaesthetic_key")):
        # A reading's own key and its text are the hospital's, so the planted rows are compared without them.
        columns = [c for c in rolemap.views()[view] if c not in ("reading_key", "value_text")]
        _, rows = run(f"SELECT {', '.join(columns)} FROM {view}")
        mine = sorted((tuple(_plain(v) for v in row) for row in rows if _key(row[0]) in keys), key=str)
        expected = sorted((tuple(_plain(v) for v in row[:len(columns)]) for row in cases[view]["rows"]), key=str)
        assert mine == expected, view
    # Every reading has a key, and a value read as a number keeps no text. The worlds' generated tables do not hold
    # their keys of two columns unique, so the key's uniqueness is the role-level shadow's to show, as above.
    _, rows = run("SELECT reading_key, value, value_text FROM role_reading")
    assert all(row[0] for row in rows) and all(row[2] is None for row in rows if row[1] is not None)
    _, rows = run("SELECT patient_key, birth_date, death_date, is_test FROM role_patient")
    patients = {row[0] for row in cases["role_patient"]["rows"]}
    assert sorted((tuple(_plain(v) for v in row) for row in rows if _key(row[0]) in patients), key=str) == \
        sorted((tuple(_plain(v) for v in row) for row in cases["role_patient"]["rows"]), key=str)


def _plain(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
    if isinstance(value, (int, float)) or type(value).__name__ == "Decimal":
        number = float(value)
        return str(int(number)) if number == int(number) else str(number)
    return str(value)


# C, on DuckDB. The standard counts run through each map.

@pytest.mark.parametrize("which", ["invented", "realistic"])
def test_the_standard_counts_run_through_each_map_and_are_rounded(which, request):
    counts = request.getfixturevalue(which)["result"]["counts"]
    assert set(counts) == set(rolemap.count_queries())
    for name, (columns, rows) in counts.items():
        for row in rows:
            for column, value in zip(columns, row):
                if column in ("anaesthetics", "readings", "with_patient", "with_reading", "with_mean_pressure", "keys_repeated"):
                    assert int(value) % 10 == 0, (name, column)


# D. A broken map is caught by the counts and not by the audit.

def test_a_map_that_stops_reaching_earlier_readings_is_caught_by_the_counts_and_not_by_the_audit(invented, tmp_path):
    broken = tmp_path / "map"
    shutil.copytree(MAP, broken)
    sql = (broken / "role_reading.sql").read_text()
    # The reading is reached through a link that only the records from 2025 carry, as a filing that changed in that
    # year would leave it.
    sql = sql.replace("JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY",
                      "JOIN OBS_SHEET s ON s.SHEET_KEY = r.SHEET_KEY\n"
                      "       JOIN ANAES_RECORD ar ON ar.ANAES_KEY = s.ANAES_KEY AND ar.ANAES_START_TS >= '2025-01-01'")
    (broken / "role_reading.sql").write_text(sql)
    roles_map = rolemap.read_map(broken, CATALOGUE)
    converted = invented["conversion"]
    found = rolemap.result(rolemap.duckdb_runner(converted.con, converted.sandbox.date_columns, roles_map))
    healthy = invented["result"]
    # The audit runs, and its bands still add up to the same neonatal anaesthetics, but the children of 2023 and 2024
    # have moved silently into the band in which nothing was recorded.
    total = lambda rows: sum(int(row[1]) for row in rows)  # noqa: E731
    assert total(found["rows"]) == total(healthy["rows"])
    nothing = _bands(found["rows"])["no mean pressure recorded"][0]
    assert nothing > _bands(healthy["rows"])["no mean pressure recorded"][0]
    assert sum(_bands(found["rows"])[band][0] for band in BANDS[1:]) < sum(_bands(healthy["rows"])[band][0] for band in BANDS[1:])
    # The counts flag the years in which the map no longer reaches the readings, and the result says so beside the band.
    assert set(found["flagged_years"]) == {2023, 2024} and healthy["flagged_years"] == {}
    assert any("any reading in their record falls from" in f and "in 2023" in f for f in found["findings"])
    years = dict(found["band_years"])
    flagged = years.get(2023, 0) + years.get(2024, 0)
    assert found["notes"]["no mean pressure recorded"] == (
        f"Of the {nothing} anaesthetics in this band, {flagged} come from years that the counts flag (2023 and 2024), "
        f"where the map may not reach the readings that were recorded.")


# The command line.

def test_the_command_line_checks_a_map_lists_its_open_items_and_compiles_the_audit():
    out = io.StringIO()
    with redirect_stdout(out):
        rolemap.main(["check", str(MAP), "--catalogue", str(FIXTURES / "invented-catalogue.csv")])
        rolemap.main(["open", str(MAP)])
        rolemap.main(["compile", str(MAP)])
    text = out.getvalue()
    assert "role_reading: one SELECT over OBS_READING, OBS_SHEET" in text and "The map has 38 open items." in text
    assert "kind map_cuff (proposed): Please confirm whether" in text and "WITH (NOLOCK)" in text


# How the proposals fared, by category of column.

def _proposal(answer=None, confidence="high", binding=None, replacement="", candidates=()):
    item = {"status": "proposed", "from": "the dictionary", "says": "A proposal.", "question": "A question.",
            "confidence": confidence, "binding": binding or {"table": "T", "column": "C"},
            "candidates": [{"from": c} for c in candidates]}
    if answer:
        item["confirmation"] = {"answer": answer, "replacement": replacement}
    return item


def test_the_scoreboard_counts_each_category_of_column_apart(tmp_path):
    data = {"roles": {
        "role_patient": {"rows": _proposal("yes"),
                         "columns": {"patient_key": _proposal("yes"), "birth_date": _proposal("no", replacement="P.BORN"),
                                     "death_date": _proposal(), "is_test": _proposal("not sure")}},
        "role_anaesthetic": {"rows": _proposal("yes"),
                             "columns": {"anaesthetic_key": _proposal("yes"),
                                         # A key reached through another table is a link, not a key.
                                         "patient_key": _proposal("no", replacement="CASE.PAT", candidates=["CASE.PAT"],
                                                                  binding={"table": "CASE", "column": "PAT",
                                                                           "path": [["A", "CASE_ID", "CASE", "ID"]]}),
                                         "start_time": _proposal("yes"), "stop_time": _proposal("yes", confidence="low")}},
        "role_reading": {"rows": _proposal(),
                         "columns": {"anaesthetic_key": _proposal("no", replacement="S.EPISODE",
                                                                  binding={"table": "R", "column": "ENC",
                                                                           "window": {"table": "A", "key": "ID", "output": "ID",
                                                                                      "start": "S", "stop": "E", "time": "T"}}),
                                     "kind": _proposal("no", replacement="R.TYPE", candidates=["R.TYPE"]),
                                     "reading_time": _proposal(), "value": _proposal("yes"), "accepted": _proposal(),
                                     "reading_key": _proposal("yes"), "value_text": _proposal()}}},
        "kinds": {}}
    assert rolemap.category("role_patient", None, data["roles"]["role_patient"]["rows"]) == "keys"
    assert rolemap.category("role_reading", "value_text") == "descriptive"
    board = rolemap.scoreboard(data)
    held = board["categories"]
    # Keys: three rows, the patient's and anaesthetic's own keys, and the reading's key.
    assert held["keys"] == {"proposals": 6, "as_proposed": 5, "listed": 0, "unlisted": 0, "not_sure": 0, "unanswered": 1}
    assert held["links"] == {"proposals": 2, "as_proposed": 0, "listed": 1, "unlisted": 1, "not_sure": 0, "unanswered": 0}
    assert held["timestamps"] == {"proposals": 5, "as_proposed": 2, "listed": 0, "unlisted": 1, "not_sure": 0, "unanswered": 2}
    assert held["codes"] == {"proposals": 3, "as_proposed": 0, "listed": 1, "unlisted": 0, "not_sure": 1, "unanswered": 1}
    assert held["descriptive"] == {"proposals": 2, "as_proposed": 1, "listed": 0, "unlisted": 0, "not_sure": 0, "unanswered": 1}
    assert sum(c["proposals"] for c in held.values()) == board["overall"]["proposals"] == 18
    text = board["text"]
    assert ("Among the links, which join one part to another through further tables or by a time window, the page made "
            "2 proposals. Of these, 0 were confirmed as proposed, 1 was corrected to an alternative that the page had "
            "listed, 1 was corrected to a column or table that the page had not listed, 0 were marked not sure, and 0 "
            "have no answer yet.") in text
    assert "Among the dates and times, the page made 5 proposals." in text
    assert "Among the descriptive columns, the page made 2 proposals." in text
    for name in ("T", "CASE", "PAT", "EPISODE", "TYPE"):
        assert not re.search(rf"\b{name}\b", text)
    assert "?" not in text and "!" not in text
    # A category in which the page made no proposal says so, and the command line prints the same text.
    del data["roles"]["role_reading"]
    assert "Among the links, which join one part to another through further tables or by a time window, the page made no proposal." \
        not in rolemap.scoreboard(data)["text"]
    data["roles"]["role_anaesthetic"]["columns"]["patient_key"] = _proposal("yes")
    assert ("Among the links, which join one part to another through further tables or by a time window, the page made "
            "no proposal.") in rolemap.scoreboard(data)["text"]
    saved = tmp_path / "map.json"
    saved.write_text(json.dumps(data), encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out):
        rolemap.main(["scoreboard", str(saved)])
    assert out.getvalue() == rolemap.scoreboard(data)["text"]


# Version 1.1: the mapping views, the source kinds, the shapes of the event parts, and the capability catalogue.

VERSION_1_0_KINDS = {"map_arterial", "map_cuff", "heart_rate", "spo2", "etco2", "temperature", "systolic_arterial",
                     "diastolic_arterial", "systolic_cuff", "diastolic_cuff", "central_venous_pressure", "fio2", "peep",
                     "tidal_volume", "respiratory_rate", "end_tidal_agent", "pain_score", "blood_glucose", "other"}


def test_every_mapping_view_has_one_shape_and_every_open_code_is_a_local_key_of_one():
    model = rolemap.contract()
    mappings = rolemap.mapping_views()
    assert set(mappings) == {"map_drug_concept", "map_procedure_concept", "map_diagnosis_concept", "map_lab_concept",
                             "map_unit_concept"}
    for name, mapping in mappings.items():
        assert [(c["name"], c["type"]) for c in mapping["columns"]] == [
            ("local_key", "local_key"), ("concept_id", "whole"), ("status", "kind"), ("provenance", "kind")]
        assert mapping["key"] == ["local_key", "concept_id"] and mapping["used_by"]
    assert [k["kind"] for k in model["vocabularies"]["mapping_status"]] == ["mapped", "unmapped", "ambiguous"]
    assert {"a person", "a reference conversion", "the hospital's own conversion"} <= {
        k["kind"] for k in model["vocabularies"]["mapping_provenance"]}
    # No draft part carries a hospital's own code or name: each column of an open domain is a local key of the mapping
    # view that names it, and nothing else names a mapping view.
    columns = {(v["name"], c["name"]): c for v in model["views"] for c in v["columns"]}
    for gone in ("role_drug.drug_name", "role_operation.procedure_code", "role_operation.procedure_name",
                 "role_diagnosis.code", "role_diagnosis.code_system", "role_diagnosis.name"):
        assert tuple(gone.split(".")) not in columns, gone
    keyed = {f"{v}.{c}": spec["mapping"] for (v, c), spec in columns.items() if spec["type"] == "local_key"}
    assert keyed == {used: name for name, mapping in mappings.items() for used in mapping["used_by"]}
    assert "local_key" in model["types"]


def test_the_shadow_plants_a_mapped_an_unmapped_an_ambiguous_and_an_unlisted_drug():
    con = rolemap.role_shadow(seed=1, anaesthetics=0)
    rows = con.execute("SELECT d.drug_event_key, m.status, m.concept_id FROM role_drug d "
                       "LEFT JOIN map_drug_concept m ON m.local_key = d.drug "
                       "WHERE d.action = 'dose' ORDER BY d.drug_event_key, m.concept_id").fetchall()
    statuses = [status for _, status, _ in rows]
    assert {"mapped", "unmapped", "ambiguous"} <= set(statuses) and None in statuses
    # An ambiguous key has a row for each concept it may mean, and an unmapped key the concept 0.
    assert sum(1 for _, status, _ in rows if status == "ambiguous") == 2
    assert all(concept == 0 for _, status, concept in rows if status == "unmapped")
    # The infusion is recorded as charted: an order, a start, a change of rate, a pause, a restart and a retrospective
    # correction that amends an earlier row, each a row with its own key and source kind, and no stop.
    infusion = con.execute("SELECT action, source_kind, amends_key, documented_time IS NOT NULL FROM role_drug "
                           "WHERE order_key = '990009500' ORDER BY given_time").fetchall()
    assert [a for a, _, _, _ in infusion] == ["ordered", "infusion_start", "rate_change", "infusion_pause", "infusion_restart",
                                              "rate_change"]
    assert infusion[0][1] == "order" and infusion[-1][1:] == ("correction", "990009003", True)
    # A drug whose source carries only the stay keeps the stay and an empty anaesthetic.
    assert con.execute("SELECT COUNT(*) FROM role_drug WHERE anaesthetic_key IS NULL AND stay_key IS NOT NULL").fetchone()[0] == 1


def test_every_event_part_has_a_key_of_its_own_a_source_kind_and_its_documentation_and_amendment():
    model = rolemap.contract()
    events = rolemap.event_parts()
    assert set(events) == {"role_transfer", "role_event", "role_drug", "role_technique", "role_fluid", "role_device",
                           "role_lab", "role_diagnosis"}
    for name, view in events.items():
        columns = {c["name"]: c for c in view["columns"]}
        # Its own key, so that no rule of the map collapses rows that share a time or a kind.
        assert len(view["key"]) == 1 and view["key"][0].endswith("_key") and view["key"][0] not in {l["column"] for l in view["links"]}
        assert columns["source_kind"]["vocabulary"] == "source_kind" and columns["source_kind"]["per_pathway"]
        assert set(view["source_kinds"]) <= set(rolemap.source_kinds())
        assert columns["documented_time"]["type"] == "datetime" and columns["amends_key"]["type"] == "key"
        # A part of an anaesthetic carries the stay as well, and no anchor drops a row that carries only the stay.
        if "anaesthetic_key" in columns and name != "role_technique":
            assert {"column": "stay_key", "to": "role_stay.stay_key"} in view["links"], name
            assert not rolemap.anchors(view), name
    # The source kinds are few and name no vendor's table.
    assert len(rolemap.source_kinds()) <= 12 and "correction" in rolemap.source_kinds()
    views = {v["name"]: v for v in model["views"]}
    assert "role_unit_stay" not in views
    transfer = [c["name"] for c in views["role_transfer"]["columns"]]
    assert transfer[:5] == ["transfer_key", "stay_key", "unit_kind", "direction", "transfer_time"]
    assert [k["kind"] for k in model["vocabularies"]["transfer_direction"]] == ["in", "out"]
    assert [k["kind"] for k in model["vocabularies"]["volume_form"]] == ["amount", "running_total"]
    assert "volume_form" in [c["name"] for c in views["role_fluid"]["columns"]]
    assert "technique" not in [c["name"] for c in views["role_anaesthetic_detail"]["columns"]]
    technique = [c["name"] for c in views["role_technique"]["columns"]]
    assert technique == ["anaesthetic_key", "technique_key", "kind", "laterality", "guidance", "catheter", "recorded_time",
                         "documented_time", "performer", "source_kind", "amends_key"]
    assert [k["kind"] for k in model["vocabularies"]["guidance"]] == ["ultrasound", "nerve_stimulator", "landmark", "none"]
    assert {"perineural", "epidural", "caudal", "intrathecal"} <= {k["kind"] for k in model["vocabularies"]["route"]}
    assert {"block_failure", "conversion_to_general", "block_complication"} <= {k["kind"] for k in model["vocabularies"]["event"]}
    assert {"infusion_pause", "infusion_restart", "ordered"} <= {k["kind"] for k in model["vocabularies"]["drug_action"]}
    # The parts are listed in the order of the export's sections: the drugs, then the techniques, then the fluids.
    order = [v["name"] for v in model["views"]]
    assert order.index("role_drug") + 1 == order.index("role_technique") == order.index("role_fluid") - 1
    # role_reading takes no source kind in version 1; that is a question for version 2.
    assert "source_kind" not in [c["name"] for c in views["role_reading"]["columns"]]


def test_two_events_that_share_their_anaesthetic_kind_and_time_are_both_kept():
    con = rolemap.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra={"role_event": [
        ["E1", "A1", None, "induction", "2024-01-01 10:00:00", "procedure_log", None, None],
        ["E2", "A1", None, "induction", "2024-01-01 10:00:00", "charted_value", "2024-01-01 12:00:00", None]]})
    assert con.execute("SELECT COUNT(*), COUNT(DISTINCT event_key), COUNT(DISTINCT source_kind) FROM role_event").fetchone() == (2, 2, 2)


def test_the_reading_kinds_grow_without_changing_a_kind_of_version_1():
    kinds = {k["kind"]: k for k in rolemap.contract()["kinds"]}
    assert VERSION_1_0_KINDS <= set(kinds) and list(kinds)[-1] == "other"
    added = set(kinds) - VERSION_1_0_KINDS
    assert {"fresh_gas_flow", "inspired_sevoflurane", "expired_desflurane", "inspired_nitrous_oxide", "peak_airway_pressure",
            "plateau_pressure", "ventilation_mode", "pain_score_flacc", "pain_score_numeric", "pain_score_faces",
            "nausea_score", "sedation_score", "ciba", "paed"} <= added
    assert all("unit" in k for k in kinds.values())
    assert kinds["fresh_gas_flow"]["unit"] == "L/min" and kinds["ventilation_mode"]["unit"] is None
    assert kinds["map_cuff"]["meaning"] == "A mean arterial pressure from a non-invasive cuff, in mmHg."


def test_the_parts_of_version_1_keep_their_definitions_and_the_mapping_views_have_hashes_of_their_own():
    hashes = rolemap.part_hashes()
    assert set(rolemap.mapping_views()) <= set(hashes)
    model = rolemap.contract()
    # A change to a mapping view is a change to every part that names it.
    changed = json.loads(json.dumps(model))
    changed["mapping_views"][0]["description"] += " Changed."
    again = rolemap.part_hashes(changed)
    assert again["map_drug_concept"] != hashes["map_drug_concept"] and again["role_drug"] != hashes["role_drug"]
    assert again["role_patient"] == hashes["role_patient"] and again["role_lab"] == hashes["role_lab"]


def test_a_map_s_concepts_and_pathways_are_checked_when_it_is_read(tmp_path):
    data = json.loads((MAP / "map.json").read_text())
    folder = tmp_path / "map"
    shutil.copytree(MAP, folder)

    def refused(change, says):
        held = json.loads(json.dumps(data))
        change(held)
        (folder / "map.json").write_text(json.dumps(held))
        with pytest.raises(rolemap.MapError, match=says):
            rolemap.read_map_json(folder)
    rows = [{"code": "1000000", "concept_id": 9100001, "status": "mapped", "provenance": "a person"}]
    refused(lambda d: d.update(concepts={"salt": "s", "views": {"map_nothing": {"rows": rows}}}), "mapping view of the contract")
    refused(lambda d: d.update(concepts={"salt": "s", "views": {"map_diagnosis_concept": {"rows": rows * 2}}}), "only an ambiguous code")
    refused(lambda d: d.update(concepts={"salt": "s", "views": {"map_diagnosis_concept": {"rows": [
        dict(rows[0], concept_id=0)]}}}), "only an unmapped row has the concept 0")
    refused(lambda d: d["roles"]["role_diagnosis"].update(source_kind="DIAG_TABLE"), "source kind")
    refused(lambda d: d["roles"]["role_staff"].update(pathways=[]), "only a part that records events")
    refused(lambda d: d["roles"]["role_diagnosis"].update(pathways=[{"name": "Bad name", "source_kind": "booking",
                                                                      "rows": {}, "columns": {}}]), "further pathway")
    # A sound translation is read, and the compiled mapping view gives each code as its key and never the code.
    held = json.loads(json.dumps(data))
    held["concepts"] = {"salt": "s", "views": {"map_diagnosis_concept": {"rows": rows}}}
    (folder / "map.json").write_text(json.dumps(held))
    found = rolemap.read_map(folder, CATALOGUE)
    key = rolemap.concept_key("s", "map_diagnosis_concept", "1000000")
    assert key in found["views"]["map_diagnosis_concept"] and "1000000" not in found["views"]["map_diagnosis_concept"]
    compiled = rolemap.compile_query("SELECT m.concept_id, COUNT(*) AS n FROM role_anaesthetic a JOIN map_diagnosis_concept m "
                                     "ON m.local_key = a.anaesthetic_key GROUP BY m.concept_id", found)
    assert "map_diagnosis_concept AS (" in compiled and "map_drug_concept AS (" not in compiled
