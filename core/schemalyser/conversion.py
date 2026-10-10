"""The conversion folder's format: what conversion.json, its steps and the files beside them hold, and the rules they
keep. The runner over the synthetic database (convert.py), the release script (release.py) and the transplant of a
reference's routes (transplant.py) all read a conversion folder through this module, so that none of them needs another
to know what the folder holds.

A conversion is a folder holding conversion.json, which lists the steps in order with the layer and route of each, one
SELECT for each step, and optionally source_to_concept_map.csv, site_mappings.csv, tables.json, draft.json and the
counts/ folder. The steps belong to the layers core, anaesthesia and derived, and each step of the first two records its
route, over the roles or direct (route_problems). A step may offer alternatives, and a direct step may name the step over
the roles that waits beside it (roles_step). The hospital schema through which a step over the roles is compiled is the
map folder beside the conversion folder (hospital_schema), and the planted scenarios' expected rows are kept apart, in
the held-out root beside it (held_out_root), which is only ever read. convert.py's docstring describes each file at
length.
"""
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


# The planted scenarios, whose expected rows the held-out root keeps.
SCENARIOS = "scenarios"


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


# The steps over the roles, and their role scenarios on the role shadow.

class RolesStepError(ValueError):
    """A step over the roles reads something other than the role views, the mapping views and the OMOP tables."""


def check_roles_step(sql, where="step"):
    """Raises RolesStepError unless sql is one SELECT, by the rule that release.py applies to a step, that reads only the
    role views, the mapping views, its own common table expressions and the OMOP tables as omop.<table>."""
    from . import rolemap
    from .selects import Refused, _single_select
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

