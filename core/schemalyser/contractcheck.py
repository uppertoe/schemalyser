"""The check of the contract's shape, for whoever adds a part, a column or a kind to the role model.

    python -m schemalyser.contractcheck [CONTRACT.json] [--roles ROLES.md]

The checker reads the contract as data and the roles document as text and says, one sentence to a line, what breaks a
rule of the role model: a part or a column without the words a proposer searches for, a kind column whose vocabulary
does not exist, a link to a key that no part has, a vocabulary with no kinds, and a roles document that has fallen out
of step with the contract. It changes nothing. The tests run it over the contract that ships with the page, so that no
version of the contract is published with a shape the page cannot work from, and the try command of the hospital
schema runs it over a candidate contract before opening a saved schema under it.

The rules are the ones a new part needs to be proposable and readable, not a full schema of the file: a field the
checker does not name is left to the code that reads it.
"""
import argparse
import json
import re
import sys
from pathlib import Path

CONTRACT = Path(__file__).parent / "rolemodel" / "contract.json"
ROLES = Path(__file__).parent / "rolemodel" / "roles.md"

NAME = re.compile(r"[a-z][a-z0-9_]*")
# A kind is a lower-case name, or a phrase where the vocabulary says where a thing came from.
KIND = re.compile(r"[a-z][a-z0-9_' ]*")
# The one kind column whose kinds are the contract's own list of kinds of reading rather than a vocabulary.
READING_KINDS = ("role_reading", "kind")
VIEW_NAME = re.compile(r"role_[a-z][a-z0-9_]*")
STATUSES = ("contract", "draft")
# A phrase of one of these words alone tells a proposer nothing: it matches half of any dictionary.
GENERIC = {"id", "key", "date", "time", "value", "name", "type", "code", "number", "text", "flag", "record", "table",
           "column", "row", "line", "identifier", "status", "kind"}
# Vocabularies that the mapping views and the pathways use, which no column of a part names.
HELD_VOCABULARIES = ("source_kind", "mapping_status", "mapping_provenance")
TOP = ("version", "views", "types", "kinds", "vocabularies", "mapping_views", "capabilities")


def check_contract(model, roles_text=None):
    """The problems of a contract, as sentences; an empty list is a contract whose shape the page can work from. With
    the text of the roles document, the document's parts, vocabularies, kinds and capabilities are checked against it."""
    problems = []
    if not isinstance(model, dict):
        return ["The contract is not a JSON object."]
    for key in TOP:
        if key not in model:
            problems.append(f"The contract has no {key}.")
    if problems:
        return problems
    views = model["views"]
    types = set(model["types"]) if isinstance(model["types"], dict) else set()
    vocabularies = model["vocabularies"] if isinstance(model["vocabularies"], dict) else {}
    mappings = {m.get("name") for m in model["mapping_views"]} if isinstance(model["mapping_views"], list) else set()
    source_kinds = {k.get("kind") for k in vocabularies.get("source_kind") or [] if isinstance(k, dict)}
    names = [v.get("name") for v in views if isinstance(v, dict)]
    keys_of = {v["name"]: list(v.get("key") or []) for v in views if isinstance(v, dict) and "name" in v}
    columns_of = {v["name"]: [c.get("name") for c in v.get("columns") or [] if isinstance(c, dict)]
                  for v in views if isinstance(v, dict) and "name" in v}
    used_vocabularies = set(HELD_VOCABULARIES)
    for view in views:
        if not isinstance(view, dict) or not isinstance(view.get("name"), str):
            problems.append("A part has no name.")
            continue
        name = view["name"]
        where = f"The part {name}"
        if not VIEW_NAME.fullmatch(name):
            problems.append(f"{where} is not named role_ and lower-case letters.")
        if names.count(name) > 1:
            problems.append(f"{where} is defined more than once.")
        if view.get("status") not in STATUSES:
            problems.append(f"{where} has a status that is not one of {', '.join(STATUSES)}.")
        if "required" in view:
            problems.append(f"{where} carries required, which the contract no longer uses: its status says what every gate may do with it.")
        for field in ("title", "description", "one_row_per", "holds"):
            if not _text(view.get(field)):
                problems.append(f"{where} has no {field.replace('_', ' ')}.")
        problems += _look_for(view.get("look_for"), where)
        columns = columns_of[name]
        if not columns:
            problems.append(f"{where} has no columns.")
        for column in columns:
            if columns.count(column) > 1:
                problems.append(f"{where} defines the column {column} more than once.")
        key = view.get("key")
        if not isinstance(key, list) or not key or any(k not in columns for k in key):
            problems.append(f"{where} has no key, or its key names a column the part does not have.")
        for link in view.get("links") or []:
            column, to = (link.get("column"), link.get("to")) if isinstance(link, dict) else (None, None)
            if column not in columns:
                problems.append(f"{where} links from {column}, which is not one of its columns.")
            target, _, target_column = str(to or "").partition(".")
            if target not in keys_of or target_column not in columns_of.get(target, []):
                problems.append(f"{where} links to {to}, which no part has.")
            elif target_column not in keys_of[target]:
                problems.append(f"{where} links to {to}, which is not the key of {target}.")
        for kind in view.get("source_kinds") or []:
            if kind not in source_kinds:
                problems.append(f"{where} names the source kind {kind}, which the source kind vocabulary does not have.")
        for column in view.get("columns") or []:
            problems += _column(column, view, types, vocabularies, mappings, used_vocabularies)
    for name, kinds in vocabularies.items():
        where = f"The vocabulary {name}"
        if not isinstance(kinds, list) or not kinds:
            problems.append(f"{where} has no kinds.")
            continue
        seen = [k.get("kind") if isinstance(k, dict) else None for k in kinds]
        for kind in kinds:
            if not isinstance(kind, dict) or not isinstance(kind.get("kind"), str) or not KIND.fullmatch(kind["kind"]):
                problems.append(f"{where} has a kind that is not named in lower-case letters.")
            elif seen.count(kind["kind"]) > 1:
                problems.append(f"{where} has the kind {kind['kind']} more than once.")
            elif not _text(kind.get("meaning")):
                problems.append(f"{where} gives the kind {kind['kind']} no meaning.")
        if name not in used_vocabularies:
            problems.append(f"{where} is named by no column of any part.")
    reading_kinds = [k.get("kind") if isinstance(k, dict) else None for k in model["kinds"]]
    for kind in reading_kinds:
        if not isinstance(kind, str) or not NAME.fullmatch(kind):
            problems.append("A kind of reading is not named in lower-case letters.")
        elif reading_kinds.count(kind) > 1:
            problems.append(f"The kind of reading {kind} is defined more than once.")
    if roles_text is not None:
        problems += check_roles(model, roles_text)
    return problems


def _column(column, view, types, vocabularies, mappings, used):
    problems = []
    if not isinstance(column, dict) or not isinstance(column.get("name"), str) or not NAME.fullmatch(column["name"]):
        return [f"The part {view['name']} has a column that is not named in lower-case letters."]
    where = f"The column {column['name']} of {view['name']}"
    for field in ("title", "meaning", "holds"):
        if not _text(column.get(field)):
            problems.append(f"{where} has no {field}.")
    if column.get("type") not in types:
        problems.append(f"{where} has a type that the contract does not define.")
    if column.get("type") == "kind":
        vocabulary = column.get("vocabulary")
        if vocabulary is None and (view["name"], column["name"]) == READING_KINDS:
            pass  # The kinds of reading are the contract's own list, not a vocabulary.
        elif vocabulary not in vocabularies:
            problems.append(f"{where} names the vocabulary {vocabulary}, which the contract does not have.")
        else:
            used.add(vocabulary)
    elif "vocabulary" in column:
        problems.append(f"{where} names a vocabulary but is not a kind.")
    if column.get("type") == "local_key" and column.get("mapping") not in mappings:
        problems.append(f"{where} names the mapping view {column.get('mapping')}, which the contract does not have.")
    linked = column["name"] in {link.get("column") for link in view.get("links") or [] if isinstance(link, dict)}
    if column.get("vocabulary") == "source_kind" or column.get("per_pathway"):
        pass
    elif not column.get("look_for") and not linked and column["name"] not in (view.get("key") or []):
        problems.append(f"{where} gives no words to look for, so no proposer can find it.")
    else:
        problems += _look_for(column.get("look_for"), where, allow_empty=True)
    if not isinstance(column.get("needed_by", []), list):
        problems.append(f"{where} has a needed_by that is not a list.")
    return problems


def _look_for(words, where, allow_empty=False):
    if words is None or words == []:
        return [] if allow_empty else [f"{where} gives no words to look for, so no proposer can find it."]
    if not isinstance(words, list) or not all(_text(w) for w in words):
        return [f"{where} has words to look for that are not a list of phrases."]
    generic = [w for w in words if w.strip().lower() in GENERIC]
    if generic:
        return [f"{where} looks for {', '.join(generic)}, which alone would match half of any dictionary."]
    return []


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def check_roles(model, text):
    """The roles document checked against the contract: a section for each part and for no other, the vocabulary
    table's kinds, a row for each kind of reading, and a row for each capability."""
    problems = []
    sections = set(re.findall(r"^### (role_[a-z0-9_]+):", text, re.M))
    parts = {v["name"] for v in model["views"] if isinstance(v, dict) and "name" in v}
    for name in sorted(parts - sections):
        problems.append(f"The roles document has no section for {name}.")
    for name in sorted(sections - parts):
        problems.append(f"The roles document has a section for {name}, which the contract does not have.")
    table = re.search(r"^\| Vocabulary \| Kinds \|\n\|[-| ]+\|\n((?:\|.*\|\n)+)", text, re.M)
    rows = {}
    if table:
        for line in table.group(1).strip().splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 2:
                rows[cells[0]] = [k.strip() for k in cells[1].split(",") if k.strip()]
    else:
        problems.append("The roles document has no table of vocabularies and kinds.")
    vocabularies = model["vocabularies"] if isinstance(model["vocabularies"], dict) else {}
    for name, kinds in vocabularies.items():
        held = [k.get("kind") for k in kinds if isinstance(k, dict)]
        if name not in rows:
            problems.append(f"The roles document's table of vocabularies has no row for {name}.")
        elif rows[name] != held:
            problems.append(f"The roles document lists the kinds of {name} as {', '.join(rows[name])}, and the contract as {', '.join(held)}.")
    for name in sorted(set(rows) - set(vocabularies)):
        problems.append(f"The roles document's table of vocabularies has a row for {name}, which the contract does not have.")
    for kind in model["kinds"]:
        name = kind.get("kind") if isinstance(kind, dict) else None
        if name and not re.search(rf"^\| {re.escape(name)} \|", text, re.M):
            problems.append(f"The roles document has no row for the kind of reading {name}.")
    for capability in model["capabilities"]:
        name = capability.get("name") if isinstance(capability, dict) else None
        if name and not re.search(rf"^\| {re.escape(name)} \|", text, re.M):
            problems.append(f"The roles document has no row for the capability {name}.")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.contractcheck", description=__doc__.split("\n")[0])
    parser.add_argument("contract", nargs="?", default=str(CONTRACT), help="the contract to check; the page's own when left out")
    parser.add_argument("--roles", default=str(ROLES), help="the roles document to check against it; the page's own when left out")
    parser.add_argument("--no-roles", action="store_true", help="check the contract alone")
    args = parser.parse_args(argv)
    try:
        model = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        roles = None if args.no_roles else Path(args.roles).read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        print(f"The contract could not be read: {error}", file=sys.stderr)
        return 2
    problems = check_contract(model, roles)
    for problem in problems:
        print(problem)
    if not problems:
        print("The contract keeps every rule of the role model's shape"
              + (", and the roles document is in step with it." if roles is not None else "."))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
