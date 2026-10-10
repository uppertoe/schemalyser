"""The audit's script on the SQL Server harness (invariant 6): the harness runs a package's query.sql byte for byte on the
synthetic database built from the invented world and writes sqlserver-result.json into the package; the manifest records
that file by its hash, which status() rechecks; and the testbed's package equivalence check runs DuckDB's translated form
of the same text on the same rows and compares the two, naming every rewrite in between.

Every test here but the last runs on DuckDB alone. Each writes the result file as the harness would, from DuckDB's own
answer, so that what is tested is the record and the comparison. The last runs the harness itself on the neonatal
package, and only where SCHEMALYSER_MSSQL_PASSWORD_FILE names the harness's password file; it carries the slow marker.
"""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from schemalyser import audit, convert, harness, rolemap, roleshadow, testbed
from test_invariants import DATE, confirmed

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
ROWS = 50
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The neonatal audit's package, built from the invented hospital schema, beside the sandbox that the harness would
    load into SQL Server: the invented world at a small row count, with its scenarios planted."""
    folder = tmp_path_factory.mktemp("neonatal")
    schema = folder / "hospital-schema.schemalyser.zip"
    schema.write_bytes(roleshadow.save_zip(confirmed(), DATE))
    audit.build(schema, rolemap.AUDIT, folder / "package", ("2024-01-01", "2024-12-31"), date=DATE)
    scenarios = [s["name"] for s in convert.chosen_scenarios(CONVERSION)]
    conversion, _ = convert.run(make_checks.WORLD, CONVERSION, ROWS, scenarios=scenarios)
    return folder / "package", conversion, scenarios


def as_the_harness_writes_it(package, conversion, scenarios, sets=None):
    """sqlserver-result.json as tools/sqlserver/harness.py writes it, with DuckDB's own result sets standing in for
    SQL Server's."""
    text = (package / "query.sql").read_bytes()
    if sets is None:
        sets = harness.package_on_duckdb(conversion.con, text.decode("utf-8"), conversion.sandbox.date_columns,
                                         conversion.sandbox.whole_columns)["result_sets"]
    found = {"format": harness.PACKAGE_RESULT_FORMAT, "file": "query.sql", "sql_sha256": hashlib.sha256(text).hexdigest(),
             "executed_sha256": hashlib.sha256(text).hexdigest(), "sqlserver": "a stand-in for SQL Server",
             "database": "clarity_shadow", "world": "fixtures", "rows": ROWS, "scenarios": scenarios,
             "planted_on_sqlserver": {name: None for name in scenarios},
             "sandbox_sha256": harness.sandbox_digest(conversion.con, conversion.sandbox.tables),
             "ran": f"{DATE}T00:00:00+00:00", "seconds": 0.1, "error": None, "result_sets": sets}
    (package / harness.PACKAGE_RESULT).write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return found


def copied(package, tmp_path):
    target = tmp_path / package.name
    shutil.copytree(package, target)
    return target


def test_the_translated_form_runs_both_parts_in_order_and_names_every_rewrite(built):
    package, conversion, _ = built
    found = harness.package_on_duckdb(conversion.con, (package / "query.sql").read_text(encoding="utf-8"),
                                      conversion.sandbox.date_columns, conversion.sandbox.whole_columns)
    # Part 1's check of the cap, the count and the coverage of the series, then part 2's answer, in order.
    assert [s["columns"][0] for s in found["result_sets"]] == ["cohort_reached_the_limit", "anaesthetics_in_period",
                                                               "cohort_anaesthetics", "minutes_below_40"]
    assert int(found["result_sets"][1]["rows"][0][0]) > 0, "the planted neonates are in the cohort"
    from schemalyser.translate import REWRITES
    assert {"cohort_key_without_isnull", "cohort_primary_key_dropped", "temp_table"} <= set(found["rewrites"])
    assert all(name in REWRITES or name in harness.PACKAGE_REWRITES for name in found["rewrites"])


def test_the_manifest_records_the_result_by_its_hash_and_status_rechecks_it(built, tmp_path):
    package, conversion, scenarios = built
    package = copied(package, tmp_path)
    assert audit.SQLSERVER_RESULT == harness.PACKAGE_RESULT
    assert json.loads((package / audit.MANIFEST).read_text())["sqlserver_run"]["file"] is None
    as_the_harness_writes_it(package, conversion, scenarios)
    # Until the run is recorded, the result is a file that the package does not account for.
    assert audit.SQLSERVER_RESULT in audit._changes(package, json.loads((package / audit.MANIFEST).read_text()))[0]
    recorded = audit.record_sqlserver_run(package)
    manifest = json.loads((package / audit.MANIFEST).read_text())
    data = (package / audit.SQLSERVER_RESULT).read_bytes()
    assert recorded["sha256"] == hashlib.sha256(data).hexdigest() == manifest["inputs"]["files"][audit.SQLSERVER_RESULT]
    assert recorded["sql_sha256"] == manifest["sql_sha256"] and recorded["outcome"] == "ran"
    assert "gives no permission to run the script" in recorded["says"] and "!" not in recorded["says"]
    assert audit.status(package)["changed"] == []
    # A change to the result file is a change to a hashed input, which voids the reviews and the approval.
    held = json.loads(data)
    held["result_sets"][0]["rows"] = [["1"]]
    (package / audit.SQLSERVER_RESULT).write_text(json.dumps(held), encoding="utf-8")
    standing = audit.status(package)
    assert standing["voided"] and audit.SQLSERVER_RESULT in standing["changed"]


def test_a_result_of_another_text_is_not_recorded(built, tmp_path):
    package, conversion, scenarios = built
    package = copied(package, tmp_path)
    found = as_the_harness_writes_it(package, conversion, scenarios)
    found["sql_sha256"] = "0" * 64
    (package / audit.SQLSERVER_RESULT).write_text(json.dumps(found), encoding="utf-8")
    with pytest.raises(audit.AuditError, match="another text of query.sql"):
        audit.record_sqlserver_run(package)


def test_the_testbed_compares_the_harness_result_with_duckdb_s_translated_form(built, tmp_path):
    package, conversion, scenarios = built
    package = copied(package, tmp_path)
    assert testbed.package_equivalence(make_checks.WORLD, CONVERSION, None)["state"] == "not run"
    assert testbed.package_equivalence(make_checks.WORLD, CONVERSION, package)["state"] == "not run on SQL Server"
    found = as_the_harness_writes_it(package, conversion, scenarios)
    # Unrecorded in the manifest, the result is not evidence about the package.
    check = testbed.package_equivalence(make_checks.WORLD, CONVERSION, package)
    assert check["state"] == "failed" and "the manifest does not record" in check["detail"].lower()
    audit.record_sqlserver_run(package)
    check = testbed.package_equivalence(make_checks.WORLD, CONVERSION, package)
    assert check["state"] == "passed" and check["passed"] and check["result_sets"] == 4, check["detail"]
    assert {r["name"] for r in check["rewrites"]} >= {"cohort_key_without_isnull", "cohort_primary_key_dropped"}
    assert all(r["what"] for r in check["rewrites"]) and "!" not in check["detail"]
    # A result set that SQL Server gave otherwise is named.
    other = copy.deepcopy(found["result_sets"])
    other[3]["rows"][0][1] = "990"
    as_the_harness_writes_it(package, conversion, scenarios, other)
    audit.record_sqlserver_run(package)
    check = testbed.package_equivalence(make_checks.WORLD, CONVERSION, package)
    assert check["state"] == "failed" and "result set 4 (minutes_below_40" in check["detail"].lower()
    assert testbed.judge([check], "fast") == "failed: the package's script gave other results on SQL Server than on DuckDB"
    # A change to query.sql after the harness ran it leaves the result about another text.
    (package / "query.sql").write_text((package / "query.sql").read_text() + "\n", encoding="utf-8")
    check = testbed.package_equivalence(make_checks.WORLD, CONVERSION, package)
    assert check["state"] == "failed" and "another text" in check["detail"]


def test_the_harness_reads_each_result_set_that_sqlcmd_prints():
    spec = importlib.util.spec_from_file_location("sqlserver_harness", ROOT / "tools" / "sqlserver" / "harness.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    sep = tools.SEPARATOR
    printed = f"a{sep}b\n-{sep}-\n1{sep}x|y\nc\n-\nNULL\n2\nz\n-\nw\n--\n-\n"
    assert tools.result_sets(printed) == [{"columns": ["a", "b"], "rows": [["1", "x|y"]]},
                                          {"columns": ["c"], "rows": [[None], ["2"]]},
                                          {"columns": ["z"], "rows": []}, {"columns": ["w"], "rows": [["-"]]}]


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("SCHEMALYSER_MSSQL_PASSWORD_FILE"),
                    reason="the SQL Server harness runs only where SCHEMALYSER_MSSQL_PASSWORD_FILE names its password file")
def test_the_harness_runs_the_package_s_script_byte_for_byte_and_the_testbed_finds_it_equivalent(built, tmp_path):
    package, _, _ = built
    package = copied(package, tmp_path)
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sqlserver" / "harness.py"), "--package", str(package),
                           "--rows", str(ROWS)], cwd=ROOT / "core", capture_output=True, text=True, timeout=1800)
    assert done.returncode == 0, done.stdout + done.stderr
    held = json.loads((package / audit.SQLSERVER_RESULT).read_text())
    assert held["executed_sha256"] == held["sql_sha256"] == hashlib.sha256((package / "query.sql").read_bytes()).hexdigest()
    assert held["sqlserver"] and len(held["result_sets"]) == 4 and held["error"] is None
    check = testbed.package_equivalence(make_checks.WORLD, CONVERSION, package)
    assert check["state"] == "passed", check["detail"]
