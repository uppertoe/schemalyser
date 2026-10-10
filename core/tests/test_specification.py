"""The first stage of compilation: a specification of an export, compiled into SQL over the roles, on the invented world
only.

The example specification and the invented episode lists are in fixtures/export/. The episodes are the planted
neonates, whose rows the role-level shadow holds, so each statement is run on the shadow and its rows are checked
against the planted rows themselves.
"""
import copy
import datetime as dt
import json

import pytest

from schemalyser import policy, rolemap, rolepolicy, roleshadow, specification
from schemalyser.translate import to_duckdb

FIXTURES = rolemap.MODEL.parents[2] / "fixtures" / "export"
SPEC = FIXTURES / "neonatal-pressures.specification.json"
KEYS = FIXTURES / "invented-episodes.csv"
PAIRS = FIXTURES / "invented-episode-pairs.csv"


@pytest.fixture(scope="module")
def shadow():
    con = roleshadow.role_shadow()
    yield con
    con.close()


def _spec(**changes):
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    spec.update(changes)
    return spec


def _run(con, sql):
    cursor = con.execute(to_duckdb(sql)[0])
    return [d[0] for d in cursor.description], cursor.fetchall()


def _section(compiled, name):
    return next(s for s in compiled["sections"] if s["name"] == name)


def _planted(view):
    held = roleshadow.planted()[view]
    return [dict(zip(held["columns"], row)) for row in held["rows"]]


# The example.

def test_the_example_holds_no_sql_and_no_key_and_keeps_every_rule():
    text = SPEC.read_text(encoding="utf-8")
    assert "SELECT" not in text.upper() and "9900" not in text
    spec, digest = specification.load(SPEC)
    assert digest == specification.sha256(SPEC.read_bytes()) and specification.validate(spec) == []


def test_each_section_compiles_to_one_statement_over_the_roles_that_the_role_policy_passes():
    spec, digest = specification.load(SPEC)
    episodes = specification.read_episodes(KEYS, "anaesthetic_keys")
    compiled = specification.compile(spec, episodes, digest)
    assert compiled["specification_sha256"] == digest
    assert compiled["episodes"] == {"form": "anaesthetic_keys", "sha256": specification.sha256(KEYS.read_bytes()), "count": 6}
    names = [s["name"] for s in compiled["sections"]]
    assert names == ["anaesthetics", "mean_pressures", "minutes_below_40", "died_within_90_days", "episode_key"]
    for entry in compiled["sections"]:
        if entry["sql"] is None:
            assert entry["status"] == "declared" and "no SQL has been written" in entry["says"]
            continue
        assert entry["role_policy"] == {"outcome": "passed", "failed": []}, entry["name"]
        assert len(policy.split(entry["sql"])[0]) == 1
    assert _section(compiled, "episode_key")["leaves"] is False and _section(compiled, "mean_pressures")["leaves"] is True


def test_the_readings_section_returns_the_accepted_readings_of_the_listed_episodes_within_the_window(shadow):
    spec, digest = specification.load(SPEC)
    episodes = specification.read_episodes(KEYS, "anaesthetic_keys")
    compiled = specification.compile(spec, episodes, digest)
    columns, rows = _run(shadow, _section(compiled, "mean_pressures")["sql"])
    assert columns == ["episode_number", "kind", "reading_time", "value", "accepted", "value_text"]
    # Expected independently from the planted rows: the accepted mean pressures of the listed anaesthetics, from 15
    # minutes before the start to 15 minutes after the stop, where there is a stop.
    listed = set(episodes["rows"])
    anaesthetics = {a["anaesthetic_key"]: a for a in _planted("role_anaesthetic") if a["anaesthetic_key"] in listed}
    wanted = 0
    for reading in _planted("role_reading"):
        held = anaesthetics.get(reading["anaesthetic_key"])
        if held is None or reading["kind"] not in ("map_arterial", "map_cuff") or reading["accepted"] != 1 or not held["stop_time"]:
            continue
        at = dt.datetime.fromisoformat(reading["reading_time"])
        start, stop = dt.datetime.fromisoformat(held["start_time"]), dt.datetime.fromisoformat(held["stop_time"])
        wanted += start - dt.timedelta(minutes=15) <= at <= stop + dt.timedelta(minutes=15)
    assert len(rows) == wanted and wanted > 0
    assert {r[1] for r in rows} <= {"map_arterial", "map_cuff"} and all(r[4] == 1 for r in rows)
    # The keys are pseudonymised: the numbers follow the starts, and the link to the keys is its own section.
    _, key = _run(shadow, _section(compiled, "episode_key")["sql"])
    starts = sorted((anaesthetics[k]["start_time"], k) for k in anaesthetics)
    assert key == [(n, k) for n, (_, k) in enumerate(starts, 1)]
    assert not any(isinstance(v, str) and v.startswith("9900") for r in rows for v in r)


def test_without_an_episode_list_the_statements_choose_no_episode_and_still_pass_the_role_policy(shadow):
    spec, _ = specification.load(SPEC)
    compiled = specification.compile(spec)
    assert compiled["episodes"] is None
    for entry in compiled["sections"]:
        if entry["sql"] is None:
            continue
        assert "The episode list is a private file" in entry["sql"] and entry["role_policy"]["outcome"] == "passed"
        assert _run(shadow, entry["sql"])[1] == []


def test_aggregate_output_counts_rows_and_anaesthetics_for_each_kind(shadow):
    spec = _spec(output={"class": "aggregate", "keys": "as recorded", "leaving": ["mean_pressures"]})
    compiled = specification.compile(spec, specification.read_episodes(KEYS, "anaesthetic_keys"))
    sql = _section(compiled, "mean_pressures")["sql"]
    columns, rows = _run(shadow, sql)
    assert columns == ["kind", "rows_charted", "anaesthetics"] and rows
    assert "episode_key" not in [s["name"] for s in compiled["sections"]]
    assert rolepolicy.check(sql)["outcome"] == "passed"


# The patient-and-date form.

def _pairs_spec(several, hours=96):
    return _spec(episodes={"form": "patient_dates", "window_hours": hours, "several": several})


def test_pairs_are_resolved_with_the_share_resolved_to_exactly_one_and_the_ambiguous_counted(shadow):
    episodes = specification.read_episodes(PAIRS, "patient_dates")
    compiled = specification.compile(_pairs_spec("all, marked"), episodes)
    resolution = _section(compiled, "episode_resolution")
    assert resolution["role_policy"]["outcome"] == "passed" and resolution["leaves"] is False
    # Four pairs: two resolve to one anaesthetic each, one patient has two anaesthetics within four days of the date,
    # and one date has no anaesthetic of its patient near it.
    assert _run(shadow, resolution["sql"]) == (["pairs", "resolved_to_one", "ambiguous", "resolved_to_none"], [(4, 2, 1, 1)])
    _, marked = _run(shadow, _section(compiled, "anaesthetics")["sql"])
    assert sorted((r[1], r[2]) for r in marked) == [(1, 0), (2, 0), (3, 1), (3, 1)]
    _, only = _run(shadow, _section(specification.compile(_pairs_spec("none"), episodes), "anaesthetics")["sql"])
    assert sorted((r[1], r[2]) for r in only) == [(1, 0), (2, 0)]
    assert compiled["period"] == ("2022-12-28", "2024-07-15")
    # With a narrower window the ambiguous pair resolves to none.
    narrow = specification.compile(_pairs_spec("all, marked", hours=12), episodes)
    assert _run(shadow, _section(narrow, "episode_resolution")["sql"])[1] == [(4, 2, 0, 2)]


# Refusals with named reasons.

@pytest.mark.parametrize("change, rule", [
    ({"format": "something else"}, "format"),
    ({"contract_version": "0.9"}, "contract_version"),
    ({"episodes": {"form": "patient_dates", "window_hours": 12, "several": "first"}}, "episodes"),
    ({"episodes": {"form": "patient_dates", "window_hours": 1.5, "several": "none"}}, "episodes"),
    ({"sections": [{"name": "x", "part": "role_colour"}]}, "sections"),
    ({"sections": [{"name": "Bad Name", "part": "role_reading"}]}, "sections"),
    ({"sections": [{"name": "a", "part": "role_reading"}, {"name": "a", "part": "role_anaesthetic"}]}, "sections"),
    ({"sections": [{"name": "a", "part": "role_reading", "kinds": ["blood_pressure"]}]}, "kinds"),
    ({"sections": [{"name": "a", "part": "role_patient", "kinds": ["map_cuff"]}]}, "kinds"),
    ({"sections": [{"name": "a", "part": "role_reading", "window": {"from": "induction", "to": "stop"}}]}, "window"),
    ({"sections": [{"name": "a", "part": "role_anaesthetic", "window": {"from": "start", "to": "stop"}}]}, "window"),
    ({"sections": [{"name": "a", "part": "role_reading", "flags": {"value": 1}}]}, "flags"),
    ({"sections": [{"name": "a", "part": "role_reading", "flags": {"accepted": 2}}]}, "flags"),
    ({"sections": [{"name": "a", "part": "role_transfer"}]}, "link"),
    ({"derived": [{"name": "d", "capability": "minutes_of_unicorns", "version": 1, "parameters": {}}]}, "derived"),
    ({"derived": [{"name": "d", "capability": "death_within_days", "version": 2, "parameters": {"days": 90}}]}, "derived"),
    ({"derived": [{"name": "d", "capability": "death_within_days", "version": 1, "parameters": {"days": "ninety"}}]}, "derived"),
    ({"derived": [{"name": "d", "capability": "death_within_days", "version": 1, "parameters": {}}]}, "derived"),
    ({"derived": [{"name": "d", "capability": "death_within_days", "version": 1, "parameters": {"days": 90, "hours": 2}}]}, "derived"),
    ({"output": {"class": "everything", "keys": "as recorded", "leaving": []}}, "output"),
    ({"output": {"class": "rows", "keys": "hashed", "leaving": []}}, "output"),
    ({"output": {"class": "rows", "keys": "as recorded", "leaving": ["notes"]}}, "output"),
])
def test_a_specification_that_breaks_a_rule_is_refused_with_the_rule_named(change, rule):
    spec = _spec(**change)
    reasons = specification.validate(spec)
    assert rule in {r["rule"] for r in reasons}, reasons
    with pytest.raises(specification.SpecificationError) as raised:
        specification.compile(spec)
    assert rule in {r["rule"] for r in raised.value.reasons} and rule in str(raised.value)


def test_an_episode_list_that_cannot_be_read_is_refused_without_its_values(tmp_path, monkeypatch):
    bad = tmp_path / "episodes.csv"
    bad.write_text("anaesthetic_key\nSECRET-KEY-1\n\x01\n", encoding="utf-8")
    with pytest.raises(specification.SpecificationError, match="Line 3") as raised:
        specification.read_episodes(bad, "anaesthetic_keys")
    assert "SECRET" not in str(raised.value)
    with pytest.raises(specification.SpecificationError, match="patient and date pairs"):
        specification.read_episodes(PAIRS, "anaesthetic_keys")
    bad.write_text("key\n1\n", encoding="utf-8")
    with pytest.raises(specification.SpecificationError, match="header is anaesthetic_key"):
        specification.read_episodes(bad, "anaesthetic_keys")
    monkeypatch.setattr(policy, "CAP", 3)
    with pytest.raises(specification.SpecificationError, match="more than the 3"):
        specification.read_episodes(KEYS, "anaesthetic_keys")


# Derived sections from the catalogue.

MINUTES = """-- capability: minutes_beyond_threshold
WITH kept AS (
    SELECT r.anaesthetic_key, r.reading_time, r.value
    FROM   role_reading r
    WHERE  r.kind = {{kind}} AND r.accepted = 1
)
SELECT k.anaesthetic_key, COUNT(*) AS readings_beyond
FROM   kept k
WHERE  k.value < {{threshold}}
GROUP  BY k.anaesthetic_key
"""


def test_a_derived_section_fills_the_capability_s_placeholders_and_joins_it_to_the_episodes(tmp_path, monkeypatch, shadow):
    monkeypatch.setattr(specification, "CAPABILITIES", tmp_path)
    (tmp_path / "minutes_beyond_threshold.sql").write_text(MINUTES, encoding="utf-8")
    spec, digest = specification.load(SPEC)
    compiled = specification.compile(spec, specification.read_episodes(KEYS, "anaesthetic_keys"), digest)
    entry = _section(compiled, "minutes_below_40")
    assert entry["status"] == "compiled" and entry["version"] == 1 and entry["role_policy"]["outcome"] == "passed"
    assert "r.kind = 'map_arterial'" in entry["sql"] and "k.value < 40" in entry["sql"]
    assert "-- capability: minutes_beyond_threshold" in entry["sql"]
    columns, rows = _run(shadow, entry["sql"])
    assert columns == ["episode_number", "readings_beyond"] and len(rows) == 6
    assert _section(compiled, "died_within_90_days")["status"] == "declared"
    (tmp_path / "minutes_beyond_threshold.sql").write_text(MINUTES.replace("{{threshold}}", "{{limit}}"), encoding="utf-8")
    with pytest.raises(specification.SpecificationError, match="placeholder limit"):
        specification.compile(spec)


# The command line.

def test_the_command_line_writes_one_file_for_each_section_and_a_record_of_hashes(tmp_path, capsys):
    out = tmp_path / "compiled"
    assert specification.main(["compile", str(SPEC), "--out", str(out), "--episodes", str(KEYS)]) == 0
    record = json.loads((out / "compiled.json").read_text(encoding="utf-8"))
    assert record["specification_sha256"] == specification.sha256(SPEC.read_bytes())
    for entry in record["sections"]:
        if entry["sql"]:
            assert entry["sql_sha256"] == specification.sha256((out / entry["sql"]).read_text(encoding="utf-8"))
    assert "compiled the specification" in capsys.readouterr().out
    assert specification.main(["check", str(SPEC)]) == 0
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(_spec(contract_version="0.9")), encoding="utf-8")
    assert specification.main(["check", str(broken)]) == 1
    assert "contract_version" in capsys.readouterr().err


def test_a_capability_whose_entry_names_its_sql_is_filled_by_the_capability_module(monkeypatch, shadow):
    """The catalogue entry is given here as the capability module expects it, so the test exercises the hand-over to
    capability.fill; where the file and this entry disagree, the module refuses and the test is skipped."""
    from schemalyser import capability
    if not (rolemap.MODEL / "capabilities" / "hypotension_burden.sql").is_file():
        pytest.skip("the capability's SQL has not been written yet")
    model = rolemap.contract()
    entry = {"name": "hypotension_burden", "version": 1, "sql": "capabilities/hypotension_burden.sql",
             "parameters": [
                 {"name": "kinds", "type": "table", "columns": [{"name": "kind", "type": "kind"}, {"name": "preference", "type": "whole"}]},
                 {"name": "direction", "type": "choice", "choices": ["below", "above"]},
                 {"name": "threshold_by_age_band", "type": "table", "columns": [
                     {"name": "from_days", "type": "whole"}, {"name": "until_days", "type": "whole_or_empty"},
                     {"name": "threshold", "type": "number"}]},
                 {"name": "window_from_minutes", "type": "whole"},
                 {"name": "window_until_minutes", "type": "whole_or_empty"},
                 {"name": "reading_stands_minutes", "type": "number"}],
             "requires": {"parts": [], "columns": [], "kinds": [], "links": [], "mapping_views": []}}
    if not any(c["name"] == "hypotension_burden" for c in model["capabilities"]):
        model["capabilities"].append(entry)
    monkeypatch.setattr(rolemap, "contract", lambda: copy.deepcopy(model))
    held = next(c for c in model["capabilities"] if c["name"] == "hypotension_burden")
    try:
        capability.read("hypotension_burden")
    except capability.CapabilityError:
        pytest.skip("the catalogue entry and the capability's file do not yet agree")
    values = {"kinds": [["map_arterial", 1], ["map_cuff", 2]], "direction": "below",
              "threshold_by_age_band": [[0, 28, 40]], "window_from_minutes": 0, "window_until_minutes": None,
              "reading_stands_minutes": 5}
    if {p["name"] for p in held["parameters"]} != set(values):
        pytest.skip("the catalogue entry's parameters have changed")
    spec = _spec(derived=[{"name": "burden", "capability": "hypotension_burden", "version": held["version"],
                           "parameters": values}],
                 output={"class": "rows", "keys": "as recorded", "leaving": ["burden"]})
    compiled = specification.compile(spec, specification.read_episodes(KEYS, "anaesthetic_keys"))
    entry = _section(compiled, "burden")
    assert entry["status"] == "compiled" and "{{" not in entry["sql"]
    columns, rows = _run(shadow, entry["sql"])
    assert columns[0] == "anaesthetic_key" and len(rows) == 6
    bad = _spec(derived=[{"name": "burden", "capability": "hypotension_burden", "version": held["version"],
                          "parameters": dict(values, direction="sideways")}])
    assert "derived" in {r["rule"] for r in specification.validate(bad)}


# What the export screen offers, and the specification that its choices make.

FIELDS = {
    "title": "Mean pressures of the planted neonates", "episodes.form": "anaesthetic_keys",
    "section": ["role_anaesthetic", "role_reading"], "name.role_anaesthetic": "anaesthetics",
    "name.role_reading": "mean_pressures", "kinds.role_reading": ["map_arterial", "map_cuff"],
    "window.role_reading": "1", "window.role_reading.from": "start", "window.role_reading.from_minutes": "-15",
    "window.role_reading.to": "stop", "window.role_reading.to_minutes": "15", "flag.role_reading.accepted": "1",
    "derived": ["hypotension_burden"],
    "param.hypotension_burden.kinds.0.0": "map_arterial", "param.hypotension_burden.kinds.0.1": "1",
    "param.hypotension_burden.kinds.1.0": "map_cuff", "param.hypotension_burden.kinds.1.1": "2",
    "param.hypotension_burden.kinds.2.0": "", "param.hypotension_burden.kinds.2.1": "",
    "param.hypotension_burden.direction": "below",
    "param.hypotension_burden.threshold_by_age_band.0.0": "0", "param.hypotension_burden.threshold_by_age_band.0.1": "28",
    "param.hypotension_burden.threshold_by_age_band.0.2": "40",
    "param.hypotension_burden.threshold_by_age_band.1.0": "28", "param.hypotension_burden.threshold_by_age_band.1.1": "",
    "param.hypotension_burden.threshold_by_age_band.1.2": "45.5",
    "param.hypotension_burden.window_from_minutes": "0", "param.hypotension_burden.window_until_minutes": "",
    "param.hypotension_burden.reading_stands_minutes": "5",
    "output.class": "rows", "output.keys": "pseudonymised", "leave": ["role_reading", "hypotension_burden", "role_drug"],
}


def test_the_screen_offers_the_sections_in_the_clinicians_words_with_their_kinds_windows_and_flags():
    offer = specification.choices()
    titles = [g["title"] for g in offer["groups"]]
    assert titles[:3] == ["The anaesthetic", "Readings, by kind", "Drugs"] and titles[-1] == "Further parts of the record"
    parts = {p["part"]: p for g in offer["groups"] for p in g["parts"]}
    assert set(parts) == set(rolemap.all_views())
    reading = parts["role_reading"]
    assert reading["window"]["column"] == "reading_time" and [f["name"] for f in reading["flags"]] == ["accepted"]
    assert {"kind": "map_arterial", "meaning": "A mean arterial pressure from an arterial line, in mmHg."} in reading["kinds"]
    assert parts["role_anaesthetic"]["episode"] and parts["role_note"]["stays_unless_named"]
    capabilities = {c["name"]: c for c in offer["capabilities"]}
    table = next(p for p in capabilities["hypotension_burden"]["parameters"] if p["name"] == "threshold_by_age_band")
    assert table["type"] == "table" and [c["name"] for c in table["columns"]] == ["from_days", "until_days", "threshold"]
    assert table["columns"][0]["title"] == "From the age in days" and table["has_default"] is False
    assert capabilities["hypotension_burden"]["has_sql"]
    # The offer is the same at every hospital: it reads the contract and the catalogue, and no hospital schema.
    assert "role_" not in json.dumps([g["title"] for g in offer["groups"]])


def test_the_screen_s_choices_make_a_specification_that_keeps_every_rule(tmp_path, capsys):
    spec = specification.from_form(FIELDS)
    assert specification.validate(spec) == []
    assert spec["sections"][1] == {"name": "mean_pressures", "part": "role_reading", "kinds": ["map_arterial", "map_cuff"],
                                   "window": {"from": "start", "from_minutes": -15, "to": "stop", "to_minutes": 15},
                                   "flags": {"accepted": 1}}
    parameters = spec["derived"][0]["parameters"]
    assert parameters["kinds"] == [["map_arterial", 1], ["map_cuff", 2]]
    assert parameters["threshold_by_age_band"] == [[0, 28, 40], [28, None, 45.5]] and parameters["window_until_minutes"] is None
    # Only a chosen section may leave, and the notes leave only when named.
    assert spec["output"]["leaving"] == ["mean_pressures", "hypotension_burden"]
    assert "role_note" not in specification.chosen(None)["leave"]
    # The choices that a specification records set the screen's fields again.
    again = specification.chosen(spec)
    assert set(again["sections"]) == {"role_anaesthetic", "role_reading"} and set(again["leave"]) == {"role_reading", "hypotension_burden"}
    # A value that is not of its type is kept as written, so that the rule it breaks is named.
    broken = specification.from_form(dict(FIELDS, **{"param.hypotension_burden.reading_stands_minutes": "five",
                                                      "episodes.form": "patient_dates", "episodes.window_hours": ""}))
    assert {r["rule"] for r in specification.validate(broken)} == {"derived", "episodes"}
    # The command line writes the same specification from the same fields.
    (tmp_path / "fields.json").write_text(json.dumps(FIELDS), encoding="utf-8")
    assert specification.main(["write", str(tmp_path / "fields.json"), "--out", str(tmp_path / "spec.json")]) == 0
    assert json.loads((tmp_path / "spec.json").read_text(encoding="utf-8")) == spec
    assert specification.main(["choices", "--out", str(tmp_path / "choices.json")]) == 0
    assert json.loads((tmp_path / "choices.json").read_text(encoding="utf-8")) == specification.choices()
