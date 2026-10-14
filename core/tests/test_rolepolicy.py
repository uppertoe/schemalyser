"""The role-level policy's dialect rule: one refused and one accepted example for each constraint, each refusal naming
its remedy, the neonatal audit and the five capabilities passing, and the rule's version in the report."""
import json
from pathlib import Path

import pytest

from schemalyser import capability, rolemap, rolepolicy

PLANTED = json.loads((capability.FOLDER / "capabilities" / "planted.json").read_text())


def dialect(sql):
    found = rolepolicy.check(sql)
    rule = next(r for r in found["rules"] if r["id"] == "dialect")
    return rule["fragments"], found["failed"]


@pytest.mark.parametrize("refused, remedy, accepted", [
    ("SELECT COUNT(*) / COUNT(DISTINCT a.patient_key) AS per_patient FROM role_anaesthetic a",
     "write CAST(COUNT(*) AS float) / COUNT(DISTINCT a.patient_key) for a fraction",
     "SELECT CAST(COUNT(*) AS float) / COUNT(DISTINCT a.patient_key) AS per_patient FROM role_anaesthetic a"),
    ("SELECT SUM(r.accepted) / 2 AS half FROM role_reading r",
     "divides two whole numbers",
     "SELECT SUM(r.accepted) / 2.0 AS half FROM role_reading r"),
    ("SELECT AVG(r.accepted) AS share FROM role_reading r",
     "write AVG(CAST(r.accepted AS float))",
     "SELECT AVG(CAST(r.accepted AS float)) AS share FROM role_reading r"),
    ("SELECT COUNT(*) AS n FROM role_reading r WHERE r.value_text = 'high'",
     "read free text with LIKE",
     "SELECT COUNT(*) AS n FROM role_reading r WHERE r.value_text LIKE 'high%'"),
    ("SELECT r.anaesthetic_key + '-' + r.kind AS label FROM role_reading r",
     "write CONCAT(",
     "SELECT CONCAT(r.anaesthetic_key, '-', r.kind) AS label FROM role_reading r"),
    ("SELECT r.anaesthetic_key, ROW_NUMBER() OVER (PARTITION BY r.anaesthetic_key ORDER BY r.reading_time) AS n FROM role_reading r",
     "end its ORDER BY in a key column of the row's part, such as reading_key",
     "SELECT r.anaesthetic_key, ROW_NUMBER() OVER (PARTITION BY r.anaesthetic_key ORDER BY r.reading_time, r.reading_key) AS n FROM role_reading r"),
    ("SELECT r.anaesthetic_key, LEAD(r.value) OVER (PARTITION BY r.anaesthetic_key) AS next_value FROM role_reading r",
     "carries an ORDER BY",
     "SELECT r.anaesthetic_key, LEAD(r.value) OVER (PARTITION BY r.anaesthetic_key ORDER BY r.reading_time) AS next_value FROM role_reading r"),
    ("SELECT TOP 10 r.anaesthetic_key, r.value FROM role_reading r ORDER BY r.value",
     "TOP chooses rows",
     "SELECT TOP 10 r.anaesthetic_key, r.value FROM role_reading r ORDER BY r.value, r.reading_key"),
    ("SELECT DATEDIFF(hour, a.start_time, a.stop_time) AS hours FROM role_anaesthetic a",
     "write DATEDIFF with day, minute or second",
     "SELECT DATEDIFF(minute, a.start_time, a.stop_time) AS minutes FROM role_anaesthetic a"),
])
def test_each_constraint_of_the_dialect_refuses_one_example_with_its_remedy_and_accepts_the_other(refused, remedy, accepted):
    fragments, failed = dialect(refused)
    assert failed == ["dialect"] and len(fragments) == 1 and remedy in fragments[0], fragments
    fragments, failed = dialect(accepted)
    assert fragments == [] and failed == [], failed


def test_a_kind_against_its_word_and_a_key_against_a_key_are_the_text_comparisons_the_dialect_allows():
    sql = ("SELECT COUNT(*) AS n FROM role_reading r JOIN role_anaesthetic a ON a.anaesthetic_key = r.anaesthetic_key "
           "WHERE r.kind = 'map_arterial' AND r.kind <> 'map_cuff'")
    assert rolepolicy.check(sql)["failed"] == []


def test_the_neonatal_audit_and_the_five_capabilities_keep_the_dialect():
    found = rolepolicy.check(rolemap.AUDIT.read_text(encoding="utf-8"))
    assert found["failed"] == [], [r for r in found["rules"] if not r["passed"]]
    for name in capability.with_sql():
        found = rolepolicy.check(capability.fill(name, PLANTED[name]["parameters"]))
        assert found["failed"] == [], (name, [r for r in found["rules"] if not r["passed"]])


def test_the_report_records_the_dialect_s_version():
    found = rolepolicy.check("SELECT COUNT(*) AS n FROM role_patient p")
    assert found["dialect_version"] == rolepolicy.DIALECT_VERSION == 1
    assert "dialect" in rolepolicy.RULES and any(r["id"] == "dialect" for r in found["rules"])
