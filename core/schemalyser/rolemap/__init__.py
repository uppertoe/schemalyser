"""Roles, maps and the standard counts: an audit written once against role views, and run at any hospital through its map.

A role describes part of an anaesthetic record with no hospital in mind. The contract, in rolemodel/contract.json, is
three role views with fixed columns:

    role_patient     (patient_key, birth_date, death_date, is_test)
    role_anaesthetic (anaesthetic_key, patient_key, start_time, stop_time)
    role_reading     (anaesthetic_key, kind, reading_time, value, accepted, reading_key, value_text)

These three are version 1 of the contract, and each carries the status contract in contract.json; the further views
carry the status draft, because no audit reads them yet. Version 1.1 adds beside them the mapping views (mapping_views),
which give the standard concept of each opaque local key that a draft part holds in place of a hospital's code, the
vocabulary of source kinds that marks each row of a part that records events (event_parts, source_kinds), and the
capability catalogue (capabilities). A map's private translation of its codes is its "concepts" section, from which
mapping_sql writes each mapping view with every code replaced by its key, and a part that records events may hold
further "pathways", each with its own source kind, which the compiled view unites.

An audit, such as rolemodel/neonatal_low_mean_pressure.sql, is one T-SQL SELECT that reads only those views.

A map, one for each hospital, is a folder that supplies each role view as one SELECT over that hospital's own tables,
in a file named after the view, and map.json, which says how each binding is known:

    {"world": "...", "description": "...",
     "roles": {"role_patient": {"file": "role_patient.sql",
                                "rows": EVIDENCE,                       one row of what is one row of the view
                                "columns": {"patient_key": EVIDENCE, ...}}, ...},
     "kinds": {"map_arterial": {"codes": ["52"], ...EVIDENCE}, "map_cuff": {...}},
     "eras": [{"about": "...", "note": "..."}],
     "questions": [{"about": "...", "question": "..."}]}

    EVIDENCE is {"status": "proposed" | "seen" | "person" | "count", "from": "where it comes from",
                 "says": "one sentence", "question": "one sentence for a person, unless a person or a count confirmed it"}

A binding is proposed when it is a reasoned guess, seen when the team's own queries use it in the same way, and
confirmed when a person or a count has settled it. Every binding that is not confirmed is an open item, with its
question, and the open items are the list that an Outline stage works through.

An audit compiled with a map is one T-SQL statement, with the role views as common table expressions, so that the
analytics team runs one query; compiler.py compiles it. The role-level shadow, a DuckDB database that holds the three
role views as tables filled with synthetic rows from a seed and the planted neonates, on which an audit can be
rehearsed before any map exists, is roleshadow.py's. This module writes the standard counts, each a query over the role
views that works at any hospital once compiled with its map, and reads their results into plain findings. An audit's
result always carries the coverage by year, because the worst errors of a map do not fail: they move children silently
into the band in which nothing was recorded.

The command line, a surface of layer 5, is rolemap/__main__.py:

    python -m schemalyser.rolemap check MAP --catalogue CATALOGUE.csv
    python -m schemalyser.rolemap compile MAP [--audit AUDIT.sql] [--counts] [--exact]
    python -m schemalyser.rolemap rehearse [--audit AUDIT.sql] [--seed 1] [--no-planted]
    python -m schemalyser.rolemap shadow WORLD CONVERSION MAP [--audit AUDIT.sql] [--rows 500]
    python -m schemalyser.rolemap open MAP
    python -m schemalyser.rolemap propose DICTIONARY.csv --catalogue CATALOGUE.csv --out FOLDER [--tables TABLES.csv]
                                          [--model contract.json] [--heading FIELD=HEADING] [--base VIEW=TABLE]
                                          [--reference lineage.json]
    python -m schemalyser.rolemap confirm MAP CONFIRMATIONS.csv [--catalogue CATALOGUE.csv] [--dictionary DICTIONARY.csv]
    python -m schemalyser.rolemap scoreboard FILE

scoreboard reads a saved hospital schema, as the one file that the page saves, its folder or its map.json, and prints
how the proposals fared, as counts that name no table or column, overall, for each part and for each of five
categories of column: keys, links, timestamps, codes and descriptive columns. It writes the same counts alone, as
scoreboard-summary.json and scoreboard-summary.md (summaries.write_scoreboard), beside the file it read, or inside the
folder where it was given one.

propose and confirm are written in propose.py, and the dictionary is read by datadict.py.
"""
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from ..catalogue import Catalogue
from ..extract import decode

MODEL = Path(__file__).parent.parent / "rolemodel"
AUDIT = MODEL / "neonatal_low_mean_pressure.sql"
PLANTED = MODEL / "planted_neonates.json"
PLANTED_CONCEPTS = MODEL / "planted_concepts.json"
MAP_FILE = "map.json"
STATUSES = ("proposed", "seen", "person", "count")
CONFIRMED = ("person", "count")
# The further fields that a draft map from the proposer carries in its evidence: the binding as data, from which the
# view's SQL is written again after a confirmation, the confidence and the other candidates of the proposal, the
# person's answer with its date, and where the binding came from (a person, or an inference), which the saved hospital
# schema adds. proposed_from says that the proposal rested on a reference conversion's lineage rather than on the
# dictionary, and stays after a person has answered.
PROPOSAL_FIELDS = ("binding", "confidence", "candidates", "confirmation", "provenance", "proposed_from")
MEAN_KINDS = ("map_arterial", "map_cuff")
# The project's least count and the step to which every count is rounded down, as for the check script.
MINIMUM_COUNT = 10
# The bands of the result of an audit in which nothing was recorded, by the number that the audit's banded CTE gives them.
NOTHING_BAND = 0
# How far the share of a year must fall below the best year's share, and how many anaesthetics it must leave
# without, before the counts flag that year as a possible change in how the data is held.
CLIFF_SHARE = 0.5
CLIFF_LEAST = 10
OUTSIDE_SHARE = 0.25

# The wording that a person reads, in one place.
WORDING = {
    "not_one_select": "{where}: a role view is one SELECT, by the rule that the release script applies to a step ({reason}).",
    "with": "{where}: a role view may not begin with WITH, because the compiled query places it among common table expressions.",
    "columns": "{where}: the view gives the columns {found}, and the contract asks for exactly {wanted}, in that order.",
    "table_part": "{where}: the table {table} is named in one part, as the catalogue names it.",
    "table_unknown": "{where}: the catalogue does not hold the table {table}.",
    "column_unknown": "{where}: the catalogue does not hold the column {column} of {table}.",
    "column_alias": "{where}: the column {column} names no table that the SELECT reads, so each column must be named with its table's alias.",
    "column_derived": "{where}: the derived table {alias} gives no column {column}.",
    "role_name": "{where}: a role view reads the hospital's own tables, and not {table}.",
    "map_json": "{where} cannot be read as JSON.",
    "map_shape": "{where}: {problem}.",
    "sentence": "{where}: {field} is one sentence of at most 400 characters, on one line, that ends with a full stop and holds no question mark, exclamation mark or $(.",
    "audit_reads": "{where}: an audit reads only the role views and its own common table expressions, and not {table}.",
    "audit_select": "{where}: an audit is one SELECT ({reason}).",
    # The compiled query.
    "compiled": "-- Schemalyser compiled this query from the audit above and the map of {world}. Each role view is one SELECT from the map, read WITH (NOLOCK), which takes no row locks but holds a schema lock while it runs, so the query should not run during the nightly load.",
    "names": "-- The map names the tables and local codes of the hospital's database, so this query is for use inside the hospital only.",
    "view": "-- {view}: {says}",
    "mapping": "-- {view}: the hospital schema's translation of its local codes, each replaced by its opaque key.",
    "blank": "-- The final SELECT leaves blank any count from 1 to 4, so that no small number can point to a child. It may be removed where the audit's approval allows exact small numbers.",
    # The findings that the reader gives.
    "cliff": "The share of anaesthetics {figure} falls from {best_count} of {best_total} in {best_year} to {count} of {total} in {year}. A fall as sharp as this between years usually means that the data is held differently in those years, so the map may not reach it there.",
    "outside": "In {year}, {count} of the {total} mean pressures linked to an anaesthetic fall outside its start and stop, against {best_count} of {best_total} in {best_year}. A change like this usually means that times are held differently in that year, for example in another time zone.",
    "kind_years": "The map gives {kind} readings in {present} and none in {absent}, in which the counts hold anaesthetics.",
    "repeated": "{count} keys of {view} repeat, holding {rows} rows between them.",
    "repeated_few": "Fewer than ten keys of {view} repeat.",
    "no_stop": "The share of anaesthetics with a recorded stop is {count} of {total} in {year}.",
    "gaps": "The commonest gap between consecutive {kind} readings is {band} in {years}, and {other_band} in {other_years}.",
    "nothing_flagged": "Of {band_words} in this band, {flagged} {verb} from years that the counts flag ({years}), where the map may not reach the readings that were recorded.",
    "nothing_clear": "None of the {count} anaesthetics in this band comes from a year that the counts flag.",
    "nothing_clear_one": "The one anaesthetic in this band does not come from a year that the counts flag.",
    "nothing_empty": "No anaesthetic falls in this band.",
    "nothing_blank": "Of the anaesthetics in this band, at least {flagged} come from years that the counts flag ({years}), where the map may not reach the readings that were recorded; a year with fewer than five is left blank.",
    "open_item": "{about}: {says}",
}
FIGURES = {"with_patient": "whose patient the map finds", "with_birth_date": "whose patient has a date of birth",
           "with_stop": "with a recorded stop", "with_reading": "with any reading in their record",
           "with_mean_pressure": "with an accepted mean pressure"}


class MapError(ValueError):
    """A map, a role view or an audit breaks a rule of the contract."""


def contract():
    """The contract: the role views, their columns, types and meanings, and the vocabulary of kinds, as data."""
    return json.loads((MODEL / "contract.json").read_text(encoding="utf-8"))


def part_hashes(model=None):
    """The hash of each part's definition in the contract, as {view: hash}: the view itself, with the kinds or the
    vocabularies that its columns of a kind name and the mapping views that its columns of a local key name, and the
    hash of each mapping view. A change to any of them is a change to the part, which makes the evidence that rested
    on it stale."""
    import hashlib
    model = model or contract()
    found = {}
    mappings = {m["name"]: m for m in model.get("mapping_views", [])}
    for view in model["views"] + model.get("mapping_views", []):
        named = sorted({c.get("vocabulary") for c in view["columns"] if c.get("vocabulary")})
        definition = {"view": view, "vocabularies": {n: model["vocabularies"].get(n) for n in named}}
        mapped = sorted({c["mapping"] for c in view["columns"] if c.get("mapping")})
        if mapped:
            definition["mappings"] = {n: mappings.get(n) for n in mapped}
        if view["name"] == "role_reading":
            definition["kinds"] = model["kinds"]
        text = json.dumps(definition, sort_keys=True, ensure_ascii=False)
        found[view["name"]] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return found


def mapping_views(model=None):
    """The mapping views of the contract, as {name: view}, each with the same four columns: local_key, concept_id,
    status and provenance."""
    return {m["name"]: m for m in (model or contract()).get("mapping_views", [])}


def mapping_columns():
    """The columns of every mapping view, in order."""
    return [c["name"] for c in next(iter(mapping_views().values()))["columns"]]


def source_kinds(model=None):
    """The kinds of source record that a part which records events may mark its rows with."""
    return [k["kind"] for k in (model or contract())["vocabularies"]["source_kind"]]


def event_parts(model=None):
    """The parts that record events, which are those with a column of the source kind, as {name: view}."""
    return {v["name"]: v for v in (model or contract())["views"] if any(c.get("per_pathway") for c in v["columns"])}


def capabilities(model=None):
    """The capability catalogue, as {name: capability}."""
    return {c["name"]: c for c in (model or contract()).get("capabilities", [])}


def concept_key(salt, mapping, code):
    """The opaque local key of a hospital's code in a mapping view: a stable hash of the code with the hospital
    schema's own salt, so that the key names no code and cannot be turned back into one without the schema."""
    import hashlib
    text = json.dumps([salt or "", mapping, str(code)], ensure_ascii=False)
    return "k" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:15]


# What a column of a local key gives for a code that the hospital schema has not listed in its mapping view, and that
# key's one row in the view.
UNLISTED = "unlisted"


def mapping_sql(name, rows):
    """The SQL of one mapping view from the private translation's rows, with every local code replaced by its key:
    one SELECT of literal rows for each, joined by UNION ALL, and an empty SELECT where there are none. rows is
    [{"local_key", "concept_id", "status", "provenance"}]."""
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    if not rows:
        return (f"SELECT CAST(NULL AS varchar(32)) AS local_key, CAST(NULL AS int) AS concept_id, "
                f"CAST(NULL AS varchar(16)) AS status, CAST(NULL AS varchar(40)) AS provenance WHERE 1 = 0")
    lines = [f"SELECT {literal(r['local_key'])} AS local_key, {int(r['concept_id'] or 0)} AS concept_id, "
             f"{literal(r['status'])} AS status, {literal(r['provenance'])} AS provenance" for r in rows]
    return "\nUNION ALL\n".join(lines)


def views():
    """The role views that every map supplies, as {name: [column, ...]}, in the contract's order. These are the
    three views that the neonatal audit reads."""
    return {view["name"]: [column["name"] for column in view["columns"]] for view in contract()["views"] if view.get("required")}


def all_views():
    """Every role view of the contract, the further views that a map may supply included, as {name: [column, ...]}."""
    return {view["name"]: [column["name"] for column in view["columns"]] for view in contract()["views"]}


def public_views():
    """Every view that a query over the roles may name: the role views and the mapping views, as {name: [column]}."""
    found = all_views()
    found.update({name: [c["name"] for c in view["columns"]] for name, view in mapping_views().items()})
    return found


def anchors(view):
    """The columns of a view, as a contract's view gives them, without which a row does not belong in the view: each
    link that is part of its key, and the anchor that the contract names, as a reading needs its anaesthetic."""
    links = {link["column"] for link in view.get("links", [])}
    return ({c for c in view.get("key", []) if c in links} | ({view["anchor"]} if view.get("anchor") else set()))


def statuses():
    """The status of each role view, as {name: "contract" | "draft"}: contract for the views of version 1.0 that the
    audits read, and draft for a further view that no audit reads yet."""
    return {view["name"]: view.get("status") or ("contract" if view.get("required") else "draft") for view in contract()["views"]}


def kinds():
    return [item["kind"] for item in contract()["kinds"]]


# The plain names of the views and their columns, which a person reads in place of the code names. Each view's title
# names the part of the record ("Patients"), and each column's title is a short phrase ("date of birth").

_TITLES = None


def _titles():
    global _TITLES
    if _TITLES is None:
        model = contract()
        _TITLES = ({view["name"]: view.get("title") or view["name"] for view in model["views"]},
                   {(view["name"], column["name"]): column.get("title") or column["name"].replace("_", " ")
                    for view in model["views"] for column in view["columns"]})
    return _TITLES


def _start(text, start):
    if start:
        return text[:1].upper() + text[1:]
    return "the " + text[4:] if text.startswith("The ") else text


def view_title(view, start=True):
    """A view's plain name, as at the start of a sentence, or within one where start is false."""
    return _start(_titles()[0].get(view, view), start)


def column_title(view, column):
    """A column's plain name as a phrase, such as "date of birth", without an article."""
    return _titles()[1].get((view, column), column.replace("_", " "))


def plain_about(about, start=False):
    """A binding named as role_view.column, role_view rows or role_view, in plain words: "the date of birth in
    Patients", "the rows of Patients" or "Patients"."""
    rows = re.fullmatch(r"(role_\w+) rows", about or "")
    column = re.fullmatch(r"(role_\w+)\.(\w+)", about or "")
    if rows:
        found = f"the rows of {view_title(rows.group(1), False)}"
    elif column:
        title = column_title(column.group(1), column.group(2))
        part = view_title(column.group(1), False)
        found = f"{'' if title.startswith('the ') else 'the '}{title}"
        # A column whose title already names its part, as "sex at birth" names the patient's details at birth, is not
        # followed by the part again.
        if title.lower().split()[-2:] != part.lower().split()[-2:]:
            found += f" in {part}"
    else:
        return view_title(about, start)
    return _start(found, start)


def plain(text, view=None):
    """Text with every code name of a view or column put into plain words. Where view is given, a bare column name of
    that view is put into plain words as well."""
    text = text or ""

    def named(match):
        at = match.start()
        return plain_about(match.group(0).strip("`"), at == 0 or text[:at].rstrip().endswith((".", ":")))
    text = re.sub(r"`?\brole_[a-z_]+(?:\.[a-z_]+)?\b`?", named, text)
    # The contract names each list of kinds a vocabulary; the page calls them the kinds the page knows.
    text = re.sub(r"\bone of the kinds of the (?:[a-z ]+ )?vocabulary\b", "one of the kinds the page knows", text)
    text = re.sub(r"\bwith its kind from the (?:[a-z ]+ )?vocabulary(?: below)?", "with its kind, one of the kinds the page knows", text)
    text = re.sub(r"\bthe complications of the (?:[a-z ]+ )?vocabulary\b", "the complications that the page knows", text)
    if view is not None:
        columns = {name for (owner, name) in _titles()[1] if owner == view and "_" in name}
        text = re.sub(r"\b[a-z]+_[a-z_]+\b", lambda m: f"the {column_title(view, m.group(0)).removeprefix('the ')}"
                      if m.group(0) in columns else m.group(0), text)
    return text


# Reading and checking a map.

def _sentence(value, where, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 400 or "\n" in value or "\r" in value \
            or "$(" in value or "?" in value or "!" in value or not value.rstrip().endswith("."):
        raise MapError(WORDING["sentence"].format(where=where, field=field))
    return value


def _evidence(item, where):
    if not isinstance(item, dict) or not {"status", "from", "says"} <= set(item) <= {"status", "from", "says", "question", "codes", *PROPOSAL_FIELDS}:
        raise MapError(WORDING["map_shape"].format(where=where, problem="evidence holds a status, from, says and, unless a person "
                                                                         "or a count confirmed it, a question"))
    if item["status"] not in STATUSES:
        raise MapError(WORDING["map_shape"].format(where=where, problem=f"the status is one of {', '.join(STATUSES)}"))
    if not isinstance(item["from"], str) or not item["from"].strip() or "\n" in item["from"]:
        raise MapError(WORDING["map_shape"].format(where=where, problem="from says where the binding comes from, on one line"))
    _sentence(item["says"], where, "says")
    if item["status"] not in CONFIRMED or "question" in item:
        if "question" not in item:
            raise MapError(WORDING["map_shape"].format(where=where, problem="a binding that no person or count has confirmed carries its question"))
        _sentence(item["question"], where, "the question")
    return item


def read_map_json(folder):
    """map.json of a map folder, checked against the contract. Raises MapError on anything else."""
    path = Path(folder) / MAP_FILE
    try:
        data = json.loads(decode(path.read_bytes()))
    except (OSError, ValueError):
        raise MapError(WORDING["map_json"].format(where=MAP_FILE)) from None
    if not isinstance(data, dict) or not {"world", "description", "roles", "kinds"} <= set(data) \
            <= {"world", "description", "roles", "kinds", "eras", "questions", "normalisations", "concepts"}:
        raise MapError(WORDING["map_shape"].format(where=MAP_FILE, problem="the map holds world, description, roles, kinds and, "
                                                                           "optionally, eras, questions, normalisations and concepts"))
    # A binding that names a normalisation is read back into its route, so that what follows checks the route itself.
    from ..normalise import resolve
    data = resolve(data)
    _sentence(data["description"], MAP_FILE, "the description")
    required, wanted = views(), all_views()
    if not isinstance(data["roles"], dict) or not set(required) <= set(data["roles"]) <= set(wanted):
        raise MapError(WORDING["map_shape"].format(where=MAP_FILE, problem=f"roles holds {', '.join(required)} and, optionally, "
                                                                           f"the further role views of the contract"))
    for view, columns in wanted.items():
        if view not in data["roles"]:
            continue
        role = data["roles"][view]
        where = f"{MAP_FILE}, {view}"
        if not isinstance(role, dict) or not {"file", "rows", "columns"} <= set(role) <= {"file", "rows", "columns", "source_kind", "pathways"} \
                or role["file"] != f"{view}.sql":
            raise MapError(WORDING["map_shape"].format(where=where, problem=f"a role holds file ({view}.sql), rows and columns, and "
                                                                           f"a part that records events may add its source kind and further pathways"))
        _pathway(view, role, columns, where)
        if "pathways" in role and (view not in event_parts() or not isinstance(role["pathways"], list)):
            raise MapError(WORDING["map_shape"].format(where=where, problem="only a part that records events takes further pathways"))
        for number, pathway in enumerate(role.get("pathways") or []):
            if view not in event_parts() or not isinstance(pathway, dict) or set(pathway) != {"name", "source_kind", "rows", "columns"} \
                    or not isinstance(pathway["name"], str) or not PATHWAY_NAME.fullmatch(pathway["name"]) \
                    or pathway["name"] in [p["name"] for p in role["pathways"][:number]]:
                raise MapError(WORDING["map_shape"].format(where=where, problem="a further pathway of a part that records events "
                                                                               "holds a plain name of its own, its source kind, rows and columns"))
            _pathway(view, pathway, columns, f"{where}@{pathway['name']}")
            if not pathway["rows"].get("binding"):
                raise MapError(WORDING["map_shape"].format(where=f"{where}@{pathway['name']}", problem="a further pathway names its table"))
    check_concepts(data.get("concepts"))
    if not isinstance(data["kinds"], dict) or not set(data["kinds"]) <= set(kinds()) - {"other"} or set(MEAN_KINDS) - set(data["kinds"]):
        raise MapError(WORDING["map_shape"].format(where=MAP_FILE, problem="kinds gives the local codes of map_arterial and map_cuff"))
    for kind, item in data["kinds"].items():
        _evidence(item, f"{MAP_FILE}, kind {kind}")
        if not isinstance(item.get("codes"), list) or not all(isinstance(code, str) and code for code in item["codes"]):
            raise MapError(WORDING["map_shape"].format(where=f"{MAP_FILE}, kind {kind}", problem="codes is a list of the local codes, as text"))
    for entry in data.get("eras", []):
        if not isinstance(entry, dict) or set(entry) != {"about", "note"}:
            raise MapError(WORDING["map_shape"].format(where=f"{MAP_FILE}, eras", problem="an era holds about and note"))
        _sentence(entry["note"], f"{MAP_FILE}, eras", "a note")
    for entry in data.get("questions", []):
        if not isinstance(entry, dict) or set(entry) != {"about", "question"}:
            raise MapError(WORDING["map_shape"].format(where=f"{MAP_FILE}, questions", problem="a question holds about and question"))
        _sentence(entry["question"], f"{MAP_FILE}, questions", "a question")
    return data


# The name of a further pathway to a part, which the map's evidence names as role_x@name.
PATHWAY_NAME = re.compile(r"[a-z][a-z0-9_]{0,30}")


def _pathway(view, role, columns, where):
    """Checks one pathway to a part: the evidence of its rows and of every column, and its source kind, which only a
    part that records events carries and which is a kind of the source kind vocabulary."""
    from ..corrections import check_shape
    _evidence(role["rows"], f"{where}, rows")
    check_shape(role["rows"].get("binding"), f"{where}, rows")
    if not isinstance(role["columns"], dict) or set(role["columns"]) != set(columns):
        raise MapError(WORDING["map_shape"].format(where=where, problem=f"columns gives evidence for exactly {', '.join(columns)}"))
    for column in columns:
        _evidence(role["columns"][column], f"{where}.{column}")
        check_shape(role["columns"][column].get("binding"), f"{where}.{column}")
    _no_clinical_filter(view, role, where)
    if "source_kind" in role and (view not in event_parts() or role["source_kind"] not in source_kinds()):
        raise MapError(WORDING["map_shape"].format(where=where, problem="a source kind belongs to a part that records events, "
                                                                       "and is one of the kinds of the source kind vocabulary"))


CLINICAL_FILTER = ("a filter by clinical meaning, on the column that holds the part's local code of a drug, procedure, "
                   "diagnosis, test or unit, is a decision of a question over the roles, which the hospital schema never holds")


def _no_clinical_filter(view, role, where):
    """Invariant 10: a filter of a part's rows may interpret the vendor's storage, but it never selects rows by the
    column that the part binds to a local key of a mapping view, since keeping only the drugs of one class, say, is a
    clinical decision that belongs to a question over the roles. Raises MapError naming the rule."""
    spec = next((v for v in contract()["views"] if v["name"] == view), None)
    keyed = set()
    for column in (spec or {}).get("columns", []):
        binding = (role["columns"].get(column["name"]) or {}).get("binding") if column.get("type") == "local_key" else None
        if binding and binding.get("table") and binding.get("column"):
            keyed.add((binding["table"].upper(), binding["column"].upper()))
    for item in (role["rows"].get("binding") or {}).get("filter") or []:
        if (str(item["table"]).upper(), str(item["column"]).upper()) in keyed:
            raise MapError(WORDING["map_shape"].format(where=f"{where}, rows", problem=CLINICAL_FILTER))


def pathways(view, role):
    """The pathways to a part, as [(name, pathway)]: the first, whose name is None, and each further one."""
    return [(None, role)] + [(p["name"], p) for p in role.get("pathways") or []]


def role_items(view, role):
    """Every binding of a part and its further pathways, as [(about, evidence)]: role_x rows, role_x.column, and for
    a further pathway role_x@name rows and role_x@name.column."""
    found = []
    for name, pathway in pathways(view, role):
        part = view if name is None else f"{view}@{name}"
        found.append((f"{part} rows", pathway["rows"]))
        found += [(f"{part}.{c}", e) for c, e in pathway["columns"].items()]
    return found


def check_concepts(concepts):
    """Checks the concept translations of a map: {"salt", "views": {mapping view: {"rows": [{"code", "description",
    "concept_id", "status", "provenance"}], ...}}}. A code that is mapped or unmapped has one row, an ambiguous code a
    row for each concept it may mean, and an unmapped row has the concept 0. Raises MapError."""
    if concepts is None:
        return
    model = contract()
    statuses = {k["kind"] for k in model["vocabularies"]["mapping_status"]}
    provenances = {k["kind"] for k in model["vocabularies"]["mapping_provenance"]}

    def bad(problem):
        raise MapError(WORDING["map_shape"].format(where=f"{MAP_FILE}, concepts", problem=problem))
    if not isinstance(concepts, dict) or not isinstance(concepts.get("salt"), str) or not isinstance(concepts.get("views"), dict):
        bad("the concepts hold a salt and the translation of each mapping view")
    for name, held in concepts["views"].items():
        if name not in mapping_views(model) or not isinstance(held, dict) or not isinstance(held.get("rows"), list):
            bad("each translation names a mapping view of the contract and holds its rows")
        by_code = {}
        for row in held["rows"]:
            if not isinstance(row, dict) or not {"code", "concept_id", "status", "provenance"} <= set(row) \
                    or not str(row["code"]).strip() or row["status"] not in statuses or row["provenance"] not in provenances \
                    or not isinstance(row["concept_id"], int) or (row["status"] == "unmapped") != (row["concept_id"] == 0):
                bad("each row gives a code, a concept, a status and a provenance, and only an unmapped row has the concept 0")
            by_code.setdefault(str(row["code"]), []).append(row)
        for code, rows in by_code.items():
            if len({r["status"] for r in rows}) != 1 or (rows[0]["status"] != "ambiguous" and len(rows) != 1):
                bad("a code that is mapped or unmapped has one row, and only an ambiguous code has several")


def concept_codes(data, mapping):
    """{code: local key} for every code that a map's translation of one mapping view lists."""
    concepts = (data or {}).get("concepts") or {}
    rows = ((concepts.get("views") or {}).get(mapping) or {}).get("rows") or []
    return {str(r["code"]): concept_key(concepts.get("salt"), mapping, r["code"]) for r in rows}


def mapping_rows(data, mapping):
    """The public rows of one mapping view, from a map's private translation: the code replaced by its key."""
    concepts = (data or {}).get("concepts") or {}
    rows = ((concepts.get("views") or {}).get(mapping) or {}).get("rows") or []
    return [{"local_key": concept_key(concepts.get("salt"), mapping, r["code"]), "concept_id": r["concept_id"],
             "status": r["status"], "provenance": r["provenance"]} for r in rows]


def _single_select(sql, where):
    from ..selects import Refused, _single_select as release_single_select
    try:
        return release_single_select(sql, where)
    except Refused as error:
        raise MapError(str(error)) from None


def _outputs(tree):
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    return [projection.alias_or_name for projection in select.expressions]


def check_view(sql, view, catalogue=None):
    """Checks one role view. Returns the source tables that it reads, in the catalogue's spelling where one is given.

    The view is one SELECT by the rule that the release script applies to a step, with no WITH of its own; it gives
    exactly the contract's columns in the contract's order; it reads no role view; and, where a catalogue is given,
    every table and column that it names is in the catalogue. Every column is named with its table's alias, unless
    the SELECT that names it reads one table only. Raises MapError on anything else.
    """
    where = f"{view}.sql"
    wanted = all_views().get(view)
    if wanted is None:
        raise MapError(WORDING["map_shape"].format(where=where, problem=f"{view} is not a role view of the contract"))
    tree = _single_select(sql, where)
    if tree.args.get("with_") or tree.args.get("with"):
        raise MapError(WORDING["with"].format(where=where))
    found = _outputs(tree)
    if [name.lower() for name in found] != wanted:
        raise MapError(WORDING["columns"].format(where=where, found=", ".join(found), wanted=", ".join(wanted)))
    tables = []
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog:
            raise MapError(WORDING["table_part"].format(where=where, table=table.sql(dialect="tsql")))
        if table.name.lower() in all_views():
            raise MapError(WORDING["role_name"].format(where=where, table=table.name))
        known = catalogue.table(table.name) if catalogue is not None else None
        if catalogue is not None and known is None:
            raise MapError(WORDING["table_unknown"].format(where=where, table=table.name))
        name = known.name if known is not None else table.name
        if name not in tables:
            tables.append(name)
    for scope in traverse_scope(tree):
        for column in scope.columns:
            source, alias = None, column.table
            if not alias:
                own = [s for s in scope.sources.values()]
                if column.name in (scope.expression.named_selects if isinstance(scope.expression, exp.Select) else []) \
                        and not isinstance(column.parent, (exp.Select,)):
                    continue    # a column of the SELECT's own output, such as in ORDER BY
                if len(own) != 1:
                    raise MapError(WORDING["column_alias"].format(where=where, column=column.name))
                source = own[0]
            else:
                outer = scope
                while outer is not None and source is None:
                    source = outer.sources.get(alias)
                    outer = outer.parent
                if source is None:
                    raise MapError(WORDING["column_alias"].format(where=where, column=f"{alias}.{column.name}"))
            if isinstance(source, exp.Table):
                if catalogue is not None and catalogue.table(source.name).column(column.name) is None:
                    raise MapError(WORDING["column_unknown"].format(where=where, column=column.name, table=source.name))
            else:
                outputs = [name.upper() for name in source.expression.named_selects] if isinstance(source.expression, exp.Select) \
                    else [name.upper() for name in _outputs(source.expression)]
                if column.name.upper() not in outputs:
                    raise MapError(WORDING["column_derived"].format(where=where, alias=alias, column=column.name))
    return tables


def read_map(folder, catalogue=None):
    """A map folder, read and checked: {"folder", "data", "views": {view: sql}, "tables": {view: [source tables]}}.

    catalogue, when given, is a Catalogue or the text of a catalogue file, and every table and column that a view
    names must be in it. Raises MapError when the map breaks a rule.
    """
    folder = Path(folder)
    if isinstance(catalogue, str):
        catalogue = Catalogue.from_csv(catalogue)
    data = read_map_json(folder)
    found, tables = {}, {}
    for view in [name for name in all_views() if name in data["roles"]]:
        try:
            sql = decode((folder / data["roles"][view]["file"]).read_bytes())
        except OSError:
            raise MapError(WORDING["map_shape"].format(where=view, problem=f"the map folder holds {view}.sql")) from None
        tables[view] = check_view(sql, view, catalogue)
        found[view] = sql
    # The mapping views are written from the map's own translations, with each code replaced by its key.
    for name in mapping_views():
        found[name] = mapping_sql(name, mapping_rows(data, name))
    return {"folder": folder, "data": data, "views": found, "tables": tables}


def open_items(roles_map):
    """The open items of a map, from its evidence: every binding that no person and no count has confirmed, with its
    question, then the map's own questions. Each item is {"about", "status", "says", "question"}."""
    data = roles_map["data"] if "data" in roles_map else roles_map
    items = []
    for view, role in data["roles"].items():
        for about, item in role_items(view, role):
            if item["status"] not in CONFIRMED:
                items.append({"about": about, "status": item["status"], "says": item["says"], "question": item["question"]})
    for kind, item in data["kinds"].items():
        if item["status"] not in CONFIRMED:
            items.append({"about": f"kind {kind}", "status": item["status"], "says": item["says"], "question": item["question"]})
    for entry in data.get("questions", []):
        items.append({"about": entry["about"], "status": "open", "says": "", "question": entry["question"]})
    return items


# The SQL of a role view as a compiled query carries it. The compilation itself is compiler.py's.

def view_sql(sql, nolock=True):
    """A role view as T-SQL, with each source table read WITH (NOLOCK)."""
    tree = sqlglot.parse_one(sql, dialect="tsql")
    if nolock:
        for table in tree.find_all(exp.Table):
            table.set("hints", [exp.WithTableHint(expressions=[exp.Var(this="NOLOCK")])])
    return tree.sql(dialect="tsql", pretty=True).replace("   WITH (NOLOCK)", " WITH (NOLOCK)")


# The standard counts. Each is one SELECT over the role views, rounded down to ten, leaving out any group that fewer
# than ten rows hold, so that its result can leave the hospital as the check results do.

def _rounded(name, step):
    return f"g.{name} - g.{name} % {step} AS {name}"


def count_queries(least=MINIMUM_COUNT, step=MINIMUM_COUNT):
    """The standard counts, as {name: {"says", "sql"}}. least is the fewest rows a group may hold, and step the number
    to which each count is rounded down; both are the project's ten unless a test on synthetic rows asks otherwise."""
    means = ", ".join(f"'{kind}'" for kind in MEAN_KINDS)
    coverage = ("with_patient", "with_birth_date", "with_stop", "with_reading", "with_mean_pressure")
    queries = {
        "coverage_by_year": {
            "says": "This query counts the anaesthetics of each year of their start, and how many of them have a patient, a date of birth, a stop, any reading and an accepted mean pressure.",
            "sql": f"""SELECT g.start_year,
       {_rounded('anaesthetics', step)},
       {(',' + chr(10) + '       ').join(_rounded(name, step) for name in coverage)}
FROM   (SELECT YEAR(a.start_time) AS start_year,
               COUNT(*) AS anaesthetics,
               SUM(CASE WHEN p.patient_key IS NOT NULL THEN 1 ELSE 0 END) AS with_patient,
               SUM(CASE WHEN p.birth_date IS NOT NULL THEN 1 ELSE 0 END) AS with_birth_date,
               SUM(CASE WHEN a.stop_time IS NOT NULL THEN 1 ELSE 0 END) AS with_stop,
               SUM(CASE WHEN r.readings > 0 THEN 1 ELSE 0 END) AS with_reading,
               SUM(CASE WHEN r.means > 0 THEN 1 ELSE 0 END) AS with_mean_pressure
        FROM   role_anaesthetic a
               LEFT JOIN (SELECT pp.patient_key, MAX(pp.birth_date) AS birth_date
                          FROM   role_patient pp
                          GROUP  BY pp.patient_key) p ON p.patient_key = a.patient_key
               LEFT JOIN (SELECT rr.anaesthetic_key,
                                 COUNT(*) AS readings,
                                 SUM(CASE WHEN rr.kind IN ({means}) AND rr.accepted = 1 AND rr.value IS NOT NULL THEN 1 ELSE 0 END) AS means
                          FROM   role_reading rr
                          GROUP  BY rr.anaesthetic_key) r ON r.anaesthetic_key = a.anaesthetic_key
        GROUP  BY YEAR(a.start_time)) g
WHERE  g.anaesthetics >= {least}
ORDER  BY g.start_year"""},
        "repeated_keys": {
            "says": "This query counts, for each role view, the keys that more than one row holds, and the rows that they hold between them.",
            "sql": f"""SELECT g.role_view,
       {_rounded('keys_repeated', step)},
       {_rounded('rows_held', step)},
       CASE WHEN g.keys_repeated > 0 THEN 1 ELSE 0 END AS any_repeated
FROM   (SELECT 'role_patient' AS role_view, COUNT(*) AS keys_repeated, COALESCE(SUM(k.n), 0) AS rows_held
        FROM   (SELECT pp.patient_key, COUNT(*) AS n FROM role_patient pp GROUP BY pp.patient_key HAVING COUNT(*) > 1) k
        UNION ALL
        SELECT 'role_anaesthetic', COUNT(*), COALESCE(SUM(k.n), 0)
        FROM   (SELECT aa.anaesthetic_key, COUNT(*) AS n FROM role_anaesthetic aa GROUP BY aa.anaesthetic_key HAVING COUNT(*) > 1) k
        UNION ALL
        SELECT 'role_reading', COUNT(*), COALESCE(SUM(k.n), 0)
        FROM   (SELECT rr.reading_key, COUNT(*) AS n FROM role_reading rr GROUP BY rr.reading_key HAVING COUNT(*) > 1) k) g
ORDER  BY g.role_view"""},
        "readings_by_kind_and_year": {
            "says": "This query counts the readings of each kind in each year, and how many of them were accepted, hold a number and belong to an anaesthetic that role_anaesthetic holds.",
            "sql": f"""SELECT g.kind,
       g.reading_year,
       {_rounded('readings', step)},
       {_rounded('accepted', step)},
       {_rounded('with_value', step)},
       {_rounded('with_anaesthetic', step)}
FROM   (SELECT r.kind,
               YEAR(r.reading_time) AS reading_year,
               COUNT(*) AS readings,
               SUM(CASE WHEN r.accepted = 1 THEN 1 ELSE 0 END) AS accepted,
               SUM(CASE WHEN r.value IS NOT NULL THEN 1 ELSE 0 END) AS with_value,
               SUM(CASE WHEN a.anaesthetic_key IS NOT NULL THEN 1 ELSE 0 END) AS with_anaesthetic
        FROM   role_reading r
               LEFT JOIN (SELECT DISTINCT aa.anaesthetic_key FROM role_anaesthetic aa) a ON a.anaesthetic_key = r.anaesthetic_key
        GROUP  BY r.kind, YEAR(r.reading_time)) g
WHERE  g.readings >= {least}
ORDER  BY g.kind, g.reading_year"""},
        "empty_values_by_reason": {
            "says": "This query counts, for each kind of reading, the readings with an empty value by reason: the record holds no value, the value it holds is not a number, or the reading was not accepted.",
            "sql": f"""SELECT g.kind,
       {_rounded('readings', step)},
       {_rounded('not_held', step)},
       {_rounded('not_a_number', step)},
       {_rounded('not_accepted', step)}
FROM   (SELECT r.kind,
               COUNT(*) AS readings,
               SUM(CASE WHEN r.value IS NULL AND r.value_text IS NULL THEN 1 ELSE 0 END) AS not_held,
               SUM(CASE WHEN r.value IS NULL AND r.value_text IS NOT NULL THEN 1 ELSE 0 END) AS not_a_number,
               SUM(CASE WHEN r.accepted = 0 THEN 1 ELSE 0 END) AS not_accepted
        FROM   role_reading r
        GROUP  BY r.kind) g
WHERE  g.readings >= {least}
ORDER  BY g.kind"""},
        "gaps_between_readings": {
            "says": "This query counts the gaps between consecutive mean pressures of the same kind within one anaesthetic, in bands, by kind and year.",
            "sql": f"""SELECT g.kind,
       g.reading_year,
       g.band_order,
       g.gap,
       {_rounded('readings', step)}
FROM   (SELECT s.kind,
               YEAR(s.reading_time) AS reading_year,
               CASE WHEN DATEDIFF(second, s.previous_time, s.reading_time) = 0 THEN 0
                    WHEN DATEDIFF(second, s.previous_time, s.reading_time) < 120 THEN 1
                    WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 300 THEN 2
                    WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 900 THEN 3
                    ELSE 4 END AS band_order,
               CASE WHEN DATEDIFF(second, s.previous_time, s.reading_time) = 0 THEN 'the same time'
                    WHEN DATEDIFF(second, s.previous_time, s.reading_time) < 120 THEN 'under 2 minutes'
                    WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 300 THEN '2 to 5 minutes'
                    WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 900 THEN 'over 5 and up to 15 minutes'
                    ELSE 'over 15 minutes' END AS gap,
               COUNT(*) AS readings
        FROM   (SELECT r.kind, r.reading_time,
                       LAG(r.reading_time) OVER (PARTITION BY r.anaesthetic_key, r.kind ORDER BY r.reading_time) AS previous_time
                FROM   role_reading r
                WHERE  r.kind IN ({means}) AND r.reading_time IS NOT NULL) s
        WHERE  s.previous_time IS NOT NULL
        GROUP  BY s.kind, YEAR(s.reading_time),
                  CASE WHEN DATEDIFF(second, s.previous_time, s.reading_time) = 0 THEN 0
                       WHEN DATEDIFF(second, s.previous_time, s.reading_time) < 120 THEN 1
                       WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 300 THEN 2
                       WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 900 THEN 3
                       ELSE 4 END,
                  CASE WHEN DATEDIFF(second, s.previous_time, s.reading_time) = 0 THEN 'the same time'
                       WHEN DATEDIFF(second, s.previous_time, s.reading_time) < 120 THEN 'under 2 minutes'
                       WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 300 THEN '2 to 5 minutes'
                       WHEN DATEDIFF(second, s.previous_time, s.reading_time) <= 900 THEN 'over 5 and up to 15 minutes'
                       ELSE 'over 15 minutes' END) g
WHERE  g.readings >= {least}
ORDER  BY g.kind, g.reading_year, g.band_order"""},
        "readings_outside_anaesthetic": {
            "says": "This query counts, for each year of the anaesthetic's start, the mean pressures linked to an anaesthetic, and how many of them were taken before its start or after its stop.",
            "sql": f"""SELECT g.start_year,
       {_rounded('readings', step)},
       {_rounded('before_start', step)},
       {_rounded('after_stop', step)}
FROM   (SELECT YEAR(a.start_time) AS start_year,
               COUNT(*) AS readings,
               SUM(CASE WHEN r.reading_time < a.start_time THEN 1 ELSE 0 END) AS before_start,
               SUM(CASE WHEN r.reading_time > a.stop_time THEN 1 ELSE 0 END) AS after_stop
        FROM   role_reading r
               JOIN role_anaesthetic a ON a.anaesthetic_key = r.anaesthetic_key
        WHERE  r.kind IN ({means}) AND a.start_time IS NOT NULL
        GROUP  BY YEAR(a.start_time)) g
WHERE  g.readings >= {least}
ORDER  BY g.start_year"""},
    }
    return queries


def _number(value):
    if value is None or value == "" or str(value).upper() == "NULL":
        return None
    number = float(value)
    return int(number) if number == int(number) else number


def _records(columns, rows):
    return [dict(zip(columns, [_number(v) if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v))
                               else v for v in row])) for row in rows]


def _years_text(years):
    years = [str(year) for year in sorted(years)]
    return years[0] if len(years) == 1 else ", ".join(years[:-1]) + " and " + years[-1]


def read_counts(results, step=MINIMUM_COUNT):
    """Plain findings from the standard counts. results is {name: (columns, rows)}, as run_counts gives it.

    Returns {"findings": [sentence], "flagged_years": {year: [reason]}, "coverage": [record]}. A year is flagged when
    a coverage figure falls in it to half or less of the best year's share, and leaves at least CLIFF_LEAST more
    anaesthetics without it than the best year's share would, or when far more of its mean pressures fall outside
    their anaesthetic than in the best year. Either usually means a change in how the data is held.
    """
    findings, flagged = [], {}
    coverage = [r for r in _records(*results["coverage_by_year"]) if r["start_year"] is not None and r["anaesthetics"]]
    # Every count is rounded down, so a share is compared at its bounds: the best year at the least that its counts
    # allow, and every other year at the most, so that rounding alone never makes a cliff.
    spare = max(step - 1, 0)
    for figure, words in FIGURES.items():
        shares = [(r[figure] / (r["anaesthetics"] + spare), r) for r in coverage]
        if not shares:
            continue
        best_share, best = max(shares, key=lambda item: (item[0], item[1]["anaesthetics"]))
        for _, record in shares:
            share = min(1.0, (record[figure] + spare) / record["anaesthetics"])
            missing = (best_share - share) * record["anaesthetics"]
            if record is not best and best_share > 0 and share <= best_share * CLIFF_SHARE and missing >= CLIFF_LEAST:
                flagged.setdefault(record["start_year"], []).append(figure)
                findings.append(WORDING["cliff"].format(figure=words, best_count=best[figure], best_total=best["anaesthetics"],
                                                        best_year=best["start_year"], count=record[figure],
                                                        total=record["anaesthetics"], year=record["start_year"]))
    outside = [r for r in _records(*results["readings_outside_anaesthetic"]) if r["start_year"] is not None and r["readings"]]
    if outside:
        shares = [((r["before_start"] + r["after_stop"]) / r["readings"], r) for r in outside]
        least_share, best = min(shares, key=lambda item: (item[0], -item[1]["readings"]))
        for share, record in shares:
            if share - least_share >= OUTSIDE_SHARE:
                flagged.setdefault(record["start_year"], []).append("outside")
                findings.append(WORDING["outside"].format(year=record["start_year"], count=record["before_start"] + record["after_stop"],
                                                          total=record["readings"], best_count=best["before_start"] + best["after_stop"],
                                                          best_total=best["readings"], best_year=best["start_year"]))
    years = {r["start_year"] for r in coverage if r["anaesthetics"] >= CLIFF_LEAST}
    readings = _records(*results["readings_by_kind_and_year"])
    for kind in MEAN_KINDS:
        present = {r["reading_year"] for r in readings if r["kind"] == kind and r["reading_year"] is not None}
        absent = years - present
        if present and absent:
            findings.append(WORDING["kind_years"].format(kind=kind, present=_years_text(present), absent=_years_text(absent)))
    for record in _records(*results["repeated_keys"]):
        if record["any_repeated"]:
            findings.append(WORDING["repeated"].format(count=record["keys_repeated"], view=record["role_view"], rows=record["rows_held"])
                            if record["keys_repeated"] else WORDING["repeated_few"].format(view=record["role_view"]))
    gaps = _records(*results["gaps_between_readings"])
    for kind in MEAN_KINDS:
        commonest = {}
        for year in sorted({g["reading_year"] for g in gaps if g["kind"] == kind}):
            rows = [g for g in gaps if g["kind"] == kind and g["reading_year"] == year]
            commonest[year] = max(rows, key=lambda g: (g["readings"], -g["band_order"]))["gap"]
        bands = sorted(set(commonest.values()), key=lambda band: -sum(1 for b in commonest.values() if b == band))
        if len(bands) > 1:
            first, second = bands[0], bands[1]
            findings.append(WORDING["gaps"].format(kind=kind, band=first, years=_years_text([y for y, b in commonest.items() if b == first]),
                                                   other_band=second, other_years=_years_text([y for y, b in commonest.items() if b == second])))
    return {"findings": findings, "flagged_years": flagged, "coverage": coverage}


# How the proposals fared. The scoreboard reads only the answers and the proposals' own records in map.json, and its
# sentences give counts and the plain names of the parts, never a table, a column or a code, so that it may be shared.

SCORE_LEVELS = ("high", "medium", "low")
SCORE_WORDING = {
    "heading": "How the proposals fared",
    "overall": "Across the hospital schema, the page made {proposals}. Of these, {as_proposed} confirmed as proposed, "
               "{listed} corrected to an alternative that the page had listed, {unlisted} corrected to a column or table "
               "that the page had not listed, {not_sure} marked not sure, and {unanswered} no answer yet.",
    "part": "In {part}, the page made {proposals}. Of these, {as_proposed} confirmed as proposed, {listed} corrected to "
            "an alternative that the page had listed, {unlisted} corrected to a column or table that the page had not "
            "listed, {not_sure} marked not sure, and {unanswered} no answer yet.",
    "none": "The page made no proposal from the dictionary, so there is nothing to report yet.",
    "level": "Of the {answered} made with {level} confidence that {have} been confirmed or corrected, {corrected} corrected, "
             "which is {share} per cent.",
    "level_none": "No proposal made with {level} confidence has yet been confirmed or corrected.",
    "nothing": "The page proposed nothing for {count}, and a person has since chosen one for {chosen} of them.",
    "category": "Among {category}, the page made {proposals}. Of these, {as_proposed} confirmed as proposed, {listed} "
                "corrected to an alternative that the page had listed, {unlisted} corrected to a column or table that the "
                "page had not listed, {not_sure} marked not sure, and {unanswered} no answer yet.",
    "category_none": "Among {category}, the page made no proposal.",
    "reference": "Among the proposals that rested on a reference conversion rather than on the dictionary, the page made "
                 "{proposals}. Of these, {as_proposed} confirmed as proposed, {listed} corrected to an alternative that the "
                 "page had listed, {unlisted} corrected to a column or table that the page had not listed, {not_sure} marked "
                 "not sure, and {unanswered} no answer yet.",
    "shared": "These figures name no table or column, so they may be shared.",
}


# The five categories of column by which the scoreboard also counts, with the words that name each. A wrong link between
# a reading and its anaesthetic matters far more than a missing descriptive column, so each is counted apart.
SCORE_CATEGORIES = {
    "keys": "the keys, which identify each row of a part or link it to another part",
    "links": "the links, which join one part to another through further tables or by a time window",
    "timestamps": "the dates and times",
    "codes": "the codes and flags, which need translating into the kinds and values that the audits read",
    "descriptive": "the descriptive columns",
}
CATEGORY_TYPES = {"key": "keys", "date": "timestamps", "datetime": "timestamps", "kind": "codes", "flag": "codes",
                  "flag_or_empty": "codes"}


def category(view, column, item=None):
    """The category of a binding for the scoreboard: links where the binding reaches its column through a path of
    further tables or a time window, and otherwise by the column's type in contract.json, so that keys, dates and
    times, and kinds and flags each have their own category, and every other column is descriptive. column is None
    for the binding of a part's rows, which identifies the part and counts as a key unless it is reached by a path."""
    binding = (item or {}).get("binding") or {}
    if binding.get("path") or binding.get("window"):
        return "links"
    if column is None:
        return "keys"
    return CATEGORY_TYPES.get(_column_types().get((view, column)), "descriptive")


_TYPES = None


def _column_types():
    global _TYPES
    if _TYPES is None:
        _TYPES = {(v["name"], c["name"]): c["type"] for v in contract()["views"] for c in v["columns"]}
    return _TYPES


def _were(n):
    return f"{n:,} {'was' if n == 1 else 'were'}"


def _had(n):
    return f"{n:,} {'has' if n == 1 else 'have'}"


def _head(text):
    """The table, or the table and column, at the start of a replacement or a candidate's from text, in capitals."""
    found = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?", text or "")
    if not found:
        return ""
    return ".".join(part.upper() for part in found.groups() if part)


def _fared(item):
    """How one proposal fared: as_proposed, listed, unlisted, not_sure or unanswered."""
    confirmation = item.get("confirmation") or {}
    answer = confirmation.get("answer")
    if answer == "yes":
        return "as_proposed"
    if answer == "not sure":
        return "not_sure"
    if answer == "no":
        wanted = _head(confirmation.get("replacement") or "")
        listed = {_head(candidate.get("from") or "") for candidate in item.get("candidates") or []}
        return "listed" if wanted and wanted in listed else "unlisted"
    return "unanswered"


def scoreboard(data):
    """How the proposals of a hospital schema fared, from its map.json as data: for each part and overall, how many were
    confirmed as proposed, corrected to an alternative that the page had listed, corrected to one it had not, marked
    not sure, and left without an answer; and, at each level of confidence, the share of those answered that were
    corrected. The same counts are given for each of five categories of column (keys, links, timestamps, codes and
    descriptive columns; see category). A binding that no proposal made, such as one written by hand, is not counted.
    Returns {"parts", "overall", "categories", "levels", "nothing", "lines", "text"}; the lines give counts and the
    plain names of the parts only."""
    empty = {"proposals": 0, "as_proposed": 0, "listed": 0, "unlisted": 0, "not_sure": 0, "unanswered": 0}
    overall, parts = dict(empty), []
    categories = {name: dict(empty) for name in SCORE_CATEGORIES}
    reference = dict(empty)
    levels = {level: {"answered": 0, "corrected": 0} for level in SCORE_LEVELS}
    nothing = {"count": 0, "chosen": 0}
    for view in [name for name in all_views() if name in (data or {}).get("roles", {})]:
        role = data["roles"][view]
        part = dict(empty)
        for column, item in [(None, role["rows"]), *role["columns"].items()]:
            if "confidence" not in item:
                continue
            fared = _fared(item)
            if item["confidence"] not in SCORE_LEVELS:
                nothing["count"] += 1
                nothing["chosen"] += fared in ("listed", "unlisted")
                continue
            part["proposals"] += 1
            part[fared] += 1
            held = categories[category(view, column, item)]
            held["proposals"] += 1
            held[fared] += 1
            if item.get("proposed_from") == "a reference conversion":
                reference["proposals"] += 1
                reference[fared] += 1
            if fared in ("as_proposed", "listed", "unlisted"):
                levels[item["confidence"]]["answered"] += 1
                levels[item["confidence"]]["corrected"] += fared != "as_proposed"
        if part["proposals"]:
            parts.append({"view": view, "title": view_title(view), **part})
            for key in empty:
                overall[key] += part[key]

    def sentence(template, counts, **more):
        return template.format(proposals=f"{counts['proposals']:,} {'proposal' if counts['proposals'] == 1 else 'proposals'}",
                               as_proposed=_were(counts["as_proposed"]), listed=_were(counts["listed"]),
                               unlisted=_were(counts["unlisted"]), not_sure=_were(counts["not_sure"]),
                               unanswered=_had(counts["unanswered"]), **more)
    lines = [SCORE_WORDING["heading"], ""]
    if not overall["proposals"]:
        lines.append(SCORE_WORDING["none"])
    else:
        lines.append(sentence(SCORE_WORDING["overall"], overall))
        lines.append("")
        lines += [sentence(SCORE_WORDING["part"], part, part=view_title(part["view"], False)) for part in parts]
        lines.append("")
        lines += [sentence(SCORE_WORDING["category"], counts, category=SCORE_CATEGORIES[name]) if counts["proposals"]
                  else SCORE_WORDING["category_none"].format(category=SCORE_CATEGORIES[name])
                  for name, counts in categories.items()]
        if reference["proposals"]:
            lines.append(sentence(SCORE_WORDING["reference"], reference))
        lines.append("")
        for level in SCORE_LEVELS:
            held = levels[level]
            if held["answered"]:
                share = round(100 * held["corrected"] / held["answered"])
                lines.append(SCORE_WORDING["level"].format(answered=f"{held['answered']:,} {'proposal' if held['answered'] == 1 else 'proposals'}",
                                                           level=level, corrected=_were(held["corrected"]), share=share,
                                                           have="has" if held["answered"] == 1 else "have"))
            else:
                lines.append(SCORE_WORDING["level_none"].format(level=level))
        if nothing["count"]:
            lines.append(SCORE_WORDING["nothing"].format(count=f"{nothing['count']:,} {'column' if nothing['count'] == 1 else 'columns'}",
                                                         chosen=f"{nothing['chosen']:,}"))
    lines += ["", SCORE_WORDING["shared"]]
    return {"parts": parts, "overall": overall, "categories": categories, "reference": reference, "levels": levels,
            "nothing": nothing, "lines": lines, "text": "\n".join(lines) + "\n"}


def read_saved_map(path):
    """map.json from a saved hospital schema: the one file that the page saves, its folder, or map.json itself."""
    import zipfile
    from ..normalise import resolve
    path = Path(path)
    try:
        if path.is_dir():
            inner = path / "map" / MAP_FILE
            data = json.loads(decode((inner if inner.exists() else path / MAP_FILE).read_bytes()))
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                data = json.loads(decode(archive.read(f"map/{MAP_FILE}")))
        else:
            data = json.loads(decode(path.read_bytes()))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        raise MapError(WORDING["map_json"].format(where=str(path.name))) from None
    # A binding that names a normalisation is read back into its route.
    return resolve(data, path.name) if isinstance(data, dict) else data

