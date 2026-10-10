"""The export screen of the workbench, screen 2 of docs/screens.md, on the invented world only, through Starlette's test
client.

The saved hospital schema is made as screen 1 makes it, as in test_workbench.py, with the table of readings given 400
million rows. The episode lists and the example specification are those of fixtures/export/. One test covers each step
of the screen, in the screen's order, and the last checks that nothing was written outside the project folder. These
tests need starlette, jinja2, markdown, python-multipart and httpx, as test_workbench.py does.
"""
import html
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

from schemalyser import audit, describe, feasibility, rolemap, roleshadow, specification, vocabulary  # noqa: E402
from schemalyser.workbench import jobs  # noqa: E402
from schemalyser.workbench.app import create_app  # noqa: E402
from test_describe import DATE, DICTIONARY, TABLES, tables_result  # noqa: E402
from test_feasibility import CONFIRMED, COUNTS  # noqa: E402

FIXTURES = rolemap.MODEL.parents[2] / "fixtures" / "export"
SPEC = FIXTURES / "neonatal-pressures.specification.json"
KEYS = FIXTURES / "invented-episodes.csv"
PAIRS = FIXTURES / "invented-episode-pairs.csv"
SCHEMA = "hospital-schema.schemalyser.zip"
W = vocabulary.EXPORT_SCREEN
# The words that the owner keeps off the screen, of which "schema" is allowed only as the hospital schema.
NEVER = [r"\bfolder", r"\bmap\b", r"\bbinding", r"\bview\b", r"role_", r"\bcontract\b", r"\bbridge", r"invented rows",
         r"under 10", r"\bcomplete\b", r"\baudit", "!"]


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
    root = tmp_path_factory.mktemp("export-screen")
    (root / "system-tmp").mkdir()
    return root


@pytest.fixture(scope="module")
def client(place, saved):
    held = tempfile.tempdir
    tempfile.tempdir = str(place / "system-tmp")
    project = place / "project"
    (project / "schemas").mkdir(parents=True)
    shutil.copy(saved, project / "schemas" / SCHEMA)
    app = create_app(project, describe_folder=place / "no-page")
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    jobs.wait_all(timeout=600)
    tempfile.tempdir = held


def _start(client):
    response = client.post("/exports", data={"schema": SCHEMA}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def _text(page):
    """What a person reads on the page: its text without the markup, the folded SQL or the copied requests."""
    page = re.sub(r"<(pre|script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page))


def _form(pairs):
    """Form fields given as pairs, as the page posts them, with a list where a name repeats."""
    found = {}
    for name, value in pairs:
        found.setdefault(name, []).append(value)
    return {k: v[0] if len(v) == 1 else v for k, v in found.items()}


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


@pytest.fixture(scope="module")
def export(client, place):
    """An export with the invented list of anaesthetic keys and the example specification, compiled once."""
    location = _start(client)
    folder = place / "project" / "exports" / location.rsplit("/", 1)[1]
    assert client.post(f"{location}/episodes", data={"form": "anaesthetic_keys"},
                       files={"file": ("episodes.csv", KEYS.read_bytes())}, follow_redirects=False).status_code == 303
    assert client.post(f"{location}/specification/load", files={"file": ("spec.json", SPEC.read_bytes())},
                       follow_redirects=False).status_code == 303
    assert client.post(f"{location}/package", follow_redirects=False).status_code == 303
    jobs.wait_all(timeout=600)
    return location, folder


def test_the_screen_s_own_words_keep_to_the_owner_s_vocabulary():
    for text in _strings({k: v for k, v in W.items() if k != "parts"}):
        for pattern in NEVER:
            assert not re.search(pattern, text, re.I), (pattern, text)
        assert all(m.start() >= 9 and text[m.start() - 9:m.start()] == "hospital " for m in re.finditer(r"(?<!{)\bschema\b(?!})", text)), text
    assert list(W["states"]) == list(feasibility.STATES)


# 1. The hospital schema.

def test_the_screen_opens_with_the_hospital_schema_and_what_each_section_supports(client, place, saved):
    location = _start(client)
    page = client.get(location)
    assert page.status_code == 200
    text = _text(page.text)
    assert W["step1"] in text and SCHEMA in text
    support = feasibility.sections(feasibility.Schema.load(saved))
    parts = {p["part"]: p for g in support["groups"] for p in g["parts"]}
    # The state of each section and its smallest piece of work are the feasibility report's, shown as recorded.
    assert parts["role_reading"]["part_state"] in text and parts["role_reading"]["smallest"]["says"] in text
    assert parts["role_fluid"]["smallest"]["says"] in text and feasibility.NOT_MAPPED in text
    # Readiness names its three states, and one that the file does not record is not reached.
    assert f"{W['readiness_validated']} {W['not_reached']}" in text and W["readiness_runs"] in text
    assert (place / "project" / "exports" / location.rsplit("/", 1)[1] / "support.json").is_file()


# 2. The episodes.

def test_the_episode_list_is_kept_in_the_project_by_its_hash_and_a_bad_one_names_its_line_but_not_its_value(client, place, tmp_path):
    location = _start(client)
    folder = place / "project" / "exports" / location.rsplit("/", 1)[1]
    assert W["episodes_note"] in _text(client.get(location).text)
    bad = b"anaesthetic_key\n990002400\n99\xc3\xa9secret\n"
    response = client.post(f"{location}/episodes", data={"form": "anaesthetic_keys"}, files={"file": ("e.csv", bad)})
    assert response.status_code == 400 and "Line 3" in response.text and "secret" not in response.text
    assert not (folder / "episodes.csv").exists()
    response = client.post(f"{location}/episodes", data={"form": "anaesthetic_keys"},
                           files={"file": ("e.csv", KEYS.read_bytes())}, follow_redirects=False)
    assert response.status_code == 303
    record = json.loads((folder / "request.json").read_text())
    assert record["episodes"] == {"form": "anaesthetic_keys", "count": 6, "sha256": specification.sha256(KEYS.read_bytes())}
    assert (folder / "episodes.csv").read_bytes() == KEYS.read_bytes()
    assert record["episodes"]["sha256"][:12] in _text(client.get(location).text)


# 3. The sections.

def test_the_sections_are_a_tree_in_the_clinician_s_words_and_an_unmapped_one_cannot_be_chosen(client):
    page = client.get(_start(client)).text
    text = _text(page)
    for title in W["groups"].values():
        assert title in text
    assert 'name="section" value="role_reading"' in page and 'name="window.role_reading.from_minutes"' in page
    assert 'name="flag.role_reading.accepted"' in page
    assert 'name="kinds.role_reading" value="map_arterial"' in page and 'value="spo2"' not in page.split('id="derived"')[0]
    # Fluids are not mapped by this hospital schema: they are shown as not currently mapped, with the work that would
    # move them, and offer nothing to choose.
    assert 'name="section" value="role_fluid"' not in page and W["cannot_choose"] in text
    fluids = page.split('id="part-fluid"')[1].split('<div class="choice')[0]
    assert feasibility.NOT_MAPPED in fluids and "For the" in fluids


# 4. The derived sections.

def test_the_derived_sections_offer_their_parameters_and_an_unsupported_one_shows_its_request(client):
    page = client.get(_start(client)).text
    text = _text(page)
    assert 'name="derived" value="hypotension_burden"' in page
    assert 'name="param.hypotension_burden.threshold_by_age_band.0.2"' in page
    assert 'name="param.monitoring_completeness.kinds.0.0"' in page
    assert W["table_columns"]["until_days"] in text and W["no_default"] in text
    # A measure that needs a part that this hospital schema does not map is not currently supported, never unavailable.
    transfusion = page.split('id="measure-transfusion"')[1].split("</details>")[0]
    assert 'name="derived"' not in transfusion and feasibility.NOT_SUPPORTED in transfusion and "For the" in transfusion
    # A measure that the catalogue declares without SQL cannot be chosen at any hospital.
    declared = page.split('id="measure-death_within_days"')[1].split("</details>")[0]
    assert 'name="derived"' not in declared and W["declared"] in _text(declared)


# 5. The output.

def test_the_output_says_that_rows_are_identifiable_and_the_notes_stay_unless_named(client):
    page = client.get(_start(client)).text
    text = _text(page)
    assert W["identifiable"] in text and W["leaving_note"] in text
    assert 'name="output.class" value="rows" checked' in page and 'name="output.keys" value="pseudonymised" checked' in page
    assert 'name="leave" value="role_reading" checked' in page and 'name="leave" value="role_note"' not in page


# 6. The specification.

def test_the_specification_is_written_from_the_choices_saved_and_loaded_again(client, place):
    location = _start(client)
    folder = place / "project" / "exports" / location.rsplit("/", 1)[1]
    fields = [("title", "Mean pressures of the planted neonates"), ("section", "role_anaesthetic"), ("section", "role_reading"),
              ("kinds.role_reading", "map_arterial"), ("window.role_reading", "1"), ("window.role_reading.from", "start"),
              ("window.role_reading.from_minutes", "-15"), ("window.role_reading.to", "stop"),
              ("window.role_reading.to_minutes", "15"), ("flag.role_reading.accepted", "1"), ("output.class", "rows"),
              ("output.keys", "pseudonymised"), ("leave", "role_reading")]
    response = client.post(f"{location}/specification", data=_form(fields), follow_redirects=False)
    assert response.status_code == 303
    spec = json.loads((folder / "specification.json").read_text())
    assert specification.validate(spec) == [] and spec["sections"][1]["kinds"] == ["map_arterial"]
    assert spec["output"]["leaving"] == ["reading"]
    # It holds no hospital material: nothing of the saved schema's tables and no key of an episode.
    text = json.dumps(spec)
    assert "OBS_" not in text and "9900" not in text and "SELECT" not in text.upper()
    page = client.get(location).text
    assert W["spec_ok"] in _text(page) and 'name="kinds.role_reading" value="map_arterial" checked' in page
    response = client.post(f"{location}/specification/save", follow_redirects=False)
    assert response.status_code == 303
    (saved_name,) = [p.name for p in (place / "project" / "specifications").glob("*.json")]
    assert json.loads((place / "project" / "specifications" / saved_name).read_text()) == spec
    other = _start(client)
    assert client.post(f"{other}/specification/load", data={"saved": saved_name}, follow_redirects=False).status_code == 303
    assert json.loads((place / "project" / "exports" / other.rsplit("/", 1)[1] / "specification.json").read_text()) == spec
    # A specification that breaks a rule is kept as chosen and named with the rule, and no package is compiled from it.
    response = client.post(f"{location}/specification", data=_form(fields + [("section", "role_reading"),
                           ("kinds.role_reading", "not_a_kind")]), follow_redirects=False)
    page = _text(client.get(location).text)
    assert W["spec_refused"] in page and specification.RULES["kinds"] in page
    assert client.post(f"{location}/package", follow_redirects=False).status_code == 400


# 7. The package.

def test_one_action_compiles_the_package_and_the_analyst_s_approval_shows_until_a_change_voids_it(client, export):
    location, folder = export
    page = client.get(location).text
    text = _text(page)
    status = audit.export_status(folder / "package")
    readings = next(s for s in status["sections"] if s["name"] == "mean_pressures")
    # Per section, the feasibility report with its two claims named apart, the series, the class and the manifest.
    assert readings["feasibility"]["verdict_text"] in text
    assert W["claim_requirements"] in text and W["claim_coverage"] in text
    assert W["series"]["count"] in text and W["series"]["coverage"] in text and readings["class_says"] in text
    assert W["roles_sql"] in text and "role_reading" in page.split('id="section-mean_pressures"')[1]
    assert W["manifest_where"].format(path=f"exports/{location.rsplit('/', 1)[1]}/package/sections/mean_pressures") in text
    assert W["outcomes"]["declared without SQL"] in text
    # The database analyst records approval with their name, and the screen shows it.
    response = client.post(f"{location}/sections/mean_pressures/approve", data={"by": "Ms D. Analyst", "decision": "approve"},
                           follow_redirects=False)
    assert response.status_code == 303
    manifest = json.loads((folder / "package" / "sections" / "mean_pressures" / "manifest.json").read_text())
    assert manifest["approval"]["state"] == "approved" and manifest["approval"]["by"] == "Ms D. Analyst"
    assert "Ms D. Analyst approved the package" in _text(client.get(location).text)
    assert client.post(f"{location}/sections/anaesthetics/approve", data={"by": " ", "decision": "approve"}).status_code == 400
    # A change to the script voids the approval, and the screen says so.
    query = folder / "package" / "sections" / "mean_pressures" / "query.sql"
    query.write_text(query.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    text = _text(client.get(location).text)
    assert W["voided"] in text


# 8. The results.

def test_the_returned_files_are_imported_as_evidence_and_the_results_package_is_shown(client, export, place):
    location, folder = export
    assert client.post(f"{location}/sections/anaesthetics/approve", data={"by": "Ms D. Analyst", "decision": "approve"},
                       follow_redirects=False).status_code == 303
    manifest = json.loads((folder / "package" / "sections" / "anaesthetics" / "manifest.json").read_text())
    columns = manifest["expected_output"]["columns"]
    result = (",".join(columns) + "\n" + ",".join("1" for _ in columns) + "\n").encode()
    response = client.post(f"{location}/sections/anaesthetics/returned", data={"by": "Ms D. Analyst"}, files={
        "outcome": ("outcome.tsv", b"rows_returned\tseconds\toutcome\n6\t1.5\tcompleted\n"),
        "plan": ("plan.sqlplan", b"<ShowPlanXML/>"), "result": ("result.csv", result)}, follow_redirects=False)
    assert response.status_code == 303, response.text
    # Both imports went through the hospital schema's evidence import, one after the other, and the export keeps the
    # schema it was compiled from.
    record = json.loads((folder / "request.json").read_text())
    latest = place / "project" / "schemas" / record["evidence_schema"]
    s = describe.Describe()
    s.restore(describe._read_saved(latest))
    assert s.log.latest("evidence imported", form="plan") and s.log.latest("evidence imported", form="production outcome")
    assert record["schema"] == SCHEMA and audit.status(folder / "package" / "sections" / "anaesthetics")["voided"] is False
    text = _text(client.get(location).text)
    assert W["shape"].format(rows=1, row_word="row", columns=len(columns), column_word="columns") in text and W["shape_same"] in text
    assert W["coverage_heading"] in text and W["disclosure_heading"] in text
    (results_folder,) = (folder / "results").iterdir()
    held = json.loads((results_folder / "results.json").read_text())
    assert held["disclosure"]["row_level"] and all(line in text for line in held["disclosure"]["does_not_protect"])


def test_an_export_of_patient_and_date_pairs_shows_the_share_resolved_to_exactly_one(client, place):
    location = _start(client)
    folder = place / "project" / "exports" / location.rsplit("/", 1)[1]
    assert client.post(f"{location}/episodes", data={"form": "patient_dates", "window_hours": "12", "several": "all, marked"},
                       files={"file": ("pairs.csv", PAIRS.read_bytes())}, follow_redirects=False).status_code == 303
    fields = [("title", "Pairs"), ("section", "role_anaesthetic"), ("output.class", "rows"), ("output.keys", "pseudonymised")]
    assert client.post(f"{location}/specification", data=_form(fields), follow_redirects=False).status_code == 303
    spec = json.loads((folder / "specification.json").read_text())
    assert spec["episodes"] == {"form": "patient_dates", "window_hours": 12, "several": "all, marked"}
    assert W["resolution_none"] in _text(client.get(location).text)
    assert client.post(f"{location}/package", follow_redirects=False).status_code == 303
    jobs.wait_all(timeout=600)
    assert client.post(f"{location}/sections/episode_resolution/approve", data={"by": "Ms D. Analyst", "decision": "approve"},
                       follow_redirects=False).status_code == 303
    result = b"pairs,resolved_to_one,ambiguous,resolved_to_none\n40,30,6,4\n"
    assert client.post(f"{location}/sections/episode_resolution/returned", data={"by": "Ms D. Analyst"},
                       files={"result": ("resolution.csv", result)}, follow_redirects=False).status_code == 303
    text = _text(client.get(location).text)
    assert W["resolution"].format(pairs=40, one=30, share=75.0, ambiguous=6, none=4) in text


def test_the_screen_s_templates_write_no_word_of_their_own_outside_the_vocabulary(client, export):
    location, _ = export
    templates = Path(create_app.__code__.co_filename).parent / "templates"
    for name in ("export.html", "exports.html", "_requests.html"):
        literal = re.sub(r"{[{%#].*?[}%#]}", " ", (templates / name).read_text(encoding="utf-8"), flags=re.S)
        for pattern in NEVER:
            assert not re.search(pattern, _text(literal), re.I), (name, pattern)
    # Every step is on the page, in its order, and each names who acts.
    page = _text(client.get(location).text)
    places = [page.index(W[f"step{n}"]) for n in range(1, 9)]
    assert places == sorted(places) and all(W[f"step{n}_who"] in page for n in range(1, 9))


def test_nothing_is_written_outside_the_project_folder(client, place, export):
    jobs.wait_all(timeout=600)
    outside = [p for p in place.rglob("*") if p.is_file() and not p.is_relative_to(place / "project")]
    assert outside == []
