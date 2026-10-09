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


# Transplanting the reference's routes into a conversion of our own.

DICTIONARY = FIXTURES / "dictionary" / "invented-dictionary.csv"
DICTIONARY_TABLES = FIXTURES / "dictionary" / "invented-tables.csv"


@pytest.fixture(scope="module")
def transplanted(dbt, tmp_path_factory):
    from schemalyser import datadict, transplant
    out = tmp_path_factory.mktemp("transplant") / "conversion"
    report = transplant.transplant(dbt, datadict.load(DICTIONARY, DICTIONARY_TABLES), out, date="2026-10-09")
    return {"out": out, "report": report}


def test_the_transplant_writes_one_step_for_each_table_of_the_reference(transplanted):
    import sqlglot
    from sqlglot import exp
    out, report = transplanted["out"], transplanted["report"]
    steps = json.loads((out / "conversion.json").read_text())
    assert [s["table"] for s in steps] == ["person", "provider", "visit_occurrence", "condition_era", "visit_detail", "device_exposure"]
    assert [s["layer"] for s in steps] == ["core"] * 3 + ["anaesthesia"] * 3
    counts = report["counts"]
    assert (counts["written"], counts["incomplete"], counts["not_written"], counts["skipped"]) == (6, 0, 0, 0)
    assert counts["not_in_dictionary"] == 0 and counts["fields_mapped"] == 36 and counts["placeholders"] == 10
    for step in steps:
        tree = sqlglot.parse_one((out / step["file"]).read_text(), dialect="tsql")
        assert isinstance(tree, exp.Select), step["file"]
    person = (out / "person.sql").read_text()
    # A literal that the lineage redacted is never invented: the filter waits as a comment, with its placeholders.
    assert "--   AND COALESCE(pm.TEST_PERSON_FLAG, {{decision:person.filter_1_1}}) = {{decision:person.filter_1_2}}" in person
    assert "0 AS race_concept_id,  -- {{decision:person.race_concept_id}}" in person
    # An identifier of another OMOP table is looked up there by its source value.
    detail = (out / "visit_detail.sql").read_text()
    assert "JOIN omop.visit_occurrence ovo ON ovo.visit_source_value = CAST(v.VISIT_KEY AS varchar(50))" in detail
    # A join that would repeat rows is not made, and the field behind it is left empty with the reason.
    assert "ANAES_STAFF is not joined" in detail and "NULL AS provider_id,  -- not reproduced" in detail
    # An expression that the lineage could not follow to one column is written as NULL, naming its shape.
    assert "NULL AS visit_end_date,  -- not reproduced from the lineage's expression CAST({VISIT.ADMIT_TS | VISIT.DISCH_TS} AS DATE)" \
        in (out / "visit_occurrence.sql").read_text()
    era = (out / "condition_era.sql").read_text()
    assert "MIN(CAST(v.ADMIT_TS AS DATE)) AS condition_era_start_date" in era and "GROUP BY" in era
    decisions = json.loads((out / "decisions.json").read_text())["decisions"]
    assert len(decisions) == 10
    held = next(d for d in decisions if d["name"] == "person.filter_1_2")
    assert held["columns"] == ["PERSON_MASTER.TEST_PERSON_FLAG"] and held["type"] == "str" and held["value"] is None
    assert held["question"].startswith("Please give the text that takes the place of {{decision:person.filter_1_2}}")
    catalogue = (out / "catalogue.csv").read_text().splitlines()
    assert catalogue[0].startswith("TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,DATA_TYPE")
    assert "dbo,VISIT,VISIT_KEY,1,numeric,,18,0,NO" in catalogue
    assert not any(",ANAES_STAFF," in line for line in catalogue)
    text = (out / "transplant-report.md").read_text()
    assert "Schemalyser wrote 6 steps, of which none is incomplete." in text
    for name in ("conversion.json", "decisions.json", "transplant-report.json", "transplant-report.md", "person.sql"):
        assert str(FIXTURES.parent) not in (out / name).read_text()


def test_the_transplanted_steps_run_in_the_testbed_on_a_sandbox_built_from_their_catalogue(transplanted, tmp_path):
    from schemalyser import testbed
    world = tmp_path / "world"
    (world / "requests").mkdir(parents=True)
    shutil.copy(transplanted["out"] / "catalogue.csv", world / "catalogue.csv")
    report = testbed.run(str(world), tmp_path / "testbed", rows=20, conversion_folder=transplanted["out"])
    assert [(s["file"], s["status"]) for s in report["steps"]] == [
        (f, "ok") for f in ("person.sql", "provider.sql", "visit_occurrence.sql", "condition_era.sql", "visit_detail.sql",
                            "device_exposure.sql")]
    assert all(s["rows"] > 0 for s in report["steps"])
    passed = {c["check"]: c["passed"] for c in report["checks"]}
    assert passed["every step ran cleanly"]
    # The transplant is a draft whose steps no person has reviewed, so the run says so and the release is refused.
    assert report["routes"]["draft"]["sentence"].startswith("This conversion is a draft, transplanted from a reference")
    assert len(report["routes"]["problems"]) == 6 and all("does not record the review" in p for p in report["routes"]["problems"])
    assert not passed["the release script was written and carries every step that ran"]
    assert report["release"]["reason"].startswith("person.sql: this step is written directly from the source tables")
    # The one required field that the lineage cannot give is reported, and nothing else.
    assert next(c for c in report["checks"] if c["check"] == "every row fits the CDM's field list")["detail"] == [
        "visit_occurrence.visit_end_date: 20 rows have no value in a required field"]


def test_an_existing_step_is_kept_and_the_transplanted_one_is_offered_beside_it(dbt, tmp_path):
    from schemalyser import datadict, transplant
    out = tmp_path / "conversion"
    report = transplant.transplant(dbt, datadict.load(DICTIONARY, DICTIONARY_TABLES), out,
                                   targets=["visit_detail", "condition_era", "measurement"], existing=CONVERSION)
    steps = json.loads((out / "conversion.json").read_text())
    detail = next(s for s in steps if s["table"] == "visit_detail")
    assert detail["file"] == "visit_detail_through_case.sql"
    assert detail["alternatives"] == ["visit_detail.sql", {"file": "visit_detail_from_reference.sql", **transplant._route(
        "a reference conversion's lineage, transplanted on " + report["date"])}]
    assert (out / "visit_detail_through_case.sql").read_text() == (CONVERSION / "visit_detail_through_case.sql").read_text()
    assert "The existing conversion already writes VISIT_DETAIL in visit_detail_through_case.sql, which is kept." \
        in (out / "visit_detail_from_reference.sql").read_text()
    assert steps.index(next(s for s in steps if s["table"] == "condition_era")) < [s["layer"] for s in steps].index("derived")
    assert next(e for e in report["targets"] if e["target"] == "measurement")["status"] == "skipped"
    assert json.loads((CONVERSION / "conversion.json").read_text())[10].get("alternatives") == ["visit_detail.sql"]


def test_a_table_or_column_that_the_dictionary_lacks_is_reported_and_never_invented(dbt, tmp_path):
    from schemalyser import datadict, transplant
    lines = [line for line in DICTIONARY.read_text().splitlines()
             if not line.startswith("AIRWAY_DEVICE,") and ",ANAES_STOP_TS," not in line]
    thin = tmp_path / "thin.csv"
    thin.write_text("\n".join(lines) + "\n")
    out = tmp_path / "conversion"
    report = transplant.transplant(dbt, datadict.load(thin, DICTIONARY_TABLES), out, targets=["visit_detail", "device_exposure"])
    assert report["not_in_dictionary"] == ["AIRWAY_DEVICE", "ANAES_RECORD.ANAES_STOP_TS"]
    rows = {e["target"]: e for e in report["targets"]}
    assert rows["device_exposure"]["status"] == "incomplete" and rows["device_exposure"]["file"] is None
    assert rows["visit_detail"]["status"] == "incomplete" and "visit_detail_end_date" in rows["visit_detail"]["fields_left_empty"]
    assert [s["file"] for s in json.loads((out / "conversion.json").read_text())] == ["visit_detail.sql"]
    sql = (out / "visit_detail.sql").read_text()
    assert "This step is incomplete, because the dictionary does not list ANAES_RECORD.ANAES_STOP_TS." in sql
    assert "ar.ANAES_STOP_TS" not in sql and "AIRWAY_DEVICE" not in (out / "catalogue.csv").read_text()


def test_the_transplant_command_writes_the_folder(tmp_path, capsys):
    assert compare.main(["reference", str(DBT), "--out", str(tmp_path / "theirs.json")]) == 0
    capsys.readouterr()
    assert compare.main(["transplant", "--lineage", str(tmp_path / "theirs.json"), "--dictionary", str(DICTIONARY),
                         "--tables", str(DICTIONARY_TABLES), "--out", str(tmp_path / "out"), "--targets", "person,provider"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("Schemalyser wrote 2 steps, of which none is incomplete.")
    assert {p.name for p in (tmp_path / "out").iterdir()} == {
        "conversion.json", "person.sql", "provider.sql", "catalogue.csv", "decisions.json", "transplant-report.json",
        "transplant-report.md", "draft.json"}


def test_the_transplant_marks_every_step_it_writes_as_direct_and_the_folder_as_a_draft(transplanted):
    from schemalyser import convert, release
    out = transplanted["out"]
    steps = json.loads((out / "conversion.json").read_text())
    assert all(s["route"] == "direct" and s["reference"] == "a reference conversion's lineage, transplanted on 2026-10-09"
               and s["reason"].startswith("The step was transplanted") and "review" not in s for s in steps)
    draft = convert.read_draft(out)
    assert draft["draft"] is True and draft["reference"] == steps[0]["reference"] and draft["date"] == "2026-10-09"
    # The release names the first step that lacks its review, and once every step is reviewed it still refuses the draft.
    with pytest.raises(release.Refused, match="person.sql: this step is written directly"):
        release.script(out)
    reviewed = out.parent / "reviewed"
    shutil.copytree(out, reviewed)
    for step in steps:
        step["review"] = {"by": "a tester", "on": "2026-10-09"}
    (reviewed / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(release.Refused, match="This conversion is a draft"):
        release.script(reviewed)
