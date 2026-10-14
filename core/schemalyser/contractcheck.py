"""The check of the contract's shape, for whoever adds a part, a column or a kind to the role model.

    python -m schemalyser.contractcheck [CONTRACT.json] [--roles ROLES.md] [--lock parts.lock.json] [--write-lock]

The checker reads the contract as data and the roles document as text and says, one sentence to a line, what breaks a
rule of the role model: a part or a column without the words a proposer searches for, a kind column whose vocabulary
does not exist, a link to a key that no part has, a vocabulary with no kinds, a roles document that has fallen out of
step with the contract, and a part whose definition changed without its version being raised. It changes nothing,
unless asked to write the lock file: the lock holds each part's version and semantic hash as last published, and
--write-lock records a new part or a raised version there once the contract passes every other rule. The tests run the
check over the contract that ships with the page, so that no version of the contract is published with a shape the
page cannot work from, and the try command of the hospital schema runs it over a candidate contract before opening a
saved schema under it.

The rules are the ones a new part needs to be proposable and readable, not a full schema of the file: a field the
checker does not name is left to the code that reads it.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import contract as model_reader

CONTRACT = Path(__file__).parent / "rolemodel" / "contract.json"
ROLES = Path(__file__).parent / "rolemodel" / "roles.md"
LOCK = model_reader.LOCK

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


def check_contract(model, roles_text=None, lock=None, published=True):
    """The problems of a contract, as sentences; an empty list is a contract whose shape the page can work from. With
    the text of the roles document, the document's parts, vocabularies, kinds and capabilities are checked against it.
    With a lock, as parts.lock.json holds it, each part's definition is compared with the one last published and a
    change without a raised version is refused; a candidate that is not yet published is held only to that, and not
    to the lock file being written."""
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
        if not _version(view.get("version")):
            problems.append(f"{where} has no version, or its version is not a whole number from 1.")
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
    for mapping in model["mapping_views"] if isinstance(model["mapping_views"], list) else []:
        if isinstance(mapping, dict) and isinstance(mapping.get("name"), str) and not _version(mapping.get("version")):
            problems.append(f"The mapping view {mapping['name']} has no version, or its version is not a whole number from 1.")
    reading_kinds = [k.get("kind") if isinstance(k, dict) else None for k in model["kinds"]]
    for kind in reading_kinds:
        if not isinstance(kind, str) or not NAME.fullmatch(kind):
            problems.append("A kind of reading is not named in lower-case letters.")
        elif reading_kinds.count(kind) > 1:
            problems.append(f"The kind of reading {kind} is defined more than once.")
    if roles_text is not None:
        problems += check_roles(model, roles_text)
    if lock is not None and not problems:
        problems += check_lock(model, lock, published)
    return problems


def _version(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def check_lock(model, lock, published=True):
    """The contract's parts and mapping views compared with the lock file: a part whose definition (its name, key,
    links and columns, in what they are and not in their words) differs from the one last published must carry a
    higher version than the lock holds, and a version is never lowered. For a published contract, the lock file must
    also be written: a new part, a raised version and a part the contract no longer defines are each named until it
    is. Returns the problems as sentences."""
    problems, unwritten = [], []
    held = (lock or {}).get("parts") or {}
    hashes, versions = model_reader.part_hashes(model), model_reader.part_versions(model)
    for name in sorted(hashes):
        where = f"The part {name}"
        if name not in held:
            unwritten.append(f"{where} is not in the lock file: write it there with --write-lock once the part is as it should be.")
            continue
        locked_version, locked_hash = held[name].get("version"), held[name].get("hash")
        if versions[name] < locked_version:
            problems.append(f"{where} has version {versions[name]}, and the lock file has {locked_version}: a version is never lowered.")
        elif hashes[name] != locked_hash and versions[name] == locked_version:
            problems.append(f"{where} has changed in what it is and its version is still {locked_version}: raise it to {locked_version + 1}.")
        elif hashes[name] != locked_hash:
            unwritten.append(f"{where} has changed to version {versions[name]}: write the lock file with --write-lock.")
        elif versions[name] > locked_version:
            unwritten.append(f"{where} has version {versions[name]} and the lock file has {locked_version}, and nothing of the part changed: write the lock file with --write-lock, or leave the version as it was.")
    for name in sorted(set(held) - set(hashes)):
        unwritten.append(f"The lock file has {name}, which the contract no longer defines: write the lock file with --write-lock.")
    return problems + (unwritten if published else [])


def write_lock(model, path=None):
    """Writes the lock of the given contract, and returns the path written. Nothing is written for a contract whose
    change breaks a rule that --write-lock cannot settle: a definition changed without a raised version, or a
    version lowered."""
    path = Path(path) if path else LOCK
    held = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    unsettled = check_lock(model, held, published=False)
    if unsettled:
        raise ValueError("\n".join(unsettled))
    path.write_text(json.dumps(model_reader.lock(model), indent=1) + "\n", encoding="utf-8")
    return path


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
    parser.add_argument("--lock", default=str(LOCK), help="the lock file of the parts' versions and hashes; the page's own when left out")
    parser.add_argument("--no-lock", action="store_true", help="do not compare the parts with the lock file")
    parser.add_argument("--write-lock", action="store_true",
                        help="once the contract passes every other rule, write the lock file from it")
    args = parser.parse_args(argv)
    try:
        model = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        roles = None if args.no_roles else Path(args.roles).read_text(encoding="utf-8")
        lock = None if args.no_lock else (model_reader.read_lock(args.lock) or {})
    except (OSError, ValueError) as error:
        print(f"The contract could not be read: {error}", file=sys.stderr)
        return 2
    problems = []
    if args.write_lock:
        # The lock is written only from a contract that keeps every other rule.
        problems = check_contract(model, roles)
        if not problems:
            try:
                written = write_lock(model, args.lock)
                print(f"The lock file {written} is written from the contract.")
                lock = model_reader.read_lock(args.lock)
            except ValueError as error:
                problems = str(error).splitlines()
    if not problems:
        problems = check_contract(model, roles, lock)
    for problem in problems:
        print(problem)
    if not problems:
        print("The contract keeps every rule of the role model's shape"
              + (", and the roles document is in step with it." if roles is not None else ".")
              + ("" if lock is None else " Every part is at the version the lock file holds."))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
