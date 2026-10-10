"""Properties of the clinical logic over the roles (B13), tested on generated role rows with hypothesis.

The planted scenarios are evidence for the cases they cover, and these properties are not a proof beyond them either.
What they add is that a rule which must hold for every history is tried on many small histories that nobody wrote by
hand: the drug exposure reconstruction of drug_exposure_infusion_roles.sql, run on a role shadow that holds generated
rows of role_drug, and two capabilities of the catalogue, hypotension burden and monitoring completeness, run on
generated readings.

The generators are kept small, a few anaesthetics with a few events each, so that each test runs well within a
minute. Where a property needs order to be otherwise unambiguous, the generators give every event of one infusion, and
every reading of one anaesthetic, a time of its own. The readings carry no amends key, so for the capabilities the
property that a row is not counted twice is tried with a reading filed twice as the same event, under one reading key.
"""
import datetime as dt
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemalyser import capability, convert, rolemap, roleshadow
from schemalyser.translate import to_duckdb

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
STEP = to_duckdb((FIXTURES / "conversion" / "drug_exposure_infusion_roles.sql").read_text(encoding="utf-8"))[0]
EXAMPLES = settings(max_examples=60, deadline=None, database=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
DAY = dt.datetime(2024, 3, 4, 8, 0)
RUNNING = ("infusion_start", "rate_change", "infusion_restart")
COLUMNS = {view["name"]: [c["name"] for c in view["columns"]]
           for view in rolemap.contract()["views"] + rolemap.contract()["mapping_views"]}


def _time(minutes):
    return (DAY + dt.timedelta(minutes=minutes)).isoformat(sep=" ")


@pytest.fixture(scope="module")
def shadow():
    """One role shadow for the module, emptied before each history, with the one OMOP table that the step reads."""
    con = roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False)
    con.execute(f"CREATE SCHEMA {convert.OMOP_SCHEMA}")
    fields = convert.cdm_fields()["visit_detail"]
    con.execute(f"CREATE TABLE {convert.OMOP_SCHEMA}.visit_detail (" + ", ".join(f'"{n}" {k}' for n, _, k in fields) + ")")
    yield con
    con.close()


def _load(con, rows):
    """Empties the role views and fills them with rows, {view: [{column: value}]}, in the order given."""
    for view in ("role_patient", "role_anaesthetic", "role_reading", "role_drug", "map_drug_concept"):
        con.execute(f"DELETE FROM {view}")
    con.execute(f"DELETE FROM {convert.OMOP_SCHEMA}.visit_detail")
    for view, held in rows.items():
        names = COLUMNS[view]
        for row in held:
            con.execute(f"INSERT INTO {view} VALUES (" + ", ".join("?" for _ in names) + ")", [row.get(n) for n in names])


def _plain(value):
    if isinstance(value, float) or hasattr(value, "as_integer_ratio") and not isinstance(value, int):
        return round(float(value), 6)
    return value


def _query(con, sql):
    cursor = con.execute(sql)
    names = [d[0].lower() for d in cursor.description]
    return [{n: _plain(v) for n, v in zip(names, row)} for row in cursor.fetchall()]


def _sorted(rows, leave_out=()):
    kept = [tuple((k, v) for k, v in row.items() if k not in leave_out) for row in rows]
    return sorted(kept, key=lambda row: [(v is None, str(v)) for _, v in row])


# The drug exposure reconstruction.

@st.composite
def infusions(draw, anaesthetic, first_key):
    """The role_drug rows of one anaesthetic's infusions: each is ordered or not, starts, then changes rate, pauses,
    restarts and stops as a nurse might chart it, each event at a minute of its own, and may end with no stop."""
    rows = []

    def event(order, drug, moment, action, amount):
        return {"drug_event_key": f"{anaesthetic}-E{first_key + len(rows) + 1:03d}", "anaesthetic_key": anaesthetic,
                "stay_key": "S1", "order_key": order, "drug": drug, "action": action, "amount": amount, "unit": "kunit",
                "route": "intravenous", "given_time": _time(moment),
                "source_kind": "order" if action == "ordered" else "administration", "documented_time": None,
                "amends_key": None}
    for number in range(draw(st.integers(1, 2))):
        order, drug = f"{anaesthetic}-O{number}", f"kdrug{number}"
        moment = draw(st.integers(0, 30))
        if draw(st.booleans()):
            rows.append(event(order, drug, moment, "ordered", 0.1))
            moment += draw(st.integers(1, 10))
        rows.append(event(order, drug, moment, "infusion_start", draw(st.sampled_from([0.1, 0.2, 0.5]))))
        state = "running"
        for _ in range(draw(st.integers(0, 5))):
            choices = ["rate_change", "infusion_pause", "infusion_stop"] if state == "running" else ["infusion_restart", "infusion_stop"]
            action = draw(st.sampled_from(choices))
            moment += draw(st.integers(1, 30))
            rows.append(event(order, drug, moment, action, draw(st.sampled_from([0.1, 0.2, 0.3, 1.0])) if action in RUNNING else None))
            state = {"infusion_pause": "paused", "infusion_stop": "stopped"}.get(action, "running")
            if state == "stopped":
                break
    return rows


def _anaesthetic_rows(anaesthetic, number):
    return {"role_patient": [{"patient_key": f"P{anaesthetic}", "birth_date": "2015-01-01", "death_date": None, "is_test": 0}],
            "role_anaesthetic": [{"anaesthetic_key": anaesthetic, "patient_key": f"P{anaesthetic}", "start_time": _time(0),
                                  "stop_time": _time(240)}],
            "visit_detail": [{"visit_detail_id": 5000 + number, "person_id": number, "visit_occurrence_id": 100 + number,
                              "visit_detail_source_value": anaesthetic}]}


def _exposures(con, drug_rows, anaesthetics=("A1",)):
    """The step's rows for a history of role_drug rows over the named anaesthetics."""
    rows = {"role_patient": [], "role_anaesthetic": [], "role_drug": drug_rows,
            "map_drug_concept": [{"local_key": "kdrug0", "concept_id": 9100001, "status": "mapped", "provenance": "a person"}]}
    visits = []
    for number, anaesthetic in enumerate(anaesthetics, 1):
        held = _anaesthetic_rows(anaesthetic, number)
        rows["role_patient"] += held["role_patient"]
        rows["role_anaesthetic"] += held["role_anaesthetic"]
        visits += held["visit_detail"]
    _load(con, rows)
    for visit in visits:
        con.execute(f"INSERT INTO {convert.OMOP_SCHEMA}.visit_detail (visit_detail_id, person_id, visit_occurrence_id, "
                    "visit_detail_source_value) VALUES (?, ?, ?, ?)", list(visit.values()))
    return _query(con, STEP)


def _minutes(row):
    return (row["drug_exposure_end_datetime"] - row["drug_exposure_start_datetime"]).total_seconds() / 60


@EXAMPLES
@given(history=infusions("A1", 0), order=st.randoms(use_true_random=False))
def test_the_order_in_which_the_drug_events_arrive_does_not_change_the_exposures(shadow, history, order):
    shuffled = list(history)
    order.shuffle(shuffled)
    assert _sorted(_exposures(shadow, shuffled)) == _sorted(_exposures(shadow, history))


@EXAMPLES
@given(history=infusions("A1", 0), other=infusions("A2", 500))
def test_another_anaesthetic_s_infusions_change_nothing_for_an_existing_anaesthetic(shadow, history, other):
    alone = _exposures(shadow, history, ("A1", "A2"))
    beside = [r for r in _exposures(shadow, history + other, ("A1", "A2")) if r["visit_detail_id"] == 5001]
    assert _sorted(beside, {"drug_exposure_id"}) == _sorted(alone, {"drug_exposure_id"})


@EXAMPLES
@given(history=infusions("A1", 0), data=st.data())
def test_a_row_that_amends_another_takes_its_place_and_is_not_counted_twice(shadow, history, data):
    running = [r for r in history if r["action"] in RUNNING]
    amended = data.draw(st.sampled_from(running))
    correction = dict(amended, drug_event_key="A1-C001", amount=7.5, source_kind="correction",
                      documented_time=_time(600), amends_key=amended["drug_event_key"])
    before = _exposures(shadow, history)
    after = _exposures(shadow, history + [correction])
    assert len(after) == len(before)
    moved = [r for r in after if r["drug_exposure_start_datetime"] == dt.datetime.fromisoformat(amended["given_time"])
             and r["sig"] in ("7.5", "7.50", "7.5000")]
    assert len(moved) == 1
    untouched = [r for r in before if r["drug_exposure_start_datetime"] != dt.datetime.fromisoformat(amended["given_time"])
                 or r["drug_source_value"] != f"{'mapped' if amended['drug'] == 'kdrug0' else 'unlisted'}:{amended['drug']}"]
    leave = {"drug_exposure_id"}
    assert set(_sorted(untouched, leave)) <= set(_sorted(after, leave))


@EXAMPLES
@given(history=infusions("A1", 0), data=st.data())
def test_a_pause_and_its_restart_take_exactly_their_own_time_out_of_the_exposure(shadow, history, data):
    before = _exposures(shadow, history)
    known = [r for r in before if r["drug_exposure_end_datetime"] is not None and _minutes(r) >= 3]
    if not known:
        return
    chosen = data.draw(st.sampled_from(known))
    length = int(_minutes(chosen))
    paused = data.draw(st.integers(1, length - 2))
    restarted = data.draw(st.integers(paused + 1, length - 1))
    started = chosen["drug_exposure_start_datetime"]
    source = next(r for r in history if dt.datetime.fromisoformat(r["given_time"]) == started and r["action"] in RUNNING
                  and chosen["drug_source_value"].endswith(":" + r["drug"]))

    def at(minutes):
        return (started + dt.timedelta(minutes=minutes)).isoformat(sep=" ")
    pause = dict(source, drug_event_key="A1-P001", action="infusion_pause", amount=None, given_time=at(paused))
    restart = dict(source, drug_event_key="A1-P002", action="infusion_restart", given_time=at(restarted))
    after = _exposures(shadow, history + [pause, restart])
    total = sum(_minutes(r) for r in before if r["drug_exposure_end_datetime"] is not None)
    assert sum(_minutes(r) for r in after if r["drug_exposure_end_datetime"] is not None) == pytest.approx(total - (restarted - paused))
    assert len(after) == len(before) + 1
    assert sum(r["drug_exposure_end_datetime"] is None for r in after) == sum(r["drug_exposure_end_datetime"] is None for r in before)


@EXAMPLES
@given(history=infusions("A1", 0), other=infusions("A2", 500))
def test_an_infusion_with_no_recorded_stop_keeps_an_end_that_is_unknown_with_its_reason(shadow, history, other):
    found = _exposures(shadow, history + other, ("A1", "A2"))
    # Written from the history alone: an infusion whose last event leaves it running has one interval of unknown end,
    # and an infusion that ends paused or stopped has none.
    expected = {}
    for row in history + other:
        if row["action"] == "ordered":
            continue
        key = (row["anaesthetic_key"], row["order_key"])
        if key not in expected or row["given_time"] > expected[key][0]:
            expected[key] = (row["given_time"], row["action"])
    unknown = sum(1 for _, action in expected.values() if action in RUNNING)
    assert sum(r["drug_exposure_end_datetime"] is None for r in found) == unknown
    for row in found:
        assert (row["drug_exposure_end_datetime"] is None) == (row["stop_reason"] == "stop not recorded"), row
        assert row["drug_exposure_end_datetime"] is None or row["drug_exposure_end_datetime"] > row["drug_exposure_start_datetime"]


# Two capabilities over generated readings.

BURDEN = {"kinds": [["map_arterial", 1], ["map_cuff", 2]], "direction": "below", "threshold_by_age_band": [[0, None, 40.0]],
          "window_from_minutes": 0, "window_until_minutes": None, "reading_stands_minutes": 5}
COMPLETENESS = {"kinds": [["map_cuff"], ["map_arterial"]], "expected_interval_minutes": 5, "window_from_minutes": 0,
                "window_until_minutes": None}
SQL = {name: to_duckdb(capability.fill(name, values))[0]
       for name, values in (("hypotension_burden", BURDEN), ("monitoring_completeness", COMPLETENESS))}


@st.composite
def anaesthetics(draw, key):
    """One anaesthetic with a stop or none, and its readings of a mean pressure, each at a minute of its own."""
    length = draw(st.integers(10, 120))
    stop = draw(st.booleans())
    minutes = draw(st.lists(st.integers(-5, length + 5), min_size=0, max_size=12, unique=True))
    readings = [{"anaesthetic_key": key, "kind": draw(st.sampled_from(["map_arterial", "map_cuff", "heart_rate"])),
                 "reading_time": _time(m), "value": float(draw(st.integers(20, 80))), "accepted": draw(st.sampled_from([1, 1, 1, 0])),
                 "reading_key": f"{key}-R{n}", "value_text": None} for n, m in enumerate(minutes)]
    return {"role_patient": [{"patient_key": f"P{key}", "birth_date": "2015-01-01", "death_date": None, "is_test": 0}],
            "role_anaesthetic": [{"anaesthetic_key": key, "patient_key": f"P{key}", "start_time": _time(0),
                                  "stop_time": _time(length) if stop else None}],
            "role_reading": readings, "length": length if stop else None}


def _capability(con, name, *histories, readings=None):
    rows = {"role_patient": [], "role_anaesthetic": [], "role_reading": []}
    for history in histories:
        for view in rows:
            rows[view] += history[view]
    if readings is not None:
        rows["role_reading"] = readings
    _load(con, rows)
    return {row["anaesthetic_key"]: row for row in _query(con, SQL[name])}


BOTH = pytest.mark.parametrize("name", ["hypotension_burden", "monitoring_completeness"])


@BOTH
@EXAMPLES
@given(history=anaesthetics("A1"), order=st.randoms(use_true_random=False))
def test_the_order_in_which_the_readings_arrive_does_not_change_a_capability_s_answer(shadow, name, history, order):
    shuffled = list(history["role_reading"])
    order.shuffle(shuffled)
    assert _capability(shadow, name, history, readings=shuffled) == _capability(shadow, name, history)


@BOTH
@EXAMPLES
@given(history=anaesthetics("A1"), other=anaesthetics("A2"))
def test_another_anaesthetic_s_readings_change_nothing_in_a_capability_s_answer_for_an_existing_one(shadow, name, history, other):
    assert _capability(shadow, name, history, other)["A1"] == _capability(shadow, name, history)["A1"]


@BOTH
@EXAMPLES
@given(history=anaesthetics("A1"), data=st.data())
def test_a_reading_filed_twice_as_the_same_event_is_not_counted_twice(shadow, name, history, data):
    if not history["role_reading"]:
        return
    twice = data.draw(st.sampled_from(history["role_reading"]))
    assert _capability(shadow, name, history, readings=history["role_reading"] + [dict(twice)]) == _capability(shadow, name, history)


@BOTH
@EXAMPLES
@given(history=anaesthetics("A1"))
def test_an_anaesthetic_with_no_recorded_stop_never_gains_a_known_duration(shadow, name, history):
    row = _capability(shadow, name, history)["A1"]
    if history["length"] is not None:
        return
    counted = sorted(dt.datetime.fromisoformat(r["reading_time"]) for r in history["role_reading"]
                     if r["accepted"] == 1 and r["kind"] != "heart_rate" and r["reading_time"] >= _time(0))
    if name == "monitoring_completeness":
        assert row["window_minutes"] is None and row["covered_minutes"] is None
        assert row["share_covered"] is None and row["longest_gap_minutes"] is None
    else:
        # The last reading stands for no time, so the minutes never reach past the last reading counted.
        span = (counted[-1] - counted[0]).total_seconds() / 60 if counted else 0
        assert row["minutes_beyond"] is None or row["minutes_beyond"] <= span + 1e-9


@EXAMPLES
@given(history=anaesthetics("A1"))
def test_the_minutes_of_hypotension_never_exceed_the_window(shadow, history):
    row = _capability(shadow, "hypotension_burden", history)["A1"]
    assert row["minutes_beyond"] is None or row["minutes_beyond"] >= 0
    if history["length"] is not None and row["minutes_beyond"] is not None:
        assert row["minutes_beyond"] <= history["length"]
    # A reading taken outside the window adds nothing to it, so the answer is the same without such readings.
    end = _time(history["length"]) if history["length"] is not None else _time(10_000)
    inside = [r for r in history["role_reading"] if _time(0) <= r["reading_time"] <= end]
    assert _capability(shadow, "hypotension_burden", history, readings=inside)["A1"] == row


@EXAMPLES
@given(history=anaesthetics("A1"))
def test_the_minutes_of_hypotension_are_zero_when_every_reading_is_on_the_safe_side(shadow, history):
    safe = [dict(r, value=max(r["value"], 40.0)) for r in history["role_reading"]]
    row = _capability(shadow, "hypotension_burden", history, readings=safe)["A1"]
    # With no reading counted the minutes are unknown, which is not zero.
    assert row["minutes_beyond"] == (0 if row["readings"] else None)


@EXAMPLES
@given(history=anaesthetics("A1"))
def test_the_minutes_covered_never_exceed_the_window(shadow, history):
    row = _capability(shadow, "monitoring_completeness", history)["A1"]
    if history["length"] is None:
        return
    assert 0 <= row["covered_minutes"] <= row["window_minutes"] == history["length"]
    assert 0 <= row["share_covered"] <= 1 and row["longest_gap_minutes"] <= history["length"]
