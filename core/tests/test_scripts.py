"""The queries that reach the table of readings, written as scripts that start from a temporary table of the cohort: each
gives the same answer as the single query that it replaces, on the synthetic rows with the planted scenarios, and each is
offered only where it is safe by construction."""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from schemalyser import boundary, charted, facts, scripts, target
from schemalyser.catalogue import Catalogue
from schemalyser.rules import SiteRules
from schemalyser.translate import to_duckdb

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
CONVERSION = FIXTURES / "conversion"
NEONATAL = (FIXTURES / "targets" / "neonatal_low_mean_pressure.sql").read_text()
CATALOGUE = Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text())
RULES = SiteRules.from_json((FIXTURES / "invented-site-rules.json").read_text())
TARGET = "neonatal_low_mean_pressure"
SETTINGS = {"from": "2021-01-01", "to": "2024-06-30"}
COUNT = {"kind": "count", "answer": "right", "date": "2026-10-05", "years": [[2023, 120, 20], [2024, 130, 20]]}
CODES = {"kind": "codes", "vocabulary": "SITE_OBS", "concept": 21490852, "codes": ["52"], "date": "2026-10-05"}
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402


def _held(*given):
    held = facts.Facts()
    for fact in given:
        held = held.with_fact(facts.check(fact, CATALOGUE))
    return held


def _target(tmp_path, settings=SETTINGS, given=(COUNT, CODES)):
    state = tmp_path / "state"
    (state / "targets").mkdir(parents=True)
    shutil.copytree(CONVERSION, state / "conversion")
    shutil.copy(FIXTURES / "invented-catalogue.csv", state / "catalogue.csv")
    shutil.copy(FIXTURES / "invented-site-rules.json", state / "site-rules.json")
    shutil.copy(FIXTURES / "targets" / f"{TARGET}.sql", state / "targets" / f"{TARGET}.sql")
    (state / "audit.json").write_text(json.dumps(settings))
    held = _held(*given)
    (state / "facts.json").write_text(held.to_json())
    (state / "conversion" / facts.SITE_MAPPINGS).write_text(held.site_mappings())
    _, found = boundary.produce(state, FIXTURES / "requests", draft_when=lambda rows, s: True)
    return next(t for t in found["targets"] if t["name"] == TARGET)


@pytest.fixture(scope="module")
def sandbox():
    found = target.run(make_checks.WORLD, CONVERSION, NEONATAL, rows=300)
    return found["conversion"]


def _run(sandbox, sql, exact=False):
    """The rows of the last statement of a query or script, run on the synthetic rows. SQL Server needs ISNULL to give the
    temporary table's key a column that cannot be empty, which DuckDB does not; DuckDB cannot add a primary key to a
    table that it has made, so that statement is left out. exact lifts the blanking of counts under ten in the same way
    in both forms, so that the counts themselves are compared."""
    sql = sql.replace("ISNULL(c.anaesthetic_id, 0)", "c.anaesthetic_id")
    if exact:
        sql = re.sub(r">= 10(\s+)THEN", r">= 0\1THEN", sql)
    statements = [s for s in to_duckdb(sql, sandbox.sandbox.date_columns) if not s.upper().startswith("ALTER TABLE")]
    try:
        for statement in statements:
            rows = sandbox.con.execute(statement).fetchall()
    finally:
        sandbox.con.execute("DROP TABLE IF EXISTS temp_cohort")
    return sorted(rows, key=repr)


def _shape(sql):
    """The script's shape: the comment lines that advise a time-out and say what it creates and reads, SET NOCOUNT ON,
    part 1, which puts the cohort into #cohort with its key and reads no table of readings, and part 2, which is returned."""
    comments = " ".join(line[3:] for line in sql.splitlines() if line.startswith("-- "))
    assert sql.startswith("-- The database analyst sets a time limit before running this script, because")
    for sentence in (scripts.WORDING["timeout"], scripts.WORDING["temporary"], scripts.WORDING["part1"], scripts.WORDING["part2"]):
        assert sentence in comments
    assert re.search(r"The count by year shows (about [\d,]+|fewer than ten) anaesthetics of the cohort (in \d{4}|from [\d-]+ to [\d-]+), and part 2 asks only", comments)
    assert sql.count("\nSET NOCOUNT ON;\n") == 1 and sql.count(scripts.DROP) == 1
    assert sql.count("INTO #cohort") == 1 and sql.count("ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_id);") == 1
    # Nothing but #cohort is created, and nothing is changed.
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE)\b", sql.replace(scripts.WORDING["temporary"], ""), re.I)
    first, _, rest = sql.partition("ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_id);")
    assert first.index(scripts.DROP) < first.index("INTO #cohort")
    statement = "\n".join(line for line in first.splitlines() if not line.startswith("--"))
    assert "OBS_READING" not in statement and "OBS_SHEET" not in statement
    assert rest.lstrip().startswith("-- " + scripts.WORDING["part2"])
    return rest


def test_the_list_as_a_script_gives_the_same_rows_as_the_single_query(sandbox):
    for year in (2023, 2024):
        found = charted.listed_query(CONVERSION, NEONATAL, CATALOGUE, RULES, "OBS_READING.OBS_TYPE_KEY", year, (), None,
                                     TARGET, _held(COUNT).count())
        rest = _shape(found["sql"])
        # The readings are reached from #cohort by key alone, with the table of readings joined last.
        assert "FROM #cohort AS c\nJOIN [dbo].[OBS_SHEET] AS s WITH (NOLOCK)\n  ON s.ANAES_KEY = c.visit_detail_source_value__key" in rest
        assert "q22_cohort" not in rest
        assert "FROM q22_cohort AS c\n" in found["single"] and "#cohort" not in found["single"]
        for exact in (False, True):
            ours, before = _run(sandbox, found["sql"], exact), _run(sandbox, found["single"], exact)
            assert ours == before
        assert ours and (year != 2024 or any(row[0] == "52" for row in ours)), (year, ours)


def test_the_count_of_the_chosen_codes_as_a_script_gives_the_same_rows(tmp_path, sandbox):
    found = _target(tmp_path)["charted"]
    rest = _shape(found["sql"])
    assert "FROM #cohort AS c\nJOIN [dbo].[OBS_SHEET] AS s WITH (NOLOCK)" in rest and "q22_cohort" not in rest
    for exact in (False, True):
        ours, before = _run(sandbox, found["sql"], exact), _run(sandbox, found["single"], exact)
        assert ours == before
    assert ours and ours[0][0] == "52"


def test_the_reference_query_as_a_script_gives_the_same_answer(tmp_path, sandbox):
    found = _target(tmp_path)
    script = found["draft_script"]
    assert found["draft_restructured"] and script["sql"] and not script["withheld"] and script["worst"] == "about 40 anaesthetics of the cohort from 2021-01-01 to 2024-06-30"
    rest = _shape(script["sql"])
    # The cohort's own common table expression reads #cohort, and the step that reads the readings starts from it by key.
    assert "q22_neonatal AS (\n  SELECT\n    anaesthetic_id,\n    person_id,\n    start_datetime,\n    end_datetime\n  FROM #cohort\n)" in rest
    assert "FROM #cohort AS c\n        JOIN [dbo].[OBS_SHEET] AS s\n        ON s.ANAES_KEY = c.visit_detail_source_value__key" in rest
    assert len(re.findall(r"\b(?:FROM|JOIN)\s+(?:\[dbo\]\.)?\[?OBS_READING\b", rest)) == 1
    for exact in (False, True):
        assert _run(sandbox, script["sql"], exact) == _run(sandbox, found["draft"], exact)
    # It agrees with the target query itself on the synthetic rows, as the single query does.
    checked = target.check_query(make_checks.WORLD, CONVERSION, target.with_settings(NEONATAL, SETTINGS, CONVERSION),
                                 found["draft"], rows=300)
    assert checked["agree"]


def test_a_script_is_offered_only_once_the_count_by_year_is_seen_and_within_the_limit(tmp_path):
    assert scripts.LIMIT == 5000
    assert scripts.worst_case(None, 2024, 2024) == (None, scripts.WORDING["unseen"])
    assert scripts.worst_case({"years": [[2024, 900, None]]}, 2024, 2024) == ("fewer than ten anaesthetics of the cohort in 2024", None)
    assert scripts.worst_case({"years": [[2025, 1730, 200]]}, "2025-01-01", "2025-12-31") == ("about 200 anaesthetics of the cohort in 2025", None)
    assert scripts.worst_case({"years": [[2023, 9000, 2400], [2024, 9000, 2600]]}, "2023-07-01", "2024-06-30")[1] == \
        ("The count by year shows that the cohort may hold as many as 5,020 anaesthetics from 2023-07-01 to 2024-06-30. Schemalyser offers a "
         "script that reads the readings only where the period holds at most 5,000 anaesthetics of the cohort, so please "
         "choose a shorter period.")
    many = dict(COUNT, years=[[2023, 9000, 2400], [2024, 9000, 2600]])
    found = _target(tmp_path, given=(many, CODES))
    assert found["listed"]["sql"] and found["listed"]["worst"] == "about 2,600 anaesthetics of the cohort in 2024"     # one year is within the limit
    assert found["charted"]["sql"] == "" and "choose a shorter period" in found["charted"]["withheld"]
    assert found["draft_script"]["sql"] == "" and "choose a shorter period" in found["draft_script"]["withheld"]


def test_a_cohort_that_joins_on_an_expression_is_not_offered():
    head = ("\nWITH\nstep_11 AS (\n  SELECT a.REC_KEY AS visit_detail_id\n  FROM REC AS a\n"
            "  LEFT JOIN omop_visit AS billed ON billed.visit_source_value__key = a.BILLED_KEY\n"
            "  LEFT JOIN omop_visit AS own ON own.visit_source_value__key = a.OWN_KEY\n"
            "  JOIN omop_visit AS vo ON vo.visit_occurrence_id = COALESCE(billed.visit_occurrence_id, own.visit_occurrence_id)\n)")
    with pytest.raises(scripts.Unsafe, match="expression of the tables already joined"):
        scripts.check_cohort(head)
    with pytest.raises(scripts.Unsafe, match="key cast to text"):
        scripts.check_cohort(head.replace("COALESCE(billed.visit_occurrence_id, own.visit_occurrence_id)",
                                          "CAST(a.OWN_KEY AS VARCHAR(50))"))
    # An outer join to a lookup may compute what it looks up from the tables already joined.
    assert scripts.check_cohort(head.replace("  JOIN omop_visit AS vo ON vo.visit_occurrence_id = COALESCE(",
                                             "  LEFT JOIN omop_site AS cs ON cs.site = COALESCE("))
