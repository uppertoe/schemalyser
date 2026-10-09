"""The static policy on the final text of a script, read on scripts written here over three invented tables.

EPISODE holds one row for each anaesthetic and SHEET one row for each sheet of a chart, and both are small; READING
holds the readings and is large. A script that reaches READING must do so through the series: a count of the cohort over
the period, then the coverage of the link from the cohort to the sheets, then the rows, each step marked by its comment
line, with the structure behind each marker checked. A conversion step is read with no cohort, and the report says what
the policy then cannot establish.
"""
import pytest

from schemalyser import policy

TABLES = {"EPISODE", "SHEET", "READING"}
SIZES = {"EPISODE": 2_000, "SHEET": 9_000, "READING": 400_000_000}

COHORT = """SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;
SELECT TOP (5000) ISNULL(e.EPISODE_KEY, 0) AS anaesthetic_key, e.START_TS AS start_time
INTO   #cohort
FROM   EPISODE AS e WITH (NOLOCK)
WHERE  e.START_TS >= CAST('2024-01-01' AS datetime)
  AND  e.START_TS < CAST('2025-01-01' AS datetime)
ORDER  BY e.EPISODE_KEY;
ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);
"""
COUNT = """-- series: count
SELECT COUNT(*) AS anaesthetics
FROM   #cohort AS c
WHERE  c.start_time >= CAST('2024-01-01' AS datetime)
  AND  c.start_time < CAST('2024-01-08' AS datetime);
"""
COVERAGE = """-- series: coverage
SELECT COUNT(*) AS anaesthetics, COUNT(s.EPISODE_KEY) AS with_sheet
FROM   #cohort AS c
LEFT JOIN SHEET AS s WITH (NOLOCK) ON s.EPISODE_KEY = c.anaesthetic_key;
"""
ROWS = """-- series: rows
SELECT COUNT(*) AS readings
FROM   #cohort AS c
JOIN   SHEET AS s WITH (NOLOCK) ON s.EPISODE_KEY = c.anaesthetic_key
JOIN   READING AS r WITH (NOLOCK) ON r.SHEET_KEY = s.SHEET_KEY;
"""
END = "DROP TABLE #cohort;\n"


def _check(sql, **more):
    return policy.check(sql, TABLES, SIZES, **more)


def _failed(found):
    return {r["id"] for r in found["rules"] if r["passed"] is False}


def _fragments(found, rule):
    return next(r for r in found["rules"] if r["id"] == rule)["fragments"]


def test_a_script_that_reaches_a_large_table_through_the_series_is_class_b_and_names_its_policy_version():
    found = _check(COHORT + COUNT + COVERAGE + ROWS + END)
    assert found["outcome"] == "passed" and found["execution_class"] == "B", _failed(found)
    assert found["policy_version"] == policy.POLICY_VERSION == "1" and found["purpose"] == "audit"
    assert found["series"]["required"] is True
    assert [s["step"] for s in found["series"]["steps"]] == ["count", "coverage", "rows"]
    assert found["series"]["first_large_read"] == {"statement": found["series"]["steps"][2]["statement"], "tables": ["READING"]}
    assert found["large_tables"] == ["READING"] and found["cannot_establish"] == []
    assert all(r["applies"] for r in found["rules"])


def test_a_script_without_the_series_or_with_its_steps_out_of_order_is_a_large_extraction_at_most():
    bare = _check(COHORT + ROWS.replace("-- series: rows\n", "") + END)
    assert _failed(bare) == {"series"} and bare["execution_class"] == "C"
    assert _fragments(bare, "series") == ["the script reads READING with no series of a count, a coverage and the rows before it"]
    swapped = _check(COHORT + COVERAGE + COUNT + ROWS + END)
    assert _failed(swapped) == {"series"} and swapped["execution_class"] == "C"
    assert "are marked coverage, count, rows" in _fragments(swapped, "series")[0]
    # The rows step marked, and the count and coverage steps missing, is no series.
    alone = _check(COHORT + ROWS + END)
    assert _failed(alone) == {"series"} and "marked rows" in _fragments(alone, "series")[0]


def test_a_large_table_read_before_the_rows_step_breaks_the_series():
    early = COVERAGE.replace("LEFT JOIN SHEET AS s WITH (NOLOCK) ON s.EPISODE_KEY = c.anaesthetic_key",
                             "LEFT JOIN SHEET AS s WITH (NOLOCK) ON s.EPISODE_KEY = c.anaesthetic_key\n"
                             "LEFT JOIN READING AS r WITH (NOLOCK) ON r.SHEET_KEY = s.SHEET_KEY")
    found = _check(COHORT + COUNT + early + ROWS + END)
    assert _failed(found) == {"series"} and found["execution_class"] == "C"
    assert _fragments(found, "series") == ["READING is read before the rows step of the series"]


def test_the_structure_behind_each_marker_is_checked_rather_than_the_comment_alone():
    unbounded = COUNT.replace("WHERE  c.start_time >= CAST('2024-01-01' AS datetime)\n  AND  c.start_time < CAST('2024-01-08' AS datetime)",
                              "WHERE  c.start_time >= CAST('2024-01-01' AS datetime)")
    found = _check(COHORT + unbounded + COVERAGE + ROWS + END)
    assert _fragments(found, "series") == ["the count step is not bounded by a first and a last date"]
    uncounted = COUNT.replace("COUNT(*) AS anaesthetics", "c.anaesthetic_key")
    assert "the count step returns no COUNT" in _fragments(_check(COHORT + uncounted + COVERAGE + ROWS + END), "series")
    unlinked = "-- series: coverage\nSELECT COUNT(*) AS anaesthetics FROM #cohort AS c;\n"
    found = _check(COHORT + COUNT + unlinked + ROWS + END)
    assert _fragments(found, "series") == ["the coverage step joins no link to the cohort"] and found["execution_class"] == "C"
    elsewhere = COUNT.replace("FROM   #cohort AS c", "FROM   EPISODE AS c")
    assert "the count step does not start from the cohort" in _fragments(_check(COHORT + elsewhere + COVERAGE + ROWS + END), "series")
    # A link tested through EXISTS is a link as well.
    exists = ("-- series: coverage\nSELECT COUNT(*) AS with_sheet FROM #cohort AS c\n"
              "WHERE EXISTS (SELECT 1 FROM SHEET AS s WHERE s.EPISODE_KEY = c.anaesthetic_key);\n")
    assert _check(COHORT + COUNT + exists + ROWS + END)["execution_class"] == "B"


def test_a_marker_of_another_form_or_standing_inside_a_statement_is_refused():
    found = _check(COHORT + COUNT + COVERAGE.replace("-- series: coverage", "-- series: links") + ROWS + END)
    assert "series" in _failed(found) and found["execution_class"] == "C"
    assert any("is not one of the markers" in f for f in _fragments(found, "series"))
    inside = ROWS.replace("FROM   #cohort AS c\n", "FROM   #cohort AS c\n-- series: rows\n")
    assert any("stands where no statement follows it" in f for f in _fragments(_check(COHORT + COUNT + COVERAGE + inside + END), "series"))


def test_a_script_that_reads_no_large_table_needs_no_series():
    small = "SELECT COUNT(*) AS sheets\nFROM   #cohort AS c\nJOIN   SHEET AS s ON s.EPISODE_KEY = c.anaesthetic_key;\n"
    found = _check(COHORT + small + END)
    assert found["execution_class"] == "B" and found["series"] == {"required": False, "steps": [], "first_large_read": None}


def test_a_script_with_no_cohort_can_be_checked_and_says_what_the_policy_cannot_establish():
    found = _check("SELECT COUNT(*) AS sheets FROM SHEET AS s;")
    assert found["execution_class"] == "B" and found["cannot_establish"] == [policy.CANNOT["no_cohort"]]
    # A conversion step reads its source whole: the rules about the cohort do not apply, and it is class C at best.
    step = ("SELECT ROW_NUMBER() OVER (ORDER BY s.SHEET_KEY) AS sheet_id, LEFT(s.LABEL, 50) AS sheet_source_value,\n"
            "       e.EPISODE_KEY AS episode_key\n"
            "FROM   SHEET AS s\nJOIN   EPISODE AS e ON CAST(e.EPISODE_KEY AS varchar(50)) = s.EPISODE_REF\n"
            "JOIN   READING AS r ON r.SHEET_KEY = s.SHEET_KEY")
    found = _check(step, purpose="conversion")
    assert found["outcome"] == "passed" and found["execution_class"] == "C", _failed(found)
    assert found["purpose"] == "conversion" and found["series"] is None
    assert found["cannot_establish"] == [policy.CANNOT["conversion"]]
    applies = {r["id"]: (r["applies"], r["passed"]) for r in found["rules"]}
    assert all(applies[rule] == (False, None) for rule in policy.COHORT_RULES)
    assert applies["tables"] == (True, True) and applies["functions"] == (True, True)
    # The text functions are a conversion's only, and a conversion step writes nothing itself.
    assert "functions" in _failed(_check("SELECT LEFT(s.LABEL, 50) AS label FROM SHEET AS s;"))
    for sql in ("INSERT INTO #step SELECT s.SHEET_KEY FROM SHEET AS s", "SELECT s.SHEET_KEY INTO #step FROM SHEET AS s"):
        assert _check(sql, purpose="conversion")["execution_class"] == "D"
    with pytest.raises(ValueError):
        _check(step, purpose="release")
