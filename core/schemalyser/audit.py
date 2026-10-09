"""The execution package of an audit: everything that a database analyst needs to run one audit once on production.

An audit is one T-SQL SELECT over the parts of the record, as rolemodel/neonatal_low_mean_pressure.sql is. This module
reads it with a saved hospital schema and writes a folder that keeps three reports apart:

    feasibility.md, feasibility.json   whether the hospital schema can answer the question (feasibility.py)
    expected-output.json               the audit run on made-up rows with the planted cases, for the shape of the answer
    safety-report.json                 the static policy on the final text of the script, with its execution class
    plan-review.json                   the review of an estimated plan, once the database analyst has obtained one

with the script itself and what describes it:

    query.sql            the audit compiled over the hospital's own tables, in two parts: part 1 puts at most 5,000
                         anaesthetics of the audit's cohort in one period into #cohort, and part 2 reaches every larger
                         table from #cohort by key alone, arranged as the series that the policy requires, each step
                         marked by its own comment line: "-- series: count", "-- series: coverage", "-- series: rows"
    question.sql         the question over the roles, and decisions.json the decisions, as the script was built from them
    specification.md     the script in the source database's terms
    manifest.json        the hashes of the script, the schema, the contract's parts and every file of the package; the
                         question, cohort, period and decisions; the class with the policy's version; the permissions,
                         the expected output and the resource assumptions; the plan review; the correctness report; and
                         the approval, which reads not approved until the database analyst records otherwise
    README.md            what the package is and what each person does with it

    python -m schemalyser.audit build SCHEMA.zip QUERY.sql --out FOLDER [--period FROM TO] [--decisions decisions.json]
    python -m schemalyser.audit plan FOLDER plan.sqlplan
    python -m schemalyser.audit status FOLDER [--schema SCHEMA.zip]
    python -m schemalyser.audit approve FOLDER --by NAME [--refuse] [--note TEXT] [--date YYYY-MM-DD]
    python -m schemalyser.audit export SCHEMA.zip SPECIFICATION.json --episodes EPISODES.csv --out FOLDER [--period FROM TO]

status prints, as JSON, whether the package still stands as it was built: it rechecks every hashed input, and a change
to any of them voids the class and the plan review and returns the approval to not approved. approve records the
database analyst's approval or refusal with their name and the date. export compiles a specification
(specification.py) and builds a package for each of its sections.

decisions.json is {"decisions": [{"about": "...", "decision": "...", "by": "...", "date": "..."}],
"exact_small_numbers": false}; with exact_small_numbers true, the audit's result keeps counts from 1 to 4, which only
the audit's approval can allow.

The package names the hospital's tables and local codes, so it stays on the hospital's own storage with the hospital
schema. Nothing here is checked on a hospital's data: the tests build a saved schema from the invented world.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import describe, feasibility, policy, rolemap
from .blanking import blanking, count_columns

TOOL = "schemalyser"
CAP = policy.CAP
MANIFEST = "manifest.json"
NOT_REVIEWED = "not yet reviewed"
# The views of the record from which part 1 may choose the cohort: the small tables of patients and anaesthetics.
COHORT_PARTS = ("role_anaesthetic", "role_patient")
# How each part of the record is reached from #cohort in part 2: (the column that links it, the column of #cohort).
REACHED = {"role_reading": ("anaesthetic_key", "anaesthetic_key"), "role_anaesthetic": ("anaesthetic_key", "anaesthetic_key"),
           "role_patient": ("patient_key", "patient_key")}
SESSION = ("SET NOCOUNT ON;", "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;", "SET LOCK_TIMEOUT 10000;",
           "SET DEADLOCK_PRIORITY LOW;")
DROP = "IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;"
# The comment line that marks each step of the series, exactly as the policy reads it, just before its statement.
SERIES_MARKERS = ("-- series: count", "-- series: coverage", "-- series: rows")

WORDING = {
    # The comments of query.sql.
    "written": "Schemalyser wrote this script on {date} from the audit {query} and the hospital schema {schema}. The script names the tables and local codes of the hospital's database, so it is for use inside the hospital only.",
    "timeout": "Before running anything, the database analyst sets a time limit: in the Query menu, Query Options, then Execution, the analyst enters a number of seconds in Execution time-out, because SQL Server Management Studio sets none otherwise.",
    "order": "The database analyst runs part 1 on its own first. The analyst then obtains the estimated plan of part 2, as README.md describes, and runs part 2 only once the plan review in the package has passed. A change to any line of this script voids the plan review.",
    "session": "The session settings below read without waiting for locks, give way to any other statement after ten seconds of waiting, and make this script the one that SQL Server ends if two ever block each other. Every table is also read WITH (NOLOCK), which still holds a schema lock, so the script should not run during the nightly load.",
    "series": "The script runs as a series of three steps of increasing size, and the database analyst can stop after any of them: a count of the anaesthetics in #cohort over the period, then how many of them each link reaches, then the rows. Each step follows a line that names it.",
    "count": "The first step counts the anaesthetics in #cohort that started from {start} to {end}.",
    "coverage": "The second step counts how many of the anaesthetics in #cohort have a matching row in {tables}, through which part 2 reaches the larger tables. It reads no large table.",
    "coverage_none": "The second step counts the anaesthetics in #cohort, because part 2 reaches no further table through a link.",
    "unmeasured": "The link of {parts} begins at a large table, so the second step cannot measure it without reading that table, and its coverage is first seen in the rows.",
    "part1": "Part 1 puts into #cohort at most {cap} anaesthetics of the audit's cohort that started from {start} to {end}, the earliest first, from {tables}. #cohort is a temporary table that exists only in the database analyst's SQL window and disappears when that window closes. Nothing else is made or changed.",
    "limit": "If cohort_reached_the_limit reads 1, #cohort holds {cap} anaesthetics and the period may hold more. In that case the database analyst stops here, and the clinician chooses a shorter period and builds the package again.",
    "part2": "Part 2 answers the audit for the anaesthetics in #cohort only. It reaches {tables} from #cohort by their keys, so that it reads only the rows of those anaesthetics.",
    "part2_small": "Part 2 answers the audit for the anaesthetics in #cohort only.",
    "blank": "The result leaves blank any count from 1 to 4, so that no small number can point to a child.",
    "exact": "The audit's approval allows exact small numbers, so the result keeps every count as it is.",
    "end": "The script removes #cohort once part 2 has run.",
    # Messages.
    "no_cohort": "Schemalyser cannot write this audit as a two-part script, because none of its steps chooses its anaesthetics from the patients and the anaesthetics alone.",
    "no_link": "Schemalyser cannot write this audit as a two-part script, because part 2 reads {part}, and #cohort does not carry {column}, by which it would be reached.",
    "unreached": "Schemalyser cannot write this audit as a two-part script, because the hospital schema does not say how {part} is linked to an anaesthetic.",
    "model": "Schemalyser has stopped, because this question needs parts that the role model does not yet describe. The feasibility report in {folder} lists them, and no script has been written.",
    "period": "The period is two dates, the first no later than the second, written as YYYY-MM-DD.",
    "decisions": "{name} is not a decisions file that Schemalyser can read: it holds one JSON object with a list of decisions.",
    "changed": "An input of the package has changed since it was built, so its execution class, any plan review and any approval no longer apply. The clinician builds the package again before a plan is reviewed or an approval recorded.",
    "built": "Schemalyser wrote the package to {folder}. The script is of class {grade}, and the policy {outcome}.",
    "reviewed": "Schemalyser reviewed the plan, and its review is: {state}.",
    "approver": "An approval or a refusal names the person who gives it, with --by.",
    "class_d": "The script is of class D, which is not to be run, so Schemalyser cannot record an approval of it. A refusal can still be recorded.",
    "exported": "Schemalyser wrote the export to {folder}, with a package for each of its {count} sections that the role policy let through.",
}

README_WORDING = {
    "title": "# The audit's execution package",
    "inside": "This package names the tables and local codes of the hospital's database, so the clinician keeps it on the hospital's own storage with the hospital schema.",
    "what": "Schemalyser wrote this package on {date} for the audit {query}, from the hospital schema {schema}. It holds the script that the database analyst runs once on the production database, with three reports that are kept apart because each answers a different question.",
    "h_reports": "## The three reports",
    "feasibility": "The feasibility report, in feasibility.md, says whether the hospital schema can answer the question, and what the clinician or the database analyst still has to settle. Its verdict is: {verdict}",
    "correctness": "The correctness report, in expected-output.json, holds the audit's answer on made-up rows, with the planted cases that test each of its rules. {planted} The figures are invented, so they show the shape of the answer and nothing about the hospital. The clinician compares the production answer's shape with them.",
    "planted_ok": "Every planted case gave its expected answer.",
    "planted_not": "{count} of the planted cases did not give their expected answer, and the clinician resolves that before the script is run.",
    "safety": "The safety report, in safety-report.json, is the policy on the final text of query.sql. Schemalyser read every statement and checked it against an allowlist of statements, tables, functions and joins, and against the rule that every large table is reached from the cohort by key. The policy {outcome}.",
    "safety_failed": "The rules that failed are: {rules}.",
    "h_class": "## The execution class",
    "class": "{says} Schemalyser derived the class from the text of the script, not from any setting.",
    "B": "Before the first run, the database analyst obtains the estimated plan of part 2 and Schemalyser reviews it, as the next section describes, and the analyst sets a time limit in Management Studio.",
    "A": "The script reads only the server's own records of its tables. The database analyst sets a time limit and can run it.",
    "C": "Before the first run, the database team approves the script, runs it in isolation from other work, such as on a reporting replica or out of hours, and watches it while it runs. The plan review is still needed.",
    "D": "The script is not to be run. The clinician rebuilds the package once the audit or the hospital schema has changed so that the policy passes.",
    "h_plan": "## The plan review",
    "plan_none": "The plan has not yet been reviewed.",
    "plan_steps": "The database analyst obtains the estimated plan in SQL Server Management Studio as follows. An estimated plan is made without running the statement it describes, but part 2 reads #cohort, which exists only once part 1 has run.",
    "steps": ["Open query.sql in a new query window, connected to the production database, and set the time limit in the Query menu, Query Options, then Execution.",
              "Select part 1, from the line that begins IF OBJECT_ID to the semicolon that ends the statement after the line -- series: coverage, and run only that selection. If cohort_reached_the_limit reads 1, stop and tell the clinician. The count after -- series: count and the counts after -- series: coverage are the first two steps of the series, and the analyst gives both to the clinician, who may stop here if either is not what the clinician expected.",
              "Select part 2, from the WITH that follows the line -- series: rows to the semicolon that ends it, and press the Display Estimated Execution Plan button on the toolbar, or Ctrl+L. Management Studio shows the plan without running part 2.",
              "Right-click the plan and choose Save Execution Plan As, and save it as plan.sqlplan. Alternatively, run SET SHOWPLAN_XML ON; on its own, then run the selection of part 2, click the XML link in the result, save it as plan.sqlplan, and run SET SHOWPLAN_XML OFF;.",
              "Give plan.sqlplan to the clinician, who runs python -m schemalyser.audit plan with this folder and the file."],
    "plan_after": "Once the plan review has passed, the database analyst runs part 2 in the same window, then the final DROP TABLE.",
    "h_approval": "## Approval",
    "approval": "The package is not approved for production until the database analyst records an approval in the manifest, whatever the reports above say. The analyst records an approval or a refusal, with their name, by running {command}. The approval records the hashes of everything the package was built from, and Schemalyser refuses to record one for a package that has changed since it was built.",
    "h_void": "## A change to the script or to what it was built from",
    "void": "The manifest records a hash of query.sql and of every other file the package was built from, with the version of the hospital schema, the parts of the role contract that the question reads and the version of the policy, and the plan review records the hash of the script for which its plan was obtained. A change to any line of query.sql, or to any of the others, voids the plan review and the execution class and returns the approval to not approved, so neither the database analyst nor the clinician edits the script by hand. The clinician builds the package again instead. python -m schemalyser.audit status rechecks every hash.",
    "h_session": "## The session settings that the script carries",
    "session": "The script begins with SET NOCOUNT ON, SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED, SET LOCK_TIMEOUT 10000 and SET DEADLOCK_PRIORITY LOW, and reads every table WITH (NOLOCK). It therefore reads without waiting for row locks, gives way after ten seconds where it would otherwise wait for a lock, and is the statement that SQL Server ends if two ever block each other. The schema lock that every read still holds means that the script should not run during the nightly load. The time limit is not among the settings, because SQL Server has no setting for it that every account may use, so the database analyst sets it in Management Studio.",
    "h_files": "## The files",
    "files": ["query.sql is the script.", "question.sql is the question over the parts of the record from which the script was compiled.",
              "decisions.json holds the clinicians' decisions.",
              "specification.md describes the script in the terms of the hospital's database.",
              "feasibility.md and feasibility.json are the feasibility report.", "expected-output.json is the answer on made-up rows.",
              "safety-report.json is the policy's report on the script.", "plan-review.json, once it exists, is the review of the estimated plan, and plan.sqlplan is the plan it reviewed.",
              "manifest.json records the versions, the hashes, the class, the policy's outcome and version, the permissions the script needs, the expected output, the resource assumptions, the state of the plan review and the approval."],
}

PLAN_WORDING = {
    "estimate": "An estimated plan is SQL Server's estimate of how it would run part 2 at the time it was obtained, made from the statistics it then held. The plan that runs may differ, and the figures above are estimates rather than measurements.",
    "accepted": "On {date}, Schemalyser reviewed the estimated plan that the database analyst obtained, and found nothing that rejects it or calls for the database team.",
    "rejected": "On {date}, Schemalyser reviewed the estimated plan that the database analyst obtained, and rejects it: {reasons}. The database analyst does not run part 2, and shows the plan and this review to the clinician and the database team.",
    "escalate": "On {date}, Schemalyser reviewed the estimated plan that the database analyst obtained, and found nothing that rejects it, but asks the database team to look at it before part 2 is run: {reasons}.",
    "mismatch": "The plan does not hold the statement of part 2 as query.sql writes it, so the plan may have been obtained for another script. Schemalyser rejects it until a plan of this script is reviewed.",
    "scan": "the plan scans {table}, a large table, where it should seek it from #cohort",
    "no_predicate": "a join in the plan has no join predicate ({op})",
    "rows": "the largest intermediate result is estimated at {rows} rows, above the threshold of {threshold}",
    "memory": "the plan asks for a memory grant of about {mb} MB, above {limit} MB",
    "spool": "the plan holds a {op} over a large input, estimated at {rows} rows",
    "seeks": "The plan seeks {tables} from #cohort, which is encouraging but not conclusive.",
    "no_seeks": "The plan shows no seek of a large table out of #cohort.",
    "largest": "The largest intermediate result is estimated at {rows} rows.",
    "grant": "The plan asks for a memory grant of about {mb} MB.",
    "no_grant": "The plan records no memory grant.",
}


class AuditError(ValueError):
    """The package cannot be written; the message says why in one sentence."""


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def tool_version():
    try:
        from importlib.metadata import version
        return version("schemalyser")
    except Exception:  # noqa: BLE001 - the package may run from its folder without being installed
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml")
        found = re.search(r'^version = "([^"]+)"', text.read_text(encoding="utf-8"), re.M) if text.exists() else None
        return found.group(1) if found else "unknown"


def _wrapped(sentence):
    return "\n".join(f"-- {line}" for line in textwrap.wrap(sentence, 110))


def _and(items):
    return describe._and(list(items))


def _day(iso):
    return describe._day(iso)


# The schema's tables.

def schema_tables(schema):
    """{part of the record: [tables its SQL reads]} for every part that the hospital schema supplies, from its files."""
    found = {}
    for path, data in sorted(schema.files.items()):
        match = re.fullmatch(r"map/(role_\w+)\.sql", path)
        if not match:
            continue
        try:
            tree = sqlglot.parse_one(describe._text(data), dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        found[match.group(1)] = list(dict.fromkeys(t.name for t in tree.find_all(exp.Table)))
    return found


def _view_sql(schema, view):
    return rolemap.view_sql(schema.sitting.view_sql(view))


def _columns_from(schema, view):
    """{column of the part: the expression over the hospital's tables that gives it}, from the part's SQL."""
    tree = sqlglot.parse_one(schema.sitting.view_sql(view), dialect="tsql")
    aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    out = {}
    for projection in tree.expressions:
        value = projection.unalias().copy()
        for column in value.find_all(exp.Column):
            if column.table in aliases:
                column.set("table", exp.to_identifier(aliases[column.table]))
        out[projection.alias_or_name] = value.sql(dialect="tsql")
    return out


# Compiling the audit into two parts.

def _reads(node, names):
    return {t.name.lower() for t in node.find_all(exp.Table) if not t.db and t.name.lower() in names}


def _cohort_cte(tree):
    for cte in tree.find_all(exp.CTE):
        reads = {t.name.lower() for t in cte.this.find_all(exp.Table)}
        if "role_anaesthetic" in reads and reads <= set(COHORT_PARTS) and \
                "anaesthetic_key" in {n.lower() for n in cte.this.named_selects}:
            return cte
    raise AuditError(WORDING["no_cohort"])


def _period(start, end):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first > last:
        raise ValueError
    return first, last


def _reached_cte(schema, view, cohort_columns):
    link, cohort_column = REACHED[view]
    if cohort_column not in cohort_columns:
        raise AuditError(WORDING["no_link"].format(part=rolemap.view_title(view, False), column=cohort_column))
    role = schema.sitting.data["roles"].get(view) or {}
    if not ((role.get("columns") or {}).get(link) or {}).get("binding"):
        raise AuditError(WORDING["unreached"].format(part=rolemap.view_title(view, False)))
    columns = [c for c in rolemap.views()[view] if c != link]
    lines, expressions, order, where = schema.sitting._reached(view, link, cohort_column, columns)
    text = ("SELECT " + ",\n       ".join([f"c.{cohort_column} AS {link}"] + [f"{expressions[c]} AS {c}" for c in columns])
            + "\n" + "\n".join(lines + describe._where(where)))
    return text, order


def _large(schema, large):
    sizes = schema.sitting.sizes

    def is_large(table):
        rows = sizes.get(table.upper())
        return rows is None or rows >= large
    return is_large


def _coverage(schema, views, cohort_columns, is_large):
    """The second step of the series: for each part that part 2 reaches from #cohort, how many of the cohort's
    anaesthetics have a matching row through the part's link, following the link only as far as its last table that is
    not large, so that the step reads no large table. Returns (statement, tables read, [parts whose link begins at a
    large table and so cannot be measured before the rows are read])."""
    ctes, joins, counts, tables, unmeasured = [], [], ["COUNT(*) AS cohort_anaesthetics"], [], []
    for n, view in enumerate(views, 1):
        link, cohort_column = REACHED[view]
        if cohort_column not in cohort_columns:
            continue
        lines, _, order, _ = schema.sitting._reached(view, link, cohort_column, [])
        small = 0
        while small < len(order) and not is_large(order[small]):
            small += 1
        if small == 0:
            unmeasured.append(view)
            continue
        tables += order[:small]
        name = f"reach_{view[len('role_'):]}"
        body = "\n".join(lines[:small + 1])
        ctes.append(f"{name} AS (\n  SELECT DISTINCT c.{cohort_column} AS {cohort_column}\n"
                    + "\n".join("  " + line for line in body.splitlines()) + "\n)")
        joins.append(f"LEFT JOIN {name} AS r{n} ON r{n}.{cohort_column} = c.{cohort_column}")
        counts.append(f"COUNT(r{n}.{cohort_column}) AS cohort_reaching_{view[len('role_'):]}")
    text = (("WITH " + ",\n".join(ctes) + "\n") if ctes else "") + "SELECT " + ",\n       ".join(counts) + \
        "\nFROM   #cohort AS c" + "".join("\n" + j for j in joins) + ";"
    return text, list(dict.fromkeys(tables)), unmeasured


def compile_audit(schema, audit_sql, start, end, blank=True, final=None, large=policy.LARGE):
    """The audit as a two-part script over the hospital's tables, arranged as the series that the policy requires: a
    count of the cohort over the period, the coverage of each link from #cohort, then the rows. final, when given,
    replaces the audit's final SELECT (for a rehearsal that reads one of its steps). Returns {"count", "part1",
    "coverage", "part2", "cohort_tables", "coverage_tables", "unmeasured", "part2_tables", "cohort_columns",
    "cohort_conditions", "counts", "columns"}."""
    tree = rolemap.check_audit(audit_sql).copy()
    # The counts are read from the audit as written, because part 2 reads the cohort's columns from #cohort, through
    # which the rule for what is a count cannot see.
    counts = count_columns(tree) if final is None else []
    cohort = _cohort_cte(tree)
    name = cohort.alias
    body = cohort.this.copy()
    alias = next(t.alias_or_name for t in body.find_all(exp.Table) if t.name.lower() == "role_anaesthetic")
    after = end + dt.timedelta(days=1)
    body.where(f"{alias}.start_time >= CAST('{start.isoformat()}' AS datetime) AND "
               f"{alias}.start_time < CAST('{after.isoformat()}' AS datetime)", dialect="tsql", copy=False)
    if "start_time" not in [c.lower() for c in body.named_selects]:
        # The count of the series bounds #cohort by the period, so #cohort carries the start of each anaesthetic.
        body.select(f"{alias}.start_time", dialect="tsql", copy=False)
    columns = list(body.named_selects)
    views_one = [v for v in COHORT_PARTS[::-1] if v in _reads(body, set(COHORT_PARTS))]
    tables = schema_tables(schema)
    cohort_tables = list(dict.fromkeys(t for v in views_one for t in tables.get(v, [])))
    ctes = ",\n".join(f"{v} AS (\n" + "\n".join("  " + line for line in _view_sql(schema, v).splitlines()) + "\n)"
                      for v in views_one)
    picked = ",\n       ".join([f"ISNULL(k.anaesthetic_key, 0) AS anaesthetic_key"]
                               + [f"k.{c}" for c in columns if c.lower() != "anaesthetic_key"])
    ordered = "k.start_time, k.anaesthetic_key" if "start_time" in [c.lower() for c in columns] else "k.anaesthetic_key"
    inner = "\n".join("  " + line for line in body.sql(dialect="tsql", pretty=True).splitlines())
    # The first step of the series: the anaesthetics of #cohort counted over the period, before any link is followed.
    count = (f"SELECT COUNT(*) AS anaesthetics_in_period\nFROM   #cohort AS c\n"
             f"WHERE  c.start_time >= CAST('{start.isoformat()}' AS datetime)\n"
             f"  AND  c.start_time < CAST('{after.isoformat()}' AS datetime);")
    part1 = (f"WITH {ctes}\nSELECT TOP ({CAP}) {picked}\nINTO   #cohort\nFROM   (\n{inner}\n) AS k\n"
             f"WHERE  k.anaesthetic_key IS NOT NULL\nORDER  BY {ordered};\n"
             "ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);\n"
             f"SELECT CASE WHEN COUNT(*) >= {CAP} THEN 1 ELSE 0 END AS cohort_reached_the_limit FROM #cohort;")
    # Part 2: the cohort's step reads #cohort, and every part still read is reached from #cohort by key.
    cohort.set("this", sqlglot.parse_one("SELECT " + ", ".join(f"c.{c}" for c in columns) + " FROM #cohort AS c",
                                         dialect="tsql"))
    if final is not None:
        replacement = sqlglot.parse_one(final, dialect="tsql")
        replacement.set("with_" if "with_" in replacement.arg_types else "with", tree.args.get("with_") or tree.args.get("with"))
        tree = replacement
    still = [v for v in rolemap.all_views() if v in _reads(tree, set(rolemap.all_views()))]
    added, part2_tables = [], []
    for view in still:
        if view not in REACHED:
            raise AuditError(WORDING["unreached"].format(part=rolemap.view_title(view, False)))
        text, order = _reached_cte(schema, view, [c.lower() for c in columns])
        added.append(exp.CTE(this=sqlglot.parse_one(text, dialect="tsql"), alias=exp.TableAlias(this=exp.to_identifier(view))))
        part2_tables += order
    with_ = tree.args.get("with_") or tree.args.get("with")
    with_.set("expressions", added + list(with_.expressions))
    text = tree.sql(dialect="tsql", pretty=True)
    if blank and counts:
        text = _blanked(tree, counts)
    part2 = text.replace("   WITH (NOLOCK)", " WITH (NOLOCK)").rstrip().rstrip(";") + ";"
    coverage, coverage_tables, unmeasured = _coverage(schema, [v for v in still if v in REACHED],
                                                      [c.lower() for c in columns], _large(schema, large))
    return {"count": count, "part1": part1, "coverage": coverage, "coverage_tables": coverage_tables,
            "unmeasured": unmeasured, "part2": part2, "cohort_name": name, "cohort_tables": cohort_tables,
            "part2_tables": list(dict.fromkeys(part2_tables)), "cohort_columns": columns,
            "cohort_conditions": [c.sql(dialect="tsql") for c in policy._conjuncts(body.args["where"].this)],
            "cohort_views": views_one, "part2_views": still, "counts": counts,
            "columns": list(tree.named_selects)}


def _blanked(tree, counts):
    """The query with its final SELECT read as result by an outer SELECT that leaves blank any count from 1 to 4."""
    key = "with_" if "with_" in tree.arg_types else "with"
    final = tree.copy()
    final.set(key, None)
    inner, outer = blanking(final, counts)
    parts = [cte.sql(dialect="tsql", pretty=True) for cte in tree.args[key].expressions]
    parts.append("result AS (\n" + "\n".join("  " + line for line in inner.splitlines()) + "\n)")
    return "WITH " + ",\n".join(parts) + "\n" + outer


def script(compiled, meta, blank=True):
    """The whole text of query.sql."""
    lines = []
    if meta.get("question"):
        lines.append(_wrapped(meta["question"]))
    lines += [_wrapped(WORDING["written"].format(**meta)), _wrapped(WORDING["timeout"]), _wrapped(WORDING["order"]),
              _wrapped(WORDING["session"])]
    lines += list(SESSION)
    lines += ["", _wrapped(WORDING["series"]), "",
              _wrapped(WORDING["part1"].format(cap=f"{CAP:,}", start=meta["start"], end=meta["end"],
                                               tables=_and(compiled["cohort_tables"]))),
              _wrapped(WORDING["limit"].format(cap=f"{CAP:,}")), DROP, compiled["part1"], "",
              _wrapped(WORDING["count"].format(start=meta["start"], end=meta["end"])), SERIES_MARKERS[0],
              compiled["count"], ""]
    if compiled["coverage_tables"]:
        lines.append(_wrapped(WORDING["coverage"].format(tables=_and(compiled["coverage_tables"]))))
    else:
        lines.append(_wrapped(WORDING["coverage_none"]))
    if compiled["unmeasured"]:
        lines.append(_wrapped(WORDING["unmeasured"].format(parts=_and(rolemap.view_title(v, False) for v in compiled["unmeasured"]))))
    lines += [SERIES_MARKERS[1], compiled["coverage"], ""]
    lines.append(_wrapped(WORDING["part2"].format(tables=_and(compiled["part2_tables"])) if compiled["part2_tables"]
                          else WORDING["part2_small"]))
    lines.append(_wrapped(WORDING["blank"] if blank else WORDING["exact"]))
    lines += [SERIES_MARKERS[2], compiled["part2"], "", _wrapped(WORDING["end"]), "DROP TABLE #cohort;"]
    return "\n".join(lines) + "\n"


def part2_text(sql):
    """The text of part 2 within query.sql: from the comment on part 2 to the semicolon that ends it."""
    statements, _ = policy.split(sql)
    found = []
    for text in statements:
        if not text.lstrip().upper().startswith("WITH"):
            continue
        try:
            tree = sqlglot.parse_one(text, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        if isinstance(tree, exp.Query) and tree.args.get("into") is None and any(policy._is_temp(t) for t in tree.find_all(exp.Table)):
            found.append(text)
    return found[-1] if found else ""


# The specification.

SPEC_WORDING = {
    "title": "# Specification of the audit script",
    "inside": "This specification names the tables and local codes of the hospital's database, so it is for use inside the hospital only.",
    "question": "The audit asks: {question}",
    "h_reads": "## What the script reads",
    "reads1": "Part 1 reads {tables}.",
    "reads2": "Part 2 reaches {tables} from #cohort by their keys.",
    "sizes": "The tables and columns query gave these sizes, and the policy treats a table of {large} rows or more, or one whose size is not known, as large:",
    "size": "{table} holds {rows}.",
    "size_unknown": "{table} has no recorded size, so the policy treats it as large.",
    "h_parts": "## Where each part of the record comes from",
    "part": "The part of the record for {title} comes from {tables}, where its columns are as follows.",
    "column": "{column} is {expression}.",
    "h_cohort": "## The cohort",
    "cohort": "Part 1 keeps the anaesthetics that the audit's step {name} chooses, and that started within the period. It keeps at most {cap}, the earliest first. The step's conditions, over the parts of the record listed above, are these:",
    "h_period": "## The period",
    "period": "The period runs from {start} to {end}, both days included, and an anaesthetic belongs to it when it starts within it. The script compares the start with {start} and with the day after {end}.",
    "h_decisions": "## The decisions",
    "decision": "{about}: {decision}",
    "decision_by": "{about}: {decision} ({by}, {date})",
    "no_decisions": "No decision was given with the audit.",
    "h_counted": "## What is counted, and how it is rounded",
    "columns": "The result has the columns {columns}, in that order.",
    "counts": "The columns {columns} are counts.",
    "blank": "The result leaves blank any count from 1 to 4, so that no small number can point to a child. The counts are otherwise exact, and the audit's approval would have to allow exact small numbers for them to be kept.",
    "exact": "The audit's approval allows exact small numbers, so the result keeps every count as it is.",
    "limit": "Part 1 also returns cohort_reached_the_limit, which is 1 when #cohort holds {cap} anaesthetics and 0 otherwise, and no other figure.",
}


def specification(schema, compiled, meta, decisions, blank):
    w = SPEC_WORDING
    lines = [w["title"], "", w["inside"], ""]
    if meta.get("question"):
        lines += [w["question"].format(question=meta["question"]), ""]
    sizes = schema.sitting.sizes
    lines += [w["h_reads"], "", w["reads1"].format(tables=_and(compiled["cohort_tables"]))]
    if compiled["part2_tables"]:
        lines.append(w["reads2"].format(tables=_and(compiled["part2_tables"])))
    lines += ["", w["sizes"].format(large=f"{meta['large']:,}"), ""]
    for table in dict.fromkeys(compiled["cohort_tables"] + compiled["part2_tables"]):
        rows = sizes.get(table.upper())
        lines.append("- " + (w["size"].format(table=table, rows=f"{rows:,} rows") if rows is not None
                             else w["size_unknown"].format(table=table)))
    lines += ["", w["h_parts"], ""]
    tables = schema_tables(schema)
    for view in dict.fromkeys(compiled["cohort_views"] + compiled["part2_views"]):
        lines += [w["part"].format(title=rolemap.view_title(view, False), tables=_and(tables.get(view, []))), ""]
        for column, expression in _columns_from(schema, view).items():
            lines.append(f"- {w['column'].format(column=column, expression='`' + expression + '`')}")
        lines.append("")
    lines += [w["h_cohort"], "", w["cohort"].format(name=compiled["cohort_name"], cap=f"{CAP:,}"), ""]
    lines += [f"- `{c}`" for c in compiled["cohort_conditions"]]
    lines += ["", w["h_period"], "", w["period"].format(start=meta["start"], end=meta["end"]), "", w["h_decisions"], ""]
    if decisions.get("decisions"):
        for item in decisions["decisions"]:
            key = "decision_by" if item.get("by") and item.get("date") else "decision"
            lines.append("- " + w[key].format(about=item.get("about", ""), decision=item.get("decision", ""),
                                              by=item.get("by", ""), date=item.get("date", "")))
    else:
        lines.append(w["no_decisions"])
    lines += ["", w["h_counted"], "", w["columns"].format(columns=_and(compiled["columns"]))]
    if compiled["counts"]:
        lines.append(w["counts"].format(columns=_and(compiled["counts"])))
    lines += [w["blank"] if blank else w["exact"], w["limit"].format(cap=f"{CAP:,}")]
    return "\n".join(lines) + "\n"


# The expected output on made-up rows.

# The most rows of the answer on made-up rows that expected-output.json keeps; rows_total says how many there were.
EXPECTED_ROWS = 1000


def expected_output(audit_sql, blank):
    """The audit run on the role-level shadow with the planted cases."""
    con = rolemap.role_shadow()
    try:
        run = rolemap.duckdb_runner(con)
        per = None
        try:
            found = rolemap.result(run, audit_sql, blank=blank)
        except rolemap.MapError:
            # A question other than the neonatal audit has no bands to read by year, so its rows are read as they are,
            # and the planted neonates, whose expected answers are the neonatal audit's, do not apply to it.
            columns, rows = run(audit_sql)
            found = {"columns": columns, "rows": rows, "coverage": None, "notes": []}
        else:
            try:
                per = rolemap.per_anaesthetic(run, audit_sql)
            except rolemap.MapError:
                per = None
    finally:
        con.close()
    cases = []
    if per is not None:
        # The expected answers are held out of the public workspace, where the planted rows come without them.
        for case in rolemap.planted().get("expectations", []):
            held = per.get(case["anaesthetic_key"])
            if not case["counted"]:
                ok = held is None
            else:
                ok = held is not None and held[1] == case["died_within_90_days"] and (
                    (held[0] is None and case["minutes_below_40"] is None)
                    or (held[0] is not None and case["minutes_below_40"] is not None and abs(held[0] - case["minutes_below_40"]) < 1e-6))
            cases.append({"anaesthetic_key": case["anaesthetic_key"], "says": case["says"], "counted": case["counted"],
                          "expected": {"minutes_below_40": case["minutes_below_40"], "died_within_90_days": case["died_within_90_days"]},
                          "found": None if held is None else {"minutes_below_40": held[0], "died_within_90_days": held[1]},
                          "matches": ok})
    return {"made_up": True,
            "says": "These figures come from made-up rows: the role-level shadow, with generated anaesthetics from 2019 to 2025 and the planted cases. They show the shape of the answer and nothing about the hospital. The period of the package is not applied to them, and their counts are not blanked, because no row belongs to a child.",
            "columns": found["columns"], "rows": [list(r) for r in found["rows"][:EXPECTED_ROWS]],
            "rows_total": len(found["rows"]), "coverage": found["coverage"],
            "notes": found["notes"], "planted": cases, "planted_match": all(c["matches"] for c in cases) if cases else None}


# The package.

NOT_APPROVED = "not approved"
APPROVAL_STATES = (NOT_APPROVED, "approved", "refused")
NOT_RECORDED = "not recorded"
# The files of a package whose hashes the manifest records and status() rechecks. plan.sqlplan joins them once a plan
# has been reviewed. README.md is left out, because Schemalyser writes it again whenever the package's state moves.
HASHED = ("query.sql", "question.sql", "decisions.json", "feasibility.json", "expected-output.json", "safety-report.json",
          "specification.md")
PLAN_FILE = "plan.sqlplan"
CORRECTNESS = "expected-output.json"
APPROVE = "python -m schemalyser.audit approve {folder} --by NAME [--refuse] [--note TEXT] [--date YYYY-MM-DD]"

INPUT_NAMES = {"schema": "the hospital schema", "contract": "the role contract", "policy": "the policy's version",
               "plan": "the plan review, which was made for another text of query.sql"}

MANIFEST_WORDING = {
    "permissions": "The account that runs the script needs SELECT on each table in select_on and nothing more, with the right that every account has to make a temporary table in tempdb, which #cohort uses.",
    "time_limit": "The database analyst sets a time limit in Management Studio, because the script cannot set one.",
    "assumes": ["The script runs on a reporting replica, or out of hours, rather than on the live clinical database.",
                "The account is restricted to reading the tables in select_on.",
                "Resource Governor caps the account's memory and CPU where the edition of SQL Server has it.",
                "The script does not run during the nightly load, because every read still holds a schema lock."],
    "approval": "No one has yet recorded approval or refusal. The database analyst records either with the command below.",
    "voided": "A change to {changed} after the package was built has returned the approval to not approved and voided the plan review.",
    "approved": "{by} approved the package on {date}.",
    "refused": "{by} refused the package on {date}.",
}


def policy_version():
    """The version of the policy that derives the class, or "not recorded" where the policy states none."""
    return str(getattr(policy, "POLICY_VERSION", NOT_RECORDED))


def _file_hashes(folder, names):
    found = {}
    for name in names:
        path = Path(folder) / name
        found[name] = sha256(path.read_bytes()) if path.is_file() else None
    return found


def _contract_record(views):
    model = rolemap.contract()
    hashes = rolemap.part_hashes(model)
    return {"version": model.get("version"), "part_hashes": {v: hashes.get(v) for v in sorted(views)}}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _decisions(path):
    if path is None:
        return {"decisions": [], "exact_small_numbers": False}
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("decisions", []), list):
            raise ValueError
    except (OSError, ValueError):
        raise AuditError(WORDING["decisions"].format(name=path.name)) from None
    data.setdefault("decisions", [])
    data["exact_small_numbers"] = bool(data.get("exact_small_numbers"))
    return data


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n"


def _approval(folder_name):
    return {"state": NOT_APPROVED, "by": None, "date": None, "note": None, "inputs_sha256": None,
            "says": MANIFEST_WORDING["approval"], "command": APPROVE.format(folder=folder_name)}


def build(schema_path, query_path, out, period=None, decisions_path=None, large=policy.LARGE, date=None, extra=None):
    """Writes the execution package to out. Returns the manifest. Raises AuditError where it cannot be written. extra,
    when given, is merged into the manifest, as the export of a specification adds its own record."""
    schema_path, query_path, out = Path(schema_path), Path(query_path), Path(out)
    date = date or dt.date.today().isoformat()
    schema = feasibility.Schema.load(schema_path)
    audit_sql = query_path.read_text(encoding="utf-8")
    decisions = _decisions(decisions_path)
    blank = not decisions["exact_small_numbers"]
    out.mkdir(parents=True, exist_ok=True)
    found = feasibility.assess(schema, audit_sql, query_path.name)
    (out / "feasibility.md").write_text(feasibility.markdown(found), encoding="utf-8")
    (out / "feasibility.json").write_text(feasibility.to_json(found), encoding="utf-8")
    if found["verdict"] == "model":
        raise AuditError(WORDING["model"].format(folder=out.name))
    try:
        start, end = _period(*period) if period else (dt.date(schema.year, 1, 1), dt.date(schema.year, 12, 31))
    except (TypeError, ValueError):
        raise AuditError(WORDING["period"]) from None
    compiled = compile_audit(schema, audit_sql, start, end, blank, large=large)
    meta = {"date": _day(date), "query": query_path.name, "schema": schema_path.name, "question": found.get("question"),
            "start": start.isoformat(), "end": end.isoformat(), "large": large}
    text = script(compiled, meta, blank)
    (out / "query.sql").write_text(text, encoding="utf-8")
    (out / "question.sql").write_text(audit_sql, encoding="utf-8")
    (out / "decisions.json").write_text(_json(decisions), encoding="utf-8")
    (out / "specification.md").write_text(specification(schema, compiled, meta, decisions, blank), encoding="utf-8")
    tables = {t for ts in schema_tables(schema).values() for t in ts}
    safety = policy.check(text, tables, schema.sitting.sizes, large=large)
    safety["sql_sha256"] = sha256(text)
    (out / "safety-report.json").write_text(_json(safety), encoding="utf-8")
    expected = expected_output(audit_sql, blank)
    (out / CORRECTNESS).write_text(_json(expected), encoding="utf-8")
    views = list(dict.fromkeys(compiled["cohort_views"] + compiled["part2_views"]))
    contract = _contract_record(views)
    schema_sha = sha256(schema_path.read_bytes()) if schema_path.is_file() else None
    counts_only = next((r["passed"] for r in safety["rules"] if r["id"] == "counts_only"), None)
    inputs = {"files": _file_hashes(out, HASHED), "schema": {"sha256": schema_sha, "schema_id": schema.schema_id},
              "contract": contract, "policy_version": policy_version()}
    manifest = {
        "tool": TOOL, "tool_version": tool_version(), "date": date,
        "schema_file": {"name": schema_path.name, "sha256": schema_sha, "schema_id": schema.schema_id,
                        "readiness": schema.readiness},
        "role_model_version": contract["version"], "contract": contract,
        "query": {"name": query_path.name, "sha256": sha256(audit_sql), "question": found.get("question")},
        "sql_sha256": sha256(text),
        "cohort": {"step": compiled["cohort_name"], "cap": CAP, "columns": compiled["cohort_columns"],
                   "conditions": compiled["cohort_conditions"], "parts": compiled["cohort_views"]},
        "period": {"from": start.isoformat(), "to": end.isoformat()}, "decisions": decisions,
        "execution_class": safety["execution_class"], "policy_outcome": safety["outcome"],
        "policy_version": policy_version(),
        "feasibility_verdict": found["verdict"], "planted_match": expected["planted_match"],
        "permissions": {"select_on": sorted(safety.get("tables_read", [])), "temporary_tables": ["#cohort"],
                        "says": MANIFEST_WORDING["permissions"]},
        "expected_output": {"columns": compiled["columns"], "counts": compiled["counts"], "counts_only": counts_only,
                            "rows_on_made_up_rows": expected["rows_total"],
                            "series": {"count": 1, "coverage": 1, "rows": "the result's own rows"},
                            "at_most_anaesthetics": CAP},
        "resources": {"cohort_cap": CAP, "large_table_rows": large, "large_tables": safety["large_tables"],
                      "lock_timeout_ms": 10000, "deadlock_priority": "low", "time_limit": MANIFEST_WORDING["time_limit"],
                      "assumes": MANIFEST_WORDING["assumes"]},
        "plan_review": {"state": NOT_REVIEWED, "plan_sha256": None},
        "correctness_report": {"file": CORRECTNESS, "sha256": inputs["files"][CORRECTNESS],
                               "planted_match": expected["planted_match"]},
        "inputs": inputs, "approval": _approval(out.name),
        "large_tables": safety["large_tables"], "large_table_rows": large, "cohort_cap": CAP,
    }
    if extra:
        manifest.update(extra)
    (out / MANIFEST).write_text(_json(manifest), encoding="utf-8")
    (out / "README.md").write_text(readme(manifest, found, expected, safety), encoding="utf-8")
    return manifest


def readme(manifest, found, expected, safety, plan_paragraph=None):
    w = README_WORDING
    lines = [w["title"], "", w["inside"], "",
             w["what"].format(date=_day(manifest["date"]), query=manifest["query"]["name"], schema=manifest["schema_file"]["name"]),
             "", w["h_reports"], "", w["feasibility"].format(verdict=found["verdict_text"]), ""]
    if expected.get("planted_match") is False:
        planted = w["planted_not"].format(count=sum(1 for c in expected["planted"] if not c["matches"]))
    else:
        planted = w["planted_ok"] if expected.get("planted") else ""
    lines += [w["correctness"].format(planted=planted).replace("  ", " "), ""]
    lines.append(w["safety"].format(outcome="passed" if safety["outcome"] == "passed" else "failed"))
    failed = [r["rule"].rstrip(".") for r in safety["rules"] if not r["passed"]]
    if failed:
        lines.append(w["safety_failed"].format(rules="; ".join(f[0].lower() + f[1:] for f in failed)))
    grade = manifest["execution_class"]
    lines += ["", w["h_class"], "", w["class"].format(says=policy.CLASSES[grade]), "", w[grade], "", w["h_plan"], ""]
    lines.append(plan_paragraph or w["plan_none"])
    lines += ["", w["plan_steps"], ""] + [f"{n}. {s}" for n, s in enumerate(w["steps"], 1)] + ["", w["plan_after"], ""]
    approval = manifest.get("approval") or {}
    lines += [w["h_approval"], "", w["approval"].format(command=approval.get("command") or APPROVE.format(folder="FOLDER")), ""]
    if approval.get("state") in ("approved", "refused"):
        lines += [MANIFEST_WORDING[approval["state"]].format(by=approval["by"], date=_day(approval["date"])), ""]
    lines += [w["h_void"], "", w["void"], "", w["h_session"], "", w["session"], "", w["h_files"], ""]
    lines += [f"- {f}" for f in w["files"]]
    return "\n".join(lines) + "\n"


def _rewrite_readme(folder, manifest, paragraph=None):
    folder = Path(folder)
    found = json.loads((folder / "feasibility.json").read_text(encoding="utf-8"))
    expected = json.loads((folder / CORRECTNESS).read_text(encoding="utf-8"))
    safety = json.loads((folder / "safety-report.json").read_text(encoding="utf-8"))
    if paragraph is None and (folder / "plan-review.json").is_file() and manifest["plan_review"].get("plan_sha256"):
        paragraph = json.loads((folder / "plan-review.json").read_text(encoding="utf-8")).get("paragraph")
    (folder / "README.md").write_text(readme(manifest, found, expected, safety, paragraph), encoding="utf-8")


def review_plan(folder, plan_path, date=None):
    """Reads an estimated plan for the package in folder, keeps a copy of it as plan.sqlplan, and writes
    plan-review.json, the manifest and README. A new review returns the approval to not approved, because the
    approval was given for what the package then held."""
    from . import plan as planning
    folder, plan_path = Path(folder), Path(plan_path)
    standing = status(folder)
    if standing["voided"]:
        raise AuditError(WORDING["changed"])
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    text = (folder / "query.sql").read_text(encoding="utf-8")
    data = plan_path.read_bytes()
    review = planning.review(data, manifest["large_tables"], part2_text(text))
    review.update({"plan_file": plan_path.name, "plan_sha256": sha256(data), "sql_sha256": manifest["sql_sha256"],
                   "date": date or dt.date.today().isoformat()})
    review["paragraph"] = planning.paragraph(review)
    if plan_path.resolve() != (folder / PLAN_FILE).resolve():
        (folder / PLAN_FILE).write_bytes(data)
    (folder / "plan-review.json").write_text(_json(review), encoding="utf-8")
    manifest["plan_review"] = {"state": review["state"], "plan_sha256": review["plan_sha256"],
                               "sql_sha256": review["sql_sha256"], "date": review["date"]}
    if "inputs" in manifest:
        manifest["inputs"]["files"][PLAN_FILE] = review["plan_sha256"]
    manifest["approval"] = _approval(folder.name)
    (folder / MANIFEST).write_text(_json(manifest), encoding="utf-8")
    _rewrite_readme(folder, manifest, review["paragraph"])
    return review


def _changes(folder, manifest, schema_path=None):
    """What has changed since the package was built, as a list of the names of the inputs whose hashes differ."""
    inputs = manifest.get("inputs")
    query = folder / "query.sql"
    current = sha256(query.read_bytes()) if query.is_file() else None
    changed = []
    if inputs is None:
        # A package built before the manifest recorded its inputs: only query.sql can be rechecked.
        return (["query.sql"] if current != manifest["sql_sha256"] else []), current
    files = inputs.get("files", {})
    now = _file_hashes(folder, files)
    changed += [name for name in files if now[name] != files[name]]
    if (folder / PLAN_FILE).is_file() and PLAN_FILE not in files:
        changed.append(PLAN_FILE)
    if current != manifest["sql_sha256"] and "query.sql" not in changed:
        changed.append("query.sql")
    recorded = inputs.get("contract") or {}
    if _contract_record(list(recorded.get("part_hashes", {}))) != recorded:
        changed.append(INPUT_NAMES["contract"])
    if inputs.get("policy_version") != policy_version():
        changed.append(INPUT_NAMES["policy"])
    if schema_path is not None:
        path = Path(schema_path)
        if not path.is_file() or sha256(path.read_bytes()) != (inputs.get("schema") or {}).get("sha256"):
            changed.append(INPUT_NAMES["schema"])
    review = manifest.get("plan_review") or {}
    if review.get("sql_sha256") not in (None, current) and INPUT_NAMES["plan"] not in changed:
        changed.append(INPUT_NAMES["plan"])
    return changed, current


def status(folder, schema_path=None):
    """Whether a package still stands as it was built. Every hashed input is rechecked: each file that the manifest
    records, the plan that was reviewed, the parts of the role contract that the question reads, the policy's version,
    and, when schema_path is given, the saved hospital schema. A change to any of them voids the plan review and the
    class, and returns the approval to not approved, which is written back into the manifest so that no approval
    outlives what it approved. Returns {"changed", "sql_changed", "execution_class", "plan_review", "approval",
    "voided", "says"}."""
    folder = Path(folder)
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    changed, _ = _changes(folder, manifest, schema_path)
    voided = bool(changed)
    approval = manifest.get("approval") or _approval(folder.name)
    if approval.get("state") != NOT_APPROVED and approval.get("inputs_sha256") != _digest(manifest.get("inputs")):
        voided, changed = True, changed or ["the inputs that the approval recorded"]
    review = manifest.get("plan_review") or {"state": NOT_REVIEWED}
    if voided:
        says = MANIFEST_WORDING["voided"].format(changed=_and(changed))
        moved = approval.get("state") != NOT_APPROVED or review.get("state") not in (NOT_REVIEWED, "voided")
        if moved:
            manifest["approval"] = dict(_approval(folder.name), voided={"date": dt.date.today().isoformat(),
                                                                          "changed": changed, "says": says})
            manifest["plan_review"] = dict(review, state="voided")
            (folder / MANIFEST).write_text(_json(manifest), encoding="utf-8")
        approval, review = manifest["approval"], manifest["plan_review"]
    return {"changed": changed, "sql_changed": "query.sql" in changed, "execution_class": manifest["execution_class"],
            "plan_review": review["state"], "approval": approval["state"], "voided": voided,
            "says": WORDING["changed"] if voided else None}


def approve(folder, by, refuse=False, note="", date=None, schema_path=None):
    """Records the database analyst's approval or refusal in the manifest, with who gave it and when, bound to the
    hashes of every input that the manifest records. Raises AuditError where the package has changed since it was
    built, where no one is named, or where an approval is asked of a script of class D."""
    folder = Path(folder)
    by = " ".join((by or "").split())
    if not by:
        raise AuditError(WORDING["approver"])
    standing = status(folder, schema_path)
    if standing["voided"]:
        raise AuditError(WORDING["changed"])
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    if not refuse and manifest["execution_class"] == "D":
        raise AuditError(WORDING["class_d"])
    date = date or dt.date.today().isoformat()
    manifest["approval"] = {"state": "refused" if refuse else "approved", "by": by, "date": date, "note": note or None,
                            "inputs_sha256": _digest(manifest.get("inputs")), "sql_sha256": manifest["sql_sha256"],
                            "execution_class": manifest["execution_class"],
                            "plan_review": (manifest.get("plan_review") or {}).get("state"),
                            "says": MANIFEST_WORDING["refused" if refuse else "approved"].format(by=by, date=_day(date)),
                            "command": APPROVE.format(folder=folder.name)}
    (folder / MANIFEST).write_text(_json(manifest), encoding="utf-8")
    _rewrite_readme(folder, manifest)
    return manifest["approval"]


# The export of a specification: each section through the existing path to a package of its own.

EXPORT = "export.json"


def build_export(schema_path, spec_path, episodes_path, out, period=None, decisions_path=None, date=None):
    """Compiles a specification with its private episode list into statements over the roles, then takes each one
    through the role policy, the feasibility report and the compilation through the hospital schema to a package of
    its own in out/sections/NAME/. Writes out/compiled/ and out/export.json, and returns the export's record. A
    section that the role policy refuses, or that the catalogue declares without SQL, is recorded with its reason and
    has no package."""
    from . import rolepolicy, specification as specs
    schema_path, out = Path(schema_path), Path(out)
    date = date or dt.date.today().isoformat()
    try:
        spec, spec_sha = specs.load(spec_path)
        episodes = specs.read_episodes(episodes_path, spec["episodes"]["form"])
        compiled = specs.compile(spec, episodes, spec_sha)
    except specs.SpecificationError as error:
        raise AuditError(str(error)) from None
    out.mkdir(parents=True, exist_ok=True)
    specs.write(compiled, out / "compiled")
    period = period or compiled["period"]
    classes = {entry["name"]: entry["output_class"] for entry in compiled["sections"]}
    record_of_spec = {"format": compiled["format"], "sha256": spec_sha, "title": compiled["title"],
                      "contract_version": compiled["contract_version"], "output": compiled["output"],
                      "output_classes": classes}
    record_of_episodes = compiled["episodes"]
    sections = []
    for entry in compiled["sections"]:
        record = {"name": entry["name"], "kind": entry["kind"], "output_class": entry["output_class"],
                  "leaves": entry["leaves"]}
        if entry["sql"] is None:
            record.update(outcome="declared without SQL", says=entry["says"])
            sections.append(record)
            continue
        query = out / "compiled" / "sections" / f"{entry['name']}.sql"
        checked = rolepolicy.check(query.read_text(encoding="utf-8"))
        if checked["outcome"] != "passed":
            record.update(outcome="refused by the role policy", rules_failed=checked["failed"])
            sections.append(record)
            continue
        extra = {"specification": dict(record_of_spec, section=entry["name"], kind=entry["kind"],
                                       output_class=entry["output_class"], leaves=entry["leaves"]),
                 "episodes": record_of_episodes}
        try:
            manifest = build(schema_path, query, out / "sections" / entry["name"], period, decisions_path, date=date,
                             extra=extra)
        except AuditError as error:
            record.update(outcome="not packaged", says=str(error))
        else:
            record.update(outcome="packaged", folder=f"sections/{entry['name']}",
                          execution_class=manifest["execution_class"], sql_sha256=manifest["sql_sha256"])
        sections.append(record)
    found = {"tool": TOOL, "tool_version": tool_version(), "date": date, "specification": record_of_spec,
             "episodes": record_of_episodes, "schema_file": {"name": schema_path.name,
                                                            "sha256": sha256(schema_path.read_bytes())},
             "period": {"from": period[0], "to": period[1]} if period else None, "sections": sections}
    (out / EXPORT).write_text(_json(found), encoding="utf-8")
    return found


# The form that a question needs for the two-part script, judged from its text and the public contract alone.

PUBLIC_FORM = {
    "form": "The question is one SELECT over the role views.",
    "cohort": "One step of the question chooses its anaesthetics from the patients and the anaesthetics alone.",
    "reached": "Every further part that the question reads is reached from that step by a key the step carries.",
    "counts": "The final SELECT returns counts or other aggregates, grouped where it groups, and no row of a patient.",
}


def _aggregated(node):
    return any(not isinstance(f.parent, exp.Window) for f in node.find_all(exp.AggFunc))


def public_form(sql):
    """The rules of PUBLIC_FORM that a question breaks, as a list of their ids, empty when it keeps them all. The
    answer depends on the question and the role contract only, never on any hospital schema, so that the status it
    gives is the same whichever hospital the project holds."""
    try:
        tree = rolemap.check_audit(sql).copy()
    except Exception:  # noqa: BLE001 - a question that is not one SELECT over the roles does not have the form
        return ["form"]
    broken = []
    try:
        cohort = _cohort_cte(tree)
    except AuditError:
        return ["cohort"]
    carried = {c.lower() for c in cohort.this.named_selects}
    inside = {id(t) for t in cohort.this.find_all(exp.Table)}
    for table in tree.find_all(exp.Table):
        view = table.name.lower()
        if id(table) in inside or view not in rolemap.all_views():
            continue
        if view not in REACHED or REACHED[view][1] not in carried:
            broken.append("reached")
            break
    final = tree if isinstance(tree, exp.Select) else None
    if final is None:
        broken.append("counts")
    else:
        grouped = {g.sql(dialect="tsql").lower() for g in (final.args.get("group") or exp.Group()).expressions}
        for projection in final.expressions:
            value = projection.unalias()
            if _aggregated(value) or isinstance(value, exp.Literal):
                continue
            if value.sql(dialect="tsql").lower() not in grouped:
                broken.append("counts")
                break
    return broken


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.audit",
                                     description="The execution package of an audit over the parts of the record.")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("build", help="Write the execution package.")
    one.add_argument("schema")
    one.add_argument("query")
    one.add_argument("--out", required=True)
    one.add_argument("--period", nargs=2, metavar=("FROM", "TO"))
    one.add_argument("--decisions")
    one.add_argument("--large", type=int, default=policy.LARGE)
    two = commands.add_parser("plan", help="Review an estimated plan of part 2.")
    two.add_argument("folder")
    two.add_argument("plan")
    three = commands.add_parser("status", help="Say, as JSON, whether the package still stands as it was built.")
    three.add_argument("folder")
    three.add_argument("--schema", help="the saved hospital schema, whose hash is rechecked as well")
    four = commands.add_parser("approve", help="Record the database analyst's approval or refusal in the manifest.")
    four.add_argument("folder")
    four.add_argument("--by", required=True, help="the name of the person who approves or refuses")
    four.add_argument("--refuse", action="store_true")
    four.add_argument("--note", default="")
    four.add_argument("--date")
    four.add_argument("--schema", help="the saved hospital schema, whose hash is rechecked before the approval")
    five = commands.add_parser("export", help="Write the packages of a specification's sections for a list of episodes.")
    five.add_argument("schema")
    five.add_argument("specification")
    five.add_argument("--episodes", required=True)
    five.add_argument("--out", required=True)
    five.add_argument("--period", nargs=2, metavar=("FROM", "TO"))
    five.add_argument("--decisions")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            manifest = build(args.schema, args.query, args.out, args.period, args.decisions, args.large)
            print(WORDING["built"].format(folder=Path(args.out).name, grade=manifest["execution_class"],
                                          outcome="passed" if manifest["policy_outcome"] == "passed" else "failed"))
        elif args.command == "status":
            print(_json(status(args.folder, args.schema)), end="")
        elif args.command == "approve":
            found = approve(args.folder, args.by, args.refuse, args.note, args.date, args.schema)
            print(found["says"])
        elif args.command == "export":
            found = build_export(args.schema, args.specification, args.episodes, args.out, args.period, args.decisions)
            print(WORDING["exported"].format(folder=Path(args.out).name, count=len(found["sections"])))
        else:
            review = review_plan(args.folder, args.plan)
            print(WORDING["reviewed"].format(state=review["state"]))
    except (AuditError, feasibility.FeasibilityError, rolemap.MapError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
