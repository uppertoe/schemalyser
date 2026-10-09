"""Reads the SQL of a capability of the catalogue and fills its parameters.

A capability of the catalogue in contract.json may name its SQL, as "sql": "capabilities/NAME.sql", a file under the
role model's folder that is public and the same at every hospital. The file is one SELECT over the role views, and it
begins with its own record of what it implements:

    -- capability: NAME
    -- version: N

Each parameter of the capability stands in the SQL as a named placeholder of one fixed form, {{name}}, where name is a
parameter that the catalogue entry declares, and every declared parameter appears. A placeholder is filled by the
parameter's type:

    number           a number, written as a literal
    whole            a whole number, written as a literal
    whole_or_empty   a whole number, or None for empty, written as a literal or NULL
    choice           one of the entry's "choices", written as a quoted literal
    table            rows of the entry's "columns", written as the body of a common table expression: one SELECT of
                     literals for each row, joined by UNION ALL, with each value cast to its column's type, so that the
                     file holds WITH name AS ({{name}}) and reads the table by that name. A column's type is kind (one
                     of the contract's kinds), number, whole or whole_or_empty.

A threshold by age band is a table, (from_days, until_days, threshold), because nearly every physiological measure
needs one, and a factor by agent is a table too. The filled SQL is a question over the roles, which the role policy
checks as it checks any other.
"""
import re
from pathlib import Path

from . import rolemap

FOLDER = rolemap.MODEL
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
TYPES = ("number", "whole", "whole_or_empty", "choice", "table")
COLUMN_TYPES = {"kind": "varchar(40)", "number": "float", "whole": "int", "whole_or_empty": "int"}


class CapabilityError(ValueError):
    """A capability's SQL, or a value given for one of its parameters, breaks a rule of the catalogue."""


def entry(name, model=None):
    """The catalogue entry of a capability. Raises CapabilityError for a name that the catalogue does not hold."""
    found = rolemap.capabilities(model).get(name)
    if found is None:
        raise CapabilityError(f"{name} is not a capability of the catalogue")
    return found


def with_sql(model=None):
    """The names of the capabilities whose catalogue entry names a SQL file, in the catalogue's order."""
    return [name for name, capability in rolemap.capabilities(model).items() if capability.get("sql")]


def read(name, model=None):
    """A capability's SQL, checked against its catalogue entry: {"name", "version", "sql", "parameters", "path"}.

    The file must name the capability and the version that the entry gives, and its placeholders must be exactly the
    entry's parameters, each of a type that the fill knows."""
    capability = entry(name, model)
    relative = capability.get("sql")
    if not relative:
        raise CapabilityError(f"{name}: the catalogue entry names no SQL")
    path = (FOLDER / relative).resolve()
    if FOLDER.resolve() not in path.parents or not path.is_file():
        raise CapabilityError(f"{name}: {relative} is not a file of the role model")
    sql = path.read_text(encoding="utf-8")
    head = sql.splitlines()[:2]
    if head != [f"-- capability: {name}", f"-- version: {capability['version']}"]:
        raise CapabilityError(f"{name}: the file begins with -- capability: {name} and -- version: {capability['version']}")
    declared = {p["name"]: p for p in capability["parameters"]}
    used = set(PLACEHOLDER.findall(sql))
    if used != set(declared):
        raise CapabilityError(f"{name}: the placeholders are {sorted(used)}, and the entry declares {sorted(declared)}")
    for parameter in declared.values():
        if parameter["type"] not in TYPES:
            raise CapabilityError(f"{name}: the parameter {parameter['name']} has the type {parameter['type']}, which "
                                  f"the fill does not know")
    return {"name": name, "version": capability["version"], "sql": sql, "parameters": declared, "path": path}


def _number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapabilityError(f"{where}: the value is a number")
    return repr(float(value))


def _whole(value, where, empty=False):
    if value is None and empty:
        return "NULL"
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapabilityError(f"{where}: the value is a whole number" + (", or None for empty" if empty else ""))
    return str(value)


def _value(kind, value, where, kinds):
    """One value as a literal of its type."""
    if kind == "kind":
        if value not in kinds:
            raise CapabilityError(f"{where}: {value!r} is not a kind of the contract")
        return "'" + value + "'"
    if kind == "number":
        return _number(value, where)
    if kind == "whole":
        return _whole(value, where)
    if kind == "whole_or_empty":
        return _whole(value, where, empty=True)
    raise CapabilityError(f"{where}: the type {kind} is not one that the fill knows")


def _table(parameter, rows, where, kinds):
    columns = parameter["columns"]
    if not isinstance(rows, list) or not rows:
        raise CapabilityError(f"{where}: the value is a list of at least one row")
    lines = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise CapabilityError(f"{where}: each row gives {', '.join(c['name'] for c in columns)}")
        cells = [f"CAST({_value(c['type'], v, where, kinds)} AS {COLUMN_TYPES[c['type']]}) AS {c['name']}"
                 for c, v in zip(columns, row)]
        lines.append("SELECT " + ", ".join(cells))
    return "\n    UNION ALL ".join(lines)


def fill(name, values, model=None):
    """The capability's SQL with every placeholder filled from values, {parameter: value}. Raises CapabilityError for
    a value that is missing, unknown or not of its parameter's type."""
    found = read(name, model)
    declared = found["parameters"]
    missing = sorted(set(declared) - set(values))
    unknown = sorted(set(values) - set(declared))
    if missing or unknown:
        raise CapabilityError(f"{name}: " + "; ".join(
            ([f"no value is given for {', '.join(missing)}"] if missing else []) +
            ([f"{', '.join(unknown)} is not a parameter"] if unknown else [])))
    kinds = set(rolemap.kinds())
    literals = {}
    for key, parameter in declared.items():
        where, value = f"{name}.{key}", values[key]
        kind = parameter["type"]
        if kind == "table":
            literals[key] = _table(parameter, value, where, kinds)
        elif kind == "choice":
            if value not in parameter["choices"]:
                raise CapabilityError(f"{where}: the value is one of {', '.join(parameter['choices'])}")
            literals[key] = "'" + value + "'"
        else:
            literals[key] = _value(kind, value, where, kinds)
    return PLACEHOLDER.sub(lambda m: literals[m.group(1)], found["sql"])
