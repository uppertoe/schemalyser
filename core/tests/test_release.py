"""Tests for the release script of the anaesthesia layer, with the hostile inputs that it must refuse."""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import sqlglot

from schemalyser import convert, release
from schemalyser.release import Refused

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CONVERSION = FIXTURES / "conversion"
WRITTEN = ["visit_detail", "procedure_occurrence", "measurement", "observation", "drug_exposure", "device_exposure"]
GOOD_STEP = "SELECT ROW_NUMBER() OVER (ORDER BY r.SEQ) AS measurement_id, r.SEQ AS person_id FROM OBS_READING r"
# The route of a step written directly from the source tables, with everything that a direct step records.
DIRECT = {"route": "direct", "reference": "an invented reference conversion", "reason": "The test needs one direct step.",
          "review": {"by": "a tester", "on": "2026-10-09"}}


def _folder(tmp_path, step=GOOD_STEP, gate=None, mappings=None, name="step.sql", table="measurement", settings=None):
    """A small conversion folder with one anaesthesia step, and optionally a gate, mapping rows and settings."""
    folder = tmp_path / "conversion"
    folder.mkdir(parents=True)
    (folder / "conversion.json").write_text(json.dumps([{"table": table, "file": name, "layer": "anaesthesia", **DIRECT}]))
    if "/" not in name and "\n" not in name and name not in (".", ".."):
        (folder / name).write_text(step)
    if gate is not None:
        (folder / "gates").mkdir()
        (folder / "gates" / "010_gate.sql").write_text(gate)
    if mappings is not None:
        (folder / "source_to_concept_map.csv").write_text(mappings)
    if settings is not None:
        (folder / "release.json").write_text(json.dumps(settings))
    return folder


def test_the_release_script_holds_only_the_anaesthesia_layer():
    from schemalyser.catalogue import Catalogue
    text = release.script(CONVERSION)
    # Nine anaesthesia steps and two derived steps, each writing to a table of the layer's own and never to a core table.
    assert text.count("INSERT INTO [$(AnaesSchemaName)].[") == 10 + 2 + 1      # the steps, and the mapping rows
    assert "INSERT INTO [$(OmopSchemaName)]" not in text and "DELETE FROM [$(OmopSchemaName)]" not in text
    assert "[$(AnaesSchemaName)].[person]" not in text and "$(SourcePrefix)[PERSON_MASTER]" not in text
    # A step reads the core's people and visits, the published views of the tables it adds to, and its own mapping rows.
    assert "JOIN [$(OmopSchemaName)].[visit_occurrence] AS [vo]" in text
    assert "FROM [$(AnaesPubSchemaName)].[visit_detail]" in text and "FROM $(SourcePrefix)[ANAES_RECORD] AS [ar]" in text
    assert "[$(AnaesSchemaName)].[source_to_concept_map] AS [what]" in text
    assert "('1', 0, 'SITE_SEX', 'Male', 8507, 'Gender', '1970-01-01', '2099-12-31', NULL)" in text
    # One union view for each table the layer adds to, a view of the core's rows for every other table, seven gates,
    # and one transaction around the tables, the rows and the gates.
    assert len(re.findall(r"CREATE OR ALTER VIEW \[\$\(AnaesPubSchemaName\)\]\.\[\w+\] AS SELECT .* UNION ALL ", text)) == 6
    assert text.count("CREATE OR ALTER VIEW [$(AnaesPubSchemaName)].[") == len(convert.cdm_fields()) + 2 == 41
    assert "CREATE OR ALTER VIEW [$(AnaesPubSchemaName)].[person] AS SELECT [person_id], [gender_concept_id]" in text
    assert text.count("THROW 50000") == 9
    assert text.index("BEGIN TRANSACTION;") < text.index("CREATE TABLE") < text.index("DELETE FROM") < text.index("THROW 50000") < text.index(
        "COMMIT TRANSACTION;")
    assert text.count("BEGIN TRANSACTION;") == 1 and "SCHEMALYSER_" not in text
    # Every rewritten step is still T-SQL that can be read.
    fields = dict(release._fields())
    for row in convert.custom_rows(convert.read_tables(CONVERSION)):
        fields.setdefault(row["table"], []).append(row)
    for step, sql in release._steps(CONVERSION):
        rewritten, columns = release.rewrite(sql, WRITTEN + ["anaesthetic", "anaesthetic_phase"], step["table"], fields=fields)
        assert sqlglot.parse_one(rewritten, dialect="tsql") is not None and columns[0].endswith("_id")
    manifest = release.source_manifest(CONVERSION, Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text()))
    assert "OBS_READING,READ_VALUE" in manifest and "ANAES_RECORD,ANAES_START_TS" in manifest and "PERSON_MASTER" not in manifest
    # The approved wording is carried as it stands.
    assert all(f"-- {line}" in text for line in release.WORDING["header"])
    assert "-- " + release.WORDING["stage_2"] in text


@pytest.mark.parametrize("step", [
    "SELECT 1 AS measurement_id, 2 AS person_id; DROP TABLE omop.person",
    "SELECT 1 AS measurement_id; SELECT 2 AS measurement_id",
    "UPDATE omop.person SET year_of_birth = 1900",
    "DELETE FROM omop.person",
    "MERGE INTO omop.person AS p USING OBS_READING AS r ON 1 = 1 WHEN MATCHED THEN DELETE;",
    "TRUNCATE TABLE omop.person",
    "DROP TABLE omop.person",
    "SELECT r.SEQ AS measurement_id INTO omop.copy FROM OBS_READING r",
    "SELECT q.a AS measurement_id FROM OPENQUERY(other_server, 'SELECT 1 AS a') AS q",
    "SELECT q.a AS measurement_id FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1 AS a') AS q",
    "SELECT q.a AS measurement_id FROM OPENDATASOURCE('SQLNCLI', 'Data Source=x').db.dbo.t AS q",
    "EXEC('DROP TABLE omop.person')",
    "SELECT r.SEQ AS measurement_id FROM far.db.dbo.OBS_READING r",
    "SELECT @x = 1",
    # An output column that is not a field of the table, or that tries to leave its brackets.
    "SELECT 1 AS measurement_id, 2 AS shoe_size",
    "SELECT 1 AS [measurement_id]], 2 AS [x]",
    "SELECT 1 AS measurement_id, 2 AS [person_id]]) SELECT 1; DROP TABLE omop.person; --]",
    "SELECT * FROM OBS_READING",
    "SELECT 1 AS measurement_id, 2 AS measurement_id",
    # A line break or $( in a literal or an identifier, which sqlcmd would read as a command or a variable.
    "SELECT 1 AS measurement_id, 'a\n:!! rm -rf /\n' AS value_source_value",
    "SELECT 1 AS measurement_id, 'a\n:on error ignore\n' AS value_source_value",
    "SELECT 1 AS measurement_id, 'a\nGO\nDROP TABLE x\n' AS value_source_value",
    "SELECT 1 AS measurement_id, 'a\r:!! ls' AS value_source_value",
    "SELECT 1 AS measurement_id, '$(OmopSchemaName)' AS value_source_value",
    "SELECT 1 AS measurement_id, N'$(X)' AS value_source_value",
    "SELECT 1 AS measurement_id, r.[SEQ\n:!! ls] AS person_id FROM OBS_READING r",
])
def test_a_step_that_is_not_one_safe_select_is_refused(tmp_path, step):
    with pytest.raises(Refused):
        release.script(_folder(tmp_path, step))


def test_a_union_and_a_common_table_expression_are_accepted(tmp_path):
    text = release.script(_folder(tmp_path, "SELECT 1 AS measurement_id, 2 AS person_id UNION ALL SELECT 2, 3"))
    assert "SELECT [step].[measurement_id] + @base, [step].[person_id]" in text
    step = "WITH r AS (SELECT x.SEQ FROM OBS_READING x) SELECT ROW_NUMBER() OVER (ORDER BY r.SEQ) AS measurement_id, r.SEQ AS person_id FROM r"
    text = release.script(_folder(tmp_path / "b", step))
    # T-SQL takes a common table expression before the INSERT, and not inside the derived table.
    assert re.search(r"WITH \[r\] AS \(.*\)\nINSERT INTO \[\$\(AnaesSchemaName\)\]\.\[measurement\]", text, re.S)


@pytest.mark.parametrize("gate", [
    "SELECT 1 AS x; DROP TABLE omop.person",
    "DELETE FROM omop.person",
    "SELECT 1 AS x INTO #kept",
    "SELECT q.a FROM OPENQUERY(s, 'SELECT 1 AS a') AS q",
    "SELECT 'a\n:!! ls' AS x",
    "SELECT '$(AnaesSchemaName)' AS x",
])
def test_a_gate_that_is_not_one_safe_select_is_refused(tmp_path, gate):
    with pytest.raises(Refused):
        release.script(_folder(tmp_path, gate=gate))


@pytest.mark.parametrize("value", ["a\n:!! ls", "a\r\nGO", "$(OmopSchemaName)"])
def test_a_mapping_value_that_could_carry_a_command_is_refused(tmp_path, value):
    header = ",".join(row["field"] for row in release._fields()["source_to_concept_map"])
    row = ["1", "0", "SITE_X", value, "8507", "Gender", "1970-01-01", "2099-12-31", ""]
    import csv
    import io
    out = io.StringIO()
    csv.writer(out).writerows([header.split(","), row])
    with pytest.raises(Refused):
        release.script(_folder(tmp_path, mappings=out.getvalue()))
    with pytest.raises(Refused):
        release.script(_folder(tmp_path / "b"), mappings=[row])


@pytest.mark.parametrize("settings", [
    {"anaesthesia_schema": "dbo"},                          # would delete the core's rows
    {"published_schema": "dbo"},
    {"published_schema": "anaes_cdm"},
    {"anaesthesia_schema": "DBO"},                          # SQL Server compares names without regard to case
    {"omop_schema": "omop]; DROP TABLE x; --"},
    {"omop_schema": 'dbo"\n:!! ls\n:setvar X "y'},
    {"anaesthesia_schema": "1abc"},
    {"anaesthesia_schema": "a" * 129},
    {"identifier_type": "INT; DROP TABLE omop.person"},
    {"identifier_type": "VARCHAR(10)"},
    {"source_prefix": "(SELECT 1) AS x; DROP TABLE omop.person; --"},
    {"source_prefix": "staging"},
    {"source_prefix": "a.b.c.d."},
    {"source_prefix": "a..b."},
    {"identifier_type": "INT"},                             # the default offset does not fit an INT
    {"identifier_offset": -1},
    {"identifier_offset": "5000000000"},
    {"on_failure": "ignore"},
    {"no_such_setting": 1},
])
def test_a_setting_that_could_reach_the_core_or_break_out_is_refused(tmp_path, settings):
    with pytest.raises(Refused):
        release.script(_folder(tmp_path), settings)
    with pytest.raises(Refused):
        release.script(_folder(tmp_path / "b", settings=settings))


def test_good_settings_change_where_things_are_and_nothing_else(tmp_path):
    text = release.script(_folder(tmp_path), {"omop_schema": "omop", "source_prefix": "Staging.dbo.", "identifier_type": "INT",
                                              "identifier_offset": 1_000_000_000})
    assert 'OmopSchemaName="omop"' in text and 'SourcePrefix="Staging.dbo."' in text
    assert "[measurement_id] INT NOT NULL" in text and "1000000000" in text


def test_the_operator_s_variables_are_never_overridden_and_are_checked_when_the_script_runs():
    text = release.script(CONVERSION)
    # A :setvar in a script overrides the operator's -v, so the script sets none, and names each variable in its run line.
    assert ":setvar" not in text
    assert release.run_command() in text and all(f'-v ' in release.run_command() and f"{name}=" in release.run_command()
                                                  for _, name in release.VARIABLES)
    assert set(re.findall(r"\$\((\w+)\)", text)) == {name for _, name in release.VARIABLES}
    # The guards come before the transaction: the schemas must be three and differ, and the core must stay below the offset.
    guards = text[:text.index("BEGIN TRANSACTION;")]
    assert "IF UPPER(N'$(OmopSchemaName)') = UPPER(N'$(AnaesSchemaName)')" in guards
    assert "N'$(AnaesSchemaName)' LIKE N'%[^A-Za-z0-9_]%'" in guards and "N'$(SourcePrefix)' LIKE N'%[^A-Za-z0-9_.]%'" in guards
    for table in WRITTEN:
        key = release._key(release._fields()[table])
        assert f"IF (SELECT MAX([{key}]) FROM [$(OmopSchemaName)].[{table}]) >= 5000000000" in guards
    # sqlcmd reads no line as a command except the one that stops it at the first error.
    # The guards are a batch of their own, so that they run even when a value would stop the rest from being read.
    assert [line for line in text.splitlines() if release.SQLCMD_LINE.match(line)] == [":on error exit", "GO"]
    assert text.index("THROW 50003") < text.index("\nGO\n") < text.index("BEGIN TRANSACTION;")


@pytest.mark.parametrize("name", ["../person.sql", "a/b.sql", "..", "x.sql\n:!! ls", "x y.sql", "x;.sql"])
def test_a_file_name_that_could_leave_its_folder_or_its_comment_is_refused(tmp_path, name):
    with pytest.raises(Refused):
        release.script(_folder(tmp_path, name=name))
    assert convert.layer_problems([{"table": "measurement", "file": name, "layer": "anaesthesia"}])


def test_a_gate_file_name_is_checked_too(tmp_path):
    folder = _folder(tmp_path, gate="SELECT 1 AS x WHERE 1 = 0")
    (folder / "gates" / "010_gate.sql").rename(folder / "gates" / "a b.sql")
    with pytest.raises(Refused):
        release.script(folder)


def test_a_mapping_is_found_only_in_the_select_that_makes_it():
    sql = """SELECT 1 AS measurement_id FROM OTHER x WHERE EXISTS (
               SELECT 1 FROM THEATRE_CASE x JOIN omop.source_to_concept_map m ON m.source_code = x.SOME_COLUMN)"""
    assert convert.mapped_columns(sqlglot.parse_one(sql, dialect="tsql")) == [("THEATRE_CASE", "SOME_COLUMN")]
    # A correlated reference reaches the table of the query around it.
    sql = """SELECT 1 AS a FROM OUTER_TABLE o WHERE EXISTS (
               SELECT 1 FROM omop.source_to_concept_map m WHERE m.source_code = o.CODE)"""
    assert convert.mapped_columns(sqlglot.parse_one(sql, dialect="tsql")) == [("OUTER_TABLE", "CODE")]


def test_identifiers_are_moved_into_the_layer_s_range_with_a_primary_key():
    text = release.script(CONVERSION)
    assert "SET @base = COALESCE((SELECT MAX([measurement_id]) FROM [$(AnaesSchemaName)].[measurement]), 5000000000);" in text
    assert text.count("SET @base = ") == 10 and "[step].[measurement_id] + @base" in text
    assert "MAX([measurement_id]) FROM [$(AnaesPubSchemaName)]" not in text
    # Every identifier of the layer's own tables is a BIGINT, a concept stays an INT, and each table has a primary key.
    assert "[measurement_id] BIGINT NOT NULL" in text and "[measurement_event_id] BIGINT NULL" in text
    assert "[measurement_concept_id] INT NOT NULL" in text
    assert "CONSTRAINT [xpk_measurement] PRIMARY KEY ([measurement_id])" in text
    # A table from an earlier run that no longer matches its definition is dropped and created again, inside the transaction.
    assert "OBJECT_ID(N'[$(AnaesSchemaName)].[xpk_measurement]', N'PK') IS NULL" in text
    assert "(1, N'measurement_id', N'bigint', 8, 0)" in text
    smaller = release.script(CONVERSION, {"identifier_type": "INT", "identifier_offset": 1_000_000_000})
    assert "(1, N'measurement_id', N'int', 4, 0)" in smaller
    assert text.index("BEGIN TRANSACTION;") < text.index("    DROP TABLE [$(AnaesSchemaName)].[measurement];")


def test_a_step_must_write_its_table_s_identifier(tmp_path):
    with pytest.raises(Refused):
        release.script(_folder(tmp_path, "SELECT r.SEQ AS person_id FROM OBS_READING r"))


def test_on_failure_keep_or_empty():
    keep = release.script(CONVERSION)
    assert keep.count("COMMIT TRANSACTION;") == 1 and release.WORDING["header"][3] in keep
    assert release.WORDING["gate_failed"].format(name="060_an_anaesthetic_was_written") in keep
    empty = release.script(CONVERSION, {"on_failure": "empty"})
    # The removal is committed before any row is written, so that a failed gate leaves the tables empty.
    assert empty.count("COMMIT TRANSACTION;") == 2 and release.WORDING["header_empty"] in empty
    assert empty.index("DELETE FROM") < empty.index("COMMIT TRANSACTION;") < empty.index("INSERT INTO [$(AnaesSchemaName)].[source_to_concept_map]")
    assert release.WORDING["gate_failed_empty"].format(name="060_an_anaesthetic_was_written") in empty
    assert release.WORDING["gate_failed"].format(name="060_an_anaesthetic_was_written") not in empty


def test_mapping_rows_can_be_given_in_place_of_the_folder_s():
    rows = [["X1", 0, "SITE_DRUG", "propofol", 753626, "RxNorm", "1970-01-01", "2099-12-31", None],
            {"source_code": "X2", "source_concept_id": 0, "source_vocabulary_id": "SITE_DRUG", "source_code_description": "it's",
             "target_concept_id": 1, "target_vocabulary_id": "RxNorm", "valid_start_date": "1970-01-01", "valid_end_date": "2099-12-31"}]
    text = release.script(CONVERSION, mappings=rows)
    assert "('X1', 0, 'SITE_DRUG', 'propofol', 753626, 'RxNorm', '1970-01-01', '2099-12-31', NULL)" in text
    assert "('X2', 0, 'SITE_DRUG', 'it''s', 1, 'RxNorm', '1970-01-01', '2099-12-31', NULL)" in text
    assert "SITE_SEX" not in text.split("-- " + release.WORDING["stage_3"])[0]


def test_the_wording_is_written_in_full_sentences():
    approved = ["header", "omop_schema", "anaesthesia_schema", "published_schema", "source_prefix", "stage_1", "stage_2", "stage_3",
                "stage_4", "stage_5", "gate_failed"]
    assert list(release.WORDING)[:len(approved)] == approved
    for key, value in release.WORDING.items():
        for line in value if isinstance(value, list) else [value]:
            assert line[0].isupper() and line.endswith((".", ":")) and "!" not in line and "?" not in line, key
            assert "$(" not in line and "\n" not in line


def test_the_command_refuses_a_hostile_folder_and_exits_with_1(tmp_path, monkeypatch, capsys):
    folder = _folder(tmp_path, "SELECT 1 AS measurement_id; DROP TABLE omop.person")
    monkeypatch.setattr(sys, "argv", ["release", str(folder), "--catalogue", str(FIXTURES / "invented-catalogue.csv"),
                                      "--out", str(tmp_path / "out")])
    assert release.main() == 1
    assert "The command has not written release.sql" in capsys.readouterr().err and not (tmp_path / "out" / "release.sql").exists()
    monkeypatch.setattr(sys, "argv", ["release", str(CONVERSION), "--catalogue", str(FIXTURES / "invented-catalogue.csv"),
                                      "--out", str(tmp_path / "good")])
    assert release.main() == 0 and (tmp_path / "good" / "release.sql").exists()
    assert "sqlcmd -S SERVER -d DATABASE -b -i release.sql -v" in capsys.readouterr().out
    shutil.rmtree(tmp_path / "good")


# The derived layer and its custom tables.

def test_the_custom_tables_are_created_in_the_anaesthesia_schema_and_published():
    text = release.script(CONVERSION)
    assert "CREATE TABLE [$(AnaesSchemaName)].[anaesthetic] (" in text and "CREATE TABLE [$(AnaesSchemaName)].[anaesthetic_phase] (" in text
    assert "[weight_kg] FLOAT NULL" in text and "[phase_name] VARCHAR(50) NOT NULL" in text and "[anaesthetic_id] BIGINT NOT NULL" in text
    assert "CONSTRAINT [xpk_anaesthetic] PRIMARY KEY ([anaesthetic_id])" in text
    # The published view shows the layer's own rows, because the core has no such table, and no guard reads the core for one.
    assert ("EXEC(N'CREATE OR ALTER VIEW [$(AnaesPubSchemaName)].[anaesthetic] AS SELECT [anaesthetic_id], [person_id]" in text
            and "FROM [$(AnaesSchemaName)].[anaesthetic]');" in text)
    assert "[$(OmopSchemaName)].[anaesthetic]" not in text
    # A derived step reads the published views and keeps the identifiers it writes, after every anaesthesia step.
    derived = text[text.index("-- anaesthetic.sql"):]
    assert "@base" not in derived.split("-- " + release.WORDING["stage_4"])[0]
    assert "FROM [$(AnaesPubSchemaName)].[procedure_occurrence] AS [po]" in derived
    assert text.index("-- device_exposure.sql") < text.index("-- anaesthetic.sql") < text.index("-- anaesthetic_phase.sql")
    assert "FROM [$(AnaesPubSchemaName)].[anaesthetic] AS [a]" in text      # a gate reads a custom table through its view
    assert release.WORDING["stage_1_custom"] in text and release.WORDING["stage_3_derived"] in text
    assert "SELECT 'anaesthetic_phase' AS table_name" in text


def _derived_folder(tmp_path, step, tables=None, table="anaesthetic"):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    (folder / "anaesthetic.sql").write_text(step)
    entries = json.loads((folder / "conversion.json").read_text())
    for entry in entries:
        if entry["file"] == "anaesthetic.sql":
            entry["table"] = table
    (folder / "conversion.json").write_text(json.dumps(entries))
    if tables is not None:
        (folder / "tables.json").write_text(tables)
    return folder


@pytest.mark.parametrize("step", [
    "SELECT po.procedure_occurrence_id AS anaesthetic_id, 1 AS shoe_size FROM omop.procedure_occurrence po",
    "SELECT po.person_id FROM omop.procedure_occurrence po",                      # no primary key
    "SELECT 1 AS anaesthetic_id; DROP TABLE omop.person",
    "SELECT 1 AS anaesthetic_id, '$(OmopSchemaName)' AS phase_name",
    "SELECT 1 AS anaesthetic_id INTO #x",
])
def test_a_derived_step_is_checked_like_any_other(tmp_path, step):
    with pytest.raises(Refused):
        release.script(_derived_folder(tmp_path, step))


def test_a_derived_step_may_write_only_a_custom_table(tmp_path):
    with pytest.raises(Refused, match="not one of them"):
        release.script(_derived_folder(tmp_path, "SELECT 1 AS measurement_id", table="measurement"))


@pytest.mark.parametrize("tables", [
    '[{"name": "person", "description": "A copy.", "fields": [{"name": "person_id", "type": "integer", "required": true, "primary_key": true, "description": "One."}]}]',
    '[{"name": "anaesthetic]; DROP TABLE x; --", "description": "A table.", "fields": [{"name": "a", "type": "integer", "required": true, "primary_key": true, "description": "One."}]}]',
    '[{"name": "anaesthetic", "description": "A table.\\n:!! ls", "fields": [{"name": "a", "type": "integer", "required": true, "primary_key": true, "description": "One."}]}]',
    '[{"name": "anaesthetic", "description": "A table.", "fields": [{"name": "a", "type": "integer", "required": true, "primary_key": true, "description": "$(AnaesSchemaName)"}]}]',
    '[{"name": "anaesthetic", "description": "A table.", "fields": [{"name": "a", "type": "int); DROP TABLE x; --", "required": true, "primary_key": true, "description": "One."}]}]',
    '[{"name": "anaesthetic", "description": "A table.", "fields": [{"name": "a]] INT); --", "type": "integer", "required": true, "primary_key": true, "description": "One."}]}]',
    'this is not json',
])
def test_a_hostile_tables_file_is_refused(tmp_path, tables):
    with pytest.raises(Refused):
        release.script(_derived_folder(tmp_path, (CONVERSION / "anaesthetic.sql").read_text(), tables))


# A step's alternatives.

def test_the_script_carries_only_the_step_s_own_file_and_checks_its_alternatives(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    steps = json.loads((folder / "conversion.json").read_text())
    assert any(step.get("alternatives") for step in steps)
    offered = release.script(folder)
    (folder / "conversion.json").write_text(json.dumps([{k: v for k, v in s.items() if k != "alternatives"} for s in steps]))
    assert release.script(folder) == offered
    assert "-- visit_detail_through_case.sql\n" in offered and "THEATRE_CASE" in offered
    assert "-- visit_detail.sql\n" not in offered and "-- measurement_blood_pressure.sql\n" not in offered


@pytest.mark.parametrize("alternative, written", [
    ("other.sql", "SELECT r.SEQ AS person_id FROM OBS_READING r"),                         # no identifier
    ("other.sql", "SELECT r.SEQ AS measurement_id, r.SEQ AS shoe_size FROM OBS_READING r"),  # not a field
    ("other.sql", "SELECT r.SEQ AS measurement_id FROM OBS_READING r; DROP TABLE x"),        # two statements
    ("other.sql", "SELECT r.SEQ AS measurement_id FROM OBS_READING r WHERE r.SEQ = @x"),     # a variable
    ("other.sql", "SELECT '$(OmopSchemaName)' AS measurement_id FROM OBS_READING r"),        # a sqlcmd variable
    ("missing.sql", None),                                                                    # not in the folder
    ("../other.sql", None),                                                                   # leaves the folder
    ("step.sql", None),                                                                       # the step itself
])
def test_an_alternative_is_checked_like_a_step(tmp_path, alternative, written):
    folder = _folder(tmp_path)
    if written is not None:
        (folder / alternative).write_text(written)
    steps = json.loads((folder / "conversion.json").read_text())
    steps[0]["alternatives"] = [alternative]
    (folder / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(Refused):
        release.script(folder)


def test_a_good_alternative_is_accepted(tmp_path):
    folder = _folder(tmp_path)
    (folder / "other.sql").write_text(GOOD_STEP.replace("r.SEQ AS person_id", "r.SEQ + 0 AS person_id"))
    steps = json.loads((folder / "conversion.json").read_text())
    plain = release.script(folder)
    steps[0]["alternatives"] = ["other.sql"]
    (folder / "conversion.json").write_text(json.dumps(steps))
    assert release.script(folder) == plain


# The conversion's counts.

def test_the_script_reports_the_conversion_s_counts_after_the_rows_written():
    text = release.script(CONVERSION)
    report = text.split(f"-- {release.WORDING['stage_5']}", 1)[1]
    assert f"-- {release.WORDING['stage_5_counts']}" in report
    assert "-- counts/010_anaesthetics_whose_stop_is_before_their_start.sql" in report
    assert ("SELECT REPLACE(N'The number of anaesthetics that the anaesthesia layer has left out because the recorded stop is "
            "before the recorded start is {count}.', N'{count}', CAST((SELECT [c].[anaesthetics] FROM (") in report
    assert "$(SourcePrefix)[ANAES_RECORD]" in report and "[$(AnaesPubSchemaName)].[visit_detail]" in report


@pytest.mark.parametrize("written", [
    "-- The number is {count}, and $(this) is a variable.\nSELECT COUNT(*) AS n FROM ANAES_RECORD ar",
    "-- The number is {count}.\nSELECT COUNT(*) AS n, COUNT(*) AS m FROM ANAES_RECORD ar",
    "-- The number is {count}.\nDELETE FROM ANAES_RECORD",
    "-- There is no place for the number.\nSELECT COUNT(*) AS n FROM ANAES_RECORD ar",
])
def test_a_count_that_breaks_a_rule_is_refused(tmp_path, written):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    (folder / "counts" / "030_hostile.sql").write_text(written + "\n")
    with pytest.raises(release.Refused):
        release.script(folder)


def test_a_count_whose_column_name_could_close_its_brackets_is_refused(tmp_path):
    # The security review's reproduction: a column name holding ] that, written without escaping, ends the
    # count's own statement and adds a DROP TABLE after it.
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    (folder / "counts" / "030_x.sql").write_text(
        "-- The number is {count}.\n"
        "SELECT COUNT(*) AS [n]]FROM(SELECT 1 n)[c]])AS varchar))r;DROP TABLE [dbo]].[person]];"
        "SELECT REPLACE('a','b',CAST((SELECT 1[c] FROM omop.visit_detail\n")
    with pytest.raises(Refused, match="output column"):
        release.script(folder)


def test_every_count_column_is_a_plain_name_written_in_brackets():
    text = release.script(CONVERSION)
    for statement in re.findall(r"^SELECT REPLACE\(N'.*", text, re.MULTILINE):
        assert re.search(r"CAST\(\(SELECT \[c\]\.\[[A-Za-z_][A-Za-z0-9_]*\] FROM \($", statement)


# The route of each step.

def test_the_script_states_the_share_of_steps_on_each_route_and_names_each_step_s_route():
    text = release.script(CONVERSION)
    assert ("-- " + release.WORDING["routes"].format(count=10, roles=1, direct=9, roles_verb="is", direct_verb="are")) in text
    at = text.index("-- visit_detail_through_case.sql\n")
    following = text[at:].splitlines()[1]
    assert following.startswith("-- This step is written directly from the source tables. It rests on the invented world's own conversion")
    at = text.index("-- drug_exposure_infusion_roles.sql\n")
    assert text[at:].splitlines()[1] == "-- " + release.WORDING["route_roles_compiled"].format(world="the invented world")
    # A derived step takes neither route, so its comment names none, and names only its class.
    assert text[text.index("-- anaesthetic.sql\n"):].splitlines()[1] == "-- " + release.WORDING["class_step"].format(grade="C")
    assert text[text.index("-- anaesthetic.sql\n"):].splitlines()[2].startswith("INSERT INTO")
    assert (release.route_summary(CONVERSION)["direct"], release.route_summary(CONVERSION)["roles"]) == (9, 1)


@pytest.mark.parametrize("drop, what", [("review", "the review"), ("reference", "the reference"), ("reason", "the reason")])
def test_a_direct_step_without_its_reference_reason_or_review_is_refused_by_name(tmp_path, drop, what):
    folder = _folder(tmp_path)
    steps = json.loads((folder / "conversion.json").read_text())
    steps[0].pop(drop)
    (folder / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(Refused, match=rf"^step\.sql: this step is written directly from the source tables, and conversion\.json does not record {what}"):
        release.script(folder)


def test_a_step_with_no_route_is_refused_by_name(tmp_path):
    folder = _folder(tmp_path)
    (folder / "conversion.json").write_text(json.dumps([{"table": "measurement", "file": "step.sql", "layer": "anaesthesia"}]))
    with pytest.raises(Refused, match="step.sql: conversion.json records no route for this step"):
        release.script(folder)


def test_a_draft_is_refused_until_its_marker_is_removed(tmp_path):
    folder = _folder(tmp_path)
    (folder / convert.DRAFT_FILE).write_text(json.dumps({"draft": True, "reference": "an invented reference"}))
    with pytest.raises(Refused, match="This conversion is a draft, transplanted from an invented reference"):
        release.script(folder)
    (folder / convert.DRAFT_FILE).unlink()
    release.script(folder)


def test_a_step_over_the_roles_is_compiled_through_the_hospital_schema_and_carried():
    text = release.script(CONVERSION)
    at = text.index("-- drug_exposure_infusion_roles.sql\n")
    step = text[at:text.index("AS [step];", at)]
    # The role view and the mapping view that the step reads come first, written from the invented map over the source
    # tables, and the step's own common table expressions follow them; the direct step is not carried.
    assert step.index("WITH [role_drug] AS (") < step.index("[map_drug_concept] AS (") < step.index("[superseded] AS (")
    assert "FROM $(SourcePrefix)[DRUG_GIVEN] AS [g]" in step and "FROM $(SourcePrefix)[DRUG_ORDER] AS [o]" in step
    assert "$(SourcePrefix)[role_drug]" not in text and "$(SourcePrefix)[map_drug_concept]" not in text
    assert "-- drug_exposure_infusion.sql\n" not in text
    assert "-- " + release.WORDING["roles_compiled"].format(world="the invented world") in text
    # The compiled step is one SELECT over the hospital's tables and the OMOP tables, and names no role view as a table.
    compiled = release.compile_roles_step((CONVERSION / "drug_exposure_infusion_roles.sql").read_text(),
                                          release.read_schema(CONVERSION), "drug_exposure_infusion_roles.sql")
    tree = sqlglot.parse_one(compiled, dialect="tsql")
    named = {cte.alias for cte in tree.find_all(sqlglot.exp.CTE)}
    assert {"role_drug", "map_drug_concept"} <= named
    read = {(t.db, t.name) for t in tree.find_all(sqlglot.exp.Table) if t.name not in named}
    assert read == {("", "DRUG_GIVEN"), ("", "DRUG_ORDER"), ("omop", "visit_detail")}
    # The source manifest lists what the compiled step reads.
    from schemalyser.catalogue import Catalogue
    manifest = release.source_manifest(CONVERSION, Catalogue.from_csv((FIXTURES / "invented-catalogue.csv").read_text()))
    assert "DRUG_GIVEN,AMENDS_KEY" in manifest and "DRUG_ORDER,ORDER_KEY" in manifest


def _changed_roles_step(tmp_path, change=lambda sql: sql):
    """A copy of the invented conversion whose step over the roles is changed, so that it is no longer the invented
    world's own file and no map lies beside the copy."""
    folder = tmp_path / "copy" / "conversion"
    shutil.copytree(CONVERSION, folder)
    path = folder / "drug_exposure_infusion_roles.sql"
    path.write_text(change(path.read_text()) + "\n-- A copy, changed for the test.\n")
    return folder


def test_a_step_over_the_roles_is_refused_without_a_hospital_schema_and_with_one_that_does_not_supply_it(tmp_path):
    folder = _changed_roles_step(tmp_path)
    assert convert.hospital_schema(folder) is None
    with pytest.raises(Refused, match="drug_exposure_infusion_roles.sql: the step is written over the roles, and no hospital schema"):
        release.script(folder)
    assert "WITH [role_drug] AS (" in release.script(folder, schema=FIXTURES / "map")
    # A map that does not bind role_drug cannot compile the step, which says which view it lacks.
    bare = tmp_path / "bare"
    shutil.copytree(FIXTURES / "map", bare)
    data = json.loads((bare / "map.json").read_text())
    data["roles"].pop("role_drug")
    (bare / "map.json").write_text(json.dumps(data))
    with pytest.raises(Refused, match="the step reads role_drug, which the hospital schema of the invented world does not supply"):
        release.script(folder, schema=bare)


def test_a_step_over_the_roles_that_reads_a_source_table_or_hides_one_is_refused(tmp_path):
    folder = _changed_roles_step(tmp_path, lambda sql: sql.replace("FROM   role_drug d", "FROM   DRUG_GIVEN d"))
    with pytest.raises(Refused, match="reads only the role views, the mapping views and the OMOP tables"):
        release.script(folder, schema=FIXTURES / "map")
    # A common table expression of the step that bears the name of a table that the map's view reads would hide it.
    folder = _changed_roles_step(tmp_path / "again", lambda sql: sql.replace("concept AS (", "drug_order AS (").replace(
        "LEFT JOIN concept c", "LEFT JOIN drug_order c"))
    with pytest.raises(Refused, match="the view role_drug of the hospital schema reads a table named DRUG_ORDER"):
        release.script(folder, schema=FIXTURES / "map")


def test_a_step_over_the_roles_that_waits_beside_a_direct_step_is_checked_and_not_carried(tmp_path):
    folder = tmp_path / "conversion"
    shutil.copytree(CONVERSION, folder)
    steps = json.loads((folder / "conversion.json").read_text())
    infusion = next(s for s in steps if s["file"] == "drug_exposure_infusion_roles.sql")
    direct = infusion["alternatives"][0]
    infusion.clear()
    infusion.update(table="drug_exposure", file="drug_exposure_infusion.sql", layer="anaesthesia", roles_step="drug_exposure_infusion_roles.sql",
                    **{k: direct[k] for k in ("route", "reference", "reason", "review")})
    (folder / "conversion.json").write_text(json.dumps(steps))
    text = release.script(folder)
    assert "-- drug_exposure_infusion.sql\n" in text and "role_drug" not in text
    (folder / "drug_exposure_infusion_roles.sql").write_text(
        (CONVERSION / "drug_exposure_infusion_roles.sql").read_text().replace("FROM   role_drug d", "FROM   DRUG_GIVEN d"))
    with pytest.raises(Refused, match="reads only the role views, the mapping views and the OMOP tables"):
        release.script(folder)


# The classes that the static policy derives for the steps and gates.

def test_the_release_records_the_class_of_every_step_and_gate():
    from schemalyser import policy
    classes = release.step_classes(CONVERSION)
    steps = json.loads((CONVERSION / "conversion.json").read_text())
    assert [e["file"] for e in classes if e["what"] == "step"] == [s["file"] for s in steps]
    assert all(e["policy_version"] == policy.POLICY_VERSION and e["report"]["purpose"] == "conversion" for e in classes)
    # Every step and gate that the script carries is of class C, the roles step as compiled through the map.
    carried = [e for e in classes if e["carried"]]
    assert len(carried) == 12 + 9 and all(e["execution_class"] == "C" for e in carried)
    # The core step that states the release date reads the server's clock, and conversion.json records why it is class D.
    (source,) = [e for e in classes if e["execution_class"] == "D"]
    assert source["file"] == "cdm_source.sql" and not source["carried"] and source["failed_rules"] == ["functions"]
    assert source["recorded"]["class"] == "D" and "GETDATE" in source["recorded"]["reason"]
    text = release.script(CONVERSION)
    header = text.split("\n:on error exit")[0]
    assert "-- " + release.WORDING["classes"].format(version=policy.POLICY_VERSION, count=21, c=21, c_verb="are") in header
    assert "-- " + release.WORDING["class_justified"].format(layer="core", name="cdm_source.sql", grade="D") in header
    assert "-- " + source["recorded"]["reason"] in header
    for entry in carried:
        if entry["what"] == "step":
            assert text[text.index(f"-- {entry['file']}\n"):].splitlines()[1:3].count(
                "-- " + release.WORDING["class_step"].format(grade="C")) == 1, entry["file"]


def test_a_carried_step_of_class_d_is_refused_and_a_stale_record_of_a_class_too(tmp_path):
    folder = _folder(tmp_path, GOOD_STEP + "\nLEFT JOIN omop.source_to_concept_map m ON m.source_code = 'X'")
    with pytest.raises(Refused, match=r"step\.sql: the static policy places this step in class D .* the rule joins"):
        release.script(folder)
    copy = tmp_path / "copy" / "conversion"
    shutil.copytree(CONVERSION, copy)
    steps = json.loads((copy / "conversion.json").read_text())
    source = next(s for s in steps if s["file"] == "cdm_source.sql")
    source["policy_class"]["class"] = "C"
    (copy / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(Refused, match="cdm_source.sql: conversion.json records the class C for this step, and the static policy now derives the class D"):
        release.script(copy)
    source["policy_class"] = {"class": "D"}
    (copy / "conversion.json").write_text(json.dumps(steps))
    with pytest.raises(Refused, match="cdm_source.sql: policy_class is"):
        release.script(copy)


def test_the_command_writes_the_class_of_every_step_beside_the_script(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["release", str(CONVERSION), "--catalogue", str(FIXTURES / "invented-catalogue.csv"),
                                      "--out", str(tmp_path / "out"), "--schema", str(FIXTURES / "map")])
    assert release.main() == 0
    written = json.loads((tmp_path / "out" / "step_classes.json").read_text())
    assert {e["file"]: e["execution_class"] for e in written}["drug_exposure_infusion_roles.sql"] == "C"
    assert all(set(e["report"]) >= {"policy_version", "rules", "execution_class"} for e in written)