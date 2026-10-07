"""The role model, the dictionary loader, the proposer and the confirmations.

The loader must read a dictionary under the headings that an export plausibly uses, keep only plain names, and never
let a description out except through the draft map. The proposer, given the invented world's dictionary, must find the
bindings that the hand-written invented map makes for the three views of the neonatal audit, and its draft must pass
the map checker. Once a person has confirmed the local codes, the draft must give the neonatal audit the same answer as
the hand-written map on the invented world's shadow. The test against the vendor's public specification lives here
too, but its expectations name the vendor's columns, so they are kept in the private folder and the test is skipped
where that folder is absent.
"""
import io
import json
import re
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from schemalyser import datadict, propose, rolemap
from schemalyser.catalogue import Catalogue

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
DICTIONARY = FIXTURES / "dictionary" / "invented-dictionary.csv"
TABLES = FIXTURES / "dictionary" / "invented-tables.csv"
EXPORT = FIXTURES / "dictionary" / "invented-dictionary-export.tsv"
CATALOGUE_FILE = FIXTURES / "invented-catalogue.csv"
CATALOGUE = Catalogue.from_csv(CATALOGUE_FILE.read_text())
MAP = FIXTURES / "map"
ROLES = rolemap.MODEL / "roles.md"
EPIC = ROOT / "reference" / "epic-ehi"
EPIC_EXPECTED = ROOT / "reference" / "stress-test" / "epic-public" / "proposer-expected.json"
DATE = "2026-10-07"


def _descriptions(*paths):
    """Every description in the invented dictionary files, for checking that none escapes."""
    found = []
    for path in paths:
        dictionary = datadict.load(path, TABLES if path == DICTIONARY else None)
        for table in dictionary.tables():
            found.append(dictionary.description(table.name))
            found += [dictionary.description(table.name, c.name) for c in table.columns.values()]
    return [d for d in found if d]


def _leaks(text, descriptions):
    """The descriptions of which any run of six words appears in text."""
    plain = " ".join(text.split())
    leaked = []
    for description in descriptions:
        words = description.split()
        if any(" ".join(words[i:i + 6]) in plain for i in range(max(len(words) - 5, 1))):
            leaked.append(description)
    return leaked


@pytest.fixture(scope="module")
def drafted(tmp_path_factory):
    out = tmp_path_factory.mktemp("draft") / "map"
    dictionary = datadict.load(DICTIONARY, TABLES)
    proposal, found, missing = propose.propose_map(dictionary, CATALOGUE, out, date=DATE, world="the invented world")
    return {"proposal": proposal, "map": found, "missing": missing, "folder": out}


# The role model.

def test_the_role_model_keeps_the_audit_views_and_describes_every_further_view_in_roles_md():
    contract = rolemap.contract()
    assert rolemap.views() == {
        "role_patient": ["patient_key", "birth_date", "death_date", "is_test"],
        "role_anaesthetic": ["anaesthetic_key", "patient_key", "start_time", "stop_time"],
        "role_reading": ["anaesthetic_key", "kind", "reading_time", "value", "accepted"]}
    further = set(rolemap.all_views()) - set(rolemap.views())
    assert {"role_stay", "role_anaesthetic_detail", "role_operation", "role_unit_stay", "role_patient_detail",
            "role_event", "role_drug", "role_device"} == further
    text = ROLES.read_text()
    names = {view["name"]: view for view in contract["views"]}
    for view in contract["views"]:
        assert f"### {view['name']}:" in text, view["name"]
        section = text.split(f"### {view['name']}:")[1].split("\n### ")[0]
        for column in view["columns"]:
            assert column["type"] in contract["types"], (view["name"], column["name"])
            assert f"| {column['name']} | {column['type']} | {'yes' if column['needed_by'] else 'no'} |" in section, \
                (view["name"], column["name"])
            if column["type"] == "kind" and view["name"] != "role_reading":
                assert column["vocabulary"] in contract["vocabularies"]
        for link in view["links"]:
            other, key = link["to"].split(".")
            assert key in [c["name"] for c in names[other]["columns"]] and link["column"] in [c["name"] for c in view["columns"]]
        assert set(view["key"]) <= {c["name"] for c in view["columns"]}
    for name, kinds in contract["vocabularies"].items():
        assert f"| {name} | {', '.join(k['kind'] for k in kinds)} |" in text
    # The roles of the outcomes and covariates name only views and columns that exist.
    for item in contract["outcomes"] + contract["covariates"]:
        for source in item["from"]:
            view, _, column = source.partition(".")
            assert view in names and (not column or column in [c["name"] for c in names[view]["columns"]]), source
    # The public model names no table of any hospital: none of the invented catalogue's names appears in it.
    model = json.dumps(contract) + text
    assert not [t.name for t in CATALOGUE.tables() if re.search(rf"\b{t.name}\b", model)]


def test_a_map_may_supply_further_views_and_an_audit_may_read_them(drafted):
    found = drafted["map"]
    assert set(rolemap.views()) <= set(found["views"]) <= set(rolemap.all_views())
    audit = "SELECT a.anaesthetic_key, s.admit_time FROM role_anaesthetic a JOIN role_anaesthetic_detail d ON " \
            "d.anaesthetic_key = a.anaesthetic_key JOIN role_stay s ON s.stay_key = d.stay_key"
    compiled = rolemap.compile_query(audit, found)
    assert "role_stay AS (" in compiled and "role_anaesthetic_detail AS (" in compiled and "role_drug AS (" not in compiled
    with pytest.raises(rolemap.MapError, match="does not supply"):
        rolemap.compile_query(audit, rolemap.read_map(MAP, CATALOGUE_FILE.read_text()))


# The loader.

def test_the_loader_reads_the_invented_dictionary_with_its_tables_and_keys():
    dictionary = datadict.load(DICTIONARY, TABLES)
    assert repr(dictionary) == "Dictionary(25 tables, 98 columns)"
    assert dictionary.table("obs_reading").primary_key() == ("SHEET_KEY", "SEQ")
    assert dictionary.table("ANAES_RECORD").primary_key() == ("ANAES_KEY",)
    assert dictionary.description("PERSON_MASTER", "BIRTH_TS") == "The date and time on which the patient was born."
    assert dictionary.description("ANAES_RECORD").startswith("This table stores one record for each anesthesia episode")
    assert dictionary.table("OBS_READING").column("READ_TS").data_type == "DATETIME"
    # Without the tables' file, the keys come from the key flags of the columns.
    alone = datadict.load(DICTIONARY)
    assert alone.table("OBS_READING").primary_key() == ("SHEET_KEY", "SEQ") and alone.description("ANAES_RECORD") == ""
    # Nothing that represents an entry shows its description.
    entry = dictionary.table("PERSON_MASTER").column("BIRTH_TS")
    assert "born" not in repr(entry) and "born" not in repr(dictionary.table("PERSON_MASTER"))


def test_the_loader_accepts_an_export_s_own_headings_and_leaves_out_names_that_are_not_plain():
    dictionary = datadict.load(EXPORT)
    assert sorted(t.name for t in dictionary.tables()) == ["ANAES_RECORD", "PERSON_MASTER"]
    assert sorted(dictionary.table("PERSON_MASTER").columns) == ["BIRTH_TS", "PERSON_KEY", "TEST_PERSON_FLAG"]
    assert dictionary.skipped == 1
    assert dictionary.table("ANAES_RECORD").primary_key() == ("ANAES_KEY",)
    assert dictionary.description("PERSON_MASTER").startswith("The PERSON_MASTER table contains one record")
    assert dictionary.description("ANAES_RECORD", "ANAES_STOP_TS").endswith("for this record, written over two lines.")
    # A caller may name headings that no export would use.
    text = EXPORT.read_text().replace("Table Name\tField\tType\tPK\tDefinition", "Tbl\tCol\tKind of data\tIdentifies\tWords")
    with pytest.raises(datadict.DictionaryError, match="--heading table=HEADING"):
        datadict.load(text)
    own = datadict.load(text, headings={"table": "Tbl", "column": "Col", "description": "Words", "data_type": "Kind of data",
                                        "key": "Identifies"})
    assert own.description("PERSON_MASTER", "BIRTH_TS") == dictionary.description("PERSON_MASTER", "BIRTH_TS")
    assert own.table("PERSON_MASTER").primary_key() == ("PERSON_KEY",)
    with pytest.raises(datadict.DictionaryError, match="no heading Nowhere"):
        datadict.load(text, headings={"table": "Nowhere"})


def test_the_loader_refuses_a_dictionary_beyond_its_limits_and_its_messages_hold_no_description(monkeypatch):
    descriptions = _descriptions(DICTIONARY)
    messages = []
    monkeypatch.setattr(datadict, "MAX_BYTES", 1000)
    with pytest.raises(datadict.DictionaryError, match="Schemalyser reads at most 1000") as error:
        datadict.load(DICTIONARY)
    messages.append(str(error.value))
    monkeypatch.setattr(datadict, "MAX_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(datadict, "MAX_ROWS", 10)
    with pytest.raises(datadict.DictionaryError, match="more than 10 rows") as error:
        datadict.load(DICTIONARY)
    messages.append(str(error.value))
    monkeypatch.setattr(datadict, "MAX_ROWS", 500_000)
    monkeypatch.setattr(datadict, "MAX_DESCRIPTION", 20)
    cut = datadict.load(DICTIONARY)
    assert cut.cut > 0 and max(len(cut.description(t.name, c)) for t in cut.tables() for c in t.columns) == 20
    with pytest.raises(datadict.DictionaryError) as error:
        datadict.load("Words,More\nThe unique ID of the patient record for this row,x\n")
    messages.append(str(error.value))
    assert not _leaks("\n".join(messages), descriptions)


# The proposer on the invented world.

def test_the_proposer_finds_the_invented_map_s_bindings_for_the_audit_views(drafted):
    proposal = drafted["proposal"]
    hand = rolemap.read_map_json(MAP)
    for view in rolemap.views():
        assert proposal[view]["rows"]["table"] == hand["roles"][view]["rows"]["from"].split(",")[0].split()[0], view
        for column, evidence in hand["roles"][view]["columns"].items():
            best = proposal[view]["columns"][column]["best"]
            assert best is not None, (view, column)
            assert f"{best['table']}.{best['column']}" == evidence["from"].split(",")[0], (view, column)
    # The proposer reaches the anaesthetic's patient through its hospital visit, and a reading's anaesthetic through
    # its sheet, each by a key that is the whole primary key of the table it joins.
    assert proposal["role_anaesthetic"]["columns"]["patient_key"]["best"]["path"] == [["ANAES_RECORD", "VISIT_KEY", "VISIT", "VISIT_KEY"]]
    assert proposal["role_reading"]["columns"]["anaesthetic_key"]["best"]["path"] == [["OBS_READING", "SHEET_KEY", "OBS_SHEET", "SHEET_KEY"]]
    # Of the further views, the invented world has no movements between units, and says nothing of planned intensive
    # care, a return to theatre, a cardiac operation or the weight at the anaesthetic.
    assert proposal["role_unit_stay"] is None
    detail = proposal["role_anaesthetic_detail"]["columns"]
    assert [c for c, p in detail.items() if p["best"] is None] == ["weight_kg", "planned_icu", "unplanned_return"]
    assert detail["asa_grade"]["best"]["column"] == "RISK_GRADE_CAT" and detail["stay_key"]["best"]["column"] == "VISIT_KEY"
    assert proposal["role_operation"]["columns"]["is_cardiac"]["best"] is None
    # A procedure reaches its anaesthetic only from the anaesthetic's side, so the proposal says so with low confidence.
    link = proposal["role_operation"]["columns"]["anaesthetic_key"]
    assert link["confidence"] == "low" and link["best"]["path"][-1] == ["THEATRE_CASE", "CASE_KEY", "ANAES_RECORD", "CASE_KEY"]


def test_the_draft_map_marks_every_binding_as_proposed_and_quotes_the_dictionary(drafted):
    data = drafted["map"]["data"]
    folder = drafted["folder"]
    dictionary = datadict.load(DICTIONARY, TABLES)
    for view, role in data["roles"].items():
        for about, item in [("rows", role["rows"])] + list(role["columns"].items()):
            assert item["status"] == "proposed" and item["question"].startswith("Please "), (view, about)
            assert item["confidence"] in ("high", "medium", "low", "none")
            binding = item["binding"]
            if binding and "column" in binding and not binding["path"]:
                quoted = dictionary.description(binding["table"], binding["column"]).rstrip(".")
                assert quoted[:40] in item["says"], (view, about)
    says = data["roles"]["role_patient"]["columns"]["birth_date"]["says"]
    assert says == ("The dictionary describes PERSON_MASTER.BIRTH_TS as \"The date and time on which the patient was born\", "
                    "and the words 'date', 'patient' and 'born' match the role.")
    assert data["roles"]["role_patient"]["rows"]["says"].startswith("The dictionary describes the table PERSON_MASTER as")
    assert data["kinds"]["map_cuff"]["codes"] == [] and data["kinds"]["map_cuff"]["from"] == "OBS_READING.OBS_TYPE_KEY"
    assert "on 2026-10-07" in data["description"]
    # Every binding is open, and the SQL files hold no description, which only map.json quotes.
    assert len(rolemap.open_items(drafted["map"])) == sum(1 + len(r["columns"]) for r in data["roles"].values()) + 2
    sql = "\n".join(path.read_text() for path in folder.glob("*.sql"))
    assert not _leaks(sql, _descriptions(DICTIONARY))
    reading = (folder / "role_reading.sql").read_text()
    assert "LEFT JOIN OBS_SHEET t1 ON t1.SHEET_KEY = t0.SHEET_KEY" in reading and "WHERE  t1.ANAES_KEY IS NOT NULL" in reading
    assert "CASE WHEN t0.ACCEPTED_FLAG IN ('N', 'No', '0') THEN 0 ELSE 1 END AS accepted" in reading
    assert "The local codes of the mean pressures are not yet known" in reading


def test_the_proposer_refuses_to_write_a_draft_from_a_real_dictionary_into_a_published_folder(tmp_path):
    dictionary = datadict.load(DICTIONARY, TABLES)
    out = FIXTURES / "map-draft-that-must-not-exist"
    with pytest.raises(propose.ProposeError, match="only to a private folder"):
        propose.propose_map(dictionary, CATALOGUE, out, date=DATE)
    assert not out.exists()


def test_the_command_line_proposes_and_prints_names_and_counts_but_no_description(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        rolemap.main(["propose", str(DICTIONARY), "--tables", str(TABLES), "--catalogue", str(CATALOGUE_FILE),
                      "--out", str(tmp_path / "draft"), "--world", "the invented world"])
    text = out.getvalue()
    assert "role_patient: PERSON_MASTER, with 4 of 4 columns proposed (4 high)." in text
    assert "role_unit_stay: the dictionary holds no table that fits." in text
    assert "every binding awaits a person's confirmation" in text
    assert not _leaks(text, _descriptions(DICTIONARY))
    with pytest.raises(SystemExit, match="--heading is written as NAME=VALUE"):
        rolemap.main(["propose", str(DICTIONARY), "--catalogue", str(CATALOGUE_FILE), "--out", str(tmp_path / "other"),
                      "--heading", "table"])


# Confirmations.

def test_confirmations_are_recorded_with_their_date_and_rewrite_the_views(drafted, tmp_path):
    folder = tmp_path / "map"
    shutil.copytree(drafted["folder"], folder)
    (tmp_path / "answers.csv").write_text(
        "attribute,answer,replacement,by,note\n"
        "role_patient.birth_date,yes,,the data engineer,\n"
        "role_patient rows,yes,,,\n"
        "role_anaesthetic.patient_key,no,THEATRE_CASE.PERSON_KEY via ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY,,\n"
        "role_reading.value,not sure,,,The engineer will look at a sample.\n"
        "kind map_arterial,no,52,,\n"
        "kind map_cuff,no,51,,\n"
        "role_stay.unplanned,no,,,\n")
    out = io.StringIO()
    with redirect_stdout(out):
        rolemap.main(["confirm", str(folder), str(tmp_path / "answers.csv"), "--catalogue", str(CATALOGUE_FILE)])
    assert "Schemalyser recorded 7 answers (2 yes, 4 no, 1 not sure)." in out.getvalue()
    data = json.loads((folder / "map.json").read_text())
    birth = data["roles"]["role_patient"]["columns"]["birth_date"]
    assert birth["status"] == "person" and "question" not in birth
    assert birth["confirmation"] == {"answer": "yes", "date": rolemap_today(), "by": "the data engineer"}
    patient = data["roles"]["role_anaesthetic"]["columns"]["patient_key"]
    assert patient["status"] == "person" and patient["from"] == "THEATRE_CASE.PERSON_KEY, by ANAES_RECORD.CASE_KEY = THEATRE_CASE.CASE_KEY"
    value = data["roles"]["role_reading"]["columns"]["value"]
    assert value["status"] == "proposed" and value["confirmation"]["answer"] == "not sure" and "question" in value
    assert data["kinds"]["map_arterial"]["codes"] == ["52"] and data["kinds"]["map_arterial"]["status"] == "person"
    unplanned = data["roles"]["role_stay"]["columns"]["unplanned"]
    assert unplanned["binding"] is None and unplanned["status"] == "proposed"
    anaesthetic = (folder / "role_anaesthetic.sql").read_text()
    assert "LEFT JOIN THEATRE_CASE t1 ON t1.CASE_KEY = t0.CASE_KEY" in anaesthetic and "t1.PERSON_KEY AS patient_key" in anaesthetic
    reading = (folder / "role_reading.sql").read_text()
    assert "WHEN CAST(t0.OBS_TYPE_KEY AS varchar(254)) IN ('52') THEN 'map_arterial'" in reading
    assert "NULL AS unplanned" in (folder / "role_stay.sql").read_text()
    # A replacement that the view cannot reach needs its link, and an answer must be one of the three.
    (tmp_path / "bad.csv").write_text("attribute,answer,replacement\nrole_patient.death_date,no,DRUG_DEF.DRUG_LABEL\n")
    with pytest.raises(propose.ProposeError, match="via TABLE.COLUMN = TABLE.COLUMN"):
        propose.confirm(folder, tmp_path / "bad.csv", CATALOGUE)
    (tmp_path / "bad.csv").write_text("attribute,answer\nrole_patient.death_date,perhaps\n")
    with pytest.raises(propose.ProposeError, match="yes, no or not sure"):
        propose.confirm(folder, tmp_path / "bad.csv", CATALOGUE)
    # With the dictionary, the proposer finds the link to a replacement on its own.
    (tmp_path / "found.csv").write_text("attribute,answer,replacement,date\nrole_drug.drug,no,DRUG_GIVEN.DRUG_KEY,2026-10-08\n")
    propose.confirm(folder, tmp_path / "found.csv", CATALOGUE, datadict.load(DICTIONARY, TABLES))
    drug = json.loads((folder / "map.json").read_text())["roles"]["role_drug"]["columns"]["drug"]
    assert drug["says"] == ("A person replaced the proposal with DRUG_GIVEN.DRUG_KEY on 2026-10-08, which the dictionary "
                            "describes as \"The unique ID of the medication given\".")


def rolemap_today():
    import datetime
    return datetime.date.today().isoformat()


def test_once_a_person_confirms_the_codes_and_the_patient_s_route_the_draft_gives_the_hand_written_map_s_answer(drafted, tmp_path):
    sys.path.insert(0, str(FIXTURES))
    import make_checks
    hand = rolemap.hospital_run(make_checks.WORLD, FIXTURES / "conversion", rolemap.read_map(MAP, CATALOGUE), rows=500)
    con, dates = hand["conversion"].con, hand["conversion"].sandbox.date_columns
    folder = tmp_path / "map"
    shutil.copytree(drafted["folder"], folder)
    (tmp_path / "codes.csv").write_text("attribute,answer,replacement\nkind map_arterial,no,52\nkind map_cuff,no,51\n")
    propose.confirm(folder, tmp_path / "codes.csv", CATALOGUE)
    codes_only = rolemap.result(rolemap.duckdb_runner(con, dates, rolemap.read_map(folder, CATALOGUE)))["rows"]
    # The proposer reaches the patient by the anaesthetic record's own visit, where the hand-written map follows the
    # conversion through the theatre case. The planted cases make the two routes differ, so the answer differs until a
    # person settles the route.
    assert codes_only != hand["result"]["rows"]
    (tmp_path / "route.csv").write_text(
        "attribute,answer,replacement\nrole_anaesthetic.patient_key,no,VISIT.PERSON_KEY via ANAES_RECORD.CASE_KEY = "
        "THEATRE_CASE.CASE_KEY then THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY\n")
    propose.confirm(folder, tmp_path / "route.csv", CATALOGUE)
    settled = rolemap.result(rolemap.duckdb_runner(con, dates, rolemap.read_map(folder, CATALOGUE)))["rows"]
    assert settled == hand["result"]["rows"]


# The vendor's public specification. Its expectations name the vendor's columns, so they are private.

def test_the_proposer_agrees_with_the_earlier_map_of_the_public_specification(tmp_path):
    if not (EPIC_EXPECTED.exists() and (EPIC / "ehi-catalogue.csv").exists()):
        pytest.skip("the public specification and its expectations are kept in the private folder, which is absent here")
    from schemalyser.extract import decode
    expected = json.loads(EPIC_EXPECTED.read_text())
    dictionary = datadict.load(EPIC / "ehi-catalogue.csv", EPIC / "ehi-tables.csv")
    catalogue = Catalogue.from_csv(decode((EPIC / "ehi-catalogue.csv").read_bytes()))
    proposal, found, _ = propose.propose_map(dictionary, catalogue, tmp_path / "draft", date=DATE)
    got = {}
    for view, item in proposal.items():
        if item is None:
            continue
        got[f"{view} rows"] = item["rows"]["table"]
        for column, p in item["columns"].items():
            got[f"{view}.{column}"] = f"{p['best']['table']}.{p['best']['column']}" if p["best"] else None
    for about, wanted in expected["agree"].items():
        assert got.get(about) == wanted, about
    # Each known disagreement is held as it stands, so that a change in the proposer shows here.
    for about, item in expected["differ"].items():
        assert got.get(about) == item["proposed"], about
