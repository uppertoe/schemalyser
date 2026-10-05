import json
import re
import shutil
from pathlib import Path

import pytest
import sqlglot

from schemalyser import profile
from schemalyser.profile import ProfileError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CONVERSION = FIXTURES / "conversion"
# The output of the profile script for the invented conversion, as SQL Server returned it, without a header line.
REAL = (FIXTURES / "profile" / "invented-core-profile.csv").read_text()
# The same script pointed at a schema that lacks most CDM tables, through a source prefix that reaches nothing.
SPARSE = (FIXTURES / "profile" / "invented-sparse-profile.csv").read_text()
# An invented output of the central team's general database profile, in a CDM 5.3 shape.
GENERAL = (FIXTURES / "profile" / "invented-general-profile.csv").read_text()
HEADER = ",".join(profile.LAYOUT) + "\n"


def edited(text, old, new):
    assert old in text
    return text.replace(old, new)


@pytest.fixture(scope="module")
def script():
    return profile.script(CONVERSION, {"source_prefix": "clarity_shadow.dbo."})


# The script.

def test_the_script_has_the_house_layout_and_one_result_set(script):
    assert script.count(f"CREATE TABLE {profile.TABLE}") == 1
    assert script.count("SELECT\n\tdp.ITEM_CATEGORY") == 1
    for category, (_, header) in profile.CATEGORIES.items():
        assert f"'{category}', 'HEADER', " + ", ".join(f"'{h}'" for h in header) in script
    # Every question that touches a core or source table runs through sp_executesql inside TRY and CATCH.
    assert script.count("BEGIN TRY") == script.count("EXEC sys.sp_executesql") == script.count("END CATCH;")
    assert "'ERROR', 'DATA'" in script
    # The comments come from the wording, and the script ends by removing its table.
    assert script.startswith("-- " + profile.WORDING["header"][0].format(version=profile.VERSION))
    assert script.rstrip().endswith(f"DROP TABLE IF EXISTS {profile.TABLE};")


def test_the_script_returns_only_names_types_and_rounded_counts(script):
    # Every count of rows is rounded down to ten, and no source value is selected into the result.
    assert script.count("/ 10) * 10") >= 10
    assert "HAVING COUNT_BIG(*) >= 10" in script
    assert "MAX([person_id])" in script and "LEN(REPLACE(CAST(MAX(" in script
    for statement in re.findall(r"N'(INSERT INTO .*?)';\n", script):
        assert "SELECT DISTINCT" not in statement.split(" FROM ")[0]
    # The joins come from the step SQL, looking through the cast that the steps apply.
    assert "CAST(s.[VISIT_KEY] AS VARCHAR(50))" in script
    assert "[clarity_shadow].[dbo].[THEATRE_CASE]" in script
    assert "c.[visit_source_value] = d.k" in script
    # A join to a table that the layer writes says nothing about the core, so it is not profiled.
    assert "visit_detail.visit_detail_source_value" not in script


def test_the_joins_are_derived_from_the_anaesthesia_steps():
    pairs = profile.conversion_facts(CONVERSION)["pairs"]
    assert set(pairs) == {("visit_occurrence", "visit_source_value", "THEATRE_CASE", "VISIT_KEY"),
                          ("visit_occurrence", "visit_source_value", "OBS_SHEET", "VISIT_KEY"),
                          ("visit_occurrence", "visit_source_value", "DRUG_GIVEN", "VISIT_KEY"),
                          ("provider", "provider_source_value", "ANAES_STAFF", "STAFF_KEY")}
    assert pairs[("visit_occurrence", "visit_source_value", "DRUG_GIVEN", "VISIT_KEY")]["steps"] == ["drug_exposure_infusion.sql"]
    assert pairs[("provider", "provider_source_value", "ANAES_STAFF", "STAFF_KEY")]["cast"] is None


def test_without_a_source_prefix_the_script_does_not_compare_keys():
    text = profile.script(CONVERSION)
    assert "SOURCE_KEY_MATCH', 'DATA'" not in text.replace("''", "'")
    assert profile.WORDING["no_source_prefix"] in text
    assert "[dbo].[visit_occurrence]" in text


@pytest.mark.parametrize("settings", [
    {"omop_schema": "dbo]; DROP TABLE person; --"},
    {"omop_schema": "dbo.cdm"},
    {"omop_schema": ""},
    {"omop_schema": 7},
    {"source_prefix": "clarity_shadow.dbo"},
    {"source_prefix": "a.b.c.d."},
    {"source_prefix": "x'; SHUTDOWN; --."},
    {"source_prefix": "[clarity].[dbo]."},
    {"unknown_setting": "dbo"},
])
def test_hostile_settings_are_refused(settings):
    with pytest.raises(ProfileError):
        profile.script(CONVERSION, settings)


def test_a_step_that_joins_on_an_unusual_name_is_refused(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    step = folder / "visit_detail_through_case.sql"
    step.write_text(step.read_text().replace("CAST(tc.VISIT_KEY AS varchar(50))", 'CAST(tc."VISIT KEY]" AS varchar(50))'))
    with pytest.raises(ProfileError):
        profile.script(folder)


def test_the_script_parses_as_t_sql(script):
    # sqlglot cannot read every statement of a T-SQL batch, so each dynamic statement is parsed by itself.
    statements = [s.replace("''", "'") for s in re.findall(r"EXEC sys\.sp_executesql N'(.*?)';\n", script)]
    assert len(statements) > 50
    for statement in statements:
        if not statement.startswith("WITH"):
            sqlglot.parse_one(statement.replace(profile.TABLE, "profile_table"), dialect="tsql")


# Reading.

def test_reading_the_real_output_from_sql_server():
    found = profile.read(REAL, CONVERSION)
    assert found["format"] == "core" and found["rejected"] == 0 and not found["errors"]
    assert found["cdm_source"]["cdm_version"] == "5.4" and found["cdm_source"]["cdm_version_concept_id"] == 756265
    assert found["tables"]["visit_occurrence"] == {"present": True, "rows": 490, "key_type": "int", "key_digits": 3}
    assert found["tables"]["visit_detail"]["rows"] == 0
    assert found["type_concepts"]["procedure_occurrence"] == {"32817": 340}
    assert found["source_values"]["visit_occurrence.visit_source_value"]["digits_percent"] == 100
    rates = {(m["core"], m["source"]): m["percent"] for m in found["matches"]}
    assert rates == {("visit_occurrence.visit_source_value", "THEATRE_CASE.VISIT_KEY"): 98,
                     ("visit_occurrence.visit_source_value", "OBS_SHEET.VISIT_KEY"): 98,
                     ("visit_occurrence.visit_source_value", "DRUG_GIVEN.VISIT_KEY"): 98,
                     ("provider.provider_source_value", "ANAES_STAFF.STAFF_KEY"): 100}
    assert found["observation_period"] == {"persons": 490, "with_period": 490}
    json.dumps(found)


def test_a_header_line_and_a_byte_order_mark_are_tolerated():
    assert profile.read("﻿" + HEADER + REAL, CONVERSION) == profile.read(REAL, CONVERSION)
    assert profile.read(REAL.replace("\n", "\r\n")) == profile.read(REAL)


def test_reading_the_sparse_output_keeps_its_errors():
    found = profile.read(SPARSE, CONVERSION)
    assert found["rejected"] == 0
    assert sum(1 for t in found["tables"].values() if not t["present"]) == 32
    assert {e["section"] for e in found["errors"]} == {"CDM_SOURCE", "OBSERVATION_PERIOD", "SOURCE_KEY_MATCH", "SOURCE_VALUE_SHAPE"}
    assert all(e["number"] == 208 for e in found["errors"])


@pytest.mark.parametrize("old,new", [
    ("CDM_TABLE,person,Y,490,int,3", "SECRET_ROWS,person,Y,490,int,3"),         # a category the script never writes
    ("CDM_TABLE,person,Y,490,int,3", "CDM_TABLE,person,Y,many,int,3"),          # a count that is not a number
    ("CDM_TABLE,person,Y,490,int,3", "CDM_TABLE,person,Y,490,int"),             # a row with too few values
    ("OBSERVATION_PERIOD,490,490,,,", "OBSERVATION_PERIOD,490,4.9e2,,,"),
    # A count that is not rounded down to ten did not come from the script.
    ("CDM_TABLE,person,Y,490,int,3", "CDM_TABLE,person,Y,491,int,3"),
    ("TYPE_CONCEPT,drug_exposure,drug_type_concept_id,32818,300,", "TYPE_CONCEPT,drug_exposure,drug_type_concept_id,32818,7,"),
])
def test_a_malformed_output_is_refused(old, new):
    with pytest.raises(ProfileError):
        profile.read(edited(REAL, old, new), CONVERSION)


@pytest.mark.parametrize("old,new", [
    ("CDM_TABLE,person,Y,490,int,3", "CDM_TABLE,patient_names,Y,490,int,3"),
    ("CDM_TABLE,person,Y,490,int,3", "CDM_TABLE,person,Y,490,=HYPERLINK(1),3"),
    ("OBSERVATION_PERIOD,490,490,,,", "OBSERVATION_PERIOD,490,-1,,,"),
    ("ANAES_STAFF.STAFF_KEY", "STAFF_MASTER.STAFF_NAME"),
    ("provider.provider_source_value,ANAES_STAFF", "provider.provider_name,ANAES_STAFF"),
    ("CDM_SOURCE,5.4,not loaded", "CDM_SOURCE,5.4,<script>alert(1)</script>"),
    ("PROFILE,1,2026-10-04,dbo,Y,", "PROFILE,1,2026-10-04,dbo;drop,Y,"),
])
def test_a_hostile_row_is_left_out_and_counted(old, new):
    found = profile.read(edited(REAL, old, new), CONVERSION)
    assert found["rejected"] == 1
    text = json.dumps(found)
    for word in ("patient_names", "STAFF_NAME", "provider_name", "HYPERLINK", "script", "drop"):
        assert word not in text


def test_without_the_conversion_any_plain_source_name_is_accepted_but_only_for_a_source_value_field():
    found = profile.read(edited(REAL, "ANAES_STAFF.STAFF_KEY", "OTHER_TABLE.OTHER_KEY"))
    assert any(m["source"] == "OTHER_TABLE.OTHER_KEY" for m in found["matches"])
    assert profile.read(edited(REAL, "ANAES_STAFF.STAFF_KEY", "OTHER_TABLE.OTHER_KEY"), CONVERSION)["rejected"] == 1


def test_reading_the_central_teams_general_profile():
    found = profile.read(GENERAL)
    assert found["format"] == "general" and found["approximate"]
    assert found["omop_schema"] == "omop"
    assert found["tables"]["visit_occurrence"]["rows"] == 12300
    assert found["tables"]["measurement"]["rows"] == 1200000
    assert found["tables"]["provider"]["rows"] == 810
    assert found["tables"]["measurement"]["key_type"] == "bigint"
    assert found["tables"]["visit_detail"]["present"] is False
    assert "admitted_from_concept_id" in found["absent_fields"]["visit_occurrence"]
    assert "measurement_event_id" in found["absent_fields"]["measurement"]
    # Local names are counted and never returned, and neither is the database name.
    assert found["local_objects"] == 1 and found["other_schema_tables"] == 1 and found["local_fields"]["person"] == 1
    text = json.dumps(found)
    for word in ("zz_", "INVENTED_OMOP", "staging", "ix_person", "admitting_source"):
        assert word not in text


# Findings and summary.

def _questions(findings):
    return [item["question"] for item in findings]


def test_the_real_output_gives_only_the_findings_it_should():
    findings = profile.suggest(profile.read(REAL, CONVERSION), CONVERSION)
    for item in findings:
        assert set(item) == {"question", "finding", "setting_or_step", "suggestion"}
    # The core's surgical procedures share the type concept of the anaesthetic steps, which is worth a look.
    assert _questions(findings) == [profile.WORDING["duplicate_question"].format(table="drug_exposure"),
                                    profile.WORDING["duplicate_question"].format(table="procedure_occurrence")]


def test_a_core_in_the_shape_of_cdm_53_names_the_fields_the_steps_need():
    text = edited(REAL, "CDM_SOURCE,5.4,not loaded,756265,1,", "CDM_SOURCE,v5.3.1,v5.0 01-JAN-24,,1,")
    text += ("CDM_FIELD_ABSENT,measurement,measurement_event_id,,,\n"
             "CDM_FIELD_ABSENT,measurement,meas_event_field_concept_id,,,\n"
             "CDM_FIELD_ABSENT,procedure_occurrence,procedure_end_date,,,\n")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    version = next(f for f in findings if f["question"] == profile.WORDING["version_question"])
    assert "v5.3.1" in version["finding"]
    for name in ("measurement.measurement_event_id", "measurement.meas_event_field_concept_id",
                 "procedure_occurrence.procedure_end_date"):
        assert name in version["suggestion"]


def test_the_general_profile_in_the_shape_of_cdm_53_gives_absent_fields_and_bigint():
    findings = profile.suggest(profile.read(GENERAL), CONVERSION)
    absent = [f for f in findings if f["question"] == profile.WORDING["absent_question"]]
    assert any("measurement_event_id" in f["finding"] for f in absent)
    bigint = next(f for f in findings if f["question"] == profile.WORDING["bigint_question"])
    assert bigint["setting_or_step"] == "identifier_type" and "measurement" in bigint["finding"]
    assert profile.WORDING["missing_question"] in _questions(findings)


def test_a_bigint_core_asks_for_the_identifier_type():
    text = REAL.replace(",int,", ",bigint,")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    bigint = next(f for f in findings if f["question"] == profile.WORDING["bigint_question"])
    assert "visit_occurrence" in bigint["finding"] and bigint["suggestion"] == profile.WORDING["bigint_suggestion"]


def test_an_int_key_near_its_limit_is_raised():
    text = edited(REAL, "CDM_TABLE,measurement,Y,0,int,", "CDM_TABLE,measurement,Y,0,int,10")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    assert profile.WORDING["headroom_question"] in _questions(findings)


def test_a_low_match_rate_says_the_guess_about_source_values_is_wrong():
    text = edited(REAL, "THEATRE_CASE.VISIT_KEY,500,490,98", "THEATRE_CASE.VISIT_KEY,500,0,0")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    match = [f for f in findings if f["question"].startswith("We assumed that the core keeps")]
    assert len(match) == 1 and match[0]["setting_or_step"] == "visit_detail_through_case.sql"
    assert "about 0 of about 500" in match[0]["finding"] and "(0 per cent)" in match[0]["finding"]
    # The findings never name a source table or column.
    assert "THEATRE_CASE" not in json.dumps(findings) and "VISIT_KEY" not in json.dumps(findings)


def test_values_that_are_not_digits_are_raised_when_the_match_was_not_measured():
    text = "\n".join(line for line in REAL.splitlines() if not line.startswith("SOURCE_KEY_MATCH,visit")) + "\n"
    text = edited(text, "visit_occurrence.visit_source_value,490,100,8,8", "visit_occurrence.visit_source_value,490,40,8,9")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    shape = [f for f in findings if f["setting_or_step"] == "source_prefix"]
    assert len(shape) == 1 and "40 per cent" in shape[0]["finding"]


def test_persons_without_an_observation_period_are_raised():
    text = edited(REAL, "OBSERVATION_PERIOD,490,490,,,", "OBSERVATION_PERIOD,490,300,,,")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    assert profile.WORDING["periods_question"] in _questions(findings)


def test_a_core_that_populates_visit_detail_is_raised():
    text = edited(REAL, "CDM_TABLE,visit_detail,Y,0,int,", "CDM_TABLE,visit_detail,Y,1200,int,4")
    findings = profile.suggest(profile.read(text, CONVERSION), CONVERSION)
    assert profile.WORDING["visit_detail_question"] in _questions(findings)


def test_the_sparse_output_raises_missing_tables_and_errors():
    findings = profile.suggest(profile.read(SPARSE, CONVERSION), CONVERSION)
    questions = _questions(findings)
    assert profile.WORDING["missing_question"] in questions and profile.WORDING["errors_question"] in questions


def test_the_summary_holds_only_standard_names_and_rounded_numbers():
    text = profile.summary(profile.read(REAL, CONVERSION))
    assert "The core records CDM version 5.4" in text
    assert "the join to visit_occurrence.visit_source_value in drug_exposure_infusion.sql" in text
    for name in ("THEATRE_CASE", "OBS_SHEET", "DRUG_GIVEN", "ANAES_STAFF", "VISIT_KEY", "STAFF_KEY", "clarity", "dbo"):
        assert name not in text
    numbers = {int(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]*\b", text)}
    assert all(n % 10 == 0 or n <= 100 for n in numbers)
    general = profile.summary(profile.read(GENERAL))
    for name in ("zz_", "INVENTED_OMOP", "omop,", "staging"):
        assert name not in general


def test_the_wording_is_in_full_sentences():
    def strings(value):
        if isinstance(value, str):
            yield value
        else:
            yield from value
    for key, value in profile.WORDING.items():
        for text in strings(value):
            assert "?" not in text and "!" not in text, key
            if not key.startswith(("summary_digits", "summary_join")):
                # A sentence may open with a table name, which is written in lower case.
                assert (text[0].isupper() or text.startswith("{table}")) and text.endswith("."), key


def test_the_command_line_writes_the_script_and_reads_a_result(tmp_path, capsys, monkeypatch):
    out = tmp_path / "profile.sql"
    monkeypatch.setattr("sys.argv", ["schemalyser.profile", str(CONVERSION), "--out", str(out),
                                     "--source-prefix", "clarity_shadow.dbo.", "--omop-schema", "dbo"])
    profile.main()
    assert out.read_text() == profile.script(CONVERSION, {"source_prefix": "clarity_shadow.dbo."})
    result = tmp_path / "result.csv"
    result.write_bytes(b"\xef\xbb\xbf" + REAL.encode())
    monkeypatch.setattr("sys.argv", ["schemalyser.profile", "--read", str(result), str(CONVERSION)])
    profile.main()
    printed = capsys.readouterr().out
    assert "The core records CDM version 5.4" in printed and "THEATRE_CASE" not in printed
    monkeypatch.setattr("sys.argv", ["schemalyser.profile", str(CONVERSION), "--out", str(out), "--omop-schema", "x;y"])
    with pytest.raises(SystemExit):
        profile.main()
