"""The question package, schemalyser-question/1: a clinician's question over the public roles, as the service writes
it and the core reads it, and the one reader and validator of it in Python.

    python -m schemalyser.question check PACKAGE.json

A package holds the title; the statement, the clinician's words for the cohort, the outcome and the result; the
decisions, each typed, with what it is about, the choice made, a typed value where one exists, the sentence, who made
it and when; an optional examinable object, the structured form of the decisions that the examiner's oracle reads;
the capabilities of the catalogue that the question composes, each with its version and parameters; the SQL over the
roles; the parts the SQL reads, each with its version in the contract and its admission for a question; any proposal
for a part or a column the contract lacks, in the contract's own shape; the contract's version; and who produced it.
Its status is not a field: what a package may claim is decided by the evidence recorded about it, never written into
it.

The shape is written once, in rolemodel/question.schema.json, in the subset of JSON Schema that the service's reader
implements as well, and this module validates against it. What the schema cannot say, check() does: the parts are
exactly the ones the SQL reads, at the contract's versions and with the contract's admissions; every capability is
in the catalogue at its version with its parameters; every proposal keeps the contract's shape; and every date reads.
The role policy, the two-part form and the examination are later stages, which read the package through this module.

This module is layer 3. It reads the contract through layer 1 and the catalogue through layer 2, and no hospital's
material.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import contract as model_reader
from . import contractcheck, rolemap

FORMAT = "schemalyser-question/1"
# A package file is named NAME.question.json, so that a reader knows what it holds before opening it.
PACKAGE_SUFFIX = ".question.json"
ADMITTED, CEILING, REFUSED = model_reader.ADMITTED, model_reader.CEILING, model_reader.REFUSED
SCHEMA = Path(__file__).parent / "rolemodel" / "question.schema.json"
RULES = {
    "json": "the file holds one JSON object",
    "schema": "the package has the fields of schemalyser-question/1 and no other",
    "format": "the format is schemalyser-question/1",
    "contract_version": "the package was written against the contract that Schemalyser holds",
    "parts": "the parts are exactly the parts the SQL reads",
    "part_version": "each part is at its version in the contract",
    "admission": "each part's admission is what the contract gives it for a question",
    "capabilities": "each capability is in the catalogue at its version, with its parameters",
    "proposals": "each proposal keeps the contract's shape",
    "dates": "every date and time reads",
    "examinable": "the examinable object names its oracle or says why it has none",
}


class QuestionError(ValueError):
    """A package that breaks a rule, with the failed rules' sentences."""


def schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def read(path, model=None):
    """The package at path, checked. Raises QuestionError naming every failed rule."""
    found = check(Path(path).read_text(encoding="utf-8"), model)
    if found["failed"]:
        raise QuestionError("\n".join(sentences(found)))
    return found["package"]


def check(text, model=None):
    """The rules applied to the text of a package. Returns {"rules": [{"id", "rule", "passed", "fragments"}],
    "failed": [rule ids], "package": the package as read, or None where it is not JSON}."""
    model = model or rolemap.contract()
    failures = {rule: [] for rule in RULES}

    def fail(rule, fragment):
        if fragment not in failures[rule]:
            failures[rule].append(fragment)

    package = None
    try:
        package = json.loads(text) if isinstance(text, str) else text
        if not isinstance(package, dict):
            raise ValueError("not an object")
    except ValueError as error:
        fail("json", f"the text is not one JSON object: {error}")
        package = None
    if package is not None:
        for where, problem in validate(package, schema()):
            fail("format" if where == "format" else "schema", f"{where}: {problem}")
        if not failures["schema"] and not failures["format"]:
            _check_content(package, model, fail)
    rules = [{"id": rule, "rule": RULES[rule], "passed": not failures[rule], "fragments": failures[rule]} for rule in RULES]
    return {"rules": rules, "failed": [r["id"] for r in rules if not r["passed"]], "package": package}


def sentences(found):
    """The failed rules of check() as sentences, one for each fragment."""
    return [f"The package breaks the rule that {r['rule']}: {fragment}." for r in found["rules"] if not r["passed"]
            for fragment in r["fragments"]]


def sql_and_decisions(path, model=None):
    """What a query file supplies to a report or a build: for a package file, one named NAME.question.json, the SQL
    over the roles, the decisions in the audit's shape and the package itself, read and checked through this module;
    for a plain .sql file, its text, no decisions and no package. Raises QuestionError for a package that breaks a
    rule."""
    path = Path(path)
    if path.name.endswith(PACKAGE_SUFFIX):
        package = read(path, model)
        return package["question_sql"], audit_decisions(package), package
    return path.read_text(encoding="utf-8"), None, None


def audit_decisions(package):
    """A package's decisions in the shape the audit records, decisions.json: about, the sentence, who and when."""
    return {"decisions": [{"about": d["about"], "decision": d["decision"], "by": d["by"], "date": d["date"]}
                          for d in package["decisions"]], "exact_small_numbers": False}


def _check_content(package, model, fail):
    if package["contract_version"] != model.get("version"):
        fail("contract_version", f"the package names contract version {package['contract_version']}, and Schemalyser holds {model.get('version')}")
    versions = model_reader.part_versions(model)
    named = [p["name"] for p in package["parts"]]
    for name in named:
        if named.count(name) > 1:
            fail("parts", f"{name} is listed more than once")
    read_by_sql, unreadable = parts_read(package["question_sql"], model)
    if unreadable:
        fail("parts", unreadable)
    for name in sorted(set(named) - set(versions)):
        fail("parts", f"{name} is not a part of the contract")
    if not unreadable:
        for name in sorted(set(read_by_sql) - set(named)):
            fail("parts", f"the SQL reads {name}, which the parts do not list")
        for name in sorted(set(named) - set(read_by_sql) - (set(named) - set(versions))):
            fail("parts", f"the parts list {name}, which the SQL does not read")
    for part in package["parts"]:
        name = part["name"]
        if name not in versions:
            continue
        if part["version"] != versions[name]:
            fail("part_version", f"{name} is at version {versions[name]} in the contract, and the package names version {part['version']}")
        wanted = model_reader.admission(name, "question", model)
        if part["admission"] != wanted:
            fail("admission", f"the contract gives {name} the admission '{wanted}' for a question, and the package says '{part['admission']}'")
    catalogue = rolemap.capabilities(model)
    for capability in package["capabilities"]:
        _check_capability(capability, catalogue, fail, "capabilities")
    for n, proposal in enumerate(package["proposals"], 1):
        for problem in _proposal_problems(proposal, model):
            fail("proposals", f"proposal {n}: {problem}")
    for n, decision in enumerate(package["decisions"], 1):
        if not _is_date(decision["date"]):
            fail("dates", f"decision {n} is dated {decision['date']}, which is not a date written as YYYY-MM-DD")
    if not _is_time(package["produced"]["at"]):
        fail("dates", f"produced.at is {package['produced']['at']}, which is not a time written as ISO 8601")
    examinable = package.get("examinable")
    if examinable is not None:
        if examinable.get("oracle") == "none":
            if not examinable.get("because"):
                fail("examinable", "the examinable says the oracle is none and does not say why")
        else:
            for field in ("cohort", "outcome", "result"):
                if field not in examinable:
                    fail("examinable", f"the examinable has no {field}, and does not say the oracle is none")
            measure = (examinable.get("outcome") or {}).get("measure")
            if isinstance(measure, dict):
                _check_capability(measure, catalogue, fail, "examinable")
            part = (examinable.get("outcome") or {}).get("part")
            if part is not None and part not in versions:
                fail("examinable", f"the outcome's part {part} is not a part of the contract")


def _check_capability(capability, catalogue, fail, rule):
    name = capability["name"]
    entry = catalogue.get(name)
    if entry is None:
        fail(rule, f"{name} is not a capability of the catalogue")
        return
    if capability["version"] != entry.get("version"):
        fail(rule, f"{name} is at version {entry.get('version')} in the catalogue, and the package names version {capability['version']}")
    declared = {p["name"] for p in entry.get("parameters") or []}
    given = set(capability["parameters"])
    if given - declared:
        fail(rule, f"{name} has no parameter named {', '.join(sorted(given - declared))}")
    if declared - given:
        fail(rule, f"{name} is given no value for {', '.join(sorted(declared - given))}")


def _proposal_problems(proposal, model):
    """A proposal tried against the contract through the checker: a column joined to its part, or a part added as a
    draft at version 1, with the checker's sentences as the problems."""
    candidate = json.loads(json.dumps(model))
    shape = json.loads(json.dumps(proposal["shape"]))
    parts = {v["name"]: v for v in candidate["views"]}
    if proposal["kind"] == "column":
        part = parts.get(proposal["part"])
        if part is None:
            return [f"a column would join {proposal['part']}, which the contract does not have"]
        if shape.get("name") in {c["name"] for c in part["columns"]}:
            return [f"{proposal['part']} already has a column named {shape.get('name')}"]
        shape.setdefault("needed_by", [])
        part["columns"].append(shape)
    else:
        if proposal["part"] in parts:
            return [f"the contract already has a part named {proposal['part']}"]
        if shape.get("name") != proposal["part"]:
            return [f"the shape is named {shape.get('name')}, and the proposal {proposal['part']}"]
        shape["status"], shape["version"] = "draft", 1
        for column in shape.get("columns") or []:
            if isinstance(column, dict):
                column.setdefault("needed_by", [])
        candidate["views"].append(shape)
    return [problem.rstrip('.') for problem in contractcheck.check_contract(candidate)]


def parts_read(sql, model=None):
    """The public views a question's SQL reads, as a sorted list, and the reason it could not be read, or None. A
    common table expression is not a view; a name that is no view of the contract is reported as read, so that the
    rule names it."""
    if not sql.strip():
        return [], None
    try:
        tree = sqlglot.parse_one(sql, dialect="tsql", error_level=sqlglot.ErrorLevel.RAISE)
    except (RecursionError, sqlglot.errors.SqlglotError):
        return [], "the SQL cannot be read as one statement, which the role policy says more about"
    if tree is None:
        return [], "the SQL holds no statement"
    ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    found = set()
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Identifier) and table.name.lower() not in ctes:
            found.add(table.name.lower())
    return sorted(found), None


def _is_date(value):
    try:
        dt.date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _is_time(value):
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError, AttributeError):
        return False


# The validator of the schema's subset of JSON Schema. The schema describes the subset at its head; the service's
# reader implements the same subset, so that the one schema is read on both sides.

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def validate(value, schema, path="", root=None):
    """The problems of a value against a schema of the subset, as [(path, sentence)]; [] where it keeps the schema."""
    root = root if root is not None else schema
    problems = []
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return validate(value, root["$defs"][name], path, root)
    if "anyOf" in schema:
        if not any(not validate(value, option, path, root) for option in schema["anyOf"]):
            problems.append((path or "the package", "is none of the shapes it may have"))
        return problems
    if "const" in schema and value != schema["const"]:
        problems.append((path or "the package", f"is not {json.dumps(schema['const'])}"))
        return problems
    if "enum" in schema and value not in schema["enum"]:
        problems.append((path or "the package", f"is not one of {', '.join(json.dumps(v) for v in schema['enum'])}"))
        return problems
    if "type" in schema and not _of_type(value, schema["type"]):
        wanted = schema["type"] if isinstance(schema["type"], str) else " or ".join(schema["type"])
        problems.append((path or "the package", f"is not {_an(wanted)}"))
        return problems
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            problems.append((path, "is empty" if schema.get("minLength") == 1 else f"is shorter than {schema['minLength']} characters"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append((path, f"does not match {schema['pattern']}"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append((path, f"is below {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            problems.append((path, f"is above {schema['maximum']}"))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problems.append((path, "is empty" if schema.get("minItems") == 1 else f"holds fewer than {schema['minItems']} items"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append((path, f"holds more than {schema['maxItems']} items"))
        if "items" in schema:
            for n, item in enumerate(value):
                problems += validate(item, schema["items"], f"{path}[{n}]", root)
    if isinstance(value, dict):
        for field in schema.get("required", []):
            if field not in value:
                problems.append((f"{path}.{field}" if path else field, "is missing"))
        properties = schema.get("properties", {})
        for field, held in value.items():
            where = f"{path}.{field}" if path else field
            if field in properties:
                problems += validate(held, properties[field], where, root)
            elif schema.get("additionalProperties") is False:
                problems.append((where, "is not a field of the package" if not path else "is not a field it may have"))
            elif isinstance(schema.get("additionalProperties"), dict):
                problems += validate(held, schema["additionalProperties"], where, root)
    return problems


def _of_type(value, wanted):
    for name in ([wanted] if isinstance(wanted, str) else wanted):
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name in TYPES and isinstance(value, TYPES[name]) and not (name != "boolean" and isinstance(value, bool)):
            return True
    return False


def _an(word):
    return ("an " if word[0] in "aeiou" else "a ") + word


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "check":
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    try:
        text = Path(argv[1]).read_text(encoding="utf-8")
    except OSError as error:
        print(f"The package could not be read: {error}", file=sys.stderr)
        return 2
    found = check(text)
    for line in sentences(found):
        print(line)
    if not found["failed"]:
        package = found["package"]
        print(f"{Path(argv[1]).name} keeps every rule of schemalyser-question/1: {package['title']}, over "
              f"{', '.join(p['name'] for p in package['parts']) or 'no part'}, with {len(package['decisions'])} decisions.")
    return 1 if found["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
