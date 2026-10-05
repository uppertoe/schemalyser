"""Chooses, for each step of a conversion, a route whose tables and columns the catalogue holds.

A step in conversion.json may offer alternatives: other files that make the same rows by another route,
for example through a documented table where the step as written reads a summary table that a site may
not have. Once a catalogue is to hand, a step that reads a table or a column that the catalogue does not
hold gives way to the first of its alternatives whose tables and columns the catalogue all holds. A step
whose own tables and columns are all there is kept as written, and so is a step for which no alternative
fits, so that its checklist says what is missing.

An alternative may be given in conversion.json as {"file": ..., "effect": ...}, where effect is one
sentence, written by the author of the conversion, that says what the route changes in the answer, such
as a group of patients that it can no longer leave out. The checklist repeats it.

The choice is applied to a copy of the conversion folder (apply), whose conversion.json then names the
chosen file in the step's place, and which records the choices in ROUTES, so that every later reader of
that folder, the checklist, the specification and the source query among them, follows the same route.
"""
import json
from pathlib import Path

import sqlglot
from sqlglot import exp

from .extract import decode
from .translate import OMOP_SCHEMA

ROUTES = "routes.json"
MAXIMUM_EFFECT = 600


def _effect(entry):
    text = entry.get("effect") if isinstance(entry, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > MAXIMUM_EFFECT or "\n" in text:
        return ""
    return text.strip()


def reads(sql):
    """The source tables and the (table, column) pairs that one step reads, as the step spells them, or None if it cannot be parsed."""
    try:
        tree = sqlglot.parse_one(sql, dialect="tsql")
    except sqlglot.errors.SqlglotError:
        return None
    if tree is None:
        return None
    named = {cte.alias.upper() for cte in tree.find_all(exp.CTE)}
    tables, columns = {}, set()
    for select in tree.find_all(exp.Select):
        aliases = {}
        for table in select.find_all(exp.Table):
            if (table.db or "").upper() == OMOP_SCHEMA.upper() or not table.name or table.name.upper() in named:
                continue
            if table.find_ancestor(exp.Select) is not select:
                continue
            tables.setdefault(table.name.upper(), table.name)
            aliases[table.alias_or_name.upper()] = table.name
        for column in select.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select or not column.table:
                continue
            name = aliases.get(column.table.upper())
            if name is not None:
                columns.add((name, column.name))
    return list(tables.values()), sorted(columns)


def missing(sql, catalogue):
    """The names that a step reads and the catalogue does not hold: tables as T and columns as T.C, in the step's spelling."""
    found = reads(sql)
    if found is None:
        return []
    tables, columns = found
    gone = [t for t in tables if catalogue.table(t) is None]
    absent = {t.upper() for t in gone}
    gone += [f"{t}.{c}" for t, c in columns
             if t.upper() not in absent and catalogue.table(t) is not None and catalogue.table(t).column(c) is None]
    return gone


def choose(folder, catalogue):
    """The routes to take: [{"step", "chosen", "missing", "effect", "tried"}] for each step that gives way to an alternative.

    tried lists the alternatives that were passed over before the chosen one, each with what it lacks.
    """
    folder = Path(folder)
    entries = json.loads((folder / "conversion.json").read_text())
    found = []
    for entry in entries:
        offered = entry.get("alternatives") or []
        if not offered or not isinstance(entry.get("file"), str):
            continue
        own = folder / entry["file"]
        if not own.is_file():
            continue
        lacking = missing(decode(own.read_bytes()), catalogue)
        if not lacking:
            continue
        tried = []
        for item in offered:
            name = item.get("file") if isinstance(item, dict) else item
            path = folder / str(name)
            if not isinstance(name, str) or not path.is_file() or path.parent != folder:
                continue
            gaps = missing(decode(path.read_bytes()), catalogue)
            if not gaps and reads(decode(path.read_bytes())) is not None:
                found.append({"step": entry["file"], "chosen": name, "missing": lacking, "effect": _effect(item),
                              "tried": tried})
                break
            tried.append({"file": name, "missing": gaps})
    return found


def apply(folder, choices):
    """Writes the choices into a conversion folder, which must be a copy: each chosen file takes its step's place.

    The step's own file becomes the first of its alternatives, so that nothing is lost, and ROUTES records the choices.
    """
    folder = Path(folder)
    entries = json.loads((folder / "conversion.json").read_text())
    by_step = {choice["step"]: choice for choice in choices}
    for entry in entries:
        choice = by_step.get(entry.get("file"))
        if choice is None:
            continue
        rest = [item for item in entry.get("alternatives") or []
                if (item.get("file") if isinstance(item, dict) else item) != choice["chosen"]]
        entry["file"], entry["alternatives"] = choice["chosen"], [choice["step"], *rest]
    (folder / "conversion.json").write_text(json.dumps(entries, indent=1) + "\n")
    (folder / ROUTES).write_text(json.dumps(choices, indent=1) + "\n")


def chosen(folder):
    """The choices recorded in a conversion folder by apply, or []."""
    path = Path(folder) / ROUTES
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return []
    return [c for c in data if isinstance(c, dict) and {"step", "chosen", "missing"} <= set(c)] if isinstance(data, list) else []
