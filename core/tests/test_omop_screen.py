"""The OMOP layer screen of the workbench, screen 3 of docs/screens.md, on the invented world only, through Starlette's
test client.

The saved hospital schema is made as screen 1 makes it, as in test_export_screen.py. Further worlds are copies of the
invented world's conversion kept in the project's worlds/, each changed so that the release refuses it or so that it is
a draft. The test on made-up rows runs once, in the fast profile at 30 rows on DuckDB. The Athena download is pointed at
an empty place inside the temporary folder, so that no run reads one. One test covers each step of the screen, in its
order, and the last checks that nothing was written outside the project folder. These tests need starlette, jinja2,
markdown, python-multipart and httpx, as test_workbench.py does.
"""
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("jinja2")
pytest.importorskip("markdown")
pytest.importorskip("multipart")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from schemalyser import describe, project as project_module, release, roleshadow, vocabulary  # noqa: E402
from schemalyser.workbench import jobs  # noqa: E402
from schemalyser.workbench.app import create_app  # noqa: E402
from test_describe import DATE, DICTIONARY, TABLES, tables_result  # noqa: E402
from test_feasibility import CONFIRMED, COUNTS  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SCHEMA = "hospital-schema.schemalyser.zip"
W = vocabulary.OMOP_SCREEN
# The words that the owner keeps off the screen, of which "schema" is allowed only as the hospital schema.
NEVER = [r"\bfolder", r"\bmap\b", r"\bbinding", r"\bview\b", r"role_", r"\bcontract\b", r"\bbridge", r"invented rows",
         r"\bcomplete\b", r"\baudit", "!"]
DIRECT = {"route": "direct", "reference": "an invented reference conversion", "reason": "The test needs one direct step.",
          "review": {"by": "a tester", "on": "2026-10-09"}}
STEP = "SELECT ROW_NUMBER() OVER (ORDER BY r.SEQ) AS measurement_id, r.SEQ AS person_id FROM OBS_READING r"


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    s.set_settings("production", 2024, "Australia/Sydney", True)
    s.tables_query()
    s.read_tables(tables_result({"OBS_READING": 400_000_000}))
    for about in CONFIRMED:
        s.confirm(about, "yes", date=DATE)
    s.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff"}, DATE)
    for query in s.count_queries(2024, "8. Run the counts"):
        if query["name"] in COUNTS:
            s.read_count(query["name"], COUNTS[query["name"]], DATE)
            s.judge_count(query["name"], "yes", "", DATE)
    path = tmp_path_factory.mktemp("schema") / SCHEMA
    path.write_bytes(roleshadow.save_zip(s, DATE))
    return path


@pytest.fixture(scope="module")
def place(tmp_path_factory):
    root = tmp_path_factory.mktemp("omop-screen")
    (root / "system-tmp").mkdir()
    return root


def _world(project, name, steps=None, step=None, draft=None):
    """A world in the project's worlds/: the invented catalogue, and the invented conversion, or one small step."""
    folder = project / "worlds" / name
    (folder / "requests").mkdir(parents=True)
    shutil.copy(FIXTURES / "invented-catalogue.csv", folder / "catalogue.csv")
    if steps is None:
        shutil.copytree(FIXTURES / "conversion", folder / "conversion")
    else:
        (folder / "conversion").mkdir()
        (folder / "conversion" / "conversion.json").write_text(json.dumps(steps))
        (folder / "conversion" / "step.sql").write_text(step or STEP)
    if draft is not None:
        (folder / "conversion" / "draft.json").write_text(json.dumps(draft))
    return str(folder)


@pytest.fixture(scope="module")
def client(place, saved):
    held, athena = tempfile.tempdir, __import__("os").environ.get("SCHEMALYSER_ATHENA")
    tempfile.tempdir = str(place / "system-tmp")
    __import__("os").environ["SCHEMALYSER_ATHENA"] = str(place / "project" / "no-athena")
    project = place / "project"
    (project / "schemas").mkdir(parents=True)
    shutil.copy(saved, project / "schemas" / SCHEMA)
    one = {"table": "measurement", "file": "step.sql", "layer": "anaesthesia"}
    worlds = {
        "draft": _world(project, "draft", draft={"draft": True, "reference": "an invented reference"}),
        "no_route": _world(project, "no_route", [one]),
        "no_review": _world(project, "no_review", [dict(one, **{k: v for k, v in DIRECT.items() if k != "review"})]),
        "class_d": _world(project, "class_d", [dict(one, **DIRECT)],
                          STEP + "\nLEFT JOIN omop.source_to_concept_map m ON m.source_code = 'X'"),
    }
    app = create_app(project, describe_folder=place / "no-page")
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.worlds = worlds
        yield test_client
    jobs.wait_all(timeout=600)
    tempfile.tempdir = held
    if athena is None:
        __import__("os").environ.pop("SCHEMALYSER_ATHENA", None)
    else:
        __import__("os").environ["SCHEMALYSER_ATHENA"] = athena


def _text(page):
    """What a person reads on the page in the screen's own words: its text without the markup, the folded SQL and
    logs, or what the core recorded, which the page sets apart as recorded."""
    page = re.sub(r"<(pre|script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    page = re.sub(r'<blockquote class="recorded[^"]*">.*?</blockquote>', " ", page, flags=re.S)
    page = re.sub(r"<code>.*?</code>", " ", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page))


def _all_text(page):
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def test_the_screen_s_own_words_keep_to_the_owner_s_vocabulary():
    for text in _strings(W):
        for pattern in NEVER:
            assert not re.search(pattern, text, re.I), (pattern, text)
        assert all(text[max(0, m.start() - 9):m.start()] == "hospital " for m in re.finditer(r"(?<!{)\bschema\b(?!})", text)), text


# 1. The conversion.

def test_the_conversion_shows_each_step_with_its_route_review_and_class_and_the_share_on_each_route(client):
    page = client.get("/omop")
    assert page.status_code == 200
    text, every = _text(page.text), _all_text(page.text)
    report = release.conversion_report(FIXTURES / "conversion", client.app.state.project.schema_path(SCHEMA))
    shares, carried = report["shares"], report["release_shares"]
    assert (shares["steps"], shares["roles"], shares["direct"]) == (20, 1, 19)
    assert W["shares"].format(world="the invented hospital", count=20, roles=1, roles_verb="is", direct=19,
                              direct_verb="are") in text
    assert W["release_shares"].format(count=10, roles=1, roles_verb="is", direct=9, direct_verb="are") in text
    assert W["not_draft"] in text and W["step1"] in text
    # A direct step shows the reference, reason and review that the conversion records, as recorded.
    assert "the invented world's own conversion, written by hand against its source tables" in every
    assert "The owner accepted it on 5 October 2026." in text
    assert W["route_roles"] in text and W["route_direct"] in text and W["route_derived"] in text
    # The class of each step under the policy, and the reason that the conversion records for a class D step.
    assert W["class"].format(grade="C") in text and W["class"].format(grade="D") in text and W["class_recorded"] in text
    assert "only the server's clock gives, through GETDATE" in every
    assert "drug_exposure_infusion.sql" in every and W["alternative"] in text
    assert W["gates"].format(count=len(report["gates"])) in text


def test_a_draft_names_the_reference_it_was_transplanted_from_and_the_command_prints_the_same_report(client):
    text = _all_text(client.get("/omop", params={"world": client.worlds["draft"]}).text)
    assert W["draft_heading"] in text
    assert "This conversion is a draft, transplanted from an invented reference, so no step of it has been accepted" in text
    folder = Path(client.worlds["draft"]) / "conversion"
    printed = subprocess.run([sys.executable, "-m", "schemalyser.release", str(folder), "--catalogue",
                              str(FIXTURES / "invented-catalogue.csv"), "--report"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parents[1])
    assert printed.returncode == 0, printed.stderr
    assert json.loads(printed.stdout)["draft_sentence"] == release.conversion_report(folder)["draft_sentence"]


# 2. The test on made-up rows.

@pytest.fixture(scope="module")
def tested(client):
    page = client.get("/omop").text
    assert W["test_none"] in _text(page)
    response = client.post("/omop/test", data={"world": "fixtures", "rows": "30", "profile": "fast", "engine": "duckdb"},
                           follow_redirects=False)
    assert response.status_code == 303 and "run=" in response.headers["location"]
    jobs.wait_all(timeout=600)
    return response.headers["location"]


def test_one_action_runs_the_test_and_the_screen_shows_its_report_by_section(client, tested):
    page = client.get(tested).text
    text, every = _text(page), _all_text(page)
    # The full profile and the Athena vocabulary are offered, each with its note.
    assert W["profiles"]["full"] in text and W["profile_note"] in text
    assert W["vocabularies"]["athena"] in text and W["vocabulary_note"] in text
    assert "The fast profile passed, in" in text
    assert W["scenarios_heading"] in text and "age by calendar date" in text
    assert W["scenarios_count"].format(passed=21, count=21) in text
    # The scenarios over the parts of the record, against the rows held out for them.
    assert W["roles_heading"] in text and "infusion boundary" in text
    assert W["roles_rows"].format(expected=8, found=8, missing=0, unexpected=0) in text
    for group in W["groups"].values():
        assert group in text
    assert f"{W['groups']['unexplained']}: 0" in text and f"{W['groups']['not_traced']}: 3" in text
    assert W["dqd_not_run"].format(reason="the fast profile does not run it") in text
    assert W["dqd_permitted"].format(count=6) in text
    assert W["rewrites_note"] in text and "float_as_double" in every
    assert W["release_eq_heading"] in text and W["states"]["not run on SQL Server"] in text
    assert W["package_eq_heading"] in text and W["states"]["not run"] in text
    run = tested.split("run=")[1].split("#")[0].split("&")[0]
    assert (client.app.state.project.root / "runs" / run / "out" / "report.json").is_file()
    assert client.get(f"/omop/progress?run={run}").headers.get("HX-Refresh") == "true"


# 3. The release.

def test_the_release_is_written_through_the_saved_hospital_schema_into_the_project(client):
    response = client.post("/omop/release", data={"world": "fixtures", "schema": SCHEMA}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    text, every = _text(page), _all_text(page)
    name = response.headers["location"].split("release=")[1].split("#")[0]
    folder = client.app.state.project.root / "omop" / "releases" / name
    script = (folder / "release.sql").read_text(encoding="utf-8")
    assert (folder / "source_manifest.csv").is_file() and (folder / "step_classes.json").is_file()
    assert W["release_written"].format(date=_day_today(), world="the invented hospital", schema=SCHEMA,
                                       path=f"omop/releases/{name}/release.sql") in text
    # The header states the routes and the classes, and the steps over the parts were compiled through the saved schema.
    assert "Of the 10 anaesthesia steps that this script carries, 1 is written over the roles and 9 are written" in every
    assert "so the script as a whole is of class C" in every
    assert "-- " + release.WORDING["roles_compiled"].format(world="the hospital") in script
    assert "FROM $(SourcePrefix)[DRUG_GIVEN]" in script
    assert W["script_copy"] in text and "sqlcmd -S SERVER" in every
    assert json.loads((folder / "record.json").read_text())["exit_code"] == 0


def _day_today():
    import datetime as dt
    day = dt.date.today()
    return f"{day.day} {day:%B %Y}"


@pytest.mark.parametrize("world, says", [
    ("no_route", "step.sql: conversion.json records no route for this step"),
    ("no_review", "step.sql: this step is written directly from the source tables, and conversion.json does not record the review"),
    ("class_d", "step.sql: the static policy places this step in class D under the conversion purpose"),
])
def test_a_refusal_of_the_release_is_shown_plainly(client, world, says):
    response = client.post("/omop/release", data={"world": client.worlds[world], "schema": SCHEMA}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    text, every = _text(page), _all_text(page)
    assert W["release_refused"].format(world=Path(client.worlds[world]).name, schema=SCHEMA) in text
    assert says in every and "because of the following problem" in every
    name = response.headers["location"].split("release=")[1].split("#")[0]
    folder = client.app.state.project.root / "omop" / "releases" / name
    assert not (folder / "release.sql").exists() and json.loads((folder / "record.json").read_text())["exit_code"] == 1


# 4. Equivalence per question.

def test_each_question_shows_its_equivalence_as_not_yet_shown(client):
    page = _text(client.get("/omop").text)
    assert W["equivalence_none"] in page
    assert client.post("/questions", data={"title": "Among neonates, how long was the pressure low?",
                                           "sql": "SELECT COUNT(*) AS n FROM role_anaesthetic"},
                       follow_redirects=False).status_code == 303
    page = _text(client.get("/omop").text)
    assert W["equivalence_says"] in page and "never assumed from the conversion passing its tests" in page
    assert "Among neonates, how long was the pressure low?" in page and W["equivalence_states"]["not yet shown"] in page
    root = client.app.state.project.root
    assert project_module.main(["equivalence", str(root)]) == 0
    assert [q["state"] for q in project_module.question_equivalence(root)] == ["not yet shown"]


def test_the_screen_keeps_its_steps_in_order_and_its_words_to_the_vocabulary(client, tested):
    # The page's own words, which lie in its main part; the header names the workbench's other screens.
    page = _text(client.get(tested).text.split("<main>", 1)[1].split("</main>", 1)[0])
    places = [page.index(W[f"step{n}"]) for n in range(1, 5)]
    assert places == sorted(places) and all(W[f"step{n}_who"] in page for n in range(1, 5))
    for pattern in NEVER:
        assert not re.search(pattern, page, re.I), pattern
    templates = Path(create_app.__code__.co_filename).parent / "templates"
    literal = re.sub(r"{[{%#].*?[}%#]}", " ", (templates / "omop.html").read_text(encoding="utf-8"), flags=re.S)
    for pattern in NEVER:
        assert not re.search(pattern, _text(literal), re.I), pattern


def test_nothing_is_written_outside_the_project_folder(client, place, tested):
    jobs.wait_all(timeout=600)
    outside = [p for p in place.rglob("*") if p.is_file() and not p.is_relative_to(place / "project")]
    assert outside == []
