"""Runs the OMOP testbed: one pass over a synthetic world that builds, converts, checks and reconciles.

    python -m schemalyser.testbed run --world fixtures --out FOLDER [--rows 200] [--engine duckdb|sqlserver]

The testbed adds no conversion logic of its own. It calls convert.run, which builds the synthetic source
from the world's definition, creates the tables of OMOP CDM 5.4 from the published field list, plants the
scenarios, runs the conversion's own SQL files, and runs the gates and counts. release.script then writes
the release script from the same files, and the testbed confirms that it carries every step that ran.

What the testbed adds is the account of the run:

- each planted scenario, passed or failed against the rows that its scenario.json states, which were
  written by hand and never taken from the conversion's output;
- a reconciliation of source to target for each step, made by running the step's own SELECT again with
  its inner joins and its WHERE conditions applied one at a time, so that every source row that does not
  reach the target is put down to the join or the condition that left it out;
- the inputs for the Data Quality Dashboard, which needs R and is therefore not run here;
- report.json for a machine and report.md for a person.

A world's testbed.json, where it has one, says which steps may write more than one row for a single row
of the table they start from. Every other step is expected to write at most one. A world without the file
has no expectation, and the reconciliation then reports what it finds without judging it.

With --engine sqlserver, the testbed also runs tools/sqlserver/harness.py, which repeats the core steps,
the release script, the gates, the counts and the scenarios on SQL Server and compares them with DuckDB.
The reconciliation and the inputs for the Data Quality Dashboard are made from DuckDB in either case.
"""
import argparse
import csv
import hashlib
import itertools
import json
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from . import convert, harness, release
from .catalogue import Catalogue
from .extract import decode
from .translate import OMOP_SCHEMA, Unreadable, Unsupported, to_duckdb

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "sqlserver" / "harness.py"
EXPECTATIONS = "testbed.json"
SECTIONS = ("versions", "world", "engines", "build", "steps", "scenarios", "reconciliation", "release", "dqd", "checks", "summary")
CDM_VERSION = "5.4"
# The schema into which load_postgresql.sql loads the testbed's tables, apart from the OMOP database's own cdm schema.
POSTGRESQL_SCHEMA = "testbed"


def _digest(paths):
    """A SHA-256 over the names and contents of files, in a fixed order."""
    found = hashlib.sha256()
    for path in sorted(paths):
        found.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return found.hexdigest()


def resolve_world(name):
    """The world for a name or folder: (World, its folder, its conversion folder).

    The invented world in fixtures/ keeps its files under its own names; any other world is a folder in the
    layout that harness.World.from_folder reads, with its conversion beside it in conversion/.
    """
    folder = Path(name)
    if not folder.exists() and (ROOT / name).exists():
        folder = ROOT / name
    folder = folder.resolve()
    if (folder / "invented-catalogue.csv").exists():
        world = harness.World(folder / "invented-catalogue.csv", folder / "requests", folder / "invented-site-rules.json",
                              folder / "invented-design.sql", folder / "planted-values.txt")
    else:
        world = harness.World.from_folder(folder)
    return world, folder, folder / "conversion"


def _vocabulary(out):
    """The vocabulary subset that the SQL Server harness uses, written to out/vocabulary. Returns the folder."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("schemalyser_sqlserver_harness", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.sample_vocabulary(out / "vocabulary")


def _load_vocabulary(con, folder):
    """Loads the subset's concepts into the CDM's vocabulary tables, so that the CDM handed to the dashboard holds them."""
    # The sandbox's connection may not read files itself, so the rows are read here and inserted.
    loaded = {}
    for table in ("concept", "concept_relationship", "concept_synonym"):
        with open(folder / f"{table.upper()}.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
        for row in rows:
            for name in ("valid_start_date", "valid_end_date"):
                if row.get(name):
                    row[name] = f"{row[name][:4]}-{row[name][4:6]}-{row[name][6:8]}"
        names = list(rows[0]) if rows else []
        if rows:
            con.executemany(f"INSERT INTO {OMOP_SCHEMA}.{table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                            [[row[name] or None for name in names] for row in rows])
        loaded[table] = con.execute(f"SELECT COUNT(*) FROM {OMOP_SCHEMA}.{table}").fetchone()[0]
    return loaded


# The reconciliation.

def _clip(text, limit=240):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _inner(join):
    return not join.args.get("side") and (join.args.get("kind") or "").upper() in ("", "INNER") \
        and (join.args.get("on") is not None or join.args.get("using"))


def _start(select):
    """What a SELECT reads first: a table, a nested query, or None."""
    found = select.args.get("from_") or select.args.get("from")
    return found.this if found is not None else None


def _combines(select):
    """Whether a SELECT groups rows, removes repeated rows, or aggregates outside a window."""
    return bool(select.args.get("group") or select.args.get("distinct") or any(
        node.find_ancestor(exp.Window) is None and node.find_ancestor(exp.Select) is select
        for projection in select.selects for node in projection.find_all(exp.AggFunc)))


def _branches(query):
    """The SELECTs of a query that is one SELECT or a UNION ALL of SELECTs, or None for anything else."""
    if isinstance(query, exp.Select):
        return [query]
    if isinstance(query, exp.Union) and not query.args.get("distinct"):
        left, right = _branches(query.this), _branches(query.expression)
        return left + right if left is not None and right is not None else None
    return None


def trace(con, date_columns, sql, written_rows):
    """The waterfall of one step: how many rows of the table it starts from reach its target, and what leaves out the rest.

    The step's own SELECT is run again with its output replaced by the row number of the table it starts from,
    first with every inner join made a left join and no WHERE, and then with the inner joins and the WHERE's
    conditions restored one at a time. The fall in distinct source rows at each stage is put down to that join
    or condition. Where the step starts from a nested query that reads one table, in one SELECT or in a union of
    SELECTs of the same table, the row number is carried through it, and what the nested query leaves out is put
    down to its own joins and conditions together. A step that groups or combines rows is not traced.
    """
    tree = sqlglot.parse_one(sql, dialect="tsql")
    if not isinstance(tree, exp.Select):
        return {"traced": False, "reason": "the step is a union of SELECTs, so its rows have no single source table"}
    if _combines(tree):
        return {"traced": False, "reason": "the step combines rows, so a target row stands for several source rows"}
    ctes = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    start = _start(tree)
    if start is None:
        return {"traced": False, "reason": "the step reads no table"}
    nested = None
    if isinstance(start, exp.Subquery):
        branches = _branches(start.this)
        starts = [_start(branch) for branch in branches or []]
        if not branches or any(_combines(b) for b in branches) or not all(isinstance(s, exp.Table) for s in starts) \
                or len({(s.db.upper(), s.name.upper()) for s in starts}) != 1 or any(not s.db and s.name.upper() in ctes for s in starts):
            return {"traced": False, "reason": "the step starts from a nested query that does not read a single table row by row"}
        nested, start = start.alias_or_name, starts[0]
    elif not isinstance(start, exp.Table) or (not start.db and start.name.upper() in ctes):
        return {"traced": False, "reason": "the step starts from a named query"}
    alias = start.alias_or_name
    table = f"{OMOP_SCHEMA}.{start.name.lower()}" if (start.db or "").upper() == OMOP_SCHEMA.upper() else start.name
    joins = tree.args.get("joins") or []
    inner = [index for index, join in enumerate(joins) if _inner(join)]
    where = tree.args.get("where")
    conditions = list(where.this.flatten()) if where is not None and isinstance(where.this, exp.And) else \
        [where.this] if where is not None else []

    def variant(kept_joins, kept_conditions):
        copy = tree.copy()
        for position, index in enumerate(inner):
            if position >= kept_joins:
                copy.args["joins"][index].set("side", "LEFT")
        kept = conditions[:kept_conditions]
        copy.set("where", exp.Where(this=exp.and_(*[c.copy() for c in kept])) if kept else None)
        copy.set("order", None)
        if nested:
            for branch in _branches(_start(copy).this):
                branch.append("expressions", exp.alias_(exp.column("rowid", table=_start(branch).alias_or_name), "testbed_source_row"))
            copy.set("expressions", [exp.column("testbed_source_row", table=nested)])
        else:
            copy.set("expressions", [exp.alias_(exp.column("rowid", table=alias), "testbed_source_row")])
        statements = to_duckdb(copy.sql(dialect="tsql"), date_columns)
        return statements[0]

    def counted(statement):
        return con.execute(f"SELECT COUNT(DISTINCT testbed_source_row), COUNT(*) FROM ({statement}) AS v").fetchone()

    try:
        quoted = table if "." in table else f'"{table}"'
        source_rows = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        first = counted(variant(0, 0))
        stages = [(None, (source_rows, None)),
                  ("the nested query's own joins and conditions" if nested else "rows that the step could not follow", first)]
        for position, index in enumerate(inner, start=1):
            join = joins[index]
            stages.append((f"the inner join to {join.this.sql(dialect='tsql')} on {join.args['on'].sql(dialect='tsql')}"
                           if join.args.get("on") is not None else f"the inner join to {join.this.sql(dialect='tsql')}",
                           counted(variant(position, 0))))
        for position, condition in enumerate(conditions, start=1):
            stages.append((f"the condition {condition.sql(dialect='tsql')}", counted(variant(len(inner), position))))
        final = variant(len(inner), len(conditions))
        several = con.execute(f"SELECT COUNT(*), COALESCE(SUM(n - 1), 0) FROM (SELECT testbed_source_row, COUNT(*) AS n "
                              f"FROM ({final}) AS v GROUP BY 1 HAVING COUNT(*) > 1) AS s").fetchone()
    except (Unreadable, Unsupported, duckdb.Error, sqlglot.errors.SqlglotError) as error:
        return {"traced": False, "reason": f"the step could not be run again for tracing ({_clip(str(error).splitlines()[0] if str(error) else type(error).__name__)})"}
    dropped = [{"by": _clip(label), "rows": before[0] - after[0]}
               for (_, before), (label, after) in itertools.pairwise(stages) if before[0] != after[0]]
    reached, target_rows = stages[-1][1]
    return {"traced": True, "source_table": table, "source_rows": source_rows, "reached": reached, "dropped": dropped,
            "target_rows": target_rows, "source_rows_with_several_target_rows": several[0], "extra_target_rows": int(several[1]),
            "starts_from_a_nested_query": bool(nested), "start_agrees": bool(nested) or first[0] == source_rows,
            "sums_agree": source_rows == reached + sum(item["rows"] for item in dropped),
            "matches_the_run": target_rows == written_rows}


def _tables_read(sql):
    """The tables that a step reads, source and OMOP, by name."""
    tree = sqlglot.parse_one(sql, dialect="tsql")
    ctes = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    found = []
    for table in tree.find_all(exp.Table):
        if (table.db or "").upper() == OMOP_SCHEMA.upper():
            found.append(f"{OMOP_SCHEMA}.{table.name.lower()}")
        elif table.name.upper() not in ctes:
            found.append(table.name.upper())
    return list(dict.fromkeys(found))


def reconcile(conversion, folder, steps, results, expectations):
    """Source to target, for each step and for each source table that the conversion reads."""
    several_allowed = (expectations or {}).get("several_rows_for_one_source_row", {})
    con, sandbox = conversion.con, conversion.sandbox
    held = {name.upper(): name for name in sandbox.tables}
    traced_steps, by_source = [], {}
    for step, result in zip(steps, results):
        sql = decode((folder / step["file"]).read_bytes())
        entry = {"step": step["file"], "layer": step["layer"], "target": step["table"].lower(), "written": result["rows"]}
        entry.update(trace(con, sandbox.date_columns, sql, result["rows"]))
        if entry["traced"]:
            if expectations is None:
                entry["expected"], entry["as_expected"] = "no expectation", None
            elif step["file"] in several_allowed:
                entry["expected"], entry["as_expected"] = "several rows may come from one source row: " + several_allowed[step["file"]], True
            else:
                entry["expected"] = "at most one target row for each source row"
                entry["as_expected"] = entry["source_rows_with_several_target_rows"] == 0
        traced_steps.append(entry)
        for name in _tables_read(sql):
            if name.upper() in held:
                by_source.setdefault(held[name.upper()], []).append(
                    {"step": step["file"], "target": entry["target"],
                     "role": "starts from" if entry.get("source_table", "").upper() == name.upper() else "joins or looks up"})
    sources = []
    for name, uses in sorted(by_source.items()):
        rows = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        before = conversion.planted_from.get(name.upper(), (name, rows))[1]
        reaching = {}
        for use in uses:
            step = next(s for s in traced_steps if s["step"] == use["step"])
            if use["role"] == "starts from" and step["traced"]:
                reaching.setdefault(step["target"], 0)
                reaching[step["target"]] += step["reached"]
        sources.append({"table": name, "rows": rows, "planted_rows": rows - before, "read_by": uses,
                        "rows_reaching_each_target": reaching})
    targets = []
    for table in dict.fromkeys(entry["target"] for entry in traced_steps):
        from_steps = sum(entry["written"] for entry in traced_steps if entry["target"] == table)
        held_rows = conversion.count(table)
        targets.append({"table": table, "rows_from_steps": from_steps, "rows_held": held_rows, "agree": from_steps == held_rows})
    traced = [entry for entry in traced_steps if entry["traced"]]
    totals = {"steps": len(traced_steps), "steps_traced": len(traced),
              "source_rows": sum(e["source_rows"] for e in traced), "reached": sum(e["reached"] for e in traced),
              "dropped": sum(sum(d["rows"] for d in e["dropped"]) for e in traced),
              "target_rows": sum(e["target_rows"] for e in traced),
              "extra_target_rows": sum(e["extra_target_rows"] for e in traced),
              "steps_with_unexpected_several_rows": [e["step"] for e in traced if e.get("as_expected") is False],
              "all_sums_agree": all(e["sums_agree"] and e["start_agrees"] and e["matches_the_run"] for e in traced)
                                and all(t["agree"] for t in targets)}
    return {"totals": totals, "steps": traced_steps, "source_tables": sources, "target_tables": targets,
            "note": "A gate removes no rows: it fails the run. Every row left out is put down to a join or a condition of a step."}


# The scenarios.

def _scenario(entry, run_gates=None):
    """A scenario's outcome from the runner's report: passed, failed, not planted, or no expectation."""
    out = {"name": entry["name"], "description": entry["description"], "planted_rows": entry["planted"],
           "fails_gate": entry["fails_gate"], "expectations": []}
    if entry["error"]:
        return dict(out, outcome="not planted", reason=entry["error"])
    if not entry["expectations"]:
        return dict(out, outcome="no expectation")
    for item in entry["expectations"]:
        out["expectations"].append({"says": item["says"], "met": item["met"], "expected": item["expected"],
                                    "found": item["found"], "error": item["error"]})
    passed = all(item["met"] for item in entry["expectations"])
    if entry["fails_gate"]:
        failed_gates = {gate["gate"] for gate in run_gates or [] if gate["rows"]}
        out["gate_failed_as_intended"] = entry["fails_gate"] in failed_gates
        passed = passed and out["gate_failed_as_intended"]
    return dict(out, outcome="passed" if passed else "failed")


# The release script.

def release_check(folder, world, steps, report):
    """Writes the release script from the same files, and confirms that it carries every anaesthesia and derived step that ran."""
    try:
        text = release.script(folder)
        manifest = release.source_manifest(folder, Catalogue.from_csv(world.catalogue_text()))
    except release.Refused as error:
        return {"written": False, "reason": str(error)}, None, None
    carried = [step["file"] for step in steps if step["layer"] != "core" and f"-- {step['file']}\n" in text]
    expected = [step["file"] for step in steps if step["layer"] != "core"]
    gates = [{"gate": g["gate"], "rows": g["rows"], "outcome": "could not be run" if g["rows"] is None else
              "passed" if g["rows"] == 0 else "failed"} for g in report["gates"]]
    counts = [{"count": c["name"], "rows": c["rows"], "error": c["error"],
               "says": convert.report_count(c["says"], c["rows"]) if c["error"] is None else None} for c in report["counts"]]
    return {"written": True, "sha256": hashlib.sha256(text.encode()).hexdigest(), "steps_carried": len(carried),
            "steps_expected": len(expected), "carries_every_step": carried == expected,
            "gates_carried": text.count("SET @broken = "), "counts_carried": sum(1 for c in report["counts"] if f"-- counts/{c['name']}" in text),
            "source_columns": manifest.count("\n") - 1, "gates": gates, "counts": counts,
            "run_with": release.run_command(release.settings_for(folder))}, text, manifest


# The inputs for the Data Quality Dashboard.

DQD_SCRIPT = """# Runs the Data Quality Dashboard on the testbed's tables, once load_postgresql.sql has loaded them.
# The tables are in the schema {schema}, and the vocabulary is read from the OMOP database's own cdm schema.
password <- trimws(readChar("/run/pg-password.txt", file.info("/run/pg-password.txt")$size))
DatabaseConnector::downloadJdbcDrivers(dbms = "postgresql", pathToDriver = "/jdbc")
connectionDetails <- DatabaseConnector::createConnectionDetails(
  dbms = "postgresql", user = "postgres", password = password,
  server = paste0(Sys.getenv("PG_HOST", "host.docker.internal"), "/postgres"),
  port = as.integer(Sys.getenv("PG_PORT", "5432")), pathToDriver = "/jdbc")
dir.create("/out", showWarnings = FALSE, recursive = TRUE)
DataQualityDashboard::executeDqChecks(
  connectionDetails = connectionDetails,
  cdmDatabaseSchema = "{schema}", resultsDatabaseSchema = "{schema}_results", vocabDatabaseSchema = "cdm",
  cdmSourceName = "Schemalyser testbed", cdmVersion = "{cdm}",
  numThreads = 1, sqlOnly = FALSE, outputFolder = "/out", outputFile = "dqd_results.json",
  verboseMode = FALSE, writeToTable = TRUE, writeTableName = "dqdashboard_results",
  checkLevels = c("TABLE", "FIELD", "CONCEPT"),
  tablesToExclude = c("CONCEPT", "VOCABULARY", "CONCEPT_ANCESTOR", "CONCEPT_RELATIONSHIP", "CONCEPT_CLASS",
                      "CONCEPT_SYNONYM", "RELATIONSHIP", "DOMAIN"))
"""
DQD_COMMAND = ("docker run --rm --platform linux/amd64 -e PG_PORT=PORT -v \"$PWD/dqd/run_dqd.R:/run_dqd.R:ro\" "
               "-v \"PASSWORD_FILE:/run/pg-password.txt:ro\" -v jdbc-drivers-data:/jdbc -v \"$PWD/dqd/out:/out\" "
               "broadsea-broadsea-run-dqd Rscript /run_dqd.R")


def dqd_inputs(conversion, folder, out):
    """Writes the CDM as a DuckDB file and as CSV files with a psql load script, and the R script for the dashboard."""
    custom = convert.read_tables(folder)
    path = out / "testbed_cdm.duckdb"
    path.unlink(missing_ok=True)
    # The sandbox's connection may not open files, so the CDM is copied row by row into a connection of its own.
    target = duckdb.connect(str(path))
    try:
        target.execute("CREATE SCHEMA cdm")
        for table, fields in conversion.tables.items():
            target.execute(f'CREATE TABLE cdm."{table}" ({", ".join(f"{chr(34)}{name}{chr(34)} {kind}" for name, _, kind in fields)})')
            rows = conversion.con.execute(f'SELECT * FROM {OMOP_SCHEMA}."{table}"').fetchall()
            if rows:
                target.executemany(f'INSERT INTO cdm."{table}" VALUES ({", ".join("?" * len(fields))})', rows)
    finally:
        target.close()
    csv_folder = out / "csv"
    csv_folder.mkdir(exist_ok=True)
    files = conversion.export()
    for name, text in files.items():
        (csv_folder / name).write_text(text, encoding="utf-8")
    load = convert.postgresql_script(files, schema=POSTGRESQL_SCHEMA, custom=custom)
    lines = load.splitlines()
    # The testbed owns its schema, so a second load replaces the first rather than adding to it.
    lines[2:2] = [f"DROP SCHEMA IF EXISTS {POSTGRESQL_SCHEMA} CASCADE;", f"CREATE SCHEMA IF NOT EXISTS {POSTGRESQL_SCHEMA}_results;"]
    (csv_folder / "load_postgresql.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "dqd").mkdir(exist_ok=True)
    (out / "dqd" / "run_dqd.R").write_text(DQD_SCRIPT.format(schema=POSTGRESQL_SCHEMA, cdm=CDM_VERSION), encoding="utf-8")
    return {"status": "not run", "reason": "R is not available here, so the Data Quality Dashboard runs afterwards in the Broadsea environment.",
            "duckdb_file": "testbed_cdm.duckdb", "duckdb_schema": "cdm", "csv_folder": "csv", "tables_exported": len(files),
            "load": "(cd csv && psql -h 127.0.0.1 -p PORT -U postgres -f load_postgresql.sql)",
            "command": DQD_COMMAND, "script": "dqd/run_dqd.R"}


# SQL Server.

def run_sqlserver(world_folder, folder, rows, out):
    """Runs the SQL Server harness over the same world and conversion, and keeps its summary. It needs the container that its README describes."""
    target = out / "sqlserver"
    command = [sys.executable, str(HARNESS), "--rows", str(rows), "--skip-safeguards", "--sample-vocabulary",
               "--conversion", str(folder), "--out", str(target)]
    if not (world_folder / "invented-catalogue.csv").exists():
        command.insert(2, str(world_folder))
    try:
        done = subprocess.run(command, capture_output=True, text=True, cwd=ROOT / "core", timeout=3600, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "not run", "reason": str(error)}
    target.mkdir(parents=True, exist_ok=True)
    (target / "harness.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    lines = done.stdout.splitlines()
    summary = lines[lines.index("SUMMARY") + 1:] if "SUMMARY" in lines else []
    return {"status": "agrees with DuckDB" if done.returncode == 0 else "differs from DuckDB or did not run",
            "exit_code": done.returncode, "summary": summary,
            "reason": None if summary else _clip((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip() else "no output"),
            "output": "sqlserver/harness.txt"}


# The run.

def _versions(vocabulary):
    project = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text())["project"]
    concepts = len((vocabulary / "CONCEPT.csv").read_text(encoding="utf-8").splitlines()) - 1
    return {"tool": f"schemalyser {project['version']}", "cdm": CDM_VERSION, "cdm_fields_sha256": _digest([convert.FIELDS]),
            "vocabulary": f"the SQL Server harness's sample subset of {concepts} public concepts, not an Athena release",
            "vocabulary_sha256": _digest(list(vocabulary.glob("*.csv"))),
            "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__, "python": sys.version.split()[0]}


def run(world_name, out, rows=200, engine="duckdb", conversion_folder=None):
    """Runs every stage and writes report.json and report.md to out. Returns the report."""
    started = time.monotonic()
    world, world_folder, folder = resolve_world(world_name)
    folder = Path(conversion_folder).resolve() if conversion_folder else folder
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    vocabulary = _vocabulary(out)
    steps = json.loads((folder / "conversion.json").read_text())
    expectation_path = world_folder / EXPECTATIONS
    expectations = json.loads(expectation_path.read_text()) if expectation_path.exists() else None

    # 1 to 4: build, create the CDM, convert, and plant and evaluate the scenarios, all in convert.run.
    conversion, report = convert.run(world, folder, rows, vocabulary)
    every = convert.read_scenarios(folder)
    scenarios = [_scenario(entry) for entry in report["scenarios"]]
    for scenario in (s for s in every if s["fails_gate"]):
        # A scenario that exists to make a gate fail runs alone, so that its failure does not stop the main run.
        _, alone = convert.run(world, folder, rows, vocabulary, scenarios=[scenario["name"]])
        scenarios.append(_scenario(alone["scenarios"][0], alone["gates"]))
    vocabulary_rows = _load_vocabulary(conversion.con, vocabulary)
    concepts = conversion.concept_problems(vocabulary / "CONCEPT.csv")

    # 5: the reconciliation. 6: the release script with its gates and counts. 7: the dashboard's inputs.
    reconciliation = reconcile(conversion, folder, steps, report["steps"], expectations)
    released, text, manifest = release_check(folder, world, steps, report)
    if text is not None:
        (out / "release").mkdir(exist_ok=True)
        (out / "release" / "release.sql").write_text(text, encoding="utf-8")
        (out / "release" / "source_manifest.csv").write_text(manifest, encoding="utf-8")
    dqd = dqd_inputs(conversion, folder, out)
    sqlserver = run_sqlserver(world_folder, folder, rows, out) if engine == "sqlserver" else None

    failures = convert.failures(report)
    checks = [
        {"check": "every step ran cleanly", "passed": all(s["status"] == "ok" for s in report["steps"])},
        {"check": "every row fits the CDM's field list", "passed": not report["problems"], "detail": report["problems"]},
        {"check": "every gate passed", "passed": all(g["rows"] == 0 for g in report["gates"])},
        {"check": "every count ran", "passed": all(c["error"] is None for c in report["counts"])},
        {"check": "every planted scenario passed", "passed": all(s["outcome"] == "passed" for s in scenarios)},
        {"check": "the reconciliation's sums agree", "passed": reconciliation["totals"]["all_sums_agree"]},
        {"check": "no source row gave several target rows where one was expected",
         "passed": None if expectations is None else not reconciliation["totals"]["steps_with_unexpected_several_rows"],
         "detail": reconciliation["totals"]["steps_with_unexpected_several_rows"]},
        {"check": "the release script was written and carries every step that ran",
         "passed": released["written"] and released["carries_every_step"]},
        {"check": "the Data Quality Dashboard", "passed": None, "detail": "not run"},
    ]
    if sqlserver is not None:
        checks.append({"check": "SQL Server agrees with DuckDB", "passed": sqlserver.get("exit_code") == 0, "detail": sqlserver.get("summary")})
    judged = [c for c in checks if c["passed"] is not None]
    passed = sum(1 for s in scenarios if s["outcome"] == "passed")
    totals = reconciliation["totals"]
    summary = {
        "outcome": "passed" if all(c["passed"] for c in judged) and not failures else "failed",
        "checks_passed": sum(1 for c in judged if c["passed"]), "checks_judged": len(judged),
        "scenarios_passed": passed, "scenarios": len(scenarios),
        "seconds": round(time.monotonic() - started, 1),
        "sentences": [
            f"The conversion ran {sum(1 for s in report['steps'] if s['status'] == 'ok')} of {len(steps)} steps cleanly over {report['built'][len('Schemalyser has built '):].rstrip('.')}.",
            f"{passed} of {len(scenarios)} planted scenarios passed against their written expectations.",
            (f"The reconciliation traced {totals['steps_traced']} of {totals['steps']} steps. Counted step by step, the tables they "
             f"start from held {totals['source_rows']:,} rows, of which {totals['reached']:,} reached a target and {totals['dropped']:,} "
             f"were left out by a join or a condition, and the steps wrote {totals['target_rows']:,} rows from them."),
            (f"{sum(1 for g in report['gates'] if g['rows'] == 0)} of {len(report['gates'])} gates passed, and "
             f"{sum(1 for c in report['counts'] if c['error'] is None)} of {len(report['counts'])} counts ran."),
            "The Data Quality Dashboard has not been run; its inputs are in testbed_cdm.duckdb and csv/.",
        ],
    }
    result = {
        "versions": _versions(vocabulary),
        "world": {"folder": world_folder.name, "conversion": folder.name, "rows": rows,
                  "catalogue_sha256": _digest([world.catalogue_path]),
                  "conversion_sha256": _digest([p for p in folder.rglob("*") if p.is_file()]),
                  "requests_sha256": _digest(world.request_files()),
                  "expectations": EXPECTATIONS if expectations is not None else None,
                  "run_at": datetime.now(UTC).isoformat(timespec="seconds")},
        "engines": {"build": "duckdb", "convert": "duckdb" + (" and sqlserver" if sqlserver else ""),
                    "scenarios": "duckdb" + (" and sqlserver" if sqlserver else ""), "reconciliation": "duckdb",
                    "gates_and_counts": "duckdb" + (" and sqlserver" if sqlserver else ""), "dqd": "not run",
                    "sqlserver": sqlserver},
        "build": {"sentence": report["built"], "mapping_rows": report["mappings"],
                  "derived_mappings": [{k: item[k] for k in ("vocabulary", "matched")} for item in report["derived"]],
                  "vocabulary_rows": vocabulary_rows, "concept_notes": len(concepts), "unmapped": report["unmapped"]},
        "steps": [{"file": step["file"], "layer": step["layer"], "table": result_["table"], "status": result_["status"],
                   "rows": result_["rows"], "sha256": _digest([folder / step["file"]])}
                  for step, result_ in zip(steps, report["steps"])],
        "scenarios": scenarios,
        "reconciliation": reconciliation,
        "release": released,
        "dqd": dqd,
        "checks": checks + [{"check": f, "passed": False} for f in failures],
        "summary": summary,
    }
    (out / "report.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(result), encoding="utf-8")
    return result


def markdown(report):
    """The report for a person."""
    s, r = report["summary"], report["reconciliation"]
    lines = ["# OMOP testbed report", "",
             (f"The run {'passed' if s['outcome'] == 'passed' else 'did not pass'}: {s['checks_passed']} of {s['checks_judged']} "
              f"judged checks passed, and it took {s['seconds']} seconds."), ""]
    lines += [*s["sentences"], ""]
    v = report["versions"]
    lines += ["## Versions", "", (f"Schemalyser is {v['tool']}, the CDM is version {v['cdm']}, the vocabulary is {v['vocabulary']}, "
                                  f"and DuckDB is version {v['duckdb']}."), "", "## Checks", ""]
    for check in report["checks"]:
        mark = "not run" if check["passed"] is None else "passed" if check["passed"] else "failed"
        lines.append(f"- {check['check']}: {mark}.")
    lines += ["", "## Scenarios", ""]
    for scenario in report["scenarios"]:
        lines.append(f"- {scenario['name']}: {scenario['outcome']}.")
        for item in scenario["expectations"]:
            if not item["met"]:
                lines.append(f"  - {item['says']} The run gave {item['found']}, and the scenario expects {item['expected']}.")
    lines += ["", "## Reconciliation", "", r["note"], "",
              "| Step | Starts from | Source rows | Reached | Left out | Target rows | Several rows from one | As expected |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for e in r["steps"]:
        if e["traced"]:
            expected = "no expectation" if e["as_expected"] is None else "yes" if e["as_expected"] else "no"
            lines.append(f"| {e['step']} | {e['source_table']} | {e['source_rows']} | {e['reached']} | "
                         f"{sum(d['rows'] for d in e['dropped'])} | {e['target_rows']} | {e['source_rows_with_several_target_rows']} | {expected} |")
        else:
            lines.append(f"| {e['step']} | not traced: {e['reason']} | | | | {e['written']} | | |")
    lines += ["", "Each row left out is put down to the join or condition that left it out:", ""]
    for e in r["steps"]:
        for d in e.get("dropped", []):
            lines.append(f"- {e['step']}: {d['rows']} rows, by {d['by']}.")
    rel, dqd = report["release"], report["dqd"]
    lines += ["", "## Release script", ""]
    lines.append(f"The release script carries {rel['steps_carried']} of {rel['steps_expected']} steps, {rel['gates_carried']} gates and "
                 f"{rel['counts_carried']} counts, and it is in release/release.sql." if rel["written"]
                 else f"The release script was not written: {rel['reason']}.")
    for count in rel.get("counts", []):
        lines.append(f"- {count['says'] or count['count'] + ': the count could not be run.'}")
    lines += ["", "## Data Quality Dashboard", "", ("The dashboard has not been run, because R is not available here. "
                                                    "To run it in the Broadsea environment, load the tables and then run the dashboard's image:"), "",
              f"    {dqd['load']}", f"    {dqd['command']}", ""]
    if report["engines"]["sqlserver"]:
        lines += ["## SQL Server", "", f"The harness reports: {report['engines']['sqlserver']['status']}.", ""]
        lines += [f"- {line}" for line in report["engines"]["sqlserver"].get("summary", [])]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.testbed")
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run", help="build, convert, check and reconcile a world, and write report.json and report.md")
    runner.add_argument("--world", required=True, help="a world folder, such as fixtures for the invented world")
    runner.add_argument("--out", type=Path, required=True, help="the folder for the report and the files it names")
    runner.add_argument("--rows", type=int, default=200)
    runner.add_argument("--conversion", type=Path, help="a conversion folder; the world's conversion/ when left out")
    runner.add_argument("--engine", choices=("duckdb", "sqlserver"), default="duckdb",
                        help="sqlserver also runs tools/sqlserver/harness.py, which needs its container")
    args = parser.parse_args()
    try:
        report = run(args.world, args.out, args.rows, args.engine, args.conversion)
    except (convert.ScenarioError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"schemalyser.testbed: {error}")
    for sentence in report["summary"]["sentences"]:
        print(sentence)
    print(f"The report is in {args.out / 'report.json'} and {args.out / 'report.md'}.")
    return 0 if report["summary"]["outcome"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
