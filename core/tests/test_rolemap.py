"""The roles, the maps, the role-level shadow and the standard counts.

An audit written once against the role views must give each planted neonate its expected minutes with no map at all,
must give the same answer through each world's map as the OMOP target gives through the conversion, and a map that
silently stops reaching the readings of earlier years must be caught by the counts, which the audit alone never does.
"""
import io
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
        "role_reading": ["anaesthetic_key", "kind", "reading_time", "value", "accepted"]}
    assert {"map_arterial", "map_cuff"} <= set(rolemap.kinds())
    rolemap.check_audit(rolemap.AUDIT.read_text())


def test_the_invented_map_is_checked_against_the_catalogue_and_lists_its_open_items():
    roles_map = rolemap.read_map(MAP, CATALOGUE)
    assert roles_map["tables"]["role_reading"] == ["OBS_READING", "OBS_SHEET"]
    items = rolemap.open_items(roles_map)
    assert items and all(item["question"].startswith("Please confirm whether") for item in items)
    # Nothing in the invented map is confirmed yet, so every binding is open, and so is the map's own question. The map
    # supplies the staff and the diagnoses as well, and leaves the fluids and the laboratory results unsupplied, because
    # the invented world records neither.
    assert len(items) == 3 + 13 + 2 + 1 + 7 + 8
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
    ("role_reading", "SELECT r.anaesthetic_key, r.kind, r.reading_time, r.value, r.accepted FROM role_reading r", "not role_reading"),
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
        columns = rolemap.views()[view]
        _, rows = run(f"SELECT {', '.join(columns)} FROM {view}")
        mine = sorted((tuple(_plain(v) for v in row) for row in rows if _key(row[0]) in keys), key=str)
        expected = sorted((tuple(_plain(v) for v in row) for row in cases[view]["rows"]), key=str)
        assert mine == expected, view
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
    assert "role_reading: one SELECT over OBS_READING, OBS_SHEET" in text and "The map has 34 open items." in text
    assert "kind map_cuff (proposed): Please confirm whether" in text and "WITH (NOLOCK)" in text
