"""A state saved by the page and loaded again: failed checks, what the team's SQL showed, and the whole round trip."""
import csv
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from schemalyser import Analysis, boundary, browser, sql_evidence
from schemalyser.catalogue import Catalogue
from schemalyser.checks import Checks
from schemalyser.rules import SiteRules

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CATALOGUE_TEXT = (FIXTURES / "invented-catalogue.csv").read_text()
RULES_TEXT = (FIXTURES / "invented-site-rules.json").read_text()
CATALOGUE = Catalogue.from_csv(CATALOGUE_TEXT)
RULES = SiteRules.from_json(RULES_TEXT)
# Two of the target queries, which between them reach every kind of item, so that the round trip stays quick.
TARGETS = ("neonatal_low_mean_pressure.sql", "airway_by_anaesthesia_type.sql")
HEAD = "check_kind,table_name,column_name,value,label,row_count,distinct_count,null_count,is_unique\n"

# One row of every kind that the check script returns, beside the fixture's column, values and years rows.
EVERY_KIND = (
    "rows,AIRWAY_DEVICE,,,,600,,,\n"
    "spans,ANAES_RECORD,ANAES_START_TS,120 to 239 minutes,ANAES_STOP_TS,50,,,\n"
    "fanout,OBS_READING,SHEET_KEY,1 row,OBS_SHEET.SHEET_KEY,400,,,\n"
    "skipped,AIRWAY_DEVICE,DEVICE_KIND_KEY,values,time,,,,\n"
    "sampled,OBS_READING,READ_TS,years,,5,,,\n"
    "ran,ANAES_RECORD,RISK_GRADE_CAT,values,,,,,\n"
    "error,WARD_DEF,WARD_KEY,,,208,,,\n"
    "error,STAFF_MASTER,,,,229,,,\n"
)


def _checks(text):
    return Checks.from_csv(text, CATALOGUE, RULES)


# Failed checks.

def test_a_failed_check_is_kept_written_back_and_replaced_by_a_later_result():
    found = _checks(HEAD + EVERY_KIND + "error,NOT_A_TABLE,X,,,208,,,\n" + "error,WARD_DEF,NO_SUCH_COLUMN,,,208,,,\n")
    assert found.failed == [("WARD_DEF", "WARD_KEY", 208), ("STAFF_MASTER", "", 229)]
    assert found.errors == 3        # the row whose column the catalogue does not hold is counted, but not kept
    written = found.to_csv()
    assert "error,STAFF_MASTER,,,,229,,,\n" in written and "error,WARD_DEF,WARD_KEY,,,208,,,\n" in written
    again = _checks(written)
    assert again.failed == sorted(found.failed) and again.errors == 2 and again.to_csv() == written
    # A later result for the same table and column replaces the failure; a skipped check does not.
    later = _checks(HEAD + "values,WARD_DEF,WARD_KEY,W1,,20,,,\n" + "skipped,STAFF_MASTER,,rows,time,,,,\n")
    merged = again.merged(later)
    assert merged.failed == [("STAFF_MASTER", "", 229)] and merged.errors == 1
    assert "error,WARD_DEF" not in merged.to_csv()
    assert again.merged(_checks(HEAD + "rows,STAFF_MASTER,,,,40,,,\n")).failed == [("WARD_DEF", "WARD_KEY", 208)]
    # A later failure of the same check replaces the earlier one.
    assert again.merged(_checks(HEAD + "error,WARD_DEF,WARD_KEY,,,207,,,\n")).failed == \
        [("STAFF_MASTER", "", 229), ("WARD_DEF", "WARD_KEY", 207)]


# What the team's SQL showed.

def test_saved_evidence_is_checked_against_the_catalogue_and_holds_only_names_counts_and_dates():
    good = {"evidence": [
        {"kind": "table", "names": ["obs_sheet"], "files": 2, "date": "2026-10-05"},
        {"kind": "column", "names": ["OBS_SHEET", "visit_key"], "files": 1, "date": "2026-10-05"},
        {"kind": "join", "names": ["VISIT", "VISIT_KEY", "OBS_SHEET", "VISIT_KEY"], "files": 3, "date": "2026-10-04"},
        {"kind": "filter", "names": ["OBS_READING", "ACCEPTED_FLAG"], "files": 1, "date": "2026-10-05"}]}
    saved = sql_evidence.Saved.from_json(json.dumps(good), CATALOGUE)
    assert saved.get("table", "OBS_SHEET") == (2, "2026-10-05")
    assert saved.get("join", "OBS_SHEET", "VISIT_KEY", "visit", "visit_key") == (3, "2026-10-04")
    again = sql_evidence.Saved.from_json(saved.to_json(), CATALOGUE)
    assert again.to_json() == saved.to_json() and "obs_sheet" not in saved.to_json()
    entry = good["evidence"][0]
    for bad in ({**entry, "names": ["SECRET_TABLE"]}, {**entry, "files": 0}, {**entry, "files": 1.5}, {**entry, "files": True},
                {**entry, "date": "5 October"}, {**entry, "kind": "guess"}, {**entry, "names": ["OBS_SHEET", "VISIT_KEY"]},
                {**entry, "file": "request.sql"}, {"kind": "join", "names": ["VISIT", "VISIT_KEY", "VISIT", "VISIT_KEY"],
                                                    "files": 1, "date": "2026-10-05"}):
        with pytest.raises(sql_evidence.EvidenceError):
            sql_evidence.Saved.from_json(json.dumps({"evidence": [bad]}), CATALOGUE)
    with pytest.raises(sql_evidence.EvidenceError):
        sql_evidence.Saved.from_json('{"evidence": [], "requests": ["a.sql"]}', CATALOGUE)


# The whole round trip, as the page saves the state and a later run loads it.

def _put(folder, kind):
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        browser.boundary_put(kind, path.relative_to(folder).as_posix(), path.read_bytes())


def _targets(folder):
    (folder / "targets").mkdir()
    for name in TARGETS:
        shutil.copy(FIXTURES / "targets" / name, folder / "targets" / name)


def _loaded(saved, folder):
    """A state folder made from a saved zip, with the conversion, the targets and the site rules."""
    folder.mkdir()
    shutil.copytree(FIXTURES / "conversion", folder / "conversion")
    _targets(folder)
    shutil.copy(FIXTURES / "invented-site-rules.json", folder / "site-rules.json")
    zipfile.ZipFile(io.BytesIO(saved)).extractall(folder)
    return folder


def _page(state, requests):
    """Runs the page's worker over a state and a folder of requests, and returns what boundary_run gives."""
    browser.clear()
    browser._analysis = Analysis(CATALOGUE_TEXT, RULES_TEXT)
    browser.boundary_begin()
    _put(state, "state")
    if requests is not None:
        _put(requests, "requests")
    return json.loads(browser.boundary_run())


def _statuses(outputs):
    return {name: [(r["question_id"], r["status"]) for r in csv.DictReader(io.StringIO(text))]
            for name, text in outputs.items() if name.endswith("checklist.csv")}


def test_a_state_saved_by_the_page_loads_again_and_saves_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    state = tmp_path / "state"
    state.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", state / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", state / "site-rules.json")
    shutil.copy(FIXTURES / "profile" / "invented-core-profile.csv", state / "core-profile.csv")
    shutil.copytree(FIXTURES / "conversion", state / "conversion")
    _targets(state)
    (state / "checks.csv").write_text((FIXTURES / "invented-checks.csv").read_text() + EVERY_KIND)
    requests = FIXTURES / "requests"

    first = _page(state, requests)
    assert first["ok"]
    # A pasted failure, and every kind of fact.
    assert json.loads(browser.checks_paste(HEAD + "error,ANAES_RECORD,ANAES_KIND_CAT,,,1205,,,\n"))["ok"]
    for fact in ({"kind": "join", "left": "OBS_SHEET.VISIT_KEY", "right": "VISIT.VISIT_KEY", "answer": "yes",
                  "date": "2026-10-05", "who": "A colleague"},
                 {"kind": "join", "left": "PERSON_MASTER.PERSON_KEY", "right": "PERSON_MASTER_2.PERSON_KEY", "answer": "no",
                  "instead": ["PERSON_MASTER.RECORD_NO", "PERSON_MASTER_2.PERSON_KEY"], "date": "2026-10-05"},
                 {"kind": "filter", "column": "OBS_READING.ACCEPTED_FLAG", "answer": "yes", "date": "2026-10-05"},
                 {"kind": "codes", "vocabulary": "SITE_OBS", "concept": "21490852", "codes": ["52"], "date": "2026-10-05",
                  "column": "OBS_READING.OBS_TYPE_KEY"}):
        assert json.loads(browser.fact_add(json.dumps(fact)))["ok"], fact
    saved = browser.state_zip()
    held = zipfile.ZipFile(io.BytesIO(saved))
    assert set(held.namelist()) == {"catalogue.csv", "checks.csv", "core-profile.csv", "facts.json",
                                    "conversion/site_mappings.csv", "sql_evidence.json"}
    checks_text = held.read("checks.csv").decode()
    for kind in ("column", "rows", "values", "years", "spans", "fanout", "skipped", "sampled", "ran", "error"):
        assert f"\n{kind}," in checks_text, kind
    assert "error,ANAES_RECORD,ANAES_KIND_CAT,,,1205,,,\n" in checks_text
    assert {f["kind"] for f in json.loads(held.read("facts.json"))["facts"]} == {"join", "filter", "codes"}
    evidence = json.loads(held.read("sql_evidence.json"))["evidence"]
    assert {e["kind"] for e in evidence} == {"table", "column", "join", "filter"}
    # Every name resolves in the catalogue, and each entry holds only its kind, its names, a count and a date.
    assert len(sql_evidence.Saved.from_json(held.read("sql_evidence.json").decode(), CATALOGUE).items) == len(evidence)
    original = Path(browser.BOUNDARY_ROOT) / "state"
    before, _ = boundary.produce(original, requests)

    # Loaded again, with the same requests, the boundary writes the same files, apart from provenance.json.
    loaded = _loaded(saved, tmp_path / "loaded")
    after, _ = boundary.produce(loaded, requests)
    assert sorted(after) == sorted(before)
    assert [name for name in before if before[name] != after[name]] == []
    # Saved again from the loaded state, everything is unchanged.
    assert _page(loaded, requests)["ok"]
    assert browser.state_zip() == saved

    # Without any request files, the saved evidence settles the same items, and saving again keeps it unchanged.
    empty = tmp_path / "no-requests"
    empty.mkdir()
    alone, _ = boundary.produce(loaded, empty)
    assert _statuses(alone) == _statuses(before)
    assert alone["sql_evidence.json"] == before["sql_evidence.json"]
    checklist = alone["targets/neonatal_low_mean_pressure/checklist.csv"]
    assert "of the team's queries on " in checklist
    assert _page(loaded, empty)["ok"]
    assert browser.state_zip() == saved
    # Without the evidence, the same run leaves those items unsettled, so it is the evidence that settles them.
    (loaded / "sql_evidence.json").unlink()
    bare, _ = boundary.produce(loaded, empty)
    assert _statuses(bare) != _statuses(before)
    browser.clear()


def test_the_boundary_refuses_saved_evidence_that_names_what_the_catalogue_does_not_hold(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    shutil.copy(FIXTURES / "invented-catalogue.csv", state / "catalogue.csv")
    (state / "sql_evidence.json").write_text('{"evidence": [{"kind": "table", "names": ["SECRET"], "files": 1, "date": "2026-10-05"}]}')
    with pytest.raises(boundary.BoundaryError) as refused:
        boundary.produce(state, FIXTURES / "requests")
    assert str(refused.value) == boundary.WORDING["bad_evidence"]
