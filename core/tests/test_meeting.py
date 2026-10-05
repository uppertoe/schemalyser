"""What a meeting with a colleague needs: answers he can give, codes found by a name search, a match query, columns he
may choose, the audit's settings, and a specification in the hospital's own terms."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from schemalyser import boundary, browser, facts, routes, target
from schemalyser.catalogue import Catalogue
from schemalyser.checks import Check, Checks
from schemalyser.rules import SiteRules

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
NEONATAL = (FIXTURES / "targets" / "neonatal_low_mean_pressure.sql").read_text()
CATALOGUE = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
RULES = SiteRules.from_json((FIXTURES / "invented-site-rules.json").read_text())
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


@pytest.fixture(scope="module")
def neonatal():
    return target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, name="neonatal_low_mean_pressure")


def test_a_join_question_states_its_direction_and_a_filter_question_asks_what_a_value_means(neonatal):
    rows, traced = neonatal
    asks = {row["question_id"]: row["_ask"] for row in rows if row.get("_ask")}
    join = next(a for a in asks.values() if a["kind"] == "join")
    assert join["text"].startswith("The query ") and join["text"].endswith(". Is that right?")
    assert "conversion" not in " ".join(a["text"] for a in asks.values())
    filters = [a for a in asks.values() if a["kind"] == "filter"]
    for ask in filters:
        assert ask["text"].startswith("In ") and "mark" in ask["text"]
    assert "three answers" not in traced["questions"]


def test_a_picker_offers_only_columns_that_could_be_keys():
    chosen = target._choosable(CATALOGUE, RULES, "PERSON_MASTER")
    assert chosen and all("NAME" not in c and "BIRTH" not in c for c in chosen)
    assert all(target.KEYLIKE.search(c) for c in chosen)


def test_codes_are_found_by_a_name_search_on_the_definition_table():
    rows, _ = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, name="n")
    search = [row["_ask"] for row in rows if row.get("_ask", {}).get("search")]
    assert search, "the neonatal codes are offered a name search"
    sql = search[0]["search"]
    assert "FROM [dbo].[OBS_TYPE_DEF] AS d WITH (NOLOCK)" in sql and "LIKE N'%MEAN%'" in sql and "TOP" not in sql
    assert "LIKE N'%UAC%'" in sql and "LIKE N'%A-LINE%'" in sql
    assert "a mean arterial pressure measured through an arterial line" in " ".join(a["meaning"] for a in search)
    found = json.loads(browser.codes_search_read("code\tOBS_LABEL\tUNIT_LABEL\n52\tART MEAN\tmmHg\n51\tNIBP MEAN\tmmHg\n=1+1\tBAD\tx\n"))
    assert [c["code"] for c in found["candidates"]] == ["52", "51"] and found["columns"] == ["OBS_LABEL", "UNIT_LABEL"]


def test_not_sure_is_recorded_and_offers_a_match_query_that_settles_the_join(neonatal):
    rows, _ = neonatal
    join = next(row for row in rows if row.get("_ask", {}).get("kind") == "join")
    names = join["_names"]
    unsure = {"facts": [{"kind": "join", "left": names["left"], "right": names["right"], "answer": "unsure", "date": "2026-10-05"}]}
    found, traced = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, facts_text=json.dumps(unsure), name="n")
    row = {r["question_id"]: r for r in found}[join["question_id"]]
    assert "was not sure" in row["evidence_in_hand"] and not row.get("_ask")
    assert row["query_state"] == "ready" and "matched" in row["_queries"][0] and "OUTER APPLY" in row["query"]
    # The match query's result settles the join where nearly every value finds its match.
    key = row["_queries"][0]
    left, right = key.split(":", 1)[1].split("-", 1)
    (lt, lc), (rt, rc) = left.split("."), right.split(".")
    result = f"matched,{lt},{lc},,{rt}.{rc},1000,990,,\n"
    merged = Checks.from_csv(CHECKS, CATALOGUE, RULES).merged(Checks.from_csv(
        "check_kind,table_name,column_name,value,label,row_count,distinct_count,null_count,is_unique\n" + result, CATALOGUE, RULES))
    again, _ = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, merged.to_csv(), facts_text=json.dumps(unsure), name="n")
    settled = {r["question_id"]: r for r in again}[join["question_id"]]
    assert settled["status"] == "answered" and "990 of 1,000" in settled["evidence_in_hand"]


def test_a_no_with_the_columns_that_do_match_changes_the_step(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    held = facts.Facts().with_fact(facts.check({"kind": "join", "left": "OBS_READING.SHEET_KEY", "right": "OBS_SHEET.SHEET_KEY",
                                                "answer": "no", "instead": ["OBS_READING.SHEET_KEY", "OBS_SHEET.ANAES_KEY"],
                                                "date": "2026-10-05"}, CATALOGUE))
    changes = routes.rejoin(folder, held)
    assert "measurement.sql" in {c["step"] for c in changes}
    text = (folder / "measurement.sql").read_text()
    assert "s.ANAES_KEY = r.SHEET_KEY" in text or "r.SHEET_KEY = s.ANAES_KEY" in text
    assert text.startswith("--")


def test_the_settings_reach_the_reference_query_and_leave_the_answer_unchanged_without_them(neonatal):
    assert target.with_settings(NEONATAL, None) == NEONATAL
    assert target.with_settings(NEONATAL, {"from": None, "to": None, "kinds": []}) == NEONATAL
    set_ = target.with_settings(NEONATAL, {"from": "2022-01-01", "to": "2022-12-31", "kinds": [4174669]})
    assert "a.start_datetime >= CAST('2022-01-01' AS DATE)" in set_ and "a.anaesthesia_type_concept_id IN (4174669)" in set_
    draft = target.source_draft(CONVERSION, set_, CATALOGUE, blank=False)
    assert "2022-01-01" in draft and "2022-12-31" in draft
    with pytest.raises(target.TargetError):
        target.read_settings('{"from": "2023-01-01", "to": "2022-01-01"}')
    # On the synthetic rows, a period that holds no anaesthetic leaves the answer with no anaesthetic in any band,
    # and the query without a period agrees with the target as before.
    world = make_checks.WORLD
    plain = target.source_draft(CONVERSION, NEONATAL, CATALOGUE, blank=False)
    assert target.check_query(world, CONVERSION, NEONATAL, plain, rows=300)["agree"]
    empty = target.with_settings(NEONATAL, {"from": "1900-01-01", "to": "1900-12-31", "kinds": []})
    result = target.check_query(world, CONVERSION, empty, target.source_draft(CONVERSION, empty, CATALOGUE, blank=False), rows=300)
    assert result["agree"]


def test_the_specification_is_in_the_hospitals_own_terms(neonatal):
    rows, traced = neonatal
    text = target.specification(CONVERSION, NEONATAL, rows, traced, CATALOGUE, "neonatal_low_mean_pressure",
                                {"from": "2022-01-01", "to": "2024-12-31", "kinds": []})
    for heading in ("1. The question", "2. The study period", "3. Where each part", "4. How the tables are joined",
                    "5. What is left out", "6. The local codes", "7. The result", "8. What is not yet settled", "9. Decisions for the clinicians", "10. Cases"):
        assert heading in text
    assert "The study period runs from 2022-01-01 to 2024-12-31" in text
    for field in ("measurement_event_id", "age_days", "anaesthetic_id", "value_as_number", "python -m"):
        assert field not in text, field
    assert "ANAES_RECORD.ANAES_START_TS" in text and "confirmed by a person on" not in text.split("6. The local codes")[1].split("7.")[0] \
        or "means" in text
    none = target.specification(CONVERSION, NEONATAL, rows, traced, CATALOGUE, "n")
    assert "No study period has been chosen" in none


def test_the_boundary_reads_the_settings_and_refuses_bad_ones(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", state / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", state / "site-rules.json")
    shutil.copytree(CONVERSION, state / "conversion")
    (state / "targets").mkdir()
    shutil.copy(FIXTURES / "targets" / "neonatal_low_mean_pressure.sql", state / "targets")
    (state / "audit.json").write_text(json.dumps({"from": "2022-01-01"}))
    _, found = boundary.produce(state, FIXTURES / "requests")
    assert found["targets"][0]["settings"]["from"] == "2022-01-01" and found["targets"][0]["kinds"]
    (state / "audit.json").write_text('{"from": "January"}')
    with pytest.raises(boundary.BoundaryError):
        boundary.produce(state, FIXTURES / "requests")


def test_a_table_whose_size_is_not_visible_is_counted_only_when_it_is_plainly_small():
    from schemalyser import checks as checking
    unrecorded = Checks.from_csv("check_kind,table_name,column_name,value,label,row_count,distinct_count,null_count,is_unique\n"
                                 "skipped,OBS_READING,,rows,unrecorded,,,,\nskipped,OBS_TYPE_DEF,,rows,unrecorded,,,,\n", CATALOGUE, RULES)
    values = Check("values", "OBS_READING", "ACCEPTED_FLAG")
    assert checking.offer(values, CATALOGUE, unrecorded, small={"OBS_TYPE_DEF"}) == ("unsampled", [])
    label = Check("values", "OBS_TYPE_DEF", "OBS_LABEL")
    assert checking.offer(label, CATALOGUE, unrecorded, small={"OBS_TYPE_DEF"})[0] == "count"


def test_the_count_by_year_reads_no_reading_and_stays_open_until_it_looks_right(neonatal):
    rows, traced = neonatal
    sql = target.year_count(CONVERSION, NEONATAL, CATALOGUE)
    assert sql and "OBS_READING" not in sql and "start_year" in sql and "age_days < 28" in sql
    count = {r["question_id"]: r for r in rows}["count-by-year"]
    assert count["status"] == "open" and count["_queries"] == ["yearcount"] and count["phase"] == "source" and not count["query"]
    for answer, years, settled in (("right", [[2023, 120, 10], [2024, 130, 20]], True),
                                   ("right", [[2022, 120, 10], [2024, 130, 20]], False),
                                   ("few", [[2023, 120, 10]], False), ("right", [[2023, 120, None]], False)):
        given = json.dumps({"facts": [{"kind": "count", "answer": answer, "years": years, "date": "2026-10-05"}]})
        found, _ = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, facts_text=given, name="n")
        row = {r["question_id"]: r for r in found}["count-by-year"]
        assert (row["status"] == "answered") == settled, (answer, years)
        if not settled:
            assert "The count rests on these matches" in row["evidence_in_hand"]


def test_a_code_marked_not_sure_stays_open_and_is_unsettled_in_the_specification():
    given = json.dumps({"facts": [{"kind": "codes", "vocabulary": "SITE_OBS", "concept": 21490852, "codes": ["52"],
                                   "uncertain": ["53"], "column": "OBS_READING.OBS_TYPE_KEY", "date": "2026-10-05"}]})
    rows, traced = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, facts_text=given, name="n")
    row = {r["question_id"]: r for r in rows}["codes-measurement.measurement_concept_id-21490852"]
    assert row["status"] == "open" and "whether 53 also means this" in row["evidence_in_hand"]
    assert browser._remains(row)


def test_the_floor_changes_the_answer_on_the_planted_cases_and_the_reference_query_agrees():
    world = make_checks.WORLD
    plain = target.check_query(world, CONVERSION, NEONATAL, target.source_draft(CONVERSION, NEONATAL, CATALOGUE, blank=False), rows=300)
    floored = target.with_settings(NEONATAL, {"floor": 36}, CONVERSION)
    assert "m.value_as_number >= 36" in floored
    result = target.check_query(world, CONVERSION, floored, target.source_draft(CONVERSION, floored, CATALOGUE, blank=False), rows=300)
    assert result["agree"] and result["target"] != plain["target"]
    arterial = target.with_settings(NEONATAL, {"pressures": "arterial_only"}, CONVERSION)
    assert "art_before" in arterial and "art_after" in arterial
    assert target.check_query(world, CONVERSION, arterial, target.source_draft(CONVERSION, arterial, CATALOGUE, blank=False), rows=300)["agree"]
    assert target.with_settings(NEONATAL, {"pressures": None, "floor": None}, CONVERSION) == NEONATAL
    with pytest.raises(target.TargetError):
        target.read_settings('{"floor": 0}')
    with pytest.raises(target.TargetError):
        target.read_settings('{"bypass": "sometimes"}')
