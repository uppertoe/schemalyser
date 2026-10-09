"""Named normalisations: source logic that is more than naming a column, recorded in the hospital schema on its own.

The contract's layer 2 rule says that a normalisation which is no more than naming a column is a binding, and that one
which is more, such as a route through several tables or several rows joined back into one text, is a named
normalisation of its own, with its inputs, its output grain, its SQL, its assumptions and its tests, so that
complicated source logic is never hidden inside what looks like a column binding.

In the saved map.json such a binding is written as a reference,

    {"table", "column", "data_type", ["derive"], "normalisation": NAME}

and map.json gains a section

    "normalisations": {NAME: {"name", "part", "column", "inputs": [{"table", "columns"}], "grain": "one row for each ...",
                              "sql", "assumptions": [sentence, ...], "tests": [{"run", "entry", "outcome", "date"}],
                              "path": [step, ...], "joined": {...} where the rows are joined}}

path and joined are the structure from which both the normalisation's own SQL and the part's view are written, so the
two cannot drift apart. Inside the sitting a binding holds its route inline, as the correction forms make it; lift()
writes the reference and the section when the map is saved, and resolve() reads them back, so that the compiled view
of a part is always written from the normalisation that the file names. A filter on a code that says what kind of
record a row is stays in the binding of the part's rows; no binding attributes a row by a time window. A binding of a
further pathway to a part is named with the pathway, as drug@orders.drug, and its record names the pathway.
"""
import copy

from . import propose, rolemap

FIELDS = {"name", "part", "column", "inputs", "grain", "sql", "assumptions", "tests", "path", "joined", "pathway"}

WORDING = {
    "step": "The route assumes that each row of {a} matches at most one row of {b} on {pairs}, so that it repeats no "
            "row of {part}.",
    "empty": "Where a row of {part} has no matching row along the route, {about} is empty.",
    "joined": "The rows of {table} whose {link} matches {on} are joined into one text in the order of {table}.{order}, "
              "separated by {separator}.",
    "joined_empty": "Where no row of {table} matches, {about} is empty.",
    "space": "a space",
    "nothing": "nothing",
}


def qualifies(binding):
    """Whether a binding is more than naming a column: a route through two or more joins, or rows joined into one
    text. A filter is left in the binding of the part's rows (see the module's description)."""
    if not binding:
        return False
    return bool(binding.get("joined")) or len(binding.get("path") or []) >= 2


def name_of(view, column, pathway=None):
    """The name of a column's normalisation, as reading.anaesthetic_key, or drug@orders.drug for a further pathway."""
    return f"{view.removeprefix('role_')}{'@' + pathway if pathway else ''}.{column}"


def _inputs(base, binding):
    from .corrections import named_columns
    tables = {base.upper(): (base, [])}
    for table, column in named_columns(binding):
        held = tables.setdefault(table.upper(), (table, []))
        if column not in held[1]:
            held[1].append(column)
    return [{"table": table, "columns": columns} for table, columns in tables.values()]


def sql_of(view, column, base, binding):
    """The normalisation's own SQL: the value it gives for each row of the part's table."""
    aliases, joins = {(): "t0"}, []
    alias, _ = propose.walk(binding.get("path") or [], aliases, joins)
    ref = f"{alias}.{propose._name(binding['column'])}"
    expression = propose.joined_sql(binding["joined"], ref, f"j{len(aliases)}") if binding.get("joined") else ref
    lines = [f"SELECT {expression} AS {column}", f"FROM   {propose._name(base)} t0"] + [f"       {j}" for j in joins]
    return "\n".join(lines) + "\n"


def describe_one(view, column, base, binding, model=None, tests=()):
    """The normalisation record of one binding that qualifies."""
    model = model or rolemap.contract()
    spec = next(v for v in model["views"] if v["name"] == view)
    about = rolemap.plain_about(f"{view}.{column}")
    part = rolemap.view_title(view, False)
    assumptions = []
    for step in binding.get("path") or []:
        pairs = " and ".join(f"{step[0]}.{a} = {step[2]}.{b}" for a, b in propose.step_pairs(step))
        assumptions.append(WORDING["step"].format(a=step[0], b=step[2], pairs=pairs, part=part))
    if binding.get("path"):
        assumptions.append(WORDING["empty"].format(part=part, about=about))
    joined = binding.get("joined")
    if joined:
        separator = joined.get("separator", " ")
        shown = WORDING["space"] if separator == " " else WORDING["nothing"] if separator == "" else f"'{separator}'"
        assumptions.append(WORDING["joined"].format(table=joined["table"], link=joined["link"], on=f"{binding['table']}.{binding['column']}",
                                                    order=joined["order"], separator=shown))
        assumptions.append(WORDING["joined_empty"].format(table=joined["table"], about=about))
    found = {"name": name_of(view, column), "part": view, "column": column, "inputs": _inputs(base, binding),
             "grain": f"one row for each {spec['one_row_per']}", "sql": sql_of(view, column, base, binding),
             "assumptions": assumptions, "tests": [dict(t) for t in tests],
             "path": copy.deepcopy(binding.get("path") or [])}
    if joined:
        found["joined"] = copy.deepcopy(joined)
    return found


def lift(data, model=None, tests=None):
    """A copy of a map's data in which every binding that qualifies is written as a reference to a named
    normalisation, with the normalisations section. tests, where given, is {about: [test, ...]}."""
    data = copy.deepcopy(data)
    data.pop("normalisations", None)
    found = {}
    for view, whole in data.get("roles", {}).items():
      for pathway, role in rolemap.pathways(view, whole):
        base = (role["rows"].get("binding") or {}).get("table")
        part = view if pathway is None else f"{view}@{pathway}"
        for column, item in role["columns"].items():
            binding = item.get("binding")
            if not base or not qualifies(binding):
                continue
            record = describe_one(view, column, base, binding, model, (tests or {}).get(f"{part}.{column}", ()))
            if pathway is not None:
                record["name"], record["pathway"] = name_of(view, column, pathway), pathway
            found[record["name"]] = record
            item["binding"] = {k: v for k, v in binding.items() if k not in ("path", "joined")}
            item["binding"]["normalisation"] = record["name"]
    if found:
        data["normalisations"] = found
    return data


def resolve(data, where=rolemap.MAP_FILE):
    """A copy of a map's data in which each reference to a normalisation is read back into the binding's route, as
    the sitting holds it. Raises rolemap.MapError for a reference that names no normalisation of the same part, or a
    normalisation of the wrong shape."""
    data = copy.deepcopy(data)
    held = data.pop("normalisations", None) or {}
    if not isinstance(held, dict):
        raise rolemap.MapError(rolemap.WORDING["map_shape"].format(where=where, problem="normalisations is a section of named normalisations"))
    for name, record in held.items():
        if not isinstance(record, dict) or set(record) - FIELDS or not {"name", "part", "column", "inputs", "grain", "sql",
                                                                        "assumptions", "tests", "path"} <= set(record) \
                or record["name"] != name or not isinstance(record["assumptions"], list) or not isinstance(record["tests"], list):
            raise rolemap.MapError(rolemap.WORDING["map_shape"].format(
                where=f"{where}, normalisation {name}", problem="a normalisation holds its name, part, column, inputs, "
                                                                "grain, SQL, assumptions, tests and route"))
    used = set()
    for view, whole in data.get("roles", {}).items():
      for pathway, role in ([(None, whole)] + [(p.get("name"), p) for p in whole.get("pathways") or [] if isinstance(p, dict)]
                            if isinstance(whole, dict) else []):
        for column, item in (role.get("columns") or {}).items():
            binding = item.get("binding")
            if not isinstance(binding, dict) or "normalisation" not in binding:
                continue
            record = held.get(binding["normalisation"])
            if record is None or record["part"] != view or record["column"] != column or record.get("pathway") != pathway:
                raise rolemap.MapError(rolemap.WORDING["map_shape"].format(
                    where=f"{where}, {view}.{column}", problem="a binding names a normalisation of its own part and column"))
            resolved = {k: v for k, v in binding.items() if k != "normalisation"}
            resolved["path"] = copy.deepcopy(record["path"])
            if record.get("joined"):
                resolved["joined"] = copy.deepcopy(record["joined"])
            item["binding"] = resolved
            used.add(binding["normalisation"])
    if set(held) - used:
        raise rolemap.MapError(rolemap.WORDING["map_shape"].format(
            where=where, problem="every normalisation is named by the binding it serves"))
    return data
