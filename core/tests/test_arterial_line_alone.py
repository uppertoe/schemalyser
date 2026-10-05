"""The decision to use the arterial line alone once it is running, proved on a planted anaesthetic.

The scenario cuff_reading_during_arterial_line plants a neonate whose run of arterial means below 40 is interrupted by
a cuff mean of 60. Under the present rule the readings below 40 stand for 4 minutes; when the arterial line alone is
used once it is running, the cuff mean is set aside and they stand for 8 minutes. Each rule is checked twice: on the
minutes of the planted anaesthetic itself, and on the band of the audit's own answer into which it falls. Each time the
reference query over the source tables must agree with the target on the synthetic rows.
"""
import sys
from pathlib import Path

import pytest

from schemalyser import target
from schemalyser.catalogue import Catalogue

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
NEONATAL = (FIXTURES / "targets" / "neonatal_low_mean_pressure.sql").read_text()
CATALOGUE = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

SCENARIO = "cuff_reading_during_arterial_line"
PLANTED = "990004400"
RULES = {"present": ({}, 4, "under 5 minutes"), "arterial_only": ({"pressures": "arterial_only"}, 8, "5 to 14 minutes")}

# The target's own steps up to the minutes below 40 of each anaesthetic, which then gives the minutes of the planted one.
_head, _rest = NEONATAL.split("),\nbanded AS (", 1)
MINUTES = _head + """)
SELECT l.minutes_below_40
FROM   low l
       JOIN omop.anaesthetic a ON a.anaesthetic_id = l.anaesthetic_id
       JOIN omop.visit_detail vd ON vd.visit_detail_id = a.visit_detail_id
WHERE  vd.visit_detail_source_value = '""" + PLANTED + "'\n"


def _checked(sql, scenarios):
    reference = target.source_draft(CONVERSION, sql, CATALOGUE, blank=False)
    return target.check_query(make_checks.WORLD, CONVERSION, sql, reference, rows=300, scenarios=scenarios)


@pytest.mark.parametrize("rule", list(RULES))
def test_the_planted_anaesthetic_gives_the_minutes_of_each_rule_and_the_reference_query_agrees(rule):
    settings, minutes, _ = RULES[rule]
    sql = target.with_settings(MINUTES, settings, CONVERSION)
    assert ("art_before" in sql) == (rule == "arterial_only")
    result = _checked(sql, [SCENARIO])
    assert result["agree"], result["differs"]
    assert [float(m) for (m,) in result["target"]] == [minutes]
    assert [float(m) for (m,) in result["query"]] == [minutes]


@pytest.mark.parametrize("rule", list(RULES))
def test_the_planted_anaesthetic_falls_in_the_band_of_its_minutes_in_the_audit_s_answer(rule):
    settings, _, band = RULES[rule]
    sql = target.with_settings(NEONATAL, settings, CONVERSION)
    planted = _checked(sql, [SCENARIO])
    assert planted["agree"], planted["differs"]
    without = target.run(make_checks.WORLD, CONVERSION, sql, rows=300, scenarios=[])
    before = {row[0]: int(row[1]) for row in without["rows"]}
    after = {row[0]: int(row[1]) for row in planted["target"]}
    # The planted anaesthetic adds one anaesthetic to the band of its minutes and to no other band.
    assert {name: after[name] - before[name] for name in after if after[name] != before[name]} == {band: 1}
