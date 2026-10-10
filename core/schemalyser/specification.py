"""The first stage of compilation: a specification of an export, compiled into SQL over the roles.

A specification is a small public file that names a clinician's choices and holds no SQL and no hospital material, so
that it can be saved, reused for another list of episodes, shared with another hospital and hashed into a package.
Its format is "schemalyser-specification/1":

    {"format": "schemalyser-specification/1",
     "contract_version": "1.1",
     "title": "one line",
     "episodes": {"form": "anaesthetic_keys"}
              or {"form": "patient_dates", "window_hours": 12, "several": "all, marked" or "none"},
     "sections": [{"name": "mean_pressures", "part": "role_reading", "kinds": ["map_arterial"],
                   "window": {"from": "start", "from_minutes": -15, "to": "stop", "to_minutes": 15},
                   "flags": {"accepted": 1}}],
     "derived": [{"name": "minutes_below_40", "capability": "minutes_beyond_threshold", "version": 1,
                  "parameters": {"kind": "map_arterial", "direction": "below", "threshold": 40, "window": "anaesthetic"}}],
     "output": {"class": "rows" or "aggregate", "keys": "pseudonymised" or "as recorded",
                "leaving": ["mean_pressures"]}}

The episode list is a separate private file, never part of the specification, recorded by its hash. It is a CSV file
whose header is anaesthetic_key, or patient_key,date for the patient-and-date form, with one episode on each line.

compile() checks the specification against the role contract, refusing it with named reasons where it breaks a rule,
and writes one statement over the roles for each section: the episodes chosen from the anaesthetics, then the part's
rows within the window, or the capability's value for each episode. The patient-and-date form adds the statement that
resolves each pair to the anaesthetics that started within the window, counts the pairs resolved to exactly one, the
ambiguous pairs and the pairs resolved to none, and keeps only the pairs resolved to exactly one when the rule is
"none". With the keys pseudonymised, every section numbers the episodes in the order of their starts, and a further
section, episode_key, which never leaves, holds the link from each number to its anaesthetic.

A derived section's SQL is the capability's file in rolemodel/capabilities/, with each placeholder {{parameter}}
filled: by capability.py where the catalogue entry names its file, and here otherwise. Where the catalogue declares a
capability that has no file yet, the section is reported as declared without SQL, and nothing is compiled for it.

The statements are SQL over the roles only, which the role policy checks, and which audit.py's export then takes
through the existing path to a package for each section. Without an episode list, the statements choose no episode,
so that the public side can check them; with one, the list's keys are written into them, and the statements are then
as private as the list.

    python -m schemalyser.specification check SPEC
    python -m schemalyser.specification compile SPEC --out FOLDER [--episodes EPISODES.csv]
    python -m schemalyser.specification choices [--out CHOICES.json]
    python -m schemalyser.specification write FIELDS.json --out SPEC

choices says what the export screen offers, from the role contract and the catalogue alone: the sections of the
anaesthetic record in the clinician's words, each with its kinds, its window and its flags, and the catalogue's
measures with their parameters. write turns the screen's choices, as the fields of its form, into a specification,
which is how the screen writes one, so that the command line can do the same with the same fields.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
import sys
from pathlib import Path

import sqlglot

from . import compiler, policy, rolemap, rolepolicy, vocabulary

FORMAT = "schemalyser-specification/1"
FORMS = ("anaesthetic_keys", "patient_dates")
SEVERAL = ("all, marked", "none")
OUTPUT_CLASSES = ("rows", "aggregate")
KEYS = ("pseudonymised", "as recorded")
ANCHORS = ("start", "stop")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
RESERVED = ("episode_key", "episode_resolution")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
CAPABILITIES = rolemap.MODEL / "capabilities"
KEY_LIMIT = 64
# The parts that are the episode itself, which a section may name to have the episode's own row, with no window.
EPISODE_PARTS = ("role_anaesthetic", "role_patient")

RULES = {
    "format": f"The file is one JSON object whose format is {FORMAT}.",
    "contract_version": "The specification names the version of the role contract that Schemalyser holds.",
    "episodes": "The episodes are anaesthetic keys, or patient and date pairs with a window in hours and a rule for several anaesthetics in the window, all, marked or none.",
    "sections": "Each section has a plain name of its own and names a part of the role contract.",
    "kinds": "Each kind that a section names is a kind of its part's vocabulary.",
    "window": "A window runs from the anaesthetic's start or stop to its start or stop, with offsets in whole minutes, on a part that records a time.",
    "flags": "Each flag that a section names is a flag of its part, set to 0 or 1.",
    "link": "Each part is linked to an anaesthetic or to a patient.",
    "derived": "Each derived section names a capability of the catalogue at its version, with a value of the right type for each of its parameters and no other.",
    "output": "The output is rows or aggregate, the keys are pseudonymised or as recorded, and each section that may leave is a section of the specification.",
}

WORDING = {
    "refused": "Schemalyser has not compiled the specification, because it breaks these rules: {reasons}.",
    "episodes_file": "{name} is not an episode list that Schemalyser can read: it is a CSV file whose header is {header}, with one episode on each line.",
    "episodes_value": "Line {line} of {name} does not hold {what}.",
    "episodes_many": "{name} holds {count:,} episodes, more than the {cap:,} that one package's cohort may hold, so the list is divided into smaller lists first.",
    "episodes_empty": "{name} holds no episode.",
    "episodes_form": "{name} holds episodes as {held}, but the specification asks for {wanted}.",
    "compiled": "Schemalyser compiled the specification into {count} statements over the roles in {folder}.",
    "declared": "The capability {capability} is declared in the catalogue, but no SQL has been written for it yet, so this section has no statement.",
    "placeholder": "The SQL of the capability {capability} holds the placeholder {name}, which is not one of its parameters.",
    "capability_sql": "The SQL of the capability {capability} is not one SELECT over the roles.",
    "clash": "The SQL of the capability {capability} names a step {name}, which the export's own steps already use.",
    "no_list": "-- The episode list is a private file, so these statements choose no episode until it is given.",
    "header": "The section {name} of the specification {title}: {says}",
    "part_says": "the rows of {title} for each episode{window}.",
    "derived_says": "the capability {capability}, version {version}, for each episode.",
    "key_says": "the link from each episode's number to its anaesthetic, which never leaves the hospital.",
    "resolution_says": "how many of the patient and date pairs resolved to exactly one anaesthetic, how many to several and how many to none.",
    "window_says": ", from {start} to {stop}",
    "fields": "{name} is not a file of the screen's choices that Schemalyser can read: it holds one JSON object of the form's fields.",
}


class SpecificationError(ValueError):
    """A specification or an episode list that cannot be compiled. reasons lists {"rule", "where", "says"}."""

    def __init__(self, message, reasons=()):
        super().__init__(message)
        self.reasons = list(reasons)


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


# The contract, as the specification reads it.

def _parts(model):
    return {v["name"]: v for v in model["views"]}


def _vocabulary(model, column):
    """The words that a column of a kind may hold."""
    if column.get("vocabulary"):
        return [k["kind"] for k in model["vocabularies"].get(column["vocabulary"], [])]
    return [k["kind"] for k in model["kinds"]]


def kind_column(view):
    """The column of a part that a section's kinds select on: the column named kind, or the part's first column of a
    kind other than its source kind."""
    columns = [c for c in view["columns"] if c["type"] == "kind" and c.get("vocabulary") != "source_kind"]
    named = [c for c in columns if c["name"] == "kind"]
    return (named or columns or [None])[0]


def time_column(view):
    """The column that holds when a part's event happened: its first column of a date and time other than the time
    it was documented."""
    if view["name"] in EPISODE_PARTS:
        return None
    return next((c for c in view["columns"] if c["type"] == "datetime" and c["name"] != "documented_time"), None)


def link_column(view):
    """The column by which a part is joined to an episode: the anaesthetic's key, or failing that the patient's."""
    names = [c["name"] for c in view["columns"]]
    if view["name"] == "role_patient":
        return "patient_key"
    return "anaesthetic_key" if "anaesthetic_key" in names else ("patient_key" if "patient_key" in names else None)


def flag_columns(view):
    return {c["name"] for c in view["columns"] if c["type"] in ("flag", "flag_or_empty")}


def every_kind(model):
    words = {k["kind"] for k in model["kinds"]}
    for items in model["vocabularies"].values():
        words |= {k["kind"] for k in items}
    return words


# What the export screen offers, from the role contract and the catalogue alone.

# The sections of the anaesthetic record as the clinician knows them, in the order of docs/product.md, each a group of
# parts of the role contract. A part that no group names is offered among the further parts of the record.
TREE = (("anaesthetic", ("role_anaesthetic", "role_patient", "role_anaesthetic_detail")), ("readings", ("role_reading",)),
        ("drugs", ("role_drug",)), ("techniques", ("role_technique",)), ("fluids", ("role_fluid",)),
        ("devices", ("role_device",)), ("events", ("role_event",)), ("staff", ("role_staff",)),
        ("operations", ("role_operation",)), ("notes", ("role_note",)))
FURTHER = "further"
# The parts that stay inside the hospital unless the clinician names them as leaving.
STAY_UNLESS_NAMED = ("role_note",)


def section_name(part):
    """The name that the screen gives a section of a part: the part's name without its prefix."""
    return part[len("role_"):] if part.startswith("role_") else part


def _kinds_with_meanings(model, column):
    if column is None:
        return []
    if column.get("vocabulary"):
        return [{"kind": k["kind"], "meaning": k.get("meaning")} for k in model["vocabularies"].get(column["vocabulary"], [])]
    return [{"kind": k["kind"], "meaning": k.get("meaning")} for k in model["kinds"]]


def _part_offer(model, view):
    words = vocabulary.EXPORT_SCREEN
    column = kind_column(view)
    time = time_column(view)
    return {"part": view["name"], "name": section_name(view["name"]),
            "title": words["parts"].get(view["name"], rolemap.view_title(view["name"])),
            "one_row_per": view.get("one_row_per"), "status": view.get("status"), "linked": link_column(view) is not None,
            "episode": view["name"] in EPISODE_PARTS, "kind_column": column["name"] if column else None,
            "kinds": _kinds_with_meanings(model, column),
            "window": {"column": time["name"], "title": rolemap.column_title(view["name"], time["name"])} if time else None,
            "flags": [{"name": c["name"], "title": c.get("title") or c["name"].replace("_", " "), "meaning": c.get("meaning")}
                      for c in view["columns"] if c["type"] in ("flag", "flag_or_empty")],
            "stays_unless_named": view["name"] in STAY_UNLESS_NAMED}


def _capability_offer(capability):
    words = vocabulary.EXPORT_SCREEN
    parameters = []
    for parameter in capability.get("parameters", []):
        parameters.append({
            "name": parameter["name"], "type": parameter["type"], "unit": parameter.get("unit"),
            "meaning": parameter.get("meaning"), "choices": parameter.get("choices") or [],
            "default": parameter.get("default"), "has_default": "default" in parameter,
            "columns": [{"name": c["name"], "type": c["type"],
                         "title": words["table_columns"].get(c["name"], c["name"].replace("_", " "))}
                        for c in parameter.get("columns", [])]})
    has_sql = bool(capability.get("sql")) or (CAPABILITIES / f"{capability['name']}.sql").is_file()
    return {"name": capability["name"], "version": capability.get("version"), "meaning": capability.get("meaning"),
            "grain": capability.get("grain"), "unit": capability.get("unit"), "window": capability.get("window"),
            "output_class": capability.get("output_class"), "has_sql": has_sql, "parameters": parameters,
            "requires": capability.get("requires")}


def choices(model=None):
    """What the export screen offers, from the role contract and the catalogue alone, and so the same at every
    hospital: {"groups": [{"group", "title", "parts": [{"part", "name", "title", "linked", "episode", "kinds",
    "window", "flags", ...}]}], "capabilities": [...], "kinds": every kind with its meaning and vocabulary, "forms",
    "several", "classes", "keys"}. Whether this hospital supports each one is the feasibility report's to say
    (feasibility.sections)."""
    model = model or rolemap.contract()
    words = vocabulary.EXPORT_SCREEN
    parts = _parts(model)
    named = {p for _, held in TREE for p in held}
    groups = list(TREE) + [(FURTHER, tuple(v["name"] for v in model["views"] if v["name"] not in named))]
    found = []
    for group, names in groups:
        offered = [_part_offer(model, parts[n]) for n in names if n in parts]
        if offered:
            found.append({"group": group, "title": words["groups"][group], "parts": offered})
    kinds = [{"kind": k["kind"], "meaning": k.get("meaning"), "vocabulary": "reading"} for k in model["kinds"]]
    for name, items in model["vocabularies"].items():
        if name not in ("source_kind", "mapping_status", "mapping_provenance"):
            kinds += [{"kind": k["kind"], "meaning": k.get("meaning"), "vocabulary": name} for k in items]
    return {"groups": found, "capabilities": [_capability_offer(c) for c in model.get("capabilities", [])],
            "kinds": kinds, "forms": list(FORMS), "several": list(SEVERAL), "classes": list(OUTPUT_CLASSES),
            "keys": list(KEYS), "anchors": list(ANCHORS), "contract_version": model.get("version")}


# The screen's choices, as the fields of its form, made into a specification.

def _field(fields, name, default=""):
    value = fields.get(name, default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return "" if value is None else str(value).strip()


def _fields(fields, name):
    value = fields.get(name, [])
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def _whole(text, empty=None):
    if text == "":
        return empty
    return int(text) if re.fullmatch(r"-?\d+", text) else text


def _number(text):
    if text == "":
        return text
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    try:
        value = float(text)
    except ValueError:
        return text
    return value if math.isfinite(value) else text


def _typed(kind, text):
    """A value of the form as the type that its rule wants, or as written where it is not one, so that validate()
    names the rule that it breaks."""
    if kind == "whole":
        return _whole(text, "")
    if kind == "whole_or_empty":
        return _whole(text, None)
    if kind == "number":
        return _number(text)
    return text


def _table(fields, prefix, columns):
    rows = {}
    pattern = re.compile(re.escape(prefix) + r"\.(\d+)\.(\d+)$")
    for key in fields:
        match = pattern.match(key)
        if match:
            rows.setdefault(int(match.group(1)), {})[int(match.group(2))] = _field(fields, key)
    found = []
    for number in sorted(rows):
        cells = [rows[number].get(n, "") for n in range(len(columns))]
        if any(cells):
            found.append([_typed(column["type"], cell) for column, cell in zip(columns, cells)])
    return found


def from_form(fields, model=None):
    """A specification from the export screen's choices, given as the fields of its form, {name: value or [values]}:

        title                                   the title
        episodes.form, episodes.window_hours, episodes.several
        section                                 each part chosen, as role_x
        name.role_x                             the section's name, where it is not the part's own
        kinds.role_x                            each kind chosen
        window.role_x                           1 to keep only the rows within a window, with window.role_x.from,
                                                .from_minutes, .to and .to_minutes
        flag.role_x.FLAG                        0 or 1, or empty for either
        derived                                 each measure of the catalogue chosen, with dname.NAME for its name
        param.NAME.PARAMETER                    a value, or each value chosen for a list; for a table, one field
                                                param.NAME.PARAMETER.ROW.COLUMN for each cell
        output.class, output.keys               the output
        leave                                   each part or measure that may leave

    Each value is taken as the type that its rule wants where it is one. The result is not checked: validate() says
    which rules it breaks."""
    model = model or rolemap.contract()
    parts = _parts(model)
    catalogue = rolemap.capabilities(model)
    form = _field(fields, "episodes.form", FORMS[0])
    episodes = {"form": form}
    if form == "patient_dates":
        episodes.update(window_hours=_whole(_field(fields, "episodes.window_hours"), ""),
                        several=_field(fields, "episodes.several", SEVERAL[0]))
    spec = {"format": FORMAT, "contract_version": model.get("version"), "title": _field(fields, "title") or None,
            "episodes": episodes, "sections": [], "derived": []}
    if spec["title"] is None:
        del spec["title"]
    names = {}
    for part in dict.fromkeys(_fields(fields, "section")):
        view = parts.get(part)
        section = {"name": _field(fields, f"name.{part}") or section_name(part), "part": part}
        if view is not None:
            kinds = _fields(fields, f"kinds.{part}")
            if kinds:
                section["kinds"] = kinds
            if _field(fields, f"window.{part}") == "1":
                section["window"] = {"from": _field(fields, f"window.{part}.from", "start"),
                                     "from_minutes": _whole(_field(fields, f"window.{part}.from_minutes"), 0),
                                     "to": _field(fields, f"window.{part}.to", "stop"),
                                     "to_minutes": _whole(_field(fields, f"window.{part}.to_minutes"), 0)}
            flags = {}
            for flag in sorted(flag_columns(view)):
                value = _field(fields, f"flag.{part}.{flag}")
                if value:
                    flags[flag] = _whole(value, value)
            if flags:
                section["flags"] = flags
        spec["sections"].append(section)
        names[part] = section["name"]
    for name in dict.fromkeys(_fields(fields, "derived")):
        capability = catalogue.get(name)
        item = {"name": _field(fields, f"dname.{name}") or name, "capability": name,
                "version": capability.get("version") if capability else None, "parameters": {}}
        for parameter in (capability or {}).get("parameters", []):
            key, kind = f"param.{name}.{parameter['name']}", parameter["type"]
            if kind == "table":
                item["parameters"][parameter["name"]] = _table(fields, key, parameter.get("columns", []))
            elif kind == "kinds":
                item["parameters"][parameter["name"]] = _fields(fields, key)
            elif kind == "concepts":
                item["parameters"][parameter["name"]] = [_whole(v.strip(), "") for v in _field(fields, key).split(",") if v.strip()]
            else:
                item["parameters"][parameter["name"]] = _typed(kind, _field(fields, key))
        spec["derived"].append(item)
        names[name] = item["name"]
    leaving = [names[n] for n in dict.fromkeys(_fields(fields, "leave")) if n in names]
    spec["output"] = {"class": _field(fields, "output.class", OUTPUT_CLASSES[0]),
                      "keys": _field(fields, "output.keys", KEYS[0]), "leaving": leaving}
    if not spec["derived"]:
        del spec["derived"]
    return spec


def chosen(spec, model=None):
    """The choices that a specification records, keyed as the screen offers them, so that a loaded specification sets
    the screen's fields: {"title", "episodes", "sections": {part: section}, "derived": {capability: item},
    "output", "leave": [part or capability]}. Without a specification, the screen's starting choices: rows with the
    keys pseudonymised, nothing chosen, and every section allowed to leave but the notes."""
    model = model or rolemap.contract()
    if not spec:
        leave = [v["name"] for v in model["views"] if v["name"] not in STAY_UNLESS_NAMED] + \
            [c["name"] for c in model.get("capabilities", [])]
        return {"title": "", "episodes": {"form": FORMS[0]}, "sections": {}, "derived": {},
                "output": {"class": OUTPUT_CLASSES[0], "keys": KEYS[0]}, "leave": leave}
    sections, derived = {}, {}
    for section in spec.get("sections") or []:
        if isinstance(section, dict):
            sections.setdefault(section.get("part"), section)
    for item in spec.get("derived") or []:
        if isinstance(item, dict):
            derived.setdefault(item.get("capability"), item)
    output = spec.get("output") if isinstance(spec.get("output"), dict) else {}
    leaving = set(output.get("leaving") or [])
    leave = [p for p, s in sections.items() if s.get("name") in leaving] + \
        [c for c, d in derived.items() if d.get("name") in leaving]
    return {"title": spec.get("title") or "", "episodes": spec.get("episodes") or {"form": FORMS[0]},
            "sections": sections, "derived": derived, "output": output, "leave": leave}


# Validation.

def _is_whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parameter_ok(kind, value, words, parameter=None):
    if kind == "choice":
        return value in (parameter or {}).get("choices", [])
    if kind == "whole_or_empty":
        return value is None or _is_whole(value)
    if kind == "table":
        width = len((parameter or {}).get("columns", []))
        return isinstance(value, list) and bool(value) and all(isinstance(r, list) and len(r) == width for r in value)
    if kind == "whole":
        return _is_whole(value)
    if kind == "number":
        return _is_number(value)
    if kind == "text":
        return isinstance(value, str) and 0 < len(value) <= 100 and "\n" not in value
    if kind == "kind":
        return isinstance(value, str) and value in words
    if kind == "kinds":
        return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v in words for v in value)
    if kind == "concepts":
        return isinstance(value, list) and bool(value) and all(_is_whole(v) for v in value)
    return False


def validate(spec, model=None):
    """The rules that a specification breaks, as [{"rule", "where", "says"}], empty when it keeps them all."""
    model = model or rolemap.contract()
    reasons = []

    def fail(rule, where, says):
        reasons.append({"rule": rule, "where": where, "says": says})

    if not isinstance(spec, dict) or spec.get("format") != FORMAT:
        fail("format", "format", f"the format is not {FORMAT}")
        return reasons
    if spec.get("contract_version") != model.get("version"):
        fail("contract_version", "contract_version",
             f"the specification names version {spec.get('contract_version')} and Schemalyser holds {model.get('version')}")
    episodes = spec.get("episodes")
    if not isinstance(episodes, dict) or episodes.get("form") not in FORMS:
        fail("episodes", "episodes.form", "the form is neither anaesthetic_keys nor patient_dates")
    elif episodes["form"] == "patient_dates":
        hours = episodes.get("window_hours")
        if not _is_whole(hours) or not 0 <= hours <= 168:
            fail("episodes", "episodes.window_hours", "the window is not a whole number of hours from 0 to 168")
        if episodes.get("several") not in SEVERAL:
            fail("episodes", "episodes.several", "the rule for several anaesthetics is neither all, marked nor none")
    parts = _parts(model)
    names = []
    sections = spec.get("sections", [])
    derived = spec.get("derived", [])
    if not isinstance(sections, list) or not isinstance(derived, list) or not (sections or derived):
        fail("sections", "sections", "the specification has no section")
        sections, derived = [], []
    for n, section in enumerate(sections):
        where = f"sections[{n}]"
        if not isinstance(section, dict):
            fail("sections", where, "the section is not an object")
            continue
        name = section.get("name")
        if not isinstance(name, str) or not NAME.match(name) or name in RESERVED:
            fail("sections", f"{where}.name", "the name is not a plain lower-case name of its own")
        elif name in names:
            fail("sections", f"{where}.name", f"{name} names two sections")
        names.append(name)
        view = parts.get(section.get("part"))
        if view is None:
            fail("sections", f"{where}.part", f"{section.get('part')} is not a part of the role contract")
            continue
        if link_column(view) is None:
            fail("link", f"{where}.part", f"{view['name']} is linked to neither an anaesthetic nor a patient")
        kinds = section.get("kinds", [])
        column = kind_column(view)
        if kinds:
            if not isinstance(kinds, list) or column is None:
                fail("kinds", f"{where}.kinds", f"{view['name']} has no column of a kind to select on")
            else:
                words = set(_vocabulary(model, column))
                for kind in kinds:
                    if kind not in words:
                        fail("kinds", f"{where}.kinds", f"{kind} is not a kind of {view['name']}.{column['name']}")
        window = section.get("window")
        if window is not None:
            if time_column(view) is None:
                fail("window", f"{where}.window", f"{view['name']} records no time of its own")
            elif not isinstance(window, dict) or window.get("from") not in ANCHORS or window.get("to") not in ANCHORS \
                    or not _is_whole(window.get("from_minutes", 0)) or not _is_whole(window.get("to_minutes", 0)):
                fail("window", f"{where}.window", "the window is not a start or stop with whole minutes at each end")
        flags = section.get("flags", {})
        if not isinstance(flags, dict):
            fail("flags", f"{where}.flags", "the flags are not an object")
        else:
            allowed = flag_columns(view)
            for flag, value in flags.items():
                if flag not in allowed:
                    fail("flags", f"{where}.flags", f"{flag} is not a flag of {view['name']}")
                elif value not in (0, 1) or isinstance(value, bool):
                    fail("flags", f"{where}.flags", f"{flag} is set to {value!r}, where 0 or 1 is wanted")
    catalogue = rolemap.capabilities(model)
    words = every_kind(model)
    for n, item in enumerate(derived):
        where = f"derived[{n}]"
        if not isinstance(item, dict):
            fail("derived", where, "the derived section is not an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not NAME.match(name) or name in RESERVED:
            fail("sections", f"{where}.name", "the name is not a plain lower-case name of its own")
        elif name in names:
            fail("sections", f"{where}.name", f"{name} names two sections")
        names.append(name)
        capability = catalogue.get(item.get("capability"))
        if capability is None:
            fail("derived", f"{where}.capability", f"{item.get('capability')} is not a capability of the catalogue")
            continue
        if item.get("version") != capability.get("version"):
            fail("derived", f"{where}.version",
                 f"{capability['name']} is at version {capability.get('version')}, not {item.get('version')}")
        given = item.get("parameters", {})
        if not isinstance(given, dict):
            fail("derived", f"{where}.parameters", "the parameters are not an object")
            continue
        declared = {p["name"]: p for p in capability.get("parameters", [])}
        for key in given:
            if key not in declared:
                fail("derived", f"{where}.parameters.{key}", f"{key} is not a parameter of {capability['name']}")
        for key, parameter in declared.items():
            if key not in given:
                fail("derived", f"{where}.parameters.{key}", f"{key} is not given")
            elif not _parameter_ok(parameter["type"], given[key], words, parameter):
                fail("derived", f"{where}.parameters.{key}", f"{key} is not a value of the type {parameter['type']}")
    output = spec.get("output")
    if not isinstance(output, dict) or output.get("class") not in OUTPUT_CLASSES:
        fail("output", "output.class", "the output class is neither rows nor aggregate")
    else:
        if output.get("keys") not in KEYS:
            fail("output", "output.keys", "the keys are neither pseudonymised nor as recorded")
        leaving = output.get("leaving", [])
        if not isinstance(leaving, list):
            fail("output", "output.leaving", "the sections that may leave are not a list")
        else:
            for name in leaving:
                if name not in names:
                    fail("output", "output.leaving", f"{name} is not a section of the specification")
    return reasons


def refusal(reasons):
    return WORDING["refused"].format(reasons="; ".join(f"{r['rule']} at {r['where']}: {r['says']}" for r in reasons))


def load(path):
    """Reads and validates a specification file. Returns (spec, its sha256). Raises SpecificationError."""
    path = Path(path)
    data = path.read_bytes()
    try:
        spec = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        reasons = [{"rule": "format", "where": "format", "says": "the file is not JSON"}]
        raise SpecificationError(refusal(reasons), reasons) from None
    reasons = validate(spec)
    if reasons:
        raise SpecificationError(refusal(reasons), reasons)
    return spec, sha256(data)


# The episode list, a private file.

def read_episodes(path, form):
    """Reads the private episode list. Returns {"form", "sha256", "count", "rows"}, where rows are keys, or
    (patient_key, date) pairs. Raises SpecificationError, naming the file and the line but no value."""
    path = Path(path)
    data = path.read_bytes()
    header = "anaesthetic_key" if form == "anaesthetic_keys" else "patient_key,date"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SpecificationError(WORDING["episodes_file"].format(name=path.name, header=header)) from None
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise SpecificationError(WORDING["episodes_empty"].format(name=path.name))
    held = ",".join(cell.strip().lower() for cell in rows[0])
    if held != header:
        if held in ("anaesthetic_key", "patient_key,date"):
            raise SpecificationError(WORDING["episodes_form"].format(
                name=path.name, held="anaesthetic keys" if held == "anaesthetic_key" else "patient and date pairs",
                wanted="anaesthetic keys" if form == "anaesthetic_keys" else "patient and date pairs"))
        raise SpecificationError(WORDING["episodes_file"].format(name=path.name, header=header))
    found = []
    for number, row in enumerate(rows[1:], 2):
        cells = [cell.strip() for cell in row]
        if form == "anaesthetic_keys":
            if len(cells) != 1 or not _key_ok(cells[0]):
                raise SpecificationError(WORDING["episodes_value"].format(line=number, name=path.name, what="one key"))
            found.append(cells[0])
        else:
            try:
                if len(cells) != 2 or not _key_ok(cells[0]):
                    raise ValueError
                found.append((cells[0], dt.date.fromisoformat(cells[1])))
            except ValueError:
                raise SpecificationError(WORDING["episodes_value"].format(
                    line=number, name=path.name, what="a patient's key and a date written as YYYY-MM-DD")) from None
    if not found:
        raise SpecificationError(WORDING["episodes_empty"].format(name=path.name))
    if len(found) > policy.CAP:
        raise SpecificationError(WORDING["episodes_many"].format(name=path.name, count=len(found), cap=policy.CAP))
    return {"form": form, "sha256": sha256(data), "count": len(found), "rows": found}


def _key_ok(value):
    return 0 < len(value) <= KEY_LIMIT and all(32 <= ord(c) < 127 for c in value)


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def episode_period(episodes, hours):
    """The period that holds every anaesthetic a list of pairs can resolve to: from the earliest date less the window
    to the latest date plus the window."""
    days = math.ceil(hours / 24)
    dates = [d for _, d in episodes["rows"]]
    return (min(dates) - dt.timedelta(days=days)).isoformat(), (max(dates) + dt.timedelta(days=days)).isoformat()


# The statements.

ANAESTHETIC = ("anaesthetic_key", "patient_key", "start_time", "stop_time")


def _indent(text, by="    "):
    return "\n".join(by + line if line else line for line in text.splitlines())


def _episode_ctes(spec, episodes):
    """The common table expressions that choose the episodes, as a list of "name AS (...)" texts, ending with the step
    named episode, which holds one row for each anaesthetic chosen."""
    form = spec["episodes"]["form"]
    columns = ", ".join(f"a.{c}" for c in ANAESTHETIC)
    if form == "anaesthetic_keys":
        chosen = ("a.anaesthetic_key IN (" + ", ".join(_literal(k) for k in episodes["rows"]) + ")") if episodes else "1 = 0"
        return [f"episode AS (\n    SELECT {columns}\n    FROM   role_anaesthetic AS a\n    WHERE  {chosen}\n)"]
    return _pair_ctes(spec, episodes) + [_pair_episode(spec)]


def _pair_ctes(spec, episodes):
    hours = spec["episodes"]["window_hours"]
    columns = ", ".join(f"a.{c}" for c in ANAESTHETIC)
    if episodes:
        patients = sorted({p for p, _ in episodes["rows"]})
        chosen = "a.patient_key IN (" + ", ".join(_literal(p) for p in patients) + ")"
        lines = [f"SELECT {n} AS pair_number, {_literal(p)} AS patient_key, CAST('{d.isoformat()}' AS datetime) AS episode_day"
                 if n == 1 else f"SELECT {n}, {_literal(p)}, CAST('{d.isoformat()}' AS datetime)"
                 for n, (p, d) in enumerate(episodes["rows"], 1)]
        pairs = "\n    UNION ALL\n    ".join(lines)
    else:
        chosen = "1 = 0"
        pairs = ("SELECT CAST(NULL AS int) AS pair_number, CAST(NULL AS varchar(64)) AS patient_key, "
                 "CAST(NULL AS datetime) AS episode_day\n    WHERE  1 = 0")
    return [
        f"candidate AS (\n    SELECT {columns}\n    FROM   role_anaesthetic AS a\n    WHERE  {chosen}\n)",
        f"pairs AS (\n    {pairs}\n)",
        ("matched AS (\n    SELECT p.pair_number, c.anaesthetic_key\n    FROM   pairs AS p\n"
         "    JOIN   candidate AS c ON c.patient_key = p.patient_key\n"
         f"    WHERE  c.start_time >= DATEADD(hour, {-hours}, p.episode_day)\n"
         f"      AND  c.start_time < DATEADD(hour, {24 + hours}, p.episode_day)\n)"),
        ("per_pair AS (\n    SELECT p.pair_number, COUNT(m.anaesthetic_key) AS matches\n    FROM   pairs AS p\n"
         "    LEFT JOIN matched AS m ON m.pair_number = p.pair_number\n    GROUP  BY p.pair_number\n)"),
    ]


def _pair_episode(spec):
    only = "\n    WHERE  pp.matches = 1" if spec["episodes"]["several"] == "none" else ""
    keys = ", ".join(f"c.{c}" for c in ANAESTHETIC)
    return (f"episode AS (\n    SELECT {keys},\n           MIN(m.pair_number) AS pair_number,\n"
            "           MAX(CASE WHEN pp.matches > 1 THEN 1 ELSE 0 END) AS ambiguous\n"
            "    FROM   candidate AS c\n    JOIN   matched AS m ON m.anaesthetic_key = c.anaesthetic_key\n"
            f"    JOIN   per_pair AS pp ON pp.pair_number = m.pair_number{only}\n"
            f"    GROUP  BY {keys}\n)")


def _numbered(spec):
    keys = ", ".join(f"e.{c}" for c in ANAESTHETIC + (("pair_number", "ambiguous") if spec["episodes"]["form"] == "patient_dates" else ()))
    return (f"numbered AS (\n    SELECT {keys},\n"
            "           ROW_NUMBER() OVER (ORDER BY e.start_time, e.anaesthetic_key) AS episode_number\n"
            "    FROM   episode AS e\n)")


def _episode_columns(spec, pseudonymised):
    """The columns of the episode that every row-level section begins with."""
    first = ["e.episode_number"] if pseudonymised else ["e.anaesthetic_key"]
    if spec["episodes"]["form"] == "patient_dates":
        first += ["e.pair_number", "e.ambiguous"]
    return first


def _source(pseudonymised):
    return "numbered AS e" if pseudonymised else "episode AS e"


def _window(section, view):
    window = section.get("window")
    if not window:
        return [], ""
    column = time_column(view)["name"]
    start = f"DATEADD(minute, {window.get('from_minutes', 0)}, e.{window['from']}_time)"
    stop = f"DATEADD(minute, {window.get('to_minutes', 0)}, e.{window['to']}_time)"
    says = WORDING["window_says"].format(start=_plain_anchor(window["from"], window.get("from_minutes", 0)),
                                         stop=_plain_anchor(window["to"], window.get("to_minutes", 0)))
    return [f"x.{column} >= {start}", f"x.{column} <= {stop}"], says


def _plain_anchor(anchor, minutes):
    if not minutes:
        return f"the anaesthetic's {anchor}"
    return f"{abs(minutes)} minutes {'before' if minutes < 0 else 'after'} the anaesthetic's {anchor}"


def _part_statement(spec, section, view, model, pseudonymised, aggregate):
    link = link_column(view)
    conditions, window_says = _window(section, view)
    kinds = section.get("kinds") or []
    column = kind_column(view)
    if kinds:
        conditions.insert(0, f"x.{column['name']} IN (" + ", ".join(_literal(k) for k in kinds) + ")")
    for flag, value in sorted((section.get("flags") or {}).items()):
        conditions.append(f"x.{flag} = {int(value)}")
    if view["name"] == "role_anaesthetic":
        joined = "FROM   " + _source(pseudonymised)
        alias_columns = []
    else:
        joined = f"FROM   {_source(pseudonymised)}\nJOIN   {view['name']} AS x ON x.{link} = e.{link}"
        alias_columns = [c for c in view["columns"] if c["name"] != link]
    where = ("\nWHERE  " + "\n  AND  ".join(conditions)) if conditions else ""
    if aggregate:
        group = [f"x.{column['name']}"] if kinds and column is not None else []
        select = group + ["COUNT(*) AS rows_charted", "COUNT(DISTINCT e.anaesthetic_key) AS anaesthetics"]
        final = "SELECT " + ",\n       ".join(select) + "\n" + joined + where + \
            (("\nGROUP  BY " + ", ".join(group)) if group else "")
    else:
        kept = [f"x.{c['name']}" for c in alias_columns if not (pseudonymised and c["type"] == "key")]
        if view["name"] == "role_anaesthetic":
            kept = ["e.start_time", "e.stop_time"]
        select = _episode_columns(spec, pseudonymised) + kept
        final = "SELECT " + ",\n       ".join(select) + "\n" + joined + where
    says = WORDING["part_says"].format(title=rolemap.view_title(view["name"], False), window=window_says)
    return final, says


def capability_sql(name, parameters, declared, entry=None):
    """The SQL of a capability with its placeholders filled, or None where the catalogue declares it without SQL. An
    entry that names its SQL file is filled by capability.py, which checks the file against the entry; otherwise the
    file rolemodel/capabilities/NAME.sql is filled here, where it exists."""
    if entry is not None and entry.get("sql"):
        from . import capability
        try:
            return capability.fill(name, parameters)
        except capability.CapabilityError as error:
            reasons = [{"rule": "derived", "where": name, "says": str(error)}]
            raise SpecificationError(refusal(reasons), reasons) from None
    path = CAPABILITIES / f"{name}.sql"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    types = {p["name"]: p["type"] for p in declared}

    def fill(match):
        key = match.group(1)
        if key not in types:
            raise SpecificationError(WORDING["placeholder"].format(capability=name, name=key))
        value, kind = parameters[key], types[key]
        if kind in ("whole", "number"):
            return repr(value)
        if kind in ("kinds",):
            return ", ".join(_literal(v) for v in value)
        if kind == "concepts":
            return ", ".join(str(int(v)) for v in value)
        return _literal(value)
    return PLACEHOLDER.sub(fill, text)


def _derived_statement(spec, item, capability, pseudonymised, aggregate, taken):
    """The derived section's final SELECT and the steps it adds, or (None, None) where the capability has no SQL."""
    text = capability_sql(capability["name"], item.get("parameters", {}), capability.get("parameters", []), capability)
    if text is None:
        return None, None
    try:
        tree = compiler.check_audit(text, where=capability["name"]).copy()
    except (rolemap.MapError, sqlglot.errors.SqlglotError):
        raise SpecificationError(WORDING["capability_sql"].format(capability=capability["name"])) from None
    key = "with_" if "with_" in tree.arg_types else "with"
    held = tree.args.get(key)
    steps = []
    if held is not None:
        for cte in held.expressions:
            if cte.alias.lower() in taken:
                raise SpecificationError(WORDING["clash"].format(capability=capability["name"], name=cte.alias))
            steps.append(f"{cte.alias} AS (\n{_indent(cte.this.sql(dialect='tsql', pretty=True))}\n)")
        tree.set(key, None)
    if "anaesthetic_key" not in {n.lower() for n in tree.named_selects}:
        raise SpecificationError(WORDING["capability_sql"].format(capability=capability["name"]))
    steps.append(f"derived_value AS (\n{_indent(tree.sql(dialect='tsql', pretty=True))}\n)")
    joined = f"FROM   {_source(pseudonymised)}\nLEFT JOIN derived_value AS d ON d.anaesthetic_key = e.anaesthetic_key"
    if aggregate:
        final = "SELECT COUNT(*) AS anaesthetics,\n       COUNT(d.anaesthetic_key) AS anaesthetics_with_a_value\n" + joined
    else:
        values = [f"d.{n}" for n in tree.named_selects if n.lower() != "anaesthetic_key"]
        final = "SELECT " + ",\n       ".join(_episode_columns(spec, pseudonymised) + values) + "\n" + joined
    return final, steps


def _statement(ctes, final, header):
    return header + "\nWITH " + ",\n".join(ctes) + "\n" + final + "\n"


def compile(spec, episodes=None, spec_sha256=None, title=None):
    """Compiles a valid specification into statements over the roles. episodes, when given, is read_episodes()'s
    result. Returns {"format", "specification_sha256", "episodes", "period", "sections": [{"name", "kind", "part" or
    "capability", "output_class", "leaves", "status", "sql", "says", "role_policy"}]}."""
    model = rolemap.contract()
    reasons = validate(spec, model)
    if reasons:
        raise SpecificationError(refusal(reasons), reasons)
    if episodes is not None and episodes["form"] != spec["episodes"]["form"]:
        raise SpecificationError(WORDING["episodes_form"].format(
            name="The episode list", held=episodes["form"].replace("_", " "), wanted=spec["episodes"]["form"].replace("_", " ")))
    title = title or spec.get("title") or "without a title"
    output = spec["output"]
    pseudonymised = output["keys"] == "pseudonymised"
    aggregate = output["class"] == "aggregate"
    leaving = set(output.get("leaving", []))
    parts = _parts(model)
    catalogue = rolemap.capabilities(model)
    base = _episode_ctes(spec, episodes) + ([_numbered(spec)] if pseudonymised else [])
    taken = {re.match(r"(\w+) AS", c).group(1).lower() for c in base}
    lead = [] if episodes else [WORDING["no_list"]]
    sections = []

    def header(name, says):
        return "\n".join(_comment(WORDING["header"].format(name=name, title=title, says=says)) + lead)

    for section in spec.get("sections", []):
        view = parts[section["part"]]
        final, says = _part_statement(spec, section, view, model, pseudonymised, aggregate)
        sections.append({"name": section["name"], "kind": "part", "part": view["name"],
                         "output_class": output["class"], "leaves": section["name"] in leaving, "status": "compiled",
                         "says": says, "sql": _statement(base, final, header(section["name"], says))})
    for item in spec.get("derived", []):
        capability = catalogue[item["capability"]]
        final, steps = _derived_statement(spec, item, capability, pseudonymised, aggregate, taken)
        says = WORDING["derived_says"].format(capability=capability["name"], version=capability["version"])
        entry = {"name": item["name"], "kind": "derived", "capability": capability["name"],
                 "version": capability["version"], "parameters": item.get("parameters", {}),
                 "output_class": output["class"], "leaves": item["name"] in leaving}
        if final is None:
            entry.update(status="declared", says=WORDING["declared"].format(capability=capability["name"]), sql=None)
        else:
            text = _statement(base + steps, final, header(item["name"], says) + f"\n-- capability: {capability['name']}")
            entry.update(status="compiled", says=says, sql=text)
        sections.append(entry)
    if pseudonymised:
        final = "SELECT e.episode_number, e.anaesthetic_key\nFROM   numbered AS e"
        sections.append({"name": "episode_key", "kind": "key", "part": "role_anaesthetic", "output_class": "rows",
                         "leaves": False, "status": "compiled", "says": WORDING["key_says"],
                         "sql": _statement(base, final, header("episode_key", WORDING["key_says"]))})
    period = None
    if spec["episodes"]["form"] == "patient_dates":
        final = ("SELECT COUNT(*) AS pairs,\n"
                 "       SUM(CASE WHEN pp.matches = 1 THEN 1 ELSE 0 END) AS resolved_to_one,\n"
                 "       SUM(CASE WHEN pp.matches > 1 THEN 1 ELSE 0 END) AS ambiguous,\n"
                 "       SUM(CASE WHEN pp.matches = 0 THEN 1 ELSE 0 END) AS resolved_to_none\n"
                 "FROM   per_pair AS pp")
        sections.append({"name": "episode_resolution", "kind": "resolution", "part": "role_anaesthetic",
                         "output_class": "aggregate", "leaves": False, "status": "compiled",
                         "says": WORDING["resolution_says"],
                         "sql": _statement(_pair_ctes(spec, episodes), final,
                                           header("episode_resolution", WORDING["resolution_says"]))})
        if episodes:
            period = episode_period(episodes, spec["episodes"]["window_hours"])
    for entry in sections:
        if entry["sql"] is not None:
            found = rolepolicy.check(entry["sql"])
            entry["role_policy"] = {"outcome": found["outcome"], "failed": found["failed"]}
        else:
            entry["role_policy"] = None
    record = None if episodes is None else {"form": episodes["form"], "sha256": episodes["sha256"], "count": episodes["count"]}
    return {"format": FORMAT, "specification_sha256": spec_sha256 or sha256(json.dumps(spec, sort_keys=True)),
            "contract_version": model.get("version"), "title": title, "episodes": record, "period": period,
            "output": {"class": output["class"], "keys": output["keys"], "leaving": sorted(leaving)},
            "sections": sections}


def _comment(sentence, width=116):
    words, lines, line = sentence.split(), [], "--"
    for word in words:
        if len(line) + 1 + len(word) > width and line != "--":
            lines.append(line)
            line = "--"
        line += " " + word
    lines.append(line)
    return lines


def write(compiled, out):
    """Writes compiled.json and one file of SQL for each section into out. Returns the paths written."""
    out = Path(out)
    (out / "sections").mkdir(parents=True, exist_ok=True)
    written = []
    record = json.loads(json.dumps(compiled))
    for entry in record["sections"]:
        if entry["sql"] is not None:
            path = out / "sections" / f"{entry['name']}.sql"
            path.write_text(entry["sql"], encoding="utf-8")
            entry["sql"] = f"sections/{entry['name']}.sql"
            entry["sql_sha256"] = sha256(path.read_text(encoding="utf-8"))
            written.append(path)
    (out / "compiled.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return written + [out / "compiled.json"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.specification",
                                     description="Compile a specification of an export into SQL over the roles.")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("check", help="Check a specification against the role contract.")
    one.add_argument("specification")
    two = commands.add_parser("compile", help="Compile a specification into one statement over the roles for each section.")
    two.add_argument("specification")
    two.add_argument("--out", required=True)
    two.add_argument("--episodes", help="the private episode list, as CSV")
    three = commands.add_parser("choices", help="Say what the export screen offers, from the role contract and the catalogue.")
    three.add_argument("--out", help="the JSON file to write, rather than the screen")
    four = commands.add_parser("write", help="Write a specification from the export screen's choices, as the fields of its form.")
    four.add_argument("fields", help="a JSON object of the form's fields, {name: value or [values]}")
    four.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "choices":
        text = json.dumps(choices(), indent=2, ensure_ascii=False) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if args.command == "write":
        try:
            fields = json.loads(Path(args.fields).read_text(encoding="utf-8"))
            if not isinstance(fields, dict):
                raise ValueError
        except (OSError, ValueError):
            print(WORDING["fields"].format(name=Path(args.fields).name), file=sys.stderr)
            return 1
        spec = from_form(fields)
        Path(args.out).write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        reasons = validate(spec)
        if reasons:
            print(refusal(reasons), file=sys.stderr)
            return 1
        print(f"The specification keeps every rule of {FORMAT}.")
        return 0
    try:
        spec, digest = load(args.specification)
        if args.command == "check":
            print(f"The specification keeps every rule of {FORMAT}.")
            return 0
        episodes = read_episodes(args.episodes, spec["episodes"]["form"]) if args.episodes else None
        compiled = compile(spec, episodes, digest)
        write(compiled, args.out)
        print(WORDING["compiled"].format(count=sum(1 for s in compiled["sections"] if s["sql"]), folder=Path(args.out).name))
        for entry in compiled["sections"]:
            if entry["status"] == "declared":
                print(entry["says"])
    except SpecificationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
