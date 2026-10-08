"""The count of how often each chosen code is charted, the cases of the specification one to a line, the reference query
composed only when it is shown, and the work that an answer does not repeat."""
import json
import shutil
import time
from pathlib import Path

from schemalyser import boundary, browser, facts, memo, target
from schemalyser.catalogue import Catalogue

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
NEONATAL = (FIXTURES / "targets" / "neonatal_low_mean_pressure.sql").read_text()
CATALOGUE = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
TARGET = "neonatal_low_mean_pressure"


def _state(tmp_path, settings=None, given=()):
    state = tmp_path / "state"
    (state / "targets").mkdir(parents=True)
    shutil.copytree(FIXTURES / "conversion", state / "conversion")
    shutil.copy(FIXTURES / "invented-catalogue.csv", state / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", state / "site-rules.json")
    shutil.copy(FIXTURES / "targets" / f"{TARGET}.sql", state / "targets" / f"{TARGET}.sql")
    if settings is not None:
        (state / "audit.json").write_text(json.dumps(settings))
    if given:
        held = facts.Facts()
        for fact in given:
            held = held.with_fact(facts.check(fact, CATALOGUE))
        (state / "facts.json").write_text(held.to_json())
        (state / "conversion" / facts.SITE_MAPPINGS).write_text(held.site_mappings())
    return state


def _target(state, **options):
    _, found = boundary.produce(state, FIXTURES / "requests", **options)
    return next(t for t in found["targets"] if t["name"] == TARGET)


CODES = {"kind": "codes", "vocabulary": "SITE_OBS", "concept": 21490852, "codes": ["52"], "date": "2026-10-05"}


def test_the_count_of_the_chosen_codes_starts_from_the_cohort_and_reaches_the_specification(tmp_path):
    # Without chosen codes, or without the end of the study period, no count is offered.
    assert _target(_state(tmp_path / "a", {"from": "2021-01-01", "to": "2024-06-30"}))["charted"] is None
    assert _target(_state(tmp_path / "b", {"from": "2021-01-01"}, [CODES]))["charted"] is None
    # Before the count by year has been seen, the count is not offered to be run, and the page says why.
    unseen = _target(_state(tmp_path / "u", {"from": "2021-01-01", "to": "2024-06-30"}, [CODES]))["charted"]
    assert unseen["sql"] == "" and unseen["withheld"].startswith("Schemalyser offers this query once the count by year has been seen")
    found = _target(_state(tmp_path / "c", {"from": "2021-01-01", "to": "2024-06-30"}, [CODES, COUNT]))["charted"]
    assert (found["from"], found["to"], found["codes"], found["kept"]) == ("2023-07-01", "2024-06-30", ["52"], False)
    sql = found["sql"]
    assert sql.startswith("-- The database analyst sets a time limit before running this script, because") and "\nSET NOCOUNT ON;\n" in sql
    assert "-- This query counts how often each chosen code was charted" in sql
    # The cohort goes into a temporary table first, and the readings are reached from it by key, for the chosen codes only.
    assert sql.index("q22_cohort AS (") < sql.index("INTO #cohort") < sql.index("FROM #cohort AS c")
    assert "ON s.ANAES_KEY = c.visit_detail_source_value__key" in sql and "IN ('52')" in sql
    assert found["worst"] == "about 40 anaesthetics of the cohort from 2023-07-01 to 2024-06-30"
    # The pasted result is read, kept as a fact, and listed in the specification beside its code.
    read = json.loads(browser.charted_read("code\treadings\tanaesthetics\n52\t1230\t40\n"))
    assert read == {"ok": True, "rows": [["52", 1230, 40]]}
    charted = {"kind": "charted", "from": found["from"], "to": found["to"], "codes": found["codes"], "counts": read["rows"],
               "date": "2026-10-05"}
    kept = _target(_state(tmp_path / "d", {"from": "2021-01-01", "to": "2024-06-30"}, [CODES, charted]))
    assert kept["charted"]["kept"] is True
    assert "From 2023-07-01 to 2024-06-30, the code 52 was charted 1,230 times on 40 of the audit's anaesthetics." \
        in kept["specification"]
    blank = dict(charted, counts=[["52", None, None]])
    assert "the code 52 was charted fewer than ten times on fewer than ten of the audit's anaesthetics." \
        in _target(_state(tmp_path / "e", {"from": "2021-01-01", "to": "2024-06-30"}, [CODES, blank]))["specification"]


def test_a_charted_fact_must_name_the_codes_it_counts():
    good = {"kind": "charted", "from": "2024-01-01", "to": "2024-12-31", "codes": ["52"], "counts": [["52", 10, None]],
            "date": "2026-10-05"}
    assert facts.check(good, CATALOGUE)["counts"] == [["52", 10, None]]
    for bad in ({"counts": [["99", 10, 10]]}, {"from": "2024"}, {"codes": []}, {"counts": [["52", -1, 0]]}):
        try:
            facts.check(dict(good, **bad), CATALOGUE)
        except facts.FactsError:
            continue
        raise AssertionError(bad)


def test_the_cases_of_the_specification_are_one_to_a_line(tmp_path):
    spec = _target(_state(tmp_path))["specification"]
    cases = spec[spec.index("10. Cases to check the query against"):].splitlines()[4:]
    assert cases and all(line.startswith("- ") and line.count(". ") == 0 for line in cases if line)
    assert "- A neonate of 10 days with no recorded death, whose cuff means are 62 at 09:10, 35 at 09:20 and 60 at 09:40, " \
           "should give 5 minutes below 40, because a reading stands for no longer than five minutes, and no death within 90 days." in cases
    assert not any("systolic" in line for line in cases)


def test_the_reference_query_is_composed_only_when_it_will_be_shown(tmp_path):
    state = _state(tmp_path)
    waiting = _target(state, draft_when=lambda rows, settings: False)
    assert waiting["draft"] is None and waiting["draft_pending"]
    shown = _target(state, draft_when=lambda rows, settings: True)
    assert shown["draft"] and not shown["draft_pending"]
    assert shown["draft"] == _target(state)["draft"]


def test_a_second_run_over_the_same_inputs_gives_the_same_outputs_and_is_quicker(tmp_path):
    state = _state(tmp_path)
    memo.clear()
    began = time.perf_counter()
    first, _ = boundary.produce(state, FIXTURES / "requests")
    cold = time.perf_counter() - began
    began = time.perf_counter()
    second, _ = boundary.produce(state, FIXTURES / "requests")
    warm = time.perf_counter() - began
    assert {k: v for k, v in first.items() if k != "sql_evidence.json"} == {k: v for k, v in second.items() if k != "sql_evidence.json"}
    assert warm < cold


COUNT = {"kind": "count", "answer": "right", "date": "2026-10-05", "years": [[2023, 120, 20], [2024, 130, 20]]}


def test_the_codes_are_chosen_from_a_list_of_what_is_charted_on_the_cohort(tmp_path):
    from schemalyser import charted
    assert charted.default_year([[2023, 1, 1], [2024, 1, 1], [2026, 1, 1]], today=__import__("datetime").date(2026, 10, 6)) == 2024
    # The list starts from the latest year in which the cohort holds ten or more anaesthetics, not from a year with too few.
    assert charted.default_year([[2023, 150, 30], [2024, 170, None], [2025, 160, None]], today=__import__("datetime").date(2026, 10, 6)) == 2023
    waiting = _target(_state(tmp_path / "a"))["listed"]      # before the count by year, the list is only promised
    assert waiting["waiting"] and not waiting["sql"]
    listed = _target(_state(tmp_path / "b", given=[COUNT]))["listed"]
    assert (listed["year"], listed["column"], listed["years"], listed["rows"]) == (2024, "OBS_READING.OBS_TYPE_KEY", [2023, 2024], None)
    sql = listed["sql"]
    assert sql.startswith("-- The database analyst sets a time limit before running this script, because") and "\nSET NOCOUNT ON;\n" in sql and listed["worst"] == "about 20 anaesthetics of the cohort in 2024"
    assert "-- This query lists every code of OBS_READING.OBS_TYPE_KEY that was charted" in sql
    # It puts the cohort into a temporary table, then reaches the readings last, by key, with no lookup of mapping rows and
    # no condition on a value.
    assert sql.index("FROM q22_cohort AS c;\nALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_id);") < sql.index("FROM #cohort AS c")
    tail = sql[sql.index("FROM #cohort AS c"):]
    assert tail.index("JOIN [dbo].[OBS_SHEET] AS s") < tail.index("JOIN [dbo].[OBS_READING] AS r")
    assert "ON s.ANAES_KEY = c.visit_detail_source_value__key" in tail and "TRY_CAST" not in tail
    assert "LEFT JOIN [dbo].[OBS_TYPE_DEF] AS d" in tail and tail.rstrip().endswith("ORDER BY COUNT(*) DESC;")
    assert json.loads(browser.listed_read("code\treadings\tanaesthetics\tOBS_LABEL\n52\t120\t10\tART MEAN\n"))["rows"] == \
        [{"code": "52", "readings": 120, "anaesthetics": 10, "names": ["ART MEAN"]}]


def test_an_empty_list_or_a_count_of_none_heads_what_remains(tmp_path):
    empty = {"kind": "listed", "column": "OBS_READING.OBS_TYPE_KEY", "year": 2024, "rows": 0, "date": "2026-10-05"}
    rows = _target(_state(tmp_path / "a", given=[COUNT, empty]))["rows"]
    assert browser._needs(rows)["remaining"][0] == "charted-empty"
    point = next(r for r in rows if r["question_id"] == "charted-empty")
    assert point["question"].startswith("The list of what is charted on the audit's anaesthetics in 2024 came back empty.")
    assert point["_queries"] and point["_queries"][0].startswith("matched:")
    none = {"kind": "charted", "from": "2024-01-01", "to": "2024-12-31", "codes": ["52"], "counts": [], "date": "2026-10-05"}
    rows = _target(_state(tmp_path / "b", given=[COUNT, CODES, none]))["rows"]
    assert browser._needs(rows)["remaining"][0] == "charted-zero"
    assert "None of the chosen codes was charted on an anaesthetic of the audit's cohort from 2024-01-01 to 2024-12-31." in \
        next(r for r in rows if r["question_id"] == "charted-zero")["question"]
