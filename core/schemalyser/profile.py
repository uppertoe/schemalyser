"""The core profile: what the anaesthesia layer needs to know about a core OMOP database it cannot see.

The anaesthesia layer adds rows beside a core OMOP database that a central team maintains, and
nothing is known about that core except what its team chooses to report. This module writes one
T-SQL script for that team to run, in the layout of the profiling scripts they already use: one
global temporary table of header and data rows, and one result set to save as CSV. The script
returns only names from the CDM 5.4 field list, the names of the source keys that the anaesthesia
steps join on, data types, and counts rounded down to the nearest ten. It returns no row of data
and no source value.

    python -m schemalyser.profile CONVERSION --out FILE [--source-prefix P] [--omop-schema S]
    python -m schemalyser.profile --read OUTPUT.csv CONVERSION

The second form reads the saved result, checks every row again, and prints a summary that is safe
to show an outsider and the findings for the register of guesses. The reader also accepts the
output of the central team's general database profile, and takes from it what it can answer.

The module also gives the same questions in a plain form, profile.plain: a short list of single SELECTs in
two tiers, which the checklist offers one at a time beside the items they answer. Tier one reads only the
server's own records; tier two reads data, bounded by construction, once tier one has given the sizes.
"""
import argparse
import csv
import io
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from .extract import _passed_through, decode
from .translate import OMOP_SCHEMA

FIELDS = Path(__file__).parent / "omop" / "cdm54_fields.csv"
VERSION = "1"
TABLE = "##SCHEMALYSER_CORE_PROFILE"
LAYOUT = ("ITEM_CATEGORY", "VALUE_01", "VALUE_02", "VALUE_03", "VALUE_04", "VALUE_05")
MINIMUM_COUNT = 10
MATCH_THRESHOLD = 95
SCHEMA_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PREFIX_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*\.){1,3}")
NAME_PATTERN = SCHEMA_PATTERN
VERSION_PATTERN = re.compile(r"[A-Za-z0-9 ._-]{0,30}")
CAST_PATTERN = re.compile(r"(N?VARCHAR|N?CHAR)\((\d{1,4}|MAX)\)|INT|BIGINT", re.IGNORECASE)
SQL_TYPES = {"bigint", "int", "smallint", "tinyint", "bit", "decimal", "numeric", "float", "real", "money",
             "smallmoney", "date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time", "char",
             "varchar", "nchar", "nvarchar", "text", "ntext", "binary", "varbinary", "image", "uniqueidentifier",
             "xml", "sql_variant", "timestamp", "geography", "geometry", "hierarchyid", "sysname"}
TYPE_PATTERN = re.compile(r"([a-z0-9_]+)(?:\((\d{1,4}|max)(?:,\d{1,2})?\))?")
# The settings that a profile reads. Any other setting that release.json holds is left alone.
SETTINGS = {"omop_schema": "dbo", "source_prefix": None}
KNOWN_SETTINGS = {"omop_schema", "anaesthesia_schema", "published_schema", "source_prefix", "identifier_type"}

# Each category of row, with its sort order and the header row that names its values.
CATEGORIES = {
    "PROFILE": (1, ("PROFILE_VERSION", "DATE_PROFILED", "OMOP_SCHEMA", "SOURCE_COMPARED")),
    "CDM_SOURCE": (2, ("CDM_VERSION", "VOCABULARY_VERSION", "CDM_VERSION_CONCEPT_ID", "ROW_COUNT")),
    "CDM_TABLE": (3, ("TABLE_NAME", "PRESENT", "ROW_COUNT_ROUNDED", "KEY_TYPE", "KEY_DIGITS")),
    "LOCAL_OBJECTS": (4, ("TABLES_AND_VIEWS_NOT_IN_CDM",)),
    "CDM_FIELD_ABSENT": (5, ("TABLE_NAME", "FIELD_NAME")),
    "CDM_FIELD_TYPE": (6, ("TABLE_NAME", "FIELD_NAME", "CORE_TYPE", "CDM_54_TYPE")),
    "CDM_FIELD_LOCAL": (7, ("TABLE_NAME", "LOCAL_FIELD_COUNT")),
    "TYPE_CONCEPT": (8, ("TABLE_NAME", "FIELD_NAME", "TYPE_CONCEPT_ID", "ROW_COUNT_ROUNDED")),
    "SOURCE_VALUE_SHAPE": (9, ("CORE_FIELD", "NON_EMPTY_ROUNDED", "ALL_DIGITS_PERCENT", "MIN_LENGTH", "MAX_LENGTH")),
    "SOURCE_KEY_MATCH": (10, ("CORE_FIELD", "SOURCE_KEY", "DISTINCT_KEYS_ROUNDED", "MATCHED_ROUNDED", "MATCHED_PERCENT")),
    "OBSERVATION_PERIOD": (11, ("PERSONS_ROUNDED", "PERSONS_WITH_PERIOD_ROUNDED")),
    "ERROR": (12, ("SECTION", "OBJECT", "ERROR_NUMBER")),
}
# The categories that only the plain form writes, which the whole script never writes, so its header rows
# stay as they were. A match measured from the core side, on a sample of the core table, is one of them.
PLAIN_CATEGORIES = {
    "SOURCE_KEY_MATCH_SAMPLED": (10, ("CORE_FIELD", "SOURCE_KEY", "CORE_VALUES_SAMPLED_ROUNDED", "FOUND_ROUNDED",
                                      "FOUND_PERCENT")),
}
ALL_CATEGORIES = {**CATEGORIES, **PLAIN_CATEGORIES}
# The categories of the central team's general database profile. Only TABLE_COLUMNS and TABLES are
# read; the others, such as the database name, the schemas and the indexes, are passed over.
GENERAL_ALL = {"DATABASE", "SCHEMA", "FUNCTION", "PROCEDURE", "MISC_ITEMS", "USER_TABLE", "INDEX",
               "FOREIGN_KEY", "VIEWS", "VIEW", "TABLE_COLIMNS", "TABLE_COLUMNS", "TABLES", "SQL_SCALAR_FUNCTION",
               "SQL_STORED_PROCEDURE", "SQL_TABLE_VALUED_FUNCTION", "SQL_INLINE_TABLE_VALUED_FUNCTION"}
# A value that a spreadsheet would read as a formula is never accepted.
FORMULA_STARTS = ("=", "+", "@", "\t", "\r", "-")

# The wording that the central team and the clinical lead read: the comments in the script, the
# summary and the findings. It is a draft until the clinical lead approves it.
WORDING = {
    "header": [
        "Core profile for the anaesthesia layer, version {version}.",
        "This script reports the shape of the core OMOP database, so that the anaesthesia layer can be checked against it before the layer is run.",
        "This script returns only the names of OMOP tables and fields, the names of the source keys that the anaesthesia layer joins on, data types, and counts rounded down to the nearest ten. It returns no row of data and no source value.",
        "This script reads the core tables and the source tables and changes neither. It writes only to one temporary table, which it removes when it has finished.",
        "To run it, connect to the OMOP database, press F5 to run the whole script, and save the single result set as a CSV file.",
        "If a table or field that the script asks about does not exist, the script records an error row for that question and carries on with the rest.",
    ],
    "omop_schema": "This script reads the core OMOP tables in the schema {schema}.",
    "source_prefix": "This script reads each source table by writing the prefix {prefix} before its name.",
    "no_source_prefix": "This script has no source prefix, so it does not compare the source keys with the core.",
    "profile": "Section 1: the script records its own version, the date, and the settings it was written with.",
    "cdm_source": "Section 2: the script reports the CDM version and the vocabulary version that CDM_SOURCE records.",
    "tables": ("Section 3: for each table of CDM 5.4, the script reports whether the table exists, its number of rows "
               "rounded down to ten, the data type of its primary key, and the number of digits in the highest key. "
               "The script does not report the key itself."),
    "local_objects": "Section 4: the script counts the tables and views in the OMOP schema that are not part of CDM 5.4. It does not name them.",
    "fields": ("Section 5: the script lists the CDM 5.4 fields that the core tables do not have and the fields whose data "
               "type differs from CDM 5.4, and counts the local fields in each table without naming them."),
    "type_concepts": ("Section 6: for each table that the anaesthesia layer adds rows to, the script counts the rows by "
                      "type concept. The script leaves out any type concept that fewer than ten rows hold."),
    "shapes": ("Section 7: for each source value field that the anaesthesia layer joins on, the script reports how many "
               "values the field holds, the percentage made only of digits, and the shortest and longest length. "
               "The script shows no value."),
    "matches": ("Section 8: for each join between a source key and a core source value field, the script counts the "
                "distinct source keys and how many of them the core holds. This match rate is the most important "
                "figure in the profile."),
    "observation_period": "Section 9: the script counts the people in PERSON and the people who have at least one observation period.",
    "results": "The script returns its findings as one result set. Please save the result as a CSV file and return it unchanged.",
    # The summary.
    "summary_source": "This profile came from the core profile script, version {version}.",
    "summary_general": "This profile came from the central team's general database profile, which gives approximate row counts and no versions.",
    "summary_version": "The core records CDM version {cdm} and vocabulary version {vocabulary} in CDM_SOURCE.",
    "summary_no_version": "The profile does not record a CDM version for the core.",
    "summary_tables": "The core holds {present} of the {total} tables of CDM 5.4. The number of other tables or views in the same schema is {local}.",
    "summary_table": "{table} holds about {rows} rows, and its primary key is of type {kind}{digits}.",
    "summary_table_no_key": "{table} holds about {rows} rows.",
    "summary_digits": ", with {digits} digits in the highest key",
    "summary_absent": "{table} does not have these CDM 5.4 fields: {fields}.",
    "summary_type": "{table}.{field} is of type {core}, where CDM 5.4 defines {cdm}.",
    "summary_match": "For {joined}, the core holds about {matched} of about {keys} distinct source keys ({percent} per cent).",
    "summary_match_unknown": "For {joined}, the profile does not give a match rate.",
    "summary_join": "the join to {field} in {steps}",
    "summary_join_numbered": "source key {number} joined to {field}",
    "summary_periods": "PERSON holds about {persons} people, and about {with_period} of them have an observation period.",
    "summary_errors": "The script could not answer {count} of its questions.",
    # The findings for the register of guesses.
    "version_question": "We assumed that the core follows CDM 5.4.",
    "version_finding": "The core records CDM version {version} in CDM_SOURCE.",
    "version_fields": ("The anaesthesia steps use {fields}, which the core tables do not have. The published views and the "
                       "steps need these fields, so the steps should be adjusted before the release script is run."),
    "version_no_fields": ("The core tables hold every field that the anaesthesia steps use. The steps can stay as they "
                          "are, but you should confirm the version with the central team."),
    "absent_question": "We assumed that the core tables hold every field of CDM 5.4.",
    "absent_finding": "{table} does not have {fields}, which the anaesthesia layer needs.",
    "absent_suggestion": ("The published view for {table} reads every CDM 5.4 field, so it cannot be created until the "
                          "steps or the view leave these fields out."),
    "bigint_question": "We assumed that the core's identifiers are of type INT.",
    "bigint_finding": "The primary keys of {tables} are of type BIGINT.",
    "bigint_suggestion": "Set identifier_type to BIGINT in release.json, so that the anaesthesia tables match the core.",
    "headroom_question": "We assumed that the core's identifiers leave room for the anaesthesia rows.",
    "headroom_finding": "The highest key in {table} has {digits} digits, and the key is of type INT, which holds at most ten.",
    "headroom_suggestion": "Discuss the numbering with the central team before the release, and consider setting identifier_type to BIGINT.",
    "missing_question": "We assumed that the core holds every table that the anaesthesia layer reads or adds to.",
    "missing_finding": "The core does not have {tables}.",
    "missing_suggestion": "The release script cannot run until these tables exist, so you can raise them with the central team.",
    "visit_detail_question": "We assumed that the core does not populate VISIT_DETAIL.",
    "visit_detail_finding": "The core's VISIT_DETAIL holds about {rows} rows.",
    "visit_detail_suggestion": ("The anaesthesia steps number their visit details on from the core's highest key. If the "
                                "core already records time in theatre, you should agree with the central team which of "
                                "the two is kept."),
    "duplicate_question": "We assumed that the core does not already hold the rows that the anaesthesia layer adds to {table}.",
    "duplicate_finding": "The core's {table} already holds about {rows} rows with the type concept {concept}, which the anaesthesia layer also writes in {steps}.",
    "duplicate_suggestion": ("The type concept alone cannot tell the core's rows from the layer's. If the core already "
                             "loads anaesthesia records into {table}, the layer would count them twice, so you should "
                             "compare the two with the central team."),
    "match_question": "We assumed that the core keeps the source system's keys as text in {field}.",
    "match_finding": "For the join in {steps}, the core holds about {matched} of about {keys} distinct source keys ({percent} per cent).",
    "match_suggestion": ("The guess about {field} does not hold for this join. If the core adds a prefix or keeps a "
                         "different key, change the join in {steps} to match, and run the profile again."),
    "match_empty_finding": "The source table for the join in {steps} held no keys when the profile was run.",
    "match_empty_suggestion": "Run the profile again once the source table holds rows, so that the match rate can be measured.",
    "shape_finding": ("Only {percent} per cent of the values in {field} are made only of digits, while the join in "
                      "{steps} compares them with source keys converted to text."),
    "shape_suggestion": "Supply the source prefix and run the profile again, so that the match rate can be measured directly.",
    "collation_finding": "SQL Server could not compare {field} with the source key in {steps}, because the two use different collations.",
    "collation_suggestion": "Add a COLLATE clause to the join in {steps}, then run the profile again.",
    "periods_question": "We assumed that every person in the core has an observation period.",
    "periods_finding": "PERSON holds about {persons} people, and only about {with_period} of them have an observation period.",
    "periods_suggestion": ("The quality gate on observation periods will fail for anaesthetics of people without one. "
                           "You can raise this with the central team."),
    "errors_question": "We assumed that the profile could answer every question it asked.",
    "errors_finding": "The script could not answer {count} questions, in these sections: {sections}.",
    "errors_suggestion": "Most of these errors mean that a table or field does not exist. You can check the error numbers in the result file.",
}


class ProfileError(ValueError):
    """A setting, a name or a profile result does not have the expected form."""


def _bracket(name):
    """A name inside square brackets. Every name is validated first; this is a second defence."""
    return "[" + name.replace("]", "]]") + "]"


def _text(value):
    """A value inside single quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def cdm_fields():
    """The CDM 5.4 field list: table -> [row], in the published order."""
    with open(FIELDS, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {table: [r for r in rows if r["table"] == table] for table in dict.fromkeys(r["table"] for r in rows)}


def _keys(fields):
    return {table: next((r for r in rows if r["primary_key"] == "Y"), None) for table, rows in fields.items()}


def _type_fields(fields):
    """The type concept field of each table, where the table has one."""
    found = {}
    for table, rows in fields.items():
        for row in rows:
            if row["field"].endswith("_type_concept_id") and row["concept_domain"] == "Type Concept":
                found.setdefault(table, row["field"])
    return found


# Reading the conversion.

FILE_NAME = re.compile(r"[A-Za-z0-9_.-]+")

def _anaesthesia_steps(folder):
    folder = Path(folder)
    steps = json.loads((folder / "conversion.json").read_text())
    # A step's file name is checked as convert.py checks it, so that no step can be read from outside the folder.
    for step in steps:
        name = step.get("file") if isinstance(step, dict) else None
        if not isinstance(name, str) or not FILE_NAME.fullmatch(name) or set(name) == {"."}:
            raise ProfileError(f"{name!r}: a step's file name may hold only letters, digits, full stops, hyphens and underscores")
    return [(step, decode((folder / step["file"]).read_bytes())) for step in steps if step.get("layer") == "anaesthesia"]


def _source(select, column, depth=0):
    """The source table behind a column of a SELECT, looking through a derived table, as (table, column)."""
    if depth > 5:
        return None
    for table in select.find_all(exp.Table):
        if table.alias_or_name.upper() == column.table.upper():
            return None if (table.db or "").upper() == OMOP_SCHEMA.upper() else (table.name, column.name)
    for subquery in select.find_all(exp.Subquery):
        if subquery.alias.upper() == column.table.upper():
            inner = subquery.find(exp.Select)
            for projection in inner.expressions if inner else []:
                carried = _passed_through(projection.unalias())
                if projection.alias_or_name.upper() == column.name.upper() and carried is not None and carried.table:
                    return _source(inner, carried, depth + 1)
    return None


def _cast(node):
    """The type that a side of a join is cast to, in T-SQL, if it is a plain cast to text or a whole number."""
    if isinstance(node, (exp.Cast, exp.TryCast)):
        kind = node.args["to"].sql(dialect="tsql").replace(" ", "")
        if CAST_PATTERN.fullmatch(kind):
            return kind.upper()
    return None


def joined_pairs(tree):
    """The joins between a core source value field and a source column, as (core table, field, source table, column, cast).

    Each pair is an equality between a column of an omop.<table> alias whose name ends in
    _source_value and a column that leads back to a source table, looking through a cast.
    """
    found = []
    for select in tree.find_all(exp.Select):
        core = {t.alias_or_name.upper(): t.name.lower() for t in select.find_all(exp.Table)
                if (t.db or "").upper() == OMOP_SCHEMA.upper() and t.name.lower() != "source_to_concept_map"}
        for comparison in select.find_all(exp.EQ):
            raw = (comparison.this, comparison.expression)
            sides = [_passed_through(side) for side in raw]
            if any(side is None or not side.table for side in sides):
                continue
            for (field, other), other_raw in ((sides, raw[1]), (sides[::-1], raw[0])):
                table = core.get(field.table.upper())
                if table and field.name.lower().endswith("_source_value"):
                    origin = _source(select, other)
                    if origin:
                        pair = (table, field.name.lower(), origin[0], origin[1], _cast(other_raw))
                        if pair not in found:
                            found.append(pair)
    return found


def conversion_facts(folder):
    """What the profile needs from a conversion: the tables the layer adds to, the joins, the type concepts and the fields used."""
    fields = cdm_fields()
    steps = _anaesthesia_steps(folder)
    written = list(dict.fromkeys(step["table"].lower() for step, _ in steps))
    pairs, types, used, reads, uses = {}, {}, {}, set(), {}
    type_fields = _type_fields(fields)
    for step, sql in steps:
        table = step["table"].lower()
        try:
            tree = sqlglot.parse_one(sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            raise ProfileError(f"{step['file']} could not be read")
        for core_table, field, source_table, source_column, cast in joined_pairs(tree):
            # A join to a table that the layer writes finds the layer's own rows, so it says nothing about the core.
            if core_table in written:
                continue
            if not all(NAME_PATTERN.fullmatch(name) for name in (source_table, source_column)):
                raise ProfileError(f"{step['file']} joins on a name that is not a plain name")
            if not any(r["field"] == field for r in fields.get(core_table, [])):
                continue
            key = (core_table, field, source_table, source_column)
            entry = pairs.setdefault(key, {"cast": cast, "steps": []})
            entry["steps"].append(step["file"])
        if isinstance(tree, exp.Select):
            for projection in tree.expressions:
                name = projection.alias_or_name.lower()
                used.setdefault(table, set()).add(name)
                uses.setdefault((table, name), []).append(step["file"])
                value = projection.unalias()
                if name == type_fields.get(table) and isinstance(value, exp.Literal) and value.is_int:
                    types.setdefault((table, int(value.this)), []).append(step["file"])
        for select in tree.find_all(exp.Select):
            aliases = {t.alias_or_name.upper(): t.name.lower() for t in select.find_all(exp.Table)
                       if (t.db or "").upper() == OMOP_SCHEMA.upper()}
            for column in select.find_all(exp.Column):
                if column.table and column.table.upper() in aliases:
                    read = (aliases[column.table.upper()], column.name.lower())
                    reads.add(read)
                    if step["file"] not in uses.setdefault(read, []):
                        uses[read].append(step["file"])
    return {"written": written, "pairs": pairs, "types": types, "used": used, "reads": reads, "uses": uses,
            "files": [step["file"] for step, _ in steps], "writers": [(step["file"], step["table"].lower()) for step, _ in steps]}


# Writing the script.

def _settings(folder, settings):
    chosen = dict(SETTINGS)
    release = Path(folder) / "release.json"
    if release.exists():
        found = json.loads(release.read_text())
        chosen.update({key: found[key] for key in SETTINGS if key in found})
    for key in settings or {}:
        if key not in KNOWN_SETTINGS:
            raise ProfileError(f"the setting {key} is not known")
    chosen.update({key: value for key, value in (settings or {}).items() if key in SETTINGS})
    if not isinstance(chosen["omop_schema"], str) or not SCHEMA_PATTERN.fullmatch(chosen["omop_schema"]):
        raise ProfileError("omop_schema must be a plain name")
    prefix = chosen["source_prefix"]
    if prefix is not None and (not isinstance(prefix, str) or not PREFIX_PATTERN.fullmatch(prefix)):
        raise ProfileError("source_prefix must be one to three plain names, each followed by a full stop")
    return chosen


def _guarded(statement, section, subject):
    """One statement run through sp_executesql, so that a missing table or column records an error row and the script carries on."""
    order = CATEGORIES["ERROR"][0]
    return ["BEGIN TRY",
            f"    EXEC sys.sp_executesql N{_text(statement)};",
            "END TRY BEGIN CATCH",
            f"    INSERT INTO {TABLE} (SORT_ORDER, ITEM_CATEGORY, ROW_TYPE, VALUE_01, VALUE_02, VALUE_03)",
            f"    VALUES ({order}, 'ERROR', 'DATA', {_text(section)}, {_text(subject)}, CAST(ERROR_NUMBER() AS varchar(20)));",
            "END CATCH;"]


def _insert(category, values):
    """The start of an INSERT of one data row of a category, naming as many value columns as there are values."""
    names = ", ".join(f"VALUE_{n:02d}" for n in range(1, values + 1))
    return f"INSERT INTO {TABLE} (SORT_ORDER, ITEM_CATEGORY, ROW_TYPE, {names}) SELECT {CATEGORIES[category][0]}, '{category}', 'DATA', "


def _rounded(expression):
    return f"CAST(({expression} / 10) * 10 AS varchar(30))"


def _field_values(fields, tables):
    """The CDM field list as a table value constructor: (t, f, kind, size, cdm)."""
    rows = []
    for table in tables:
        for row in fields[table]:
            datatype = row["datatype"]
            length = re.fullmatch(r"varchar\((\d+|max)\)", datatype)
            if length:
                kind, size = "varchar", ("-1" if length.group(1) == "max" else length.group(1))
            else:
                kind, size = {"integer": "int"}.get(datatype, datatype), "NULL"
            rows.append(f"({_text(table)}, {_text(row['field'])}, {_text(kind)}, {size}, {_text(datatype)})")
    return "(VALUES " + ", ".join(rows) + ") AS f(t, f, kind, size, cdm)"


def script(conversion_folder, settings=None):
    """The core profile script for a conversion folder, as T-SQL text."""
    chosen = _settings(conversion_folder, settings)
    facts = conversion_facts(conversion_folder)
    fields = cdm_fields()
    keys = _keys(fields)
    type_fields = _type_fields(fields)
    schema = chosen["omop_schema"]
    prefix = chosen["source_prefix"]
    s = _bracket(schema)

    def core(table):
        return f"{s}.{_bracket(table)}"

    lines = [f"-- {line.format(version=VERSION)}" for line in WORDING["header"]]
    lines += [f"-- {WORDING['omop_schema'].format(schema=schema)}",
              f"-- {WORDING['source_prefix'].format(prefix=prefix) if prefix else WORDING['no_source_prefix']}",
              "", "SET NOCOUNT ON;", "", f"DROP TABLE IF EXISTS {TABLE};", "",
              f"CREATE TABLE {TABLE} (",
              "\tSORT_ORDER      INTEGER,", "\tITEM_CATEGORY   VARCHAR(50),", "\tROW_TYPE        VARCHAR(20),",
              "\tVALUE_01        VARCHAR(200),", "\tVALUE_02        VARCHAR(200),", "\tVALUE_03        VARCHAR(200),",
              "\tVALUE_04        VARCHAR(200),", "\tVALUE_05        VARCHAR(200)", "\t);", ""]
    lines.append("-- HEADER rows")
    for category, (order, header) in CATEGORIES.items():
        names = ", ".join(f"VALUE_{n:02d}" for n in range(1, len(header) + 1))
        lines.append(f"INSERT INTO {TABLE} (SORT_ORDER, ITEM_CATEGORY, ROW_TYPE, {names}) "
                     f"VALUES ({order}, '{category}', 'HEADER', {', '.join(_text(h) for h in header)});")

    lines += ["", f"-- {WORDING['profile']}",
              _insert("PROFILE", 4) + f"{_text(VERSION)}, CONVERT(varchar(10), GETDATE(), 23), "
              f"{_text(schema)}, {_text('Y' if prefix else 'N')};"]

    # The versions are returned only when they are short and made of letters, digits and simple marks.
    def safe(column, length):
        return f"CASE WHEN LEN({column}) <= {length} AND {column} NOT LIKE '%[^A-Za-z0-9 ._-]%' THEN {column} END"

    lines += ["", f"-- {WORDING['cdm_source']}"]
    lines += _guarded(_insert("CDM_SOURCE", 4) + f"(SELECT TOP (1) {safe('[cdm_version]', 20)} FROM {core('cdm_source')}), "
                      f"(SELECT TOP (1) {safe('[vocabulary_version]', 30)} FROM {core('cdm_source')}), NULL, "
                      f"(SELECT CAST(COUNT_BIG(*) AS varchar(30)) FROM {core('cdm_source')});", "CDM_SOURCE", "cdm_source")
    lines += _guarded(f"UPDATE {TABLE} SET VALUE_03 = (SELECT TOP (1) CAST([cdm_version_concept_id] AS varchar(20)) "
                      f"FROM {core('cdm_source')}) WHERE ITEM_CATEGORY = 'CDM_SOURCE' AND ROW_TYPE = 'DATA';",
                      "CDM_SOURCE", "cdm_source.cdm_version_concept_id")

    lines += ["", f"-- {WORDING['tables']}"]
    for table in fields:
        key = keys[table]
        object_id = f"OBJECT_ID(N{_text(core(table))})"
        rows = (f"CASE WHEN OBJECTPROPERTY({object_id}, 'IsUserTable') = 1 "
                f"THEN (SELECT COALESCE(SUM(p.rows), 0) FROM sys.partitions AS p WHERE p.object_id = {object_id} AND p.index_id IN (0, 1)) "
                f"ELSE (SELECT COUNT_BIG(*) FROM {core(table)}) END")
        kind = (f"(SELECT TYPE_NAME(c.system_type_id) FROM sys.columns AS c WHERE c.object_id = {object_id} "
                f"AND c.name = {_text(key['field'])})") if key else "NULL"
        lines += [f"IF OBJECT_ID(N{_text(core(table))}) IS NULL",
                  "    " + _insert("CDM_TABLE", 2) + f"{_text(table)}, 'N';",
                  "ELSE BEGIN"]
        lines += ["    " + line for line in _guarded(
            _insert("CDM_TABLE", 4) + f"{_text(table)}, 'Y', {_rounded(rows)}, {kind};", "CDM_TABLE", table)]
        if key and key["datatype"] == "integer":
            digits = f"CAST(LEN(REPLACE(CAST(MAX({_bracket(key['field'])}) AS varchar(40)), '-', '')) AS varchar(10))"
            lines += ["    " + line for line in _guarded(
                f"UPDATE {TABLE} SET VALUE_05 = (SELECT {digits} FROM {core(table)}) "
                f"WHERE ITEM_CATEGORY = 'CDM_TABLE' AND ROW_TYPE = 'DATA' AND VALUE_01 = {_text(table)};",
                "CDM_TABLE", f"{table}.{key['field']}")]
        lines.append("END;")

    in_schema = f"LOWER(x.TABLE_SCHEMA) = LOWER({_text(schema)})"
    table_list = "(VALUES " + ", ".join(f"({_text(t)})" for t in fields) + ") AS cdm(t)"
    lines += ["", f"-- {WORDING['local_objects']}"]
    lines += _guarded(_insert("LOCAL_OBJECTS", 1) + f"CAST(COUNT(*) AS varchar(10)) FROM INFORMATION_SCHEMA.TABLES AS x "
                      f"WHERE {in_schema} AND NOT EXISTS (SELECT 1 FROM {table_list} WHERE cdm.t = LOWER(x.TABLE_NAME));",
                      "LOCAL_OBJECTS", schema)

    # The three questions about fields share one copy of the field list, so they are asked in one statement.
    core_type = ("c.DATA_TYPE + CASE WHEN c.CHARACTER_MAXIMUM_LENGTH = -1 THEN '(max)' "
                 "WHEN c.CHARACTER_MAXIMUM_LENGTH IS NOT NULL THEN '(' + CAST(c.CHARACTER_MAXIMUM_LENGTH AS varchar(10)) + ')' ELSE '' END")
    matches = ("(f.kind = c.DATA_TYPE OR (f.kind = 'varchar' AND c.DATA_TYPE = 'nvarchar') "
               "OR (f.kind = 'datetime' AND c.DATA_TYPE = 'datetime2')) AND (f.size IS NULL OR f.size = c.CHARACTER_MAXIMUM_LENGTH)")
    names = "(SORT_ORDER, ITEM_CATEGORY, ROW_TYPE, VALUE_01, VALUE_02, VALUE_03, VALUE_04)"
    statement = (
        f"WITH f AS (SELECT * FROM {_field_values(fields, list(fields))}), "
        f"c AS (SELECT LOWER(x.TABLE_NAME) AS t, LOWER(x.COLUMN_NAME) AS f, x.DATA_TYPE, x.CHARACTER_MAXIMUM_LENGTH "
        f"FROM INFORMATION_SCHEMA.COLUMNS AS x WHERE {in_schema}) "
        f"INSERT INTO {TABLE} {names} "
        f"SELECT {CATEGORIES['CDM_FIELD_ABSENT'][0]}, 'CDM_FIELD_ABSENT', 'DATA', f.t, f.f, NULL, NULL FROM f "
        f"WHERE EXISTS (SELECT 1 FROM c WHERE c.t = f.t) AND NOT EXISTS (SELECT 1 FROM c WHERE c.t = f.t AND c.f = f.f) "
        f"UNION ALL SELECT {CATEGORIES['CDM_FIELD_TYPE'][0]}, 'CDM_FIELD_TYPE', 'DATA', f.t, f.f, LOWER({core_type}), f.cdm "
        f"FROM f JOIN c ON c.t = f.t AND c.f = f.f WHERE NOT ({matches}) "
        f"UNION ALL SELECT {CATEGORIES['CDM_FIELD_LOCAL'][0]}, 'CDM_FIELD_LOCAL', 'DATA', c.t, CAST(COUNT(*) AS varchar(10)), NULL, NULL "
        f"FROM c WHERE EXISTS (SELECT 1 FROM f WHERE f.t = c.t) AND NOT EXISTS (SELECT 1 FROM f WHERE f.t = c.t AND f.f = c.f) "
        f"GROUP BY c.t;")
    lines += ["", f"-- {WORDING['fields']}"]
    lines += _guarded(statement, "CDM_FIELD_TYPE", schema)

    lines += ["", f"-- {WORDING['type_concepts']}"]
    for table in facts["written"]:
        field = type_fields.get(table)
        if not field:
            continue
        f = _bracket(field)
        lines += _guarded(_insert("TYPE_CONCEPT", 4) + f"{_text(table)}, {_text(field)}, CAST(g.k AS varchar(20)), {_rounded('g.n')} "
                          f"FROM (SELECT {f} AS k, COUNT_BIG(*) AS n FROM {core(table)} GROUP BY {f} "
                          f"HAVING COUNT_BIG(*) >= {MINIMUM_COUNT}) AS g;", "TYPE_CONCEPT", f"{table}.{field}")

    lines += ["", f"-- {WORDING['shapes']}"]
    for table, field in dict.fromkeys((t, f) for t, f, _, _ in facts["pairs"]):
        value = f"CAST({_bracket(field)} AS nvarchar(4000))"
        lines += _guarded(
            _insert("SOURCE_VALUE_SHAPE", 5) + f"{_text(f'{table}.{field}')}, {_rounded('s.n')}, "
            f"CASE WHEN s.n >= {MINIMUM_COUNT} THEN CAST(s.digits * 100 / s.n AS varchar(10)) END, "
            f"CASE WHEN s.n >= {MINIMUM_COUNT} THEN CAST(s.lo AS varchar(10)) END, "
            f"CASE WHEN s.n >= {MINIMUM_COUNT} THEN CAST(s.hi AS varchar(10)) END "
            f"FROM (SELECT COUNT_BIG(*) AS n, SUM(CASE WHEN v.v NOT LIKE N'%[^0-9]%' THEN CAST(1 AS bigint) ELSE 0 END) AS digits, "
            f"MIN(LEN(v.v)) AS lo, MAX(LEN(v.v)) AS hi FROM (SELECT {value} AS v FROM {core(table)} "
            f"WHERE {_bracket(field)} IS NOT NULL AND LTRIM(RTRIM({value})) <> N'') AS v) AS s;",
            "SOURCE_VALUE_SHAPE", f"{table}.{field}")

    lines += ["", f"-- {WORDING['matches']}"]
    if prefix:
        reach = "".join(_bracket(part) + "." for part in prefix.rstrip(".").split("."))
        for (table, field, source_table, source_column), entry in facts["pairs"].items():
            key = f"s.{_bracket(source_column)}"
            cast = f"CAST({key} AS {entry['cast']})" if entry["cast"] else key
            lines += _guarded(
                _insert("SOURCE_KEY_MATCH", 5) + f"{_text(f'{table}.{field}')}, {_text(f'{source_table}.{source_column}')}, "
                f"{_rounded('k.n')}, {_rounded('k.m')}, CASE WHEN k.n >= {MINIMUM_COUNT} THEN CAST(k.m * 100 / k.n AS varchar(10)) END "
                f"FROM (SELECT COUNT_BIG(*) AS n, SUM(CASE WHEN m.hit IS NULL THEN CAST(0 AS bigint) ELSE 1 END) AS m "
                f"FROM (SELECT DISTINCT {cast} AS k FROM {reach}{_bracket(source_table)} AS s WHERE {key} IS NOT NULL) AS d "
                f"OUTER APPLY (SELECT TOP (1) 1 AS hit FROM {core(table)} AS c WHERE c.{_bracket(field)} = d.k) AS m) AS k;",
                "SOURCE_KEY_MATCH", f"{table}.{field}={source_table}.{source_column}")

    lines += ["", f"-- {WORDING['observation_period']}"]
    person, period = core("person"), core("observation_period")
    persons = f"(SELECT COUNT_BIG(*) FROM {person})"
    covered = (f"(SELECT COUNT_BIG(*) FROM {person} AS p WHERE EXISTS "
               f"(SELECT 1 FROM {period} AS o WHERE o.[person_id] = p.[person_id]))")
    lines += _guarded(_insert("OBSERVATION_PERIOD", 2) + f"{_rounded(persons)}, {_rounded(covered)};",
                      "OBSERVATION_PERIOD", "observation_period")

    lines += ["", f"-- {WORDING['results']}",
              "SELECT", "\tdp.ITEM_CATEGORY,"]
    lines += [f"\tCOALESCE(dp.VALUE_{n:02d}, '') AS VALUE_{n:02d}{',' if n < 5 else ''}" for n in range(1, 6)]
    lines += [f"FROM {TABLE} dp", "ORDER BY", "\tdp.SORT_ORDER,", "\tdp.ROW_TYPE DESC,",
              "\tdp.VALUE_01,", "\tdp.VALUE_02,", "\tdp.VALUE_03,", "\tdp.VALUE_04,", "\tdp.VALUE_05;",
              "", f"DROP TABLE IF EXISTS {TABLE};", ""]
    return "\n".join(lines)


# The plain form.
#
# The same questions, as a short list of plain queries that the central team can read in seconds and run one
# at a time, each beside the checklist item that it answers. Each is one SELECT with no variable, no dynamic
# SQL, no setting, no temporary table and no error handling, and returns rows in LAYOUT, so that read
# accepts its result. Tier one reads only the server's own records and no table. Tier two reads data, and
# each query is bounded by construction; none is offered until tier one has given the size of its table.

# The most distinct source keys that a match query takes from its source table.
PLAIN_KEY_SAMPLE = 10_000
PLAIN_WIDTH = 100
PLAIN_WORDING = {
    "tier_one": "This query reads from SQL Server's own records whether the core holds each table below, its number "
                "of rows rounded down to ten, the type of its primary key, and which of the fields below it lacks or "
                "holds with another type. It reads no table and returns no row of data.",
    "key": "This query reads the highest key of {table} and returns only its number of digits.",
    "cdm_source": "This query returns the CDM version and the vocabulary version that cdm_source records, and its number of rows.",
    "type_concepts": "This query counts the rows of {table} by {field}, rounded down to ten, and leaves out any type "
                     "concept that fewer than ten rows hold.",
    "type_sampled": "Because {table} is large, it reads about {percent} per cent of its pages, and its counts are estimates scaled up from that sample.",
    "match": "This query takes up to {sample} distinct keys of {source} and counts how many of them {core} holds, "
             "rounded down to ten. It returns no key and no value.",
    "match_sampled": "Because {source_table} is large, it takes the keys from about {percent} per cent of its pages.",
    "match_cost": "It looks each key up in {table}, which holds about {rows} rows.",
    "match_core": "Because {table} is large, this query measures the join from the core side. It reads about {percent} "
                  "per cent of the pages of {table}, takes up to {sample} distinct values of {core} from them, and counts "
                  "how many of them are keys in {home}, rounded down to ten. The result is an estimate from a sample. "
                  "It returns no key and no value.",
    "count": "This query counts the rows of {table}, rounded down to ten. It reads the whole of {table}, because "
             "SQL Server keeps no record of its size.",
    "safe": "It only reads. WITH (NOLOCK) means that it takes no row locks, but it holds a schema lock while it runs, "
            "so it should not run during the nightly load.",
}


def _plain_comment(sentences):
    import textwrap
    return [f"-- {line}" for sentence in sentences for line in textwrap.wrap(sentence, PLAIN_WIDTH - 3)]


def _plain_select(values):
    """A SELECT list of the six columns of LAYOUT, under their own names, NULL where not given."""
    items = [f"{values[i] if i < len(values) else 'NULL'} AS {name}" for i, name in enumerate(LAYOUT)]
    lines = [f"SELECT {items[0]}"]
    for item in items[1:]:
        if len(lines[-1]) + len(item) + 2 > PLAIN_WIDTH:
            lines[-1] += ","
            lines.append("       " + item)
        else:
            lines[-1] += ", " + item
    return lines


def _core_name(schema, table):
    return f"{_bracket(schema)}.{_bracket(table)}"


def plain_tier_one(tables, fields_wanted, settings=None, conversion_folder=None):
    """Tier one: one query that reads only the server's own records, for a literal list of CDM tables and fields.

    tables is a list of CDM table names; fields_wanted a list of (table, field) whose presence and type it
    checks. It returns a PROFILE row, a CDM_TABLE row for every table, whether the core holds it or not, and a
    CDM_FIELD_ABSENT or CDM_FIELD_TYPE row for every listed field that the core lacks or holds with another type.
    """
    chosen = _settings(conversion_folder, settings) if conversion_folder else {**SETTINGS, **(settings or {})}
    schema, prefix = chosen["omop_schema"], chosen["source_prefix"]
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ProfileError("omop_schema must be a plain name")
    fields = cdm_fields()
    keys = _keys(fields)
    tables = [t for t in dict.fromkeys(tables) if t in fields]
    wanted = [(t, f) for t, f in dict.fromkeys(fields_wanted) if t in tables and any(r["field"] == f for r in fields[t])]
    if not tables:
        return ""
    lines = _plain_comment([PLAIN_WORDING["tier_one"]])
    lines += _plain_select(["'PROFILE'", _text(VERSION), "CONVERT(varchar(10), GETDATE(), 23)", _text(schema),
                            _text("Y" if prefix else "N")])
    lines += ["UNION ALL"]
    lines += _plain_select(["'CDM_TABLE'", "n.t", "CASE WHEN o.object_id IS NULL THEN 'N' ELSE 'Y' END",
                            "CAST((SUM(p.rows) / 10) * 10 AS varchar(30))", "MAX(TYPE_NAME(k.system_type_id))"])
    pairs = [f"({_text(t)}, {_text(keys[t]['field'] if keys.get(t) else '')})" for t in tables]
    for i, pair in enumerate(pairs):
        lines.append(("FROM (VALUES " if i == 0 else " " * 13) + pair
                     + ("," if i < len(pairs) - 1 else ") AS n (t, key_field)"))
    lines += [f"LEFT JOIN sys.objects AS o ON o.name = n.t AND o.schema_id = SCHEMA_ID({_text(schema)}) AND o.type IN ('U', 'V')",
              "LEFT JOIN sys.partitions AS p ON p.object_id = o.object_id AND p.index_id IN (0, 1)",
              "LEFT JOIN sys.columns AS k ON k.object_id = o.object_id AND k.name = n.key_field",
              "GROUP BY n.t, o.object_id"]
    if wanted:
        core_type = ("LOWER(c.DATA_TYPE + CASE WHEN c.CHARACTER_MAXIMUM_LENGTH = -1 THEN '(max)' "
                     "WHEN c.CHARACTER_MAXIMUM_LENGTH IS NOT NULL THEN '(' + CAST(c.CHARACTER_MAXIMUM_LENGTH AS varchar(10)) + ')' ELSE '' END)")
        lines += ["UNION ALL"]
        lines += _plain_select(["CASE WHEN c.COLUMN_NAME IS NULL THEN 'CDM_FIELD_ABSENT' ELSE 'CDM_FIELD_TYPE' END",
                                "f.t", "f.f", f"CASE WHEN c.COLUMN_NAME IS NOT NULL THEN {core_type} END",
                                "CASE WHEN c.COLUMN_NAME IS NOT NULL THEN f.cdm END"])
        rows = []
        for t, f in wanted:
            datatype = next(r["datatype"] for r in fields[t] if r["field"] == f)
            length = re.fullmatch(r"varchar\((\d+|max)\)", datatype)
            kind, size = (("varchar", "-1" if length.group(1) == "max" else length.group(1)) if length
                          else ({"integer": "int"}.get(datatype, datatype), "NULL"))
            rows.append(f"({_text(t)}, {_text(f)}, {_text(kind)}, {size}, {_text(datatype)})")
        for i, row in enumerate(rows):
            lines.append(("FROM (VALUES " if i == 0 else " " * 13) + row
                         + ("," if i < len(rows) - 1 else ") AS f (t, f, kind, size, cdm)"))
        matches = ("(f.kind = c.DATA_TYPE OR (f.kind = 'varchar' AND c.DATA_TYPE = 'nvarchar') "
                   "OR (f.kind = 'datetime' AND c.DATA_TYPE = 'datetime2')) AND (f.size IS NULL OR f.size = c.CHARACTER_MAXIMUM_LENGTH)")
        lines += ["JOIN INFORMATION_SCHEMA.TABLES AS x",
                  f"     ON LOWER(x.TABLE_NAME) = f.t AND LOWER(x.TABLE_SCHEMA) = LOWER({_text(schema)})",
                  "LEFT JOIN INFORMATION_SCHEMA.COLUMNS AS c",
                  "     ON c.TABLE_SCHEMA = x.TABLE_SCHEMA AND c.TABLE_NAME = x.TABLE_NAME AND LOWER(c.COLUMN_NAME) = f.f",
                  f"WHERE c.COLUMN_NAME IS NULL OR NOT ({matches})"]
    return "\n".join(lines) + ";"


def plain_count(table, schema="dbo"):
    """A count of a CDM table whose size the server keeps no record of, such as a view, as a CDM_TABLE row."""
    lines = _plain_comment([PLAIN_WORDING["count"].format(table=table), PLAIN_WORDING["safe"]])
    lines += _plain_select(["'CDM_TABLE'", _text(table), "'Y'", "CAST((COUNT_BIG(*) / 10) * 10 AS varchar(30))"])
    lines.append(f"FROM {_core_name(schema, table)} WITH (NOLOCK);")
    return "\n".join(lines)


def plain_key(table, schema="dbo"):
    """Tier two: the number of digits in the highest key of a table, from one MAX over the key, as a CDM_TABLE row."""
    key = _keys(cdm_fields())[table]
    digits = f"CAST(LEN(REPLACE(CAST(MAX({_bracket(key['field'])}) AS varchar(40)), '-', '')) AS varchar(10))"
    lines = _plain_comment([PLAIN_WORDING["key"].format(table=table), PLAIN_WORDING["safe"]])
    lines += _plain_select(["'CDM_TABLE'", _text(table), "'Y'", "NULL", "NULL", digits])
    lines.append(f"FROM {_core_name(schema, table)} WITH (NOLOCK);")
    return "\n".join(lines)


def plain_cdm_source(schema="dbo"):
    """Tier two: the versions that cdm_source records, returned only when they are short and plain."""
    def safe(column, length):
        return f"MAX(CASE WHEN LEN({column}) <= {length} AND {column} NOT LIKE '%[^A-Za-z0-9 ._-]%' THEN {column} END)"
    lines = _plain_comment([PLAIN_WORDING["cdm_source"], PLAIN_WORDING["safe"]])
    lines += _plain_select(["'CDM_SOURCE'", safe("[cdm_version]", 20), safe("[vocabulary_version]", 30),
                            "CAST(MAX([cdm_version_concept_id]) AS varchar(20))", "CAST(COUNT_BIG(*) AS varchar(30))"])
    lines.append(f"FROM {_core_name(schema, 'cdm_source')} WITH (NOLOCK);")
    return "\n".join(lines)


def plain_type_concepts(table, schema="dbo", percent=None):
    """Tier two: the rows of a table by type concept, from a literal sample where the table is large."""
    field = _type_fields(cdm_fields())[table]
    f = _bracket(field)
    from .checks import percent_text, sample_clause, scaled
    source = _core_name(schema, table) + (sample_clause(percent) if percent else "") + " WITH (NOLOCK)"
    counted = scaled("g.n", percent) if percent else "(g.n / 10) * 10"
    comment = [PLAIN_WORDING["type_concepts"].format(table=table, field=field)]
    if percent:
        comment.append(PLAIN_WORDING["type_sampled"].format(table=table, percent=percent_text(percent)))
    lines = _plain_comment(comment + [PLAIN_WORDING["safe"]])
    lines += _plain_select(["'TYPE_CONCEPT'", _text(table), _text(field), "CAST(g.k AS varchar(20))",
                            f"CAST({counted} AS varchar(30))"])
    lines += [f"FROM (SELECT {f} AS k, COUNT_BIG(*) AS n",
              f"      FROM {source}",
              f"      GROUP BY {f}",
              f"      HAVING COUNT_BIG(*) >= {MINIMUM_COUNT}) AS g;"]
    return "\n".join(lines)


def plain_match(pair, cast, settings, core_rows, source_percent=None):
    """Tier two: how many of a bounded sample of distinct source keys the core holds, for one join.

    It takes up to PLAIN_KEY_SAMPLE distinct keys from the source table, from a literal sample of its pages
    where the table is large, and looks each one up in the core field, so that a whole core table is never
    joined to a whole source table. It is offered only where the core table is no larger than the limit of
    the plain checks. It returns no key and no value.
    """
    table, field, source_table, source_column = pair
    schema, prefix = settings["omop_schema"], settings["source_prefix"]
    reach = "".join(_bracket(part) + "." for part in prefix.rstrip(".").split("."))
    key = f"s.{_bracket(source_column)}"
    value = f"CAST({key} AS {cast})" if cast else key
    from .checks import percent_text, sample_clause
    sample = sample_clause(source_percent) if source_percent else ""
    comment = [PLAIN_WORDING["match"].format(sample=f"{PLAIN_KEY_SAMPLE:,}", source=f"{source_table}.{source_column}",
                                             core=f"{table}.{field}")]
    if source_percent:
        comment.append(PLAIN_WORDING["match_sampled"].format(source_table=source_table, percent=percent_text(source_percent)))
    comment += [PLAIN_WORDING["match_cost"].format(table=table, rows=_count(core_rows)), PLAIN_WORDING["safe"]]
    lines = _plain_comment(comment)
    lines += _plain_select(["'SOURCE_KEY_MATCH'", _text(f"{table}.{field}"), _text(f"{source_table}.{source_column}"),
                            "CAST((COUNT_BIG(*) / 10) * 10 AS varchar(30))",
                            "CAST((SUM(m.hit) / 10) * 10 AS varchar(30))",
                            f"CASE WHEN COUNT_BIG(*) >= {MINIMUM_COUNT} THEN CAST(SUM(m.hit) * 100 / COUNT_BIG(*) AS varchar(10)) END"])
    lines += [f"FROM (SELECT DISTINCT TOP ({PLAIN_KEY_SAMPLE}) {value} AS k",
              f"      FROM {reach}{_bracket(source_table)} AS s{sample} WITH (NOLOCK)",
              f"      WHERE {key} IS NOT NULL) AS d",
              f"CROSS APPLY (SELECT CASE WHEN EXISTS (SELECT 1 FROM {_core_name(schema, table)} AS c WITH (NOLOCK)",
              f"                                      WHERE c.{_bracket(field)} = d.k)",
              "                   THEN CAST(1 AS bigint) ELSE 0 END AS hit) AS m;"]
    return "\n".join(lines)


def plain_match_core(pair, settings, core_rows, home=None, home_kind=None, percent=None):
    """Tier two, for a core table over the limit: the join measured from the core side, on a bounded sample.

    It reads a literal share of the core table's pages, chosen from its known size, takes up to
    PLAIN_KEY_SAMPLE distinct non-empty values of the core field, and looks each one up as a key of home, the
    (table, column) that the source key refers to, or of the source column itself where none is known. A
    whole-number key is looked up through TRY_CAST, so that the lookup can use the key's index. It returns
    one SOURCE_KEY_MATCH_SAMPLED row, which names the join as SOURCE_KEY_MATCH does.
    """
    from . import checks as checking
    table, field, source_table, source_column = pair
    schema, prefix = settings["omop_schema"], settings["source_prefix"]
    reach = "".join(_bracket(part) + "." for part in prefix.rstrip(".").split("."))
    home_table, home_column = home or (source_table, source_column)
    percent = percent or checking.sample_percent(core_rows)
    c = f"c.{_bracket(field)}"
    key = f"s.{_bracket(home_column)}"
    lookup = (f"{key} = TRY_CAST(d.v AS bigint)" if home_kind == "number"
              else f"{key} = d.v" if home_kind == "text" else f"CAST({key} AS nvarchar(4000)) = d.v")
    comment = [PLAIN_WORDING["match_core"].format(table=table, percent=checking.percent_text(percent), sample=f"{PLAIN_KEY_SAMPLE:,}",
                                                  core=f"{table}.{field}", home=f"{home_table}.{home_column}"),
               PLAIN_WORDING["safe"]]
    lines = _plain_comment(comment)
    lines += _plain_select(["'SOURCE_KEY_MATCH_SAMPLED'", _text(f"{table}.{field}"), _text(f"{source_table}.{source_column}"),
                            "CAST((COUNT_BIG(*) / 10) * 10 AS varchar(30))",
                            "CAST((SUM(m.hit) / 10) * 10 AS varchar(30))",
                            f"CASE WHEN COUNT_BIG(*) >= {MINIMUM_COUNT} THEN CAST(SUM(m.hit) * 100 / COUNT_BIG(*) AS varchar(10)) END"])
    lines += [f"FROM (SELECT DISTINCT TOP ({PLAIN_KEY_SAMPLE}) CAST({c} AS nvarchar(4000)) AS v",
              f"      FROM {_core_name(schema, table)} AS c{checking.sample_clause(percent)} WITH (NOLOCK)",
              f"      WHERE {c} IS NOT NULL AND LTRIM(RTRIM(CAST({c} AS nvarchar(4000)))) <> N'') AS d",
              f"CROSS APPLY (SELECT CASE WHEN EXISTS (SELECT 1 FROM {reach}{_bracket(home_table)} AS s WITH (NOLOCK)",
              f"                                      WHERE {lookup})",
              "                   THEN CAST(1 AS bigint) ELSE 0 END AS hit) AS m;"]
    return "\n".join(lines)


def plain_settings(conversion_folder, settings=None):
    """The settings that the plain queries are written with: release.json's, with any given ones in their place."""
    return _settings(conversion_folder, settings)


def plain(conversion_folder, settings=None, profile_text=None, source_sizes=None):
    """The whole plain form for a conversion, as a list of {"id", "tier", "state", "sql"}, tier one first.

    Tier two waits for tier one: a query is offered only on a table whose size the profile in hand gives,
    except a count marked as reading the whole table where the server keeps no size. source_sizes gives the
    sizes of source tables that the check results hold, by name; a match query waits for its source table.
    """
    from . import checks as checking
    chosen = _settings(conversion_folder, settings)
    schema = chosen["omop_schema"]
    facts = conversion_facts(conversion_folder)
    fields = cdm_fields()
    keys, type_fields = _keys(fields), _type_fields(fields)
    found = read(profile_text, conversion_folder) if profile_text else None
    tables = list(dict.fromkeys([*facts["written"], *(p[0] for p in facts["pairs"]), "cdm_source"]))
    wanted = sorted({(t, f) for t, f in facts["reads"] if t in fields} | {(p[0], p[1]) for p in facts["pairs"]})
    queries = [{"id": "profile:tier-one", "tier": 1, "state": "ready",
                "sql": plain_tier_one(tables, wanted, chosen)}]
    known = (found or {}).get("tables", {})
    for table in tables:
        entry = known.get(table)
        if entry is None:
            continue
        if not entry["present"]:
            continue
        if entry["rows"] is None:
            queries.append({"id": f"profile:count:{table}", "tier": 2, "state": "count", "sql": plain_count(table, schema)})
            continue
        large = entry["rows"] > checking.PLAIN_EXACT_ROWS
        if table == "cdm_source":
            queries.append({"id": "profile:cdm-source", "tier": 2, "state": "ready", "sql": plain_cdm_source(schema)})
            continue
        if keys.get(table) and keys[table]["datatype"] == "integer":
            queries.append({"id": f"profile:key:{table}", "tier": 2, "state": "ready", "sql": plain_key(table, schema)})
        if table in facts["written"] and table in type_fields:
            percent = checking.sample_percent(entry["rows"]) if large else None
            queries.append({"id": f"profile:types:{table}", "tier": 2, "state": "sampled" if large else "ready",
                            "sql": plain_type_concepts(table, schema, percent)})
    for pair, entry in facts["pairs"].items():
        query = plain_match_offer(pair, entry["cast"], chosen, known, source_sizes)
        if query:
            queries.append(query)
    return queries


def plain_match_offer(pair, cast, settings, tables, source_sizes, home=None, home_kind=None, exact_rows=None):
    """How the match query of one join is offered: a dictionary with its state, and its SQL where it can run.

    The state is "setting" without a source prefix, "sizes" until tier one gives the core table's size,
    "source-sizes" until the check results give the source table's, "absent" where the core lacks the table,
    "count" where the server keeps no size for it, "core-side" where the core table is over the limit, so that
    the join is measured from the core side on a sample, and otherwise "ready" or "sampled". home is the
    (table, column) that the source key refers to, where it is known, and home_kind "number" or "text".
    """
    from . import checks as checking
    table, field, source_table, source_column = pair
    ident = f"profile:match:{table}.{field}={source_table}.{source_column}"
    entry = tables.get(table)
    source_rows = next((n for name, n in (source_sizes or {}).items() if name.upper() == source_table.upper()), None)
    if not settings.get("source_prefix"):
        return {"id": ident, "tier": 2, "state": "setting", "sql": ""}
    if entry is None:
        return {"id": ident, "tier": 2, "state": "sizes", "sql": ""}
    if not entry["present"]:
        return {"id": ident, "tier": 2, "state": "absent", "sql": ""}
    if entry["rows"] is None:
        return {"id": f"profile:count:{table}", "tier": 2, "state": "count", "sql": plain_count(table, settings["omop_schema"])}
    if source_rows is None:
        return {"id": ident, "tier": 2, "state": "source-sizes", "sql": ""}
    if entry["rows"] > (checking.PLAIN_EXACT_ROWS if exact_rows is None else exact_rows):
        # The core table is large, so the join is measured from the core side, on a sample of it.
        return {"id": ident, "tier": 2, "state": "core-side",
                "sql": plain_match_core(pair, settings, entry["rows"], home, home_kind)}
    percent = checking.sample_percent(source_rows) if source_rows > checking.PLAIN_EXACT_ROWS else None
    return {"id": ident, "tier": 2, "state": "sampled" if percent else "ready",
            "sql": plain_match(pair, cast, settings, entry["rows"], percent)}


def pasted_rows(text):
    """Rows pasted from a results grid, which is tab-separated, or from a CSV file, without header rows or row counts."""
    text = (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "\t" in text:
        rows = [[cell.strip() for cell in line.split("\t")] for line in text.split("\n")]
    else:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]
    kept = []
    for row in rows:
        if not row or not any(row) or (len(row) == 1 and re.fullmatch(r"\(\d+ rows? affected\)", row[0])):
            continue
        if all(re.fullmatch(r"-*", cell) for cell in row):
            continue    # the line that sqlcmd prints under its headers
        if tuple(cell.upper() for cell in row[:6]) == LAYOUT:
            continue
        if len(row) == 6 and row[0].upper() in ALL_CATEGORIES and \
                tuple(v.upper() for v in row[1:1 + len(ALL_CATEGORIES[row[0].upper()][1])]) == ALL_CATEGORIES[row[0].upper()][1]:
            continue    # a header row of the whole script
        kept.append(["" if cell == "NULL" else cell for cell in row])
    return kept


def _profile_text(rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(LAYOUT)
    writer.writerows(rows)
    return out.getvalue()


def accepted_rows(rows, conversion_folder):
    """The rows that read accepts, each read on its own, so that a refused row is left out rather than kept."""
    pairs = conversion_facts(conversion_folder)["pairs"] if conversion_folder else None
    kept = []
    for row in rows:
        if len(row) != len(LAYOUT) or row[0].upper() not in ALL_CATEGORIES:
            raise ProfileError("a row does not have six values")
        try:
            found = _read_core([row], conversion_folder, pairs)
        except ProfileError:
            continue    # a count that is not a number, or not rounded down to ten
        if not found["rejected"]:
            kept.append([row[0].upper(), *row[1:]])
    return kept


def merged(earlier_text, later_rows, conversion_folder):
    """A profile with later rows added, as CSV text in LAYOUT, which read accepts.

    A later row replaces the earlier row for the same thing: a table's row is merged value by value, so that
    the highest key from tier two joins the size and the key type from tier one; tier one's report on a
    table replaces the earlier fields of that table; type concepts are replaced as a set for each table; and
    a match, a shape or a single row such as CDM_SOURCE replaces the earlier one. An earlier general profile
    is not kept, because its rows cannot stand beside the core profile's.
    """
    earlier = []
    if earlier_text:
        rows = _rows(earlier_text)
        if rows and tuple(cell.upper() for cell in rows[0][:6]) == LAYOUT:
            rows = rows[1:]
        if rows and not all(row[0].upper() in GENERAL_ALL for row in rows):
            earlier = [row for row in rows if len(row) == len(LAYOUT)
                       and tuple(v.upper() for v in row[1:1 + len(ALL_CATEGORIES.get(row[0].upper(), (0, ("",)))[1])])
                       != ALL_CATEGORIES.get(row[0].upper(), (0, ("",)))[1]]
    combined = {}
    def key(row):
        category, values = row[0].upper(), row[1:]
        if category in ("PROFILE", "CDM_SOURCE", "LOCAL_OBJECTS", "OBSERVATION_PERIOD"):
            return (category,)
        if category in ("CDM_TABLE", "CDM_FIELD_LOCAL", "SOURCE_VALUE_SHAPE"):
            return (category, values[0])
        if category == "TYPE_CONCEPT":
            return (category, values[0], values[2])
        return (category, values[0], values[1])
    for row in earlier:
        combined[key(row)] = list(row)
    # The tables that later rows report afresh: tier one's rows, which carry the size or the key type, or say
    # that the table is absent, and the tables whose type concepts are counted again.
    reported = {row[1] for row in later_rows if row[0].upper() == "CDM_TABLE" and (row[2] == "N" or row[3] or row[4])}
    recounted = {row[1] for row in later_rows if row[0].upper() == "TYPE_CONCEPT"}
    for k in list(combined):
        if (k[0] in ("CDM_FIELD_ABSENT", "CDM_FIELD_TYPE", "CDM_FIELD_LOCAL") and k[1] in reported) or \
                (k[0] == "TYPE_CONCEPT" and k[1] in recounted):
            del combined[k]
    for row in later_rows:
        k = key(row)
        if k[0] in ("SOURCE_KEY_MATCH", "SOURCE_KEY_MATCH_SAMPLED"):
            # A match from either side replaces the earlier match of the same join from the other side.
            other = "SOURCE_KEY_MATCH" if k[0] == "SOURCE_KEY_MATCH_SAMPLED" else "SOURCE_KEY_MATCH_SAMPLED"
            combined.pop((other,) + k[1:], None)
        if k[0] == "CDM_TABLE" and k in combined:
            combined[k] = [new if new else old for new, old in zip(row, combined[k])]
            if row[2] == "N":
                combined[k] = list(row)
        else:
            combined[k] = list(row)
    ordered = sorted(combined.values(), key=lambda row: (ALL_CATEGORIES[row[0].upper()][0], row[0], row[1:]))
    text = _profile_text(ordered)
    read(text, conversion_folder)
    return text


# Reading the result.

def _number(text, rounded=False):
    """A whole number, or None for an empty cell. Anything else is refused."""
    text = text.strip()
    if text in ("", "NULL"):
        return None
    if not text.isdecimal() or len(text) > 20:
        raise ProfileError("a count is not a number")
    value = int(text)
    if rounded and value % 10:
        raise ProfileError("a count is not rounded down to ten")
    return value


def _sql_type(text):
    text = text.strip().lower()
    found = TYPE_PATTERN.fullmatch(text)
    return text if found and found.group(1) in SQL_TYPES else None


def _type_matches(core, cdm):
    """Whether a core type stands for the CDM 5.4 type of a field, by the rule that the script applies."""
    found = TYPE_PATTERN.fullmatch(core)
    base, size = found.group(1), found.group(2)
    length = re.fullmatch(r"varchar\((\d+|max)\)", cdm)
    if length:
        return base in ("varchar", "nvarchar") and (size is None or size == length.group(1))
    wanted = {"integer": ("int",), "datetime": ("datetime", "datetime2")}.get(cdm, (cdm,))
    return base in wanted


def _empty_profile(kind):
    return {"format": kind, "version": None, "omop_schema": None, "source_compared": None, "cdm_source": None,
            "tables": {}, "local_objects": None, "absent_fields": {}, "type_differences": [], "local_fields": {},
            "type_concepts": {}, "source_values": {}, "matches": [], "observation_period": None, "errors": [],
            "rejected": 0, "approximate": kind == "general"}


def _rows(text):
    text = text.lstrip("﻿")
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text)) if row and any(c.strip() for c in row)]


def read(text, conversion_folder=None):
    """Reads a profile result, keeping only rows that the script could have returned. Returns a plain dictionary.

    The text may be the result of the core profile script, or of the central team's general
    database profile. With a conversion folder, a join is accepted only if the conversion makes it.
    """
    rows = _rows(text)
    if rows and tuple(cell.upper() for cell in rows[0][:6]) == LAYOUT:
        rows = rows[1:]
    if not rows:
        raise ProfileError("the profile holds no rows")
    if all(row[0].upper() in GENERAL_ALL for row in rows):
        return _read_general(rows)
    return _read_core(rows, conversion_folder)


def _read_core(rows, conversion_folder, pairs=None):
    fields = cdm_fields()
    known = {(t, r["field"]) for t, rs in fields.items() for r in rs}
    datatypes = {(t, r["field"]): r["datatype"] for t, rs in fields.items() for r in rs}
    if pairs is None:
        pairs = conversion_facts(conversion_folder)["pairs"] if conversion_folder else None
    profile = _empty_profile("core")

    def core_field(name):
        table, _, field = name.partition(".")
        ok = (table, field) in known and field.endswith("_source_value")
        return (table, field) if ok and (pairs is None or any(p[:2] == (table, field) for p in pairs)) else None

    def source_key(core, name):
        table, _, column = name.partition(".")
        if not (NAME_PATTERN.fullmatch(table) and NAME_PATTERN.fullmatch(column)):
            return None
        if pairs is not None and core + (table, column) not in pairs:
            return None
        return table, column

    for row in rows:
        if len(row) != len(LAYOUT):
            raise ProfileError("a row does not have six values")
        category, values = row[0].upper(), row[1:]
        if category not in ALL_CATEGORIES:
            raise ProfileError("a row has a category that the script does not write")
        header = ALL_CATEGORIES[category][1]
        if tuple(v.upper() for v in values[:len(header)]) == header:
            continue
        if any(v.startswith(FORMULA_STARTS) for v in values):
            profile["rejected"] += 1
            continue
        v1, v2, v3, v4, v5 = values
        if category == "PROFILE":
            if v1.isdecimal() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v2) and SCHEMA_PATTERN.fullmatch(v3) and v4 in ("Y", "N"):
                profile.update(version=v1, date=v2, omop_schema=v3, source_compared=v4 == "Y")
            else:
                profile["rejected"] += 1
        elif category == "CDM_SOURCE":
            if VERSION_PATTERN.fullmatch(v1) and VERSION_PATTERN.fullmatch(v2):
                profile["cdm_source"] = {"cdm_version": v1 or None, "vocabulary_version": v2 or None,
                                         "cdm_version_concept_id": _number(v3), "rows": _number(v4)}
            else:
                profile["rejected"] += 1
        elif category == "CDM_TABLE":
            kind = _sql_type(v4) if v4 else None
            if v1 in fields and v2 in ("Y", "N") and (kind or not v4):
                digits = _number(v5)
                profile["tables"][v1] = {"present": v2 == "Y", "rows": _number(v3, rounded=True),
                                         "key_type": kind, "key_digits": digits if digits is not None and digits <= 40 else None}
            else:
                profile["rejected"] += 1
        elif category == "LOCAL_OBJECTS":
            profile["local_objects"] = _number(v1)
        elif category == "CDM_FIELD_ABSENT":
            if (v1, v2) in known:
                profile["absent_fields"].setdefault(v1, []).append(v2)
            else:
                profile["rejected"] += 1
        elif category == "CDM_FIELD_TYPE":
            kind = _sql_type(v3)
            if (v1, v2) in known and kind and v4 == datatypes[(v1, v2)]:
                profile["type_differences"].append({"table": v1, "field": v2, "core_type": kind, "cdm_type": v4})
            else:
                profile["rejected"] += 1
        elif category == "CDM_FIELD_LOCAL":
            if v1 in fields:
                profile["local_fields"][v1] = _number(v2)
            else:
                profile["rejected"] += 1
        elif category == "TYPE_CONCEPT":
            count = _number(v4, rounded=True)
            if (v1, v2) in known and v2.endswith("_type_concept_id") and count and count >= MINIMUM_COUNT:
                concept = _number(v3)
                profile["type_concepts"].setdefault(v1, {})[str(concept) if concept is not None else ""] = count
            else:
                profile["rejected"] += 1
        elif category == "SOURCE_VALUE_SHAPE":
            found = core_field(v1)
            percent = _number(v3)
            if found and (percent is None or percent <= 100):
                profile["source_values"][v1] = {"non_empty": _number(v2, rounded=True), "digits_percent": percent,
                                                "min_length": _number(v4), "max_length": _number(v5)}
            else:
                profile["rejected"] += 1
        elif category in ("SOURCE_KEY_MATCH", "SOURCE_KEY_MATCH_SAMPLED"):
            # A sampled match was measured from the core side: the core values sampled, and how many of them
            # the source system holds as keys. It is an estimate, and says so.
            found = core_field(v1)
            key = source_key(found, v2) if found else None
            keys, matched, percent = _number(v3, rounded=True), _number(v4, rounded=True), _number(v5)
            if key and (percent is None or percent <= 100) and (keys or 0) >= (matched or 0):
                entry = {"core": v1, "source": v2, "keys": keys, "matched": matched, "percent": percent,
                         "side": "core" if category == "SOURCE_KEY_MATCH_SAMPLED" else "source"}
                if pairs is not None:
                    entry["steps"] = pairs[found + key]["steps"]
                profile["matches"].append(entry)
            else:
                profile["rejected"] += 1
        elif category == "OBSERVATION_PERIOD":
            profile["observation_period"] = {"persons": _number(v1, rounded=True), "with_period": _number(v2, rounded=True)}
        elif category == "ERROR":
            subject = v2
            if "=" in subject:
                core, _, source = subject.partition("=")
                found = core_field(core)
                ok = bool(found and source_key(found, source))
            else:
                table, _, field = subject.partition(".")
                ok = (table in fields and (not field or (table, field) in known)) or subject == profile["omop_schema"]
            if v1 in CATEGORIES and ok:
                profile["errors"].append({"section": v1, "object": subject, "number": _number(v3)})
            else:
                profile["rejected"] += 1
    return profile


def _approximate(text):
    """A row count in the general profile's form, such as 812, 12.3 k or 4.5 M, rounded down to ten."""
    found = re.fullmatch(r"(\d{1,12})(?:\.(\d))?\s*([kMB])?", text.strip())
    if not found:
        raise ProfileError("a row count is not in the expected form")
    whole, tenth, unit = found.groups()
    scale = {None: 1, "k": 1000, "M": 1000000, "B": 1000000000}[unit]
    value = int(whole) * scale + (int(tenth) * scale // 10 if tenth else 0)
    return (value // 10) * 10


def _read_general(rows):
    """Reads the central team's general database profile. Local names are counted and never kept."""
    fields = cdm_fields()
    keys = _keys(fields)
    columns, counts = {}, {}
    for row in rows:
        category = row[0].upper()
        if category == "TABLE_COLUMNS" and len(row) >= 6 and row[3].isdecimal():
            # A type such as DECIMAL(10,2) holds a comma, which a plain CSV export splits into two values.
            columns.setdefault((row[1], row[2].lower()), []).append((row[4].lower(), ",".join(row[5:])))
        elif category == "TABLES" and len(row) >= 4 and row[3].upper() != "ROW_CNTS":
            counts[(row[1], row[2].lower())] = _approximate(row[3])
    held = {}
    for schema, table in set(columns) | set(counts):
        if table in fields:
            held[schema] = held.get(schema, 0) + 1
    if not held:
        raise ProfileError("the general profile names no table of CDM 5.4")
    schema = max(sorted(held), key=lambda name: held[name])
    profile = _empty_profile("general")
    profile["omop_schema"] = schema if SCHEMA_PATTERN.fullmatch(schema) else None
    profile["local_objects"] = sum(1 for s, t in set(columns) | set(counts) if s == schema and t not in fields)
    profile["other_schema_tables"] = sum(1 for s, _ in set(columns) | set(counts) if s != schema)
    for table in fields:
        if (schema, table) not in columns and (schema, table) not in counts:
            profile["tables"][table] = {"present": False, "rows": None, "key_type": None, "key_digits": None}
            continue
        found = {name: _sql_type(kind) for name, kind in columns.get((schema, table), [])}
        key = keys[table]
        profile["tables"][table] = {"present": True, "rows": counts.get((schema, table)),
                                    "key_type": found.get(key["field"]) and TYPE_PATTERN.fullmatch(found[key["field"]]).group(1)
                                    if key else None, "key_digits": None}
        if not found:
            continue
        absent = [row["field"] for row in fields[table] if row["field"] not in found]
        if absent:
            profile["absent_fields"][table] = absent
        for row in fields[table]:
            kind = found.get(row["field"])
            if kind and not _type_matches(kind, row["datatype"]):
                profile["type_differences"].append({"table": table, "field": row["field"], "core_type": kind,
                                                    "cdm_type": row["datatype"]})
        local = sum(1 for name in found if not any(row["field"] == name for row in fields[table]))
        if local:
            profile["local_fields"][table] = local
    return profile


# Findings and summary.

def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _count(value):
    return f"{value:,}" if isinstance(value, int) else "an unknown number of"


def _is_54(version):
    return bool(re.fullmatch(r"(?:v|cdm\s*v?)?\s*5\.4(?:\.\d+)?", (version or "").strip(), re.IGNORECASE))


def suggest(profile, conversion_folder):
    """The findings of a profile for the register of guesses, as dictionaries."""
    facts = conversion_facts(conversion_folder)
    w = WORDING
    found = []

    def add(question, finding, setting_or_step, suggestion):
        found.append({"question": question, "finding": finding, "setting_or_step": setting_or_step, "suggestion": suggestion})

    needed = set()
    for table, names in facts["used"].items():
        needed |= {(table, name) for name in names}
    needed |= facts["reads"]
    # The published view for a table that the layer adds to reads every CDM 5.4 field of the core's table.
    needed |= {(table, name) for table in facts["written"] for name in profile["absent_fields"].get(table, [])}
    affected = sorted((t, f) for t, f in needed if f in profile["absent_fields"].get(t, []))

    def steps_using(names):
        steps = []
        for table, field in names:
            found = facts["uses"].get((table, field)) or [step for step, written in facts["writers"] if written == table]
            steps += [step for step in found if step not in steps]
        return _join(steps)

    version = (profile.get("cdm_source") or {}).get("cdm_version")
    if version and not _is_54(version):
        names = [f"{t}.{f}" for t, f in affected]
        add(w["version_question"], w["version_finding"].format(version=version),
            steps_using(affected) if names else "omop_schema",
            w["version_fields"].format(fields=_join(names)) if names else w["version_no_fields"])
    else:
        by_table = {}
        for table, field in affected:
            by_table.setdefault(table, []).append(field)
        for table, names in sorted(by_table.items()):
            add(w["absent_question"], w["absent_finding"].format(table=table, fields=_join(names)),
                steps_using((table, name) for name in names), w["absent_suggestion"].format(table=table))

    tables = profile["tables"]
    # The release script keeps its mapping rows in a table of its own, so the core need not have one.
    touched = (set(facts["written"]) | {t for t, _ in facts["reads"]} | {p[0] for p in facts["pairs"]}) - {"source_to_concept_map"}
    missing = sorted(t for t in touched if t in tables and not tables[t]["present"])
    if missing:
        add(w["missing_question"], w["missing_finding"].format(tables=_join(missing)), "omop_schema", w["missing_suggestion"])

    bigint = sorted(t for t, entry in tables.items() if entry["present"] and entry["key_type"] == "bigint")
    if bigint:
        add(w["bigint_question"], w["bigint_finding"].format(tables=_join(bigint)), "identifier_type", w["bigint_suggestion"])
    for table, entry in sorted(tables.items()):
        if entry["key_type"] == "int" and (entry["key_digits"] or 0) >= 10 and table in touched:
            add(w["headroom_question"], w["headroom_finding"].format(table=table, digits=entry["key_digits"]),
                "identifier_type", w["headroom_suggestion"])

    detail = tables.get("visit_detail")
    if detail and detail["present"] and detail["rows"]:
        add(w["visit_detail_question"], w["visit_detail_finding"].format(rows=_count(detail["rows"])),
            _join(s for s in facts["files"] if s.startswith("visit_detail")) or "visit_detail", w["visit_detail_suggestion"])

    for (table, concept), steps in sorted(facts["types"].items()):
        rows = profile["type_concepts"].get(table, {}).get(str(concept))
        if rows:
            add(w["duplicate_question"].format(table=table),
                w["duplicate_finding"].format(table=table, rows=_count(rows), concept=concept, steps=_join(steps)),
                _join(steps), w["duplicate_suggestion"].format(table=table))

    measured = set()
    for match in profile["matches"]:
        table, _, field = match["core"].partition(".")
        source_table, _, source_column = match["source"].partition(".")
        entry = facts["pairs"].get((table, field, source_table, source_column))
        if entry is None:
            continue
        measured.add((table, field))
        steps = _join(entry["steps"])
        name = f"{table}.{field}"
        if not match["keys"]:
            add(w["match_question"].format(field=name), w["match_empty_finding"].format(steps=steps), steps,
                w["match_empty_suggestion"])
        elif match["percent"] is not None and match["percent"] < MATCH_THRESHOLD:
            add(w["match_question"].format(field=name),
                w["match_finding"].format(matched=_count(match["matched"]), keys=_count(match["keys"]),
                                          percent=match["percent"], steps=steps),
                steps, w["match_suggestion"].format(field=name, steps=steps))
    for error in profile["errors"]:
        if error["section"] == "SOURCE_KEY_MATCH" and error["number"] == 468:
            core, _, source = error["object"].partition("=")
            table, _, field = core.partition(".")
            entry = facts["pairs"].get((table, field) + tuple(source.partition(".")[::2]))
            if entry:
                steps = _join(entry["steps"])
                add(w["match_question"].format(field=core), w["collation_finding"].format(field=core, steps=steps),
                    steps, w["collation_suggestion"].format(steps=steps))
                measured.add((table, field))
    for name, shape in sorted(profile["source_values"].items()):
        table, _, field = name.partition(".")
        if (table, field) in measured or shape["digits_percent"] is None or shape["digits_percent"] >= MATCH_THRESHOLD:
            continue
        steps = sorted({s for key, entry in facts["pairs"].items() if key[:2] == (table, field) and entry["cast"]
                        for s in entry["steps"]})
        if steps:
            add(w["match_question"].format(field=name),
                w["shape_finding"].format(percent=shape["digits_percent"], field=name, steps=_join(steps)),
                "source_prefix", w["shape_suggestion"])

    periods = profile.get("observation_period")
    if periods and periods["persons"] is not None and (periods["with_period"] or 0) < periods["persons"]:
        add(w["periods_question"], w["periods_finding"].format(with_period=_count(periods["with_period"]),
                                                              persons=_count(periods["persons"])),
            "040_anaesthetics_fall_inside_an_observation_period.sql" if (Path(conversion_folder) / "gates").exists()
            else "observation_period", w["periods_suggestion"])

    if profile["errors"]:
        sections = sorted({e["section"] for e in profile["errors"]})
        add(w["errors_question"], w["errors_finding"].format(count=len(profile["errors"]), sections=_join(sections)),
            "omop_schema", w["errors_suggestion"])
    return found


def summary(profile):
    """A short account of the profile that holds only standard names and rounded numbers."""
    w = WORDING
    lines = [w["summary_general"] if profile["format"] == "general" else w["summary_source"].format(version=profile["version"] or "unknown")]
    version = profile.get("cdm_source") or {}
    if version.get("cdm_version"):
        lines.append(w["summary_version"].format(cdm=version["cdm_version"], vocabulary=version.get("vocabulary_version") or "unknown"))
    else:
        lines.append(w["summary_no_version"])
    tables = profile["tables"]
    present = [t for t, e in tables.items() if e["present"]]
    lines.append(w["summary_tables"].format(present=len(present), total=len(tables) or len(cdm_fields()),
                                            local=profile.get("local_objects") or 0))
    for table in present:
        entry = tables[table]
        if entry["rows"] and entry["key_type"]:
            digits = w["summary_digits"].format(digits=entry["key_digits"]) if entry["key_digits"] else ""
            lines.append(w["summary_table"].format(table=table, rows=_count(entry["rows"]), kind=entry["key_type"], digits=digits))
        elif entry["rows"]:
            lines.append(w["summary_table_no_key"].format(table=table, rows=_count(entry["rows"])))
    for table, names in sorted(profile["absent_fields"].items()):
        lines.append(w["summary_absent"].format(table=table, fields=_join(names)))
    for item in profile["type_differences"]:
        lines.append(w["summary_type"].format(table=item["table"], field=item["field"], core=item["core_type"], cdm=item["cdm_type"]))
    for number, match in enumerate(profile["matches"], 1):
        # The source names are left out, because they belong to the hospital's source system.
        joined = (w["summary_join"].format(field=match["core"], steps=_join(match["steps"])) if match.get("steps")
                  else w["summary_join_numbered"].format(number=number, field=match["core"]))
        if match["percent"] is None:
            lines.append(w["summary_match_unknown"].format(joined=joined))
        else:
            lines.append(w["summary_match"].format(joined=joined, matched=_count(match["matched"]),
                                                   keys=_count(match["keys"]), percent=match["percent"]))
    periods = profile.get("observation_period")
    if periods:
        lines.append(w["summary_periods"].format(with_period=_count(periods["with_period"]), persons=_count(periods["persons"])))
    if profile["errors"]:
        lines.append(w["summary_errors"].format(count=len(profile["errors"])))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.profile")
    parser.add_argument("conversion", type=Path)
    parser.add_argument("--out", type=Path, help="where to write the profile script")
    parser.add_argument("--read", type=Path, help="a saved profile result to read")
    parser.add_argument("--source-prefix")
    parser.add_argument("--omop-schema")
    args = parser.parse_args()
    if bool(args.out) == bool(args.read):
        parser.error("give either --out to write the script or --read to read a result")
    try:
        if args.out:
            settings = {key: value for key, value in (("source_prefix", args.source_prefix),
                                                      ("omop_schema", args.omop_schema)) if value}
            args.out.write_text(script(args.conversion, settings), encoding="utf-8")
            print(f"The profile script is in {args.out}.")
        else:
            profile = read(decode(args.read.read_bytes()), args.conversion)
            print(summary(profile))
            for item in suggest(profile, args.conversion):
                print(f"{item['question']}\n  {item['finding']}\n  {item['suggestion']} ({item['setting_or_step']})\n")
    except ProfileError as error:
        raise SystemExit(f"schemalyser.profile: {error}")


if __name__ == "__main__":
    main()
