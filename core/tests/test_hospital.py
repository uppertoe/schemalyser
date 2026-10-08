"""The invented hospital: the database that the front page builds from its published files and runs its own queries on
when the invented dictionary is in use.

It is built here from the same files that the page publishes (fixtures/hospital and the invented catalogue), and is shown
to hold the invented world's own rows, as the tests build that world. The query of step 5, one list of codes, the counts
and a test query are then run on it, two-part scripts as two parts, and each result is read back exactly as a paste,
with the journal recording that it came from the invented hospital.
"""
import json
import sys
from pathlib import Path

import pytest

from schemalyser import browser, describe, hospital

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
DICTIONARY = FIXTURES / "dictionary" / "invented-dictionary.csv"
TABLES = FIXTURES / "dictionary" / "invented-tables.csv"
DATE = "2026-10-07"


def published():
    return hospital.files_from(FIXTURES / "hospital", FIXTURES / "invented-catalogue.csv")


@pytest.fixture(scope="module")
def invented():
    return hospital.InventedHospital(published())


def sitting(invented_dictionary=True):
    s = describe.Describe()
    s.version = "test"
    s.load_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), {}, "invented-dictionary.csv", "invented-tables.csv",
                      "2. Load the data dictionary", invented=invented_dictionary)
    s.propose(date=DATE)
    return s


def test_the_published_files_hold_the_invented_world_s_own_rows(invented):
    sys.path.insert(0, str(FIXTURES))
    import make_hospital
    world = make_hospital.world()
    for name in world.sandbox.tables:
        theirs = world.con.execute(f'SELECT * FROM "{name}" ORDER BY ALL').fetchall()
        ours = invented.sandbox.con.execute(f'SELECT * FROM "{name}" ORDER BY ALL').fetchall()
        assert ours == theirs, name
    # The files are the generator's own output, so that a change to the world shows here until they are written again.
    assert make_hospital.files(world) == {k: v for k, v in published().items() if k != "catalogue.csv"}


def test_step_5_a_list_of_codes_and_the_counts_run_on_it_and_read_back_as_pastes(invented):
    s = sitting()
    s.tables_query("5. Check which tables exist")
    receipt = s.run_invented(invented, "tables-and-columns", "tables")
    assert receipt["tables"] == receipt["asked"] and receipt["absent"] == 0 and s.view()["catalogue"]
    # A list of codes is a two-part script: part 1 fills #cohort, and part 2 reads it.
    found = s.charted_query("role_reading.kind", 2024, "7. Choose the hospital's codes")
    assert len(invented.parts(found["sql"])) == 2
    receipt = s.run_invented(invented, "charted-role_reading-kind", "charted", key="role_reading.kind", year=2024)
    assert receipt["rows"] >= 2 and {"51", "52"} <= {r["code"] for r in s.codes["role_reading.kind"]["rows"]}
    s.choose_codes("role_reading.kind", {"52": "map_arterial", "51": "map_cuff"}, DATE)
    for query in s.count_queries(2024, "8. Run the counts"):
        receipt = s.run_invented(invented, f"count-{query['name']}", "count", name=query["name"])
        assert receipt["rows"] >= 1, query["name"]
    assert {"map_arterial", "map_cuff"} <= {row[0] for row in s.counts["readings_by_kind"]["rows"]}
    journal = json.loads(s.folder_files(date=DATE)["journal.json"])["entries"]
    run = [e for e in journal if e.get("pasted")]
    assert run and all(e["from"] == "invented hospital" for e in run)


def test_a_values_query_and_a_test_query_run_on_it(invented):
    s = sitting()
    found = s.values_query("role_anaesthetic rows", "THEATRE_CASE", "CASE_STATUS_CAT", 2024, "6. Confirm each column")
    assert s.run_invented(invented, found["name"], "values")["values"]
    s.correction_keep({"form": "window", "about": "role_reading.anaesthetic_key", "table": "OBS_SHEET", "column": "VISIT_KEY",
                       "key": "VISIT_KEY", "before": 15, "after": 15}, date=DATE)
    probe = s.probe_query("role_reading.anaesthetic_key", 2024, "6. Confirm each column")
    assert len(invented.parts(probe["sql"])) == 2
    receipt = s.run_invented(invented, probe["name"], "probe", about="role_reading.anaesthetic_key")
    assert receipt["rows"] == 1 and receipt["findings"]


def test_only_the_invented_dictionary_and_an_offered_query_are_answered(invented):
    s = sitting(invented_dictionary=False)
    s.tables_query()
    with pytest.raises(describe.DescribeError, match="only the queries written for the invented dictionary"):
        s.run_invented(invented, "tables-and-columns", "tables")
    with pytest.raises(describe.DescribeError, match="has not written this query yet"):
        sitting().run_invented(invented, "count-coverage_by_year", "count", name="coverage_by_year")


def test_the_worker_builds_it_once_and_answers_through_the_bridge():
    files = published()
    names = sorted(files)
    browser.describe_begin("test")
    assert json.loads(browser.describe_hospital_build(json.dumps(names), *[files[n] for n in names]))["ok"]
    built = browser._hospital
    browser.describe_hospital_build(json.dumps(names), *[files[n] for n in names])
    assert browser._hospital is built
    browser.describe_dictionary(DICTIONARY.read_bytes(), TABLES.read_bytes(), "{}", "invented-dictionary.csv",
                                "invented-tables.csv", "2", True)
    browser.describe_propose(lambda done, total: None)
    browser.describe_tables_query("5")
    reply = json.loads(browser.describe_hospital_run(json.dumps({"query": "tables-and-columns", "read": "tables"})))
    assert reply["ok"] and reply["model"]["catalogue_source"] == "query"
