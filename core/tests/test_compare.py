"""The comparison of a reference conversion's lineage with ours, on the invented reference in fixtures/compare."""
import json
import shutil
from pathlib import Path

import pytest

from schemalyser import compare

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PLAIN = FIXTURES / "compare" / "reference-plain"
DBT = FIXTURES / "compare" / "reference-dbt"
CONVERSION = FIXTURES / "conversion"


@pytest.fixture(scope="module")
def plain():
    return compare.lineage(PLAIN)


@pytest.fixture(scope="module")
def dbt():
    return compare.lineage(DBT)


@pytest.fixture(scope="module")
def ours():
    return compare.lineage(CONVERSION)


@pytest.fixture(scope="module")
def report(ours, plain):
    return compare.compare(ours, plain)


def _without_files(data):
    return {name: {k: v for k, v in entry.items() if k != "files"} for name, entry in data["targets"].items()}


def _row(report, target):
    return next(r for r in report["detail"]["targets"] if r["target"] == target)


def test_the_plain_and_dbt_variants_have_the_same_lineage(plain, dbt):
    assert plain["kind"] == "plain" and dbt["kind"] == "dbt"
    assert _without_files(plain) == _without_files(dbt)
    assert plain["sources"] == dbt["sources"]
    assert plain["files"]["unparsed"] == [] and dbt["files"]["unparsed"] == []


def test_each_way_of_naming_a_target_is_recognised(plain, dbt):
    assert set(plain["targets"]) == {"person", "visit_occurrence", "visit_detail", "device_exposure", "provider", "condition_era"}
    # INSERT INTO, the file's name, and the columns of the final SELECT.
    assert plain["targets"]["person"]["files"] == ["person.sql"]
    assert plain["targets"]["visit_detail"]["files"] == ["visit_detail.sql"]
    assert plain["targets"]["visit_occurrence"]["files"] == ["encounters.sql"]
    # An alias in the models file, and one in a config block.
    assert dbt["targets"]["device_exposure"]["files"] == ["models/omop/airway_devices.sql"]
    assert dbt["targets"]["condition_era"]["files"] == ["models/omop/diagnosis_eras.sql"]


def test_an_intermediate_is_followed_to_its_tables_filters_and_aggregations(plain):
    era = plain["targets"]["condition_era"]
    assert era["source_tables"] == ["PERSON_MASTER", "VISIT", "VISIT_DIAGNOSIS"]
    assert {"tables": ["VISIT", "VISIT_DIAGNOSIS"], "on": [["VISIT.VISIT_KEY", "VISIT_DIAGNOSIS.VISIT_KEY"]]} in era["joins"]
    assert era["filters"] == [{"test": "VISIT_DIAGNOSIS.PRIMARY_FLAG = <str>", "columns": ["VISIT_DIAGNOSIS.PRIMARY_FLAG"]}]
    assert "MIN(CAST(VISIT.ADMIT_TS AS DATE))" in era["aggregations"] and "COUNT(*)" in era["aggregations"]
    assert era["fields"]["condition_era_start_date"]["columns"] == ["VISIT.ADMIT_TS"]


def test_a_join_through_another_omop_table_is_written_as_a_join_of_source_tables(plain):
    visits = plain["targets"]["visit_detail"]
    assert {"tables": ["ANAES_RECORD", "VISIT"], "on": [["ANAES_RECORD.VISIT_KEY", "VISIT.VISIT_KEY"]]} in visits["joins"]
    assert visits["other_omop_tables"] == ["provider", "visit_occurrence"]


def test_literals_are_redacted_to_their_type(plain, dbt):
    for data in (plain, dbt):
        text = json.dumps(data)
        assert "LOCAL_" not in text and "'Y'" not in text and "'N'" not in text
    assert {"test": "COALESCE(PERSON_MASTER.TEST_PERSON_FLAG, <str>) = <str>", "columns": ["PERSON_MASTER.TEST_PERSON_FLAG"]} \
        in plain["targets"]["person"]["filters"]


def test_our_own_conversion_folder_is_read_in_full(ours):
    assert ours["kind"] == "conversion"
    assert ours["files"]["unparsed"] == []
    assert "visit_detail" in ours["targets"] and "anaesthetic" not in ours["targets"]
    assert ours["targets"]["visit_detail"]["files"] == ["visit_detail_through_case.sql"]


def test_the_report_finds_the_planted_agreements_and_differences(report):
    assert report["summary"]["counts"] == {
        "targets_compared": 15, "agreeing": 3, "differing": 2, "only_ours": 9, "only_theirs": 1,
        "routes_we_lack": 6, "uncertainty_items": 6,
    }
    for target in ("person", "visit_occurrence", "provider"):
        assert _row(report, target)["status"] == "agree"
    # The shared route: the reference reaches the visit's patient and ward directly, and ours through OMOP tables.
    assert [j["tables"] for j in _row(report, "visit_occurrence")["joins"]["shared"]] == [["PERSON_MASTER", "VISIT"], ["VISIT", "WARD_DEF"]]
    # The different join.
    detail = _row(report, "visit_detail")
    assert detail["status"] == "differ"
    assert [j["tables"] for j in detail["joins"]["only_theirs"]] == [["ANAES_RECORD", "VISIT"]]
    assert [j["tables"] for j in detail["joins"]["only_ours"]] == [["ANAES_RECORD", "THEATRE_CASE"], ["THEATRE_CASE", "VISIT"]]
    assert detail["source_tables"]["only_ours"] == ["THEATRE_CASE"]
    assert detail["routes_we_lack"] == {"tables": [], "joins": [["ANAES_RECORD", "VISIT"]]}
    # The filter on a different column, with everything else alike.
    device = _row(report, "device_exposure")
    assert device["filters"] == {"shared": [], "only_ours": [["AIRWAY_DEVICE.PLACED_TS"]], "only_theirs": [["AIRWAY_DEVICE.REMOVED_TS"]]}
    assert device["joins"]["only_ours"] == device["joins"]["only_theirs"] == [] and not device["fields"]["fed_differently"]
    # The target that only the reference writes.
    assert _row(report, "condition_era")["status"] == "only_theirs"
    assert _row(report, "condition_era")["uncertainty"] == []


def test_the_uncertainty_item_for_a_different_pathway_is_worded_as_agreed(report):
    assert _row(report, "visit_detail")["uncertainty"] == [
        "Two candidate pathways exist for the anaesthetic episode (VISIT_DETAIL): ours reads ANAES_RECORD through THEATRE_CASE "
        "to VISIT, the reference reads ANAES_RECORD joined directly to VISIT. The hospital schema has not established whether "
        "both are needed. Validate their coverage separately before accepting either as complete."]
    assert _row(report, "device_exposure")["uncertainty"][0].startswith(
        "The two conversions choose the rows of airway and other devices (DEVICE_EXPOSURE) differently: ours tests "
        "AIRWAY_DEVICE.PLACED_TS, the reference tests AIRWAY_DEVICE.REMOVED_TS.")


def test_the_saved_hospital_schema_gives_a_third_view(ours, plain):
    found = compare.compare(ours, plain, compare.schema_view(FIXTURES / "map" / "map.json"))
    detail = _row(found, "visit_detail")
    assert "THEATRE_CASE" in detail["schema"]["tables"]
    assert "The hospital schema proposes our pathway, and it has not established whether the reference's is also needed." \
        in detail["uncertainty"][0]


def test_the_summary_names_nothing(report, plain, ours):
    names = set(plain["sources"]) | {c for cols in plain["sources"].values() for c in cols} | set(ours["sources"])
    names |= {t for t in report["detail"]["targets"] for t in [t["target"], t["target"].upper()]}
    shared = compare.markdown(report).split("## Detail")[0]
    summary = json.dumps(report["summary"])
    for name in names:
        assert name not in shared and name not in summary
    assert report["summary"]["shareable"] is True and report["detail"]["private"] is True


def test_a_file_that_does_not_parse_is_recorded_without_its_text(tmp_path):
    folder = tmp_path / "reference"
    shutil.copytree(PLAIN, folder)
    (folder / "broken.sql").write_text("SELECT SECRET_COLUMN_XYZ FROM WHERE ((( GROUP\n")
    found = compare.lineage(folder)
    assert found["files"]["unparsed"] == [{"file": "broken.sql", "error": found["files"]["unparsed"][0]["error"]}]
    assert found["files"]["unparsed"][0]["error"] in ("ParseError", "TokenError", "NoStatement")
    assert "SECRET_COLUMN_XYZ" not in json.dumps(found)
    assert set(found["targets"]) == set(compare.lineage(PLAIN)["targets"])


def test_a_model_with_other_jinja_is_recorded_as_not_read(tmp_path):
    folder = tmp_path / "project"
    shutil.copytree(DBT, folder)
    (folder / "models" / "omop" / "loops.sql").write_text("{% for x in [1, 2] %} select {{ x }} as HIDDEN_NAME {% endfor %}\n")
    found = compare.lineage(folder)
    assert {"file": "models/omop/loops.sql", "error": "Jinja"} in found["files"]["unparsed"]
    assert "HIDDEN_NAME" not in json.dumps(found)


def test_the_yaml_reader_takes_sources_and_aliases():
    data = compare._yaml((DBT / "models" / "sources.yml").read_text())
    assert data["sources"][0]["tables"][0] == {"name": "person_master", "identifier": "PERSON_MASTER"}
    assert data["sources"][1]["schema"] == "cdm"
    assert compare._dbt_names(DBT)[1] == {"airway_devices": "device_exposure"}


def test_the_commands_write_the_lineage_and_the_report(tmp_path, capsys):
    assert compare.main(["reference", str(PLAIN), "--out", str(tmp_path / "theirs.json")]) == 0
    assert "private" in capsys.readouterr().out
    assert compare.main(["reference", str(CONVERSION), "--out", str(tmp_path / "ours.json")]) == 0
    assert compare.main(["report", "--ours", str(tmp_path / "ours.json"), "--theirs", str(tmp_path / "theirs.json"),
                         "--out", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "The comparison covered 15 OMOP tables." in printed
    text = (tmp_path / "out" / "report.md").read_text()
    assert text.index("## Summary, which may be shared") < text.index("## Detail, which is private")
    assert json.loads((tmp_path / "out" / "report.json").read_text())["format"] == compare.REPORT_FORMAT
    for written in ("theirs.json", "ours.json", "out/report.json", "out/report.md"):
        assert str(FIXTURES.parent) not in (tmp_path / written).read_text()
    assert compare.main(["report", "--ours", str(tmp_path / "missing.json"), "--theirs", str(tmp_path / "theirs.json"),
                         "--out", str(tmp_path / "out")]) == 1
