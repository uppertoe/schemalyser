"""Screen 1, describing the record: the saved hospital schema from the invented dictionary and the invented world.

The sitting is walked through as the page walks it: the dictionary is loaded, the map proposed, the tables and columns
query read, bindings confirmed, the codes chosen from the list of what is charted, and the counts read. The queries
that the screen writes are run on the invented world's shadow in DuckDB, so that they are known to run and to give
what the page expects. The saved schema is then written, read back into a new sitting, and checked. No description from the
dictionary may appear in any query, and the dictionary's own files always go into the saved schema.
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
    # The invented world is small, so a group or a figure under ten is kept here.
    sql = re.sub(r">= 10 THEN", ">= 0 THEN", sql).replace("WHERE  g.readings >= 10", "WHERE  g.readings >= 0")
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
    # The figures count columns only; the table of a part is counted apart.
    tally = sitting.tally()
    assert (tally["confirmed"], tally["corrected"], tally["not_sure"]) == (1, 1, 1)
    assert tally["remaining"] == tally["total"] - 3
    assert tally["tables_remaining"] == tally["tables"] - 1
    assert [q["about"] for q in sitting.questions()] == ["role_reading.value"]
    # A question for the database team states the proposal and asks whether it is right.
    assert sitting.questions()[0]["question"] == (
        "The page proposes OBS_READING.READ_VALUE as the value in Readings charted during an anaesthetic. Is that right, "
        "and if not, which column holds it?")
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


def test_the_saved_schema_records_everything_and_a_new_sitting_restores_and_checks_it(sitting):
    files = sitting.folder_files(date=DATE)
    names = set(files)
    assert {"settings.json", "journal.json", "confirmations.csv", "README.md", "map/map.json", "map/role_patient.sql",
            "codes/role_reading.kind.json", "counts/judgements.json", "dictionary/invented-dictionary.csv"} <= names
    assert "must stay on the hospital's own storage" in files["README.md"].decode()
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
        if name.startswith("dictionary/") and name != "dictionary/dictionary.json":
            continue
        if name == "map/map.json":
            assert DATE in json.loads(text)["description"]
        elif name.endswith(".json"):
            assert json.loads(text)["version"] == "test" and json.loads(text)["written"] == DATE
        elif name == "confirmations.csv":
            assert all(r["version"] == "test" for r in rows)
        else:
            assert "Schemalyser test" in text.split("\n", 1)[0] or "Schemalyser test" in text[:400], name
    # Nothing but map.json, which quotes the dictionary as its evidence, and the dictionary itself holds a description.
    for name, data in files.items():
        if name != "map/map.json" and not name.startswith("dictionary/"):
            assert not leaks(data.decode()), name
    # A saved schema that holds no copy of the dictionary is not restored until a dictionary is loaded, and nothing of
    # it is taken meanwhile.
    files = {k: v for k, v in files.items() if not k.startswith("dictionary/")}
    again = describe.Describe()
    again.version = "test"
    refused = again.restore(files)
    assert refused["needs_dictionary"] and not refused["map"] and again.data is None and again.restored is None
    # Once the dictionary is loaded, a new sitting restores the saved schema, and the check rebuilds the same schema.
    again.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    restored = again.restore(files)
    assert restored["map"] and restored["tables"] and restored["codes"] == 2 and restored["counts"] == 3
    assert again.tally() == sitting.tally() and again.data == sitting.data
    chosen = lambda s: [(v["key"], v["chosen"], v["rows"], v["lookup"] if v["chosen"] else None) for v in s.view()["vocabularies"]]  # noqa: E731
    assert chosen(again) == chosen(sitting)
    checked = again.check()
    assert checked["rebuilt"] and checked["same"], checked["differences"]
    assert [q["name"] for q in checked["queries"]][0] == "tables-and-columns"
    # A saved schema that is no longer right says where.
    edited = json.loads(files["map/map.json"])
    edited["roles"]["role_patient"]["columns"]["death_date"]["binding"]["column"] = "BIRTH_WEIGHT_G"
    other = describe.Describe()
    other.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes())
    other.restore({**files, "map/map.json": json.dumps(edited).encode()})
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


def test_the_dictionary_is_always_in_the_saved_schema_and_is_restored_from_it(sitting):
    files = sitting.folder_files(date=DATE)
    assert files["dictionary/invented-dictionary.csv"] == DICTIONARY.read_bytes()
    assert "This file holds a copy of it in dictionary/" in files["README.md"].decode()
    again = describe.Describe()
    assert again.restore(files)["dictionary"] and again.dictionary.column_count() == 98
    assert again.view()["dictionary"]["source"] == "saved" and not again.invented


def test_a_schema_made_with_the_invented_dictionary_says_so_everywhere_and_keeps_saying_so():
    s = describe.Describe()
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv",
                      invented=True)
    files = s.folder_files(date=DATE)
    first = "This file was made with the invented dictionary, for practice, and describes no hospital."
    assert files["README.md"].decode().split("\n", 1)[0] == first
    settings = json.loads(files["settings.json"])
    assert settings["invented"] and settings["dictionary"]["invented"]
    assert json.loads(files["journal.json"])["entries"][0]["invented"]
    again = describe.Describe()
    again.restore(files)
    assert again.invented and again.folder_files(date=DATE)["README.md"].decode().startswith(first)


def test_a_dictionary_without_known_headings_asks_for_them_and_names_none_of_its_descriptions():
    s = describe.Describe()
    data = b"Thing\tWhat\nPERSON\tThe patient record of the hospital with all details\n"
    with pytest.raises(describe.DescribeError, match="Name the headings yourself") as raised:
        s.load_dictionary(data)
    assert "patient record" not in str(raised.value)
    receipt = s.load_dictionary(b"Thing\tPart\tWhat\nPERSON\tPERSON_KEY\tThe key\n", headings={"table": "Thing", "column": "Part", "description": "What"})
    assert receipt["columns"] == 1


def fresh():
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv")
    s.propose(date=DATE)
    return s


def test_a_yes_on_a_column_that_holds_codes_leaves_it_to_translate_and_the_schema_a_draft():
    s = fresh()
    items = {i["about"]: i for r in s.view()["roles"] for i in r["items"]}
    # A flag bound to text, whose values the proposer guessed, says what it assumes before Yes.
    flag = items["role_patient.is_test"]["coding"]
    assert flag["form"] == "flag" and not flag["translated"] and flag["values"] == ["Y", "Yes", "1"]
    assert flag["assumed"].startswith("The page assumes that the test patient in Patients is 1 where "
                                      "PERSON_MASTER.TEST_PERSON_FLAG holds Y, Yes or 1")
    assert "Confirm or change it" in flag["assumed"]
    # A flag bound to a column of codes, and a kind, are to translate as well.
    assert items["role_stay.unplanned"]["coding"] == {"form": "flag", "translated": False, "assumed": "", "values": []}
    assert items["role_reading.kind"]["coding"]["form"] == "kind" and items["role_reading.kind"]["coding"]["list"]
    assert items["role_patient.birth_date"]["coding"] is None
    s.confirm("role_patient.is_test", "yes", date=DATE)
    s.confirm("role_reading.kind", "yes", date=DATE)
    tally = s.tally()
    assert tally["untranslated"] == 2 and tally["confirmed"] == 0
    assert [u["about"] for u in s.untranslated()] == ["role_patient.is_test", "role_reading.kind"]
    files = s.folder_files(date=DATE)
    settings = json.loads(files["settings.json"])
    assert settings["complete"] is False
    assert settings["draft"].startswith("draft: ") and settings["draft"].endswith(" unanswered and 2 still to translate")
    readme = files["README.md"].decode()
    assert "## This hospital schema is a draft" in readme and "The test patient in Patients (`PERSON_MASTER.TEST_PERSON_FLAG`)" in readme
    # The 1-or-0 form on the proposed column translates it, and step 7's codes translate the kind.
    s.correction_keep({"form": "derived", "about": "role_patient.is_test", "table": "PERSON_MASTER",
                       "column": "TEST_PERSON_FLAG", "derive": {"form": "flag", "values": ["Y"]}}, date=DATE)
    s.choose_codes("role_reading.kind", {"52": "map_arterial"}, DATE)
    tally = s.tally()
    assert tally["untranslated"] == 0 and tally["confirmed"] == 2 and not s.untranslated()


def test_the_form_on_the_proposed_column_is_a_confirmation_and_its_probe_counts_1_and_0():
    s = fresh()
    kept = s.correction_keep({"form": "derived", "about": "role_patient.is_test", "table": "PERSON_MASTER",
                              "column": "TEST_PERSON_FLAG", "derive": {"form": "flag", "values": ["Y"]}}, date=DATE)
    item = s.data["roles"]["role_patient"]["columns"]["is_test"]
    assert kept["probe"] == "flag" and item["confirmation"]["answer"] == "yes"
    assert s.confirmations[-1]["answer"] == "yes" and s.tally()["corrected"] == 0
    # A flag that the form never leaves empty is probed as 1 and 0 alone.
    probe = s.probe_query("role_patient.is_test", 2024)
    assert probe["columns"] == ["ones", "zeros"] and "AS empty" not in probe["sql"] and "is 1 and 0" in probe["sql"]
    s.read_probe("role_patient.is_test", "ones\tzeros\n20\t1200\n")
    assert s.probe_findings("role_patient.is_test") == ["The flag is 1 in about 20 rows and 0 in about 1,200."]
    # The probe reads a small table without a cohort, so no year is recorded with it, nor with the values query.
    assert "year" not in s.journal[probe["name"]]
    values = s.values_query("role_patient.is_test", "PERSON_MASTER", "TEST_PERSON_FLAG", 2024)
    assert not values["script"] and "year" not in s.journal[values["name"]]
    assert "PERSON_MASTER, whose size is not known" in values["sql"] and "small tables" not in values["sql"]
    # A different column through the same form is a correction.
    s.correction_keep({"form": "column", "about": "role_anaesthetic.patient_key", "table": "THEATRE_CASE", "column": "PERSON_KEY"},
                      date=DATE)
    assert s.data["roles"]["role_anaesthetic"]["columns"]["patient_key"]["confirmation"]["answer"] == "no"


def test_map_json_says_how_many_columns_a_person_has_answered_for():
    s = fresh()
    assert "no person has yet answered" in json.loads(s.folder_files(date=DATE)["map/map.json"])["description"]
    s.confirm("role_patient.birth_date", "yes", date=DATE)
    s.confirm("role_patient rows", "yes", date=DATE)
    description = json.loads(s.folder_files(date=DATE)["map/map.json"])["description"]
    assert DATE in description and "no person" not in description
    assert re.search(r"A person has since answered for 2 of its [\d,]+ columns and tables", description)


def test_a_count_leaves_out_a_small_group_and_leaves_empty_a_small_figure_within_one():
    s = fresh()
    queries = {q["name"]: q for q in s.count_queries(2024)}
    coverage = queries["coverage_by_year"]["sql"]
    assert "WHERE  g.anaesthetics >= 10" in coverage
    assert "CASE WHEN g.with_patient >= 10 THEN g.with_patient - g.with_patient % 10 END AS with_patient" in coverage
    assert "is left out, and a figure under ten within it is left empty" in " ".join(coverage.replace("-- ", "").split())
    assert "WHERE  g.readings >= 10" in queries["readings_by_kind"]["sql"]
    # Only the count that reads one year's cohort records the year.
    assert "year" not in s.journal["count-coverage_by_year"] and s.journal["count-readings_by_kind"]["year"] == 2024
    # A figure under ten comes back empty and is not read as a fall.
    s.read_count("coverage_by_year", "start_year\tanaesthetics\twith_patient\twith_birth_date\twith_death_date\ttest_patients\twith_stop\tstop_before_start\n"
                 "2023\t400\t400\t400\t20\t0\t390\t0\n2024\t20\tNULL\t20\tNULL\tNULL\t20\tNULL\n", DATE)
    assert not any("have a patient whom" in f for f in s.findings("coverage_by_year"))


# The data dictionary made from the database: the one query of every table, its result pasted or saved as a file, the
# vendor's descriptions added to it, and step 5 answered by it.

# The tables whose columns carry a description in the database's own records, in the made-up result below. The rest
# have none, as most of a vendor's tables do.
DESCRIBED = {"PERSON_MASTER", "THEATRE_CASE", "ANAES_RECORD", "OBS_READING", "OBS_TYPE_DEF"}


def database_result(delimiter="\t", sizes=None):
    """The data dictionary query's result for the invented world: the catalogue, a size for each table, the key flag and
    the dictionary's description for the tables in DESCRIBED. With "\\t" it is the grid as copied with its headers;
    with "," it is the file that SQL Server Management Studio saves, which does not quote a value that holds a comma."""
    from schemalyser import datadict, first_ask
    dictionary = datadict.load(DICTIONARY, TABLES)
    sizes = sizes or {"OBS_READING": 25_000_000}
    lines = [delimiter.join(first_ask.DATABASE_LAYOUT)]
    for row in csv.DictReader(CATALOGUE.open()):
        table = dictionary.table(row["TABLE_NAME"])
        entry = table.column(row["COLUMN_NAME"]) if table else None
        words = dictionary.description(row["TABLE_NAME"], row["COLUMN_NAME"]) if row["TABLE_NAME"] in DESCRIBED else ""
        cells = [row[k] or "NULL" for k in first_ask.QUERY_ORDER]
        key = "YES" if table is not None and entry is not None and entry.name in table.primary_key() else "NO"
        lines.append(delimiter.join(cells + [str(sizes.get(row["TABLE_NAME"], 1200)), key, words or "NULL"]))
    return "\n".join(lines) + "\n\n(98 rows affected)\n\nCompletion time: 2026-10-08T10:00:00\n"


def test_the_data_dictionary_query_reads_every_table_from_the_database_s_own_records():
    import sqlglot
    from schemalyser import first_ask
    s = describe.Describe()
    s.version = "test"
    sql = s.dictionary_query("2. Load the data dictionary")["sql"]
    assert sql.startswith("-- Written by Schemalyser test on ")
    for part in ("FROM INFORMATION_SCHEMA.COLUMNS AS c", "sys.partitions", "p.index_id IN (0, 1)", "SUM(p.rows)",
                 "INFORMATION_SCHEMA.KEY_COLUMN_USAGE", "INFORMATION_SCHEMA.TABLE_CONSTRAINTS", "'PRIMARY KEY'",
                 "LEFT JOIN sys.extended_properties", "N'MS_Description'", "AS DESCRIPTION", "AS IS_PRIMARY_KEY",
                 "AS TABLE_ROWS", "DATA_TYPE", "IS_NULLABLE", "ORDINAL_POSITION"):
        assert part in sql, part
    # Every table: the query names no table and narrows to none.
    assert "TABLE_NAME IN" not in sql and "WHERE c." not in sql
    assert not any(name in sql for name in ("PERSON_MASTER", "THEATRE_CASE", "OBS_READING"))
    assert "never a row of any table" in " ".join(line[3:] for line in sql.splitlines() if line.startswith("-- "))
    statements = [t for t in sqlglot.parse(sql, read="tsql") if t is not None]
    assert len(statements) == 1 and statements[0].key == "select"
    assert sql == s.dictionary_query()["sql"] and first_ask.database_query() in sql


@pytest.mark.parametrize("delimiter", ["\t", ","])
def test_the_result_pasted_or_saved_makes_the_dictionary_and_answers_step_5(delimiter):
    s = describe.Describe()
    s.version = "test"
    text = database_result(delimiter)
    receipt = s.load_from_database(("﻿" + text).encode("utf-8"), "result.csv", "2. Load the data dictionary")
    assert receipt["source"] == "database" and receipt["columns"] == 98 and receipt["tables"] == 25
    described = sum(1 for row in csv.DictReader(CATALOGUE.open()) if row["TABLE_NAME"] in DESCRIBED)
    assert receipt["described"] == described and 0 < described < 98
    assert receipt["sized"] == 25 and receipt["keyed"] >= 20
    # A description that holds a comma, which the saved file does not quote, is read whole.
    assert s.dictionary.description("PERSON_MASTER", "PERSON_KEY") == \
        "The unique ID of the patient record for this row. Other tables use this column to link to PERSON_MASTER."
    assert s.dictionary.description("VISIT_DIAGNOSIS", "VISIT_KEY") == ""
    assert s.dictionary.table("PERSON_MASTER").primary_key() == ("PERSON_KEY",)
    # The same result is the result of the tables and columns query, so step 5 is answered.
    model = s.view()
    assert model["catalogue"] and model["catalogue_source"] == "database"
    s.propose(date=DATE)
    items = {i["about"]: i for r in s.view()["roles"] for i in r["items"]}
    assert items["role_reading.value"]["presence"]["state"] == "large"
    assert all(i["presence"]["state"] != "missing" for i in items.values() if i["presence"])
    # The pasted grid and the saved file give the same dictionary.
    other = describe.Describe()
    other.load_from_database(database_result("\t" if delimiter == "," else ","))
    assert other.dictionary_files["data"] == s.dictionary_files["data"]


def test_a_result_without_its_headers_says_how_to_include_them():
    s = describe.Describe()
    body = database_result().split("\n", 1)[1]
    with pytest.raises(describe.DescribeError, match="Include column headers"):
        s.load_from_database(body.encode())
    with pytest.raises(describe.DescribeError, match="data dictionary query"):
        s.load_from_database(b"")


def test_the_vendor_s_descriptions_are_added_by_name_without_regard_to_case_and_saved_with_the_schema():
    s = describe.Describe()
    s.version = "test"
    s.load_from_database(database_result().encode(), step="2. Load the data dictionary")
    before = s.dictionary_receipt()["described"]
    # The vendor's export spells the names in lower case, and describes a table that the database does not hold.
    vendor = DICTIONARY.read_text().split("\n")
    vendor = "\n".join([vendor[0]] + [line.lower() if i % 2 else line for i, line in enumerate(vendor[1:], 1)]
                       + ["NO_SUCH_TABLE,NO_SUCH_COLUMN,VARCHAR,NO,A table that this database does not hold."])
    receipt = s.add_descriptions(vendor.encode(), None, {}, "vendor.csv")
    assert receipt["described"] == 98 and receipt["vendor"] == {"file": "vendor.csv", "matched": 98, "gained": 98 - before}
    assert s.dictionary.table("NO_SUCH_TABLE") is None and receipt["tables"] == 25
    assert s.dictionary.description("VISIT_DIAGNOSIS", "VISIT_KEY")
    assert s.view()["catalogue_source"] == "database"
    s.propose(date=DATE)
    files = s.folder_files(DATE)
    assert {"dictionary/data-dictionary.csv", "dictionary/vendor.csv", "queries/01-data-dictionary.sql"} <= set(files)
    info = json.loads(files["dictionary/dictionary.json"])
    assert info["source"] == "database" and info["vendor"] == "vendor.csv"
    entry = json.loads(files["journal.json"])["entries"][0]
    assert entry["vendor_file"] == "vendor.csv" and entry["vendor_gained"] == 98 - before
    # A new sitting opens the saved schema with nothing else, and step 5 is answered again.
    again = describe.Describe()
    found = again.restore(files)
    assert found["dictionary"] and found["map"] and not found["needs_dictionary"]
    restored = again.dictionary_receipt()
    assert restored["saved"] and restored["source"] == "database" and restored["described"] == 98
    assert restored["vendor"]["gained"] == 98 - before
    assert again.view()["catalogue_source"] == "database" and again.sizes["OBS_READING"] == 25_000_000
    # The check against the database compares a new result of the query with the one that made the dictionary.
    check = {q["name"]: q for q in again.check()["queries"]}
    assert check["data-dictionary"]["rows"]
    fewer = "\n".join(line for line in database_result().split("\n") if "\tVISIT_DIAGNOSIS\t" not in line)
    assert again.compare("data-dictionary", fewer)["differences"] == \
        ["The table VISIT_DIAGNOSIS was in the earlier result and is not in the new one."]


def test_a_vendor_file_alone_is_the_dictionary_and_leaves_step_5_to_its_query():
    s = describe.Describe()
    with pytest.raises(describe.DescribeError, match="made from the database"):
        s.add_descriptions(DICTIONARY.read_bytes())
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes())
    assert s.view()["catalogue_source"] is None and not s.view()["catalogue"]
    with pytest.raises(describe.DescribeError, match="made from the database"):
        s.add_descriptions(DICTIONARY.read_bytes())
    s.read_tables(tables_result())
    assert s.view()["catalogue_source"] == "query"
