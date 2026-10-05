import csv
import hashlib
import io
import json
import os
import re
import shutil
import zipfile
import subprocess
import sys
from pathlib import Path

import pytest
import sqlglot

from schemalyser import Analysis, boundary, convert, harness, questions, target
from schemalyser.catalogue import Catalogue

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PACKAGE = Path(__file__).resolve().parents[1] / "schemalyser"
PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]
STATE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
REQUESTS_COMMIT = "FEDCBA9876543210FEDCBA9876543210FEDCBA98"
# A request written to put text of its own into the outputs. None of these words may appear in any of them.
HOSTILE = """-- Zanzibarine note: Quartermaine asked for this
SELECT 'Zanzibarine' AS Xylophonist, p.Xylophonist_Col, p.SEX_CAT
FROM PERSON_MASTER AS p
JOIN Zanzibarine_Table AS z ON z.Xylophonist = p.PERSON_KEY
WHERE p.SEX_CAT IN ('Zanzibarine', 'Xylophonist') AND p.BIRTH_TS > '2016-03-14';
EXEC('SELECT * FROM Zanzibarine_Secret');
"""
HOSTILE_WORDS = ("zanzibarine", "xylophonist")


def make_state(folder, *, rules=True, checks=True, conversion=True, targets=True, profile=True):
    """The invented world, arranged in the layout of the state folder."""
    folder.mkdir(parents=True)
    shutil.copy(FIXTURES / "invented-catalogue.csv", folder / boundary.CATALOGUE)
    if rules:
        shutil.copy(FIXTURES / "invented-site-rules.json", folder / boundary.RULES)
    if checks:
        shutil.copy(FIXTURES / "invented-checks.csv", folder / boundary.CHECKS)
    if conversion:
        shutil.copytree(FIXTURES / "conversion", folder / boundary.CONVERSION)
    if targets:
        shutil.copytree(FIXTURES / "targets", folder / boundary.TARGETS)
    if profile:
        shutil.copy(FIXTURES / "profile" / "invented-core-profile.csv", folder / boundary.PROFILE)
    return folder


def make_requests(folder, hostile=False):
    shutil.copytree(FIXTURES / "requests", folder)
    if hostile:
        (folder / "2025" / "Zanzibarine Xylophonist.sql").write_text(HOSTILE)
    return folder


def run(state, requests, out, *extra):
    code = boundary.main(["--state", str(state), "--requests", str(requests), "--out", str(out), *extra])
    return code, {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8") for p in sorted(out.rglob("*")) if p.is_file()} \
        if out.exists() else {}


@pytest.fixture(scope="module")
def invented(tmp_path_factory):
    base = tmp_path_factory.mktemp("boundary")
    state, requests = make_state(base / "state"), make_requests(base / "requests")
    code, files = run(state, requests, base / "out", "--state-commit", STATE_COMMIT, "--requests-commit", REQUESTS_COMMIT)
    return {"state": state, "requests": requests, "code": code, "files": files, "base": base}


@pytest.fixture(scope="module")
def hostile(tmp_path_factory):
    base = tmp_path_factory.mktemp("hostile")
    state, requests = make_state(base / "state"), make_requests(base / "requests", hostile=True)
    code, files = run(state, requests, base / "out")
    return {"code": code, "files": files}


def world(state, requests):
    return harness.World(state / boundary.CATALOGUE, requests, state / boundary.RULES)


# What is produced.

def test_the_invented_world_produces_every_output(invented):
    files = invented["files"]
    assert invented["code"] == 0
    for name in ("summary.md", "provenance.json", "check_script.sql", "questions.csv", "questions-summary.txt"):
        assert name in files, name
    for name in ("infant_low_pressure", "airway_by_anaesthesia_type", "infant_low_pressure_from_anaesthetic"):
        assert f"targets/{name}/checklist.csv" in files and f"targets/{name}/readiness.txt" in files


def test_the_pack_is_the_analysers_own_from_the_requests_alone(invented):
    analysis = Analysis((invented["state"] / boundary.CATALOGUE).read_text(), (invented["state"] / boundary.RULES).read_text())
    for path in sorted(invented["requests"].rglob("*.sql")):
        analysis.add_request(path.relative_to(invented["requests"]).as_posix(), path.read_text())
    pack = analysis.pack()
    assert {f"inventory/{name}" for name in pack} == {n for n in invented["files"] if n.startswith("inventory/")}
    for name, text in pack.items():
        assert invented["files"][f"inventory/{name}"] == text, name
    assert "inventory/checks.csv" not in invented["files"]


def test_the_check_script_plans_checks_for_the_columns_that_the_conversion_maps(invented):
    script = invented["files"]["check_script.sql"]
    state = invented["state"]
    requests_only = world(state, invented["requests"]).analysis().check_script()
    mapped = set()
    for step, sql in questions._steps(state / boundary.CONVERSION):
        mapped.update(convert.mapped_columns(sqlglot.parse_one(sql, dialect="tsql")))
    assert mapped
    for table, column in mapped:
        assert f"''values'', ''{table}'', ''{column}''" in script, (table, column)
    assert any(f"''values'', ''{t}'', ''{c}''" not in requests_only for t, c in mapped)


def test_the_check_script_matches_the_conversions_own_analysis(invented):
    """The check script is planned as convert.run plans it: the requests with the conversion as one more request."""
    state = invented["state"]
    checks = (state / boundary.CHECKS).read_text()
    analysis = world(state, invented["requests"]).analysis(checks)
    rules = (state / boundary.RULES).read_text()
    analysis.add_request("conversion", convert.as_request(
        [(step["table"], sql) for step, sql in questions._steps(state / boundary.CONVERSION)], questions._definitions(rules)))
    assert invented["files"]["check_script.sql"] == analysis.check_script(include_spans=True, include_fanout=True)


def test_the_register_and_the_checklists_match_their_own_commands(invented):
    state, files = invented["state"], invented["files"]
    checks = (state / boundary.CHECKS).read_text()
    profile = (state / boundary.PROFILE).read_text()
    found = questions.questions(world(state, invented["requests"]), state / boundary.CONVERSION, checks, profile)
    assert files["questions.csv"] == questions.to_csv(found)
    assert files["questions-summary.txt"] == questions.summary(found)
    for path in sorted((state / boundary.TARGETS).glob("*.sql")):
        rows, traced = target.checklist(world(state, invented["requests"]), state / boundary.CONVERSION,
                                        path.read_text(), checks, profile)
        assert files[f"targets/{path.stem}/checklist.csv"] == target.to_csv(rows)
        assert files[f"targets/{path.stem}/readiness.txt"] == target.readiness(rows, traced)


def test_two_runs_give_identical_files(invented, tmp_path):
    code, files = run(invented["state"], invented["requests"], tmp_path / "again",
                      "--state-commit", STATE_COMMIT, "--requests-commit", REQUESTS_COMMIT)
    assert code == 0 and files == invented["files"]


# The summary and the provenance.

def test_the_summary_names_what_was_read_and_each_target(invented):
    text = invented["files"]["summary.md"]
    assert STATE_COMMIT in text and REQUESTS_COMMIT.lower() in text
    assert "| Request files read | 15 |" in text
    found = list(csv.DictReader(io.StringIO(invented["files"]["questions.csv"])))
    assert boundary.WORDING["questions_total"].format(
        total=len(found), **{s: sum(r["status"] == s for r in found) for s in questions.STATUSES}) in text
    for name in ("infant_low_pressure", "airway_by_anaesthesia_type", "infant_low_pressure_from_anaesthetic"):
        assert f"### {name}" in text
        rows = list(csv.DictReader(io.StringIO(invented["files"][f"targets/{name}/checklist.csv"])))
        for row in rows:
            if row["blocking"] == "yes" and row["status"] == "open":
                assert boundary._cell(row["question"]) in text
    assert not re.search(r"^#+ .*\?", text, re.M)


def test_the_provenance_names_the_commits_and_every_output(invented):
    files = invented["files"]
    data = json.loads(files["provenance.json"])
    assert data["stateCommit"] == STATE_COMMIT and data["requestsCommit"] == REQUESTS_COMMIT.lower()
    assert set(data["outputs"]) == set(files) - {"provenance.json"}
    for name, digest in data["outputs"].items():
        assert hashlib.sha256(files[name].encode("utf-8")).hexdigest() == digest, name
    assert data["options"] == boundary.DEFAULT_OPTIONS


@pytest.mark.parametrize("value", ["main", "abc", "0123456789abcdef0123456789abcdef01234567x", "zz12345", "12 34567"])
def test_a_commit_that_is_not_hexadecimal_is_refused(invented, tmp_path, value):
    code, files = run(invented["state"], invented["requests"], tmp_path / "out", "--state-commit", value)
    assert code == 2 and not files


# The allowlist.

def _allowed(state):
    """Every word that an output may hold: the catalogue, the site rules, the OMOP field list, the conversion's
    own files and vocabularies, the names of the target files, and the fixed text of the package itself."""
    words = set()

    def add(text):
        words.update(w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(text)))

    catalogue = Catalogue.from_csv((state / boundary.CATALOGUE).read_text())
    for table in catalogue.tables():
        add(table.name + " " + " ".join(c.name for c in table.columns.values()) + " " + " ".join(
            str(c.data_type) for c in table.columns.values()))
    add((state / boundary.RULES).read_text())
    for table, fields in convert.cdm_fields().items():
        add(table + " " + " ".join(field for field, _, _ in fields))
    conversion = state / boundary.CONVERSION
    for path in conversion.rglob("*"):
        add(path.name)
    for table in convert.read_tables(conversion):
        add(table["name"] + " " + " ".join(field["name"] for field in table["fields"]))
    with open(conversion / "source_to_concept_map.csv", newline="") as f:
        add(" ".join(row["source_vocabulary_id"] for row in csv.DictReader(f)))
    add(" ".join(questions.vocabularies(conversion)))
    # The public names of the concepts that the conversion uses are the conversion's own file.
    if (conversion / "concept_names.csv").exists():
        add((conversion / "concept_names.csv").read_text())
    for path in conversion.glob("*.sql"):
        add(" ".join(re.findall(r"'(SITE_[A-Z_]+)'", path.read_text())))
    for path in (state / boundary.TARGETS).glob("*.sql"):
        add(path.stem)
    # The intents are sentences that the author of the conversion wrote, so they are the conversion's own text.
    intents = conversion / "intents.json"
    if intents.exists():
        add(" ".join(entry["intent"] for entry in json.loads(intents.read_text())))
    # The fixed wording and the fixed SQL of the check script are the package's own text.
    for path in PACKAGE.glob("*.py"):
        add(path.read_text())
    from schemalyser import tuning
    add(json.dumps(tuning.table()))
    return words


def _unknown(files, allowed):
    unknown = {}
    for name, text in files.items():
        text = re.sub(r"\b[0-9a-fA-F]{7,64}\b", " ", text)    # the commits and the checksums
        words = {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if w.lower() not in allowed}
        if words:
            unknown[name] = sorted(words)
    return unknown


def test_every_word_in_every_output_is_on_the_allowlist(invented):
    assert not _unknown(invented["files"], _allowed(invented["state"]))


def test_no_planted_value_appears_in_any_output(invented):
    for name, text in invented["files"].items():
        found = [value for value in PLANTED if value.lower() in text.lower()]
        assert not found, (name, found)


def test_no_value_from_the_check_results_appears_in_any_output(invented):
    """Each listed value and label in the invented check results that is not itself a name from the
    catalogue, the rules or the wording must be absent from every output."""
    allowed = _allowed(invented["state"])
    values = set()
    with open(invented["state"] / boundary.CHECKS, newline="") as f:
        for row in csv.DictReader(f):
            if row["check_kind"] == "values":
                for cell in (row["value"], row["label"]):
                    if cell and re.search(r"[A-Za-z]", cell) and not all(
                            w.lower() in allowed for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", cell)):
                        values.add(cell)
    assert values
    for name, text in invented["files"].items():
        assert not [value for value in values if value in text], name


def test_a_hostile_request_puts_nothing_of_its_own_into_any_output(hostile, invented):
    assert hostile["code"] == 0
    assert "| Request files read | 16 |" in hostile["files"]["summary.md"]
    for name, text in hostile["files"].items():
        lowered = text.lower()
        assert not [word for word in HOSTILE_WORDS if word in lowered], name
        assert not [value for value in PLANTED if value.lower() in lowered], name
    assert not _unknown(hostile["files"], _allowed(invented["state"]))


# Absences and refusals.

def test_a_state_with_only_a_catalogue_gives_the_inventory_and_the_check_script(tmp_path):
    state = make_state(tmp_path / "state", rules=False, checks=False, conversion=False, targets=False, profile=False)
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 0
    assert "check_script.sql" in files and "inventory/elements.csv" in files
    assert "questions.csv" not in files and not [n for n in files if n.startswith("targets/")]
    text = files["summary.md"]
    for key in ("rules_absent", "checks_absent", "profile_absent", "conversion_absent", "questions_none",
                "targets_none", "state_commit_none", "requests_commit_none"):
        assert boundary._cell(boundary.WORDING[key]) in text, key


def test_targets_without_a_conversion_are_named_but_not_checked(tmp_path):
    state = make_state(tmp_path / "state", conversion=False)
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 0 and boundary._cell(boundary.WORDING["targets_no_conversion"]) in files["summary.md"]


@pytest.mark.parametrize("damage, message", [
    (lambda s: (s / boundary.CATALOGUE).unlink(), "no_catalogue"),
    (lambda s: (s / boundary.CATALOGUE).write_text("not,a,catalogue\n1,2,3\n"), "empty_catalogue"),
    (lambda s: (s / boundary.RULES).write_text("{ not json"), "bad_rules"),
    (lambda s: (s / boundary.RULES).write_text('{"unknownKey": 1}'), "bad_rules"),
    (lambda s: (s / boundary.OPTIONS).write_text("{ not json"), "bad_options"),
    (lambda s: (s / boundary.OPTIONS).write_text('{"includeSpans": "yes"}'), "bad_options"),
    (lambda s: (s / boundary.OPTIONS).write_text('{"includeEverything": true}'), "bad_options"),
    (lambda s: (s / boundary.CHECKS).write_text("a,b\n1,2\n"), "bad_checks"),
    (lambda s: (s / boundary.CONVERSION / "conversion.json").write_text("{ not json"), "bad_conversion"),
    (lambda s: (s / boundary.PROFILE).write_text(""), "bad_profile"),
])
def test_an_unusable_state_writes_nothing_and_says_why(tmp_path, capsys, damage, message):
    state = make_state(tmp_path / "state")
    damage(state)
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 2 and not files and not (tmp_path / "out").exists()
    assert boundary.WORDING[message] in capsys.readouterr().err


def test_a_missing_folder_or_a_used_output_folder_is_refused(invented, tmp_path, capsys):
    code, _ = run(tmp_path / "nowhere", invented["requests"], tmp_path / "a")
    assert code == 2 and boundary.WORDING["no_state"] in capsys.readouterr().err
    code, _ = run(invented["state"], tmp_path / "nowhere", tmp_path / "b")
    assert code == 2 and boundary.WORDING["no_requests"] in capsys.readouterr().err
    used = tmp_path / "used"
    used.mkdir()
    (used / "old.txt").write_text("left from before")
    code, _ = run(invented["state"], invented["requests"], used)
    assert code == 2 and boundary.WORDING["out_not_empty"] in capsys.readouterr().err
    assert [p.name for p in used.iterdir()] == ["old.txt"]


def test_a_target_that_cannot_be_read_is_named_and_the_rest_is_written(tmp_path, capsys):
    state = make_state(tmp_path / "state")
    (state / boundary.TARGETS / "not_a_select.sql").write_text("DELETE FROM omop.person;")
    (state / boundary.TARGETS / "bad name!.sql").write_text("SELECT 1;")
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 1
    assert "targets/infant_low_pressure/readiness.txt" in files and "questions.csv" in files
    assert not [n for n in files if n.startswith("targets/not_a_select/") or "bad name" in n]
    text = files["summary.md"]
    assert boundary._cell(boundary.WORDING["target_refused"].format(name="not_a_select")) in text
    assert boundary._cell(boundary.WORDING["target_names_refused"].format(count="1 target query")) in text
    assert "could not read 1 target query" in capsys.readouterr().out


def test_a_target_that_is_not_ready_is_not_an_error(invented):
    assert invented["code"] == 0
    assert "| no |" in invented["files"]["summary.md"]


def test_links_and_large_files_are_left_out_and_counted(invented, tmp_path):
    requests = make_requests(tmp_path / "requests")
    (requests / "large.sql").write_bytes(b"-- " + b"x" * boundary.MAX_REQUEST_BYTES)
    os.symlink(invented["state"] / boundary.CATALOGUE, requests / "link.sql")
    (requests / ".git").mkdir()
    (requests / ".git" / "hidden.sql").write_text("SELECT 1;")
    kept, left_out = boundary.request_files(requests)
    assert left_out == 2 and len(kept) == 15
    code, files = run(invented["state"], requests, tmp_path / "out")
    assert code == 0
    assert boundary._cell(boundary.WORDING["left_out"].format(count="2 request files")) in files["summary.md"]


def test_the_spans_and_fanout_checks_are_included_by_default_and_the_options_can_leave_them_out(invented, tmp_path):
    assert boundary.DEFAULT_OPTIONS == {"includeSpans": True, "includeFanout": True, "writeSourceDraft": False,
                                        "writeSpecification": False}
    assert "''spans''" in invented["files"]["check_script.sql"] and "''fanout''" in invented["files"]["check_script.sql"]
    for key in ("options_spans", "options_fanout"):
        assert boundary._cell(boundary.WORDING[key]) in invented["files"]["summary.md"], key
    state = tmp_path / "state"
    shutil.copytree(invented["state"], state)
    (state / boundary.OPTIONS).write_text('{"includeSpans": false, "includeFanout": false}')
    code, files = run(state, invented["requests"], tmp_path / "out")
    assert code == 0
    assert "''spans''" not in files["check_script.sql"] and "''fanout''" not in files["check_script.sql"]
    for key in ("options_spans_off", "options_fanout_off"):
        assert boundary._cell(boundary.WORDING[key]) in files["summary.md"], key
    assert json.loads(files["provenance.json"])["options"] == {"includeSpans": False, "includeFanout": False, "writeSourceDraft": False,
                                                                 "writeSpecification": False}


def test_everything_adds_the_provenance_to_the_outputs_as_write_does(invented):
    outputs, facts = boundary.produce(invented["state"], invented["requests"], STATE_COMMIT, REQUESTS_COMMIT)
    assert boundary.everything(outputs, facts) == invented["files"]


# What the module needs and what it reaches.

def test_the_module_imports_without_duckdb_and_the_run_opens_no_network_module(invented, tmp_path):
    code = (
        "import sys\n"
        "import schemalyser.boundary as b\n"
        "assert 'duckdb' not in sys.modules, 'duckdb at import'\n"
        f"assert b.main(['--state', {str(invented['state'])!r}, '--requests', {str(invented['requests'])!r}, "
        f"'--out', {str(tmp_path / 'out')!r}]) == 0\n"
        "print(sorted(m for m in ('socket', 'ssl', 'http.client', 'urllib.request') if m in sys.modules))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=PACKAGE.parent, env={**os.environ, "PYTHONPATH": str(PACKAGE.parent)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"


def test_the_wording_is_calm_and_complete():
    def texts(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from texts(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from texts(item)
        else:
            yield value

    for text in texts(boundary.WORDING):
        assert "?" not in text and "!" not in text, text
    for key, text in boundary.WORDING.items():
        if isinstance(text, str) and not key.startswith("h_") and key not in ("title", "ready_yes", "ready_no", "target_details"):
            assert text.rstrip().endswith((".", ":")), key


# The page's entry point, which runs the same code inside the browser's worker.

def test_the_pages_entry_point_gives_the_boundary_commands_own_files(invented, monkeypatch, tmp_path):
    from schemalyser import browser
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser.boundary_begin()
    for kind, folder in (("state", invented["state"]), ("requests", invented["requests"])):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            assert browser.boundary_put(kind, path.relative_to(folder).as_posix(), path.read_bytes())
    result = json.loads(browser.boundary_run(STATE_COMMIT, REQUESTS_COMMIT))
    assert result["ok"] and result["conversion"] and result["requests"] == 15
    assert browser._boundary["files"] == invented["files"]
    # A path that leaves its folder, or a kind that is neither, is refused. A refused file is counted in the next run.
    assert not browser.boundary_put("state", "../outside.csv", b"x")
    assert not browser.boundary_put("elsewhere", "catalogue.csv", b"x")
    for entry in result["targets"]:
        rows = list(csv.DictReader(io.StringIO(invented["files"][f"targets/{entry['name']}/checklist.csv"])))
        blocking = [row for row in rows if row["blocking"] == "yes"]
        assert entry["counts"] == {"total": len(blocking), **{s: sum(r["status"] == s for r in blocking)
                                                              for s in questions.STATUSES}}
        assert [row["id"] for row in entry["rows"]] == [row["question_id"] for row in rows]
        for row in entry["rows"]:
            if row["kind"] == "relationship" and row["status"] == "open":
                assert row["group"] == "sql"
            if row["status"] == "answered":
                assert row["group"] == "answered"
            if row["kind"] in ("codes", "core", "meaning", "timing"):
                assert row["group"] != "sql"
    names = zipfile.ZipFile(io.BytesIO(_pack_zip(browser, invented))).namelist()
    assert {n for n in names if n.startswith("boundary/")} == {f"boundary/{n}" for n in invented["files"]}
    browser.clear()
    assert browser._boundary is None and not (tmp_path / "worker").exists()


def _pack_zip(browser, invented):
    browser._analysis = Analysis((invented["state"] / boundary.CATALOGUE).read_text())
    return browser.pack_zip()


def test_the_pages_entry_point_names_a_state_it_cannot_use(monkeypatch, tmp_path):
    from schemalyser import browser
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser.boundary_begin()
    browser.boundary_put("state", "catalogue.csv", (FIXTURES / "invented-catalogue.csv").read_bytes())
    browser.boundary_put("state", "site-rules.json", b"{ not json")
    browser.boundary_put("requests", "a.sql", b"SELECT 1;")
    assert json.loads(browser.boundary_run()) == {"ok": False, "problem": "bad_rules"}
    browser.clear()


def test_the_pages_entry_point_carries_intent_and_route_when_the_checklist_has_them(invented, monkeypatch, tmp_path):
    """The columns intent and route are optional: read by name, and empty until the checklist gives them."""
    from schemalyser import browser
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    original = target.checklist

    def with_columns(*args, **kwargs):
        rows, traced = original(*args, **kwargs)
        for row in rows:
            if row["kind"] == "relationship":
                row["intent"] = "This join finds the hospital visit that the anaesthetic took place in."
                row["route"] = "The sample queries do not connect these two tables at all."
        return rows, traced

    browser.boundary_begin()
    for kind, folder in (("state", invented["state"]), ("requests", invented["requests"])):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            browser.boundary_put(kind, path.relative_to(folder).as_posix(), path.read_bytes())
    plain = json.loads(browser.boundary_run())
    # The page receives both columns for every row, and an answered relationship has no route to report.
    assert all("intent" in row and "route" in row for t in plain["targets"] for row in t["rows"])
    assert any(row["intent"] for t in plain["targets"] for row in t["rows"] if row["kind"] == "relationship")
    assert all(row["route"] == "" for t in plain["targets"] for row in t["rows"] if row["status"] == "answered")
    monkeypatch.setattr(target, "checklist", with_columns)
    given = json.loads(browser.boundary_run())
    joins = [row for t in given["targets"] for row in t["rows"] if row["kind"] == "relationship"]
    assert joins and all(row["intent"].startswith("This join") and row["route"] for row in joins)
    browser.clear()


# Nothing outside the state folder is read, whether through a step's file name or through a link.

OUTSIDE_WORD = "Quixotical"


def _outside_target(tmp_path):
    """A target query outside the state folder, which names a table that appears nowhere else."""
    outside = tmp_path / "elsewhere"
    outside.mkdir(exist_ok=True)
    (outside / "x.sql").write_text(f"SELECT COUNT(*) AS n FROM omop.person p JOIN {OUTSIDE_WORD}_Table q ON q.id = p.person_id;\n")
    return outside / "x.sql"


@pytest.mark.parametrize("name", ["../../elsewhere/x.sql", "ABSOLUTE"])
def test_a_step_file_outside_the_conversion_folder_is_refused(tmp_path, capsys, name):
    outside = _outside_target(tmp_path)
    state = make_state(tmp_path / "state")
    conversion = state / boundary.CONVERSION
    steps = json.loads((conversion / "conversion.json").read_text())
    steps[0]["file"] = str(outside) if name == "ABSOLUTE" else name
    (conversion / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(ValueError):
        questions._steps(conversion)
    from schemalyser import profile
    steps[0]["layer"] = "anaesthesia"
    (conversion / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(profile.ProfileError):
        profile._anaesthesia_steps(conversion)
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 2 and not files and boundary.WORDING["bad_conversion"] in capsys.readouterr().err


def test_a_link_that_leads_outside_the_state_folder_is_left_out_and_counted(tmp_path):
    outside = _outside_target(tmp_path)
    state = make_state(tmp_path / "state")
    os.symlink(outside, state / boundary.TARGETS / "evil.sql")
    os.symlink(outside, state / boundary.CONVERSION / "evil.sql")
    os.symlink(outside.parent, state / boundary.TARGETS / "more")
    # A link that stays inside the state folder is read as the file it leads to.
    os.symlink(state / boundary.TARGETS / "infant_low_pressure.sql", state / boundary.TARGETS / "same_question.sql")
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 0
    assert not [n for n in files if "evil" in n] and "targets/same_question/checklist.csv" in files
    assert not [n for n, text in files.items() if OUTSIDE_WORD.lower() in text.lower()]
    assert boundary._cell(boundary.WORDING["state_left_out"].format(count="3 files or folders")) in files["summary.md"]


def test_a_step_file_that_is_a_link_outside_the_state_folder_is_not_read(tmp_path, capsys):
    outside = _outside_target(tmp_path)
    state = make_state(tmp_path / "state")
    conversion = state / boundary.CONVERSION
    first = json.loads((conversion / "conversion.json").read_text())[0]["file"]
    (conversion / first).unlink()
    os.symlink(outside, conversion / first)
    code, files = run(state, make_requests(tmp_path / "requests"), tmp_path / "out")
    assert code == 2 and not files and boundary.WORDING["bad_conversion"] in capsys.readouterr().err


def test_a_link_to_a_folder_outside_the_requests_folder_is_left_out_and_counted(tmp_path):
    outside = _outside_target(tmp_path)
    requests = make_requests(tmp_path / "requests")
    os.symlink(outside.parent, requests / "elsewhere")
    kept, left_out = boundary.request_files(requests)
    assert left_out == 1 and len(kept) == 15


@pytest.mark.parametrize("paths", [
    ["x" * 300 + ".sql"],
    ["/".join(["d"] * 3000) + "/a.sql"],
    ["a.sql", "a.sql/b.sql"],
])
def test_the_pages_entry_point_refuses_a_path_it_cannot_write_and_counts_it(monkeypatch, tmp_path, paths):
    from schemalyser import browser
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser.boundary_begin()
    browser.boundary_put("state", "catalogue.csv", (FIXTURES / "invented-catalogue.csv").read_bytes())
    results = [browser.boundary_put("requests", path, b"SELECT 1;") for path in paths]
    assert results[-1] is False and results[:-1] == [True] * (len(paths) - 1)
    result = json.loads(browser.boundary_run())
    assert result["ok"]
    assert boundary._cell(boundary.WORDING["unusable_paths"].format(count="1 file")) in result["summary"]
    browser.clear()


def test_an_unexpected_error_prints_one_fixed_sentence_and_nothing_from_an_input(invented, monkeypatch, tmp_path, capsys):
    planted = "Zanzibarine_Secret_Text"

    def failing(*args, **kwargs):
        raise sqlglot.errors.ParseError(f"Invalid expression near SELECT {planted} FROM somewhere")

    monkeypatch.setattr(boundary, "produce", failing)
    code, _ = run(invented["state"], invented["requests"], tmp_path / "out")
    printed = capsys.readouterr()
    assert code == 3 and code not in (0, 1, 2)
    assert planted not in printed.out + printed.err
    assert boundary.WORDING["unexpected"] in printed.err
