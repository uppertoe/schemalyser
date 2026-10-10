"""The execution package of an audit, the static policy on its script and the review of an estimated plan, on the invented
world only.

The saved schema is made as screen 1 makes it, from the invented dictionary, with the table of readings given 400
million rows in the tables and columns query so that the policy treats it as large. The plan of a scan,
plans/scan-of-readings.sqlplan, is an estimated plan that SQL Server 2022 gave on the invented world for a query of the
shape of part 2, with the database's name replaced; the invented world has no indexes, so SQL Server scans the
readings. The plan of a seek, plans/seek-from-cohort.sqlplan, is written by hand.
"""
import copy
import datetime as dt
import shutil
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
import sqlglot

from schemalyser import (audit, compiler, corrections, describe, feasibility, plan, policy, propose, results, rolemap,
                         roleshadow, specification)
from schemalyser.describe import __main__ as describe_main
from schemalyser.translate import to_duckdb
from test_describe import DATE, DICTIONARY, TABLES, tables_result
from test_feasibility import CONFIRMED

PLANS = Path(__file__).parent / "plans"
READINGS = 400_000_000


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    s.set_settings("production", 2024, "Australia/Sydney", True)
    s.tables_query()
    s.read_tables(tables_result({"OBS_READING": READINGS}))
    for about in CONFIRMED:
        s.confirm(about, "yes", date=DATE)
    s.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff"}, DATE)
    path = tmp_path_factory.mktemp("schema") / "hospital-schema.schemalyser.zip"
    path.write_bytes(roleshadow.save_zip(s, DATE))
    return path


@pytest.fixture(scope="module")
def schema(saved):
    return feasibility.Schema.load(saved)


@pytest.fixture(scope="module")
def package(saved, tmp_path_factory):
    out = tmp_path_factory.mktemp("package")
    decisions = out.parent / "decisions.json"
    decisions.write_text(json.dumps({"decisions": [{"about": "The period", "decision": "The audit covers 2024.",
                                                    "by": "the clinician", "date": DATE}]}), encoding="utf-8")
    manifest = audit.build(saved, rolemap.AUDIT, out, ("2024-01-01", "2024-12-31"), decisions, date=DATE)
    return out, manifest


def _policy(schema, sql):
    tables = {t for ts in audit.schema_tables(schema).values() for t in ts}
    return policy.check(sql, tables, schema.sitting.sizes)


def _failed(found):
    return {r["id"] for r in found["rules"] if not r["passed"]}


# The package of the neonatal audit.

def test_the_neonatal_audit_builds_a_package_of_class_b_whose_policy_passes(package):
    out, manifest = package
    assert sorted(p.name for p in out.iterdir()) == ["README.md", "decisions.json", "evidence-requests.json",
                                                     "expected-output.json", "feasibility.json", "feasibility.md",
                                                     "manifest.json", "query.sql", "question.sql", "safety-report.json",
                                                     "specification.md"]
    safety = json.loads((out / "safety-report.json").read_text(encoding="utf-8"))
    assert safety["execution_class"] == "B" and safety["outcome"] == "passed", [r for r in safety["rules"] if not r["passed"]]
    assert safety["large_tables"] == ["OBS_READING"]
    assert manifest["execution_class"] == "B" and manifest["policy_outcome"] == "passed"
    assert manifest["plan_review"] == {"state": audit.NOT_REVIEWED, "plan_sha256": None}
    assert manifest["sql_sha256"] == audit.sha256((out / "query.sql").read_text(encoding="utf-8"))
    assert manifest["query"]["sha256"] == audit.sha256(rolemap.AUDIT.read_text(encoding="utf-8"))
    assert manifest["role_model_version"] == "1.1" and manifest["schema_file"]["sha256"]
    assert manifest["period"] == {"from": "2024-01-01", "to": "2024-12-31"}
    assert manifest["decisions"]["decisions"][0]["about"] == "The period"
    assert manifest["schema_file"]["readiness"]["parts"]["role_reading"]["runs"] == DATE


def test_the_script_is_in_two_parts_with_the_cohort_capped_and_the_readings_reached_from_it(package):
    out, _ = package
    text = (out / "query.sql").read_text(encoding="utf-8")
    part1, part2 = text.split("-- Part 2 ", 1)
    assert "SELECT TOP (5000)" in part1 and "INTO   #cohort" in part1 and "OBS_READING" not in part1
    assert "CAST('2024-01-01' AS DATETIME)" in part1 and "CAST('2025-01-01' AS DATETIME)" in part1
    assert "ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);" in part1
    assert "FROM #cohort AS c\n  JOIN OBS_SHEET AS t1 WITH (NOLOCK)" in part2
    assert "JOIN OBS_READING AS t0 WITH (NOLOCK)\n    ON t0.SHEET_KEY = t1.SHEET_KEY" in part2
    for count in ("anaesthetics", "died_within_90_days", "children", "children_died_within_90_days"):
        assert f"CASE WHEN r.{count} BETWEEN 1 AND 4 THEN NULL ELSE r.{count} END AS {count}" in part2
    assert text.rstrip().endswith("DROP TABLE #cohort;")
    for setting in audit.SESSION:
        assert setting in text
    assert audit.part2_text(text).startswith("WITH role_reading AS (")


def _shadow(schema):
    """A shadow in DuckDB shaped as the hospital schema names the tables, holding the planted role rows, with the role
    views of the schema. Returns (the connexion, its date columns, {view: the view's T-SQL})."""
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
    rows = roleshadow.role_rows(held)
    for name in ["role_patient", "role_anaesthetic"] + [v for v in data["roles"] if v not in ("role_patient", "role_anaesthetic")]:
        for values in rows.get(name, []):
            shadow.place(name, values)
    shadow.pair_encounters()
    con, date_columns = shadow.database()
    return con, date_columns, views


def _two_parts(con, date_columns, compiled):
    """The rows of part 2 once part 1 has filled the cohort, as the script runs them, with #cohort as a table."""
    filling = next(s for s in policy.split(compiled["part1"])[0] if "INTO   #cohort" in s)
    # DuckDB will not mix a text key with the 0 by which SQL Server makes the key fit for a primary key.
    filling = filling.replace("INTO   #cohort\n", "").replace("ISNULL(k.anaesthetic_key, 0)", "k.anaesthetic_key")
    con.execute("CREATE OR REPLACE TEMP TABLE cohort_rehearsal AS " + to_duckdb(filling, date_columns)[0])
    return con.execute(to_duckdb(compiled["part2"].replace("#cohort", "cohort_rehearsal"), date_columns)[0]).fetchall()


PER_ANAESTHETIC = "SELECT x.anaesthetic_key, x.minutes_below_40, x.died FROM banded x"


def test_the_two_part_script_gives_every_planted_case_its_answer_on_a_shadow_of_the_hospitals_tables(schema):
    """The compiled script runs in DuckDB on a shadow shaped as the hospital schema names the tables, with #cohort as a
    temporary table; each planted neonate must give its expected minutes and death."""
    con, date_columns, _ = _shadow(schema)
    compiled = audit.compile_audit(schema, rolemap.AUDIT.read_text(encoding="utf-8"), dt.date(2024, 1, 1), dt.date(2024, 12, 31),
                                   final=PER_ANAESTHETIC)
    found = {str(k): (None if m is None else float(m), int(d)) for k, m, d in _two_parts(con, date_columns, compiled)}
    assert found and roleshadow._neonates(found) == []


@pytest.mark.parametrize("final", [PER_ANAESTHETIC, None])
def test_the_two_part_script_gives_the_same_rows_as_the_audit_as_one_query_over_the_role_views(schema, final):
    """Carried from the earlier design's scripts (B8). The audit, run as one query over the role views of the same
    shadow with the anaesthetics of the period alone, gives the rows that part 1 and part 2 give together, both for
    each anaesthetic and for the audit's own answer with its small counts left as they are."""
    con, date_columns, views = _shadow(schema)
    audit_sql = rolemap.AUDIT.read_text(encoding="utf-8")
    compiled = audit.compile_audit(schema, audit_sql, dt.date(2024, 1, 1), dt.date(2024, 12, 31), blank=False, final=final)
    for name, sql in views.items():
        view = to_duckdb(sql, date_columns)[0]
        if name == "role_anaesthetic":
            view = (f"SELECT * FROM ({view}) AS a WHERE a.start_time >= TIMESTAMP '2024-01-01' "
                    "AND a.start_time < TIMESTAMP '2025-01-01'")
        con.execute(f"CREATE VIEW {name} AS {view}")
    tree = compiler.check_audit(audit_sql)
    if final is not None:
        tree = tree.copy()
        chosen = sqlglot.parse_one(final, dialect="tsql")
        chosen.set("with_" if "with_" in chosen.arg_types else "with", (tree.args.get("with_") or tree.args.get("with")).copy())
        tree = chosen
    single = con.execute(to_duckdb(tree.sql(dialect="tsql"), date_columns)[0]).fetchall()
    two = _two_parts(con, date_columns, compiled)
    assert single and sorted(map(repr, two)) == sorted(map(repr, single))


def test_the_expected_output_comes_from_made_up_rows_with_every_planted_case_matching(package):
    out, _ = package
    expected = json.loads((out / "expected-output.json").read_text(encoding="utf-8"))
    assert expected["made_up"] is True and "made-up rows" in expected["says"]
    assert expected["planted_match"] is True and len(expected["planted"]) == len(roleshadow.planted()["expectations"])
    assert [r[0] for r in expected["rows"]] == ["no mean pressure recorded", "none", "under 5 minutes", "5 to 14 minutes",
                                                "15 minutes or more"]


def test_the_readme_and_specification_name_who_acts_and_keep_to_the_pages_words(package):
    out, _ = package
    readme = (out / "README.md").read_text(encoding="utf-8")
    spec = (out / "specification.md").read_text(encoding="utf-8")
    assert "Class B: bounded validation." in readme and "The plan has not yet been reviewed." in readme
    assert "Display Estimated Execution Plan" in readme and "SET SHOWPLAN_XML ON" in readme and ".sqlplan" in readme
    assert "voids the plan review" in readme and "SET LOCK_TIMEOUT 10000" in readme
    assert "database analyst" in readme and "clinician" in readme
    assert "OBS_READING holds 400,000,000 rows." in spec and "The period runs from 2024-01-01 to 2024-12-31" in spec
    assert "The period: The audit covers 2024. (the clinician, 2026-10-07)" in spec
    for text in (readme, spec):
        assert not re.search(r"\b(map|maps|binding|bindings|view|views)\b", text, re.I)
        prose = "\n".join(line for line in text.splitlines() if not line.startswith("The audit asks:"))
        assert "!" not in prose and "?" not in prose


def test_a_question_that_needs_parts_the_model_does_not_describe_stops_the_build(saved, tmp_path):
    query = tmp_path / "colour.sql"
    query.write_text("-- How many anaesthetics had a colour?\nSELECT COUNT(*) AS anaesthetics FROM role_anaesthetic a "
                     "WHERE a.colour = 1", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="role model does not yet describe"):
        audit.build(saved, query, tmp_path / "out", ("2024-01-01", "2024-12-31"), date=DATE)
    assert (tmp_path / "out" / "feasibility.md").exists() and not (tmp_path / "out" / "query.sql").exists()


# The policy on hand-written scripts.

GOOD = """SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;
SELECT TOP ({top}) ISNULL(a.ANAES_KEY, 0) AS anaesthetic_key, a.ANAES_START_TS AS started
INTO   #cohort
FROM   ANAES_RECORD AS a WITH (NOLOCK)
WHERE  a.ANAES_START_TS >= CAST('2024-01-01' AS datetime)
  AND  a.ANAES_START_TS < CAST('2025-01-01' AS datetime)
ORDER  BY a.ANAES_KEY;
ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);
-- series: count
SELECT COUNT(*) AS anaesthetics FROM #cohort AS c
WHERE  c.started >= CAST('2024-01-01' AS datetime) AND c.started < CAST('2025-01-01' AS datetime);
-- series: coverage
SELECT COUNT(*) AS anaesthetics, COUNT(s.ANAES_KEY) AS with_a_sheet
FROM   #cohort AS c
LEFT JOIN OBS_SHEET AS s WITH (NOLOCK) ON s.ANAES_KEY = c.anaesthetic_key;
-- series: rows
{part2};
DROP TABLE #cohort;
"""
COUNTED = """SELECT COUNT(*) AS readings
FROM   #cohort AS c
JOIN   OBS_SHEET AS s WITH (NOLOCK) ON s.ANAES_KEY = c.anaesthetic_key
JOIN   OBS_READING AS r WITH (NOLOCK) ON r.SHEET_KEY = s.SHEET_KEY"""


def _script(top=5000, part2=COUNTED):
    return GOOD.format(top=top, part2=part2)


def test_a_hand_written_script_of_the_same_form_passes_as_class_b(schema):
    found = _policy(schema, _script())
    assert found["outcome"] == "passed" and found["execution_class"] == "B", _failed(found)


def test_a_cross_join_fails_the_joins_rule_and_is_not_permitted(schema):
    found = _policy(schema, _script(part2=COUNTED + "\nCROSS JOIN ANAES_RECORD AS x"))
    assert "joins" in _failed(found) and found["execution_class"] == "D"
    assert any("CROSS JOIN" in f for f in next(r for r in found["rules"] if r["id"] == "joins")["fragments"])


def test_a_function_outside_the_allowlist_fails_the_functions_rule(schema):
    found = _policy(schema, _script(part2=COUNTED.replace("COUNT(*)", "COUNT(DISTINCT HASHBYTES('SHA2_256', r.READ_VALUE))")))
    assert _failed(found) == {"functions"} and found["execution_class"] == "D"


def test_the_readings_reached_without_the_cohort_fail_and_are_not_permitted(schema):
    found = _policy(schema, _script(part2="SELECT COUNT(*) AS readings FROM OBS_READING AS r WITH (NOLOCK) "
                                          "WHERE r.READ_TS >= CAST('2024-01-01' AS datetime)"))
    assert _failed(found) == {"large_from_cohort"} and found["execution_class"] == "D"


def test_a_cohort_above_the_cap_fails_the_cap_and_is_a_large_extraction(schema):
    found = _policy(schema, _script(top=20000))
    assert _failed(found) == {"cohort_cap"} and found["execution_class"] == "C"
    assert next(r for r in found["rules"] if r["id"] == "cohort_cap")["fragments"] == ["TOP (20000) in the statement that fills #cohort"]


def test_further_shapes_take_their_rule_and_class(schema):
    rows = _policy(schema, _script(part2=COUNTED.replace("COUNT(*) AS readings", "r.READ_VALUE")))
    assert _failed(rows) == {"counts_only"} and rows["execution_class"] == "C"
    unbounded = _policy(schema, _script().replace("WHERE  a.ANAES_START_TS >= CAST('2024-01-01' AS datetime)\n  AND  ", "WHERE  "))
    assert _failed(unbounded) == {"cohort_period"} and unbounded["execution_class"] == "C"
    for sql, rule in (("EXEC sp_executesql N'SELECT 1'", "dynamic"), ("UPDATE ANAES_RECORD SET ANAES_KEY = 1", "statements"),
                      ("SELECT COUNT(*) AS n FROM otherdb.dbo.ANAES_RECORD AS a", "names"),
                      ("SELECT COUNT(*) AS n FROM SECRET_TABLE AS a", "tables"),
                      ("SELECT COUNT(*) AS n FROM ANAES_RECORD a, VISIT v", "joins"),
                      ("SELECT COUNT(*) AS n FROM #cohort AS c JOIN VISIT AS v ON CAST(v.VISIT_KEY AS varchar(20)) = c.anaesthetic_key", "joins"),
                      ("SELECT COUNT(*) AS n FROM OPENQUERY(other, 'SELECT 1')", "dynamic"),
                      ("SELECT 1 FROM ANAES_RECORD WHERE (((", "parse")):
        found = _policy(schema, sql)
        assert rule in _failed(found) and found["execution_class"] == "D", (sql, _failed(found))
    metadata = _policy(schema, "SELECT TABLE_NAME, COUNT(*) AS columns FROM INFORMATION_SCHEMA.COLUMNS GROUP BY TABLE_NAME")
    assert metadata["outcome"] == "passed" and metadata["execution_class"] == "A"


# The review of an estimated plan.

def _copy(package, tmp_path):
    out = tmp_path / "package"
    shutil.copytree(package[0], out)
    return out


def _seek_plan(part2):
    return (PLANS / "seek-from-cohort.sqlplan").read_text(encoding="utf-8").replace(
        "PART_2", escape(part2, {'"': "&quot;", "\n": "&#10;"}))


def test_the_plan_review_rejects_a_scan_of_the_readings_from_sql_server(package, tmp_path):
    data = (PLANS / "scan-of-readings.sqlplan").read_bytes()
    statement = re.search(r'StatementText="([^"]*)"', data.decode()).group(1)
    from xml.sax.saxutils import unescape
    found = plan.review(data, ["OBS_READING"], unescape(statement, {"&#xa;": "\n", "&apos;": "'", "&quot;": '"'}))
    assert found["state"] == "rejected" and found["matches_sql"]
    assert [r["rule"] for r in found["reasons"]] == ["scan"] and found["reasons"][0]["table"] == "OBS_READING"
    assert found["encouraging"] == [] and found["estimate"] is True
    # The same plan given to the package is also not the plan of its part 2.
    out = _copy(package, tmp_path)
    review = audit.review_plan(out, PLANS / "scan-of-readings.sqlplan", date=DATE)
    assert review["state"] == "rejected" and {r["rule"] for r in review["reasons"]} == {"scan", "mismatch"}


def test_the_plan_review_accepts_a_seek_from_the_cohort_and_records_both_hashes(package, tmp_path):
    out = _copy(package, tmp_path)
    text = (out / "query.sql").read_text(encoding="utf-8")
    saved = tmp_path / "plan.sqlplan"
    saved.write_text(_seek_plan(audit.part2_text(text)), encoding="utf-8")
    review = audit.review_plan(out, saved, date=DATE)
    assert review["state"] == "accepted", review["reasons"]
    assert review["encouraging"] == ["OBS_READING"] and review["largest_rows"] == 250000
    assert review["plan_sha256"] == audit.sha256(saved.read_bytes()) and review["sql_sha256"] == audit.sha256(text)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["plan_review"]["state"] == "accepted"
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "found nothing that rejects it" in readme and "encouraging but not conclusive" in readme
    assert "An estimated plan is SQL Server's estimate" in readme


def test_a_plan_without_a_join_predicate_or_with_a_large_grant_is_not_accepted():
    base = _seek_plan("SELECT 1")
    loose = base.replace("<OuterReferences>\n                      <ColumnReference Database=\"[invented_world]\" Schema=\"[dbo]\" "
                         "Table=\"[OBS_SHEET]\" Alias=\"[t1]\" Column=\"SHEET_KEY\"/>\n                    </OuterReferences>", "")
    assert loose != base
    assert plan.review(loose.encode(), ["OBS_READING"], "SELECT 1")["state"] == "rejected"
    greedy = base.replace('SerialDesiredMemory="65536"', 'SerialDesiredMemory="4194304"')
    found = plan.review(greedy.encode(), ["OBS_READING"], "SELECT 1")
    assert found["state"] == "escalate" and found["escalations"][0]["rule"] == "memory"
    assert plan.review(b"not a plan", ["OBS_READING"], "SELECT 1")["state"] == "rejected"


def test_a_change_to_the_script_voids_the_plan_review(saved, tmp_path):
    out = tmp_path / "package"
    audit.build(saved, rolemap.AUDIT, out, ("2024-01-01", "2024-12-31"), date=DATE)
    query = out / "query.sql"
    query.write_text(query.read_text(encoding="utf-8").replace("TOP (5000)", "TOP (50000)"), encoding="utf-8")
    with pytest.raises(audit.AuditError, match="has changed since it was built"):
        audit.review_plan(out, PLANS / "scan-of-readings.sqlplan")


# The series of bounded steps (A17).

def _statements_after(text, marker):
    """The statement that follows a marker line, up to the semicolon that ends it."""
    after = text.split(marker + "\n", 1)[1]
    statements, _ = policy.split(after)
    return statements[0]


def test_part_1_and_part_2_form_the_series_of_a_count_a_coverage_and_the_rows(package, schema):
    out, _ = package
    text = (out / "query.sql").read_text(encoding="utf-8")
    lines = text.splitlines()
    markers = [line for line in lines if line.startswith("-- series:")]
    assert markers == list(audit.SERIES_MARKERS)
    # Each marker is a line of its own, just before its statement.
    for marker in audit.SERIES_MARKERS:
        following = lines[lines.index(marker) + 1]
        assert not following.startswith("--") and following.strip(), (marker, following)
    count = _statements_after(text, "-- series: count")
    assert count.startswith("SELECT COUNT(*) AS anaesthetics_in_period\nFROM   #cohort AS c")
    assert "CAST('2024-01-01' AS datetime)" in count and "CAST('2025-01-01' AS datetime)" in count
    coverage = _statements_after(text, "-- series: coverage")
    assert "FROM   #cohort AS c" in coverage and "JOIN   OBS_SHEET AS t1 WITH (NOLOCK) ON t1.ANAES_KEY = c.anaesthetic_key" in coverage
    assert "COUNT(r1.anaesthetic_key) AS cohort_reaching_reading" in coverage
    rows = _statements_after(text, "-- series: rows")
    assert rows.startswith("WITH role_reading AS (") and rows == audit.part2_text(text)
    # The rows statement is the first to read a large table.
    before = text.split("-- series: rows\n", 1)[0]
    assert "OBS_READING" not in "\n".join(line for line in before.splitlines() if not line.startswith("--"))
    assert "OBS_READING" in rows
    found = _policy(schema, text)
    assert next(r for r in found["rules"] if r["id"] == "series")["passed"], found["rules"]
    assert found["execution_class"] == "B"


def test_the_coverage_step_does_not_follow_a_link_that_begins_at_a_large_table(schema, saved, tmp_path):
    compiled = audit.compile_audit(schema, rolemap.AUDIT.read_text(encoding="utf-8"), dt.date(2024, 1, 1),
                                   dt.date(2024, 12, 31), large=1)
    # With every table counted as large, no link can be measured before the rows are read.
    assert compiled["coverage_tables"] == [] and set(compiled["unmeasured"]) == {"role_reading"}
    assert "OBS" not in compiled["coverage"]


# The manifest, its approval and its voiding (A20).

def test_the_manifest_records_everything_that_the_contract_lists(package):
    out, manifest = package
    assert manifest["sql_sha256"] == audit.sha256((out / "query.sql").read_text(encoding="utf-8"))
    assert manifest["schema_file"]["schema_id"] and manifest["inputs"]["schema"]["sha256"] == manifest["schema_file"]["sha256"]
    assert manifest["contract"]["version"] == "1.1"
    assert set(manifest["contract"]["part_hashes"]) == {"role_anaesthetic", "role_patient", "role_reading"}
    assert manifest["contract"]["part_hashes"]["role_reading"] == rolemap.part_hashes()["role_reading"]
    assert manifest["query"]["question"].startswith("Among neonates") and manifest["cohort"]["cap"] == 5000
    assert manifest["cohort"]["step"] == "neonatal" and manifest["cohort"]["conditions"]
    assert manifest["period"] == {"from": "2024-01-01", "to": "2024-12-31"}
    assert manifest["decisions"]["decisions"][0]["about"] == "The period"
    assert manifest["execution_class"] == "B" and manifest["policy_version"] == str(getattr(policy, "POLICY_VERSION", "not recorded"))
    assert manifest["permissions"]["select_on"] == sorted(json.loads((out / "safety-report.json").read_text())["tables_read"])
    assert "OBS_READING" in manifest["permissions"]["select_on"] and manifest["permissions"]["temporary_tables"] == ["#cohort"]
    assert manifest["expected_output"]["columns"][0] == "minutes_below_40" and manifest["expected_output"]["counts_only"] is True
    assert manifest["expected_output"]["rows_on_made_up_rows"] == 5
    assert manifest["resources"]["cohort_cap"] == 5000 and manifest["resources"]["large_tables"] == ["OBS_READING"]
    assert manifest["resources"]["assumes"]
    assert manifest["plan_review"] == {"state": audit.NOT_REVIEWED, "plan_sha256": None}
    report = manifest["correctness_report"]
    assert report["file"] == "expected-output.json" and report["sha256"] == audit.sha256((out / report["file"]).read_bytes())
    assert manifest["approval"]["state"] == "not approved" and manifest["approval"]["by"] is None
    assert manifest["approval"]["command"].startswith("python -m schemalyser.audit approve ")
    for name in audit.HASHED:
        assert manifest["inputs"]["files"][name] == audit.sha256((out / name).read_bytes()), name


def _approved(package, tmp_path):
    out = _copy(package, tmp_path)
    approval = audit.approve(out, "the database analyst", note="Run on the replica.", date=DATE)
    assert approval["state"] == "approved" and approval["by"] == "the database analyst" and approval["date"] == DATE
    assert audit.status(out)["approval"] == "approved"
    return out


def test_an_approval_is_recorded_with_its_actor_and_date_and_shown_in_the_readme(package, tmp_path):
    out = _approved(package, tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approval"]["inputs_sha256"] and manifest["approval"]["note"] == "Run on the replica."
    assert "the database analyst approved the package on 7 October 2026." in (out / "README.md").read_text(encoding="utf-8")
    with pytest.raises(audit.AuditError, match="names the person"):
        audit.approve(out, "  ")
    refused = audit.approve(out, "the database team", refuse=True, date=DATE)
    assert refused["state"] == "refused" and audit.status(out)["approval"] == "refused"
    assert audit.main(["approve", str(out), "--by", "the database analyst", "--date", DATE]) == 0


@pytest.mark.parametrize("name", audit.HASHED)
def test_a_change_to_any_hashed_file_voids_the_reviews_and_the_approval(package, tmp_path, name):
    out = _approved(package, tmp_path)
    path = out / name
    path.write_bytes(path.read_bytes() + b"\n")
    found = audit.status(out)
    assert found["voided"] and name in found["changed"] and found["approval"] == "not approved"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approval"]["state"] == "not approved" and manifest["approval"]["voided"]["changed"] == found["changed"]
    assert manifest["plan_review"]["state"] == "voided"
    with pytest.raises(audit.AuditError, match="has changed since it was built"):
        audit.approve(out, "the database analyst")


def test_a_plan_that_appears_or_changes_after_the_review_voids_the_approval(package, tmp_path):
    out = _approved(package, tmp_path)
    (out / audit.PLAN_FILE).write_bytes(b"<ShowPlanXML/>")
    assert audit.PLAN_FILE in audit.status(out)["changed"]
    reviewed = _copy(package, tmp_path / "second")
    saved = tmp_path / "plan.sqlplan"
    saved.write_text(_seek_plan(audit.part2_text((reviewed / "query.sql").read_text(encoding="utf-8"))), encoding="utf-8")
    audit.review_plan(reviewed, saved, date=DATE)
    assert (reviewed / audit.PLAN_FILE).read_bytes() == saved.read_bytes() and not audit.status(reviewed)["voided"]
    audit.approve(reviewed, "the database analyst", date=DATE)
    # A second review moves the package on, so the approval given before it no longer stands.
    audit.review_plan(reviewed, saved, date=DATE)
    assert audit.status(reviewed)["approval"] == "not approved"
    (reviewed / audit.PLAN_FILE).write_bytes(b"changed")
    assert audit.PLAN_FILE in audit.status(reviewed)["changed"]


def test_a_change_to_the_contract_the_policy_version_or_the_schema_voids_the_approval(package, tmp_path, monkeypatch, saved):
    out = _approved(package, tmp_path)
    assert not audit.status(out, saved)["voided"]
    other = tmp_path / "other.zip"
    other.write_bytes(saved.read_bytes() + b" ")
    assert "the hospital schema" in audit.status(out, other)["changed"]
    out = _approved(package, tmp_path / "policy")
    monkeypatch.setattr(policy, "POLICY_VERSION", "a later version", raising=False)
    assert "the policy's version" in audit.status(out)["changed"]
    monkeypatch.undo()
    out = _approved(package, tmp_path / "contract")
    hashes = dict(rolemap.part_hashes(), role_reading="changed")
    monkeypatch.setattr(rolemap, "part_hashes", lambda model=None: hashes)
    found = audit.status(out)
    assert "the role contract" in found["changed"] and found["approval"] == "not approved"


def test_a_script_of_class_d_cannot_be_approved_but_can_be_refused(package, tmp_path):
    out = _copy(package, tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["execution_class"] = "D"
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(audit.AuditError, match="class D"):
        audit.approve(out, "the database analyst")
    assert audit.approve(out, "the database analyst", refuse=True)["state"] == "refused"


# The form that the import's status rests on, from the question and the contract alone (A7).

def test_the_public_form_is_judged_from_the_question_alone():
    assert audit.public_form(rolemap.AUDIT.read_text(encoding="utf-8")) == []
    rows = ("WITH chosen AS (SELECT a.anaesthetic_key, a.start_time FROM role_anaesthetic a)\n"
            "SELECT r.value FROM chosen c JOIN role_reading r ON r.anaesthetic_key = c.anaesthetic_key")
    assert audit.public_form(rows) == ["counts"]
    assert audit.public_form(rows.replace("SELECT r.value", "SELECT COUNT(*) AS readings")) == []
    assert audit.public_form("SELECT COUNT(*) AS readings FROM role_reading r") == ["cohort"]
    assert audit.public_form("SELECT 1 FROM ((") == ["form"]


# The export of a specification, through the existing path.

def test_a_specification_is_exported_as_a_package_for_each_section(saved, tmp_path):
    folder = Path(rolemap.MODEL).parents[2] / "fixtures" / "export"
    found = audit.build_export(saved, folder / "neonatal-pressures.specification.json", folder / "invented-episodes.csv",
                               tmp_path / "export", ("2024-01-01", "2024-12-31"), date=DATE)
    outcomes = {s["name"]: s for s in found["sections"]}
    assert outcomes["anaesthetics"]["outcome"] == "packaged" and outcomes["mean_pressures"]["outcome"] == "packaged"
    # Rows of the readings are a large clinical extraction.
    assert outcomes["mean_pressures"]["execution_class"] == "C" and outcomes["episode_key"]["leaves"] is False
    # A derived section has a package once its capability's SQL is written, and is declared without SQL until then.
    for name, capability in (("minutes_below_40", "minutes_beyond_threshold"), ("died_within_90_days", "death_within_days")):
        if (specification.CAPABILITIES / f"{capability}.sql").is_file():
            assert outcomes[name]["outcome"] != "declared without SQL"
        else:
            assert outcomes[name]["outcome"] == "declared without SQL"
    manifest = json.loads((tmp_path / "export" / "sections" / "mean_pressures" / "manifest.json").read_text(encoding="utf-8"))
    spec_sha = audit.sha256((folder / "neonatal-pressures.specification.json").read_bytes())
    assert manifest["specification"]["sha256"] == spec_sha and manifest["specification"]["output_class"] == "rows"
    assert manifest["specification"]["output_classes"]["mean_pressures"] == "rows"
    assert manifest["episodes"] == {"form": "anaesthetic_keys", "count": 6,
                                    "sha256": audit.sha256((folder / "invented-episodes.csv").read_bytes())}
    assert json.loads((tmp_path / "export" / "export.json").read_text(encoding="utf-8"))["specification"]["sha256"] == spec_sha



# The results package (A21).

def test_the_results_package_records_the_approved_query_the_cohort_the_coverage_and_the_disclosure(package, tmp_path):
    out = _approved(package, tmp_path)
    output = tmp_path / "result.csv"
    output.write_text("minutes_below_40,anaesthetics\nnone,120\n", encoding="utf-8")
    reconciliation = tmp_path / "reconciliation.md"
    reconciliation.write_text("Twenty anaesthetics were compared with the chart.\n", encoding="utf-8")
    record = results.write(out, output, tmp_path / "results", reconciliation, ran_by="the database analyst", date=DATE)
    held = json.loads((tmp_path / "results" / "results.json").read_text(encoding="utf-8"))
    assert held == json.loads(json.dumps(record, default=str)) and held["format"] == results.FORMAT
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    query = held["approved_query"]
    assert query["sql_sha256"] == manifest["sql_sha256"] and query["inputs_sha256"] == manifest["approval"]["inputs_sha256"]
    assert query["approval"] == {"by": "the database analyst", "date": DATE} and query["schema"]["schema_id"]
    assert "readiness" not in query["schema"] and query["contract"]["version"] == "1.1"
    assert held["cohort"]["definition"]["step"] == "neonatal" and held["cohort"]["period"] == {"from": "2024-01-01", "to": "2024-12-31"}
    assert held["coverage"]["limitations"] is not None
    disclosure = held["disclosure"]
    assert disclosure["small_counts_blanked"] is True and disclosure["rounded"] is False and disclosure["row_level"] is False
    assert disclosure["protects"] and any("differencing" in line for line in disclosure["does_not_protect"])
    assert held["output"]["sha256"] == audit.sha256(output.read_bytes())
    assert (tmp_path / "results" / held["output"]["file"]).read_bytes() == output.read_bytes()
    assert held["reconciliation"]["state"] == "recorded"
    readme = (tmp_path / "results" / "README.md").read_text(encoding="utf-8")
    assert "stays inside the hospital" in readme and "!" not in readme


def test_no_results_package_is_written_for_a_changed_or_unapproved_package_or_into_a_workspace(package, tmp_path):
    output = tmp_path / "result.csv"
    output.write_text("anaesthetics\n120\n", encoding="utf-8")
    unapproved = _copy(package, tmp_path / "unapproved")
    with pytest.raises(results.ResultsError, match="has not been approved"):
        results.write(unapproved, output, tmp_path / "one")
    approved = _approved(package, tmp_path / "approved")
    workspace_folder = tmp_path / "workspace"
    workspace_folder.mkdir()
    (workspace_folder / "manifest.json").write_text(json.dumps({"profile": "public", "files": []}), encoding="utf-8")
    with pytest.raises(results.ResultsError, match="inside a public workspace"):
        results.write(approved, output, workspace_folder / "results")
    assert not (workspace_folder / "results").exists()
    query = approved / "query.sql"
    query.write_text(query.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(results.ResultsError, match="has changed since it was built"):
        results.write(approved, output, tmp_path / "two")
    assert not (tmp_path / "one").exists() and not (tmp_path / "two").exists()


# The evidence round trip: the plan obtained and the outcome of the production run enter the hospital schema through the
# evidence import, from the requests that the package carries, as the feasibility report's requests do.

def test_the_package_s_requests_take_the_plan_and_the_production_outcome_back_into_the_hospital_schema(package, saved, tmp_path):
    out, manifest = package
    held = json.loads((out / audit.REQUESTS).read_text(encoding="utf-8"))
    assert held["schema_id"] == manifest["schema_file"]["schema_id"]
    requests = {r["form"]: r for r in held["requests"]}
    assert set(requests) == {"plan", "production outcome"}
    for request in requests.values():
        assert request["format"] == describe.REQUEST_FORMAT and request["request_id"] in request["says"]
        assert "python -m schemalyser.describe import-evidence" in request["says"] and "!" not in request["says"]
        assert "covers" not in request
    schema_file = tmp_path / saved.name
    schema_file.write_bytes(saved.read_bytes())
    (tmp_path / "plan.sqlplan").write_text("<ShowPlanXML/>", encoding="utf-8")
    assert describe_main.main(["import-evidence", str(schema_file), str(out / audit.REQUESTS), str(tmp_path / "plan.sqlplan"),
                               "--id", requests["plan"]["request_id"], "--actor", "Dr D", "--out", str(tmp_path / "one")]) == 0
    (first,) = (tmp_path / "one").glob("hospital-schema-*.schemalyser.zip")
    s = describe.Describe()
    s.restore(describe._read_saved(first))
    kept = s.log.latest("evidence imported", form="plan")
    assert kept["payload"]["request_id"] == requests["plan"]["request_id"] and s.texts[kept["payload"]["result"]].strip() == "<ShowPlanXML/>"
    # The outcome is checked against the columns its request expects, and validates no part of the record.
    with pytest.raises(describe.DescribeError, match="does not have the columns"):
        s.import_evidence(requests["production outcome"], "rows\n12\n", "Dr D")
    s.import_evidence(requests["production outcome"], "rows_returned\tseconds\toutcome\n12\t3.5\tcompleted\n", "Dr D")
    outcome = s.log.latest("evidence imported", form="production outcome")
    assert outcome["payload"]["figure"] == [{"rows_returned": 12, "seconds": 3.5, "outcome": "completed"}]
    assert all(record.get("validated") is None for kind in ("bindings", "links", "translations")
               for record in s.dimensions[kind].values())
    # The outcome is evidence on the package, under the hash of its query.sql, and the saved file keeps it.
    (record,) = s.dimensions["packages"][manifest["sql_sha256"]]["production outcome"]
    assert record["figure"] == outcome["payload"]["figure"] and record["by"] == "Dr D" and record["entry"] == outcome["id"]
    again = describe.Describe()
    again.restore(s.folder_files())
    assert again.dimensions["packages"] == s.dimensions["packages"]


def test_a_production_outcome_never_validates_and_reconciles_only_what_holds_no_current_reconciliation(package, saved):
    # Only a clinical reconciliation of a sample against the clinical record, by a person, sets clinically validated. A
    # production outcome whose request covers parts records on them, where nothing reconciles them yet, that the run
    # measured them against the database, with its figure and no judgement.
    out, manifest = package
    held = json.loads((out / audit.REQUESTS).read_text(encoding="utf-8"))
    request = next(r for r in held["requests"] if r["form"] == "production outcome")
    s = describe.Describe()
    s.restore(describe._read_saved(saved))
    parts = ["role_patient", "role_anaesthetic", "role_reading"]
    request = dict(request, covers=s.covered(parts))
    before = {(kind, subject): (s.dimensions[kind].get(subject) or {}).get("reconciled")
              for kind, subject, _ in s._subjects() if request["covers"].get(subject) == s._current(kind, subject)}
    found = s.import_evidence(request, "rows_returned\tseconds\toutcome\n12\t3.5\tcompleted\n", "Dr D")
    assert all(record.get("validated") is None for kind in ("bindings", "links", "translations")
               for record in s.dimensions[kind].values())
    assert all(part["reached"] != describe.VALIDATED for part in s.readiness()["parts"].values())
    changed = 0
    for (kind, subject), earlier in before.items():
        now = (s.dimensions[kind].get(subject) or {}).get("reconciled")
        if earlier and not describe.evidence.stale(earlier, s._current(kind, subject)):
            assert now == earlier
        else:
            changed += 1
            assert now["form"] == "production outcome" and now["judgement"] == describe.NOT_RECORDED
            assert now["entry"] == found["entry"]["id"] and now["figure"][0]["rows_returned"] == 12
    assert changed
