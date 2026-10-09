"""The first ask: the names of the tables that a set of requests reads, and the one query of the server's own records
that screen 1 builds from them."""
import json
import re

import sqlglot

from schemalyser import first_ask

HOSTILE = """-- Treatment of Jane_Citizen in a comment: SELECT * FROM Jane_Citizen
WITH Bob_Smith_Cohort AS (SELECT * FROM THEATRE_CASE)
SELECT c.CASE_KEY, 'FROM Alice_Patient' AS note
FROM Bob_Smith_Cohort AS Carol_Jones
JOIN [dbo].[VISIT] AS Dan_Brown ON Dan_Brown.VISIT_KEY = Carol_Jones.VISIT_KEY
JOIN #Eve_Patient_Temp AS t ON 1 = 1
JOIN @Frank_Patient_Rows AS v ON 1 = 1
JOIN [Bad;Zq] ON 1 = 1
JOIN [Quote'Zq] ON 1 = 1
JOIN [Bracket]]Zq] ON 1 = 1
JOIN [Line
ZqBreak] ON 1 = 1
JOIN [""" + "Y" * 200 + """] ON 1 = 1;
EXEC('SELECT * FROM Grace_Dynamic');
SELECT * INTO #Harry_Patient FROM ANAES_RECORD;
DECLARE @Ivy_Patient TABLE (k int);
"""
NEVER = ("Jane_Citizen", "Bob_Smith", "Alice_Patient", "Carol_Jones", "Dan_Brown", "Eve_Patient", "Frank_Patient",
         "Zq", "YYYY", "Grace_Dynamic", "Harry_Patient", "Ivy_Patient")


def test_only_plain_names_of_stored_tables_reach_the_first_query():
    assert first_ask.names_in(HOSTILE) == {"THEATRE_CASE", "VISIT", "ANAES_RECORD"}
    names = first_ask.Names()
    names.add(HOSTILE)
    chosen, left_out = names.chosen()
    sql = first_ask.query(chosen)
    for word in NEVER:
        assert word not in sql, word
    assert "N'ANAES_RECORD', N'THEATRE_CASE', N'VISIT'" in sql and left_out == 0
    # A plain, real-looking table name reaches the query.
    assert first_ask.names_in("SELECT p.X FROM WARD_STAY_LOG AS p") == {"WARD_STAY_LOG"}
    # The query drops any name that is not plain, even when one is handed to it directly.
    direct = first_ask.query(["GOOD_NAME", "bad'name", "semi;colon", "line\nbreak", "[bracket]", "Z" * 129])
    assert "N'GOOD_NAME')" in direct and "bad" not in direct and "semi" not in direct and "bracket" not in direct
    assert "Z" * 129 not in direct and first_ask.query([]) == ""
    # It reads only the server's own records, as one SELECT.
    body = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert len([s for s in sqlglot.parse(body, dialect="tsql") if s]) == 1
    assert set(re.findall(r"FROM (\S+)", body)) == {"sys.partitions", "INFORMATION_SCHEMA.COLUMNS"}
    assert "(SUM(p.rows) / 10) * 10" in body and "p.index_id IN (0, 1)" in body


def test_above_the_cap_the_names_that_most_files_read_are_kept():
    names = first_ask.Names()
    for i in range(first_ask.MAXIMUM_NAMES + 30):
        names.add(f"SELECT 1 FROM T_{i:04d}")
    names.add("SELECT 1 FROM T_0529 JOIN T_0528 ON 1 = 1")
    chosen, left_out = names.chosen()
    assert len(chosen) == first_ask.MAXIMUM_NAMES and left_out == 30
    assert "T_0529" in chosen and "T_0528" in chosen and "T_0527" not in chosen


def test_a_result_that_lacks_most_tables_or_every_lookup_table_raises_a_doubt_about_the_database():
    from schemalyser.rules import SiteRules
    rules = SiteRules.from_json(json.dumps({"definitionKeys": [{"column": "KIND_C", "definitionTable": "ZC_KIND",
                                                                "labelColumn": "NAME"}]}))
    assert first_ask.doubt(["A", "B", "C"], ["a"], rules) == "most"
    assert first_ask.doubt(["A", "B", "ZC_KIND"], ["A", "B"], rules) == "lookups"
    assert first_ask.doubt(["A", "B", "ZC_KIND"], ["A", "zc_kind"], rules) == ""
    assert first_ask.doubt(["A", "B"], ["A", "B"], rules) == "" and first_ask.doubt([], [], rules) == ""
