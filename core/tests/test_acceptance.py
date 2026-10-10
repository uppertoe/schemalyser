"""The two acceptance tests of section 7 of docs/contract.md, each as one test that walks its numbered steps in order on
invented material and asserts each step's outcome, so that the test can be read beside the contract line by line.

The query lifecycle writes a second, small question beside the neonatal audit: the hypoxaemia burden of the catalogue,
as minutes with a saturation below 90 in bands. Its planted rows are a development fixture in
fixtures/questions/saturation_below_90/rows.json, and its expected answers, written by hand from their description and
never from a run, are held out in fixtures/held-out/questions/saturation_below_90/expected.json.

Step 6 runs the exact text of query.sql on DuckDB's translated form, on a shadow shaped as the hospital schema names the
tables. Where SCHEMALYSER_MSSQL_PASSWORD_FILE names the SQL Server harness's password file, the same text also runs
byte for byte on the harness's SQL Server container, loaded with the same shadow; without it, the SQL Server stage is
recorded as not run, as the testbed's fast profile records it, and the test passes on DuckDB alone.
"""
import copy
import csv
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
import sqlglot

from schemalyser import (audit, compiler, convert, corrections, describe, feasibility, policy, propose, rolemap,
                         roleshadow, workspace)
from schemalyser.translate import to_duckdb
from test_describe import DICTIONARY, TABLES, tables_result
from test_feasibility import CONFIRMED

ROOT = workspace.ROOT
FIXTURES = ROOT / "fixtures"
HELD_OUT = FIXTURES / "held-out"
PLANS = Path(__file__).parent / "plans"
DATE = "2026-10-10"
NAME = "saturation_below_90"
READINGS = 400_000_000
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

QUESTION = """-- capability: hypoxaemia_burden
-- Among children who had an anaesthetic, how many minutes of each anaesthetic were spent with a saturation below 90
-- per cent?
-- The question is the hypoxaemia burden of the catalogue with one threshold, 90, for every age, over the whole of each
-- anaesthetic, written out over the three role views so that it can be read as it stands.
-- A test patient is left out, as is an anaesthetic with no start or with a stop before its start.
-- The question reads the saturation by pulse oximetry (spo2), accepted and with a value, taken from the start to the
-- stop. Where two readings have the same time, it keeps the lower.
-- Each kept reading stands until the next kept reading of the same anaesthetic, or until the stop for the last, and
-- for no longer than five minutes. The last reading of an anaesthetic with no recorded stop stands for no time.
-- The minutes below 90 are the sum of the time for which the readings below 90 stand. An anaesthetic with no
-- saturation recorded has no minutes, which is unknown and not zero, and it is counted in a band of its own.
-- The question gives one row for each band, in order: no saturation recorded, none, under 5 minutes, 5 to 14 minutes
-- and 15 minutes or more, with the number of anaesthetics and the number of children in each.
WITH anaesthetic AS (
    SELECT a.anaesthetic_key,
           a.patient_key,
           a.start_time,
           a.stop_time
    FROM   role_anaesthetic a
           JOIN role_patient p ON p.patient_key = a.patient_key
    WHERE  p.is_test = 0
      AND  a.start_time IS NOT NULL
      AND  (a.stop_time IS NULL OR a.stop_time >= a.start_time)
),
reading AS (
    SELECT n.anaesthetic_key,
           n.stop_time,
           r.reading_time,
           r.value,
           ROW_NUMBER() OVER (PARTITION BY n.anaesthetic_key, r.reading_time ORDER BY r.value) AS kept_at_time
    FROM   anaesthetic n
           JOIN role_reading r
             ON r.anaesthetic_key = n.anaesthetic_key
            AND r.kind = 'spo2'
            AND r.accepted = 1
            AND r.value IS NOT NULL
            AND r.reading_time >= n.start_time
            AND (n.stop_time IS NULL OR r.reading_time <= n.stop_time)
),
stood AS (
    SELECT anaesthetic_key,
           value,
           reading_time,
           COALESCE(LEAD(reading_time) OVER (PARTITION BY anaesthetic_key ORDER BY reading_time), stop_time) AS until_time
    FROM   reading
    WHERE  kept_at_time = 1
),
low AS (
    SELECT anaesthetic_key,
           SUM(CASE WHEN value < 90 AND until_time IS NOT NULL
                    THEN CASE WHEN DATEDIFF(second, reading_time, until_time) > 300 THEN 300
                              ELSE DATEDIFF(second, reading_time, until_time) END
                    ELSE 0 END) / 60.0 AS minutes_below_90
    FROM   stood
    GROUP  BY anaesthetic_key
),
burden AS (
    SELECT n.anaesthetic_key,
           n.patient_key,
           l.minutes_below_90,
           CASE WHEN l.anaesthetic_key IS NULL THEN 0
                WHEN l.minutes_below_90 = 0 THEN 1
                WHEN l.minutes_below_90 < 5 THEN 2
                WHEN l.minutes_below_90 < 15 THEN 3
                ELSE 4 END AS band
    FROM   anaesthetic n
           LEFT JOIN low l ON l.anaesthetic_key = n.anaesthetic_key
),
band AS (
    SELECT 0 AS band, 'no saturation recorded' AS minutes_below_90
    UNION ALL SELECT 1, 'none'
    UNION ALL SELECT 2, 'under 5 minutes'
    UNION ALL SELECT 3, '5 to 14 minutes'
    UNION ALL SELECT 4, '15 minutes or more'
)
SELECT b.minutes_below_90,
       COUNT(x.band) AS anaesthetics,
       COUNT(DISTINCT x.patient_key) AS children
FROM   band b
       LEFT JOIN burden x ON x.band = b.band
GROUP  BY b.band, b.minutes_below_90
ORDER  BY b.band
"""
TITLE = "Hypoxaemia burden in bands of minutes below a saturation of 90\n"
NOTE = ("The question measures, for each anaesthetic of a child, how many minutes the saturation spent below 90 per cent, "
        "and reports the number of anaesthetics and of children in each band of minutes, and no row of any patient.\n")


def _comparable(rows):
    return sorted(([convert._comparable(v) for v in row] for row in rows), key=lambda row: [(v is None, str(v)) for v in row])


def _private_schema(path, extra_confirmed=()):
    """A saved hospital schema made from the invented dictionary as the page makes one, with the readings large and the
    saturation's observation type translated, as the hospital's own project would hold it."""
    sitting = describe.Describe()
    sitting.version = "test"
    sitting.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    sitting.propose(date=DATE)
    sitting.set_settings("production", 2024, "Australia/Sydney", True)
    sitting.tables_query()
    sitting.read_tables(tables_result({"OBS_READING": READINGS}))
    for about in list(CONFIRMED) + list(extra_confirmed):
        sitting.confirm(about, "yes", date=DATE)
    sitting.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff", "10": "spo2"}, DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(roleshadow.save_zip(sitting, DATE))
    return path


def _per_anaesthetic(sql):
    """The question with its final SELECT replaced by one that reads each anaesthetic's minutes from its own step."""
    tree = compiler.check_audit(sql)
    final = sqlglot.parse_one("SELECT x.anaesthetic_key, x.minutes_below_90 FROM burden x", dialect="tsql")
    final.set("with_" if "with_" in final.arg_types else "with", (tree.args.get("with_") or tree.args.get("with")).copy())
    return final.sql(dialect="tsql")


def _hospital_shadow(schema, planted):
    """A DuckDB database shaped as the hospital schema names the tables, holding the planted role rows placed through the
    schema's bindings. Returns (the connexion, its date columns)."""
    sitting = schema.sitting
    data = copy.deepcopy(sitting.data)
    vocabularies = {name: sitting._vocabulary_codes(name) for name in data["roles"]}
    views = {}
    for name in data["roles"]:
        role = dict(data["roles"][name])
        role["_date"] = ""
        views[name] = propose.view_sql(name, role, data["kinds"], sitting.model, vocabularies[name])
    held = copy.copy(sitting)
    held.data = data
    shadow = roleshadow.Shadow(held, views, data["kinds"], vocabularies)
    shadow.register()
    contract = {v["name"]: v for v in sitting.model["views"]}
    for name in ("role_patient", "role_anaesthetic", "role_reading"):
        for row in planted[name]:
            shadow.place(name, roleshadow._typed(contract[name], row))
    shadow.pair_encounters()
    return shadow.database()


def _run_script_on_duckdb(con, date_columns, text):
    """Runs every statement of query.sql in order on DuckDB's translated form, with #cohort as a table, and returns the
    statements that ran and the rows of the last, which is part 2."""
    statements, _ = policy.split(text)
    ran, rows = [], None
    for statement in statements:
        head = statement.lstrip().upper()
        if head.startswith(("SET ", "IF OBJECT_ID", "ALTER TABLE #COHORT", "DROP TABLE #COHORT")):
            continue
        if "INTO   #cohort" in statement:
            # DuckDB will not mix a text key with the 0 by which SQL Server makes the key fit for a primary key.
            filling = statement.replace("INTO   #cohort\n", "").replace("ISNULL(k.anaesthetic_key, 0)", "k.anaesthetic_key")
            con.execute("CREATE OR REPLACE TEMP TABLE cohort_rehearsal AS " + to_duckdb(filling, date_columns)[0])
        else:
            rows = con.execute(to_duckdb(statement.replace("#cohort", "cohort_rehearsal"), date_columns)[0]).fetchall()
        ran.append(statement)
    return ran, rows


def _run_script_on_sql_server(con, tables, text):
    """Loads the shadow's tables into a database of their own on the harness's SQL Server and runs query.sql there as
    it is. Returns part 2's rows as text, or None where the harness is not available."""
    password = os.environ.get("SCHEMALYSER_MSSQL_PASSWORD_FILE")
    if not password:
        return None
    spec = importlib.util.spec_from_file_location("sqlserver_harness", ROOT / "tools" / "sqlserver" / "harness.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    harness.SOURCE_DATABASE = "schemalyser_acceptance"
    server = harness.Server(harness.CONTAINER, Path(password).read_text(encoding="utf-8").strip())
    try:
        harness.load_source(server, con, (FIXTURES / "invented-catalogue.csv").read_text(encoding="utf-8"), tables)
        out = server.run(text, harness.SOURCE_DATABASE, separator="|", trim=True)
    finally:
        server.clean()
    lines = [line.split("|") for line in out.splitlines() if line.count("|") == 2]
    return [[None if value == "NULL" else value for value in line] for line in lines[-5:]]


def _seek_plan(part2):
    return (PLANS / "seek-from-cohort.sqlplan").read_text(encoding="utf-8").replace(
        "PART_2", escape(part2, {'"': "&quot;", "\n": "&#10;"}))


def _in_workspace(work, *args, code=None):
    env = dict(os.environ, PYTHONPATH="core", PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-c", code] if code is not None else [sys.executable, *args]
    return subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=300)


def _catalogue_names():
    names = set()
    with (FIXTURES / "invented-catalogue.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            names |= {row["TABLE_NAME"], row["COLUMN_NAME"]}
    return {n for n in names if re.fullmatch(r"[A-Z][A-Z0-9]*_[A-Z0-9_]+", n or "")}


@pytest.mark.slow
def test_the_query_lifecycle_runs_end_to_end_on_invented_material(tmp_path):
    held_out = json.loads((HELD_OUT / "questions" / NAME / "expected.json").read_text(encoding="utf-8"))
    planted = json.loads((FIXTURES / "questions" / NAME / "rows.json").read_text(encoding="utf-8"))["roles"]

    # 1. On a computer with no access to hospital resources, a coding agent is given the exported public workspace and
    # nothing else.
    work = tmp_path / "workspace"
    manifest = workspace.export(work, "public", date=DATE)
    held = [f["path"] for f in manifest["files"] if f["path"].startswith(("fixtures/held-out", "reference", "notes"))]
    assert held == [] and not (work / "fixtures" / "held-out").exists() and not (work / "fixtures" / "questions").exists()

    # 2. It writes a new clinical question over the roles and the catalogue, checks it with the workspace's own check,
    # and produces a question package.
    package = work / "queries" / NAME
    package.mkdir(parents=True)
    (package / "question.sql").write_text(QUESTION, encoding="utf-8")
    (package / "title.txt").write_text(TITLE, encoding="utf-8")
    (package / "note.md").write_text(NOTE, encoding="utf-8")
    checked = _in_workspace(work, "-m", "schemalyser.workspace", "check", f"queries/{NAME}")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "The import would return the status accepted." in checked.stdout
    own = workspace.check_folder(package)
    assert own["status"] == "accepted" and own["failed"] == [] and own["form"] == []
    assert feasibility.named_capabilities(QUESTION) == ["hypoxaemia_burden"]
    assert sorted(p.name for p in package.iterdir()) == ["note.md", "question.sql", "title.txt"]

    # 3. The package is transferred into a private Schemalyser project built on the invented hospital schema and
    # imported as untrusted input.
    project = tmp_path / "hospital-project"
    schema_path = _private_schema(project / "schemas" / "hospital-schema.schemalyser.zip")
    transferred = tmp_path / "transfer" / NAME
    transferred.parent.mkdir()
    shutil.copytree(package, transferred)
    result, imported, record = workspace.import_question(transferred, project, date=DATE)
    assert record is not None and imported["status"] == own["status"]
    built = record / "package"
    validation = json.loads((record / "import-validation.json").read_text(encoding="utf-8"))
    assert validation["build_failure"] is None and validation["package"] == "package", validation["build_failure"]

    # 4. The feasibility report names the capabilities the question requires and the evidence state of each against the
    # project's hospital schema.
    found = json.loads((built / "feasibility.json").read_text(encoding="utf-8"))
    (named,) = found["capabilities"]
    assert (named["name"], named["version"]) == ("hypoxaemia_burden", 1)
    states = {row["id"]: row["state"] for row in found["states"]}
    assert named["requirements"] and set(named["requirements"]) <= set(states)
    # The schema confirms the saturation's code and the columns the question reads, but leaves the flag of an accepted
    # reading as proposed, so the capability, which counts accepted readings, stands no higher than proposed.
    assert states["role_reading.kind = spo2"] == states["role_reading.value"] == feasibility.CONFIRMED
    assert states["role_reading.accepted"] == feasibility.PROPOSED
    assert named["state"] == feasibility.PROPOSED and "role_reading.accepted" in named["requirements"]
    assert "hypoxaemia_burden, version 1" in (built / "feasibility.md").read_text(encoding="utf-8")

    # 5. The question passes independently specified planted scenarios on made-up rows, held out from the workspace the
    # agent was given.
    con = roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=planted)
    run = roleshadow.duckdb_runner(con)
    _, answer = run(QUESTION)
    assert [list(r) for r in answer] == held_out["answer"]["rows"], held_out["answer"]["says"]
    _, minutes = run(_per_anaesthetic(QUESTION))
    assert _comparable(minutes) == _comparable(held_out["per_anaesthetic"]["rows"]), held_out["says"]
    con.close()

    # 6. The compiler produces the two-part script for a private synthetic SQL Server, and the harness executes that
    # exact artefact: here on DuckDB's translated form, and on SQL Server where the harness is available.
    text = (built / "query.sql").read_text(encoding="utf-8")
    part1, part2 = text.split("-- Part 2 ", 1)
    assert "SELECT TOP (5000)" in part1 and "INTO   #cohort" in part1 and "OBS_READING" not in part1
    assert audit.part2_text(text).startswith("WITH role_reading AS (")
    schema = feasibility.Schema.load(schema_path)
    shadow, date_columns = _hospital_shadow(schema, planted)
    ran, released = _run_script_on_duckdb(shadow, date_columns, text)
    assert len(ran) == len([s for s in policy.split(text)[0] if s.lstrip().upper().startswith(("WITH", "SELECT"))])
    expected_2024 = held_out["answer_in_2024_as_released"]["rows"]
    assert [list(r) for r in released] == expected_2024, held_out["answer_in_2024_as_released"]["says"]
    tables = json.loads((built / "manifest.json").read_text(encoding="utf-8"))["permissions"]["select_on"]
    on_sql_server = _run_script_on_sql_server(shadow, tables, text)
    if on_sql_server is not None:
        assert [[r[0]] + [None if v is None else int(v) for v in r[1:]] for r in on_sql_server] == expected_2024
    shadow.close()

    # 7. The static policy, the execution class and the plan review are produced.
    safety = json.loads((built / "safety-report.json").read_text(encoding="utf-8"))
    assert safety["outcome"] == "passed" and safety["execution_class"] == "B", [r for r in safety["rules"] if not r["passed"]]
    assert safety["sql_sha256"] == audit.sha256(text) and safety["large_tables"] == ["OBS_READING"]
    assert next(r for r in safety["rules"] if r["id"] == "series")["passed"]
    plan = tmp_path / "plan.sqlplan"
    plan.write_text(_seek_plan(audit.part2_text(text)), encoding="utf-8")
    review = audit.review_plan(built, plan, date=DATE)
    assert review["state"] == "accepted", review["reasons"]
    assert review["plan_sha256"] == audit.sha256(plan.read_bytes()) and review["sql_sha256"] == audit.sha256(text)

    # 8. The execution package is written, and nothing about the hospital returns to the agent beyond the fixed status,
    # which is the status that the workspace's own check already gave.
    for name in audit.HASHED + (audit.PLAN_FILE, "manifest.json", "README.md", "plan-review.json"):
        assert (built / name).is_file(), name
    assert result == {"query": NAME, "result": "accepted", "rules_failed": [], "says": workspace.WORDING["accepted"]}
    assert result["result"] == own["status"]
    returned = (transferred / workspace.RESULT).read_text(encoding="utf-8")
    assert json.loads(returned) == result
    assert sorted(p.name for p in transferred.iterdir()) == ["import-result.json", "note.md", "question.sql", "title.txt"]
    assert not [n for n in _catalogue_names() if n in returned] and str(project) not in returned

    # 9. A database analyst inspects the package and records approval or refusal in its manifest; a change to an input
    # then voids it.
    approval = audit.approve(built, "the database analyst", note="Run on the reporting replica.", date=DATE, schema_path=schema_path)
    recorded = json.loads((built / "manifest.json").read_text(encoding="utf-8"))["approval"]
    assert approval["state"] == recorded["state"] == "approved" and recorded["by"] == "the database analyst"
    assert audit.status(built, schema_path)["approval"] == "approved"
    newer = _private_schema(tmp_path / "newer" / "hospital-schema.schemalyser.zip", ["role_patient.death_date"])
    standing = audit.status(built, newer)
    assert standing["voided"] and "the hospital schema" in standing["changed"] and standing["approval"] == "not approved"
    assert json.loads((built / "manifest.json").read_text(encoding="utf-8"))["approval"]["state"] == "not approved"


BOUNDARY = "infusion_boundary"
PUBLIC_HALF = """
import json
from pathlib import Path
from schemalyser import convert, rolemap, roleshadow
from schemalyser.translate import to_duckdb

folder = Path("fixtures/conversion")
(scenario,) = [s for s in convert.read_role_scenarios(folder) if s["name"] == "infusion_boundary"]
(statement,) = to_duckdb((folder / "drug_exposure_infusion_roles.sql").read_text(encoding="utf-8"))
con = roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=scenario["roles"])
con.execute("CREATE SCHEMA omop")
for table, fields in convert.cdm_fields().items():
    con.execute(f'CREATE TABLE omop."{table}" (' + ", ".join(f'"{n}" {k}' for n, _, k in fields) + ")")
for table, rows in scenario["omop"].items():
    for row in rows:
        con.execute(f'INSERT INTO omop."{table}" (' + ", ".join(f'"{n}"' for n in row) + ") VALUES ("
                    + ", ".join("?" for _ in row) + ")", list(row.values()))
cursor = con.execute(statement)
print(json.dumps({"expected": scenario["expected"], "columns": [d[0].lower() for d in cursor.description],
                  "rows": cursor.fetchall()}, default=str))
"""


def test_the_infusion_boundary_holds_from_the_invented_source_and_from_the_public_workspace_alone(tmp_path):
    held = json.loads((HELD_OUT / "conversion" / "scenarios" / BOUNDARY / "expected.json").read_text(encoding="utf-8"))
    role_held = json.loads((HELD_OUT / "conversion" / "role_scenarios" / BOUNDARY / "expected.json").read_text(encoding="utf-8"))

    # The invented world holds an intraoperative infusion as the source records it, and the private side of the
    # invented hospital normalises those records into the drug part's events with their source kinds, through the map.
    converted, report = convert.run(make_checks.WORLD, FIXTURES / "conversion", rows=40, scenarios=[BOUNDARY])
    roles_map = rolemap.read_map(FIXTURES / "map")
    (statement,) = to_duckdb(roles_map["views"]["role_drug"], converted.sandbox.date_columns)
    cursor = converted.con.execute(f"SELECT * FROM ({statement}) AS d WHERE d.anaesthetic_key IN ('990005300', '990005301')")
    names = [d[0].lower() for d in cursor.description]
    events = [dict(zip(names, row)) for row in cursor.fetchall()]
    assert {e["source_kind"] for e in events} == {"order", "administration", "correction"}
    assert {e["action"] for e in events} >= {"ordered", "infusion_start", "rate_change", "infusion_pause", "infusion_restart",
                                             "infusion_stop"}
    (correction,) = [e for e in events if e["source_kind"] == "correction"]
    assert correction["amends_key"] in {e["drug_event_key"] for e in events} and correction["documented_time"] is not None
    first = [e for e in events if e["anaesthetic_key"] == "990005300" and e["action"] != "ordered"]
    assert "infusion_stop" not in {e["action"] for e in first}

    # A transformation written over the roles reconstructs the exposure intervals and produces the OMOP rows, compiled
    # through the hospital schema, and its output is checked against expected rows written independently and held out.
    assert list(report["compiled"]) == ["drug_exposure_infusion_roles.sql"]
    assert [s["rows"] for s in report["steps"] if s["table"] == "drug_exposure"][-1] == 8
    (scenario,) = report["scenarios"]
    assert scenario["name"] == BOUNDARY and scenario["error"] is None
    assert [item["expected"] for item in scenario["expectations"]] == [convert.shown_rows([e["result"]]) for e in held["expectations"]]
    assert all(item["met"] for item in scenario["expectations"]), [i for i in scenario["expectations"] if not i["met"]]

    # The agent needs nothing beyond the contract: in the exported workspace alone, the step runs on a role shadow built
    # from the workspace's own files, which hold no expected rows.
    work = tmp_path / "workspace"
    workspace.export(work, "public", date=DATE)
    done = _in_workspace(work, code=PUBLIC_HALF)
    assert done.returncode == 0, done.stderr
    public = json.loads(done.stdout.strip().splitlines()[-1])
    assert public["expected"] is None
    at = [public["columns"].index(c) for c in role_held["columns"]]
    found = [[row[i] for i in at] for row in public["rows"]]
    assert _comparable(found) == _comparable(role_held["rows"]), role_held["says"]

    # Its output represents the missing stop as an interval of unknown end, with its reason, rather than inventing a
    # duration.
    end, reason = role_held["columns"].index("drug_exposure_end_datetime"), role_held["columns"].index("stop_reason")
    unknown = [row for row in found if row[end] is None]
    assert len(unknown) == 1 and unknown[0][reason] == "stop not recorded"
    assert all(row[reason] is None for row in found if row[end] is not None)
