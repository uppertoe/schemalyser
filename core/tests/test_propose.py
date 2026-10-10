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

from schemalyser import datadict, propose, rolemap, roleshadow
from schemalyser.rolemap import __main__ as rolemap_main
from schemalyser import compiler
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
        "role_reading": ["anaesthetic_key", "kind", "reading_time", "value", "accepted", "reading_key", "value_text"]}
    further = set(rolemap.all_views()) - set(rolemap.views())
    assert {"role_stay", "role_anaesthetic_detail", "role_operation", "role_transfer", "role_patient_detail",
            "role_event", "role_drug", "role_technique", "role_device", "role_staff", "role_fluid", "role_lab",
            "role_diagnosis", "role_note", "role_finding"} == further
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
            if column["type"] == "local_key":
                assert column["mapping"] in rolemap.mapping_views()
        for link in view["links"]:
            other, key = link["to"].split(".")
            assert key in [c["name"] for c in names[other]["columns"]] and link["column"] in [c["name"] for c in view["columns"]]
        assert set(view["key"]) <= {c["name"] for c in view["columns"]}
    for name, kinds in contract["vocabularies"].items():
        assert f"| {name} | {', '.join(k['kind'] for k in kinds)} |" in text
    # Every kind of reading has its unit in both, and none is lost.
    for kind in contract["kinds"]:
        assert f"| {kind['kind']} | {kind['unit'] or 'none'} | {kind['meaning']}" in text, kind["kind"]
    # Each mapping view has its section, with the same four columns, and every column that names it does so.
    mappings = rolemap.mapping_views()
    assert set(mappings) == {"map_drug_concept", "map_procedure_concept", "map_diagnosis_concept", "map_lab_concept", "map_unit_concept"}
    for name, mapping in mappings.items():
        assert [c["name"] for c in mapping["columns"]] == ["local_key", "concept_id", "status", "provenance"]
        section = text.split(f"### {name}:")[1].split("\n### ")[0]
        for column in mapping["columns"]:
            assert f"| {column['name']} | {column['type']} | no |" in section, (name, column["name"])
        for used in mapping["used_by"]:
            view, _, column = used.partition(".")
            assert next(c for c in names[view]["columns"] if c["name"] == column)["mapping"] == name
    # The source kinds are listed in their own table, and every part that records events carries the column.
    source = text.split("## The kinds of source record")[1].split("\n## ")[0]
    for kind in contract["vocabularies"]["source_kind"]:
        assert f"| {kind['kind']} | {kind['meaning']} |" in source
    assert set(rolemap.event_parts()) == {"role_transfer", "role_event", "role_drug", "role_technique", "role_fluid",
                                          "role_device", "role_lab", "role_diagnosis"}
    assert "source_kind" not in [c["name"] for c in names["role_reading"]["columns"]] and "version 2" in source
    # Each capability has its row, which names every column, kind and mapping view it requires, and each requirement
    # names what exists.
    catalogue = text.split("## The capability catalogue")[1].split("\n## ")[0]
    columns = {(v["name"], c["name"]) for v in contract["views"] for c in v["columns"]}
    for capability in contract["capabilities"]:
        row = next(line for line in catalogue.splitlines() if line.startswith(f"| {capability['name']} |"))
        assert f"| {capability['version']} | {capability['output_class']} |" in row
        wanted = capability["requires"]
        for item in wanted["columns"] + wanted["kinds"] + wanted["mapping_views"]:
            assert f"`{item}`" in row, (capability["name"], item)
        for item in wanted["columns"]:
            assert tuple(item.split(".")) in columns, item
        assert set(wanted["parts"]) <= set(names) | set(mappings)
        assert capability["output_class"] in {k["kind"] for k in contract["output_classes"]}
    transfusion = rolemap.capabilities()["transfusion"]
    assert "role_anaesthetic_detail.weight_kg" in transfusion["requires"]["columns"]
    assert "`role_anaesthetic_detail.weight_kg`" in next(line for line in catalogue.splitlines() if line.startswith("| transfusion |"))
    # The public model names no table of any hospital: none of the invented catalogue's names appears in it.
    model = json.dumps(contract) + text
    assert not [t.name for t in CATALOGUE.tables() if re.search(rf"\b{t.name}\b", model)]


def test_a_map_may_supply_further_views_and_an_audit_may_read_them(drafted):
    found = drafted["map"]
    # A read map gives every mapping view as well, from its translations, which the draft does not yet hold.
    assert set(rolemap.views()) <= set(found["views"]) <= set(rolemap.public_views())
    assert set(rolemap.mapping_views()) <= set(found["views"])
    audit = "SELECT a.anaesthetic_key, s.admit_time FROM role_anaesthetic a JOIN role_anaesthetic_detail d ON " \
            "d.anaesthetic_key = a.anaesthetic_key JOIN role_stay s ON s.stay_key = d.stay_key"
    compiled = compiler.compile_query(audit, found)
    assert "role_stay AS (" in compiled and "role_anaesthetic_detail AS (" in compiled and "role_drug AS (" not in compiled
    hand = rolemap.read_map(MAP, CATALOGUE_FILE.read_text())
    with pytest.raises(rolemap.MapError, match="does not supply"):
        compiler.compile_query(audit, hand)
    # The invented map supplies the staff, so an audit of whether a consultant anaesthetist was present compiles with it,
    # and it supplies no fluids, so an audit of transfusion is refused.
    consultant = "SELECT a.anaesthetic_key, MAX(CASE WHEN s.role = 'anaesthetist' AND s.grade = 'consultant' THEN 1 ELSE 0 END) " \
                 "AS consultant_present FROM role_anaesthetic a LEFT JOIN role_staff s ON s.anaesthetic_key = a.anaesthetic_key " \
                 "GROUP BY a.anaesthetic_key"
    assert "role_staff AS (" in compiler.compile_query(consultant, hand)
    with pytest.raises(rolemap.MapError, match="role_fluid, which the map does not supply"):
        compiler.compile_query("SELECT f.anaesthetic_key FROM role_fluid f WHERE f.kind = 'red_cells'", hand)


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
            if best.get("derive"):
                # A key made from several columns names each of them, as the hand-written map does.
                named = " and ".join(f"{best['table']}.{c}" for c in [*best["derive"]["with"], best["column"]])
                assert named == evidence["from"], (view, column)
                continue
            assert f"{best['table']}.{best['column']}" == evidence["from"].split(",")[0], (view, column)
    # The proposer reaches the anaesthetic's patient through its hospital visit, and a reading's anaesthetic through
    # its sheet, each by a key that is the whole primary key of the table it joins.
    assert proposal["role_anaesthetic"]["columns"]["patient_key"]["best"]["path"] == [["ANAES_RECORD", "VISIT_KEY", "VISIT", "VISIT_KEY"]]
    assert proposal["role_reading"]["columns"]["anaesthetic_key"]["best"]["path"] == [["OBS_READING", "SHEET_KEY", "OBS_SHEET", "SHEET_KEY"]]
    # Of the further views, the invented world has no movements between units, no fluids, no laboratory results and no
    # notes, and it says nothing of planned intensive care, a return to theatre, a cardiac operation or the weight and
    # height at the anaesthetic.
    assert all(proposal[view] is None for view in ("role_transfer", "role_fluid", "role_lab", "role_note", "role_finding"))
    detail = proposal["role_anaesthetic_detail"]["columns"]
    assert [c for c, p in detail.items() if p["best"] is None] == ["weight_kg", "planned_icu", "unplanned_return", "height_cm"]
    assert detail["location"]["best"]["column"] == "ROOM_KEY"
    # The staff and the diagnoses are found where the hand-written map finds them, and a column that the invented
    # world does not record, such as a grade or the time of a diagnosis, is given empty rather than guessed.
    for view in ("role_staff", "role_diagnosis"):
        assert proposal[view]["rows"]["table"] == hand["roles"][view]["rows"]["from"].split()[0], view
    staff = {c: p["best"] and p["best"]["column"] for c, p in proposal["role_staff"]["columns"].items()}
    assert staff == {"anaesthetic_key": "ANAES_KEY", "person_key": "STAFF_KEY", "role": "ROLE_CAT", "grade": None,
                     "present_from": "START_TS", "present_to": "END_TS"}
    # The diagnosis is the code that becomes a local key of the mapping view of diagnoses, and the row has a key of its
    # own made from the visit and its line; the source kind is named rather than proposed.
    diagnosis = {c: p["best"] and p["best"]["column"] for c, p in proposal["role_diagnosis"]["columns"].items()}
    assert diagnosis == {"diagnosis_key": "SEQ", "patient_key": "PERSON_KEY", "stay_key": "VISIT_KEY", "diagnosis": "ICD_CODE",
                         "is_principal": "PRIMARY_FLAG", "recorded_time": None, "source_kind": None, "documented_time": None,
                         "amends_key": None}
    assert proposal["role_diagnosis"]["columns"]["diagnosis_key"]["best"]["derive"] == {"form": "key", "with": ["VISIT_KEY"]}
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
            # The source kind of a pathway is named by the hospital schema rather than read from the dictionary, so
            # the page proposes the part's usual kind with no confidence of a match.
            assert item["confidence"] in ("high", "medium", "low", "none") if about != "source_kind" else \
                item["from"] == "the pathway's source kind" and role["source_kind"] == item["says"].split("kind ")[1].split(":")[0]
            binding = item["binding"]
            if binding and "column" in binding and not binding["path"] and not binding.get("derive"):
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
    # A reading's key is made from the two columns that identify a row of its table, and its value keeps its text only
    # where that text is not a number.
    assert "CONCAT(CAST(t0.SHEET_KEY AS varchar(254)), '-', CAST(t0.SEQ AS varchar(254))) AS reading_key" in reading
    assert "CASE WHEN TRY_CAST(t0.READ_VALUE AS float) IS NULL THEN CAST(t0.READ_VALUE AS nvarchar(4000)) END AS value_text" in reading
    assert data["roles"]["role_reading"]["columns"]["reading_key"]["says"].startswith("OBS_READING has no single column that identifies a row")
    assert "The hospital's codes of the mean pressures are not yet chosen" in reading


def test_the_proposer_refuses_to_write_a_draft_from_a_real_dictionary_into_a_published_folder(tmp_path):
    dictionary = datadict.load(DICTIONARY, TABLES)
    out = FIXTURES / "map-draft-that-must-not-exist"
    with pytest.raises(propose.ProposeError, match="only to a private folder"):
        propose.propose_map(dictionary, CATALOGUE, out, date=DATE)
    assert not out.exists()


def test_the_command_line_proposes_and_prints_names_and_counts_but_no_description(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        rolemap_main.main(["propose", str(DICTIONARY), "--tables", str(TABLES), "--catalogue", str(CATALOGUE_FILE),
                      "--out", str(tmp_path / "draft"), "--world", "the invented world"])
    text = out.getvalue()
    assert "role_patient: PERSON_MASTER, with 4 of 4 columns proposed (4 high)." in text
    assert "role_transfer: the dictionary holds no table that fits." in text
    assert "every binding awaits a person's confirmation" in text
    assert not _leaks(text, _descriptions(DICTIONARY))
    with pytest.raises(SystemExit, match="--heading is written as NAME=VALUE"):
        rolemap_main.main(["propose", str(DICTIONARY), "--catalogue", str(CATALOGUE_FILE), "--out", str(tmp_path / "other"),
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
        rolemap_main.main(["confirm", str(folder), str(tmp_path / "answers.csv"), "--catalogue", str(CATALOGUE_FILE)])
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


# A reference conversion's lineage as evidence.

REFERENCE_DBT = FIXTURES / "compare" / "reference-dbt"
SAYS_FILTER = ("A conversion at another hospital reads this table for PERSON, and tests PERSON_MASTER.TEST_PERSON_FLAG "
               "to choose its rows.")


@pytest.fixture(scope="module")
def reference():
    from schemalyser import compare
    return compare.lineage(REFERENCE_DBT)


def _thinned(tmp_path, table, column):
    """The invented dictionary with one column's description left empty, as a thin vendor's description would be."""
    import csv as csv_
    rows = list(csv_.reader(io.StringIO(DICTIONARY.read_text())))
    out = io.StringIO()
    csv_.writer(out, lineterminator="\n").writerows([r[:4] + [""] if (r[0], r[1]) == (table, column) else r for r in rows])
    path = tmp_path / "thin.csv"
    path.write_text(out.getvalue())
    return datadict.load(path, TABLES)


def test_the_contract_names_the_omop_tables_of_each_view_as_roles_md_gives_them():
    text = ROLES.read_text().split("## How each role relates to OMOP")[1].split("\n## ")[0]
    for view in rolemap.contract()["views"]:
        row = next(line for line in text.splitlines() if line.startswith(f"| {view['name']} |"))
        named = re.findall(r"\b([A-Z][A-Z_]+[A-Z])\b", row.split("|")[2])
        assert view["omop"] == [n.lower() for n in named], view["name"]


def test_a_column_with_a_thin_description_gains_the_reference_s_table_as_a_candidate(reference, tmp_path):
    thin = _thinned(tmp_path, "PERSON_MASTER", "TEST_PERSON_FLAG").restricted_to(CATALOGUE)
    without = propose.Proposer(thin).propose()["role_patient"]["columns"]["is_test"]
    assert without["best"] is None
    found = propose.Proposer(thin, reference=reference).propose()["role_patient"]["columns"]["is_test"]
    best = found["best"]
    assert (best["table"], best["column"], found["confidence"]) == ("PERSON_MASTER", "TEST_PERSON_FLAG", "low")
    assert best["reference"] == SAYS_FILTER


def test_a_dictionary_match_still_outranks_the_reference(reference):
    # A reference that fills the start of the anaesthetic from its stop: the dictionary's words name the start, so the
    # dictionary's match leads and the reference's column is only listed after it.
    import copy as copy_
    altered = copy_.deepcopy(reference)
    altered["targets"]["visit_detail"]["fields"]["visit_detail_start_datetime"]["columns"] = ["ANAES_RECORD.ANAES_STOP_TS"]
    dictionary = datadict.load(DICTIONARY, TABLES).restricted_to(CATALOGUE)
    found = propose.Proposer(dictionary, reference=altered).propose()["role_anaesthetic"]["columns"]["start_time"]
    assert (found["best"]["table"], found["best"]["column"]) == ("ANAES_RECORD", "ANAES_START_TS")
    assert not found["best"].get("reference") and found["confidence"] != "low"
    listed = [(c["table"], c["column"]) for c in found["candidates"]]
    assert ("ANAES_RECORD", "ANAES_STOP_TS") in listed
    stop = next(c for c in found["candidates"] if c["column"] == "ANAES_STOP_TS")
    assert stop["reference"] == ("A conversion at another hospital reads this table for VISIT_DETAIL, and fills "
                                 "visit_detail_start_datetime from ANAES_RECORD.ANAES_STOP_TS.")
    assert stop["score"] < found["best"]["score"]
    # Without the reference, the proposal is the same.
    plain = propose.Proposer(dictionary).propose()["role_anaesthetic"]["columns"]["start_time"]
    assert (plain["best"]["table"], plain["best"]["column"]) == ("ANAES_RECORD", "ANAES_START_TS")


def test_a_proposal_that_rests_on_the_reference_records_its_provenance(reference, tmp_path):
    thin = _thinned(tmp_path, "PERSON_MASTER", "TEST_PERSON_FLAG")
    lineage = tmp_path / "lineage.json"
    lineage.write_text(json.dumps(reference))
    out = tmp_path / "map"
    printed = io.StringIO()
    with redirect_stdout(printed):
        rolemap_main.main(["propose", str(tmp_path / "thin.csv"), "--tables", str(TABLES), "--catalogue", str(CATALOGUE_FILE),
                      "--out", str(out), "--reference", str(lineage)])
    data = json.loads((out / "map.json").read_text())
    item = data["roles"]["role_patient"]["columns"]["is_test"]
    assert item["proposed_from"] == propose.REFERENCE == "a reference conversion"
    assert item["says"] == SAYS_FILTER and item["from"] == "PERSON_MASTER.TEST_PERSON_FLAG"
    assert sum(1 for role in data["roles"].values() for i in role["columns"].values() if i.get("proposed_from")) == 1
    rolemap.read_map(out, CATALOGUE)
    board = rolemap.scoreboard(data)
    assert board["reference"]["proposals"] == 1 and board["reference"]["unanswered"] == 1
    assert any(line.startswith("Among the proposals that rested on a reference conversion rather than on the dictionary, "
                               "the page made 1 proposal.") for line in board["lines"])
    (tmp_path / "answers.csv").write_text("attribute,answer\nrole_patient.is_test,yes\n")
    propose.confirm(out, tmp_path / "answers.csv", CATALOGUE)
    item = json.loads((out / "map.json").read_text())["roles"]["role_patient"]["columns"]["is_test"]
    assert item["proposed_from"] == "a reference conversion" and item["confirmation"]["proposed_from"] == "a reference conversion"
    assert rolemap.scoreboard(json.loads((out / "map.json").read_text()))["reference"]["as_proposed"] == 1
    # A file that is not a lineage is refused with a sentence that a person can act on.
    with pytest.raises(propose.ProposeError, match="could not read this file as a lineage"):
        propose.read_reference(b"{}")


def test_the_page_passes_the_reference_to_the_proposer_and_the_journal_records_only_its_name_and_hash(reference, tmp_path):
    import hashlib
    import zipfile
    from schemalyser import describe
    _thinned(tmp_path, "PERSON_MASTER", "TEST_PERSON_FLAG")
    data = json.dumps(reference).encode()
    d = describe.Describe()
    kind, receipt = d.upload((tmp_path / "thin.csv").read_bytes(), TABLES.read_bytes(), {}, "thin.csv", "tables.csv", "Step 2",
                             data, "their-lineage.json")
    assert kind == "dictionary" and receipt["reference"] == {"file": "their-lineage.json", "targets": 6}
    d.propose(date=DATE)
    item = d.data["roles"]["role_patient"]["columns"]["is_test"]
    # Where the proposal came from is recorded when it is written, and not worked out later from its status.
    assert item["proposed_from"] == describe.REFERENCE and item["provenance"] == "a reference conversion"
    d.confirm("role_patient.is_test", "yes", date=DATE)
    files = d.folder_files(DATE)
    journal = json.loads(files["journal.json"])["entries"]
    entry = next(e for e in journal if e["kind"] == "reference lineage loaded")["payload"]
    assert entry["reference_file"] == "their-lineage.json" and entry["reference_sha256"] == hashlib.sha256(data).hexdigest()
    assert "PERSON_MASTER" not in files["journal.json"].decode()
    assert files["dictionary/reference-lineage.json"] == data
    rows = list(csv_rows(files["confirmations.csv"]))
    assert next(r for r in rows if r["attribute"] == "role_patient.is_test")["proposed_from"] == "a reference conversion"
    # The saved hospital schema opens again with the reference, and the proposal rebuilds the same.
    archive = io.BytesIO(d.folder_zip(DATE))
    with zipfile.ZipFile(archive) as held:
        saved = {name: held.read(name) for name in held.namelist()}
    again = describe.Describe()
    found = again.restore(saved)
    assert found["reference"] and again.reference == reference
    assert again.dictionary_entry["reference_sha256"] == entry["reference_sha256"]
    with pytest.raises(describe.DescribeError, match="reference conversion's lineage"):
        describe.Describe().upload(DICTIONARY.read_bytes(), None, {}, "d.csv", "t.csv", "", b"not a lineage", "x.json")


def csv_rows(data):
    import csv as csv_
    return csv_.DictReader(io.StringIO(data.decode()))


def rolemap_today():
    import datetime
    return datetime.date.today().isoformat()


def test_once_a_person_confirms_the_codes_and_the_patient_s_route_the_draft_gives_the_hand_written_map_s_answer(drafted, tmp_path):
    sys.path.insert(0, str(FIXTURES))
    import make_checks
    hand = roleshadow.hospital_run(make_checks.WORLD, FIXTURES / "conversion", rolemap.read_map(MAP, CATALOGUE), rows=500)
    con, dates = hand["conversion"].con, hand["conversion"].sandbox.date_columns
    folder = tmp_path / "map"
    shutil.copytree(drafted["folder"], folder)
    (tmp_path / "codes.csv").write_text("attribute,answer,replacement\nkind map_arterial,no,52\nkind map_cuff,no,51\n")
    propose.confirm(folder, tmp_path / "codes.csv", CATALOGUE)
    codes_only = roleshadow.result(roleshadow.duckdb_runner(con, dates, rolemap.read_map(folder, CATALOGUE)))["rows"]
    # The proposer reaches the patient by the anaesthetic record's own visit, where the hand-written map follows the
    # conversion through the theatre case. The planted cases make the two routes differ, so the answer differs until a
    # person settles the route.
    assert codes_only != hand["result"]["rows"]
    (tmp_path / "route.csv").write_text(
        "attribute,answer,replacement\nrole_anaesthetic.patient_key,no,VISIT.PERSON_KEY via ANAES_RECORD.CASE_KEY = "
        "THEATRE_CASE.CASE_KEY then THEATRE_CASE.VISIT_KEY = VISIT.VISIT_KEY\n")
    propose.confirm(folder, tmp_path / "route.csv", CATALOGUE)
    settled = roleshadow.result(roleshadow.duckdb_runner(con, dates, rolemap.read_map(folder, CATALOGUE)))["rows"]
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
    # The expectations were written against version 1.0 of the contract. A column that version 1.1 added, retyped as a
    # local key or a kind, or moved to a part of its own is left out until they are written again.
    # Only the names of the bindings are reported, never the specification's tables or columns.
    wrong = [about for about, wanted in expected["agree"].items() if _unchanged_since_1_0(about) and got.get(about) != wanted]
    # Each known disagreement is held as it stands, so that a change in the proposer shows here.
    wrong += [about for about, item in expected["differ"].items()
              if _unchanged_since_1_0(about) and got.get(about) != item["proposed"]]
    assert not wrong


# The parts and columns that version 1.1 reshaped, against which the expectations of version 1.0 say nothing.
RESHAPED = {"role_transfer", "role_technique", "role_unit_stay", "role_drug.route", "role_operation rows"}


def _unchanged_since_1_0(about):
    view, _, column = about.split(" ")[0].partition(".")
    if about in RESHAPED or view in RESHAPED or about.split(" ")[0] in RESHAPED or view in rolemap.event_parts() and about.endswith(" rows"):
        return False
    spec = next((v for v in rolemap.contract()["views"] if v["name"] == view), None)
    if spec is None:
        return False
    if not column:
        return True
    found = next((c for c in spec["columns"] if c["name"] == column), None)
    return found is not None and found["type"] != "local_key" and not found.get("per_pathway") and not found.get("optional") \
        and column not in spec["key"]
