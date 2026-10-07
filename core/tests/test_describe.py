"""Screen 1, describing the record: the hospital folder from the invented dictionary and the invented world.

The sitting is walked through as the page walks it: the dictionary is loaded, the map proposed, the tables and columns
query read, bindings confirmed, the codes chosen from the list of what is charted, and the counts read. The queries
that the screen writes are run on the invented world's shadow in DuckDB, so that they are known to run and to give
what the page expects. The folder is then written, read back into a new sitting, and checked. No description from the
dictionary may appear in any query, and the dictionary's own files go into the folder only when a person asks.
"""
import csv
import io
import json
import re
import sys
from pathlib import Path

import pytest

from schemalyser import describe, rolemap
from schemalyser.translate import to_duckdb

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
DICTIONARY = FIXTURES / "dictionary" / "invented-dictionary.csv"
TABLES = FIXTURES / "dictionary" / "invented-tables.csv"
CATALOGUE = FIXTURES / "invented-catalogue.csv"
DATE = "2026-10-07"


def tables_result(sizes=None, leave_out=()):
    """The tables and columns query's result for the invented world, as SQL Server Management Studio copies it."""
    sizes = sizes or {}
    lines = ["\t".join(["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE", "CHARACTER_MAXIMUM_LENGTH",
                        "NUMERIC_PRECISION", "NUMERIC_SCALE", "IS_NULLABLE", "TABLE_ROWS"])]
    for row in csv.DictReader(CATALOGUE.open()):
        if row["TABLE_NAME"] in leave_out:
            continue
        cells = [row[k] or "NULL" for k in ("TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE",
                                            "CHARACTER_MAXIMUM_LENGTH", "NUMERIC_PRECISION", "NUMERIC_SCALE", "IS_NULLABLE")]
        lines.append("\t".join(cells + [str(sizes.get(row["TABLE_NAME"], 1000))]))
    return "\n".join(lines) + "\n"


def descriptions():
    from schemalyser import datadict
    dictionary = datadict.load(DICTIONARY, TABLES)
    found = []
    for table in dictionary.tables():
        found.append(dictionary.description(table.name))
        found += [dictionary.description(table.name, c.name) for c in table.columns.values()]
    return [d for d in found if d and len(d.split()) >= 6]


def leaks(text):
    plain = " ".join(text.split())
    return [d for d in descriptions() if any(" ".join(d.split()[i:i + 6]) in plain for i in range(len(d.split()) - 5))]


@pytest.fixture(scope="module")
def world():
    sys.path.insert(0, str(FIXTURES))
    import make_checks
    from schemalyser import convert
    converted, _ = convert.run(make_checks.WORLD, FIXTURES / "conversion", 200)
    return converted


def run(world, sql):
    """The rows of a query or script's last statement on the invented world, as the page's tests run scripts."""
    sql = re.sub(r"ISNULL\(a\.anaesthetic_key, 0\)", "a.anaesthetic_key", sql)
    sql = re.sub(r">= 10 THEN", ">= 0 THEN", sql)
    statements = [s for s in to_duckdb(sql, world.sandbox.date_columns) if not s.upper().startswith("ALTER TABLE")]
    try:
        for statement in statements:
            cursor = world.con.execute(statement)
            rows = cursor.fetchall()
            columns = [c[0] for c in cursor.description]
    finally:
        world.con.execute("DROP TABLE IF EXISTS temp_cohort")
    return columns, rows


def grid(columns, rows):
    return "\n".join("\t".join("NULL" if v is None else str(v) for v in row) for row in [columns, *rows]) + "\n"


@pytest.fixture(scope="module")
def sitting(world):
    s = describe.Describe()
    s.version = "test"
    receipt = s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv",
                                "invented-tables.csv", "2. Load the data dictionary")
    assert receipt["columns"] == 98 and receipt["tables"] == 25
    seen = []
    s.propose(progress=lambda done, total: seen.append((done, total)), date=DATE)
    assert seen[0] == (0, len(rolemap.contract()["views"])) and seen[-1][0] == seen[-1][1]
    return s


def test_the_tables_query_names_the_proposal_s_tables_and_its_result_marks_each_binding(sitting):
    query = sitting.tables_query("5. Run the tables and columns query")
    assert query["sql"].startswith("-- Written by Schemalyser test on ")
    assert "INFORMATION_SCHEMA.COLUMNS" in query["sql"] and "N'OBS_READING'" in query["sql"] and "N'OBS_TYPE_DEF'" in query["sql"]
    receipt = sitting.read_tables(tables_result({"OBS_READING": 25_000_000}, leave_out=("DRUG_DEF",)))
    assert receipt["absent"] >= 1 and receipt["doubt"] == ""
    model = sitting.view()
    items = {i["about"]: i for r in model["roles"] for i in r["items"]}
    assert items["role_patient.birth_date"]["presence"]["state"] == "present"
    assert items["role_reading.value"]["presence"] == {"state": "large", "missing": [], "large": [["OBS_READING", 25_000_000]],
                                                       "rows": 25_000_000}
    # The definition shown beside a binding is the dictionary's own, and the alternatives carry theirs.
    assert items["role_patient.birth_date"]["definition"] == "The date and time on which the patient was born."
    assert all("replacement" in c for i in items.values() for c in i["candidates"])
    with pytest.raises(describe.DescribeError):
        sitting.read_tables("not a result")


def test_answers_are_recorded_with_their_date_and_counted(sitting):
    sitting.confirm("role_patient.birth_date", "yes", date=DATE)
    sitting.confirm("role_patient rows", "yes", date=DATE)
    sitting.confirm("role_reading.value", "not sure", date=DATE)
    # A replacement written by hand is checked against the dictionary and its reach.
    with pytest.raises(describe.DescribeError, match="holds no column"):
        sitting.confirm("role_anaesthetic.patient_key", "no", "THEATRE_CASE.NO_SUCH", date=DATE)
    with pytest.raises(describe.DescribeError, match="TABLE.COLUMN"):
        sitting.confirm("role_anaesthetic.patient_key", "no", "THEATRE_CASE", date=DATE)
    sitting.confirm("role_anaesthetic.patient_key", "no",
                    "VISIT.PERSON_KEY via ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY then THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY",
                    date=DATE)
    tally = sitting.tally()
    assert (tally["confirmed"], tally["corrected"], tally["not_sure"]) == (2, 1, 1)
    assert tally["remaining"] == tally["total"] - 4
    assert [q["about"] for q in sitting.questions()] == ["role_reading.value"]
    patient = sitting.data["roles"]["role_anaesthetic"]["columns"]["patient_key"]
    assert patient["status"] == "person" and patient["confirmation"]["date"] == DATE
    assert "LEFT JOIN THEATRE_CASE" in sitting.view_sql("role_anaesthetic")
    assert [c["answer"] for c in sitting.confirmations] == ["yes", "yes", "not sure", "no"]


def test_the_list_of_what_is_charted_runs_from_the_cohort_and_the_chosen_codes_reach_the_views(sitting, world):
    keys = {v["key"]: v for v in sitting.vocabularies()}
    assert keys["role_reading.kind"]["lookup"] == ["OBS_TYPE_DEF", "OBS_LABEL"] and not keys["role_reading.kind"]["reason"]
    years = [r[0] for r in run(world, sitting.count_queries(2024)[0]["sql"])[1]]
    year = max(years)
    found = sitting.charted_query("role_reading.kind", year, "7. Settle the local codes")
    sql = found["sql"]
    part1, _, part2 = sql.partition("ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);")
    assert "INTO   #cohort" in part1 and "OBS_READING" not in part1 and "OBS_SHEET" not in part1
    # Part 2 starts from #cohort and joins the table of readings last, by key, before the lookup of names.
    joins = re.findall(r"JOIN\s+(\w+)", part2)
    assert joins[:2] == ["OBS_SHEET", "OBS_READING"] and joins[-1] == "OBS_TYPE_DEF"
    assert not leaks(sql)
    columns, rows = run(world, sql)
    assert columns == list(describe.CHARTED_COLUMNS) and {"51", "52"} <= {r[0] for r in rows}
    receipt = sitting.read_charted("role_reading.kind", grid(columns, rows), year)
    assert receipt["rows"] == len(rows)
    sitting.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff", "zzz": "map_cuff", "1": "not_a_kind"}, DATE)
    assert sitting.data["kinds"]["map_arterial"]["codes"] == ["52"] and sitting.data["kinds"]["map_arterial"]["status"] == "person"
    assert "IN ('52') THEN 'map_arterial'" in sitting.view_sql("role_reading")
    # Another vocabulary's codes are translated in its own view.
    event = sitting.charted_query("role_event.kind", year)
    columns, rows = run(world, event["sql"])
    code = rows[0][0]
    sitting.read_charted("role_event.kind", grid(columns, rows), year)
    sitting.choose_codes("role_event.kind", {code: "induction"}, DATE)
    assert f"IN ('{code}') THEN 'induction'" in sitting.view_sql("role_event")
    sitting.set_settings("training", year)


def test_the_counts_run_and_the_readings_count_sees_the_chosen_codes(sitting, world):
    queries = {q["name"]: q for q in sitting.count_queries(sitting.settings["year"], "8. Run the counts")}
    assert [n for n, q in queries.items() if q["safe"]] == ["coverage_by_year", "repeated_keys"]
    for name in ("coverage_by_year", "repeated_keys"):
        assert "OBS_READING" not in queries[name]["sql"]
    assert "INTO   #cohort" in queries["readings_by_kind"]["sql"]
    for name, query in queries.items():
        assert not leaks(query["sql"])
        columns, rows = run(world, query["sql"])
        assert columns == list(describe.COUNT_COLUMNS[name])
        sitting.read_count(name, grid(columns, rows), DATE)
    kinds = {row[0] for row in sitting.counts["readings_by_kind"]["rows"]}
    assert {"map_arterial", "map_cuff", "other"} <= kinds
    sitting.judge_count("coverage_by_year", "yes", "The years match the theatre system.", DATE)
    assert sitting.counts["coverage_by_year"]["looks_right"] == "yes"


def test_the_folder_records_everything_and_a_new_sitting_restores_and_checks_it(sitting):
    files = sitting.folder_files(keep_dictionary=False, date=DATE)
    names = set(files)
    assert {"settings.json", "journal.json", "confirmations.csv", "README.md", "map/map.json", "map/role_patient.sql",
            "codes/role_reading.kind.json", "counts/judgements.json"} <= names
    assert not any(n.startswith("dictionary/") for n in names)
    queries = sorted(n for n in names if n.startswith("queries/"))
    assert queries[0] == "queries/01-tables-and-columns.sql"
    assert {n.replace("queries/", "results/").replace(".sql", ".tsv") for n in queries} == {n for n in names if n.startswith("results/")}
    journal = json.loads(files["journal.json"])
    assert journal["version"] == "test" and journal["entries"][0]["name"] == "dictionary"
    assert journal["entries"][0]["sha256"] and "description" not in json.dumps(journal["entries"][0]).lower()
    assert all(e.get("database") == "training" for e in journal["entries"][1:] if e["name"].startswith("count-"))
    # The year that the lists and counts used is saved, the judgement says which database it was made on, and the README
    # lists each query run on a training database, to be run again on production.
    assert json.loads(files["settings.json"])["year"] is not None
    assert json.loads(files["counts/judgements.json"])["counts"]["coverage_by_year"]["database"] == "training"
    readme = files["README.md"].decode()
    assert "## Queries to run again on production" in readme and "count-coverage_by_year.sql`" in readme
    rows = list(csv.DictReader(io.StringIO(files["confirmations.csv"].decode())))
    assert [r["attribute"] for r in rows][:2] == ["role_patient.birth_date", "role_patient rows"]
    # Every file names the tool's version and the date, except map.json, whose format is fixed and which gives the
    # date of the proposal, and the dictionary's own files.
    for name, data in files.items():
        text = data.decode()
        if name == "map/map.json":
            assert DATE in json.loads(text)["description"]
        elif name.endswith(".json"):
            assert json.loads(text)["version"] == "test" and json.loads(text)["written"] == DATE
        elif name == "confirmations.csv":
            assert all(r["version"] == "test" for r in rows)
        else:
            assert "Schemalyser test" in text.split("\n", 1)[0] or "Schemalyser test" in text[:400], name
    # Nothing but map.json, which quotes the dictionary as its evidence, holds a description.
    for name, data in files.items():
        if name != "map/map.json":
            assert not leaks(data.decode()), name
    # A new sitting restores the folder, and with the dictionary the check rebuilds the same map.
    again = describe.Describe()
    again.version = "test"
    restored = again.restore(files)
    assert restored["map"] and restored["tables"] and restored["codes"] == 2 and restored["counts"] == 3
    assert again.tally() == sitting.tally() and again.data == sitting.data
    # Without the dictionary the page cannot name the lookup of a vocabulary whose codes nobody has chosen yet, so the
    # vocabularies are compared by their codes.
    chosen = lambda s: [(v["key"], v["chosen"], v["rows"], v["lookup"] if v["chosen"] else None) for v in s.view()["vocabularies"]]  # noqa: E731
    assert chosen(again) == chosen(sitting)
    assert again.check()["rebuilt"] is False
    again.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    checked = again.check()
    assert checked["rebuilt"] and checked["same"], checked["differences"]
    assert [q["name"] for q in checked["queries"]][0] == "tables-and-columns"
    # A folder that is no longer right says where.
    edited = json.loads(files["map/map.json"])
    edited["roles"]["role_patient"]["columns"]["death_date"]["binding"]["column"] = "BIRTH_WEIGHT_G"
    other = describe.Describe()
    other.restore({**files, "map/map.json": json.dumps(edited).encode()})
    other.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes())
    found = other.check()
    assert not found["same"] and any(d.startswith("The date of death in Patients differs") for d in found["differences"])
    # The new result of a query is compared with the earlier one.
    changed = again.compare("tables-and-columns", tables_result({"OBS_READING": 40_000_000}, leave_out=("VISIT_DIAGNOSIS",)))
    assert "The table VISIT_DIAGNOSIS was in the earlier result and is not in the new one." in changed["differences"]
    assert any(d.startswith("OBS_READING held about 25,000,000 rows") for d in changed["differences"])
    held = again.results["count-coverage_by_year"]
    columns, rows = describe.read_grid(held)
    rows[0][1] = str(int(rows[0][1]) * 2 + 20)
    found = again.compare("count-coverage_by_year", grid(columns, rows))["differences"]
    assert found and "anaesthetics of the row" in found[0]


def test_the_dictionary_is_kept_only_when_asked_and_is_restored_from_the_folder(sitting):
    files = sitting.folder_files(keep_dictionary=True, date=DATE)
    assert files["dictionary/invented-dictionary.csv"] == DICTIONARY.read_bytes()
    assert "a person ticked the box" in files["README.md"].decode()
    again = describe.Describe()
    assert again.restore(files)["dictionary"] and again.dictionary.column_count() == 98


def test_a_dictionary_without_known_headings_asks_for_them_and_names_none_of_its_descriptions():
    s = describe.Describe()
    data = b"Thing\tWhat\nPERSON\tThe patient record of the hospital with all details\n"
    with pytest.raises(describe.DescribeError, match="Name the headings yourself") as raised:
        s.load_dictionary(data)
    assert "patient record" not in str(raised.value)
    receipt = s.load_dictionary(b"Thing\tPart\tWhat\nPERSON\tPERSON_KEY\tThe key\n", headings={"table": "Thing", "column": "Part", "description": "What"})
    assert receipt["columns"] == 1
