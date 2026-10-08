"""Runs the OMOP testbed: one pass over a synthetic world that builds, converts, checks and reconciles.

    python -m schemalyser.testbed run --world fixtures --out FOLDER [--rows 200] [--engine duckdb|sqlserver] [--profile fast|full]

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
- the reconciliation's coverage: the steps traced with every excluded row accounted for, the steps traced with the
  fan-out that testbed.json allows, the steps that could not be traced, each with its reason, and any discrepancy
  that the reconciliation cannot explain;
- the inputs for the Data Quality Dashboard, and in the full profile its results, with each failure set against
  the world's dqd-expectations.json;
- the release equivalence check, which reads the SQL Server harness's summary.json;
- report.json for a machine and report.md for a person.

The fast profile, the default, runs everything above apart from the dashboard, and passes when every judged check
passes. The full profile also loads the tables into the OMOP database and runs the dashboard's Broadsea image, and it
passes only when the dashboard ran with no failure that dqd-expectations.json does not permit and release
equivalence passed, which needs --engine sqlserver.

A world's testbed.json, where it has one, says which steps may write more than one row for a single row
of the table they start from. Every other step is expected to write at most one. A world without the file
has no expectation, and the reconciliation then reports what it finds without judging it.

With --engine sqlserver, the testbed also runs tools/sqlserver/harness.py, which repeats the core steps,
the release script, the gates, the counts and the scenarios on SQL Server and compares them with DuckDB.
The reconciliation and the inputs for the Data Quality Dashboard are made from DuckDB in either case.
"""
import argparse
import csv
import fnmatch
import hashlib
import itertools
import json
import os
import re
import shutil
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
DQD_EXPECTATIONS = "dqd-expectations.json"
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
    return {"totals": totals, "coverage": coverage(traced_steps, targets), "steps": traced_steps, "source_tables": sources,
            "target_tables": targets,
            "note": "A gate removes no rows: it fails the run. Every row left out is put down to a join or a condition of a step."}


def _discrepancies(entry):
    """What a traced step's figures leave unexplained, as sentences; none when every row is accounted for."""
    found = []
    if not entry["start_agrees"]:
        found.append("the step's SELECT, with its joins made left joins and no WHERE, did not keep every row of the table it starts from")
    if not entry["sums_agree"]:
        found.append("the rows left out and the rows that reached the target do not add up to the rows of the table it starts from")
    if not entry["matches_the_run"]:
        found.append(f"the trace gives {entry['target_rows']} target rows, and the run wrote {entry['written']}")
    if entry["source_rows_with_several_target_rows"]:
        if entry["as_expected"] is None:
            found.append("some source rows gave several target rows, and the world has no testbed.json to say whether they may")
        elif not entry["as_expected"]:
            found.append("some source rows gave several target rows, and testbed.json expects at most one")
    return found


def coverage(steps, targets):
    """Which steps the reconciliation accounted for in full, which it confirmed as fanning out, which it could not trace, and what it cannot explain."""
    accounted, fan_out, not_traced, unexplained = [], [], [], []
    for entry in steps:
        if not entry["traced"]:
            not_traced.append({"step": entry["step"], "reason": entry["reason"]})
        elif found := _discrepancies(entry):
            unexplained.append({"step": entry["step"], "reason": "; ".join(found)})
        elif entry["source_rows_with_several_target_rows"]:
            fan_out.append({"step": entry["step"], "expected": entry["expected"],
                            "source_rows_with_several_target_rows": entry["source_rows_with_several_target_rows"]})
        else:
            accounted.append(entry["step"])
    for table in targets:
        if not table["agree"]:
            unexplained.append({"table": table["table"], "reason": f"the steps wrote {table['rows_from_steps']} rows to the table, "
                                                                  f"and it holds {table['rows_held']}"})
    return {"steps": len(steps), "traced": len(steps) - len(not_traced),
            "accounted": {"count": len(accounted), "steps": accounted},
            "fan_out_confirmed": {"count": len(fan_out), "steps": fan_out},
            "not_traced": {"count": len(not_traced), "steps": not_traced},
            "unexplained": {"count": len(unexplained), "items": unexplained},
            "outcome": "passed" if not unexplained else "failed"}


def _names(items):
    return ", ".join(item if isinstance(item, str) else item.get("step") or item.get("table") for item in items)


def coverage_sentences(c, totals):
    """The reconciliation's coverage in plain sentences, which never count an untraced step as reconciled."""
    traced, steps, k = c["traced"], c["steps"], c["not_traced"]["count"]
    unexplained = c["unexplained"]["items"]
    traced_steps = [item for item in unexplained if "step" in item]
    if not unexplained:
        first = f"The reconciliation traced {traced} of {steps} steps and accounted for every excluded row in them"
    else:
        first = (f"The reconciliation traced {traced} of {steps} steps and accounted for every excluded row in "
                 f"{traced - len(traced_steps)} of them; {len(unexplained)} "
                 f"{'discrepancy is' if len(unexplained) == 1 else 'discrepancies are'} unexplained ({_names(unexplained)})")
    if k == 0:
        first += ", and every step could be traced."
    else:
        first += (f"; {k} {'step' if k == 1 else 'steps'} could not be traced ({_names(c['not_traced']['steps'])}), "
                  f"so {'its' if k == 1 else 'their'} rows are not reconciled.")
    sentences = [first]
    f = c["fan_out_confirmed"]
    if f["count"]:
        sentences.append(f"In {f['count']} of the traced steps ({_names(f['steps'])}), a source row gave several target rows, "
                         "as testbed.json allows.")
    sentences.append(f"Counted step by step, the tables that the traced steps start from held {totals['source_rows']:,} rows, of which "
                     f"{totals['reached']:,} reached a target and {totals['dropped']:,} were left out by a join or a condition, "
                     f"and the steps wrote {totals['target_rows']:,} rows from them.")
    return sentences


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
    return {"status": "not run", "reason": "the fast profile does not run it",
            "duckdb_file": "testbed_cdm.duckdb", "duckdb_schema": "cdm", "csv_folder": "csv", "tables_exported": len(files),
            "load": "(cd csv && psql -h 127.0.0.1 -p PORT -U postgres -f load_postgresql.sql)",
            "command": DQD_COMMAND, "script": "dqd/run_dqd.R"}


DQD_IMAGE = "broadsea-broadsea-run-dqd"
# The OMOP database of the ATLAS setup, which the dashboard's image reaches as host.docker.internal.
PG_PORT = "55440"
PG_PASSWORD_FILE = ROOT / "reference" / "pg-password.txt"


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def read_dqd_expectations(path):
    """The dashboard failures that a world permits, from its dqd-expectations.json: a list of entries, each naming a check
    and a CDM table, and optionally a field, as names or patterns with * and ?, and the reason in a sentence."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not all(isinstance(e, dict) and e.get("check") and e.get("table") and e.get("reason")
                                                  for e in entries):
        raise ValueError(f"{Path(path).name} must be a list of entries, each with a check, a table and a reason")
    return entries


def _expected(row, expectations):
    """The first expectation that a dashboard result matches, or None."""
    def fits(pattern, value):
        return pattern is None or fnmatch.fnmatchcase((value or "").upper(), pattern.upper())
    return next((e for e in expectations or [] if fits(e["check"], row.get("checkName")) and fits(e["table"], row.get("cdmTableName"))
                 and fits(e.get("field"), row.get("cdmFieldName"))), None)


def dqd_results(path, expectations=None):
    """The dashboard's results file, counted by outcome and by category, with each failure set against the expectations.

    A check that failed or could not run is expected when an entry of the expectations matches it, and unexpected otherwise.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    blank = {"checks": 0, "passed": 0, "failed": 0, "could_not_run": 0, "not_applicable": 0}
    found, by_category, failures = dict(blank), {}, []
    for row in data.get("CheckResults") or []:
        outcome = ("could_not_run" if _flag(row.get("isError")) else "not_applicable" if _flag(row.get("notApplicable"))
                   else "failed" if _flag(row.get("failed")) else "passed")
        category = by_category.setdefault(row.get("category") or "no category", dict(blank))
        for counted in (found, category):
            counted["checks"] += 1
            counted[outcome] += 1
        if outcome in ("failed", "could_not_run"):
            match = _expected(row, expectations)
            failures.append({"check": row.get("checkName"), "table": row.get("cdmTableName"), "field": row.get("cdmFieldName"),
                             "category": row.get("category"), "outcome": outcome.replace("_", " "),
                             "violated_rows": row.get("numViolatedRows"), "denominator_rows": row.get("numDenominatorRows"),
                             "expected": match is not None, "reason": match["reason"] if match else None})
    unexpected = [f for f in failures if not f["expected"]]
    return dict(found, by_category=dict(sorted(by_category.items())), expected_failures=len(failures) - len(unexpected),
                unexpected_failures=len(unexpected), failures=failures)


def _failure_name(failure):
    place = ".".join(part for part in (failure["table"], failure["field"]) if part)
    return f"{failure['check']} on {place}" if place else failure["check"]


def run_dqd(out, inputs, expectations=None):
    """Loads the testbed's tables into the OMOP database and runs the dashboard's image on them, as docs/testbed.md describes.

    It needs psql, Docker with the Broadsea image for the dashboard, the OMOP database listening on 127.0.0.1 at the port
    that SCHEMALYSER_PG_PORT names (55440 by default), and its password in reference/pg-password.txt or the file that
    SCHEMALYSER_PG_PASSWORD_FILE names. The password is passed through the environment and a read-only mount, and never printed.
    """
    port = os.environ.get("SCHEMALYSER_PG_PORT", PG_PORT)
    password_file = Path(os.environ.get("SCHEMALYSER_PG_PASSWORD_FILE", PG_PASSWORD_FILE))

    def not_run(reason):
        return dict(inputs, status="not run", reason=reason)

    for tool in ("psql", "docker"):
        if shutil.which(tool) is None:
            return not_run(f"{tool} is not available here")
    if not password_file.is_file():
        return not_run("the password file for the OMOP database is missing")
    if subprocess.run(["docker", "image", "inspect", DQD_IMAGE], capture_output=True, check=False).returncode != 0:
        return not_run(f"Docker does not hold the image {DQD_IMAGE}, which Broadsea builds with its dqd profile")
    env = dict(os.environ, PGPASSWORD=password_file.read_text(encoding="utf-8").strip())
    psql = ["psql", "-h", "127.0.0.1", "-p", port, "-U", "postgres", "-d", "postgres", "-q", "-v", "ON_ERROR_STOP=1"]
    try:
        loaded = subprocess.run([*psql, "-f", "load_postgresql.sql"], cwd=out / "csv", env=env, capture_output=True, text=True,
                                timeout=900, check=False)
        if loaded.returncode != 0:
            return not_run("the tables could not be loaded into the OMOP database ("
                           + _clip((loaded.stderr or loaded.stdout).strip().splitlines()[-1] if (loaded.stderr or loaded.stdout).strip() else "no output") + ")")
        results = out / "dqd" / "out" / "dqd_results.json"
        results.parent.mkdir(parents=True, exist_ok=True)
        results.unlink(missing_ok=True)
        started = time.monotonic()
        done = subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "-e", f"PG_PORT={port}",
                               "-v", f"{(out / 'dqd' / 'run_dqd.R').resolve()}:/run_dqd.R:ro",
                               "-v", f"{password_file.resolve()}:/run/pg-password.txt:ro", "-v", "jdbc-drivers-data:/jdbc",
                               "-v", f"{results.parent.resolve()}:/out", DQD_IMAGE, "Rscript", "/run_dqd.R"],
                              capture_output=True, text=True, timeout=3600, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return not_run(_clip(str(error)))
    (out / "dqd" / "dqd.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    if done.returncode != 0 or not results.exists():
        return not_run(f"the dashboard's image stopped with exit code {done.returncode}; its output is in dqd/dqd.txt")
    return dict(inputs, status="ran", reason=None, seconds=round(time.monotonic() - started, 1),
                results_file="dqd/out/dqd_results.json", output="dqd/dqd.txt", **dqd_results(results, expectations))


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
    # The last lines name the folders of this machine, so the report keeps only the counts.
    summary = [line for line in (lines[lines.index("SUMMARY") + 1:] if "SUMMARY" in lines else [])
               if not line.startswith(("The scripts that were run are in", "The same summary, with a checksum"))]
    structured = read_harness_summary(target)
    return {"status": "agrees with DuckDB" if done.returncode == 0 else "differs from DuckDB or did not run",
            "exit_code": done.returncode, "summary": summary, "summary_json": structured,
            "reason": None if summary else _clip((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip() else "no output"),
            "output": "sqlserver/harness.txt", "summary_file": "sqlserver/summary.json" if structured is not None else None}


def read_harness_summary(folder):
    """The harness's summary.json in a folder, or None when it did not write one."""
    path = Path(folder) / "summary.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _summary_numbers(summary, start):
    """The numbers in the harness's summary line that begins with start, or None when the summary has no such line."""
    line = next((line for line in summary if line.startswith(start)), None)
    return [int(n) for n in re.findall(r"\d+", line[len(start):])] if line is not None else None


def release_equivalence(sqlserver):
    """The release equivalence check, read from the SQL Server harness's summary.

    It passes only when the harness reports that the release script, executed on SQL Server, produced the same derived
    rows and the same scenario rows as DuckDB: every OMOP object compared is identical, no step failed and the release
    script ran to its end, the DuckDB run was clean, and every expectation of the planted scenarios was met on both engines.
    """
    check = {"check": "release equivalence"}
    if sqlserver is None:
        return dict(check, state="not run on SQL Server", passed=None,
                    detail="The testbed ran on DuckDB alone; --engine sqlserver runs the harness that this check reads.")
    if sqlserver.get("summary_json") is not None:
        return dict(check, source="summary.json", **_equivalence_from_json(sqlserver["summary_json"]))
    fallback = "The harness wrote no summary.json, so the check read its printed summary instead. "
    found = _equivalence_from_text(sqlserver)
    return dict(check, source="the printed summary", **dict(found, detail=fallback + found["detail"]))


def _sentence(parts):
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] + "."


def _equivalence_from_json(summary):
    """Release equivalence from the harness's summary.json: every compared table and every scenario must agree."""
    tables, scenarios = summary.get("tables") or [], summary.get("scenarios") or []
    problems = []
    differ = [t["object"] for t in tables if not t.get("agree")]
    if not tables:
        problems.append("the harness compared no OMOP object")
    elif differ:
        problems.append(f"{len(differ)} of {len(tables)} OMOP objects differ between the engines ({', '.join(differ)})")
    if (summary.get("release") or {}).get("status") != "committed":
        problems.append("the release script did not run to its end on SQL Server")
    steps = [s["file"] for s in summary.get("steps") or [] if not s.get("agree")]
    if steps:
        problems.append(f"{len(steps)} core steps failed or wrote a different number of rows ({', '.join(steps)})")
    if summary.get("duckdb_failures"):
        problems.append(f"the DuckDB run was not clean for {len(summary['duckdb_failures'])} reasons")
    unmatched = [s["scenario"] for s in scenarios if not s.get("agree")]
    if not scenarios:
        problems.append("the harness evaluated no planted scenario")
    elif unmatched:
        problems.append(f"{len(unmatched)} of {len(scenarios)} planted scenarios were not met on both engines ({', '.join(unmatched)})")
    if problems:
        return {"state": "failed", "passed": False, "detail": _sentence(problems)}
    expectations = sum(len(s.get("expectations") or []) for s in scenarios)
    return {"state": "passed", "passed": True,
            "detail": (f"The release script ran on SQL Server, all {len(tables)} OMOP objects matched DuckDB's by row count and "
                       f"checksum, and all {len(scenarios)} planted scenarios, with {expectations} expectations, were met on both engines.")}


def _equivalence_from_text(sqlserver):
    """Release equivalence from the harness's printed summary, for a harness that wrote no summary.json."""
    summary = sqlserver.get("summary") or []
    objects = _summary_numbers(summary, "OMOP objects compared:")
    steps = _summary_numbers(summary, "Steps that failed or wrote a different number of rows:")
    duckdb_clean = _summary_numbers(summary, "Reasons that the DuckDB run itself was not clean:")
    scenarios = _summary_numbers(summary, "Expectations of the planted scenarios not met on both engines:")
    if None in (objects, steps, duckdb_clean, scenarios):
        return dict(state="failed", passed=False,
                    detail="The harness gave no summary, so it has not shown that SQL Server agrees with DuckDB. "
                           + (sqlserver.get("reason") or ""))
    problems = []
    if objects[0] == 0 or objects[2]:
        problems.append(f"{objects[2]} of {objects[0]} OMOP objects differ between the engines")
    if steps[0]:
        problems.append(f"{steps[0]} steps failed or wrote a different number of rows, or the release script stopped")
    if duckdb_clean[0]:
        problems.append(f"the DuckDB run was not clean for {duckdb_clean[0]} reasons")
    if scenarios[1] == 0 or scenarios[0]:
        problems.append(f"{scenarios[0]} of {scenarios[1]} expectations of the planted scenarios were not met on both engines")
    if problems:
        return dict(state="failed", passed=False, detail=_sentence(problems))
    return dict(state="passed", passed=True,
                detail=(f"The release script ran on SQL Server, all {objects[0]} OMOP objects matched DuckDB's, and all "
                        f"{scenarios[1]} expectations of the planted scenarios were met on both engines."))


def judge(checks, profile):
    """The run's outcome: passed, or failed with each reason. The full profile requires the dashboard and release equivalence."""
    reasons = []
    for check in checks:
        name = check["check"]
        if name.startswith("the Data Quality Dashboard reported no failure"):
            if check["passed"] is False:
                unexpected = check.get("detail") or []
                reasons.append(f"the Data Quality Dashboard reported {len(unexpected)} "
                               f"{'failure' if len(unexpected) == 1 else 'failures'} that dqd-expectations.json does not permit "
                               f"({', '.join(unexpected)})")
        elif name == "the Data Quality Dashboard ran":
            if profile == "full" and not check["passed"]:
                reasons.append("the Data Quality Dashboard did not run")
        elif name == "release equivalence":
            if check["state"] == "failed":
                reasons.append("release equivalence failed on SQL Server")
            elif profile == "full" and check["state"] != "passed":
                reasons.append("release equivalence was not run on SQL Server")
        elif check["passed"] is False:
            reasons.append(f"the check that {name} did not pass" if name.startswith(("every", "no ", "the ")) else name)
    return "passed" if not reasons else "failed: " + "; ".join(reasons)


# The run.

def _versions(vocabulary):
    project = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text())["project"]
    concepts = len((vocabulary / "CONCEPT.csv").read_text(encoding="utf-8").splitlines()) - 1
    return {"tool": f"schemalyser {project['version']}", "cdm": CDM_VERSION, "cdm_fields_sha256": _digest([convert.FIELDS]),
            "vocabulary": f"the SQL Server harness's sample subset of {concepts} public concepts, not an Athena release",
            "vocabulary_sha256": _digest(list(vocabulary.glob("*.csv"))),
            "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__, "python": sys.version.split()[0]}


def run(world_name, out, rows=200, engine="duckdb", conversion_folder=None, profile="fast", dqd_expectations=None):
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
    # The dashboard failures that the world permits: the file named, else the world's own, else the conversion's.
    dqd_path = Path(dqd_expectations) if dqd_expectations else next(
        (p for p in (world_folder / DQD_EXPECTATIONS, folder / DQD_EXPECTATIONS) if p.is_file()), None)
    permitted = read_dqd_expectations(dqd_path) if dqd_path else None

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
    if profile == "full":
        dqd = run_dqd(out, dqd, permitted)
    dqd["expectations"] = ({"file": dqd_path.name, "entries": len(permitted), "sha256": _digest([dqd_path])}
                           if dqd_path else None)
    sqlserver = run_sqlserver(world_folder, folder, rows, out) if engine == "sqlserver" else None
    equivalence = release_equivalence(sqlserver)

    failures = convert.failures(report)
    coverage_ = reconciliation["coverage"]
    checks = [
        {"check": "every step ran cleanly", "passed": all(s["status"] == "ok" for s in report["steps"])},
        {"check": "every row fits the CDM's field list", "passed": not report["problems"], "detail": report["problems"]},
        {"check": "every gate passed", "passed": all(g["rows"] == 0 for g in report["gates"])},
        {"check": "every count ran", "passed": all(c["error"] is None for c in report["counts"])},
        {"check": "every planted scenario passed", "passed": all(s["outcome"] == "passed" for s in scenarios)},
        {"check": "the reconciliation found no unexplained discrepancy", "passed": coverage_["outcome"] == "passed",
         "detail": coverage_["unexplained"]["items"]},
        {"check": "the release script was written and carries every step that ran",
         "passed": released["written"] and released["carries_every_step"]},
        {"check": "the Data Quality Dashboard ran", "passed": dqd["status"] == "ran" if profile == "full" else None,
         "detail": dqd["reason"] or "ran"},
        {"check": "the Data Quality Dashboard reported no failure that dqd-expectations.json does not permit",
         "passed": dqd["unexpected_failures"] == 0 if dqd["status"] == "ran" else None,
         "detail": [_failure_name(f) for f in dqd.get("failures", []) if not f["expected"]]},
        equivalence,
    ]
    if sqlserver is not None:
        checks.append({"check": "SQL Server agrees with DuckDB", "passed": sqlserver.get("exit_code") == 0, "detail": sqlserver.get("summary")})
    checks += [{"check": f, "passed": False} for f in failures]
    judged = [c for c in checks if c["passed"] is not None]
    passed = sum(1 for s in scenarios if s["outcome"] == "passed")
    if dqd["status"] == "ran":
        dqd_sentence = (f"The Data Quality Dashboard ran {dqd['checks']:,} checks: {dqd['passed']:,} passed, {dqd['failed']:,} failed, "
                        f"{dqd['could_not_run']:,} could not run and {dqd['not_applicable']:,} did not apply. Of the "
                        f"{dqd['expected_failures'] + dqd['unexpected_failures']:,} that failed or could not run, "
                        f"{dqd['expected_failures']:,} are expected, with a reason in "
                        f"{dqd['expectations']['file'] if dqd['expectations'] else 'no expectations file'}, and "
                        f"{dqd['unexpected_failures']:,} are not.")
    elif profile == "full":
        dqd_sentence = f"The Data Quality Dashboard did not run, because {dqd['reason']}, so the full profile cannot pass."
    else:
        dqd_sentence = "The fast profile does not run the Data Quality Dashboard; its inputs are in testbed_cdm.duckdb and csv/."
    equivalence_sentence = {
        "passed": "Release equivalence passed: on SQL Server, the release script produced the same derived rows and scenario rows as DuckDB.",
        "failed": f"Release equivalence failed. {equivalence['detail']}",
        "not run on SQL Server": "Release equivalence has not been run on SQL Server, because the testbed ran on DuckDB alone.",
    }[equivalence["state"]]
    summary = {
        "profile": profile,
        "outcome": judge(checks, profile),
        "checks_passed": sum(1 for c in judged if c["passed"]), "checks_judged": len(judged),
        "scenarios_passed": passed, "scenarios": len(scenarios),
        "seconds": round(time.monotonic() - started, 1),
        "sentences": [
            f"The conversion ran {sum(1 for s in report['steps'] if s['status'] == 'ok')} of {len(steps)} steps cleanly over {report['built'][len('Schemalyser has built '):].rstrip('.')}.",
            f"{passed} of {len(scenarios)} planted scenarios passed against their written expectations.",
            *coverage_sentences(coverage_, reconciliation["totals"]),
            (f"{sum(1 for g in report['gates'] if g['rows'] == 0)} of {len(report['gates'])} gates passed, and "
             f"{sum(1 for c in report['counts'] if c['error'] is None)} of {len(report['counts'])} counts ran."),
            dqd_sentence,
            equivalence_sentence,
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
                    "gates_and_counts": "duckdb" + (" and sqlserver" if sqlserver else ""),
                    "dqd": "postgresql, through the Broadsea image" if dqd["status"] == "ran" else "not run",
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
        "checks": checks,
        "summary": summary,
    }
    (out / "report.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(result), encoding="utf-8")
    return result


def markdown(report):
    """The report for a person."""
    s, r = report["summary"], report["reconciliation"]
    lines = ["# OMOP testbed report", "",
             (f"The run in the {s['profile']} profile "
              + ("passed" if s["outcome"] == "passed" else f"did not pass, because {s['outcome'][len('failed: '):]}")
              + f". {s['checks_passed']} of {s['checks_judged']} judged checks passed, and the run took {s['seconds']} seconds."), ""]
    lines += [*s["sentences"], ""]
    v = report["versions"]
    lines += ["## Versions", "", (f"Schemalyser is {v['tool']}, the CDM is version {v['cdm']}, the vocabulary is {v['vocabulary']}, "
                                  f"and DuckDB is version {v['duckdb']}."), "", "## Checks", ""]
    for check in report["checks"]:
        mark = check.get("state") or ("not judged in this profile" if check["passed"] is None else "passed" if check["passed"] else "failed")
        lines.append(f"- {check['check']}: {mark}.")
    lines += ["", "## Scenarios", ""]
    for scenario in report["scenarios"]:
        lines.append(f"- {scenario['name']}: {scenario['outcome']}.")
        for item in scenario["expectations"]:
            if not item["met"]:
                lines.append(f"  - {item['says']} The run gave {item['found']}, and the scenario expects {item['expected']}.")
    c = r["coverage"]
    lines += ["", "## Reconciliation", "", r["note"], "",
              f"- Traced, with every excluded row accounted for ({c['accounted']['count']}): {_names(c['accounted']['steps']) or 'none'}.",
              f"- Traced, with the fan-out that testbed.json allows confirmed ({c['fan_out_confirmed']['count']}): "
              f"{_names(c['fan_out_confirmed']['steps']) or 'none'}."]
    lines += [f"- Not traced, so not reconciled ({c['not_traced']['count']}):" if c["not_traced"]["count"] else "- Not traced (0): none."]
    lines += [f"  - {item['step']}: {item['reason']}." for item in c["not_traced"]["steps"]]
    lines += [f"- With an unexplained discrepancy ({c['unexplained']['count']}):" if c["unexplained"]["count"] else "- With an unexplained discrepancy (0): none."]
    lines += [f"  - {item.get('step') or item.get('table')}: {item['reason']}." for item in c["unexplained"]["items"]]
    lines += ["",
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
    lines += ["", "## Data Quality Dashboard", ""]
    if dqd["status"] == "ran":
        lines += [(f"The dashboard ran {dqd['checks']:,} checks in {dqd['seconds']} seconds: {dqd['passed']:,} passed, {dqd['failed']:,} "
                   f"failed, {dqd['could_not_run']:,} could not run and {dqd['not_applicable']:,} did not apply. Its results are in "
                   f"{dqd['results_file']}."), "", "| Category | Checks | Passed | Failed | Could not run | Not applicable |",
                  "|---|---:|---:|---:|---:|---:|"]
        lines += [f"| {name} | {n['checks']} | {n['passed']} | {n['failed']} | {n['could_not_run']} | {n['not_applicable']} |"
                  for name, n in dqd["by_category"].items()]
        lines.append("")
        unexpected = [f for f in dqd["failures"] if not f["expected"]]
        expected = [f for f in dqd["failures"] if f["expected"]]
        lines.append(f"{len(unexpected)} of the checks that failed or could not run are not permitted by "
                     f"{dqd['expectations']['file'] if dqd['expectations'] else 'an expectations file, because the world has none'}"
                     + (", and each of them fails the full profile:" if unexpected else "."))
        lines += [f"- {_failure_name(f)} ({f['category']}): {f['outcome']}, with {f['violated_rows']} of {f['denominator_rows']} rows in breach."
                  if f["outcome"] == "failed" else f"- {_failure_name(f)} ({f['category']}): {f['outcome']}." for f in unexpected]
        if expected:
            lines += ["", f"{len(expected)} are expected, each for the reason given:"]
            grouped = {}
            for f in expected:
                grouped.setdefault(f["reason"], []).append(f)
            for reason, items in grouped.items():
                names = sorted({_failure_name(f) for f in items})
                lines.append(f"- {', '.join(names[:4])}{f' and {len(names) - 4} more' if len(names) > 4 else ''} "
                             f"({len(items)} {'check' if len(items) == 1 else 'checks'}): {reason}")
        lines.append("")
    else:
        lines += [(f"The dashboard has not been run, because {dqd['reason'].rstrip('.')}. You can run it in the Broadsea environment "
                   "by loading the tables and then running the dashboard's image:"), "", f"    {dqd['load']}", f"    {dqd['command']}", ""]
    eq = next(check for check in report["checks"] if check["check"] == "release equivalence")
    said = {"passed": "The check passed.", "failed": "The check failed.", "not run on SQL Server": "The check has not been run on SQL Server."}
    lines += ["## Release equivalence", "", f"{said[eq['state']]} {eq['detail']}", ""]
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
    runner.add_argument("--dqd-expectations", type=Path,
                        help="the dashboard failures that are permitted; the world's dqd-expectations.json when left out")
    runner.add_argument("--profile", choices=("fast", "full"), default="fast",
                        help="full also runs the Data Quality Dashboard and requires it and release equivalence to pass")
    args = parser.parse_args()
    try:
        report = run(args.world, args.out, args.rows, args.engine, args.conversion, args.profile, args.dqd_expectations)
    except (convert.ScenarioError, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"schemalyser.testbed: {error}")
    for sentence in report["summary"]["sentences"]:
        print(sentence)
    outcome = report["summary"]["outcome"]
    print(f"The {args.profile} profile " + ("passed." if outcome == "passed" else f"did not pass, because {outcome[len('failed: '):]}."))
    print(f"The report is in {args.out / 'report.json'} and {args.out / 'report.md'}.")
    return 0 if report["summary"]["outcome"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
