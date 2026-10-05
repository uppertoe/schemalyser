"""Writes the release script for the anaesthesia layer of a conversion.

The core OMOP database belongs to someone else, and is refreshed first. This script is what would
run after it. It keeps the anaesthesia rows in tables of its own, beside the core tables, and
publishes one view for each of those tables that joins the core's rows to its own, with a view
of the core's own rows for every other table of CDM 5.4, so that the published schema is a whole
CDM that OHDSI tools can read. It changes no core table.

    python -m schemalyser.release CONVERSION --catalogue CATALOGUE.csv --out FOLDER

The folder receives release.sql, which is run with sqlcmd, and source_manifest.csv, which lists
the source tables and columns that the anaesthesia steps read. The command prints the sqlcmd line
that runs the script, and exits with 1 when it refuses a step, a gate, a mapping row or a setting.

What is assumed about the real database is held in settings, so that each assumption can change
without the steps changing: where the core tables are, where the source tables are reached, how
the layer's identifiers are typed and where they begin, and what a failed gate leaves behind. A
conversion folder may hold release.json to set them. The places are sqlcmd variables that the
operator gives with -v: the script sets none itself, because a :setvar in a script overrides -v.

Everything that the script carries from the folder is checked before it is written, because
sqlcmd reads a script line by line and substitutes $(name) wherever it appears. A step or a gate
must be one SELECT that writes nothing and reaches no other server, and no literal or identifier
in it, or in a mapping row, may hold a line break or $(.

The derived layer's steps run after the anaesthesia steps. Each writes a custom table that the
folder's tables.json defines, which the script creates in the anaesthesia schema beside the
layer's other tables and publishes as a view of its own rows. tables.json is checked as strictly
as a step: a plain name that is not a table of CDM 5.4, a type from a short fixed list, and
descriptions without a line break or $(.
"""
import argparse
import csv
import io
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from .catalogue import Catalogue
from .convert import (COUNT_MARK, FIELDS, IDENTIFIER_OFFSET, LAYERS, TablesError, alternatives, as_request, custom_rows, layer_problems,
                      read_counts, read_tables)
from .extract import analyse_request, decode
from .translate import OMOP_SCHEMA

SETTINGS = {
    "omop_schema": "dbo",                    # the schema that holds the core OMOP tables
    "anaesthesia_schema": "anaes_cdm",       # the schema for the anaesthesia tables
    "published_schema": "anaes_pub",         # the schema for the views that join the two
    "source_prefix": "clarity_stage.",       # what is written before the name of a source table
    "identifier_type": "BIGINT",             # INT or BIGINT, for the identifiers of the anaesthesia tables
    "identifier_offset": IDENTIFIER_OFFSET,  # the anaesthesia layer numbers its rows from just above this
    "on_failure": "keep",                    # keep: a failed gate leaves the previous rows; empty: it leaves none
}
# The wording of the script, kept in one place. The clinical lead approved the first fourteen strings,
# from "header" to "gate_failed", as they stand. The strings after them await approval.
WORDING = {
    "header": [
        "Anaesthesia layer for OMOP: release script.",
        "This script adds the anaesthesia rows to tables of their own, beside the core OMOP tables, and does not change any core table.",
        "Run it with sqlcmd after the core OMOP refresh has finished.",
        "If a quality gate fails, the script stops and the rows from the previous run stay in place.",
    ],
    "omop_schema": "The schema that holds the core OMOP tables.",
    "anaesthesia_schema": "The schema for the anaesthesia tables, which this script owns.",
    "published_schema": "The schema for the views that ATLAS and other tools read, which join the core rows to the anaesthesia rows.",
    "source_prefix": "The prefix that reaches the source tables, for example a staging schema followed by a full stop.",
    "stage_1": "Stage 1: the script creates its two schemas, its tables and its views where they do not exist yet.",
    "stage_2": "Stage 2: the script removes the rows that its previous run wrote, and loads the mapping rows that it carries.",
    "stage_3": "Stage 3: the script writes the anaesthesia rows, one step at a time.",
    "stage_4": "Stage 4: the script runs each quality gate. A gate lists the rows that break a rule, so a gate that lists no rows has passed.",
    "stage_5": "Stage 5: the script reports how many rows it wrote to each table.",
    "gate_failed": "A quality gate failed: {name}. The script has stopped, and the rows from the previous run are unchanged.",
    # Awaiting approval.
    "header_empty": "If a quality gate fails, the script stops and leaves the anaesthesia tables empty, because the rows from the previous run may point at the wrong people once the core has been refreshed.",
    "run": "The operator runs this script with sqlcmd and gives a value for each of the four variables below with -v, as in the following line, which uses the values that the conversion's settings suggest:",
    "run_variables": "The script sets none of these variables itself, so the value given with -v is the one it uses. If a variable has no value, sqlcmd stops before the script changes anything.",
    "guard": "Before it changes anything, the script checks the values it has been given and the identifiers that the core already holds.",
    "guard_names": "The script has stopped before changing anything, because the schema name given for {variable} is empty, is longer than 128 characters, begins with a digit, or holds a character other than a letter, a digit or an underscore.",
    "guard_prefix": "The script has stopped before changing anything, because the source prefix must be one to three names, each made of letters, digits and underscores and each followed by a full stop.",
    "guard_distinct": "The script has stopped before changing anything, because the core schema, the anaesthesia schema and the published schema must be three different schemas.",
    "guard_identifiers": "The script has stopped before changing anything, because the core's table {table} already holds an identifier at or above {offset}, where the anaesthesia layer's identifiers begin. If the core's identifiers have grown this far, raise the identifier_offset setting above the core's highest identifier, then write the release script again.",
    "stage_1_redefine": "Where a table from an earlier run no longer matches the definition below, for example because the identifier type has changed, the script drops it and creates it again.",
    "stage_1_redefine_keep": "The script does this inside the transaction, so a failed gate restores the earlier table and its rows.",
    "stage_1_complete": "The script also publishes a view of the core's own rows for every other table of CDM 5.4, so that the published schema is a complete CDM for ATLAS and the other OHDSI tools.",
    "stage_2_empty": "The script commits the removal of the previous rows at once, so that a failed gate leaves the anaesthesia tables empty.",
    "stage_3_identifiers": "Each step numbers its own rows from 1. The script adds the start of the anaesthesia layer's range, {offset}, for the first step that writes a table, and the highest identifier that the layer has already written to that table for each later step.",
    "gate_failed_empty": "A quality gate failed: {name}. The script has stopped, and the anaesthesia tables are empty.",
    # Lines that the command prints for the person who writes the script.
    "not_written": "The command has not written release.sql, because of the following problem: {reason}.",
    "run_with": "The operator runs release.sql with the following line, once the server and the database have been filled in:",
    # Awaiting approval: the derived layer.
    "stage_1_custom": "The script also creates the custom tables that the derived layer writes, beside the anaesthesia tables, and publishes a view of each one.",
    "stage_3_derived": "The derived steps run last. They read only the OMOP tables, and each one writes its custom table with the identifiers of the rows it is built from.",
    # Awaiting approval: the conversion's counts.
    "stage_5_counts": "The script then reports each of the conversion's counts, such as the anaesthetics that the layer has left out, as a sentence that holds a number and never an identifier.",
}
PLACE = {"omop": "SCHEMALYSER_OMOP", "published": "SCHEMALYSER_PUBLISHED", "source": "SCHEMALYSER_SOURCE", "own": "SCHEMALYSER_OWN"}
# Each place that a setting holds, and the sqlcmd variable that the operator gives for it.
VARIABLES = (("omop_schema", "OmopSchemaName"), ("anaesthesia_schema", "AnaesSchemaName"),
             ("published_schema", "AnaesPubSchemaName"), ("source_prefix", "SourcePrefix"))
MAPPING_TABLE = "source_to_concept_map"
SQL_TYPES = {"float": "FLOAT", "date": "DATE", "datetime": "DATETIME2(0)", "varchar(max)": "VARCHAR(MAX)"}
SCHEMA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# An output column that the script names: a letter or underscore followed by letters, digits and underscores.
COLUMN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
SOURCE_PREFIX = re.compile(r"([A-Za-z_][A-Za-z0-9_]*\.){1,3}")
FILE_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# Functions that reach another server or run text as SQL. A step or a gate may name none of them.
REFUSED_WORDS = {"OPENQUERY", "OPENROWSET", "OPENDATASOURCE", "OPENXML", "EXEC", "EXECUTE", "SP_EXECUTESQL", "XP_CMDSHELL"}
# Statements and clauses that change something. A step or a gate may hold none of them.
REFUSED_NODES = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "TruncateTable", "Alter", "Command", "Into", "Execute", "Use")
    if hasattr(exp, name))
VARIABLE_TOKENS = {getattr(TokenType, name) for name in ("PARAMETER", "SESSION_PARAMETER") if hasattr(TokenType, name)}
QUOTED_TOKENS = {TokenType.STRING, TokenType.NATIONAL_STRING, TokenType.IDENTIFIER}
# A line that sqlcmd would read as a command of its own, and not as T-SQL.
SQLCMD_LINE = re.compile(r"\s*(GO\b|:|!!)", re.IGNORECASE)


class Refused(ValueError):
    """Something in the conversion folder or the settings cannot go into a release script."""


def _fields():
    with open(FIELDS, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {table: [r for r in rows if r["table"] == table] for table in dict.fromkeys(r["table"] for r in rows)}


def _key(fields):
    """The primary key field of a table, or None."""
    return next((row["field"] for row in fields if row["primary_key"] == "Y"), None)


def _bracket(name):
    return "[" + name.replace("]", "]]") + "]"


def _variables(text):
    """Puts the sqlcmd variables where the placeholders stand."""
    return (text.replace(f"[{PLACE['omop']}].", "[$(OmopSchemaName)].")
                .replace(f"[{PLACE['published']}].", "[$(AnaesPubSchemaName)].")
                .replace(f"[{PLACE['own']}].", "[$(AnaesSchemaName)].")
                .replace(f"[{PLACE['source']}].", "$(SourcePrefix)"))


def check_file_name(name):
    """Refuses a step or gate file name that could reach outside its folder or break a comment."""
    if not isinstance(name, str) or not FILE_NAME.fullmatch(name) or set(name) == {"."}:
        raise Refused(f"{name!r}: a file name may hold only letters, digits, full stops, hyphens and underscores")
    return name


def check_column(name, where):
    """Refuses an output column name that is not a plain name, so that it can never close its brackets."""
    if not isinstance(name, str) or not COLUMN_NAME.fullmatch(name):
        raise Refused(f"{where}: an output column must be named with letters, digits and underscores, beginning with a letter or an underscore")
    return name


def check_text(value, where):
    """Refuses text that could carry a sqlcmd command or variable: a line break, or $(."""
    if "\r" in value or "\n" in value or "$(" in value:
        raise Refused(f"{where}: a value may not hold a line break or $(, which sqlcmd would read as a command or a variable")
    return value


def _single_select(sql, where):
    """The parsed tree of one SELECT, or of a union of SELECTs, after every rule that keeps a script safe."""
    try:
        tokens = sqlglot.tokenize(sql, dialect="tsql")
        trees = [tree for tree in sqlglot.parse(sql, dialect="tsql") if tree is not None]
    except sqlglot.errors.SqlglotError as error:
        raise Refused(f"{where}: the SQL cannot be read ({str(error).splitlines()[0]})") from None
    for token in tokens:
        check_text(token.text, where)
        if token.token_type in VARIABLE_TOKENS or token.text.startswith("@"):
            raise Refused(f"{where}: a step or a gate may not use a variable")
        if token.token_type not in QUOTED_TOKENS and token.text.upper() in REFUSED_WORDS:
            raise Refused(f"{where}: a step or a gate may not use {token.text.upper()}")
    if len(trees) != 1:
        raise Refused(f"{where}: a step or a gate must be exactly one statement, and this holds {len(trees)}")
    tree = trees[0]
    selects = [tree] if isinstance(tree, exp.Select) else list(tree.find_all(exp.Select)) if isinstance(tree, exp.SetOperation) else []
    if not selects or (isinstance(tree, exp.SetOperation) and any(
            not isinstance(side, (exp.Select, exp.SetOperation, exp.Subquery)) for node in tree.find_all(exp.SetOperation)
            for side in (node.this, node.expression))):
        raise Refused(f"{where}: a step or a gate must be one SELECT, or a union of SELECTs")
    for node in tree.walk():
        if isinstance(node, REFUSED_NODES) or (isinstance(node, exp.Select) and node.args.get("into")):
            raise Refused(f"{where}: a step or a gate may only read, and this one holds {node.key.upper()}")
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier) and node.this is not None:
            raise Refused(f"{where}: a table may be named with at most three parts, and may not be a function")
    return tree


def rewrite(sql, written, table=None, where="step", fields=None):
    """One SELECT with every table given its place: a core table, a published view, or a source table.

    Returns the T-SQL text with placeholders for the three places, and the names of its output columns.
    With table, each output column must be a field of that table, named once: a table of CDM 5.4, or
    a custom table among fields, which holds the field rows of every table that may be written.
    Raises Refused when the SQL breaks a rule that keeps the script safe.
    """
    tree = _single_select(sql, where)
    named = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    for found in list(tree.find_all(exp.Table)):
        if not found.db and found.name.upper() in named:
            continue
        if (found.db or "").upper() == OMOP_SCHEMA.upper():
            # The mapping rows are the anaesthesia layer's own, and are kept apart from any the core holds.
            place = (PLACE["own"] if found.name.lower() == MAPPING_TABLE
                     else PLACE["published"] if found.name.lower() in written else PLACE["omop"])
        else:
            place = PLACE["source"]
        found.set("db", exp.to_identifier(place))
        found.set("catalog", None)
    columns = [projection.alias_or_name for projection in tree.selects]
    if table is not None:
        for name in columns:
            check_column(name, where)
        known = {row["field"] for row in (fields or _fields()).get(table.lower(), [])}
        unknown = [name for name in columns if name not in known]
        if not known:
            raise Refused(f"{where}: {table} is not a table of CDM 5.4 or a custom table")
        if unknown:
            raise Refused(f"{where}: {', '.join(map(repr, unknown))} is not a field of {table.lower()}")
        if len(set(columns)) != len(columns):
            raise Refused(f"{where}: an output column is named twice")
    text = tree.sql(dialect="tsql", pretty=True, identify=True, comments=False)
    if "$(" in text or any(SQLCMD_LINE.match(line) for line in text.splitlines()):
        raise Refused(f"{where}: the rewritten SQL holds a line that sqlcmd would read as a command")
    return text, columns


def _custom(folder):
    """The folder's custom tables, as read_tables gives them. Raises Refused when tables.json breaks a rule."""
    try:
        return read_tables(folder)
    except TablesError as error:
        raise Refused(str(error)) from None


def _steps(folder, custom=None):
    """The anaesthesia steps and then the derived steps, each with its SQL."""
    folder = Path(folder)
    steps = json.loads((folder / "conversion.json").read_text())
    names = {table["name"] for table in (_custom(folder) if custom is None else custom)}
    problems = layer_problems(steps, names)
    if problems:
        raise Refused("; ".join(problems))
    for step in steps:
        check_file_name(step["file"])
    return [(step, decode((folder / step["file"]).read_bytes())) for step in steps if step["layer"] in LAYERS[1:]]


def _gates(folder):
    return [(check_file_name(path.name), decode(path.read_bytes())) for path in sorted((Path(folder) / "gates").glob("*.sql"))]


def _counts(folder, written):
    """Each of the conversion's counts as one SELECT of its sentence with the number in place, checked as a gate is."""
    try:
        counts = read_counts(folder)
    except ValueError as error:
        raise Refused(str(error)) from None
    found = []
    for item in counts:
        name = check_file_name(item["name"])
        says = check_text(item["says"], f"counts/{name}")
        text, columns = rewrite(item["sql"], written, where=f"counts/{name}")
        if len(columns) != 1:
            raise Refused(f"counts/{name}: a count gives one column, which holds the number")
        column = _bracket(check_column(columns[0], f"counts/{name}"))
        sentence = says.replace("'", "''")
        found.append((name, f"SELECT REPLACE(N'{sentence}', N'{COUNT_MARK}', CAST((SELECT [c].{column} FROM (\n"
                            f"{_variables(text)}\n) AS [c]) AS varchar(20))) AS [count_report];"))
    return found


def settings_for(folder=None, settings=None):
    """The settings for a folder: the defaults, then the folder's release.json, then those given. Raises Refused on a bad one."""
    chosen = dict(SETTINGS)
    if folder is not None and (Path(folder) / "release.json").exists():
        chosen.update(json.loads((Path(folder) / "release.json").read_text()))
    chosen.update(settings or {})
    unknown = sorted(set(chosen) - set(SETTINGS))
    if unknown:
        raise Refused(f"unknown settings: {', '.join(unknown)}")
    for key in ("omop_schema", "anaesthesia_schema", "published_schema"):
        if not isinstance(chosen[key], str) or not SCHEMA_NAME.fullmatch(chosen[key]):
            raise Refused(f"{key}: a schema name is a letter or underscore followed by up to 127 letters, digits and underscores")
    schemas = [chosen[key].lower() for key in ("omop_schema", "anaesthesia_schema", "published_schema")]
    if len(set(schemas)) != 3:
        raise Refused("the core, anaesthesia and published schemas must be three different schemas")
    if not isinstance(chosen["source_prefix"], str) or not SOURCE_PREFIX.fullmatch(chosen["source_prefix"]):
        raise Refused("source_prefix: one to three names, each followed by a full stop, as in staging.dbo.")
    kind = str(chosen["identifier_type"]).upper()
    if kind not in ("INT", "BIGINT"):
        raise Refused("identifier_type: INT or BIGINT")
    chosen["identifier_type"] = kind
    offset = chosen["identifier_offset"]
    highest = 2 ** 31 - 1 if kind == "INT" else 2 ** 62
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < highest // 2:
        raise Refused(f"identifier_offset: a whole number from 0 to {highest // 2 - 1}, so that {kind} identifiers have room above it")
    if chosen["on_failure"] not in ("keep", "empty"):
        raise Refused("on_failure: keep or empty")
    return chosen


def run_command(settings=None, script_name="release.sql"):
    """The sqlcmd line that runs the script, with a -v for each variable. SERVER and DATABASE are for the operator to fill in."""
    chosen = settings_for(None, settings)
    values = " ".join(f'{name}="{chosen[key]}"' for key, name in VARIABLES)
    return f"sqlcmd -S SERVER -d DATABASE -b -i {script_name} -v {values}"


def _mapping_rows(rows, fields, own):
    """Mapping rows as INSERT statements. A database cannot read a file from a repository, so the rows travel in the script."""
    names = [row["field"] for row in fields]
    numeric = {row["field"] for row in fields if row["datatype"] == "integer"}

    def literal(name, value, index):
        if value is None or value == "":
            return "NULL"
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        value = str(value)
        check_text(value, f"mapping row {index + 1}, {name}")
        if name in numeric and re.fullmatch(r"-?\d+", value):
            return value
        return "'" + value.replace("'", "''") + "'"

    as_dicts = [row if isinstance(row, dict) else dict(zip(names, row)) for row in rows]
    statements = []
    for start in range(0, len(as_dicts), 500):
        values = ",\n".join("  (" + ", ".join(literal(name, row.get(name), start + i) for name in names) + ")"
                            for i, row in enumerate(as_dicts[start:start + 500]))
        statements.append(f"INSERT INTO {own}.[{MAPPING_TABLE}] ({', '.join(f'[{name}]' for name in names)}) VALUES\n{values};")
    return statements


def _folder_mappings(folder):
    """The folder's mapping rows, with the site's confirmed rows added, as the conversion runner loads them."""
    from .convert import mapping_dicts
    return mapping_dicts(folder)


# What SQL Server reports of each column type in sys.columns: (type name, max_length).
SERVER_TYPES = {"BIGINT": ("bigint", 8), "INT": ("int", 4), "FLOAT": ("float", 8), "DATE": ("date", 3), "DATETIME2(0)": ("datetime2", 6)}


def _definitions(table, fields, chosen):
    """Each column of one of the layer's own tables as (name, type, NOT NULL), with the identifiers in the chosen type."""
    columns = []
    for row in fields:
        if row["datatype"] == "integer":
            identifier = row["field"].endswith("_id") and not row["field"].endswith("concept_id")
            kind = chosen["identifier_type"] if identifier else "INT"
        else:
            kind = SQL_TYPES.get(row["datatype"], row["datatype"].upper())
        columns.append((row["field"], kind, row["required"] == "Y" or row["primary_key"] == "Y"))
    return columns


def _server_type(kind):
    if kind in SERVER_TYPES:
        return SERVER_TYPES[kind]
    size = re.fullmatch(r"VARCHAR\((\d+|MAX)\)", kind)
    return "varchar", -1 if size.group(1) == "MAX" else int(size.group(1))


def _create_table(table, fields, chosen, own):
    """The statements that make one of the layer's own tables match its definition, keeping it when it already does."""
    columns = _definitions(table, fields, chosen)
    key = _key(fields) if table != MAPPING_TABLE else None
    name = f"{own}.[{table}]"
    quoted = f"N'{own}.[{table}]'"
    expected = ", ".join(f"({position}, N'{field}', N'{_server_type(kind)[0]}', {_server_type(kind)[1]}, {0 if required else 1})"
                         for position, (field, kind, required) in enumerate(columns, start=1))
    differs = [f"(SELECT COUNT(*) FROM sys.columns c WHERE c.object_id = OBJECT_ID({quoted})) <> {len(columns)}",
               f"(SELECT COUNT(*) FROM sys.columns c JOIN (VALUES {expected}) AS d (column_id, name, type_name, max_length, is_nullable)\n"
               f"         ON c.column_id = d.column_id AND c.name = d.name AND TYPE_NAME(c.user_type_id) = d.type_name\n"
               f"        AND c.max_length = d.max_length AND c.is_nullable = d.is_nullable\n"
               f"      WHERE c.object_id = OBJECT_ID({quoted})) <> {len(columns)}"]
    if key:
        differs.insert(0, f"OBJECT_ID(N'{own}.[xpk_{table}]', N'PK') IS NULL")
    definitions = [f"    [{field}] {kind}{' NOT NULL' if required else ' NULL'}" for field, kind, required in columns]
    if key:
        definitions.append(f"    CONSTRAINT [xpk_{table}] PRIMARY KEY ([{key}])")
    return [f"IF OBJECT_ID({quoted}, N'U') IS NOT NULL AND (\n    " + "\n    OR ".join(differs) + ")",
            f"    DROP TABLE {name};",
            f"IF OBJECT_ID({quoted}, N'U') IS NULL",
            f"CREATE TABLE {name} (", ",\n".join(definitions), ");"]


def _guards(written, fields, chosen):
    """The checks at the top of the script, which stop it before it changes anything."""
    def throw(number, message):
        return f"    THROW {number}, N'{message.replace(chr(39), chr(39) * 2)}', 1;"

    lines = [f"-- {WORDING['guard']}"]
    for key, name in VARIABLES[:3]:
        value = f"N'$({name})'"
        lines += [f"IF {value} = N'' OR LEN({value}) > 128 OR {value} LIKE N'%[^A-Za-z0-9_]%' OR {value} LIKE N'[0-9]%'",
                  throw(50001, WORDING["guard_names"].format(variable=name))]
    prefix = "N'$(SourcePrefix)'"
    lines += [f"IF {prefix} LIKE N'%[^A-Za-z0-9_.]%' OR {prefix} NOT LIKE N'%_.' OR {prefix} LIKE N'.%' OR {prefix} LIKE N'%..%'\n"
              f"   OR {prefix} LIKE N'[0-9]%' OR {prefix} LIKE N'%.[0-9]%'\n"
              f"   OR LEN({prefix}) - LEN(REPLACE({prefix}, N'.', N'')) NOT BETWEEN 1 AND 3",
              throw(50002, WORDING["guard_prefix"])]
    omop, own, published = (f"UPPER(N'$({name})')" for _, name in VARIABLES[:3])
    lines += [f"IF {omop} = {own} OR {omop} = {published} OR {own} = {published}", throw(50003, WORDING["guard_distinct"])]
    for table in written:
        key = _key(fields[table])
        if key:
            lines += [f"IF (SELECT MAX([{key}]) FROM [$(OmopSchemaName)].[{table}]) >= {chosen['identifier_offset']}",
                      throw(50004, WORDING["guard_identifiers"].format(table=table, offset=chosen["identifier_offset"]))]
    return lines


def _insert(step, sql, written, fields, own, offset):
    """One step as an INSERT into the layer's own table, its identifiers moved into the layer's range.

    A derived step keeps the identifiers it writes, which are those of the rows it is built from.
    """
    table = step["table"].lower()
    where = step["file"]
    text, columns = rewrite(sql, written, table, where, fields)
    key = _key(fields[table])
    if key and key not in columns:
        raise Refused(f"{where}: the step must write {key}, the identifier of {table}")
    renumber = key if step["layer"] == "anaesthesia" else None
    tree = sqlglot.parse_one(text, dialect="tsql")
    name = "with_" if "with_" in tree.arg_types else "with"
    ctes = tree.args.get(name)
    if ctes is not None:
        tree.set(name, None)
    body = tree.sql(dialect="tsql", pretty=True, identify=True, comments=False)
    prefix = (ctes.sql(dialect="tsql", pretty=True, identify=True, comments=False) + "\n") if ctes is not None else ""
    outputs = ", ".join(f"[step].{_bracket(c)} + @base" if c == renumber else f"[step].{_bracket(c)}" for c in columns)
    lines = ["", f"-- {where}"]
    if renumber:
        lines.append(f"SET @base = COALESCE((SELECT MAX([{key}]) FROM {own}.[{table}]), {offset});")
    lines += [f"{_variables(prefix)}INSERT INTO {own}.[{table}] ({', '.join(_bracket(c) for c in columns)})",
              f"SELECT {outputs}", "FROM (", _variables(body), ") AS [step];"]
    return lines


def script(folder, settings=None, mappings=None):
    """The whole release script for the anaesthesia steps of a conversion folder.

    mappings, when given, are the mapping rows the script carries in place of the folder's
    source_to_concept_map.csv: dictionaries keyed by field, or lists in the order of the table's fields.
    Raises Refused when a step, a gate, a mapping row or a setting breaks a rule.
    """
    folder = Path(folder)
    chosen = settings_for(folder, settings)
    custom = _custom(folder)
    steps = _steps(folder, custom)
    gates = _gates(folder)
    written = list(dict.fromkeys(step["table"].lower() for step, _ in steps))
    cdm = _fields()
    fields = dict(cdm)
    for row in custom_rows(custom):
        fields.setdefault(row["table"], []).append(row)
    described = {table["name"]: table["description"] for table in custom}
    for table in written:
        if table not in fields:
            raise Refused(f"{table} is not a table of CDM 5.4 or a custom table")
    layer_tables = [table for table in written if table not in described]
    own, published = "[$(AnaesSchemaName)]", "[$(AnaesPubSchemaName)]"
    offset = chosen["identifier_offset"]
    empty = chosen["on_failure"] == "empty"

    # Every step and gate is checked before anything is written.
    inserts = [_insert(step, sql, written, fields, own, offset) for step, sql in steps]
    # A step's alternatives are checked as strictly as the step, although the script carries only the step's own file.
    for step, _ in steps:
        for name in alternatives(step):
            check_file_name(name)
            path = folder / name
            if not path.is_file():
                raise Refused(f"{name}: the alternative of {step['file']} is not in the conversion folder")
            _insert(dict(step, file=name), decode(path.read_bytes()), written, fields, own, offset)
    checked_gates = [(name, _variables(rewrite(sql, written, where=name)[0])) for name, sql in gates]
    counts = _counts(folder, written)
    mapping_rows = _mapping_rows(_folder_mappings(folder) if mappings is None else mappings, fields[MAPPING_TABLE], own)

    header = WORDING["header"][:3] + [WORDING["header_empty"] if empty else WORDING["header"][3]]
    lines = [f"-- {line}" for line in header] + ["--", f"-- {WORDING['run']}", f"--   {run_command(chosen)}",
                                                 f"-- {WORDING['run_variables']}"]
    for key, name in VARIABLES:
        lines.append(f"--   {name}: {WORDING[key]}")
    lines += ["", ":on error exit", "SET NOCOUNT ON;", "SET XACT_ABORT ON;", ""]
    # The guards are a batch of their own, so that they run, and say what is wrong, even when a value
    # given with -v would stop the rest of the script from being read at all.
    lines += _guards(layer_tables, fields, chosen) + ["GO", "", "DECLARE @base BIGINT;", "DECLARE @broken BIGINT;"]

    lines += ["", "BEGIN TRANSACTION;", "", f"-- {WORDING['stage_1']}", f"-- {WORDING['stage_1_redefine']}"]
    if not empty:
        lines.append(f"-- {WORDING['stage_1_redefine_keep']}")
    for name in ("AnaesSchemaName", "AnaesPubSchemaName"):
        lines.append(f"IF SCHEMA_ID(N'$({name})') IS NULL EXEC(N'CREATE SCHEMA [$({name})]');")
    for table in [MAPPING_TABLE] + layer_tables:
        lines += _create_table(table, fields[table], chosen, own)
        if table == MAPPING_TABLE:
            continue    # the mapping rows are read by the steps and are not published
        names = ", ".join(f"[{row['field']}]" for row in fields[table])
        lines.append(f"EXEC(N'CREATE OR ALTER VIEW {published}.[{table}] AS "
                     f"SELECT {names} FROM [$(OmopSchemaName)].[{table}] UNION ALL SELECT {names} FROM {own}.[{table}]');")
    if described:
        lines.append(f"-- {WORDING['stage_1_custom']}")
    for table in written:
        if table not in described:
            continue
        lines.append(f"-- {table}: {described[table]}")
        lines += _create_table(table, fields[table], chosen, own)
        names = ", ".join(f"[{row['field']}]" for row in fields[table])
        lines.append(f"EXEC(N'CREATE OR ALTER VIEW {published}.[{table}] AS SELECT {names} FROM {own}.[{table}]');")
    lines.append(f"-- {WORDING['stage_1_complete']}")
    for table, rows in cdm.items():
        if table not in written:
            names = ", ".join(f"[{row['field']}]" for row in rows)
            lines.append(f"EXEC(N'CREATE OR ALTER VIEW {published}.[{table}] AS SELECT {names} FROM [$(OmopSchemaName)].[{table}]');")

    lines += ["", f"-- {WORDING['stage_2']}"]
    lines += [f"DELETE FROM {own}.[{table}];" for table in reversed(written)]
    lines.append(f"DELETE FROM {own}.[{MAPPING_TABLE}];")
    if empty:
        lines += [f"-- {WORDING['stage_2_empty']}", "COMMIT TRANSACTION;", "BEGIN TRANSACTION;"]
    lines += mapping_rows

    lines += ["", f"-- {WORDING['stage_3']}", f"-- {WORDING['stage_3_identifiers'].format(offset=offset)}"]
    for (step, _), insert in zip(steps, inserts):
        if step["layer"] == "derived" and step is next(s for s, _ in steps if s["layer"] == "derived"):
            lines += ["", f"-- {WORDING['stage_3_derived']}"]
        lines += insert

    lines += ["", f"-- {WORDING['stage_4']}"]
    failed = WORDING["gate_failed_empty" if empty else "gate_failed"]
    for name, gate in checked_gates:
        message = failed.format(name=Path(name).stem).replace("'", "''")
        # The script counts the rows that a gate lists. It does not ask whether any row exists, because SQL Server
        # then plans for a row that turns up early, and a gate that passes lists none, so that plan reads every
        # row many times over. On 83,000 readings the count took a tenth of a second and the other form a minute.
        lines += ["", f"-- {name}", "SET @broken = (SELECT COUNT_BIG(*) FROM (", gate, ") AS gate);", "IF @broken > 0", "BEGIN",
                  "    SELECT TOP (50) * FROM (", gate, "    ) AS gate;", f"    THROW 50000, N'{message}', 1;", "END;"]

    lines += ["", "COMMIT TRANSACTION;", "", f"-- {WORDING['stage_5']}"]
    lines.append("\nUNION ALL\n".join(
        f"SELECT '{table}' AS table_name, COUNT(*) AS rows_written FROM {own}.[{table}]" for table in written) + ";")
    if counts:
        lines += ["", f"-- {WORDING['stage_5_counts']}"]
        for name, statement in counts:
            lines += [f"-- counts/{name}", statement]
    text = "\n".join(lines) + "\n"
    # sqlcmd substitutes only the four variables, and reads no other line as a command of its own.
    allowed = {name for _, name in VARIABLES}
    stray = set(re.findall(r"\$\(([^)]*)\)", text)) - allowed
    if stray or [line for line in text.splitlines() if SQLCMD_LINE.match(line)] != [":on error exit", "GO"]:
        raise Refused("the script holds a sqlcmd command or variable that it did not put there itself")
    return text


def source_manifest(folder, catalogue):
    """The source tables and columns that the anaesthesia steps read, as CSV text."""
    request = as_request([(step["table"], sql) for step, sql in _steps(folder)])
    findings = analyse_request(request, catalogue, []).findings
    columns = sorted({(f[1], f[2]) for f in findings if f[0] == "column"})
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["table_name", "column_name"])
    writer.writerows(columns)
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.release")
    parser.add_argument("conversion", type=Path)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        text = script(args.conversion)
        manifest = source_manifest(args.conversion, Catalogue.from_csv(decode(args.catalogue.read_bytes())))
    except Refused as error:
        print(WORDING["not_written"].format(reason=error), file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "release.sql").write_text(text, encoding="utf-8")
    (args.out / "source_manifest.csv").write_text(manifest, encoding="utf-8")
    print(f"release.sql holds the {LAYERS[1]} and {LAYERS[2]} steps, and source_manifest.csv lists {manifest.count(chr(10)) - 1} source columns")
    print(WORDING["run_with"])
    print(f"  {run_command(settings_for(args.conversion))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
