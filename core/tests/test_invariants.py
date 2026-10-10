"""The invariants of docs/contract.md, section 5, that no other test held: one test for each, named after it.

Everything here works on invented material. Invariant 2's sweep reads its deny-list from invariant2-denylist.json
beside this file, which holds only invented stand-ins, and shows on planted text that each entry is caught before it
sweeps the repository. A private copy with real names, kept outside version control, can be named by the environment
variable SCHEMALYSER_PRIVATE_DENYLIST, and the same sweep then reads it too.
"""
import ast
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import types
import zipfile
from pathlib import Path

import pytest

from schemalyser import audit, browser, describe, evidence, feasibility, rolemap, roleshadow
from test_describe import DATE, DICTIONARY, TABLES, tables_result
from test_feasibility import CONFIRMED

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "core" / "schemalyser"
DENYLIST = Path(__file__).with_name("invariant2-denylist.json")


def fresh():
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    return s


def opened(files):
    s = describe.Describe()
    s.version = "test"
    s.restore(files)
    return s


def confirmed():
    """A sitting of the invented world with the three contract parts confirmed, the readings large and the codes chosen."""
    s = fresh()
    s.set_settings("production", 2024, "Australia/Sydney", True)
    s.tables_query()
    s.read_tables(tables_result({"OBS_READING": 400_000_000}))
    for about in CONFIRMED:
        s.confirm(about, "yes", date=DATE)
    s.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff"}, DATE)
    return s


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "hospital-schema.schemalyser.zip"
    path.write_bytes(roleshadow.save_zip(confirmed(), DATE))
    return path


@pytest.fixture(scope="module")
def package(saved, tmp_path_factory):
    out = tmp_path_factory.mktemp("package") / "package"
    manifest = audit.build(saved, rolemap.AUDIT, out, ("2024-01-01", "2024-12-31"), date=DATE)
    return out, manifest


# Invariant 2. What names the vendor's model stays in an environment the hospital approves.

SWEPT = ("core", "site/src", "site/public", "fixtures", "docs", "tools")


def _denylist():
    held = json.loads(DENYLIST.read_text(encoding="utf-8"))
    private = os.environ.get("SCHEMALYSER_PRIVATE_DENYLIST")
    if private:
        extra = json.loads(Path(private).read_text(encoding="utf-8"))
        for key in ("patterns", "names", "allowed"):
            held[key] = held.get(key, []) + extra.get(key, [])
    return held


def sweep(texts, denylist):
    """Every confidential-looking match in {path: text}, as [(path, what it looks like, the text matched)]."""
    found = []
    allowed = set(denylist.get("allowed", []))
    names = [re.compile(re.escape(name), re.I) for name in denylist.get("names", [])]
    for path, text in texts.items():
        for entry in denylist["patterns"]:
            for match in re.finditer(entry["regex"], text):
                if match.group(0) not in allowed:
                    found.append((path, entry["name"], match.group(0)))
        for name in names:
            if name.search(text):
                found.append((path, "a name on the deny-list", name.pattern))
    return found


def _version_controlled():
    """The files under SWEPT that git tracks or would track, which excludes what .gitignore keeps out of version
    control. The deny-list itself is passed over, since it is the one file that holds the stand-ins."""
    try:
        listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *SWEPT],
                                cwd=ROOT, capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("the sweep reads the files that git holds, and this is not a git checkout")
    texts = {}
    for relative in sorted(set(listed.decode("utf-8").split("\0")) - {""}):
        path = ROOT / relative
        if path == DENYLIST or not path.is_file():
            continue
        texts[relative] = path.read_bytes().decode("utf-8", errors="replace")
    return texts


def test_invariant_2_no_version_controlled_file_names_anything_the_deny_list_describes(tmp_path):
    denylist = _denylist()
    # The sweep is shown to catch each entry first: every pattern's invented example and every invented name, planted
    # in text, is found, and an allowed string is not.
    for entry in denylist["patterns"]:
        assert sweep({"planted.txt": f"a line that holds {entry['example']} among other words"}, denylist), entry["name"]
    for name in denylist["names"]:
        assert sweep({"planted.sql": f"SELECT 1 FROM {name}"}, denylist), name
    assert not sweep({"allowed.txt": " ".join(denylist.get("allowed", []))}, denylist)
    texts = _version_controlled()
    assert len(texts) > 100 and "core/schemalyser/workspace.py" in texts and "docs/contract.md" in texts
    assert sweep(texts, denylist) == []


# Invariant 4. Evidence is never inferred: its scope and its staleness.

def _coverage_request(s):
    parts = ["role_patient", "role_anaesthetic"]
    return {"format": describe.REQUEST_FORMAT, "request_id": "qcoverage", "schema_id": s.identity["schema_id"],
            "form": "pathway coverage", "moves": parts, "parts": parts, "period": {"from": "2024-01-01", "to": "2024-12-31"}}


def test_invariant_4_a_figure_measured_over_one_period_is_never_reported_for_another(tmp_path):
    s = fresh()
    roleshadow.save(s, DATE)
    result = ("part\tperiod_from\tperiod_to\tpathways_found\tpathways_mapped\tnote\n"
              "role_patient\t2024-01-01\t2024-12-31\t3\t3\t\n"
              "role_anaesthetic\t2024-01-01\t2024-12-31\t2\t2\t\n")
    found = s.import_evidence(_coverage_request(s), result, "Dr C", date=DATE)
    path = tmp_path / "schema.zip"
    path.write_bytes(describe._zipped(found["files"]))
    schema = feasibility.Schema.load(path)
    measured = feasibility.coverage_evidence(schema, "role_patient", *map(_date, ("2024-01-01", "2024-12-31")))
    assert measured["state"] == feasibility.COVERAGE_ASSESSED and measured["covering"] == [found["entry"]["id"]]
    # The same figure, asked about for 2023, is not reported, and for a period that reaches into 2023 it covers part.
    earlier = feasibility.coverage_evidence(schema, "role_patient", *map(_date, ("2023-01-01", "2023-12-31")))
    assert earlier["state"] == feasibility.COVERAGE_NOT_ASSESSED and earlier["covering"] == []
    across = feasibility.coverage_evidence(schema, "role_patient", *map(_date, ("2023-07-01", "2024-06-30")))
    assert across["state"] == feasibility.COVERAGE_PARTLY
    report = feasibility.assess(schema, rolemap.AUDIT.read_text(encoding="utf-8"), period=(_date("2023-01-01"), _date("2023-12-31")))
    assert {p["part"]: p["state"] for p in report["coverage"]["parts"]}["role_patient"] == feasibility.COVERAGE_NOT_ASSESSED
    assert not report["coverage"]["established"]


def _date(text):
    import datetime as dt
    return dt.date.fromisoformat(text)


def test_invariant_4_a_changed_binding_leaves_the_evidence_that_rested_on_it_stale_with_the_reason_after_a_save():
    s = fresh()
    s.tables_query()
    s.read_tables(tables_result())
    s.confirm("role_anaesthetic.patient_key", "yes", date=DATE)
    roleshadow.test(s, DATE)
    s.confirm("role_anaesthetic.patient_key", "no", "THEATRE_CASE.PERSON_KEY", date=DATE)
    again = opened(s.folder_files(DATE))
    stale = {(e["subject"], e["dimension"]): e["reasons"] for e in again.stale_evidence()}
    assert stale[("role_anaesthetic.patient_key", "tested")] == ["the binding changed"]
    assert not any(subject.startswith("role_reading") for subject, _ in stale)
    assert again.readiness()["parts"]["role_anaesthetic"]["runs"] is None


def test_invariant_4_a_changed_contract_part_leaves_the_evidence_of_that_part_alone_stale_with_the_reason(monkeypatch):
    s = fresh()
    s.confirm("role_reading.value", "yes", date=DATE)
    s.confirm("role_patient.birth_date", "yes", date=DATE)
    files = roleshadow.save(s, DATE)
    changed = dict(rolemap.part_hashes(), role_reading="1" * 16)
    monkeypatch.setattr(rolemap, "part_hashes", lambda model=None: changed)
    again = opened(files)
    assert again.restored["contract_changed"] == ["role_reading"]
    stale = {(e["subject"], e["dimension"]): e["reasons"] for e in again.stale_evidence()}
    assert stale[("role_reading.value", "confirmed")] == ["the contract changed"]
    assert ("role_patient.birth_date", "confirmed") not in stale


# Invariant 5. Three reports stay apart, and "complete" is never said for less than clinical validation.

REPORTS = {"feasibility.json": ("verdict", "verdict_text"), "expected-output.json": ("planted_match",),
           "safety-report.json": ("execution_class", "class_says", "outcome")}


def _keys(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_invariant_5_the_three_reports_are_three_files_and_none_carries_another_s_verdict(package):
    out, manifest = package
    held = {name: json.loads((out / name).read_text(encoding="utf-8")) for name in REPORTS}
    assert len({(out / name).read_bytes() for name in REPORTS}) == 3
    for name, report in held.items():
        own = set(REPORTS[name])
        assert own <= set(report), (name, own - set(report))
        others = {k for other, keys in REPORTS.items() if other != name for k in keys} - own
        assert not (_keys(report) & others), (name, _keys(report) & others)
    # The manifest records each verdict in a field of its own, beside the others, and combines none of them.
    assert manifest["feasibility_verdict"] == held["feasibility.json"]["verdict"]
    assert manifest["execution_class"] == held["safety-report.json"]["execution_class"]
    assert manifest["planted_match"] == held["expected-output.json"]["planted_match"]
    assert not {"verdict", "overall", "ready", "complete", "status"} & set(manifest)
    # Each report's wording speaks of its own question alone.
    assert not re.search(r"class [A-D]\b|planted", held["feasibility.json"]["verdict_text"])
    assert not re.search(r"feasib|answerable|planted", held["safety-report.json"]["class_says"], re.I)


# "complete data" is a provenance, a count over the whole of the tables it reads (evidence.COMPLETE), and says nothing of
# how far the schema has been checked, so it is the one use of the word that a report may make.
COMPLETE = re.compile(r"\bcomplete\b(?! data\b)", re.I)


def test_invariant_5_no_report_says_complete_for_a_schema_short_of_clinical_validation(package, saved):
    out, _ = package
    assert evidence.COMPLETE == "complete data"
    schema = feasibility.Schema.load(saved)
    assert schema.readiness["parts"]["role_reading"]["reached"] != describe.VALIDATED
    said = {path.name: path.read_text(encoding="utf-8") for path in out.iterdir() if path.suffix in (".md", ".json", ".sql")}
    with zipfile.ZipFile(saved) as archive:
        said.update({f"schema/{n}": archive.read(n).decode("utf-8", "replace") for n in archive.namelist()
                     if n.endswith((".md", ".json")) and not n.startswith("dictionary/")})
    said["feasibility report"] = feasibility.markdown(feasibility.assess(schema, rolemap.AUDIT.read_text(encoding="utf-8")))
    assert {name for name, text in said.items() if COMPLETE.search(text)} == set()


# Invariant 6. A change to the resolved mappings, and not only to the script, voids the evidence that referred to it.

def test_invariant_6_a_changed_mapping_voids_the_package_s_approval_and_review_while_its_script_is_unchanged(package, saved, tmp_path):
    import shutil
    out = tmp_path / "package"
    shutil.copytree(package[0], out)
    audit.approve(out, "the database analyst", date=DATE)
    assert not audit.status(out, saved)["voided"]
    script = (out / "query.sql").read_bytes()
    # The hospital schema's translation of the kinds changes: the cuff's code is now another one.
    later = opened(describe._read_saved(saved))
    later.choose_codes("role_reading.kind", {"52": "map_arterial", "53": "map_cuff"}, DATE)
    remapped = tmp_path / "remapped.zip"
    remapped.write_bytes(roleshadow.save_zip(later, DATE))
    found = audit.status(out, remapped)
    assert found["voided"] and found["changed"] == ["the hospital schema"]
    assert found["approval"] == "not approved" and found["plan_review"] == "voided"
    assert (out / "query.sql").read_bytes() == script
    # The mapping resolves differently, which is why the evidence built on the earlier one no longer stands.
    rebuilt = audit.build(remapped, rolemap.AUDIT, tmp_path / "rebuilt", ("2024-01-01", "2024-12-31"), date=DATE)
    assert rebuilt["sql_sha256"] != audit.sha256(script.decode("utf-8"))
    with pytest.raises(audit.AuditError, match="has changed since it was built"):
        audit.approve(out, "the database analyst", schema_path=remapped)


# Invariant 7. Schemalyser never executes against a hospital database, and no surface connects to one.

DRIVERS = {"pyodbc", "pymssql", "sqlalchemy", "psycopg", "psycopg2", "psycopg2cffi", "asyncpg", "pg8000", "pymysql",
           "mysql", "mysqldb", "mariadb", "cx_oracle", "oracledb", "ibm_db", "teradatasql", "snowflake", "jaydebeapi",
           "jpype", "turbodbc", "pytds", "ctds", "aioodbc", "databricks", "pyhive", "impala", "sqlanydb", "pyodbc_ext"}


def driver_imports(source):
    """The database drivers that a module's text imports, by an import statement, importlib.import_module or
    __import__, as a set of their top-level names in lower case."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if callee in ("import_module", "__import__"):
                names = [node.args[0].value]
        found |= {n.split(".")[0].lower() for n in names} & DRIVERS
    return found


def test_invariant_7_no_module_of_the_core_imports_a_database_driver():
    planted = "import os\nfrom sqlalchemy import create_engine\nimportlib.import_module('pyodbc')\n"
    assert driver_imports(planted) == {"sqlalchemy", "pyodbc"}
    modules = sorted(PACKAGE.rglob("*.py"))
    assert len(modules) > 30
    found = {p.relative_to(PACKAGE).as_posix(): driver_imports(p.read_text(encoding="utf-8")) for p in modules}
    assert {path: names for path, names in found.items() if names} == {}


def test_invariant_7_the_workbench_listens_only_on_the_trusted_hosts(tmp_path, monkeypatch):
    pytest.importorskip("starlette")
    from schemalyser.workbench import __main__ as workbench_main
    from schemalyser.workbench import app as workbench_app
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    served = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: served.append(kw)))
    monkeypatch.setattr(workbench_main, "CONTAINER_MARKER", tmp_path / ".dockerenv")
    assert workbench_main.main(["--project", str(tmp_path / "project"), "--describe", str(tmp_path / "no-page")]) == 0
    assert [kw["host"] for kw in served] == ["127.0.0.1"] and served[0]["proxy_headers"] is False
    assert workbench_main.main(["--project", str(tmp_path / "project"), "--host", "0.0.0.0"]) == 2 and len(served) == 1
    app = workbench_app.create_app(tmp_path / "project", describe_folder=tmp_path / "no-page")
    hosts = [m.kwargs["allowed_hosts"] for m in app.user_middleware if m.cls is TrustedHostMiddleware]
    assert hosts == [["127.0.0.1", "localhost"]] and workbench_app.HOSTS == ["127.0.0.1", "localhost"]


# A file that Python reads at start-up in each command the workbench starts. It records each attempt to reach another
# computer and refuses it, and writes a mark that it was loaded, so that the test knows the guard was in place.
GUARD = '''
import os, socket
_log = os.environ["SCHEMALYSER_TEST_SOCKET_LOG"]
with open(_log + ".loaded", "a") as f:
    f.write(str(os.getpid()) + "\\n")
def _refuse(where):
    with open(_log, "a") as f:
        f.write(repr(where) + "\\n")
    raise OSError("refused by the test of invariant 7")
_connect = socket.socket.connect
def connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _refuse(address)
    return _connect(self, address)
def connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _refuse(address)
    return _connect(self, address)
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.create_connection = lambda address, *a, **k: _refuse(address)
_getaddrinfo = socket.getaddrinfo
def getaddrinfo(host, *a, **k):
    if host not in (None, "localhost", "127.0.0.1", "::1"):
        _refuse(host)
    return _getaddrinfo(host, *a, **k)
socket.getaddrinfo = getaddrinfo
'''


@pytest.mark.slow
def test_invariant_7_the_workbench_reaches_no_other_computer_while_it_builds_a_package_and_runs_the_testbed(saved, tmp_path, monkeypatch):
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from schemalyser.workbench import jobs
    from schemalyser.workbench.app import create_app
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    log = tmp_path / "connections.log"
    monkeypatch.setenv("SCHEMALYSER_TEST_SOCKET_LOG", str(log))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in (str(guard), os.environ.get("PYTHONPATH")) if p))
    # In this process, too, any connection to another computer is recorded and refused.
    attempts = []

    def refuse(self, address, *rest):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(address)
            raise OSError("refused by the test of invariant 7")
        return real(self, address, *rest)
    def create_connection(address, *rest, **more):
        attempts.append(address)
        raise OSError("refused by the test of invariant 7")
    real = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    project = tmp_path / "project"
    (project / "schemas").mkdir(parents=True)
    (project / "schemas" / saved.name).write_bytes(saved.read_bytes())
    held = tempfile.tempdir
    try:
        with TestClient(create_app(project, describe_folder=tmp_path / "no-page"), base_url="http://127.0.0.1") as client:
            question = client.post("/questions", data={"title": "Among neonates, was the pressure low?",
                                                       "sql": rolemap.AUDIT.read_text(encoding="utf-8")}, follow_redirects=False)
            name = question.headers["location"].rsplit("/", 1)[1]
            built = client.post("/audits", data={"schema": saved.name, "question": name}, follow_redirects=False)
            ran = client.post("/runs", data={"world": "fixtures", "rows": "30", "profile": "fast", "engine": "duckdb"},
                              follow_redirects=False)
            assert built.status_code == ran.status_code == 303
            jobs.wait_all(timeout=600)
            assert "Class B: bounded validation." in client.get(built.headers["location"]).text
            assert "The fast profile passed" in client.get(ran.headers["location"]).text
    finally:
        tempfile.tempdir = held
    assert len(Path(f"{log}.loaded").read_text().split()) >= 2
    assert not log.exists() or log.read_text() == ""
    assert attempts == []


# Invariant 9. A surface owns no logic: every operation of the page's bridge is carried by a command of the command line,
# over the same saved file. test_describe_command.py compares the hospital schema that the page saves with the one that
# the commands save from the same calls, one command at a time.

# The operations that read the sitting and change nothing that the saved hospital schema holds (site/e2e/describe.spec.ts
# names the same set). Each has a command, and the test below shows that none changes anything.
READS = {"model", "check", "compare", "dictionary_query", "names", "columns", "joins", "schema_files"}


def commands():
    """The commands of python -m schemalyser.describe, read from its parser."""
    from schemalyser.describe import __main__ as describe_main
    found = next(a for a in describe_main.parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    return set(found.choices)


def bridge_operations():
    return {name.removeprefix("describe_") for name in dir(browser) if name.startswith("describe_") and callable(getattr(browser, name))}


def test_invariant_9_every_operation_of_the_page_s_bridge_has_a_command_on_the_command_line():
    from schemalyser.describe import __main__ as describe_main
    bridge, carried, subcommands = bridge_operations(), describe_main.BRIDGE, commands()
    assert len(bridge) > 30
    # Every operation of the bridge names a command, and nothing is named for an operation the bridge does not have.
    assert sorted(bridge - set(carried)) == [] and sorted(set(carried) - bridge) == []
    assert sorted(set(carried.values()) - subcommands) == []
    # Each command is one that the command line implements, and the walk is a convenience beside them.
    assert all(callable(getattr(describe_main, "cmd_" + name.replace("-", "_"), None)) for name in subcommands)
    assert "walk" in subcommands and "walk" not in carried.values()
    # A read and an operation that changes the schema are never carried by the same command.
    changing = {carried[name] for name in bridge - READS - {"schema_open"}}
    assert not changing & {carried[name] for name in READS}, "an operation that changes the schema is not a read"


def test_invariant_9_each_operation_left_to_the_page_alone_changes_nothing_that_the_saved_schema_holds():
    s = fresh()
    s.tables_query()
    s.read_tables(tables_result())
    s.confirm("role_patient.birth_date", "yes", date=DATE)
    browser._describe = s
    try:
        before = s.folder_files(DATE)
        replies = {
            "model": browser.describe_model(),
            "check": browser.describe_check(),
            "compare": browser.describe_compare(json.dumps({"name": "tables-and-columns", "text": tables_result()})),
            "dictionary_query": browser.describe_dictionary_query(""),
            "names": browser.describe_names(),
            "columns": browser.describe_columns(json.dumps({"table": "PERSON_MASTER"})),
            "joins": browser.describe_joins(json.dumps({"table": "ANAES_RECORD"})),
            "schema_files": browser.describe_schema_files(),
        }
        assert set(replies) == READS
        for name, reply in replies.items():
            held = json.loads(reply)
            assert held is not None and (not isinstance(held, dict) or held.get("ok", True)), (name, held)
        after = s.folder_files(DATE)
        assert sorted(after) == sorted(before)
        # The time at which the files are written is the one thing that differs between two writes a second apart.
        steady = {n: re.sub(rb'"saved": "[^"]*"', b'"saved": "TIME"', d) for n, d in before.items()}
        assert {n for n in after if re.sub(rb'"saved": "[^"]*"', b'"saved": "TIME"', after[n]) != steady[n]} == set()
    finally:
        browser._describe = None


# Invariant 10. Source normalisation is private and clinical logic is public: a binding holds no decision of a question.

def _with_decision(files, form):
    held = json.loads(files["map/map.json"])
    if form == "window":
        binding = held["roles"]["role_reading"]["columns"]["anaesthetic_key"]["binding"]
        binding["window"] = {"start": "START_TS", "stop": "STOP_TS"}
    elif form == "clinical filter":
        # The drugs of one class, kept by the column that holds the drug's own code.
        drug = held["roles"]["role_drug"]["columns"]["drug"]["binding"]
        held["roles"]["role_drug"]["rows"]["binding"]["filter"] = [
            {"table": drug["table"], "column": drug["column"], "path": drug["path"], "values": ["an invented class"]}]
    else:
        # A filter on the code that says what kind of record a row is interprets the vendor's storage, and is kept.
        held["roles"]["role_drug"]["rows"]["binding"]["filter"] = [
            {"table": "DRUG_GIVEN", "column": "ACTION_CAT", "path": [], "values": ["1"]}]
    return dict(files, **{"map/map.json": json.dumps(held).encode("utf-8")})


RULES = {"window": "never attributes a row to an anaesthetic by a time window",
         "clinical filter": "a filter by clinical meaning"}


@pytest.mark.parametrize("form", ["window", "clinical filter"])
def test_invariant_10_a_saved_schema_whose_binding_holds_a_clinical_decision_is_refused_on_opening_with_the_rule_named(form, tmp_path):
    files = _with_decision(roleshadow.save(fresh(), DATE), form)
    s = describe.Describe()
    found = s.restore(files)
    assert not found["map"] and s.data is None
    assert RULES[form] in found["refused"] and RULES[form] in found["problem"]
    # Nothing of the refused file stays in the sitting: no journal, no dictionary, and no version to carry on from.
    assert len(s.log) == 0 and s.dictionary is None and s.identity["schema_id"] is None
    with pytest.raises(feasibility.FeasibilityError, match=re.escape(RULES[form])):
        feasibility.Schema(files)
    # The same file without its dictionary is held to the same rule.
    bare = {n: d for n, d in files.items() if not n.startswith("dictionary/")}
    with pytest.raises(feasibility.FeasibilityError, match=re.escape(RULES[form])):
        feasibility.Schema(bare)
    path = tmp_path / "schema.zip"
    path.write_bytes(describe._zipped(files))
    with pytest.raises(feasibility.FeasibilityError, match=re.escape(RULES[form])):
        audit.build(path, rolemap.AUDIT, tmp_path / "package", date=DATE)


def test_invariant_10_a_filter_that_interprets_the_vendor_s_storage_is_kept():
    files = _with_decision(roleshadow.save(fresh(), DATE), "storage filter")
    found = describe.Describe().restore(files)
    assert found["map"] and found["refused"] == ""
    assert feasibility.Schema(files).map["roles"]["role_drug"]["rows"]["binding"]["filter"][0]["column"] == "ACTION_CAT"
