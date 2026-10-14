"""The role contract as data, read in one place: the parts, their columns and links, the kinds and vocabularies, the
mapping views and the capability catalogue, and the one function that says what a gate may do with a part.

This module is layer 1. It imports nothing of the package, so that every layer reads the contract through it and the
hospital-schema layer no longer stands in for the contract. The semantic hashes of the parts, which the saved hospital
schema records to know when its evidence went stale, and the lock of their versions are made here as well.
"""
import hashlib
import json
from pathlib import Path

MODEL = Path(__file__).parent / "rolemodel"

# What a gate may do with a part for a purpose. A gate never reads a part's status itself: it asks admission().
ADMITTED = "admitted"
CEILING = "admitted with a ceiling"
REFUSED = "refused"
# The purposes a gate has: a question over the roles (the role policy and the import), its compilation through a
# hospital schema (the two-part script), the description of the record (the page, which may propose and answer every
# part), and a conversion step over the roles.
PURPOSES = ("question", "compile", "describe", "conversion")


def contract():
    """The contract: the role views, their columns, types and meanings, and the vocabulary of kinds, as data."""
    return json.loads((MODEL / "contract.json").read_text(encoding="utf-8"))


# The fields of a part and of a column that give the part its meaning to a hospital schema and to a question: what the
# part is called, what identifies a row, how it links to the other parts, and what each column holds. A description, a
# title, a word a proposer searches for or a word it avoids, the audits that need a column and the part's status change
# none of that, so a change to them leaves every piece of evidence that rested on the part current.
SEMANTIC_PART_FIELDS = ("name", "key", "key_may_combine", "anchor", "links", "source_kinds", "domain", "identifying")
SEMANTIC_COLUMN_FIELDS = ("name", "type", "vocabulary", "mapping", "optional", "per_pathway", "if_empty", "same_source_as",
                          "identifying", "held_text")
# The one kind column whose kinds are the contract's own list of kinds of reading rather than a vocabulary, and the name
# under which vocabulary_hashes() gives that list.
READING_KINDS = ("role_reading", "kind")
READING_VOCABULARY = "kinds"
LOCK = MODEL / "parts.lock.json"


def part_definition(view):
    """The projection of a part or a mapping view that its hash is made from: the semantic fields of the part and of
    each of its columns, in the contract's order of columns, and nothing of its wording."""
    found = {field: view[field] for field in SEMANTIC_PART_FIELDS if field in view}
    found["columns"] = [{field: c[field] for field in SEMANTIC_COLUMN_FIELDS if field in c} for c in view.get("columns") or []]
    return found


def _digest(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def part_hashes(model=None):
    """The semantic hash of each part and of each mapping view, as {name: hash}: the hash of part_definition(), which
    changes when a part's name, key, links or columns change in what they are, and not when its words change. A
    change to it makes the evidence that rested on the part stale; a change to a vocabulary is a change to the
    translation of the columns that name it, which vocabulary_hashes() serves, and not to the part."""
    model = model or contract()
    return {view["name"]: _digest(part_definition(view)) for view in model["views"] + model.get("mapping_views", [])}


def vocabulary_hashes(model=None):
    """The hash of each closed vocabulary's kinds, as {vocabulary: hash}, with the contract's own kinds of reading
    under READING_VOCABULARY. The kinds are hashed by name: a kind added, removed or renamed is a change to what a
    column's codes translate to, and a kind's meaning reworded is not."""
    model = model or contract()
    found = {name: _digest([k.get("kind") for k in kinds]) for name, kinds in model["vocabularies"].items()}
    found[READING_VOCABULARY] = _digest([k.get("kind") for k in model["kinds"]])
    return found


def column_vocabulary(part, column, model=None):
    """The vocabulary that a column of a part names, as vocabulary_hashes() names it, or None where the column holds no
    kind."""
    if (part, column) == READING_KINDS:
        return READING_VOCABULARY
    for view in (model or contract())["views"]:
        if view["name"] == part:
            return next((c.get("vocabulary") for c in view["columns"] if c["name"] == column), None)
    return None


def part_versions(model=None):
    """The version of each part and of each mapping view, as {name: version}: a whole number that whoever changes a
    part's definition raises, which the lock file holds beside the part's hash and a question names for each part it
    reads."""
    model = model or contract()
    return {view["name"]: view.get("version") for view in model["views"] + model.get("mapping_views", [])}


def lock(model=None):
    """The lock of the parts, as parts.lock.json holds it: the contract's version and, for each part and mapping
    view, its version and its semantic hash. The checker compares the contract with the committed lock and refuses a
    changed definition whose version was not raised."""
    model = model or contract()
    hashes, versions = part_hashes(model), part_versions(model)
    return {"contract_version": model.get("version"),
            "parts": {name: {"version": versions[name], "hash": hashes[name]} for name in sorted(hashes)}}


def read_lock(path=None):
    """The committed lock file, or None where there is none."""
    path = Path(path) if path else LOCK
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


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


def views():
    """The role views that every map supplies, as {name: [column, ...]}, in the contract's order. These are the
    three views that the neonatal audit reads."""
    return {view["name"]: [column["name"] for column in view["columns"]] for view in contract()["views"]
            if view.get("status") == "contract"}


def all_views():
    """Every role view of the contract, the further views that a map may supply included, as {name: [column, ...]}."""
    return {view["name"]: [column["name"] for column in view["columns"]] for view in contract()["views"]}


def public_views():
    """Every view that a query over the roles may name: the role views and the mapping views, as {name: [column]}."""
    found = all_views()
    found.update({name: [c["name"] for c in view["columns"]] for name, view in mapping_views().items()})
    return found


def contract_column_types(model=None):
    """{view: {column: type}} over every part and mapping view."""
    model = model or contract()
    return {v["name"]: {c["name"]: c["type"] for c in v["columns"]} for v in model["views"] + model.get("mapping_views", [])}


def anchors(view):
    """The columns of a view, as a contract's view gives them, without which a row does not belong in the view: each
    link that is part of its key, and the anchor that the contract names, as a reading needs its anaesthetic."""
    links = {link["column"] for link in view.get("links", [])}
    return ({c for c in view.get("key", []) if c in links} | ({view["anchor"]} if view.get("anchor") else set()))


def statuses():
    """The status of each role view, as {name: status}: contract for the views that the audits read, draft for a
    further view that no audit reads yet, and rehearsed, once the contract has it, for a draft that runs through a naive
    schema on the stand-in and agrees with a hand-written reference."""
    return {view["name"]: view["status"] for view in contract()["views"]}


def kinds():
    return [item["kind"] for item in contract()["kinds"]]




def admission(part, purpose, model=None):
    """What a gate may do with a part for a purpose: ADMITTED, CEILING or REFUSED. Every part may be described. A
    part at contract status is admitted for every purpose. A rehearsed part, one that ran through a naive schema on
    the stand-in and was identical to a hand-written reference, is admitted with a ceiling for a question, its
    compilation and a conversion, as the contract's amendment of 14 October 2026 has it: a package over it may reach
    mapping supported and run on production, and it reaches production validated only through a reconciliation that
    covers each rehearsed part by name. A draft is refused for everything but description. A mapping view is admitted
    for every purpose, as a contract part is."""
    if purpose not in PURPOSES:
        raise ValueError(f"{purpose} is not a purpose a gate has")
    model = model or contract()
    if part in mapping_views(model):
        # A mapping view translates a hospital's codes and names none of them: any question may read it.
        return ADMITTED
    status = {v["name"]: v.get("status") for v in model["views"]}.get(part)
    if status is None:
        return REFUSED
    if purpose == "describe":
        return ADMITTED
    if status == "contract":
        return ADMITTED
    if status == "rehearsed":
        return CEILING
    return REFUSED
