"""The local workbench, on the invented world only, through Starlette's test client.

The saved hospital schema is made as screen 1 makes it, as in test_feasibility.py, with the table of readings given
400 million rows so that the audit's package is of class B. Every test works in a temporary folder that holds the
project folder and a folder for the system's temporary files, and the last test checks that nothing was written outside
the project folder. These tests need starlette, jinja2, markdown, python-multipart and httpx:

    uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 --with pytest --with starlette==0.52.1 --with jinja2 \\
        --with markdown --with python-multipart --with httpx python -m pytest -q tests/test_workbench.py
"""
import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("jinja2")
pytest.importorskip("markdown")
pytest.importorskip("multipart")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from schemalyser import audit, describe, rolemap  # noqa: E402
from schemalyser.workbench import jobs  # noqa: E402
from schemalyser.workbench.app import create_app  # noqa: E402
from test_describe import DATE, DICTIONARY, TABLES, tables_result  # noqa: E402
from test_feasibility import CONFIRMED, COUNTS  # noqa: E402

PLANS = Path(__file__).parent / "plans"
AUDIT_SQL = rolemap.AUDIT.read_text(encoding="utf-8")
BODY = AUDIT_SQL.split("\n", 2)[2]  # the audit without the first two lines of its leading comment


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
    path = tmp_path_factory.mktemp("schema") / "hospital-schema.schemalyser.zip"
    path.write_bytes(s.folder_zip(DATE))
    return path


@pytest.fixture(scope="module")
def place(tmp_path_factory):
    """A temporary folder with the project folder and a folder that stands for the system's temporary files."""
    root = tmp_path_factory.mktemp("workbench")
    (root / "system-tmp").mkdir()
    return root


@pytest.fixture(scope="module")
def client(place, saved):
    held = tempfile.tempdir
    tempfile.tempdir = str(place / "system-tmp")
    project = place / "project"
    (project / "schemas").mkdir(parents=True)
    shutil.copy(saved, project / "schemas" / saved.name)
    app = create_app(project, hosts=["testserver"], describe_folder=place / "no-page")
    with TestClient(app) as test_client:
        yield test_client
    jobs.wait_all(timeout=600)
    tempfile.tempdir = held


def _question(client, title="Among neonates, how long was the mean pressure below 40?"):
    response = client.post("/questions", data={"title": title, "sql": BODY}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[1]


def _audit(client, question, **more):
    response = client.post("/audits", data=dict({"schema": "hospital-schema.schemalyser.zip", "question": question}, **more),
                           follow_redirects=False)
    assert response.status_code == 303, response.text
    jobs.wait_all(timeout=300)
    return response.headers["location"]


def test_the_overview_lists_a_schema_placed_in_the_project(client, place):
    page = client.get("/").text
    assert "hospital-schema.schemalyser.zip" in page
    assert "Patients: <span class=\"state\">checked against the database</span>" in page
    assert "Across the hospital schema, the page made" in page
    assert json.loads((place / "project" / "workbench.json").read_text()) == {"name": "project", "tool_version": audit.tool_version()}
    for folder in ("schemas", "questions", "audits", "runs"):
        assert (place / "project" / folder).is_dir()


def test_a_question_added_on_the_page_has_its_feasibility_report(client, place):
    name = _question(client)
    assert (place / "project" / "questions" / name).read_text().startswith("-- Among neonates, how long")
    assert "Among neonates, how long" in client.get("/").text
    page = client.get(f"/answerable?schema=hospital-schema.schemalyser.zip&question={name}").text
    assert "This question is expressible but cannot yet be answered reliably." in page
    assert "Evidence requests" in page and "For the database analyst" in page
    programme = client.get("/answerable?schema=hospital-schema.schemalyser.zip").text
    assert "The programme of questions" in programme and "have every requirement checked against the database" in programme


def test_an_audit_package_is_built_and_its_class_read_from_the_page(client, place):
    name = _question(client, "Among neonates, what was the mean pressure in the audit?")
    location = _audit(client, name, **{"from": "2024-01-01", "to": "2024-06-30", "decisions": "The audit covers half a year."})
    page = client.get(location).text
    assert "Class B: bounded validation." in page
    assert "The plan review is <span class=\"state\">not yet reviewed</span>" in page
    assert "Copy the script" in page and "#cohort" in page
    package = place / "project" / "audits" / location.rsplit("/", 1)[1] / "package"
    assert (package / "manifest.json").is_file() and (package / "query.sql").is_file()
    assert "The audit covers half a year." in (package / "manifest.json").read_text()


def test_the_plan_review_is_voided_once_the_script_changes(client, place):
    name = _question(client, "Among neonates, was the plan reviewed?")
    location = _audit(client, name)
    with (PLANS / "seek-from-cohort.sqlplan").open("rb") as plan:
        response = client.post(f"{location}/plan", files={"file": ("seek-from-cohort.sqlplan", plan)}, follow_redirects=False)
    assert response.status_code in (303, 400)
    package = place / "project" / "audits" / location.rsplit("/", 1)[1] / "package"
    assert (package / "plan-review.json").is_file()
    assert "The review is voided" not in client.get(location).text
    query = package / "query.sql"
    query.write_text(query.read_text(encoding="utf-8").replace("TOP (5000)", "TOP (50000)"), encoding="utf-8")
    page = client.get(location).text
    assert "The review is voided." in page and "has changed since the package was built" in page
    assert "The plan review is <span class=\"state\">voided</span>" in page


def test_a_fast_run_of_the_test_on_made_up_rows_and_its_report(client, place):
    response = client.post("/runs", data={"world": "fixtures", "rows": "30", "profile": "fast", "engine": "duckdb"},
                           follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    progress = client.get(f"{location}/progress")
    assert progress.status_code == 200
    jobs.wait_all(timeout=600)
    assert client.get(f"{location}/progress").headers.get("HX-Refresh") == "true"
    page = client.get(location).text
    assert "The fast profile passed" in page
    assert "Unexplained discrepancies: 0" in page and "Could not be traced: 2" in page
    assert "every planted scenario passed" in page.lower()
    step = re.search(rf'href="({re.escape(location)}/steps/person\.sql)"', page).group(1)
    detail = client.get(step).text
    assert "SELECT" in detail and "wrote" in detail
    assert '<span class="outcome good">passed</span>' in client.get("/runs").text


def test_a_form_from_another_site_is_refused(client):
    response = client.post("/questions", data={"title": "x", "sql": "SELECT 1"}, headers={"Origin": "http://elsewhere.example"})
    assert response.status_code == 403


def test_nothing_is_written_outside_the_project_folder(client, place):
    jobs.wait_all(timeout=600)
    outside = [p for p in place.rglob("*") if p.is_file() and not p.is_relative_to(place / "project")]
    assert outside == []
