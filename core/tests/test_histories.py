"""Indistinguishable histories (B12): pairs of invented source histories that differ in one distinction a capability
needs, normalised into the role rows, with the column that carries the distinction asserted to differ.

Each pair is written as rows of the invented hospital's own tables, which are invented and public, and normalised
through the invented map exactly as its role views are written, translated for DuckDB. The drug part and the readings
are bound by the map, so the first six pairs go through it. The map does not yet bind the events part, so the seventh
pair builds its role rows directly from the contract's columns and vocabulary, and it is expected to fail: it records a
distinction that the contract does not yet carry.

If two histories that a capability must tell apart give the same role rows, no question over the roles can tell them
apart, whatever its SQL. That is the sharpest test of whether the roles hold enough, and it needs no hospital.
"""
import csv
import datetime as dt
from pathlib import Path

import duckdb
import pytest

from schemalyser import rolemap
from schemalyser.translate import to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MAP = rolemap.read_map(FIXTURES / "map")
DUCK_TYPES = {"varchar": "VARCHAR", "nvarchar": "VARCHAR", "char": "VARCHAR", "int": "INTEGER", "datetime": "TIMESTAMP"}


def _catalogue():
    """The invented catalogue's columns, as {table: [(column, DuckDB type)]} in their order."""
    tables = {}
    with (FIXTURES / "invented-catalogue.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            kind = DUCK_TYPES.get(row["DATA_TYPE"])
            if row["DATA_TYPE"] == "numeric":
                kind = f"DECIMAL({row['NUMERIC_PRECISION']},{row['NUMERIC_SCALE']})"
            tables.setdefault(row["TABLE_NAME"], []).append((row["COLUMN_NAME"], kind or "VARCHAR"))
    return tables


CATALOGUE = _catalogue()


def normalise(view, history):
    """The rows of a role view that the invented map gives for a history of the invented hospital's tables, as a list of
    {column: value} sorted by every value. history is {table: [{column: value}]}; a table the map reads and the history
    leaves out is empty."""
    con = duckdb.connect()
    dates = set()
    for table in MAP["tables"][view]:
        columns = CATALOGUE[table]
        dates |= {name for name, kind in columns if kind == "TIMESTAMP"}
        con.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{name}" {kind}' for name, kind in columns) + ")")
        for row in history.get(table, []):
            names = list(row)
            con.execute(f'INSERT INTO "{table}" (' + ", ".join(f'"{n}"' for n in names) + ") VALUES ("
                        + ", ".join("?" for _ in names) + ")", [row[n] for n in names])
    (statement,) = to_duckdb(MAP["views"][view], dates)
    cursor = con.execute(statement)
    names = [d[0].lower() for d in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    con.close()
    return sorted(rows, key=lambda row: [(v is None, str(v)) for v in row.values()])


def differing(first, second):
    """The columns in which two role rows differ."""
    return {column for column in first if first[column] != second[column]}


def at(hour, minute):
    return dt.datetime(2024, 8, 6, hour, minute)


# The invented hospital's codes, as the map translates them: the drug 990005190 is a mapped drug, the unit 12 is the
# unit the translations list, the route 1 is intravenous, the action 6 starts an infusion and 1 is a dose given.
GIVEN = {"GIVEN_KEY": 1, "VISIT_KEY": 10, "ANAES_KEY": "990007300", "DRUG_KEY": "990005190", "GIVEN_TS": at(8, 20),
         "DOSE_AMT": 0.1, "DOSE_UNIT_CAT": 12, "ROUTE_CAT": 1, "ACTION_CAT": 6, "ORDER_KEY": 100, "DOC_TS": at(8, 21),
         "AMENDS_KEY": None}
SHEET = {"SHEET_KEY": "S1", "VISIT_KEY": 10, "ANAES_KEY": "990007300"}
READING = {"SHEET_KEY": "S1", "SEQ": 1, "OBS_TYPE_KEY": "52", "READ_TS": at(8, 30), "READ_VALUE": "45", "ACCEPTED_FLAG": "Y"}


def test_an_order_and_an_administration_of_the_same_drug_at_the_same_time_differ_in_their_source_kind():
    # Drug exposure needs this: an order is not an exposure, and a question that counts what was given must be able
    # to leave the orders out without inferring which rows they are.
    order = {"ORDER_KEY": 100, "VISIT_KEY": 10, "ANAES_KEY": "990007300", "DRUG_KEY": "990005190", "ORDER_TS": at(8, 20),
             "DOSE_AMT": 0.1, "DOSE_UNIT_CAT": 12, "ROUTE_CAT": 1}
    (ordered,) = normalise("role_drug", {"DRUG_ORDER": [order]})
    (given,) = normalise("role_drug", {"DRUG_GIVEN": [dict(GIVEN, DOC_TS=None)]})
    assert (ordered["source_kind"], given["source_kind"]) == ("order", "administration")
    assert differing(ordered, given) == {"drug_event_key", "action", "source_kind"}
    assert ordered["given_time"] == given["given_time"] and ordered["drug"] == given["drug"]


def test_an_entry_made_at_the_time_and_one_backdated_that_afternoon_differ_in_their_documented_time():
    # The documentation lag that a timing capability must measure before it trusts a charted time.
    (prompt,) = normalise("role_drug", {"DRUG_GIVEN": [GIVEN]})
    (backdated,) = normalise("role_drug", {"DRUG_GIVEN": [dict(GIVEN, DOC_TS=at(16, 0))]})
    assert differing(prompt, backdated) == {"documented_time"}
    assert prompt["given_time"] == backdated["given_time"] == at(8, 20)
    assert (prompt["documented_time"], backdated["documented_time"]) == (at(8, 21), at(16, 0))


def test_a_row_and_its_later_amendment_differ_from_the_row_alone_by_a_row_that_names_it_in_its_amends_key():
    # The infusion reconstruction needs this: a correction takes the place of the row it amends, so that the rate is
    # not counted twice.
    correction = dict(GIVEN, GIVEN_KEY=2, DOSE_AMT=0.12, DOC_TS=at(16, 0), AMENDS_KEY=1)
    alone = normalise("role_drug", {"DRUG_GIVEN": [GIVEN]})
    amended = normalise("role_drug", {"DRUG_GIVEN": [GIVEN, correction]})
    assert [row["amends_key"] for row in alone] == [None]
    assert sorted(row["amends_key"] or "" for row in amended) == ["", "given-1"]
    original = next(row for row in amended if row["amends_key"] is None)
    assert original == alone[0]
    corrected = next(row for row in amended if row["amends_key"] is not None)
    assert corrected["source_kind"] == "correction" and float(corrected["amount"]) == 0.12


def test_two_overlapping_infusions_on_different_orders_differ_from_one_order_in_their_order_key():
    # Vasoactive exposure needs this: two infusions of one drug that run at once are two exposures, and a second start
    # on the same order is a restatement of one.
    second = dict(GIVEN, GIVEN_KEY=2, GIVEN_TS=at(8, 30), DOC_TS=at(8, 31))
    two = normalise("role_drug", {"DRUG_GIVEN": [GIVEN, dict(second, ORDER_KEY=101)]})
    one = normalise("role_drug", {"DRUG_GIVEN": [GIVEN, second]})
    by_key = {row["drug_event_key"]: row for row in one}
    assert {row["drug_event_key"]: differing(row, by_key[row["drug_event_key"]]) for row in two} == {
        "given-1": set(), "given-2": {"order_key"}}
    assert sorted(row["order_key"] for row in two) == ["100", "101"]
    assert sorted(row["order_key"] for row in one) == ["100", "100"]


def test_a_reading_filed_twice_as_the_same_event_and_two_readings_of_one_value_differ_in_their_reading_key():
    # Hypotension burden needs this: the same reading filed twice stands once, and two readings at one time are two
    # readings that the capability chooses between.
    twice = normalise("role_reading", {"OBS_SHEET": [SHEET], "OBS_READING": [READING, READING]})
    two = normalise("role_reading", {"OBS_SHEET": [SHEET], "OBS_READING": [READING, dict(READING, SEQ=2)]})
    assert [row["reading_key"] for row in twice] == ["S1-1", "S1-1"]
    assert [row["reading_key"] for row in two] == ["S1-1", "S1-2"]
    assert differing(twice[1], two[1]) == {"reading_key"}


def test_a_reading_that_was_never_charted_and_one_charted_as_unobtainable_differ_in_a_row_with_no_value():
    # Monitoring completeness needs this: a gap in the monitoring is not a charted statement that no value could be
    # obtained, and neither is a stable patient.
    later = dict(READING, SEQ=2, READ_TS=at(8, 35))
    missing = normalise("role_reading", {"OBS_SHEET": [SHEET], "OBS_READING": [READING]})
    absent = normalise("role_reading", {"OBS_SHEET": [SHEET], "OBS_READING": [READING, dict(later, READ_VALUE="unable to obtain")]})
    assert [row["reading_time"] for row in missing] == [at(8, 30)]
    assert [row["reading_time"] for row in absent] == [at(8, 30), at(8, 35)]
    recorded = absent[1]
    assert recorded["value"] is None and recorded["value_text"] == "unable to obtain" and recorded["kind"] == "map_arterial"


# The events part is not yet bound by the invented map, so its role rows are built directly: each charted event gives
# one row with the contract's columns, its kind the word of the contract's vocabulary that the invented code names, or
# other where the vocabulary has no such word, as the contract says of other.

EVENT_CODES = {"IND": "induction", "PARENT_IN": "parent_present_at_induction", "PARENT_OUT": "parent_absent_at_induction"}


def _role_events(charted):
    model = rolemap.contract()
    view = next(v for v in model["views"] if v["name"] == "role_event")
    words = {k["kind"] for k in model["vocabularies"]["event"]}
    rows = []
    for number, (code, moment) in enumerate(charted, 1):
        source = {"event_key": f"E{number}", "anaesthetic_key": "990007300", "stay_key": "10",
                  "kind": EVENT_CODES[code] if EVENT_CODES[code] in words else "other", "event_time": moment,
                  "source_kind": "procedure_log", "documented_time": moment, "amends_key": None}
        rows.append({column["name"]: source.get(column["name"]) for column in view["columns"]})
    return rows


@pytest.mark.xfail(strict=True, reason=(
    "The contract carries no distinction between a parent present at induction and a parent absent: role_event's "
    "vocabulary has no kind for either, so both are charted as other, and the induction quality capability, which "
    "splits the induction behaviour score by parental presence where charted, cannot be answered through the roles."))
def test_a_parent_present_at_induction_and_a_parent_absent_differ_in_the_role_rows():
    present = _role_events([("IND", at(8, 0)), ("PARENT_IN", at(8, 0))])
    absent = _role_events([("IND", at(8, 0)), ("PARENT_OUT", at(8, 0))])
    assert present != absent
