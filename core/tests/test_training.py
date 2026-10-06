"""A meeting whose queries run against a training database with fictional patients: the build is real, so what depends on
it is settled as before, but every count and every pattern of charting means nothing, so a result that depends on the data
is shown and marked to be asked again on the production copy. On production, or when nobody is sure, nothing changes."""
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from schemalyser import boundary, browser, target
from schemalyser.analysis import Analysis
from schemalyser.catalogue import Catalogue
from schemalyser.checks import Checks
from schemalyser.rules import SiteRules

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
NAME = "neonatal_low_mean_pressure"
NEONATAL = (FIXTURES / "targets" / f"{NAME}.sql").read_text()
CATALOGUE_TEXT = (FIXTURES / "invented-catalogue.csv").read_text()
RULES_TEXT = (FIXTURES / "invented-site-rules.json").read_text()
CATALOGUE = Catalogue.from_csv(CATALOGUE_TEXT)
RULES = SiteRules.from_json(RULES_TEXT)
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
HEAD = "check_kind,table_name,column_name,value,label,row_count,distinct_count,null_count,is_unique\n"
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

TRAINING_COUNT = target.COUNT_WORDING["training"]
AGAIN = target.TRAINING_WORDING["again"]


def _count(answer, years):
    return json.dumps({"facts": [{"kind": "count", "answer": answer, "years": years, "date": "2026-10-05"}]})


def _rows(database, facts_text=None, checks=CHECKS):
    rows, traced = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, checks, facts_text=facts_text, name="n",
                                    database=database)
    return {r["question_id"]: r for r in rows}, rows, traced


def test_the_database_is_kept_in_audit_json_and_checked():
    for value in ("production", "training", "unsure"):
        assert target.read_settings(json.dumps({"database": value}))["database"] == value
    assert target.read_settings("{}")["database"] is None and target.read_settings("")["from"] is None
    with pytest.raises(target.TargetError):
        target.read_settings('{"database": "test"}')
    # Not knowing is treated as production, so that no result is set aside without cause.
    assert target.training({"database": "training"})
    assert not any(target.training(s) for s in ({"database": "unsure"}, {"database": "production"}, {}, None))
    # A count seen on a training database is a fact of its own, which nobody judged.
    assert json.loads(_count("training", [[2024, 10, None]]))


@pytest.mark.parametrize("database", [None, "production", "unsure"])
def test_on_production_or_when_nobody_is_sure_the_count_is_judged_as_before(database):
    right, rows, _ = _rows(database, _count("right", [[2023, 120, 10], [2024, 130, 20]]))
    count = right["count-by-year"]
    assert count["status"] == "answered" and not count.get("_again")
    assert not browser._needs(rows)["again"]
    # A count kept on a training database is not judged, so on production it stays open and the question is asked.
    kept, rows, _ = _rows(database, _count("training", [[2023, 120, 10], [2024, 130, 20]]))
    count = kept["count-by-year"]
    assert count["status"] == "open" and "came from a training database, so you did not judge them" in count["evidence_in_hand"]
    assert "count-by-year" in browser._needs(rows)["remaining"]
    # A match count and a listed flag settle as before.
    assert not any(r.get("_again") for r in rows)


def test_on_a_training_database_the_count_is_kept_unjudged_and_is_to_be_asked_again():
    # Before the count has run, the item is open and offers the count, as it does on production.
    before, _, traced = _rows("training")
    assert before["count-by-year"]["status"] == "open" and before["count-by-year"]["_queries"] == ["yearcount"]
    found, rows, traced = _rows("training", _count("training", [[2023, 120, 10], [2024, 130, 20]]))
    count = found["count-by-year"]
    assert count["status"] == "partly" and count["_again"]
    assert count["question"] == target.COUNT_WORDING["question_training"]
    assert count["evidence_in_hand"].startswith("You ran the count on 2026-10-05 on a training database with fictional patients.")
    assert TRAINING_COUNT in count["evidence_in_hand"] and "The count rests on these matches" not in count["evidence_in_hand"]
    # The count is still offered, to be run on the production copy, and does not remain among what the meeting must settle.
    assert count["_queries"] == ["yearcount"] and traced["year_count"]
    needs = browser._needs(rows)
    assert "count-by-year" in needs["again"] and "count-by-year" not in needs["remaining"]
    assert not browser._remains(count)
    # An empty cohort is noted, with the same sentence, and does not stop the audit or head what remains.
    empty, rows, _ = _rows("training", _count("training", [[2023, 120, None], [2024, 130, None]]))
    count = empty["count-by-year"]
    assert target.COUNT_WORDING["no_cohort"] in count["evidence_in_hand"] and TRAINING_COUNT in count["evidence_in_hand"]
    assert count["status"] == "partly" and not count.get("_top") and "count-by-year" not in browser._needs(rows)["remaining"]


def _unsure_join_with_match():
    """The facts in which a person was not sure of a join, and the check results with a good match count for it."""
    _, rows, _ = _rows(None)
    join = next(row for row in rows if (row.get("_ask") or {}).get("kind") == "join")
    names = join["_names"]
    unsure = json.dumps({"facts": [{"kind": "join", "left": names["left"], "right": names["right"], "answer": "unsure",
                                    "date": "2026-10-05"}]})
    asked = {r["question_id"]: r for r in target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, facts_text=unsure,
                                                            name="n")[0]}[join["question_id"]]
    (lt, lc), (rt, rc) = (part.split(".") for part in asked["_queries"][0].split(":", 1)[1].split("-", 1))
    merged = Checks.from_csv(CHECKS, CATALOGUE, RULES).merged(
        Checks.from_csv(HEAD + f"matched,{lt},{lc},,{rt}.{rc},1000,990,,\n", CATALOGUE, RULES))
    return join["question_id"], names, unsure, merged.to_csv()


def test_on_a_training_database_a_match_count_and_a_listed_flag_are_shown_and_settle_nothing():
    join_id, names, unsure, checks = _unsure_join_with_match()
    production, _, _ = _rows("production", unsure, checks)
    assert production[join_id]["status"] == "answered"
    found, rows, _ = _rows("training", unsure, checks)
    join = found[join_id]
    # The result stays in view, and the item is to be asked again rather than settled or open.
    assert join["status"] == "partly" and join["_again"] and "990 of 1,000" in join["evidence_in_hand"]
    assert join["evidence_in_hand"].endswith(AGAIN)
    # The values that the sampled check of a flag listed are shown, and settle nothing either.
    flag = found["filter-OBS_READING.ACCEPTED_FLAG"]
    assert production["filter-OBS_READING.ACCEPTED_FLAG"].get("_again") is None
    assert flag["_again"] and flag["status"] == "partly" and AGAIN in flag["evidence_in_hand"]
    needs = browser._needs(rows)
    assert {join_id, "filter-OBS_READING.ACCEPTED_FLAG"} <= set(needs["again"]) and join_id not in needs["remaining"]
    # What depends on the build alone is settled as before: a person's yes, the tables, the columns and the codes.
    yes = json.dumps({"facts": [{"kind": "join", "left": names["left"], "right": names["right"], "answer": "yes", "date": "2026-10-05"}]})
    confirmed, _, _ = _rows("training", yes, checks)
    assert confirmed[join_id]["status"] == "answered" and not confirmed[join_id].get("_again")
    assert all(r["status"] == production[i]["status"] for i, r in found.items() if r["kind"] in ("table", "column", "codes"))


def test_the_specification_states_the_database_and_lists_what_is_to_be_asked_again():
    w = target.SPECIFICATION_WORDING
    found, rows, traced = _rows("training", _count("training", [[2023, 120, 10], [2024, 130, 20]]))
    text = target.specification(CONVERSION, NEONATAL, rows, traced, CATALOGUE, "n",
                                {"from": None, "to": None, "kinds": [], "database": "training"})
    head, rest = text.split("1. The question")
    assert w["database_training"] in head
    unsettled = rest.split("8. What is not yet settled")[1].split("9. Decisions")[0]
    again = unsettled.split(w["h_again"])[1]
    assert "- The count of anaesthetics by year: run the query again on the production copy and paste the result." in again
    assert "- What the values of OBS_READING.ACCEPTED_FLAG that the steps compare with mean: run the query again" in again
    assert "Not yet settled: the count of anaesthetics by year" not in unsettled
    for database in ("production", "unsure"):
        rows_, traced_ = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, name="n", database=database)
        plain = target.specification(CONVERSION, NEONATAL, rows_, traced_, CATALOGUE, "n", {"database": database})
        assert w[f"database_{database}"] in plain.split("1. The question")[0] and w["h_again"] not in plain
    # Without the setting, the specification is as it was.
    rows_, traced_ = target.checklist(make_checks.WORLD, CONVERSION, NEONATAL, CHECKS, name="n")
    bare = target.specification(CONVERSION, NEONATAL, rows_, traced_, CATALOGUE, "n")
    assert not any(w[f"database_{d}"] in bare for d in target.DATABASES)


def _state(folder, database, facts):
    folder.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", folder / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", folder / "site-rules.json")
    shutil.copytree(CONVERSION, folder / "conversion")
    (folder / "targets").mkdir()
    shutil.copy(FIXTURES / "targets" / f"{NAME}.sql", folder / "targets")
    (folder / "checks.csv").write_text(CHECKS)
    (folder / "audit.json").write_text(json.dumps({"from": "2023-01-01", "to": "2024-12-31", "database": database}))
    (folder / "facts.json").write_text(json.dumps({"facts": facts}))
    return folder


def test_on_a_training_database_what_is_charted_is_to_be_asked_again_and_stops_nothing(tmp_path):
    count = {"kind": "count", "answer": "training", "years": [[2023, 120, 10], [2024, 130, 20]], "date": "2026-10-05"}
    column = "OBS_READING.OBS_TYPE_KEY"

    def rows(name, database, *facts):
        _, found = boundary.produce(_state(tmp_path / name, database, [count, *facts]), FIXTURES / "requests")
        return {r["question_id"]: r for r in next(t for t in found["targets"] if t["name"] == NAME)["rows"]}

    empty = {"kind": "listed", "column": column, "year": 2024, "rows": 0, "date": "2026-10-05"}
    # On production an empty list is an open point that heads what remains; on training it is to be asked again.
    production = rows("production", "production", dict(count, answer="right"), empty)
    assert production["charted-empty"]["_top"] and production["charted-empty"]["status"] == "open"
    trained = rows("training", "training", empty)
    point = trained["charted-empty"]
    assert point["_again"] and not point["_top"] and not browser._remains(point)
    assert "came back empty on a training database" in point["question"]
    # Codes chosen from a list and a count of the chosen codes are each to be asked again, without stopping the audit.
    listed = dict(empty, rows=45)
    counted = {"kind": "charted", "from": "2024-01-01", "to": "2024-12-31", "codes": ["52"], "counts": [], "date": "2026-10-05"}
    chosen = rows("chosen", "training", listed, counted)
    assert chosen["charted-listed"]["_again"] and "rows may be missing or oddly frequent" in chosen["charted-listed"]["question"]
    assert chosen["charted-counted"]["_again"] and "charted-zero" not in chosen
    # On production the same count of none for the chosen codes stops the audit, as before.
    assert rows("zero", "production", dict(count, answer="right"), listed, counted)["charted-zero"]["_top"]


def _put(folder, kind):
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            browser.boundary_put(kind, path.relative_to(folder).as_posix(), path.read_bytes())


def _page(state):
    browser.clear()
    browser._analysis = Analysis(CATALOGUE_TEXT, RULES_TEXT)
    browser.boundary_begin()
    _put(state, "state")
    _put(FIXTURES / "requests", "requests")
    return json.loads(browser.boundary_run())


def test_a_state_saved_from_a_training_meeting_loads_again_and_still_knows(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    state = _state(tmp_path / "state", None, [])
    (state / "audit.json").unlink()
    first = _page(state)
    assert first["ok"] and not first["targets"][0]["settings"].get("database")
    # The colleague says that the SQL window is connected to a training database, and the count by year is kept unjudged.
    chosen = json.loads(browser.settings_set(json.dumps({"from": "2023-01-01", "to": "2024-12-31", "database": "training"})))
    assert chosen["ok"] and all(t["settings"]["database"] == "training" for t in chosen["boundary"]["targets"])
    kept = json.loads(browser.fact_add(json.dumps({"kind": "count", "answer": "training", "date": "2026-10-05",
                                                   "years": [[2023, 120, 10], [2024, 130, 20]]})))
    page = next(t for t in kept["boundary"]["targets"] if t["name"] == NAME)
    item = next(r for r in page["rows"] if r["id"] == "count-by-year")
    assert item["again"] and item["status"] == "partly" and "count-by-year" in page["needs"]["again"]
    assert "count-by-year" not in page["needs"]["remaining"]
    saved = browser.state_zip()
    held = zipfile.ZipFile(io.BytesIO(saved))
    assert json.loads(held.read("audit.json"))["database"] == "training"
    assert json.loads(held.read("facts.json"))["facts"][0]["answer"] == "training"

    # Loaded again, by the boundary command and by the page, the checklist still knows that the database was a training one.
    loaded = tmp_path / "loaded"
    loaded.mkdir()
    shutil.copytree(CONVERSION, loaded / "conversion")
    (loaded / "targets").mkdir()
    shutil.copy(FIXTURES / "targets" / f"{NAME}.sql", loaded / "targets")
    shutil.copy(FIXTURES / "invented-site-rules.json", loaded / "site-rules.json")
    held.extractall(loaded)
    outputs, found = boundary.produce(loaded, FIXTURES / "requests")
    again = next(t for t in found["targets"] if t["name"] == NAME)
    assert again["settings"]["database"] == "training"
    assert next(r for r in again["rows"] if r["question_id"] == "count-by-year")["_again"]
    assert target.SPECIFICATION_WORDING["database_training"] in again["specification"]
    reloaded = next(t for t in _page(loaded)["targets"] if t["name"] == NAME)
    assert "count-by-year" in reloaded["needs"]["again"]
    assert browser.state_zip() == saved
    # Changed on the page like an answer, the choice works the checklist out again, and the count is asked as before.
    changed = json.loads(browser.settings_set(json.dumps({"from": "2023-01-01", "to": "2024-12-31", "database": "production"})))
    now = next(t for t in changed["boundary"]["targets"] if t["name"] == NAME)
    assert not now["needs"]["again"] and "count-by-year" in now["needs"]["remaining"]
    browser.clear()
