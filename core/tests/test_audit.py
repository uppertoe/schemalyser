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

from schemalyser import audit, corrections, describe, feasibility, plan, policy, propose, rolemap
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
    path.write_bytes(s.save_zip(DATE))
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
    assert sorted(p.name for p in out.iterdir()) == ["README.md", "expected-output.json", "feasibility.json", "feasibility.md",
                                                     "manifest.json", "query.sql", "safety-report.json", "specification.md"]
    safety = json.loads((out / "safety-report.json").read_text(encoding="utf-8"))
    assert safety["execution_class"] == "B" and safety["outcome"] == "passed", [r for r in safety["rules"] if not r["passed"]]
    assert safety["large_tables"] == ["OBS_READING"]
    assert manifest["execution_class"] == "B" and manifest["policy_outcome"] == "passed"
    assert manifest["plan_review"] == {"state": audit.NOT_REVIEWED}
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


def test_the_two_part_script_gives_every_planted_case_its_answer_on_a_shadow_of_the_hospitals_tables(schema):
    """The compiled script runs in DuckDB on a shadow shaped as the hospital schema names the tables, with #cohort as a
    temporary table; each planted neonate must give its expected minutes and death."""
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
    shadow = corrections.Shadow(held, views, data["kinds"], vocabularies)
    shadow.register()
    rows = corrections.role_rows(held)
    for name in ["role_patient", "role_anaesthetic"] + [v for v in data["roles"] if v not in ("role_patient", "role_anaesthetic")]:
        for values in rows.get(name, []):
            shadow.place(name, values)
    shadow.pair_encounters()
    con, date_columns = shadow.database()
    compiled = audit.compile_audit(schema, rolemap.AUDIT.read_text(encoding="utf-8"), dt.date(2024, 1, 1), dt.date(2024, 12, 31),
                                   final="SELECT x.anaesthetic_key, x.minutes_below_40, x.died FROM banded x")
    filling = next(s for s in policy.split(compiled["part1"])[0] if "INTO   #cohort" in s)
    # DuckDB will not mix a text key with the 0 by which SQL Server makes the key fit for a primary key.
    filling = filling.replace("INTO   #cohort\n", "").replace("ISNULL(k.anaesthetic_key, 0)", "k.anaesthetic_key")
    con.execute("CREATE TEMP TABLE cohort_rehearsal AS " + to_duckdb(filling, date_columns)[0])
    got = con.execute(to_duckdb(compiled["part2"].replace("#cohort", "cohort_rehearsal"), date_columns)[0]).fetchall()
    found = {str(k): (None if m is None else float(m), int(d)) for k, m, d in got}
    assert found and corrections._neonates(found) == []


def test_the_expected_output_comes_from_made_up_rows_with_every_planted_case_matching(package):
    out, _ = package
    expected = json.loads((out / "expected-output.json").read_text(encoding="utf-8"))
    assert expected["made_up"] is True and "made-up rows" in expected["says"]
    assert expected["planted_match"] is True and len(expected["planted"]) == len(rolemap.planted()["expectations"])
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
SELECT TOP ({top}) ISNULL(a.ANAES_KEY, 0) AS anaesthetic_key
INTO   #cohort
FROM   ANAES_RECORD AS a WITH (NOLOCK)
WHERE  a.ANAES_START_TS >= CAST('2024-01-01' AS datetime)
  AND  a.ANAES_START_TS < CAST('2025-01-01' AS datetime)
ORDER  BY a.ANAES_KEY;
ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);
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
    with pytest.raises(audit.AuditError, match="has changed since the package was built"):
        audit.review_plan(out, PLANS / "scan-of-readings.sqlplan")
