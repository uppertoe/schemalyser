"""Runs a conversion to OMOP over the sandbox, checks what it writes, and exports it.

A conversion is a folder holding

    conversion.json           the steps in order: [{"table": "person", "file": "person.sql", "layer": "core",
                              "route": "direct", "reference": ..., "reason": ..., "review": {"by": ..., "on": ...}}, ...]
    *.sql                     one SELECT for each step, in T-SQL, naming its columns as the OMOP fields
    source_to_concept_map.csv optional mapping rows, in the layout of the OMOP table of that name

    tables.json               optional custom tables, which the derived layer writes beside the CDM tables
    intents.json              optional: what each join, filter and vocabulary of the steps is meant to do
    gates/*.sql               optional: one SELECT each of the rows that break a rule, so that no rows means it passes
    counts/*.sql              optional: one SELECT each of a single count, such as the anaesthetics that the layer
                              leaves out, whose first line is the sentence that reports it, with {count} for the number

A step may also list "alternatives": other single-SELECT files that make the same rows by another
route. The checklist of a target query weighs them against the sample queries, and at the boundary a
step that reads what the catalogue does not hold gives way to the first alternative that reads only
what it holds (routes.py); the runner and the release script ignore them, unless a run is given
--alternative FILE to use one in its step's place.

Each SELECT reads the source tables, and may read OMOP tables already written as omop.<table>.

Each step of the core and anaesthesia layers records its route, as docs/contract.md's layer 3 requires. A step on the
route "roles" is written over the role views and the mapping views, and reads OMOP tables already written; a step on
the route "direct" is written from the hospital's source tables, and records the reference it rests on by name, the
reason it takes that route, and its review, {"by": who accepted it, "on": the date}, without which the release script
refuses it (route_problems). A derived step reads only the OMOP tables, so it takes neither route and records none.
An alternative given as {"file": ..., "route": ...} records its own route in the same way, and one given as a bare
file name takes its step's. A step over the roles runs compiled through the hospital schema, the map folder beside the conversion folder
(hospital_schema), as the release script compiles it: each role view and mapping view that it reads becomes a common
table expression over the source tables (compiled_steps). A direct step may name, as "roles_step", a step over the
roles that makes the same rows, which waits beside it until the world's map binds the parts it reads; it is kept apart
from the alternatives, and its role scenarios run on the role shadow (run_role_scenarios), as those of every step
over the roles do. draft.json, where the folder holds it, marks the conversion as a draft, such as
one that transplant.py wrote from a reference, and the runner and the release script report it as one.

A planted scenario keeps its inputs beside the conversion, in scenarios/<name>/rows.sql with a scenario.json that
describes them, and its expected rows apart, in the held-out root: held-out/<conversion folder's name>/scenarios/<name>/
expected.json beside the conversion folder (held_out_root). The expected rows are written by hand from the clinical
description and never from a run, and no code path writes into the held-out root. A folder without a held-out root,
such as a private twin, may keep its expectations in scenario.json; a copy of the invented world's conversion reads
the invented world's held-out root for the scenarios whose planted rows it holds unchanged.

Each step belongs to a layer. The core layer stands in for an OMOP database that someone else
maintains: people, visits and the other tables the anaesthesia steps rely on. It exists so that
the whole pipeline can be tested here, and it is not meant to be run against a real database.
The anaesthesia layer is the part that is: it finds its people and visits in the OMOP tables and
never writes a table that belongs to the core alone. The derived layer runs last. It reads only
the OMOP tables, and each of its steps writes a custom table that tables.json defines, such as
one row for each anaesthetic, so that the tables of CDM 5.4 stay as the model defines them. A
custom table is read as omop.<table>, like any other.
The runner creates the OMOP tables of CDM 5.4 from the published field list, inserts each step's
rows, checks them against that list, and writes one CSV file for each table that holds rows.

    python -m schemalyser.convert WORLD CONVERSION --out OUT [--rows 500] [--checks CHECKS.csv] [--alternative FILE]

The world supplies the catalogue, the requests and the stand-in database, as for the harness.
The conversion's own SQL is analysed with the requests, so that the tables it needs are built.
With --checks, the sandbox is built from those check results, such as a hospital's own, instead
of from answers computed over the stand-in database.

Each anaesthesia step numbers its own rows from 1, and the runner moves them into the layer's
range: it adds the identifier offset for the first step that writes a table, and the highest
identifier that the layer has already written there for each later step, as the release script does.

The command exits with 1 when a step does not run cleanly, a gate fails or cannot be run, or any
problem is reported, so that a conversion that writes nothing where it should cannot pass unnoticed.
"""
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from . import harness, mapping
from .catalogue import Catalogue
from .extract import _passed_through, decode
from .sandbox import Sandbox
from .translate import OMOP_SCHEMA, Unreadable, Unsupported, to_duckdb

FIELDS = Path(__file__).parent / "omop" / "cdm54_fields.csv"
# The tables a conversion writes clinical rows into. The vocabulary tables are checked by their publisher.
CLINICAL_TABLES = {"person", "observation_period", "visit_occurrence", "visit_detail", "condition_occurrence",
                   "drug_exposure", "procedure_occurrence", "device_exposure", "measurement", "observation", "death",
                   "note", "specimen", "provider", "care_site", "episode"}
LAYERS = ("core", "anaesthesia", "derived")
# The tables that only the core layer may write. The anaesthesia layer reads them and adds rows to the others.
CORE_ONLY_TABLES = {"person", "observation_period", "visit_occurrence", "death", "provider", "care_site", "location", "cdm_source"}
# The anaesthesia layer's identifiers begin just above this number, which every identifier of the core must stay below.
IDENTIFIER_OFFSET = 5_000_000_000
FILE_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# The lines that a run adds to its report, in the voice of the release script. They await the clinical lead's approval.
WORDING = {
    "file_name": "{name}: the runner has not read this step, because a file name may hold only letters, digits, full stops, hyphens and underscores.",
    "unreadable": "{table}: the analysis could not read this step as one SELECT, so it took the step as it stands, and the sandbox may lack tables or values that the step reads.",
    "derived_missing": "{vocabulary}: the runner has derived no mapping rows, because the catalogue does not hold {absent}.",
    "derived_unreadable": "{vocabulary}: the runner has derived no mapping rows, because it could not read the labels from {table} ({error}).",
    "core_fields": "{table}: the core's table lacks {fields}, which the published views of CDM 5.4 read.",
    "core_identifier": "{table}.{key}: the core already holds the identifier {highest}, at or above {offset}, where the anaesthesia layer's identifiers begin.",
    "not_checked": "The runner could not check the written tables against the CDM ({error}).",
    "not_clean": "The conversion has not run cleanly, for the {count} reasons listed above, so the command exits with 1.",
    # Awaiting approval: the derived layer, the custom tables and the measurements without a mapping row.
    "layer_order": "{name}: this {layer} step follows a {later} step, and the layers run in the order core, anaesthesia, derived.",
    "derived_table": "{name}: a derived step writes a custom table that tables.json defines, and {table} is not one of them.",
    "custom_written": "{name}: only a derived step may write {table}, because it is a custom table.",
    # The alternatives that a step may offer, which the runner uses only when it is asked to.
    "alternatives_list": "{name}: the step's alternatives are a list of file names.",
    "alternative_same": "{name}: an alternative is a different file from the step itself, named once.",
    "alternative_entry": "{name}: an alternative is a file name, or an entry with a file and, if given, the step's own table and layer.",
    "alternative_unknown": "{name} is not an alternative of any step in conversion.json.",
    "unmapped": "The measurement table holds {rows} rows from {variables} variables that have no mapping row. Each of these rows has the concept 0 until a mapping row is written for its variable.",
    "worklist": "The file unmapped_measurements.csv lists those variables by their source identifier, as a worklist for the mapping rows, and it is for use inside the hospital only.",
    # Awaiting approval: the planted scenarios.
    "scenario_planted": "The runner planted {rows} source rows for {count} scenarios before the core layer ran.",
    "scenario_met": "{scenario}: the expectation is met. {says}",
    "scenario_not_met": "{scenario}: the expectation is not met. {says} The query gave {found}, and the scenario expects {expected}.",
    "scenario_not_run": "{scenario}: the runner could not evaluate an expectation, because {reason}. {says}",
    "scenario_not_planted": "{scenario}: the runner has not planted this scenario, because {reason}.",
    "scenario_gate": "{scenario}: this scenario exists to make the gate {gate} fail, so it runs only when it is named.",
    # The conversion's counts, such as the anaesthetics that the layer leaves out.
    "count_not_run": "{name}: the runner could not run this count ({reason}).",
    # A step over the roles, which runs compiled through the hospital schema.
    "no_schema": "{name}: the step is written over the roles, and the runner has found no hospital schema through which to compile the role views that it reads. The schema is the map folder beside the conversion folder, or one given to the runner.",
}
# The types that a field of a custom table may have, and how each is held in the sandbox.
CUSTOM_TYPES = re.compile(r"integer|float|date|datetime|varchar\(([1-9][0-9]{0,3})\)")
CUSTOM_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
TABLES_FILE = "tables.json"
COUNTS_FOLDER = "counts"
# The placeholder in a count's sentence where the number goes.
COUNT_MARK = "{count}"



# The mapping rows that a person confirmed for a site, which a conversion folder may hold beside its own.
SITE_MAPPINGS = "site_mappings.csv"


def merged_mappings(own_rows, site_rows):
    """The conversion's own mapping rows with the site's added, where the site's row wins for the same code and vocabulary."""
    key = lambda row: (str(row.get("source_vocabulary_id") or "").upper(), str(row.get("source_code") or ""))  # noqa: E731
    site = {key(row) for row in site_rows}
    return [row for row in own_rows if key(row) not in site] + list(site_rows)


def mapping_dicts(folder):
    """The mapping rows of a conversion folder, as dictionaries: its own source_to_concept_map.csv, with the rows
    of site_mappings.csv, which a person confirmed, added. Where both give a row for the same source code under
    the same vocabulary, the site's row wins."""
    found = {}
    for name in ("source_to_concept_map.csv", SITE_MAPPINGS):
        path = Path(folder) / name
        found[name] = list(csv.DictReader(io.StringIO(decode(path.read_bytes())))) if path.exists() else []
    return merged_mappings(found["source_to_concept_map.csv"], found[SITE_MAPPINGS])


def mapping_text(folder):
    """The same mapping rows as CSV text, or "" where the folder holds none."""
    rows = mapping_dicts(folder)
    if not rows:
        return ""
    fields = list(dict.fromkeys(name for row in rows for name in row))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()

class TablesError(ValueError):
    """tables.json does not define its custom tables in the form that the runner and the release script accept."""


def _sentence(value, where):
    """A description: one line of text, with nothing that sqlcmd would read as a command or a variable."""
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise TablesError(f"{where}: a description is one sentence of text, of at most 500 characters")
    if "\r" in value or "\n" in value or "$(" in value:
        raise TablesError(f"{where}: a description may not hold a line break or $(")
    return value


def read_tables(folder):
    """The custom tables that a conversion folder's tables.json defines, checked, or [] when it has none.

    Each table is {"name", "description", "fields": [{"name", "type", "required", "primary_key", "description"}]}.
    A name is a plain identifier in lower case and is not the name of a table of CDM 5.4. A type is
    integer, float, date, datetime or varchar(n), with n from 1 to 8000. A table has at most one
    primary key field, which is required. Raises TablesError on anything else.
    """
    path = Path(folder) / TABLES_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(decode(path.read_bytes()))
    except ValueError:
        raise TablesError(f"{TABLES_FILE} cannot be read as JSON") from None
    if not isinstance(data, list):
        raise TablesError(f"{TABLES_FILE} holds a list of tables")
    cdm = set(cdm_fields())
    tables, seen = [], set()
    for position, table in enumerate(data, start=1):
        where = f"{TABLES_FILE}, table {position}"
        if not isinstance(table, dict) or set(table) != {"name", "description", "fields"}:
            raise TablesError(f"{where}: a table has exactly a name, a description and its fields")
        name = table["name"]
        if not isinstance(name, str) or not CUSTOM_NAME.fullmatch(name):
            raise TablesError(f"{where}: a table name is a letter or underscore followed by up to 62 lower-case letters, digits and underscores")
        if name in cdm or name in seen:
            raise TablesError(f"{where}: {name} is already the name of a table of CDM 5.4 or of another custom table")
        seen.add(name)
        fields, names = [], set()
        if not isinstance(table["fields"], list) or not table["fields"]:
            raise TablesError(f"{where}: a table has at least one field")
        for field in table["fields"]:
            if not isinstance(field, dict) or set(field) != {"name", "type", "required", "primary_key", "description"}:
                raise TablesError(f"{where}: a field has exactly a name, a type, required, primary_key and a description")
            if not isinstance(field["name"], str) or not CUSTOM_NAME.fullmatch(field["name"]) or field["name"] in names:
                raise TablesError(f"{where}: a field name is a plain name in lower case, given once")
            names.add(field["name"])
            if not isinstance(field["type"], str) or not CUSTOM_TYPES.fullmatch(field["type"]) or (
                    field["type"].startswith("varchar") and int(field["type"][8:-1]) > 8000):
                raise TablesError(f"{where}, {field['name']}: the type is integer, float, date, datetime or varchar(n), with n from 1 to 8000")
            if not isinstance(field["required"], bool) or not isinstance(field["primary_key"], bool):
                raise TablesError(f"{where}, {field['name']}: required and primary_key are true or false")
            if field["primary_key"] and not field["required"]:
                raise TablesError(f"{where}, {field['name']}: a primary key field is required")
            fields.append({key: field[key] for key in ("name", "type", "required", "primary_key")}
                          | {"description": _sentence(field["description"], f"{where}, {field['name']}")})
        if sum(field["primary_key"] for field in fields) > 1:
            raise TablesError(f"{where}: a table has at most one primary key field")
        tables.append({"name": name, "description": _sentence(table["description"], where), "fields": fields})
    return tables


def custom_rows(tables):
    """The fields of custom tables in the layout of the CDM field list: table, field, required, datatype, primary_key."""
    return [{"table": table["name"], "field": field["name"], "required": "Y" if field["required"] else "N",
             "datatype": field["type"], "primary_key": "Y" if field["primary_key"] else "N", "concept_domain": ""}
            for table in tables for field in table["fields"]]


def layer_problems(steps, custom=None):
    """What a list of steps breaks in the rule of layers.

    custom, when given, is the set of custom table names: only a derived step may write one, and a
    derived step may write nothing else.
    """
    found = []
    layers = [step.get("layer") for step in steps]
    if "core" in layers and "anaesthesia" in layers and layers.index("anaesthesia") < len(layers) - 1 - layers[::-1].index("core"):
        found.append("a core step follows an anaesthesia step: the core is refreshed first, and knows nothing of the anaesthesia layer")
    latest = None
    for step in steps:
        layer = step.get("layer")
        name = step.get("file")
        if not isinstance(name, str) or not FILE_NAME.fullmatch(name) or set(name) == {"."}:
            found.append(WORDING["file_name"].format(name=repr(name)))
            continue
        if layer not in LAYERS:
            found.append(f"{step['file']}: the step names no layer, and must be one of {', '.join(LAYERS[:-1])} or {LAYERS[-1]}")
            continue
        if latest is not None and LAYERS.index(layer) < LAYERS.index(latest) and not (layer == "core" and latest == "anaesthesia"):
            found.append(WORDING["layer_order"].format(name=name, layer=layer, later=latest))
        if latest is None or LAYERS.index(layer) > LAYERS.index(latest):
            latest = layer
        table = str(step.get("table", "")).lower()
        if layer == "anaesthesia" and table in CORE_ONLY_TABLES:
            found.append(f"{step['file']}: an anaesthesia step may not write {step['table']}, which belongs to the core")
        if custom is not None:
            if layer == "derived" and table not in custom:
                found.append(WORDING["derived_table"].format(name=name, table=table))
            elif layer != "derived" and table in custom:
                found.append(WORDING["custom_written"].format(name=name, table=table))
        found += _alternative_problems(step)
        if ROLES_STEP in step:
            named = step[ROLES_STEP]
            if not isinstance(named, str) or not FILE_NAME.fullmatch(named) or set(named) == {"."}:
                found.append(WORDING["file_name"].format(name=repr(named)))
            elif named == name or named in alternatives(step) or layer == "derived":
                found.append(f"{name}: its step over the roles is a file of its own, apart from the step and its "
                             f"alternatives, and a derived step names none")
    return found


def _alternative_problems(step):
    """What a step's optional list of alternatives breaks: each is another file for the same table and layer."""
    name, offered = step.get("file"), step.get("alternatives")
    if offered is None:
        return []
    if not isinstance(offered, list):
        return [WORDING["alternatives_list"].format(name=name)]
    found, seen = [], {name}
    for item in offered:
        if isinstance(item, dict):
            if not set(item) <= {"file", "table", "layer", "effect", *ROUTE_FIELDS} or str(item.get("table", step.get("table"))).lower() != \
                    str(step.get("table")).lower() or item.get("layer", step.get("layer")) != step.get("layer"):
                found.append(WORDING["alternative_entry"].format(name=name))
                continue
            item = item.get("file")
        if not isinstance(item, str) or not FILE_NAME.fullmatch(item) or set(item) == {"."}:
            found.append(WORDING["file_name"].format(name=repr(item)))
        elif item in seen:
            found.append(WORDING["alternative_same"].format(name=name))
        seen.add(item)
    return found


# The routes of a step, as the contract's layer 3 names them.
ROUTES = ("roles", "direct")
ROUTE_FIELDS = ("route", "reference", "reason", "review")
ROLES_STEP = "roles_step"
DRAFT_FILE = "draft.json"
HELD_OUT = "held-out"
EXPECTED_FILE = "expected.json"
ROLE_SCENARIOS = "role_scenarios"
INVENTED_CONVERSION = Path(__file__).resolve().parents[2] / "fixtures" / "conversion"
ROUTE_WORDING = {
    "no_route": "{name}: conversion.json records no route for this step. A step that reads the hospital's record is written "
                "over the roles or directly from the source tables, and its entry says which, as \"route\": \"roles\" or "
                "\"direct\".",
    "bad_route": "{name}: the route is \"roles\" or \"direct\".",
    "direct_needs": "{name}: this step is written directly from the source tables, and conversion.json does not record {what}, "
                    "so the release cannot carry it. A direct step records the reference it rests on, the reason it takes that "
                    "route, and the review that accepted it.",
    "text": "{name}: the {field} is one line of text, of at most 400 characters, with no $(.",
    "review": "{name}: the review is {{\"by\": who accepted the step, \"on\": the date as YYYY-MM-DD}}.",
    "derived": "{name}: a derived step reads only the OMOP tables, so it takes neither route and records none.",
    "draft": "This conversion is a draft{source}, so no step of it has been accepted for a release.",
    "shares": "Of the {count} steps that read the hospital's record, {roles} {roles_verb} written over the roles and {direct} "
              "{direct_verb} written directly from the source tables.",
}


def _one_line(value):
    return isinstance(value, str) and value.strip() and len(value) <= 400 and "\n" not in value and "\r" not in value \
        and "$(" not in value


def _route_record_problems(entry, name, inherited=None):
    """What one route record breaks: an entry of conversion.json, or an alternative given as an entry of its own."""
    route = entry.get("route", (inherited or {}).get("route"))
    if route is None:
        return [ROUTE_WORDING["no_route"].format(name=name)]
    if route not in ROUTES:
        return [ROUTE_WORDING["bad_route"].format(name=name)]
    if route != "direct":
        return []
    record = entry if "route" in entry else (inherited or {})
    found, absent = [], []
    for field in ("reference", "reason"):
        if field not in record or record[field] in (None, ""):
            absent.append(f"the {field}")
        elif not _one_line(record[field]):
            found.append(ROUTE_WORDING["text"].format(name=name, field=field))
    review = record.get("review")
    if not review:
        absent.append("the review")
    elif not isinstance(review, dict) or not _one_line(review.get("by")) or not isinstance(review.get("on"), str) \
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", review["on"]) or not set(review) <= {"by", "on", "note"} \
            or ("note" in review and not _one_line(review["note"])):
        found.append(ROUTE_WORDING["review"].format(name=name))
    if absent:
        what = absent[0] if len(absent) == 1 else ", ".join(absent[:-1]) + " or " + absent[-1]
        found.insert(0, ROUTE_WORDING["direct_needs"].format(name=name, what=what))
    return found


def route_problems(steps):
    """What the steps break in the rule of routes, one sentence for each, naming its step or alternative.

    Every step of the core and anaesthesia layers records its route. A direct step records its reference, its reason and
    its review. A derived step records none. An alternative given as an entry records its own route or takes its step's.
    """
    found = []
    for step in steps:
        name = step.get("file")
        if step.get("layer") == "derived":
            if any(field in step for field in ROUTE_FIELDS):
                found.append(ROUTE_WORDING["derived"].format(name=name))
            continue
        found += _route_record_problems(step, name)
        for item in step.get("alternatives") or []:
            if isinstance(item, dict) and "route" in item:
                found += _route_record_problems(item, item.get("file"))
    return found


def route_of(step, alternative=None):
    """The route of a step, or of one of its alternatives or its step over the roles by file name: "roles", "direct", or
    None for a derived step."""
    if step.get("layer") == "derived":
        return None
    if alternative is not None and alternative == step.get(ROLES_STEP):
        return "roles"
    for item in step.get("alternatives") or []:
        if alternative is not None and isinstance(item, dict) and item.get("file") == alternative and "route" in item:
            return item["route"]
    return step.get("route")


def route_shares(steps, layers=("core", "anaesthesia")):
    """How many of the steps of the given layers take each route: {"steps", "roles", "direct", "unrecorded", "sentence"}."""
    chosen = [step for step in steps if step.get("layer") in layers]
    roles = sum(1 for step in chosen if step.get("route") == "roles")
    direct = sum(1 for step in chosen if step.get("route") == "direct")
    count = len(chosen)
    sentence = ROUTE_WORDING["shares"].format(count=count, roles=roles, direct=direct,
                                              roles_verb="is" if roles == 1 else "are", direct_verb="is" if direct == 1 else "are")
    return {"steps": count, "roles": roles, "direct": direct, "unrecorded": count - roles - direct,
            "share_roles": round(roles / count, 3) if count else None,
            "share_direct": round(direct / count, 3) if count else None, "sentence": sentence}


def roles_steps(steps):
    """Each step or alternative on the route over the roles: [{"file", "table", "layer", "alternative_of"}]."""
    found = []
    for step in steps:
        if step.get("layer") != "derived" and step.get("route") == "roles":
            found.append({"file": step["file"], "table": step["table"], "layer": step["layer"], "alternative_of": None})
        for item in step.get("alternatives") or []:
            if isinstance(item, dict) and item.get("route") == "roles":
                found.append({"file": item["file"], "table": step["table"], "layer": step["layer"], "alternative_of": step["file"]})
        if step.get("layer") != "derived" and isinstance(step.get(ROLES_STEP), str):
            found.append({"file": step[ROLES_STEP], "table": step["table"], "layer": step["layer"], "alternative_of": step["file"]})
    return found


def read_draft(folder):
    """The folder's draft.json, which marks the conversion as a draft, or None where the folder holds none."""
    path = Path(folder) / DRAFT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(decode(path.read_bytes()))
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def draft_sentence(draft):
    """The sentence that reports a draft, naming where it came from where draft.json says."""
    source = draft.get("reference") if isinstance(draft, dict) else None
    return ROUTE_WORDING["draft"].format(source=f", transplanted from {source}" if _one_line(source) else "")


# The folder of the hospital schema beside a conversion folder, as the invented world keeps fixtures/map beside
# fixtures/conversion.
HOSPITAL_SCHEMA = "map"


def hospital_schema(folder):
    """The map folder of the hospital schema through which a conversion's steps over the roles are compiled, or None.

    It is the map folder beside the conversion folder, where there is one. A copy of the invented world's conversion
    elsewhere, whose steps over the roles are the invented world's own files unchanged, uses the invented world's map,
    as such a copy reads the invented world's held-out root."""
    folder = Path(folder).resolve()
    own = folder.parent / HOSPITAL_SCHEMA
    if (own / "map.json").is_file():
        return own
    invented = INVENTED_CONVERSION.parent / HOSPITAL_SCHEMA
    try:
        steps = json.loads((folder / "conversion.json").read_text())
    except (OSError, ValueError):
        return None
    # Only a step or an alternative that runs over the roles is compiled; one that waits beside a direct step is not.
    names = [entry["file"] for entry in roles_steps(steps) if isinstance(entry.get("file"), str)
             and not any(step.get(ROLES_STEP) == entry["file"] for step in steps)]
    if names and (invented / "map.json").is_file() and all(
            FILE_NAME.fullmatch(name) and (folder / name).is_file() and (INVENTED_CONVERSION / name).is_file()
            and (folder / name).read_bytes() == (INVENTED_CONVERSION / name).read_bytes() for name in names):
        return invented
    return None


def held_out_root(folder):
    """The held-out root of a conversion folder: held-out/<the folder's name> beside it. It is only ever read."""
    folder = Path(folder).resolve()
    return folder.parent / HELD_OUT / folder.name


def _read_held_out(path):
    """One held-out file, opened for reading only, as JSON."""
    with open(path, "rb") as f:
        return json.loads(decode(f.read()))


def _held_out_for(folder, kind, name):
    """The held-out expected file for one scenario, or None. kind is SCENARIOS or ROLE_SCENARIOS.

    The folder's own held-out root comes first. A copy of the invented world's conversion, whose planted inputs for the
    scenario are unchanged, reads the invented world's held-out root, so that a test may work on a copy."""
    own = held_out_root(folder) / kind / name / EXPECTED_FILE
    if own.is_file():
        return own
    invented = held_out_root(INVENTED_CONVERSION) / kind / name / EXPECTED_FILE
    planted = "rows.sql" if kind == SCENARIOS else "rows.json"
    mine, theirs = Path(folder) / kind / name / planted, INVENTED_CONVERSION / kind / name / planted
    if invented.is_file() and mine.is_file() and theirs.is_file() and mine.read_bytes() == theirs.read_bytes():
        return invented
    return None


def alternatives(step):
    """The file names of a step's alternatives, in the order that conversion.json gives them."""
    return [item.get("file") if isinstance(item, dict) else item for item in step.get("alternatives") or []]


def with_alternatives(steps, names):
    """The steps with each named alternative in place of its step's own file. Raises ValueError for a name that is not
    offered, or for an alternative over the roles, which the runner over the source tables cannot run."""
    names = list(names or [])
    waiting = [name for name in names if any(step.get(ROLES_STEP) == name for step in steps)]
    if waiting:
        raise ValueError(f"{waiting[0]} is written over the roles, which the runner over the source tables cannot run; "
                         f"its role scenarios run on the role shadow instead")
    chosen = []
    for step in steps:
        picked = [name for name in names if name in alternatives(step)]
        if picked and route_of(step, picked[0]) == "roles":
            raise ValueError(f"{picked[0]} is written over the roles, which the runner over the source tables cannot run; "
                             f"its role scenarios run on the role shadow instead")
        # An alternative runs on its own route, which may differ from its step's, as a direct alternative of a step over
        # the roles does.
        chosen.append(dict(step, file=picked[0], route=route_of(step, picked[0])) if picked else step)
    offered = {name for step in steps for name in alternatives(step)}
    unknown = [name for name in names if name not in offered]
    if unknown:
        raise ValueError("; ".join(WORDING["alternative_unknown"].format(name=name) for name in unknown))
    return chosen


# The tables that are exported. The vocabulary tables come from Athena and are never written by a conversion.
EXPORTED_TABLES = CLINICAL_TABLES | {"source_to_concept_map", "cdm_source", "location"}
DUCK_TYPES = {"integer": "BIGINT", "float": "DOUBLE", "date": "DATE", "datetime": "TIMESTAMP"}


def cdm_fields():
    """The tables of CDM 5.4: table -> [(field, required, DuckDB type)], in the published order."""
    tables = {}
    with open(FIELDS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            kind = DUCK_TYPES.get(row["datatype"], "VARCHAR")
            tables.setdefault(row["table"], []).append((row["field"], row["required"] == "Y", kind))
    return tables


def mapped_columns(tree):
    """The source columns that a step looks up in the mapping table, as (table, column) pairs.

    A conversion can only map the values it knows about, so each such column has its values
    listed by the checks, under the same rules as any other column.
    """
    def own(select, kind):
        """The nodes of a kind that belong to this SELECT itself, and not to a query nested inside it."""
        return [node for node in select.find_all(kind) if node is not select and node.find_ancestor(exp.Select) is select]

    def scopes(select):
        """This SELECT and each SELECT around it, whose tables a correlated reference may name."""
        while select is not None:
            yield select
            select = select.find_ancestor(exp.Select)

    def source(select, column):
        """The source table behind a column of this SELECT, looking through one derived table and out to enclosing queries."""
        for scope in scopes(select):
            for table in own(scope, exp.Table):
                if table.alias_or_name.upper() == column.table.upper():
                    return None if (table.db or "").upper() == OMOP_SCHEMA.upper() else (table.name, column.name)
            for subquery in own(scope, exp.Subquery):
                if subquery.alias.upper() == column.table.upper():
                    inner = subquery.find(exp.Select)
                    for projection in inner.expressions if inner else []:
                        carried = _passed_through(projection.unalias())
                        if projection.alias_or_name.upper() == column.name.upper() and carried is not None and carried.table:
                            return source(inner, carried)
                    return None
        return None

    found = []
    for select in tree.find_all(exp.Select):
        mapping_aliases = {t.alias_or_name.upper() for scope in scopes(select) for t in own(scope, exp.Table)
                           if (t.db or "").upper() == OMOP_SCHEMA.upper() and t.name.lower() == "source_to_concept_map"}
        for comparison in own(select, exp.EQ):
            sides = [_passed_through(side) for side in (comparison.this, comparison.expression)]
            if any(side is None or not side.table for side in sides):
                continue
            for code, other in (sides, sides[::-1]):
                if code.name.lower() == "source_code" and code.table.upper() in mapping_aliases:
                    origin = source(select, other)
                    if origin and origin not in found:
                        found.append(origin)
    return found


def _bracket(name):
    return "[" + str(name).replace("]", "]]") + "]"


def as_request(steps, definitions=None, problems=None):
    """The whole conversion as one request, for the analysis.

    Each step is rewritten as SELECT ... INTO a temp table that stands for its OMOP table, and each
    reading of an OMOP table reads that temp table. The analysis then follows a key through an OMOP
    table back to the source column it was copied from, so the sandbox gives both the same values.
    A step that cannot be read as one SELECT is passed through as it stands, and when a list is
    given as problems, a line saying so is added to it.
    """
    from . import memo
    key = memo.digest([(str(table), str(sql)) for table, sql in steps], sorted((definitions or {}).items()))
    text, unreadable = memo.remembered("as_request", key, lambda: _as_request(steps, definitions))
    if problems is not None:
        problems.extend(unreadable)
    return text


def _as_request(steps, definitions=None):
    """as_request's text, with the lines that say which steps could not be read as one SELECT."""
    problems = []
    stand_in = lambda name: exp.Table(this=exp.to_identifier(f"#omop_{name.lower()}"))  # noqa: E731
    parts, lookups = [], []
    for table, sql in steps:
        try:
            tree = sqlglot.parse_one(sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            tree = None
        if not isinstance(tree, exp.Select):
            problems.append(WORDING["unreadable"].format(table=table))
            parts.append(sql)
            continue
        for source_table, source_column in mapped_columns(tree):
            defined = (definitions or {}).get(source_column.upper())
            # Where the site rules give the column a definition table, the check reads its labels
            # from that table, so the table is joined here and is built with the rest.
            # Each name is bracketed with any closing bracket doubled, so that a name taken from a step cannot end its brackets.
            joined = f" JOIN {_bracket(defined[0])} AS d ON d.{_bracket(defined[1])} = x.{_bracket(source_column)}" if defined else ""
            lookups.append(f"SELECT 1 AS mapped FROM {_bracket(source_table)} AS x{joined} WHERE x.{_bracket(source_column)} IN ('A')")
        for found in list(tree.find_all(exp.Table)):
            if (found.db or "").upper() == OMOP_SCHEMA.upper():
                replacement = stand_in(found.name)
                if found.alias:
                    replacement.set("alias", found.args["alias"])
                found.replace(replacement)
        tree.set("into", exp.Into(this=stand_in(table)))
        parts.append(tree.sql(dialect="tsql", comments=False))
    return ";\n".join(parts + list(dict.fromkeys(lookups))) + ";\n", problems


def _definitions(custom=()):
    """Every field of CDM 5.4 and of the custom tables, as rows of the field list."""
    with open(FIELDS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f)) + custom_rows(custom)


class Conversion:
    def __init__(self, sandbox, identifier_offset=IDENTIFIER_OFFSET, custom=()):
        """custom, when given, is the list of custom tables that read_tables gives, created beside the CDM tables."""
        self.sandbox = sandbox
        self.con = sandbox.con
        self.tables = cdm_fields()
        self.custom = {table["name"] for table in custom}
        self.definitions = _definitions(custom)
        for row in custom_rows(custom):
            kind = DUCK_TYPES.get(row["datatype"], "VARCHAR")
            self.tables.setdefault(row["table"], []).append((row["field"], row["required"] == "Y", kind))
        self.identifier_offset = identifier_offset
        self.layer_highest = {}     # table -> the highest identifier that the anaesthesia layer has written to it
        self.planted_from = {}      # SOURCE TABLE -> (its name, the rows it held before any scenario was planted)
        self.keys = {row["table"]: row["field"] for row in self.definitions if row["primary_key"] == "Y"}
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {OMOP_SCHEMA}")
        for table, fields in self.tables.items():
            columns = ", ".join(f'"{name}" {kind}' for name, _, kind in fields)
            self.con.execute(f'CREATE OR REPLACE TABLE {OMOP_SCHEMA}."{table}" ({columns})')

    def load_vocabulary_versions(self, vocabulary):
        """Loads the list of vocabularies from an Athena download, so that a step can state the vocabulary version."""
        path = Path(vocabulary) / "VOCABULARY.csv"
        if not path.exists():
            return
        fields = [name for name, _, _ in self.tables["vocabulary"]]
        with open(path, newline="", encoding="utf-8") as f:
            rows = [[(row.get(name) or None) for name in fields] for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)]
        if rows:
            self.con.executemany(f"INSERT INTO {OMOP_SCHEMA}.vocabulary VALUES ({', '.join('?' * len(fields))})", rows)

    def load_mappings(self, text):
        """Loads mapping rows into omop.source_to_concept_map. Only the fields of that table are read."""
        fields = [name for name, _, _ in self.tables["source_to_concept_map"]]
        rows = [[(row.get(name) or None) for name in fields] for row in csv.DictReader(io.StringIO(text))]
        if rows:
            marks = ", ".join("?" * len(fields))
            self.con.executemany(f"INSERT INTO {OMOP_SCHEMA}.source_to_concept_map VALUES ({marks})", rows)
        return len(rows)

    def step(self, table, sql, layer="core"):
        """Runs one step. Returns what happened as a plain dictionary.

        A core step's rows go in as they are. An anaesthesia step numbers its own rows from 1, and
        its identifiers are moved into the layer's range: past the offset for the first step that
        writes a table, and past the highest identifier the layer has written there for a later one.
        A derived step's rows go in as they are, because its identifiers are those of the OMOP rows
        it is built from, and it must write the primary key of its custom table.
        """
        table = table.lower()
        if table not in self.tables:
            return {"table": table, "status": "unknown-table", "rows": 0}
        try:
            statements = to_duckdb(sql, self.sandbox.date_columns)
        except Unreadable:
            return {"table": table, "status": "unreadable", "rows": 0}
        except Unsupported:
            return {"table": table, "status": "unsupported", "rows": 0}
        rewrites = list(getattr(statements, "rewrites", []))
        if len(statements) != 1:
            return {"table": table, "status": "not-one-select", "rows": 0, "rewrites": rewrites}
        found = self._insert_step(table, statements[0], layer)
        found["rewrites"] = rewrites
        return found

    def _insert_step(self, table, statement, layer):
        """Inserts the rows of one translated step, as step describes. Returns what happened."""
        known = {name for name, _, _ in self.tables[table]}
        try:
            produced = [d[0].lower() for d in self.con.execute(f"SELECT * FROM ({statement}) AS step LIMIT 0").description]
            extra = sorted(set(produced) - known)
            if extra:
                return {"table": table, "status": "unknown-fields", "rows": 0, "fields": extra}
            columns = ", ".join(f'"{name}"' for name in produced)
            key = self.keys.get(table) if layer == "anaesthesia" else None
            if layer in ("anaesthesia", "derived") and self.keys.get(table) and self.keys[table] not in produced:
                return {"table": table, "status": "no-identifier", "rows": 0, "fields": [self.keys[table]]}
            base = self.layer_highest.get(table, self.identifier_offset)
            outputs = ", ".join(f'"{name}" + {base} AS "{name}"' if name == key else f'"{name}"' for name in produced)
            before = self.count(table)
            self.con.execute(f'CREATE OR REPLACE TEMP TABLE step_rows AS SELECT {outputs} FROM ({statement}) AS step')
            self.con.execute(f'INSERT INTO {OMOP_SCHEMA}."{table}" ({columns}) SELECT {columns} FROM step_rows')
            if key:
                highest = self.con.execute(f'SELECT MAX("{key}") FROM step_rows').fetchone()[0]
                self.layer_highest[table] = max(base, highest if highest is not None else base)
            self.con.execute("DROP TABLE step_rows")
        except duckdb.Error as error:
            return {"table": table, "status": "database-error", "rows": 0, "message": str(error).splitlines()[0]}
        return {"table": table, "status": "ok", "rows": self.count(table) - before}

    def core_problems(self, tables):
        """What the core holds that the anaesthesia layer cannot work with, for the tables the layer adds to.

        Every identifier of the core must stay below the offset, and every table must hold the fields
        of CDM 5.4, because the published schema shows the core's rows through views of those fields.
        """
        found = []
        for table, fields in self.tables.items():
            present = {row[0].lower() for row in self.con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = ? AND table_name = ?",
                [OMOP_SCHEMA, table]).fetchall()}
            missing = [name for name, _, _ in fields if name not in present]
            if missing:
                found.append(WORDING["core_fields"].format(table=table, fields=", ".join(missing)))
        for table in tables:
            key = self.keys.get(table)
            if not key:
                continue
            try:
                highest = self.con.execute(f'SELECT MAX("{key}") FROM {OMOP_SCHEMA}."{table}"').fetchone()[0]
            except duckdb.Error:
                continue    # reported above as a missing field
            if highest is not None and highest >= self.identifier_offset:
                found.append(WORDING["core_identifier"].format(table=table, key=key, highest=highest, offset=self.identifier_offset))
        return found

    def count(self, table):
        return self.con.execute(f'SELECT COUNT(*) FROM {OMOP_SCHEMA}."{table}"').fetchone()[0]

    def problems(self):
        """What the written tables break in the CDM: empty required fields, repeated primary keys and text that is too long."""
        found = []
        definitions = self.definitions
        keys = self.keys
        exported = EXPORTED_TABLES | self.custom
        # The sandbox does not enforce the length of a text field, and the real database does.
        for row in definitions:
            length = re.fullmatch(r"varchar\((\d+)\)", row["datatype"])
            if length and row["table"] in exported and self.count(row["table"]):
                longer = self.con.execute(f'SELECT COUNT(*) FROM {OMOP_SCHEMA}."{row["table"]}" '
                                          f'WHERE length("{row["field"]}") > {int(length.group(1))}').fetchone()[0]
                if longer:
                    found.append(f"{row['table']}.{row['field']}: {longer} rows hold text longer than "
                                 f"the {length.group(1)} characters the field allows")
        for table, fields in self.tables.items():
            if table not in exported or not self.count(table):
                continue
            for name, required, _ in fields:
                if required:
                    empty = self.con.execute(f'SELECT COUNT(*) FROM {OMOP_SCHEMA}."{table}" WHERE "{name}" IS NULL').fetchone()[0]
                    if empty:
                        found.append(f"{table}.{name}: {empty} rows have no value in a required field")
            key = keys.get(table)
            if key:
                repeated = self.con.execute(
                    f'SELECT COUNT(*) FROM (SELECT "{key}" FROM {OMOP_SCHEMA}."{table}" GROUP BY 1 HAVING COUNT(*) > 1)').fetchone()[0]
                if repeated:
                    found.append(f"{table}.{key}: {repeated} values of the primary key are repeated")
        return found

    def gates(self, folder):
        """Runs each quality gate in a folder. A gate is one SELECT of the rows that break a rule, so no rows means it passes."""
        results = []
        for path in sorted(Path(folder).glob("*.sql")):
            try:
                statements = to_duckdb(decode(path.read_bytes()), self.sandbox.date_columns)
                rows = self.con.execute(f"SELECT COUNT(*) FROM ({statements[0]}) AS gate").fetchone()[0] if len(statements) == 1 else None
            except (Unreadable, Unsupported, duckdb.Error):
                rows = None
            results.append({"gate": path.name, "rows": rows})
        return results

    def counts(self, folder):
        """Runs each of the conversion's counts. Returns [{"name", "says", "rows", "error"}], where rows is the number."""
        results = []
        for item in read_counts(folder):
            entry = {"name": item["name"], "says": item["says"], "rows": None, "error": None}
            try:
                statements = to_duckdb(item["sql"], self.sandbox.date_columns)
                found = self.con.execute(statements[0]).fetchall() if len(statements) == 1 else None
                if found is None or len(found) != 1 or len(found[0]) != 1 or not isinstance(found[0][0], int):
                    entry["error"] = "a count gives one row with one whole number"
                else:
                    entry["rows"] = found[0][0]
            except (Unreadable, Unsupported, duckdb.Error) as error:
                entry["error"] = str(error).splitlines()[0] if str(error) else type(error).__name__
            results.append(entry)
        return results

    def concepts_used(self):
        """Each concept field that holds rows: (table, field, domain the model expects) -> {concept: rows}."""
        with open(FIELDS, newline="", encoding="utf-8") as f:
            fields = [row for row in csv.DictReader(f)
                      if row["field"].endswith("_concept_id") and not row["field"].endswith("source_concept_id")]
        used = {}
        for row in fields:
            table, field = row["table"], row["field"]
            if table in CLINICAL_TABLES and self.count(table):
                counts = self.con.execute(f'SELECT "{field}", COUNT(*) FROM {OMOP_SCHEMA}."{table}" '
                                          f'WHERE "{field}" IS NOT NULL GROUP BY 1').fetchall()
                if counts:
                    used[(table, field, row["concept_domain"])] = dict(counts)
        return used

    def concept_problems(self, concept_file):
        """Checks every concept the conversion wrote against CONCEPT.csv from an Athena download.

        A concept must be in the file, be a standard concept, and belong to the domain that the
        model expects of its field. Rows written with the concept 0 are counted, because each is a
        value that has not been mapped.
        """
        used = self.concepts_used()
        wanted = {concept for counts in used.values() for concept in counts if concept}
        known = {}
        if Path(concept_file).suffix == ".duckdb":
            # A working copy of a whole Athena download, as the testbed builds it, holds CONCEPT as text.
            with duckdb.connect(str(concept_file), read_only=True) as source:
                source.execute("CREATE TEMP TABLE wanted (concept_id VARCHAR)")
                source.executemany("INSERT INTO wanted VALUES (?)", [[str(concept)] for concept in wanted])
                for concept, name, domain_id, standard in source.execute(
                        "SELECT c.concept_id, c.concept_name, c.domain_id, c.standard_concept FROM concept c "
                        "JOIN wanted w ON w.concept_id = c.concept_id").fetchall():
                    known[int(concept)] = (name, domain_id, standard or "")
            concept_file = None
        with open(concept_file, newline="", encoding="utf-8") if concept_file else io.StringIO("concept_id\tconcept_name\tdomain_id\tstandard_concept\n") as f:
            reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
            header = [name.lower() for name in next(reader)]
            at = {name: header.index(name) for name in ("concept_id", "concept_name", "domain_id", "standard_concept")}
            for row in reader:
                if row and row[0].isdigit() and int(row[0]) in wanted:
                    known[int(row[0])] = (row[at["concept_name"]], row[at["domain_id"]], row[at["standard_concept"]])
        found = []
        for (table, field, domain), counts in used.items():
            for concept, rows in sorted(counts.items()):
                where = f"{table}.{field} = {concept}"
                if concept == 0:
                    found.append(f"{table}.{field}: {rows} rows have the concept 0, which means not mapped")
                elif concept not in known:
                    found.append(f"{where}: this concept is not in the vocabulary ({rows} rows)")
                else:
                    name, actual, standard = known[concept]
                    if standard != "S":
                        found.append(f"{where} ({name}): this is not a standard concept ({rows} rows)")
                    if domain and actual not in [part.strip() for part in domain.split(",")]:
                        found.append(f"{where} ({name}): its domain is {actual}, and the field expects {domain} ({rows} rows)")
        return found

    def export(self):
        """Each table that holds rows, as CSV text with its fields in the published order."""
        files = {}
        for table, fields in self.tables.items():
            if table not in EXPORTED_TABLES | self.custom or not self.count(table):
                continue
            names = [name for name, _, _ in fields]
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(names)
            columns = ", ".join(f'"{name}"' for name in names)
            for row in self.con.execute(f'SELECT {columns} FROM {OMOP_SCHEMA}."{table}" ORDER BY 1').fetchall():
                writer.writerow(["" if value is None else value for value in row])
            files[f"{table}.csv"] = out.getvalue()
        return files

    def unmapped(self):
        """The measurement rows whose variable has no mapping row, as counts only: {"rows", "variables"}."""
        rows, variables = self.con.execute(f"SELECT COUNT(*), COUNT(DISTINCT measurement_source_value) FROM {OMOP_SCHEMA}.measurement "
                                           f"WHERE measurement_concept_id = 0").fetchone()
        return {"rows": rows, "variables": variables}

    def unmapped_worklist(self):
        """Each variable without a mapping row, by its source identifier, with its rows and anaesthetics, as CSV text.

        The identifiers are the source system's own, so the file is a worklist for use inside the hospital.
        """
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["measurement_source_value", "rows", "anaesthetics"])
        writer.writerows(self.con.execute(
            f"SELECT measurement_source_value, COUNT(*), COUNT(DISTINCT measurement_event_id) FROM {OMOP_SCHEMA}.measurement "
            f"WHERE measurement_concept_id = 0 GROUP BY 1 ORDER BY 2 DESC, 1").fetchall())
        return out.getvalue()


# Every integer is a BIGINT, because the anaesthesia layer's identifiers begin above 5,000,000,000, beyond the range of integer.
POSTGRESQL_TYPES = {"integer": "BIGINT", "datetime": "TIMESTAMP", "float": "NUMERIC", "varchar(max)": "TEXT"}


def postgresql_script(files, schema="cdm", custom=()):
    """A psql script that creates the CDM 5.4 tables, and any custom tables, where they are missing and loads the exported files.

    It is run from the folder that holds the files: psql -d DATABASE -f load_postgresql.sql
    """
    fields = _definitions(custom)
    lines = ["-- Creates the OMOP CDM 5.4 tables where they do not exist yet, and loads the exported files."
             + (" It creates the custom tables beside them in the same way." if custom else ""),
             "\\set ON_ERROR_STOP on", f"CREATE SCHEMA IF NOT EXISTS {schema};"]
    for table in dict.fromkeys(row["table"] for row in fields):
        columns = ",\n".join(
            f'  "{row["field"]}" {POSTGRESQL_TYPES.get(row["datatype"], row["datatype"])}{" NOT NULL" if row["required"] == "Y" else ""}'
            for row in fields if row["table"] == table)
        lines.append(f"CREATE TABLE IF NOT EXISTS {schema}.{table} (\n{columns}\n);")
    for name in files:
        table = name[:-len(".csv")]
        names = ", ".join(f'"{row["field"]}"' for row in fields if row["table"] == table)
        lines.append(f"\\copy {schema}.{table} ({names}) FROM '{name}' WITH (FORMAT csv, HEADER true)")
    return "\n".join(lines) + "\n"


def derive_mappings(conversion, derived, vocabulary):
    """Adds mapping rows that are proposed by name, for local codes whose values only the sandbox knows.

    Each entry names a table of local codes with their labels. The labels are looked up in the
    vocabulary, and each one that matches a single standard concept becomes a mapping row.
    """
    summary = []
    for entry in derived:
        table, code, label = (entry[key] for key in ("table", "code", "label"))
        missing = {"vocabulary": entry["vocabulary"], "matched": 0, "unmatched": []}
        known = conversion.sandbox.catalogue.table(table)
        if known is None or any(known.column(name) is None for name in (code, label)):
            absent = table if known is None else ", ".join(f"{table}.{name}" for name in (code, label) if known.column(name) is None)
            summary.append(dict(missing, problem=WORDING["derived_missing"].format(vocabulary=entry["vocabulary"], absent=absent)))
            continue
        try:
            labels = conversion.con.execute(
                f'SELECT DISTINCT CAST("{code}" AS VARCHAR), CAST("{label}" AS VARCHAR) FROM "{table}" '
                f'WHERE "{code}" IS NOT NULL AND "{label}" IS NOT NULL').fetchall()
        except duckdb.Error as error:
            summary.append(dict(missing, problem=WORDING["derived_unreadable"].format(
                vocabulary=entry["vocabulary"], table=table, error=str(error).splitlines()[0])))
            continue
        matched, unmatched = mapping.propose(labels, vocabulary, entry.get("domain", "Drug"),
                                             entry.get("concept_class", "Ingredient"), entry.get("coded_in"))
        found = mapping.rows(labels, matched, entry["vocabulary"])
        if found:
            marks = ", ".join("?" * len(mapping.LAYOUT))
            conversion.con.executemany(f"INSERT INTO {OMOP_SCHEMA}.source_to_concept_map VALUES ({marks})", found)
        summary.append({"vocabulary": entry["vocabulary"], "matched": len(found),
                        "unmatched": sorted({text for _, text, _ in unmatched})})
    return summary


# Planted scenarios.
#
# A scenario is a folder under the conversion's scenarios/, holding scenario.json and rows.sql:
#
#     {"description": "one sentence that states the awkward case",
#      "fails_gate": "030_identifiers_are_not_repeated.sql",        (only for a scenario that exists to make a gate fail)
#      "expectations": [{"says": "one sentence", "query": "SELECT COUNT(*) AS n FROM omop.measurement m ...", "result": [2]}]}
#
# rows.sql holds plain INSERT statements into the source tables. Every identifier of nine or more
# digits in it falls in SCENARIO_IDENTIFIERS, a range that the sandbox never generates (its keys are
# the row number, or a million times the key group's number plus the row number), so that a planted
# row cannot collide with a generated one and can be found again by its identifier. Each expectation
# is one SELECT against omop.<table> that gives one row, and result is that row.

SCENARIOS = "scenarios"
SCENARIO_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
SCENARIO_IDENTIFIERS = (990_000_000, 990_999_999)
LONG_NUMBER = re.compile(r"(?<![0-9.])[0-9]{9,}(?![0-9])")


class ScenarioError(ValueError):
    """A scenario folder does not hold a scenario in the form that the runner accepts."""


# A line that sqlcmd would read as a command of its own, as release.py refuses it.
SQLCMD_LINE = re.compile(r"\s*(GO\b|:|!!)", re.IGNORECASE)
# The only nodes that a planted INSERT may hold: a one-part table with its columns, and rows of literals.
PLANTED_NODES = (exp.Insert, exp.Schema, exp.Table, exp.Identifier, exp.Values, exp.Tuple, exp.Literal, exp.Null, exp.Neg)
# What a planted INSERT ... SELECT may hold besides. It exists for a scenario that needs many rows, such as
# a long anaesthetic with thousands of readings: the SELECT may read only rows of literals given inline,
# cross joined, and may compute with arithmetic, CAST and DATEADD alone. It reads no table at all.
GENERATED_NODES = (exp.Select, exp.From, exp.Join, exp.TableAlias, exp.Column, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod,
                   exp.Paren, exp.Cast, exp.DataType, exp.DataTypeParam, exp.DateAdd, exp.Var)


def _generated_rows(select):
    """Whether a SELECT reads only inline rows of literals, cross joined, as GENERATED_NODES allows."""
    if not isinstance(select, exp.Select) or not isinstance(select.args.get("from_", select.args.get("from")), exp.From):
        return False
    if any(value not in (None, False, []) for key, value in select.args.items() if key not in ("expressions", "from_", "from", "joins")):
        return False
    sources = [select.args.get("from_", select.args.get("from")).this] + [join.this for join in select.args.get("joins") or []]
    if not all(isinstance(source, exp.Values) and source.alias for source in sources):
        return False
    for join in select.args.get("joins") or []:
        if any(value not in (None, False, []) for key, value in join.args.items() if key not in ("this", "kind")) \
                or (join.args.get("kind") or "").upper() != "CROSS":
            return False
    for node in select.walk():
        if isinstance(node, exp.Var) and not isinstance(node.parent, exp.DateAdd):
            return False
    return True


def _planted_insert(statement):
    """The table that one statement of rows.sql writes, or None unless it is INSERT INTO TABLE (columns) VALUES (literals).

    A statement may instead INSERT INTO TABLE (columns) SELECT from inline rows of literals alone, as
    _generated_rows allows, so that a scenario can plant many rows without writing each one.
    """
    generated = isinstance(statement, exp.Insert) and _generated_rows(statement.expression)
    if not isinstance(statement, exp.Insert) or not isinstance(statement.this, exp.Schema) \
            or not (isinstance(statement.expression, exp.Values) or generated) or not statement.this.expressions:
        return None
    if any(value not in (None, False) for key, value in statement.args.items() if key not in ("this", "expression")):
        return None
    target = statement.this.this
    if not isinstance(target, exp.Table) or not isinstance(target.this, exp.Identifier) or target.args.get("db") \
            or target.args.get("catalog") or target.args.get("alias"):
        return None
    allowed = PLANTED_NODES + (GENERATED_NODES if generated else ())
    for node in statement.walk():
        if not isinstance(node, allowed) or (not generated and isinstance(node, exp.Neg) and not (
                isinstance(node.this, exp.Literal) and not node.this.is_string)):
            return None
        if isinstance(node, exp.Table) and node is not target:
            return None
        if isinstance(node, exp.Literal) and ("\r" in node.name or "\n" in node.name or "$(" in node.name):
            return None
    if not all(isinstance(column, exp.Identifier) for column in statement.this.expressions):
        return None
    return target.name


def _scenario_tables(sql, where, codes=frozenset()):
    """The tables that a scenario's rows.sql writes, after checking that it holds only plain INSERT statements into source tables.

    Each statement is INSERT INTO one source table, named in one part, with its columns, and VALUES
    that are literals only, none of which holds a line break or $(. No line of the file may be one
    that sqlcmd would read as a command. codes are the source codes of the conversion's mapping
    rows, which a planted row may hold however long they are.
    """
    if "$(" in sql or any(SQLCMD_LINE.match(line) for line in sql.splitlines()):
        raise ScenarioError(f"{where}: rows.sql may not hold $( or a line that sqlcmd would read as a command")
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="tsql") if s is not None]
    except sqlglot.errors.SqlglotError:
        raise ScenarioError(f"{where}: rows.sql cannot be read as T-SQL") from None
    tables = []
    for statement in statements:
        target = _planted_insert(statement)
        if target is None or target.upper() == OMOP_SCHEMA.upper():
            raise ScenarioError(f"{where}: rows.sql may hold only INSERT statements into a source table, each with its columns "
                                f"and with VALUES that are literals on one line each")
        tables.append(target)
    for number in LONG_NUMBER.findall(sql):
        if number not in codes and not SCENARIO_IDENTIFIERS[0] <= int(number) <= SCENARIO_IDENTIFIERS[1]:
            raise ScenarioError(f"{where}: the identifier {number} in rows.sql lies outside the range kept for planted rows, "
                                f"{SCENARIO_IDENTIFIERS[0]} to {SCENARIO_IDENTIFIERS[1]}")
    return tables


def check_expectation_query(sql, where="expectation"):
    """Raises ScenarioError unless sql is one SELECT that reads only omop.<table>, under the rule that release.py applies to a gate."""
    from .release import Refused, _single_select
    try:
        _single_select(sql, where)
    except Refused as error:
        raise ScenarioError(str(error)) from None
    try:
        trees = [tree for tree in sqlglot.parse(sql, dialect="tsql") if tree is not None]
    except sqlglot.errors.SqlglotError:
        raise ScenarioError(f"{where}: the query cannot be read as T-SQL") from None
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
        raise ScenarioError(f"{where}: the query is one SELECT")
    named = {cte.alias.upper() for cte in trees[0].find_all(exp.CTE)}
    for node in trees[0].walk():
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise ScenarioError(f"{where}: the query may only read")
        if isinstance(node, exp.Table) and not (not node.db and node.name.upper() in named) \
                and (node.db or "").upper() != OMOP_SCHEMA.upper():
            raise ScenarioError(f"{where}: the query reads only the OMOP tables, as omop.<table>")
    return trees[0]


def read_counts(folder):
    """The conversion's counts: [{"name", "says", "sql"}], in the order of their file names, or [] when it has none.

    A count is one SELECT that gives one row with one number, such as the anaesthetics that the layer
    leaves out, and never an identifier. Its first line is a comment that holds the sentence which
    reports it, with {count} once where the number goes. Raises ValueError when a file breaks a rule.
    """
    found = []
    for path in sorted((Path(folder) / COUNTS_FOLDER).glob("*.sql")):
        if not FILE_NAME.fullmatch(path.name):
            raise ValueError(WORDING["file_name"].format(name=path.name))
        sql = decode(path.read_bytes())
        first = sql.splitlines()[0] if sql.strip() else ""
        says = first[2:].strip() if first.startswith("--") else ""
        if says.count(COUNT_MARK) != 1 or not says.endswith(".") or "$(" in says:
            raise ValueError(f"{COUNTS_FOLDER}/{path.name}: the first line is a comment that holds the sentence which reports "
                             f"the count, with {COUNT_MARK} once where the number goes, and it ends with a full stop")
        found.append({"name": path.name, "says": says, "sql": sql})
    return found


def report_count(says, rows):
    """A count's sentence with its number in place."""
    return says.replace(COUNT_MARK, f"{rows:,}")


def read_scenarios(folder):
    """The planted scenarios of a conversion folder, checked, in the order of their names, or [] when it has none.

    Each scenario's planted rows and description come from the folder, and its expectations, the gate it exists to make
    fail and its cases from the held-out root (held_out_root), or from scenario.json where the folder has no held-out
    root. A scenario whose expectations are found nowhere has none, and the runner refuses to count it as met."""
    from . import memo
    if not (Path(folder) / SCENARIOS).is_dir():
        return []
    roots = [held_out_root(folder), held_out_root(INVENTED_CONVERSION)]
    key = memo.digest(memo.folder(folder), *[memo.folder(root) if root.is_dir() else "" for root in roots])
    return memo.remembered("scenarios", key, lambda: _read_scenarios(folder))


def _read_scenarios(folder):
    base = Path(folder) / SCENARIOS
    if not base.is_dir():
        return []
    gates = {path.name for path in (Path(folder) / "gates").glob("*.sql")}
    codes = frozenset((row.get("source_code") or "").strip() for row in mapping_dicts(folder))
    found = []
    for path in sorted(p for p in base.iterdir() if p.is_dir()):
        where = f"{SCENARIOS}/{path.name}"
        if not SCENARIO_NAME.fullmatch(path.name):
            raise ScenarioError(f"{where}: a scenario's name is a plain name in lower case")
        try:
            data = json.loads(decode((path / "scenario.json").read_bytes()))
            rows = decode((path / "rows.sql").read_bytes())
        except (OSError, ValueError):
            raise ScenarioError(f"{where}: the folder holds scenario.json and rows.sql, and both can be read") from None
        if not isinstance(data, dict) or not {"description"} <= set(data) <= {"description", "expectations", "fails_gate", "cases"}:
            raise ScenarioError(f"{where}: scenario.json holds a description and, where the folder has no held-out root, "
                                f"the expectations and, optionally, fails_gate and cases")
        held = _held_out_for(folder, SCENARIOS, path.name)
        source = "held out" if held is not None else "beside the rows" if "expectations" in data else None
        if held is not None:
            try:
                expected = _read_held_out(held)
            except (OSError, ValueError):
                raise ScenarioError(f"{where}: the held-out expected.json cannot be read") from None
            if not isinstance(expected, dict) or not {"expectations"} <= set(expected) <= {"expectations", "fails_gate", "cases"}:
                raise ScenarioError(f"{where}: the held-out expected.json holds the expectations and, optionally, "
                                    f"fails_gate and cases")
            if set(data) - {"description"}:
                raise ScenarioError(f"{where}: the expectations are held out, so scenario.json holds the description alone")
            data = dict(expected, description=data["description"])
        # cases, where given, says for each planted case in one sentence what a query that follows the rules should give.
        cases = data.get("cases", [])
        if not isinstance(cases, list) or not all(isinstance(c, str) and c.strip() and "\n" not in c and len(c) <= 600 for c in cases):
            raise ScenarioError(f"{where}: cases is a list of single sentences, one for each planted case")
        if not isinstance(data["description"], str) or not data["description"].strip():
            raise ScenarioError(f"{where}: the description is one sentence of text")
        if "fails_gate" in data and data["fails_gate"] not in gates:
            raise ScenarioError(f"{where}: fails_gate names a gate of the conversion")
        expectations, reads = data.get("expectations", []), set()
        if source is not None and (not isinstance(expectations, list) or not expectations):
            raise ScenarioError(f"{where}: a scenario has at least one expectation")
        for number, item in enumerate(expectations, start=1):
            if not isinstance(item, dict) or set(item) != {"says", "query", "result"} or not isinstance(item["says"], str) \
                    or not isinstance(item["query"], str) or not isinstance(item["result"], list) or not item["result"] \
                    or not all(value is None or isinstance(value, (int, float, str)) for value in item["result"]):
                raise ScenarioError(f"{where}, expectation {number}: an expectation has exactly says, query and result, "
                                    f"and its result is one row of values")
            tree = check_expectation_query(item["query"], f"{where}, expectation {number}")
            reads.update(table.name.lower() for table in tree.find_all(exp.Table) if (table.db or "").upper() == OMOP_SCHEMA.upper())
        found.append({"name": path.name, "description": data["description"], "fails_gate": data.get("fails_gate"),
                      "expectations_from": source, "cases": [" ".join(c.split()) for c in cases],
                      "expectations": expectations, "reads": sorted(reads), "rows": rows, "tables": _scenario_tables(rows, where, codes)})
    return found


def chosen_scenarios(folder, names=None):
    """The scenarios to plant: those named, in the order of their names, or, when names is None, every
    scenario that does not exist to make a gate fail. An empty list of names plants none."""
    every = read_scenarios(folder)
    if names is None:
        return [s for s in every if not s["fails_gate"]]
    known = {s["name"] for s in every}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ScenarioError(f"{', '.join(unknown)}: the conversion has no scenario of that name")
    return [s for s in every if s["name"] in set(names)]


def plant(conversion, scenario):
    """Inserts a scenario's rows into the sandbox's source tables. Returns the number of rows planted."""
    held = {name.upper() for name in conversion.sandbox.tables}
    absent = sorted({table for table in scenario["tables"] if table.upper() not in held
                     or conversion.sandbox.catalogue.table(table) is None})
    if absent:
        raise ScenarioError(f"rows.sql writes {', '.join(absent)}, which the sandbox does not hold")
    for table in scenario["tables"]:
        if table.upper() not in conversion.planted_from:
            name = next(n for n in conversion.sandbox.tables if n.upper() == table.upper())
            conversion.planted_from[table.upper()] = (name, conversion.con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    planted = 0
    try:
        for statement in to_duckdb(scenario["rows"], conversion.sandbox.date_columns):
            result = conversion.con.execute(statement).fetchone()
            planted += result[0] if result else 0
    except (Unreadable, Unsupported, duckdb.Error) as error:
        raise ScenarioError(f"rows.sql did not run ({str(error).splitlines()[0] if str(error) else type(error).__name__})") from None
    return planted


def _value_matches(found, expected):
    if expected is None or found is None:
        return found is None and expected is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            number = float(found)
        except (TypeError, ValueError):
            return False
        return abs(number - expected) <= 1e-6 * max(1.0, abs(expected))
    return str(found) == str(expected)


def expectation_met(rows, expected):
    """Whether the rows that an expectation's query gave are exactly the one row it expects."""
    return len(rows) == 1 and len(rows[0]) == len(expected) and all(_value_matches(f, e) for f, e in zip(rows[0], expected))


def shown_rows(rows):
    """An answer as plain text, for the report."""
    if not rows:
        return "no row"
    if len(rows) > 1:
        return f"{len(rows)} rows"
    return ", ".join("empty" if value is None else str(value) for value in rows[0])


def evaluate(conversion, scenario):
    """Runs each expectation of a scenario on the written tables. Returns [{"says", "expected", "found", "met", "error"}]."""
    results = []
    for item in scenario["expectations"]:
        entry = {"says": item["says"], "expected": shown_rows([item["result"]]), "found": None, "met": False, "error": None}
        try:
            statements = to_duckdb(item["query"], conversion.sandbox.date_columns)
            rows = conversion.con.execute(statements[0]).fetchall() if len(statements) == 1 else None
        except (Unreadable, Unsupported, duckdb.Error) as error:
            rows, entry["error"] = None, str(error).splitlines()[0] if str(error) else type(error).__name__
        if rows is None:
            entry["error"] = entry["error"] or "the query is not one statement"
        else:
            entry["found"], entry["met"] = shown_rows(rows), expectation_met(rows, item["result"])
        results.append(entry)
    return results


# The steps over the roles, and their role scenarios on the role shadow.

class RolesStepError(ValueError):
    """A step over the roles reads something other than the role views, the mapping views and the OMOP tables."""


def check_roles_step(sql, where="step"):
    """Raises RolesStepError unless sql is one SELECT, by the rule that release.py applies to a step, that reads only the
    role views, the mapping views, its own common table expressions and the OMOP tables as omop.<table>."""
    from . import rolemap
    from .release import Refused, _single_select
    try:
        tree = _single_select(sql, where)
    except Refused as error:
        raise RolesStepError(str(error)) from None
    named = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    public = set(rolemap.public_views())
    for table in tree.find_all(exp.Table):
        if (table.db or "").upper() == OMOP_SCHEMA.upper() and not table.catalog:
            continue
        if table.db or table.catalog or table.name.lower() not in public | named:
            raise RolesStepError(f"{where}: a step over the roles reads only the role views, the mapping views and the OMOP "
                                 f"tables, and not {table.sql(dialect='tsql')}")
    return tree


def read_role_scenarios(folder):
    """The role scenarios of a conversion folder, in the order of their names, or [] when it has none.

    A role scenario plants rows of the role views and the mapping views, and of the OMOP tables that the core would hold,
    in role_scenarios/<name>/rows.json as {"description", "roles": {view: [row, ...]}, "omop": {table: [{field: value}]}},
    with each role row in the order of the contract's columns. Its expected rows are held out, in the held-out root's
    role_scenarios/<name>/expected.json, as {"step", "table", "says", "columns", "rows"}: the step over the roles that it
    judges, the OMOP table that the step writes, a sentence, and the rows expected in the named columns, written by hand.
    """
    from . import rolemap
    base = Path(folder) / ROLE_SCENARIOS
    if not base.is_dir():
        return []
    public = rolemap.public_views()
    cdm = cdm_fields()
    found = []
    for path in sorted(p for p in base.iterdir() if p.is_dir()):
        where = f"{ROLE_SCENARIOS}/{path.name}"
        if not SCENARIO_NAME.fullmatch(path.name):
            raise ScenarioError(f"{where}: a scenario's name is a plain name in lower case")
        try:
            data = json.loads(decode((path / "rows.json").read_bytes()))
        except (OSError, ValueError):
            raise ScenarioError(f"{where}: the folder holds rows.json, and it can be read as JSON") from None
        if not isinstance(data, dict) or set(data) != {"description", "roles", "omop"} or not _one_line(data["description"]):
            raise ScenarioError(f"{where}: rows.json holds a description of one line, the roles and the omop rows")
        for view, rows in data["roles"].items():
            if view not in public or not isinstance(rows, list) or not all(
                    isinstance(row, list) and len(row) == len(public[view]) for row in rows):
                raise ScenarioError(f"{where}: {view} is a role view or a mapping view, and each of its rows gives every "
                                    f"column of the contract, in order")
        for table, rows in data["omop"].items():
            fields = {name for name, _, _ in cdm.get(table, [])}
            if not fields or not isinstance(rows, list) or not all(isinstance(row, dict) and set(row) <= fields for row in rows):
                raise ScenarioError(f"{where}: {table} is a table of CDM 5.4, and each of its rows names its fields")
        held = _held_out_for(folder, ROLE_SCENARIOS, path.name)
        expected = None
        if held is not None:
            expected = _read_held_out(held)
            if not isinstance(expected, dict) or set(expected) != {"step", "table", "says", "columns", "rows"} \
                    or not isinstance(expected["columns"], list) or not all(
                        isinstance(row, list) and len(row) == len(expected["columns"]) for row in expected["rows"]):
                raise ScenarioError(f"{where}: the held-out expected.json holds the step, the table, what it says, the "
                                    f"columns and the expected rows")
        found.append({"name": path.name, "description": data["description"], "roles": data["roles"], "omop": data["omop"],
                      "expected": expected})
    return found


def _comparable(value):
    """A value of a row as the comparison of a role scenario sees it: a number, or text that reads as one, as a float,
    and a time in the ISO form."""
    import datetime as dt
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    # Text that reads as a number is compared as one, so that a rate kept as text, such as 2 or 2.0, compares alike
    # whichever engine wrote it.
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return str(value)


def _sorted_rows(rows):
    return sorted(rows, key=lambda row: [(value is None, str(value)) for value in row])


def run_role_scenario(folder, scenario):
    """Runs the step that a role scenario judges on the role shadow with the scenario's rows, and compares what it writes
    with the expected rows. Returns {"name", "step", "table", "says", "outcome", "expected_rows", "found_rows", "missing",
    "unexpected", "error", "rewrites"}; the outcome is passed, failed, or not run, with the error."""
    from . import rolemap
    expected = scenario["expected"]
    out = {"name": scenario["name"], "description": scenario["description"], "step": None, "table": None, "says": None,
           "outcome": "not run", "expected_rows": 0, "found_rows": 0, "missing": [], "unexpected": [], "error": None,
           "rewrites": []}
    if expected is None:
        return dict(out, error="its expected rows are held out, and the runner has found no held-out root that holds them")
    out.update(step=expected["step"], table=expected["table"], says=expected["says"], expected_rows=len(expected["rows"]))
    steps = json.loads((Path(folder) / "conversion.json").read_text())
    offered = {item["file"]: item for item in roles_steps(steps)}
    if expected["step"] not in offered or offered[expected["step"]]["table"].lower() != expected["table"].lower():
        return dict(out, error=f"{expected['step']} is not a step over the roles that writes {expected['table']}")
    sql = decode((Path(folder) / expected["step"]).read_bytes())
    try:
        check_roles_step(sql, expected["step"])
        statements = to_duckdb(sql)
    except (RolesStepError, Unreadable, Unsupported) as error:
        return dict(out, error=str(error) or type(error).__name__)
    out["rewrites"] = list(statements.rewrites)
    con = rolemap.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=scenario["roles"])
    try:
        con.execute(f"CREATE SCHEMA {OMOP_SCHEMA}")
        for table, fields in cdm_fields().items():
            con.execute(f'CREATE TABLE {OMOP_SCHEMA}."{table}" (' + ", ".join(f'"{n}" {k}' for n, _, k in fields) + ")")
        for table, rows in scenario["omop"].items():
            for row in rows:
                names = list(row)
                con.execute(f'INSERT INTO {OMOP_SCHEMA}."{table}" (' + ", ".join(f'"{n}"' for n in names) + ") VALUES ("
                            + ", ".join("?" for _ in names) + ")", [row[n] for n in names])
        if len(statements) != 1:
            return dict(out, error="the step is not one statement")
        cursor = con.execute(statements[0])
        produced = [d[0].lower() for d in cursor.description]
        rows = cursor.fetchall()
    except duckdb.Error as error:
        return dict(out, error=str(error).splitlines()[0])
    finally:
        con.close()
    absent = [c for c in expected["columns"] if c.lower() not in produced]
    if absent:
        return dict(out, error=f"the step writes no {', '.join(absent)}")
    at = [produced.index(c.lower()) for c in expected["columns"]]
    found = _sorted_rows([[_comparable(row[i]) for i in at] for row in rows])
    wanted = _sorted_rows([[_comparable(v) for v in row] for row in expected["rows"]])
    missing, unexpected = list(wanted), []
    for row in found:
        if row in missing:
            missing.remove(row)
        else:
            unexpected.append(row)
    return dict(out, outcome="passed" if not missing and not unexpected else "failed", found_rows=len(found),
                missing=missing, unexpected=unexpected)


def run_role_scenarios(folder):
    """Every role scenario of a conversion folder, run as run_role_scenario does, in the order of their names."""
    return [run_role_scenario(folder, scenario) for scenario in read_role_scenarios(folder)]


def compiled_steps(world, folder, steps, schema=None):
    """{file: T-SQL} for each step on the route over the roles, compiled through the hospital schema as the release
    script compiles it (release.compile_roles_step), so that the runner runs the very text that the script carries.
    schema is a map folder or a map already read, and is by default the folder's own (hospital_schema). Raises
    ValueError when a step over the roles has no hospital schema to be compiled through, or cannot be compiled."""
    from . import release
    over = [step for step in steps if step.get("layer") != "derived" and step.get("route") == "roles"]
    if not over:
        return {}
    roles_map = release.read_schema(folder, schema, world.catalogue_text())
    if roles_map is None:
        raise ValueError(WORDING["no_schema"].format(name=over[0]["file"]))
    return {step["file"]: release.compile_roles_step(decode((Path(folder) / step["file"]).read_bytes()), roles_map, step["file"])
            for step in over}


def run(world, folder, rows=500, vocabulary=None, checks=None, between=None, identifier_offset=None, scenarios=None,
        alternatives=None, schema=None):
    """Builds the sandbox for a world with its conversion's SQL included, and runs the conversion.

    checks, when given, are check results in the layout of the check script, as text or as a path,
    and the sandbox is built from them instead of from answers computed over the stand-in database.
    between, when given, is called with the conversion after the core steps and before the
    anaesthesia steps, so that a test can alter the stand-in core as a real core might differ from it.
    The identifier offset is the folder's release.json setting, or the default.
    scenarios names the planted scenarios whose rows go into the source tables after the sandbox is
    built and before the core layer runs. None plants every scenario of the folder that does not exist
    to make a gate fail, and an empty list plants none. Each expectation is evaluated after the gates.
    alternatives names alternatives that conversion.json offers, each of which then runs in place of its
    step's own file, so that an alternative can be tried before a person makes it the step. Without it,
    every step runs its own file and the alternatives are ignored.
    The folder's counts run after the gates, and the report gives each one's number under "counts".
    A step over the roles runs compiled through the hospital schema (compiled_steps), which schema names and which is
    by default the map folder beside the conversion folder, and the report gives its text under "compiled".
    """
    folder = Path(folder)
    steps = json.loads((folder / "conversion.json").read_text())
    named = [step for step in steps if not isinstance(step.get("file"), str) or not FILE_NAME.fullmatch(step["file"])
             or set(step["file"]) == {"."}]
    if named:
        raise ValueError("; ".join(layer_problems(named)))
    custom = read_tables(folder)
    refused = layer_problems(steps, {table["name"] for table in custom})
    if refused:
        raise ValueError("; ".join(refused))
    steps = with_alternatives(steps, alternatives)
    read_counts(folder)     # a count that breaks a rule is refused before anything is built
    compiled = compiled_steps(world, folder, steps, schema)

    def text(step):
        return compiled.get(step["file"]) or decode((folder / step["file"]).read_bytes())
    planted_scenarios = chosen_scenarios(folder, scenarios)
    if identifier_offset is None:
        settings = json.loads((folder / "release.json").read_text()) if (folder / "release.json").exists() else {}
        identifier_offset = settings.get("identifier_offset", IDENTIFIER_OFFSET)

    definitions = {}
    if world.rules_path:
        for rule in json.loads(world.rules_path.read_text()).get("definitionKeys", []):
            if isinstance(rule, dict) and rule.get("column") and rule.get("definitionTable"):
                definitions[rule["column"].upper()] = (rule["definitionTable"], rule.get("keyColumn") or rule["column"])

    unread = []

    def analysis(checks_csv=None):
        result = world.analysis(checks_csv)
        result.add_request("conversion", as_request([(step["table"], text(step)) for step in steps], definitions, unread))
        return result

    if checks is None:
        first = analysis()
        answers, _ = harness.run_checks(first, world.truth(first, rows))
        checks = harness.checks_csv(answers)
    elif isinstance(checks, Path):
        checks = decode(checks.read_bytes())
    unread.clear()
    second = analysis(checks)
    sandbox = Sandbox(Catalogue.from_csv(world.catalogue_text()), harness.inventory_zip(second))
    built = sandbox.build(rows)
    conversion = Conversion(sandbox, identifier_offset, custom)
    # The planted scenarios go into the source tables before any step runs, as rows of the source would.
    planting = []
    for scenario in planted_scenarios:
        entry = {"name": scenario["name"], "description": scenario["description"], "fails_gate": scenario["fails_gate"],
                 "tables": scenario["tables"], "reads": scenario["reads"], "planted": 0, "error": None, "expectations": []}
        try:
            if not scenario["expectations"]:
                raise ScenarioError("its expected rows are held out, and the runner has found no held-out root that holds them")
            entry["planted"] = plant(conversion, scenario)
        except ScenarioError as error:
            entry["error"] = str(error)
        planting.append((scenario, entry))
    mappings = mapping_text(folder)
    mapped = conversion.load_mappings(mappings) if mappings else 0
    derived = folder / "derived_mappings.json"
    proposed = derive_mappings(conversion, json.loads(derived.read_text()), vocabulary) if vocabulary and derived.exists() else []
    if vocabulary:
        conversion.load_vocabulary_versions(vocabulary)
    layer_tables = list(dict.fromkeys(step["table"].lower() for step in steps if step.get("layer") == "anaesthesia"))
    results, core, started = [], [], False
    for step in steps:
        if step.get("layer") == "anaesthesia" and not started:
            # The core has been refreshed. What it holds is checked before the anaesthesia layer runs.
            started = True
            if between is not None:
                between(conversion)
            core = conversion.core_problems(layer_tables)
        results.append(conversion.step(step["table"], text(step), step.get("layer", "core")))
    problems = list(dict.fromkeys(unread)) + [item["problem"] for item in proposed if item.get("problem")]
    problems += core
    try:
        problems += conversion.problems()
    except duckdb.Error as error:
        problems.append(WORDING["not_checked"].format(error=str(error).splitlines()[0]))
    gates = conversion.gates(folder / "gates")
    counts = conversion.counts(folder)
    for scenario, entry in planting:
        if entry["error"] is None:
            entry["expectations"] = evaluate(conversion, scenario)
    draft = read_draft(folder)
    return conversion, {"built": built["sentence"], "mappings": mapped, "derived": proposed, "steps": results,
                        "gates": gates, "counts": counts, "problems": problems, "unmapped": conversion.unmapped(),
                        "scenarios": [entry for _, entry in planting],
                        "routes": {"shares": route_shares(steps), "problems": route_problems(steps)}, "compiled": compiled,
                        "draft": {"sentence": draft_sentence(draft), **draft} if draft is not None else None}


def failures(report):
    """Each reason that a run must not be taken as clean: a step that did not run, a gate that failed or could not be run, a problem."""
    found = [f"step {index + 1} ({step['table']}): {step['status']}" for index, step in enumerate(report["steps"]) if step["status"] != "ok"]
    found += [f"gate {gate['gate']}: " + ("could not be run" if gate["rows"] is None else f"failed, with {gate['rows']} rows")
              for gate in report["gates"] if gate["rows"] != 0]
    found += [f"count {count['name']}: could not be run ({count['error']})" for count in report.get("counts", []) if count["error"]]
    for scenario in report.get("scenarios", []):
        if scenario["error"]:
            found.append(f"scenario {scenario['name']}: not planted ({scenario['error']})")
        found += [f"scenario {scenario['name']}: " + ("not evaluated" if item["error"] else "not met") + f": {item['says']}"
                  for item in scenario["expectations"] if not item["met"]]
    return found + [f"problem: {problem}" for problem in report["problems"]]


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.convert")
    parser.add_argument("world", type=Path)
    parser.add_argument("conversion", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--vocabulary", type=Path, help="CONCEPT.csv from an Athena download, or the folder that holds it")
    parser.add_argument("--checks", type=Path, help="check results from the check script, such as a hospital's own, to build the sandbox from")
    parser.add_argument("--scenarios", help="the planted scenarios to run, separated by commas; every scenario that does not "
                                            "exist to make a gate fail when left out")
    parser.add_argument("--no-scenarios", action="store_true", help="plant no scenario")
    parser.add_argument("--alternative", action="append", default=[],
                        help="an alternative that conversion.json offers, to run in place of its step's own file; may be given more than once")
    args = parser.parse_args()
    vocabulary = (args.vocabulary if args.vocabulary.is_dir() else args.vocabulary.parent) if args.vocabulary else None
    names = [] if args.no_scenarios else [n.strip() for n in args.scenarios.split(",") if n.strip()] if args.scenarios else None
    try:
        conversion, report = run(harness.World.from_folder(args.world), args.conversion, args.rows, vocabulary, args.checks,
                                 scenarios=names, alternatives=args.alternative)
    except (ScenarioError, ValueError) as error:
        raise SystemExit(f"schemalyser.convert: {error}")
    args.out.mkdir(parents=True, exist_ok=True)
    files = conversion.export()
    for name, text in files.items():
        (args.out / name).write_text(text, encoding="utf-8")
    (args.out / "load_postgresql.sql").write_text(postgresql_script(files, custom=read_tables(args.conversion)), encoding="utf-8")
    unmapped = report.get("unmapped") or {"rows": 0, "variables": 0}
    if unmapped["rows"]:
        (args.out / "unmapped_measurements.csv").write_text(conversion.unmapped_worklist(), encoding="utf-8")
    print(report["built"])
    print(f"mapping rows loaded: {report['mappings']}")
    for item in report["derived"]:
        print(f"{item['vocabulary']}: {item['matched']} codes matched one concept; "
              f"{len(item['unmatched'])} labels did not: {', '.join(item['unmatched'][:40])}")
    for step in report["steps"]:
        detail = step.get("message") or ", ".join(step.get("fields", []))
        print(f"{step['table']}: {step['status']}, {step['rows']} rows" + (f" ({detail})" if detail else ""))
    layers = {step["file"]: step.get("layer") for step in json.loads((args.conversion / "conversion.json").read_text())}
    for layer in LAYERS:
        print(f"{layer} layer: {sum(1 for name in layers.values() if name == layer)} steps")
    if report["draft"]:
        print(report["draft"]["sentence"])
    print(report["routes"]["shares"]["sentence"])
    for problem in report["routes"]["problems"]:
        print("route:", problem)
    print(WORDING["unmapped"].format(rows=f"{unmapped['rows']:,}", variables=f"{unmapped['variables']:,}"))
    if unmapped["rows"]:
        print(WORDING["worklist"])
    for gate in report["gates"]:
        outcome = "could not be run" if gate["rows"] is None else "passed" if gate["rows"] == 0 else f"failed, with {gate['rows']} rows"
        print(f"gate {gate['gate']}: {outcome}")
    for count in report["counts"]:
        print(WORDING["count_not_run"].format(name=count["name"], reason=count["error"]) if count["error"]
              else report_count(count["says"], count["rows"]))
    planted = [s for s in report["scenarios"] if not s["error"]]
    if planted:
        print(WORDING["scenario_planted"].format(rows=f"{sum(s['planted'] for s in planted):,}", count=len(planted)))
    for scenario in report["scenarios"]:
        if scenario["error"]:
            print(WORDING["scenario_not_planted"].format(scenario=scenario["name"], reason=scenario["error"]))
        if scenario["fails_gate"]:
            print(WORDING["scenario_gate"].format(scenario=scenario["name"], gate=scenario["fails_gate"]))
        for item in scenario["expectations"]:
            key = "scenario_not_run" if item["error"] else "scenario_met" if item["met"] else "scenario_not_met"
            print(WORDING[key].format(scenario=scenario["name"], says=item["says"], found=item["found"],
                                      expected=item["expected"], reason=item["error"]))
    for problem in report["problems"]:
        print("problem:", problem)
    if not report["problems"]:
        print("no required field is empty, no primary key is repeated and no text is too long for its field")
    if args.vocabulary:
        concept_file = args.vocabulary / "CONCEPT.csv" if args.vocabulary.is_dir() else args.vocabulary
        concerns = conversion.concept_problems(concept_file)
        for concern in concerns:
            print("concept:", concern)
        if not concerns:
            print("every concept is standard, mapped and in the domain its field expects")
    failed = failures(report)
    if failed:
        print(WORDING["not_clean"].format(count=len(failed)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
