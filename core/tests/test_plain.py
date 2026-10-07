"""The plain form of each check, the queries on the checklist, and the results pasted back."""
import csv
import io
import json
import re
import sys
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from schemalyser import browser, checks as checking, convert, harness, questions, target
from schemalyser.checks import (LAYOUT, MAXIMUM_VALUES, PLAIN_EXACT_ROWS, PLAIN_SAMPLE_ROWS, SAMPLED_KINDS, UNRECORDED,
                                Check, Checks, ChecksError)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CONVERSION = FIXTURES / "conversion"
TARGETS = FIXTURES / "targets"
sys.path.insert(0, str(FIXTURES))
import answer_queries  # noqa: E402
import make_checks  # noqa: E402

PLANTED = [line for line in (FIXTURES / "planted-values.txt").read_text().splitlines() if line.strip()]
CHECKS = (FIXTURES / "invented-checks.csv").read_text()
NOT_PLAIN = ("@", "EXEC", "SP_EXECUTESQL", "DECLARE", "SET ", "#", "TRY", "CATCH", "INSERT", "INTO ", "UPDATE ",
             "DELETE ", "MERGE ", "DROP ", "CREATE ", "ALTER ", "TRUNCATE ", "GO\n", "BEGIN", "OPENQUERY", "OPENROWSET")


@pytest.fixture(scope="module")
def analysis():
    """The invented world's analysis, with the conversion added as one more request, as the boundary plans it."""
    found = make_checks.analysis()
    found.add_request("conversion", convert.as_request(
        [(step["table"], sql) for step, sql in questions._steps(CONVERSION)],
        questions._definitions((FIXTURES / "invented-site-rules.json").read_text())))
    return found


@pytest.fixture(scope="module")
def planned(analysis):
    return analysis.planned_checks(include_years=True, include_spans=True, include_fanout=True)


@pytest.fixture(scope="module")
def con():
    return answer_queries.stand_in()


def _without_comments(sql):
    return "\n".join(line for line in sql.splitlines() if not line.startswith("--"))


def _is_one_select(sql):
    # sqlglot cannot read a table hint after TABLESAMPLE, which SQL Server accepts, so the hint is set aside here.
    statements = [s for s in sqlglot.parse(sql.replace(" WITH (NOLOCK)", ""), dialect="tsql") if s is not None]
    return len(statements) == 1 and isinstance(statements[0], (exp.Select, exp.Union))


# The plain form.

def test_each_plain_query_is_one_select_with_nothing_but_reading_in_it(analysis, planned):
    kinds = set()
    for check in planned:
        for percent in ([None, 7] if check.kind in SAMPLED_KINDS else [None]):
            sql = check.plain(analysis.catalogue, percent)
            body = _without_comments(sql).upper()
            assert _is_one_select(sql), check.key()
            for word in NOT_PLAIN:
                assert word not in body, (check.key(), word)
            # Every table that it reads is read without taking locks.
            assert body.count("FROM [") == body.count("WITH (NOLOCK)") > 0
            # The rounding and the least count are written into the query as numbers.
            assert "/ 10) * 10" in body
            if check.kind not in ("rows", "column"):
                assert f">= {checking.MINIMUM_COUNT}" in body
            if check.kind == "values":
                limit = check.maximum()
                assert body.count(f"TOP ({limit + 1})") == 2 and f"<= {limit}" in body
            if not percent and check.kind in checking.RAN_KINDS:
                assert f"SELECT 'RAN', '{check.table}', '{check.column}', '{check.kind.upper()}'," in body
            if percent:
                assert body.count(f"TABLESAMPLE SYSTEM ({percent} PERCENT) REPEATABLE ({checking.SAMPLE_SEED}) WITH (NOLOCK)") >= 1
                assert f"(CAST(G.N * 100.0 / {percent} AS BIGINT) / 10) * 10" in body
                # The guard and the main query read the same pages, because both use the same seed.
                assert body.count(f"REPEATABLE ({checking.SAMPLE_SEED})") == (2 if check.kind == "values" else 1)
                assert f"SELECT 'SAMPLED', '{check.table}', '{check.column}', '{check.kind.upper()}', NULL, {percent}," in body
            for value in PLANTED:
                assert value.lower() not in sql.lower(), value
            kinds.add(check.kind)
    assert kinds >= {"column", "values", "years", "spans", "fanout"}
    # A count of the rows of a table, which the checklist offers where the server records no size.
    count = Check("rows", "THEATRE_CASE").plain(analysis.catalogue)
    assert _is_one_select(count) and "FROM [dbo].[THEATRE_CASE] WITH (NOLOCK)" in count
    assert "reads the whole of THEATRE_CASE" in count


def test_the_size_query_reads_only_the_servers_own_records(analysis):
    sql = checking.size_query(analysis.catalogue, ["OBS_READING", "ANAES_RECORD", "OBS_READING"])
    body = _without_comments(sql)
    assert _is_one_select(sql)
    for word in NOT_PLAIN:
        assert word not in body.upper(), word
    assert "FROM (VALUES (N'dbo', N'ANAES_RECORD'),\n             (N'dbo', N'OBS_READING')) AS n" in body
    assert "LEFT JOIN sys.tables AS t" in body and "LEFT JOIN sys.partitions AS p" in body
    assert "p.index_id IN (0, 1)" in body and "(SUM(p.rows) / 10) * 10 AS row_count" in body
    # No table is read: the only names after FROM or JOIN are the server's own records.
    assert set(re.findall(r"(?:FROM|JOIN) (\S+)", body)) == {"(VALUES", "sys.tables", "sys.partitions"}
    assert checking.size_query(analysis.catalogue, []) == ""


def test_each_plain_query_gives_the_same_rows_as_the_exact_form(analysis, planned, con):
    exact = []
    for check in planned:
        if check.kind == "values":
            guard = check.guard(analysis.catalogue).replace("@maximum_definitions", str(checking.MAXIMUM_DEFINITIONS))
            guard = guard.replace("@maximum_values", str(MAXIMUM_VALUES))
            if answer_queries.rows(guard, con)[0][0] and int(answer_queries.rows(guard, con)[0][0]) > check.maximum():
                continue
        select = check.select(analysis.catalogue).replace("@minimum_count", str(checking.MINIMUM_COUNT))
        names = [n.strip() for n in check.columns().strip("()").split(",")]
        for values in answer_queries.rows(select, con):
            row = dict.fromkeys(LAYOUT, "NULL")
            row.update(dict(zip(names, values)))
            exact.append(tuple(row[n] for n in LAYOUT))
    every = [tuple(row) for check in planned for row in answer_queries.rows(check.plain(analysis.catalogue), con)]
    plain = [row for row in every if row[0] != "ran"]
    assert len(exact) > 100 and sorted(plain) == sorted(exact)
    # Each query that can find nothing adds one row that records that it ran.
    ran = [row for row in every if row[0] == "ran"]
    assert len(ran) == sum(1 for check in planned if check.kind in checking.RAN_KINDS)
    assert len(Checks.from_pasted("\n".join("\t".join(row) for row in every), analysis.catalogue, analysis.rules).ran) == len(ran)
    # Every kind of check gives rows, so that each is compared.
    assert {row[0] for row in plain} == {"column", "values", "years", "spans", "fanout"}
    # A sampled form of the whole table gives the same counts, with one row that records the sample.
    for check in planned:
        if check.kind in SAMPLED_KINDS:
            whole = answer_queries.rows(check.plain(analysis.catalogue, 100).replace(checking.sample_clause(100), ""), con)
            assert sorted(map(tuple, whole)) == sorted(
                [tuple(r) for r in answer_queries.rows(check.plain(analysis.catalogue), con) if r[0] != "ran"]
                + [("sampled", check.table, check.column, check.kind, "NULL", "100", "NULL", "NULL", "NULL")])


# The size rules.

def _sizes(**tables):
    text = "\n".join(f"rows,{name},,,,{count},,," for name, count in tables.items())
    world = make_checks.WORLD.analysis()
    return Checks.from_csv(",".join(LAYOUT) + "\n" + text + "\n", world.catalogue, world.rules)


def test_a_table_of_unknown_size_gets_only_the_size_query_and_a_large_table_gets_a_sample(analysis, planned):
    catalogue = analysis.catalogue
    values = next(c for c in planned if c.kind == "values" and c.table == "OBS_READING" and not c.definition)
    labelled = next(c for c in planned if c.kind == "values" and c.definition and c.table == "OBS_READING")
    spans = next(c for c in planned if c.kind == "spans" and c.table == "ANAES_RECORD")
    column = next(c for c in planned if c.kind == "column" and c.table == "OBS_READING")
    fanout = next(c for c in planned if c.kind == "fanout" and c.table == "OBS_READING")
    # Without check results, every check waits for the size query, and nothing else is offered.
    for check in (values, labelled, spans, column, fanout):
        assert checking.offer(check, catalogue, None) == ("sizes", [])
    # A definition key reads its definition table as well, so it waits for that table's size too.
    assert checking.offer(labelled, catalogue, _sizes(OBS_READING=640)) == ("sizes", [])
    # A small table gets the exact query.
    small = _sizes(OBS_READING=640, OBS_TYPE_DEF=20, ANAES_RECORD=600)
    for check in (values, labelled, spans, column, fanout):
        state, offered = checking.offer(check, catalogue, small)
        assert state == "exact" and offered == [(check, check.plain(catalogue))]
    # A large table gets a sample of about five million rows for a values, years or spans check, and no
    # query at all for a column or fanout check, which needs every row.
    large = _sizes(OBS_READING=200_000_000, OBS_TYPE_DEF=20, ANAES_RECORD=PLAIN_EXACT_ROWS + 10)
    state, [(check, sql)] = checking.offer(values, catalogue, large)
    assert state == "sampled" and "TABLESAMPLE SYSTEM (2.5 PERCENT) REPEATABLE (20261005) WITH (NOLOCK)" in sql
    assert checking.sample_percent(250_000_000) * 250_000_000 / 100 == PLAIN_SAMPLE_ROWS
    # At any size the sample holds about five million rows, through a fraction of a per cent where it must.
    assert checking.sample_percent(6_000_000_000) == 0.08333 and checking.percent_text(0.08333) == "0.08333"
    state, [(check, sql)] = checking.offer(spans, catalogue, large)
    assert state == "sampled" and "TABLESAMPLE SYSTEM (50 PERCENT)" in sql
    assert checking.sample_percent(10 ** 12) == 0.0005
    for check in (column, fanout):
        assert checking.offer(check, catalogue, large) == ("large", [])
    # A table at the limit is not large.
    assert checking.offer(column, catalogue, _sizes(OBS_READING=PLAIN_EXACT_ROWS))[0] == "exact"
    # Where the server keeps no record of the size, as for a view, the table is counted first, and the count
    # query reads it only up to just past the limit.
    unrecorded = Checks.from_pasted(f"skipped\tOBS_READING\tNULL\trows\t{UNRECORDED}\tNULL\tNULL\tNULL\tNULL\n",
                                    catalogue, analysis.rules)
    assert unrecorded.skipped == [("rows", "OBS_READING", "", UNRECORDED)]
    for check in (values, column):
        state, offered = checking.offer(check, catalogue, unrecorded)
        assert state == "count" and offered == [(Check("rows", "OBS_READING"), Check("rows", "OBS_READING").plain(catalogue, bounded=True))]
        assert f"SELECT TOP ({PLAIN_EXACT_ROWS + 10}) 1 AS x" in offered[0][1]
    # Once the count is in, the check is offered by its size.
    counted = unrecorded.merged(_sizes(OBS_READING=640))
    assert counted.skipped == [] and checking.offer(values, catalogue, counted)[0] == "exact"
    # A count that passes the limit comes back as skipped for its size, and no query then reads the table, because a
    # view cannot be read in part.
    over = unrecorded.merged(Checks.from_pasted("skipped\tOBS_READING\tNULL\trows\tsize\tNULL\tNULL\tNULL\tNULL\n",
                                                catalogue, analysis.rules))
    assert checking.too_large("OBS_READING", over) and not checking.unrecorded("OBS_READING", over)
    for check in (values, column):
        assert checking.offer(check, catalogue, over) == ("unsampled", [])


# Pasted results.

def test_pasted_results_are_read_from_a_grid_or_a_csv_file(analysis):
    catalogue, rules = analysis.catalogue, analysis.rules
    header = "\t".join(LAYOUT)
    grid = ("values\tTHEATRE_CASE\tSERVICE_CAT\t3\tNULL\t120\tNULL\tNULL\tNULL\r\n"
            "values\tTHEATRE_CASE\tSERVICE_CAT\t7\tNULL\t40\tNULL\tNULL\tNULL\r\n")
    with_header = Checks.from_pasted(header + "\r\n" + grid, catalogue, rules)
    without = Checks.from_pasted(grid, catalogue, rules)
    as_csv = Checks.from_pasted(",".join(LAYOUT) + "\n" + grid.replace("\t", ",").replace("\r", ""), catalogue, rules)
    for found in (with_header, without, as_csv):
        assert found.values == {("THEATRE_CASE", "SERVICE_CAT"): [("3", "", 120), ("7", "", 40)]}
        assert (found.read, found.accepted) == (2, 2)
    # The results of several queries can be pasted together, each with its header, and the count of rows
    # that sqlcmd prints is passed over.
    two = (header + "\n" + grid + "\n(2 rows affected)\n" + header + "\n"
           + "rows\tTHEATRE_CASE\tNULL\tNULL\tNULL\t530\tNULL\tNULL\tNULL\n")
    found = Checks.from_pasted(two, catalogue, rules)
    assert found.rows == {"THEATRE_CASE": 530} and len(found.values[("THEATRE_CASE", "SERVICE_CAT")]) == 2
    for nothing in ("", "\n\n", header, "just some words"):
        with pytest.raises(ChecksError):
            Checks.from_pasted(nothing, catalogue, rules)


def test_hostile_pasted_rows_are_left_out_as_from_csv_leaves_them_out(analysis):
    catalogue, rules = analysis.catalogue, analysis.rules
    good = "values\tTHEATRE_CASE\tSERVICE_CAT\t3\tNULL\t120\tNULL\tNULL\tNULL"
    hostile = [
        "values\tSTAFF_MASTER\tSTAFF_GRADE\tConsultant\tNULL\t120\tNULL\tNULL\tNULL",   # a table of people
        "values\tTHEATRE_CASE\tSERVICE_CAT\t4\tNULL\t123\tNULL\tNULL\tNULL",           # a count not rounded
        "values\tTHEATRE_CASE\tSERVICE_CAT\t5\tNULL\t0\tNULL\tNULL\tNULL",             # a count under ten
        "values\tSECRET_TABLE\tSERVICE_CAT\t6\tNULL\t120\tNULL\tNULL\tNULL",           # a table not in the catalogue
        "values\tTHEATRE_CASE\tSERVICE_CAT\t=HYPERLINK(1)\tNULL\t120\tNULL\tNULL\tNULL",  # a formula
        "values\tPERSON_MASTER\tGIVEN_NAME\tAlice\tNULL\t120\tNULL\tNULL\tNULL",        # a column named like a name
        "sampled\tTHEATRE_CASE\tSERVICE_CAT\tvalues\tNULL\t500\tNULL\tNULL\tNULL",      # a share that is no percentage
        "skipped\tTHEATRE_CASE\tNULL\trows\tbecause\tNULL\tNULL\tNULL\tNULL",          # a reason that is not fixed
    ]
    found = Checks.from_pasted("\n".join([good] + hostile), catalogue, rules)
    assert found.values == {("THEATRE_CASE", "SERVICE_CAT"): [("3", "", 120)]}
    assert (found.read, found.accepted) == (1 + len(hostile), 1)
    assert not found.sampled and not found.skipped and not found.rows
    # A pasted values result that reaches the limit is refused as a whole.
    many = "\n".join(f"values\tTHEATRE_CASE\tSERVICE_CAT\t{n}\tNULL\t10\tNULL\tNULL\tNULL" for n in range(MAXIMUM_VALUES + 1))
    found = Checks.from_pasted(many, catalogue, rules)
    assert found.values == {} and found.accepted == 0
    # A row with the wrong number of columns refuses the whole paste, as it refuses a file.
    with pytest.raises(ChecksError):
        Checks.from_pasted(good + "\tone more", catalogue, rules)
    with pytest.raises(ChecksError):
        Checks.from_pasted("DROP TABLE\tTHEATRE_CASE\tNULL\tNULL\tNULL\t10\tNULL\tNULL\tNULL", catalogue, rules)


def test_a_later_result_for_the_same_check_replaces_the_earlier_one(analysis):
    catalogue, rules = analysis.catalogue, analysis.rules
    earlier = Checks.from_csv(CHECKS, catalogue, rules)
    assert len(earlier.values[("OBS_READING", "OBS_TYPE_KEY")]) == 10
    later = Checks.from_pasted("values\tOBS_READING\tOBS_TYPE_KEY\t14\tWeight\t500\tNULL\tNULL\tNULL\n"
                               "sampled\tOBS_READING\tOBS_TYPE_KEY\tvalues\tNULL\t5\tNULL\tNULL\tNULL\n"
                               "rows\tAIRWAY_DEVICE\tNULL\tNULL\tNULL\t530\tNULL\tNULL\tNULL\n", catalogue, rules)
    merged = earlier.merged(later)
    assert merged.values[("OBS_READING", "OBS_TYPE_KEY")] == [("14", "Weight", 500)]
    assert merged.sampled == [("values", "OBS_READING", "OBS_TYPE_KEY", 5)]
    assert merged.rows["AIRWAY_DEVICE"] == 530
    # Everything else is kept.
    others = {k: v for k, v in earlier.values.items() if k != ("OBS_READING", "OBS_TYPE_KEY")}
    assert {k: v for k, v in merged.values.items() if k != ("OBS_READING", "OBS_TYPE_KEY")} == others
    assert merged.columns == earlier.columns and merged.years == earlier.years
    # An exact result replaces the sampled one, and the merged results survive being written and read again.
    exact = Checks.from_pasted("values\tOBS_READING\tOBS_TYPE_KEY\t14\tWeight\t520\tNULL\tNULL\tNULL\n", catalogue, rules)
    again = merged.merged(exact)
    assert again.sampled == [] and again.values[("OBS_READING", "OBS_TYPE_KEY")] == [("14", "Weight", 520)]
    read_back = Checks.from_csv(again.to_csv(), catalogue, rules)
    assert read_back.values == again.values and read_back.rows == again.rows


# The checklist.

def _offered(rows, traced):
    return traced["queries"]["sizes"], traced["queries"]["queries"]


def test_the_checklists_carry_the_queries_and_tick_when_their_results_are_given(con):
    world = make_checks.WORLD
    counted = {}
    for path in sorted(TARGETS.glob("*.sql")):
        sql = path.read_text()
        rows, traced = target.checklist(world, CONVERSION, sql)
        sizes, queries = _offered(rows, traced)
        # Without check results the checklist asks for the table sizes and nothing else.
        assert sizes and sizes["sql"].startswith("-- This query reads from SQL Server's own records") and not queries
        # The core items carry the core profile's queries, which the central OMOP team runs; they are tested apart.
        rows = [r for r in rows if r["kind"] != "core"]
        waiting = [r for r in rows if r["query_state"] and r["question_id"] != "count-by-year"]
        assert waiting and {r["query_state"] for r in waiting} == {"waiting"}
        assert all(not r["query"] and "table sizes query" in r["query_reason"] for r in waiting)
        assert all(r["status"] != "answered" for r in waiting)
        # The sizes are given, and the queries appear, each once, beside the items that need them.
        known = Checks.from_pasted(answer_queries.answer([sizes["sql"]], con), world.analysis().catalogue,
                                   world.analysis().rules)
        rows, traced = target.checklist(world, CONVERSION, sql, known.to_csv())
        rows = [r for r in rows if r["kind"] != "core"]
        sizes, queries = _offered(rows, traced)
        assert sizes is None and queries
        ids = [q["id"] for q in queries]
        assert len(ids) == len(set(ids)) and {q["state"] for q in queries} == {"exact"}
        for row in rows:
            for key in [k for k in row["_queries"] if k != "yearcount"]:
                assert key in ids and next(q["sql"] for q in queries if q["id"] == key) in row["query"]
            assert bool(row["query"]) == (row["query_state"] == "ready")
        offered = [r for r in rows if r["query_state"]]
        assert {r["kind"] for r in offered} <= {"filter", "codes", "timing"}
        # Only the checks that an item of this target needs are offered.
        assert {key for r in rows for key in r["_queries"] if key != "yearcount"} == set(ids)
        # Their results are given, and the items tick.
        answered = known.merged(Checks.from_pasted(answer_queries.answer([q["sql"] for q in queries], con),
                                                   world.analysis().catalogue, world.analysis().rules))
        after, traced = target.checklist(world, CONVERSION, sql, answered.to_csv())
        before = {r["question_id"]: r["status"] for r in rows}
        ticked = [r["question_id"] for r in after if r["status"] == "answered" and before[r["question_id"]] != "answered"]
        assert ticked, path.name
        assert not traced["queries"]["queries"] and traced["queries"]["sizes"] is None
        counted[path.stem] = len(ids)
        # The file of queries that the boundary writes holds the same queries.
        written = target.queries_file(path.stem, {"sizes": None, "queries": queries})
        assert written.count("WITH (NOLOCK)") == sum(q["sql"].count("WITH (NOLOCK)") for q in queries)
    assert counted["neonatal_low_mean_pressure"] >= counted["airway_by_anaesthesia_type"] > 0


def test_an_item_with_a_large_table_says_how_to_answer_instead():
    world = make_checks.WORLD
    sql = (TARGETS / "neonatal_low_mean_pressure.sql").read_text()
    catalogue = world.analysis().catalogue
    sizes = {t.name: 600 for t in catalogue.tables()}
    sizes["PERSON_MASTER_2"] = PLAIN_EXACT_ROWS * 3
    sizes["ANAES_RECORD"] = PLAIN_EXACT_ROWS * 3
    text = ",".join(LAYOUT) + "\n" + "".join(f"rows,{name},,,,{count},,,\n" for name, count in sizes.items())
    rows, traced = target.checklist(world, CONVERSION, sql, text)
    found = {r["question_id"]: r for r in rows}
    # The share of deaths needs every row of a large table, so no query is offered, and the item says how to answer instead.
    death = found["B-tuning-death_share"]
    assert death["query_state"] == "large" and not death["query"] and not death["_queries"]
    assert "holds more than 10,000,000 rows" in death["query_reason"] and "under tuning in the site rules" in death["query_reason"]
    # The durations can be counted from a sample.
    duration = found["B-tuning-anaesthetic_duration"]
    assert duration["query_state"] == "ready" and "TABLESAMPLE SYSTEM (16.67 PERCENT) REPEATABLE (20261005) WITH (NOLOCK)" in duration["query"]
    assert "reads only a random sample of it" in duration["query_reason"]
    assert "column:PERSON_MASTER_2.DEATH_TS" not in {q["id"] for q in traced["queries"]["queries"]}


# The page's entry point.

def test_the_page_reads_a_pasted_result_merges_it_and_works_the_checklist_out_again(monkeypatch, tmp_path):
    from schemalyser import Analysis
    state, requests = tmp_path / "state", FIXTURES / "requests"
    state.mkdir()
    for name, source in (("catalogue.csv", "invented-catalogue.csv"), ("site-rules.json", "invented-site-rules.json"),
                         ("checks.csv", "invented-checks.csv")):
        (state / name).write_bytes((FIXTURES / source).read_bytes())
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path / "worker"))
    browser._analysis = Analysis((FIXTURES / "invented-catalogue.csv").read_text(),
                                 (FIXTURES / "invented-site-rules.json").read_text())
    browser.boundary_begin()
    for kind, folder, prefix in (("state", state, ""), ("state", CONVERSION, "conversion/"), ("state", TARGETS, "targets/"),
                                 ("requests", requests, "")):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            assert browser.boundary_put(kind, prefix + path.relative_to(folder).as_posix(), path.read_bytes())
    first = json.loads(browser.boundary_run())
    airway = next(t for t in first["targets"] if t["name"] == "airway_by_anaesthesia_type")
    assert airway["sizes"]["tables"] == ["AIRWAY_DEVICE"]
    # Each checklist is headed by its question in plain words, and offers only the decisions that can change its answer.
    assert airway["title"] == "Which airway devices are placed during each type of anaesthetic?" and airway["decisions"] == []
    neonatal = next(t for t in first["targets"] if t["name"] == "neonatal_low_mean_pressure")
    assert neonatal["decisions"] == ["pressures", "floor", "ceiling", "isolated", "bypass", "ecmo", "age"]
    # Codes that came with the folder for this audit are said to have come with it, in the order of their numbers, and an
    # unconfirmed meaning is shown as the folder's assumption.
    chosen = next(row["ask"] for row in neonatal["rows"] if (row.get("ask") or {}).get("choose"))
    assert "came with the folder for this audit" in chosen["text"] and "you pasted" not in chosen["text"]
    codes = [code for code, _ in chosen["choose"]]
    assert codes == sorted(codes, key=lambda c: (not c.isdecimal(), int(c) if c.isdecimal() else 0, c))
    assert "Invasive Mean blood pressure" in chosen["assumed"]["52"]
    ready = [q for q in airway["queries"] if q["state"] == "exact"]
    assert [q["id"] for q in ready] == ["values:ANAES_RECORD.ANAES_KIND_CAT"]
    items = {row["id"]: row for row in airway["rows"]}
    kind_items = [i for i, row in items.items() if "values:ANAES_RECORD.ANAES_KIND_CAT" in row["queryIds"]]
    # Each kind rests on a code that only the conversion's folder gives, so it stays open until a result lists the code.
    assert kind_items and all(items[i]["status"] == "open" and items[i]["queryState"] == "ready" for i in kind_items)
    assert first["checks"] == Checks.from_csv(CHECKS, browser._analysis.catalogue, browser._analysis.rules).to_csv()
    # Nothing that can be read is refused as a whole, and nothing is changed.
    assert json.loads(browser.checks_paste("just some words")) == {"ok": False}
    unchanged = json.loads(browser.checks_paste("values\tSECRET\tX\t1\tNULL\t10\tNULL\tNULL\tNULL"))
    assert unchanged == {"ok": True, "pasted": {"read": 1, "accepted": 0}, "boundary": None}
    # The result of the ready query, and of the size query, are pasted together.
    pasted = answer_queries.answer([ready[0]["sql"], airway["sizes"]["sql"]])
    result = json.loads(browser.checks_paste(pasted))
    assert result["ok"] and result["pasted"]["accepted"] == result["pasted"]["read"] > 0
    again = next(t for t in result["boundary"]["targets"] if t["name"] == "airway_by_anaesthesia_type")
    after = {row["id"]: row for row in again["rows"]}
    # The values are in, so no query is needed for the kinds; a person still reads the codes and chooses them on the page.
    assert all(after[i]["status"] == "open" and not after[i]["queryIds"] and after[i]["ask"]["choose"] for i in kind_items)
    # The codes of the kinds came from the result that was pasted, and the question says so.
    assert all("The result that you pasted lists the codes" in after[i]["ask"]["text"] for i in kind_items)
    # The size is in, so the query that waited for it is offered now.
    assert again["sizes"] is None and [q["id"] for q in again["queries"]] == ["values:AIRWAY_DEVICE.DEVICE_KIND_KEY"]
    merged = Checks.from_csv(result["boundary"]["checks"], browser._analysis.catalogue, browser._analysis.rules)
    assert ("ANAES_RECORD", "ANAES_KIND_CAT") in merged.values and merged.rows["AIRWAY_DEVICE"] > 0
    assert merged.values[("OBS_READING", "OBS_TYPE_KEY")] == Checks.from_csv(
        CHECKS, browser._analysis.catalogue, browser._analysis.rules).values[("OBS_READING", "OBS_TYPE_KEY")]
    # The boundary's own files follow the merged results, and the state names no commit any more.
    assert "AIRWAY_DEVICE.DEVICE_KIND_KEY" in browser._boundary["files"]["targets/airway_by_anaesthesia_type/queries.sql"]
    assert json.loads(browser._boundary["files"]["provenance.json"])["stateCommit"] is None
    # A new analysis keeps what was pasted, until the page is cleared.
    browser.boundary_begin()
    for kind, folder, prefix in (("state", state, ""), ("state", CONVERSION, "conversion/"), ("state", TARGETS, "targets/"),
                                 ("requests", requests, "")):
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            browser.boundary_put(kind, prefix + path.relative_to(folder).as_posix(), path.read_bytes())
    kept = json.loads(browser.boundary_run())
    assert kept["checks"] == result["boundary"]["checks"]
    browser.clear()
    assert browser._pasted is None and browser._checks is None


def test_a_query_that_finds_nothing_is_recorded_and_not_offered_again(analysis):
    world = make_checks.WORLD
    catalogue, rules = analysis.catalogue, analysis.rules
    sql = (TARGETS / "airway_by_anaesthesia_type.sql").read_text()
    # The state's check results, with the size of AIRWAY_DEVICE, and the query on DEVICE_KIND_KEY run and found empty.
    earlier = Checks.from_csv(CHECKS, catalogue, rules)
    nothing = Checks.from_pasted("rows\tAIRWAY_DEVICE\tNULL\tNULL\tNULL\t530\tNULL\tNULL\tNULL\n"
                                 "ran\tAIRWAY_DEVICE\tDEVICE_KIND_KEY\tvalues\tNULL\tNULL\tNULL\tNULL\tNULL\n", catalogue, rules)
    assert nothing.ran == [("values", "AIRWAY_DEVICE", "DEVICE_KIND_KEY", "")]
    check = Check("values", "AIRWAY_DEVICE", "DEVICE_KIND_KEY")
    merged = earlier.merged(nothing)
    assert checking.ran_empty(check, merged) is True and checking.held(check, merged)
    assert checking.ran_empty(check, earlier) is None
    rows, traced = target.checklist(world, CONVERSION, sql, merged.to_csv())
    assert "values:AIRWAY_DEVICE.DEVICE_KIND_KEY" not in {q["id"] for q in traced["queries"]["queries"]}
    device = [r for r in rows if r["question_id"].startswith("codes-device_exposure")]
    assert device and all(r["query_state"] == "ran" and "has run and found nothing" in r["query_reason"] for r in device)
    assert all("The query for AIRWAY_DEVICE.DEVICE_KIND_KEY has run and found no value" in r["evidence_in_hand"] for r in device)
    # A later result that finds values replaces the record that the query found nothing.
    found = merged.merged(Checks.from_pasted("values\tAIRWAY_DEVICE\tDEVICE_KIND_KEY\t1\tNULL\t20\tNULL\tNULL\tNULL\n"
                                             "ran\tAIRWAY_DEVICE\tDEVICE_KIND_KEY\tvalues\tNULL\tNULL\tNULL\tNULL\tNULL\n",
                                             catalogue, rules))
    assert checking.ran_empty(check, found) is False
    # A record of a run that names no catalogue column, or a kind that cannot find nothing, is left out.
    hostile = Checks.from_pasted("ran\tAIRWAY_DEVICE\tSECRET\tvalues\tNULL\tNULL\tNULL\tNULL\tNULL\n"
                                 "ran\tAIRWAY_DEVICE\tDEVICE_KIND_KEY\tcolumn\tNULL\tNULL\tNULL\tNULL\tNULL\n"
                                 "ran\tANAES_RECORD\tANAES_START_TS\tspans\tSECRET\tNULL\tNULL\tNULL\tNULL\n", catalogue, rules)
    assert hostile.ran == [] and hostile.accepted == 0
