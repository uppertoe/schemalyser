"""The role contract as data, read in one place: the parts, their columns and links, the kinds and vocabularies, the
mapping views and the capability catalogue, and the one function that says what a gate may do with a part.

This module is layer 1. It imports nothing of the package, so that every layer reads the contract through it and the
hospital-schema layer no longer stands in for the contract. The hashes of the parts, which the saved hospital schema
records to know when its evidence went stale, are made here as well.
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
    part at contract status is admitted for every purpose. A rehearsed part, one that runs through a naive schema on
    the stand-in and agrees with a hand-written reference, is admitted with a ceiling for a question, its compilation
    and a conversion: the package it serves cannot claim more than the examination on made-up rows gives, until the
    contract's dated amendment for that part says otherwise. A draft is refused for everything but description."""
    if purpose not in PURPOSES:
        raise ValueError(f"{purpose} is not a purpose a gate has")
    status = {v["name"]: v.get("status") for v in (model or contract())["views"]}.get(part)
    if status is None:
        return REFUSED
    if purpose == "describe":
        return ADMITTED
    if status == "contract":
        return ADMITTED
    if status == "rehearsed":
        return CEILING
    return REFUSED
