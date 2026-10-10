"""Tests for the SQL of the first five capabilities, run on the role shadow against answers written by hand and held out."""
import json
from pathlib import Path

import pytest

from schemalyser import capability, rolemap, rolepolicy, roleshadow
from schemalyser.translate import to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
HELD_OUT = FIXTURES / "held-out" / "capabilities"
PLANTED = json.loads((capability.FOLDER / "capabilities" / "planted.json").read_text())
FIRST_FIVE = ["hypotension_burden", "hypoxaemia_burden", "hypothermia", "monitoring_completeness", "volatile_consumption"]
# The neonatal audit's own settings, as the first instance of hypotension burden.
NEONATAL = {"kinds": [["map_arterial", 1], ["map_cuff", 2]], "direction": "below", "threshold_by_age_band": [[0, 28, 40.0]],
            "window_from_minutes": 0, "window_until_minutes": None, "reading_stands_minutes": 5}


def _run(name, parameters, rows=None, con=None):
    """The capability filled with its parameters and run on a role shadow: (columns, rows)."""
    con = con or roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=rows)
    statements = to_duckdb(capability.fill(name, parameters))
    assert len(statements) == 1
    cursor = con.execute(statements[0])
    return [d[0] for d in cursor.description], cursor.fetchall()


def _comparable(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return value


def _rows(rows):
    return sorted(([_comparable(v) for v in row] for row in rows), key=lambda row: [str(v) for v in row])


def test_the_first_five_each_name_their_sql_in_the_catalogue_and_the_file_names_them_back():
    assert capability.with_sql() == FIRST_FIVE
    for name in FIRST_FIVE:
        entry = rolemap.capabilities()[name]
        found = capability.read(name)
        assert entry["sql"] == f"capabilities/{name}.sql" and found["path"].is_file()
        assert found["sql"].startswith(f"-- capability: {name}\n-- version: {entry['version']}\n")
        # Every parameter stands in the SQL as {{name}}, and nothing else does.
        assert set(capability.PLACEHOLDER.findall(found["sql"])) == {p["name"] for p in entry["parameters"]}
        assert set(PLANTED[name]["parameters"]) == {p["name"] for p in entry["parameters"]}
    # The two burdens are one shape: the same statement, under their own words.
    statement = {name: "\n".join(line for line in capability.read(name)["sql"].splitlines() if not line.startswith("--"))
                 for name in ("hypotension_burden", "hypoxaemia_burden")}
    assert statement["hypotension_burden"] == statement["hypoxaemia_burden"]
    assert all(rolemap.capabilities()[name]["shape"] == "minutes_beyond_threshold" for name in statement)


@pytest.mark.parametrize("name", FIRST_FIVE)
def test_each_capability_filled_is_a_question_over_the_roles_that_the_role_policy_accepts(name):
    checked = rolepolicy.check(capability.fill(name, PLANTED[name]["parameters"]))
    assert checked["failed"] == [], checked


@pytest.mark.parametrize("name", FIRST_FIVE)
def test_each_capability_gives_the_held_out_answers_on_its_planted_cases(name):
    expected = json.loads((HELD_OUT / name / "expected.json").read_text())
    columns, rows = _run(name, PLANTED[name]["parameters"], PLANTED[name]["rows"])
    assert [c for c in columns if c in expected["columns"]] == expected["columns"]
    at = [columns.index(c) for c in expected["columns"]]
    assert _rows([[row[i] for i in at] for row in rows]) == _rows(expected["rows"]), expected["says"]


def test_the_neonatal_audit_is_the_first_instance_of_hypotension_burden():
    # On the role shadow with the planted neonates and four hundred generated anaesthetics, the capability with the
    # audit's settings gives the same minutes below 40 for every anaesthetic that the audit counts, and for no other.
    con = roleshadow.role_shadow(seed=1, anaesthetics=400)
    audit = roleshadow.per_anaesthetic(roleshadow.duckdb_runner(con))
    _, rows = _run("hypotension_burden", NEONATAL, con=con)
    banded = {str(key): minutes for key, _, threshold, _, minutes in rows if threshold is not None}
    assert set(banded) == set(audit) and len(audit) > 20
    for key, (minutes, _) in audit.items():
        assert (minutes is None) == (banded[key] is None), key
        assert minutes is None or abs(minutes - banded[key]) < 1e-9, key
    # The audit says so, and the capability says that the audit is its first instance.
    assert "neonatal_low_mean_pressure.sql" in capability.read("hypotension_burden")["sql"]


@pytest.mark.parametrize("values, says", [
    (dict(NEONATAL, direction="sideways"), "one of below, above"),
    (dict(NEONATAL, kinds=[["map_unicorn", 1]]), "not a kind of the contract"),
    (dict(NEONATAL, reading_stands_minutes="5; DROP TABLE x"), "is a number"),
    (dict(NEONATAL, window_from_minutes=1.5), "whole number"),
    (dict(NEONATAL, threshold_by_age_band=[]), "at least one row"),
    (dict(NEONATAL, threshold_by_age_band=[[0, 28]]), "each row gives"),
    ({k: v for k, v in NEONATAL.items() if k != "direction"}, "no value is given for direction"),
    (dict(NEONATAL, colour="red"), "colour is not a parameter"),
])
def test_a_value_that_is_not_of_its_parameter_s_type_is_refused(values, says):
    with pytest.raises(capability.CapabilityError, match=says):
        capability.fill("hypotension_burden", values)


def test_a_capability_without_sql_or_outside_the_catalogue_is_refused():
    with pytest.raises(capability.CapabilityError, match="names no SQL"):
        capability.read("transfusion")
    with pytest.raises(capability.CapabilityError, match="not a capability"):
        capability.read("minutes_of_unicorns")


def test_the_planted_cases_name_no_table_of_any_hospital_and_keep_to_the_planted_range():
    text = (capability.FOLDER / "capabilities" / "planted.json").read_text()
    keys = {row[0] for case in PLANTED.values() if isinstance(case, dict) for row in case["rows"]["role_anaesthetic"]}
    assert keys and all(990_000_000 <= int(key) <= 990_999_999 for key in keys)
    assert "OBS_" not in text and "ANAES_" not in text
