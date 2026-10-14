"""Stage 1 of the examination, the static stage: everything that can be said of a package without running it."""
import hashlib
import json
from pathlib import Path

import sqlglot

from .. import question, workspace

STAGE = "static"
RULES = {
    "columns": "The columns the package declares in its examinable result are the columns its SQL returns, in that order.",
}


def run(path, admit=None):
    """The static stage over a package file. Returns the stage's report: the package's path, hash and title; every
    rule applied with whether it passed and the fragments that broke it; the rules failed; the two-part form's
    problems; the policy's admission of each part and the dialect's version; and the level the stage grants, "well
    formed" where every rule passes and the question has the two-part form, with a sentence saying so or why not.
    admit="draft" lets the examiner read a draft part, which the report records."""
    path = Path(path)
    data = path.read_bytes() if path.is_file() else b""
    checked = workspace.check_question(path, admit=admit)
    rules = list(checked["rules"])
    package = checked["package"]
    declared = _declared_columns(package, checked["sql"]) if package is not None else []
    rules.append({"id": "columns", "rule": RULES["columns"], "passed": not declared, "fragments": declared})
    failed = [r["id"] for r in rules if not r["passed"]]
    admission = {"ceiling": [], "drafts": []}
    dialect_version = None
    if package is not None and not checked["failed"]:
        from .. import rolepolicy
        policy = rolepolicy.check(checked["sql"], [c["name"] for c in package["capabilities"]], admit=admit)
        admission, dialect_version = policy["admission"], policy["dialect_version"]
    form = checked["form"]
    level = "well formed" if not failed and not form else "not well formed"
    if failed:
        says = f"The package is not well formed: it breaks {_count(len(failed), 'rule')}, {', '.join(failed)}."
    elif form:
        says = ("The package is not well formed, because its question lacks the two-part form a hospital compiles: "
                + "; ".join(_form_words(f) for f in form) + ".")
    else:
        says = "The package is well formed: it keeps every rule of its format and of the role policy, and its question has the two-part form."
        if admission["ceiling"]:
            says += f" It reads {', '.join(admission['ceiling'])}, admitted with a ceiling."
        if admission["drafts"]:
            says += f" It reads {', '.join(admission['drafts'])}, a draft admitted for this examination only."
    return {"stage": STAGE, "package": {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                                        "title": package["title"] if package else None},
            "rules": rules, "failed": failed, "form": form, "admission": admission, "dialect_version": dialect_version,
            "level": level, "says": says}


def _declared_columns(package, sql):
    """The problems with the columns the examinable result declares against the SQL's final SELECT, as fragments."""
    examinable = package.get("examinable") or {}
    declared = (examinable.get("result") or {}).get("columns")
    if not declared or not sql:
        return []
    try:
        tree = sqlglot.parse_one(sql, dialect="tsql")
    except sqlglot.errors.SqlglotError:
        return ["the SQL cannot be read, so its columns cannot be compared with those declared"]
    returned = [e.alias_or_name for e in tree.expressions] if hasattr(tree, "expressions") else []
    if [c.lower() for c in returned] != [c.lower() for c in declared]:
        return [f"the examinable result declares the columns {', '.join(declared)}, and the SQL returns {', '.join(returned) or 'none it names'}"]
    return []


def _form_words(rule):
    from .. import audit
    return audit.PUBLIC_FORM.get(rule, rule).rstrip(".")


def _count(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def sentences(report):
    """The report's failed rules as sentences, one for each fragment, then its verdict."""
    lines = [f"The package breaks the rule that {r['rule'][0].lower() + r['rule'][1:].rstrip('.')}: {fragment}."
             for r in report["rules"] if not r["passed"] for fragment in r["fragments"]]
    return lines + [report["says"]]


def to_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
