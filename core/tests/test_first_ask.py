"""The first ask without a catalogue, the two phases of a checklist, and the audit query."""
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest
import sqlglot

from schemalyser import Analysis, boundary, browser, first_ask, target
from schemalyser.catalogue import Catalogue
from schemalyser.checks import Checks
from schemalyser.rules import SiteRules

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CONVERSION = FIXTURES / "conversion"
TARGETS = FIXTURES / "targets"
CATALOGUE = (FIXTURES / "invented-catalogue.csv").read_text()
RULES = (FIXTURES / "invented-site-rules.json").read_text()
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
PROFILE = (FIXTURES / "profile" / "invented-core-profile.csv").read_text()
sys.path.insert(0, str(FIXTURES))
import make_checks  # noqa: E402

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


def _result(names, sizes):
    """The first query's result for some invented tables, as the results grid would copy it."""
    catalogue = Catalogue.from_csv(CATALOGUE)
    lines = ["\t".join(first_ask.LAYOUT)]
    for name in names:
        table = catalogue.table(name)
        for column in sorted(table.columns.values(), key=lambda c: c.position or 0):
            lines.append("\t".join([table.schema, table.name, column.name, str(column.position), column.data_type,
                                    str(column.max_length) if column.max_length else "NULL", "NULL",
                                    str(column.scale) if column.scale is not None else "NULL",
                                    "NO" if column.nullable is False else "YES", str(sizes.get(name, "NULL"))]))
    return "\n".join(lines) + "\n(12 rows affected)\n"


def test_the_result_becomes_the_catalogue_and_the_sizes():
    rules = SiteRules.from_json(RULES)
    text = _result(["THEATRE_CASE", "VISIT"], {"THEATRE_CASE": 530, "VISIT": 520})
    catalogue_text, checks_text, facts = first_ask.read(text, rules)
    catalogue = Catalogue.from_csv(catalogue_text)
    assert {t.name for t in catalogue.tables()} == {"THEATRE_CASE", "VISIT"}
    assert Checks.from_csv(checks_text, catalogue, rules).rows == {"THEATRE_CASE": 530, "VISIT": 520}
    assert facts["tables"] == 2 and facts["sized"] == 2
    # Earlier check results are kept, with these sizes in place of theirs.
    _, merged, _ = first_ask.read(text, rules, "check_kind,table_name,column_name,value,label,row_count,distinct_count,"
                                               "null_count,is_unique\nrows,VISIT,,,,10,,,\n")
    assert Checks.from_csv(merged, catalogue, rules).rows["VISIT"] == 520
    # A row whose names are not plain is left out, as the catalogue leaves it out; a wrong layout is refused.
    hostile = text + "dbo\tBad;Table\tX\t1\tint\tNULL\tNULL\tNULL\tYES\t10\n"
    assert {t.name for t in Catalogue.from_csv(first_ask.read(hostile, rules)[0]).tables()} == {"THEATRE_CASE", "VISIT"}
    for bad in ("", "words", "dbo\tVISIT\tVISIT_KEY\n"):
        with pytest.raises(first_ask.FirstAskError):
            first_ask.read(bad, rules)
    # A table whose size comes back empty, as for a view or an account that cannot read the server's records, is
    # recorded as unrecorded at once, so that the table sizes query is not asked for it.
    _, unsized, facts = first_ask.read(_result(["THEATRE_CASE", "VISIT"], {"VISIT": 520}), rules)
    found = Checks.from_csv(unsized, catalogue, rules)
    assert found.rows == {"VISIT": 520} and found.skipped == [("rows", "THEATRE_CASE", "", "unrecorded")]
    assert facts["sized"] == 1


def test_the_first_query_is_never_written_into_any_output(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser.clear()
    browser.first_ask_begin()
    request = HOSTILE + "\nSELECT 1 FROM ZZ_ONLY_IN_A_REQUEST;"
    browser.first_ask_add(request.encode())
    asked = json.loads(browser.first_ask_query())
    assert "ZZ_ONLY_IN_A_REQUEST" in asked["sql"] and asked["names"] == 4
    found = json.loads(browser.first_ask_read(_result(["THEATRE_CASE", "VISIT", "ANAES_RECORD"], {"VISIT": 520}),
                                              RULES.encode()))
    assert found["ok"] and "ZZ_ONLY" not in found["catalogue"] + found["checks"]
    # The analysis runs from the result, and nothing that it writes holds the query or the name.
    browser.start(found["catalogue"].encode(), RULES.encode(), found["checks"].encode())
    browser.add("one.sql", request.encode())
    browser.finish()
    state = tmp_path / "state"
    state.mkdir()
    browser.boundary_begin()
    for name, text in (("catalogue.csv", found["catalogue"]), ("checks.csv", found["checks"]), ("site-rules.json", RULES)):
        browser.boundary_put("state", name, text.encode())
    browser.boundary_put("requests", "one.sql", request.encode())
    result = json.loads(browser.boundary_run())
    assert result["ok"]
    archive = zipfile.ZipFile(io.BytesIO(browser.pack_zip()))
    everything = "".join(archive.read(n).decode() for n in archive.namelist()) + json.dumps(result)
    state_zip = zipfile.ZipFile(io.BytesIO(browser.state_zip()))
    everything += "".join(state_zip.read(n).decode() for n in state_zip.namelist())
    assert "ZZ_ONLY" not in everything and asked["sql"] not in everything
    for word in NEVER:
        assert word not in everything, word
    assert sorted(state_zip.namelist()) == ["catalogue.csv", "checks.csv", "sql_evidence.json"]
    browser.clear()


def test_each_item_says_which_phase_needs_it_and_readiness_is_stated_for_each():
    world = make_checks.WORLD
    sql = (TARGETS / "neonatal_low_mean_pressure.sql").read_text()
    rows, traced = target.checklist(world, CONVERSION, sql, CHECKS, PROFILE)
    phases = {row["question_id"]: row["phase"] for row in rows}
    assert set(phases.values()) == set(target.PHASES)
    for row in rows:
        if row["kind"] in ("core", "meaning", "timing") and not row["question_id"].startswith(("count-", "route-")):
            assert row["phase"] == "release", row["question_id"]
        if row["kind"] in ("table", "column", "relationship"):
            # A table, a column or a join is needed unless the answer does not depend on it.
            assert row["phase"] in ("source", "unneeded"), row["question_id"]
    # The source draft carries the mapping rows, so the codes are needed to answer from the source database.
    assert all(row["phase"] == "source" for row in rows if row["kind"] == "codes" and "SITE_OBS" in row["question_id"])
    text = target.readiness(rows, traced)
    source, release = target.stage_counts(rows, "source"), target.stage_counts(rows)
    assert source["total"] < release["total"]
    assert ("The question is ready to be answered from the source database" in text) == (source["open"] == 0)
    assert "for the OMOP release" in text
    # The audit query reads each source table WITH (NOLOCK), and returns only counts.
    draft = traced["draft"]
    assert draft.count("WITH (NOLOCK)") >= 5 and "omop_measurement WITH" not in draft
    facts = target.draft_facts(draft, Catalogue.from_csv(CATALOGUE))
    assert facts["counts_only"] and {"ANAES_RECORD", "OBS_READING"} <= set(facts["tables"])
    # A question that lists records returns a row for each.
    listing = "SELECT p.person_id, p.year_of_birth FROM omop.person AS p"
    rows, traced = target.checklist(world, CONVERSION, listing, CHECKS, PROFILE)
    assert traced["draft"] and not target.draft_facts(traced["draft"], Catalogue.from_csv(CATALOGUE))["counts_only"]


def test_the_boundary_writes_the_source_draft_only_when_asked(tmp_path):
    import shutil
    state = tmp_path / "state"
    state.mkdir()
    for name, source in (("catalogue.csv", "invented-catalogue.csv"), ("site-rules.json", "invented-site-rules.json")):
        shutil.copy(FIXTURES / source, state / name)
    shutil.copytree(CONVERSION, state / "conversion")
    (state / "targets").mkdir()
    shutil.copy(TARGETS / "neonatal_low_mean_pressure.sql", state / "targets")
    outputs, _ = boundary.produce(state, FIXTURES / "requests")
    assert not any(name.endswith("source_draft.sql") for name in outputs)
    (state / "boundary.json").write_text(json.dumps({"writeSourceDraft": True}))
    outputs, facts = boundary.produce(state, FIXTURES / "requests")
    draft = outputs["targets/neonatal_low_mean_pressure/source_draft.sql"]
    assert "WITH (NOLOCK)" in draft and "for use inside the hospital only" in boundary.summary(facts)
    assert not any(name.startswith("inventory/") and "draft" in name for name in outputs)
