"""Corrections: the richer bindings that a person makes on screen 1 without writing SQL, and the report of the check
that tests each one on invented rows before it is kept. The check itself runs on the role shadow (roleshadow.py), which
is layer 4's, and this module keeps the forms, how each is written into the map, and the report of what the check found.

A binding in map.json was "this table, this column", reached from the view's own table by a path of joins on one
column each. A correction can now say more, each in a form with plain words on the page:

    column      another column, reached by the joins that the dictionary shows
    derived     a value worked out from a column: a flag that is 1 when the column holds one of a list of values, a
                number multiplied by a factor with an offset added (a change of unit), the date of a date and time, or
                text with the spaces at either end removed
    filter      only the rows of the role whose column holds one of a list of values
    path        a link through one, two or three tables, each step a join of one column to another
    pair        a link whose join matches two columns at once
    joined      several rows joined back in order into one text, as the lines of a note
    codes       the local codes of a kind column, translated to the role's kinds

In map.json each is written into the binding as data, never as SQL:

    {"table", "column", "path", "data_type"}                 as before; a step of a path is [A, a, B, b], or
                                                             [A, a, B, b, [[a2, b2], ...]] where it joins on more pairs
    + "derive": {"form": "flag", "values": [...]}            or {"form": "scale", "factor": f, "offset": o},
                                                             {"form": "date"} or {"form": "trim"}
    + "joined": {"table", "link", "text", "order",           where table, column and path name the column of the
                 "separator"}                                view's own row that the joined rows carry
    rows binding + "filter": [{"table", "column", "path", "values"}]

The filter has one rule, which the page states beside its form. A filter on a code that the source stores to say
what kind of record a row is, such as the type of a line of a shared table of events or a flag that marks a row as
deleted, interprets the vendor's storage and is a normalisation, which the hospital schema may hold. A filter by
clinical meaning, such as keeping only the drugs of one class or only the anaesthetics of one specialty, is a decision
of a question, which the contract reserves for the public logic over the roles, and the hospital schema does not hold
it. Nor does it attribute a row to an anaesthetic by a time window: a row whose source carries only its stay keeps its
stay and an empty anaesthetic key, and a question attributes it. The window form that once did so is gone, and a saved
binding that still holds a window is refused when the map is read.

A part that records events may be reached by several pathways, each a binding of its own with its own source kind
(rolemap.pathways). The check places the invented rows of such a part through every pathway, and expects the union.

propose.view_sql writes the SQL of each form, and propose.plan describes what it does with a column, so that the
check's own model of a binding (roleshadow.evaluate) reads the same description as the SQL writer.

The check (roleshadow.run_check) builds a shadow from the map: invented rows of every role, the planted neonates among them, written into
tables shaped as the map's bindings name them, so that each view should give back what its bindings describe. It then
compiles every view and checks it against the contract, runs it, compares what it gives with what the model of its
bindings says it should give, checks the rules of the record that a query can check (a key identifies one row, a flag
is never empty, a reading links to exactly one anaesthetic), runs the standard counts, and runs the neonatal audit
through the compiled views and compares each planted case with its expected answer. Nothing in it comes from any
hospital: the rows are invented there, and the tables and columns are only the names that the map gives.
"""
import hashlib
import json
import re

from . import propose, rolemap
from .catalogue import NAME
from .evidence import PERSON

FORMS = ("column", "rows", "derived", "filter", "path", "pair", "joined", "codes")
DERIVED = ("flag", "scale", "date", "trim")
# Which derived forms suit which types of the role model.
DERIVED_TYPES = {"flag": ("flag", "flag_or_empty"), "scale": ("number", "whole"), "date": ("date",),
                 "trim": ("text", "kind", "key")}
MOST_STEPS = 3
MOST_VALUES = 50
# The codes that the shadow gives a kind whose local codes are not yet chosen, and the code of a reading of no kind
# the map translates. Each is a number written as text, so that it suits a column of either type.
OTHER_CODE = "9899"
NOT_IN_LIST = "9898"
SHADOW_ANAESTHETICS = 80
SHADOW_SEED = 7
FURTHER_ANAESTHETICS = 30
ENCOUNTER_PAIRS = 6
# The shadow holds a few pairs of anaesthetics of one patient, a few hours apart, that share every join column of the
# anaesthetic's own table, as two anaesthetics in one admission share their encounter, so that a link by such a column
# alone is seen to repeat rows. The pairs are the same whatever the map binds, so that a change is never blamed for
# what the map already did.
THING = {"role_reading": "reading", "role_event": "event", "role_drug": "drug given", "role_device": "device",
         "role_staff": "person present", "role_fluid": "fluid given", "role_note": "note", "role_operation": "operation",
         "role_finding": "finding", "role_anaesthetic_detail": "detail of an anaesthetic"}

WORDING = {
    "unknown_form": "Schemalyser does not know a correction of the kind {form}.",
    "unknown_about": "The hospital schema holds nothing named {about}.",
    "not_drafted": "The hospital schema does not yet hold {view}, so it cannot be corrected here.",
    "not_a_name": "Please write the replacement as TABLE.COLUMN, such as the name of a table, a full stop and the name of one of its columns.",
    "no_dictionary": "Please load the dictionary in step 2 first, because every table and column of a correction must be in it.",
    "not_found": "The dictionary holds no column {name}.",
    "no_table": "The dictionary holds no table {name}.",
    "not_in_result": "The result of the tables and columns query holds no column {name}.",
    "unreachable": "The table that holds this part does not reach {table} by any link that the dictionary shows. To name the links, the database analyst chooses A link through other tables.",
    "derive_type": "{about} is {type_words}, and a value worked out as {form_words} does not suit it.",
    "values": "Please give at least one value, and at most {most}.",
    "factor": "Please give the factor as a number other than nought, and the offset as a number.",
    "steps": "Please give from one to {most} steps, each joining a column of the table before to a column of the next table.",
    "step_start": "The first link starts from the table that holds this part, {table}.",
    "step_chain": "Step {number} starts from {table}, the table that step {before} reaches.",
    "pair_needed": "A join on two columns needs a second pair of columns in its step.",
    "not_a_link": "{about} is not a link to another part of the record, so it cannot be a link through other tables.",
    "joined_type": "Rows joined into one text suit a column of text, and {about} is {type_words}.",
    "joined_fields": "Please name the table of the rows, the column that links each to this row, the column of text and the column that gives their order.",
    "separator": "Please give a separator of at most ten characters, with no line break.",
    "filter_rows": "A filter applies to the rows of a part, so please choose it on the row for the table, at the head of the part.",
    "codes_kind": "A translation of codes suits a column that holds a kind, and {about} is {type_words}.",
    "codes_unknown": "{kind} is not one of the kinds the page knows for this column.",
    # The sentences that each form means.
    "say_column": "{column} is {source}{how}.",
    "say_flag": "{column} is 1 where {source}{how} holds {values}, and 0 where it holds anything else or is empty.",
    "say_flag_or_empty": "{column} is 1 where {source}{how} holds {values}, 0 where it holds anything else, and empty where it is empty.",
    "say_scale": "{column} is {source}{how} multiplied by {factor}{offset}.",
    "say_offset_plus": ", with {offset} added",
    "say_offset_minus": ", with {offset} taken away",
    "say_date": "{column} is the date of {source}, without its time{how}.",
    "say_trim": "{column} is {source} with any spaces at either end removed{how}.",
    "say_filter": "The rows of {view} are only those in which {source} holds {values}{how}.",
    "say_rows": "The rows of {view} come from {table}, one row for each {what}, and the page proposes every column of this part again from that table.",
    "say_path": "{column} is {source}, which {base} reaches {steps}.",
    "say_joined": "{column} is every {text} of the rows of {table} whose {link} matches {on}, joined in the order of {order} with {separator} between them.",
    "say_codes": "The local {codes} of {source} {are} translated as follows: {pairs}. Every other code is other.",
    "say_how": ", reached {path}",
    "say_steps": "through {steps}",
    "say_step": "{table}, matching {pairs}",
    "space": "a space",
    "nothing": "nothing",
    # The test on made-up rows.
    "check_whole": "This change keeps the hospital schema whole: all {views} of the record run on made-up rows and give the rows they should, every identifying column is unique, every flag is filled, and the invented newborns of the test audit give the expected answer.",
    "check_whole_now": "The hospital schema as it stands is whole: all {views} of the record run on made-up rows and give the rows they should, every identifying column is unique, every flag is filled, and the invented newborns of the test audit give the expected answer.",
    "check_breaks": "This change breaks the hospital schema in {count}:",
    "check_kept_old": "This change breaks nothing that held before it. {count} {were} there before it and {remain}:",
    "check_mends": "This change also mends {count} that {were} there before it.",
    "check_now_breaks": "The hospital schema as it stands has {count}:",
    "placeholders": "The local codes of {kinds} are not chosen yet, so the test on made-up rows gives {them}.",
    "orphans": "In {view}, {count} made-up rows link to an anaesthetic that Anaesthetics does not hold, which can be right where Anaesthetics leaves some anaesthetics out.",
    "contract": "In {view}, {problem}",
    "runs_not": "In {view}, the page could not run this SQL on made-up rows: {error}.",
    "doubled": "In {view}, {count} made-up rows appear more than once, each under a second value of {link}, so a row is linked to more than one {target}.",
    "doubled_reading": "In {view}, {count} made-up readings appear twice, each linked to a second anaesthetic, so a reading no longer links to exactly one anaesthetic.",
    "missing": "In {view}, {count} of the {total} made-up rows that its columns should give are missing.",
    "extra": "In {view}, {count} made-up rows appear that its columns should not give.",
    "keys": "In {view}, {count} values of the column that identifies a row appear more than once, so that column no longer identifies one row.",
    "flag_empty": "{subject} is empty in {count}, and a flag of 1 or 0 is never empty.",
    "type": "{subject} gives {found} where this column should hold {wanted}.",
    "count_fails": "The count of {name}, one of the counts that the page runs on every hospital schema to test it, could not run on made-up rows.",
    "neonatal_missing": "The test audit, which counts minutes of low mean pressure in invented newborns, cannot run, because the hospital schema does not yet hold {views}.",
    "neonatal_fails": "The page could not run the test audit, which counts minutes of low mean pressure in invented newborns, on made-up rows: {error}.",
    "neonatal_minutes": "The invented newborn {key} gives {found} minutes below 40, where {wanted} are expected.",
    "neonatal_died": "The invented newborn {key} gives {found} for a death within 90 days, where {wanted} is expected.",
    "neonatal_absent": "The invented newborn {key} is not counted, although the test audit should count it.",
    "neonatal_present": "The invented newborn {key} is counted, although the test audit should leave it out.",
    "neonatal_more": "{count} further invented newborns give an answer other than the expected one.",
}
TYPE_WORDS = {"key": "a key", "date": "a date", "datetime": "a date and time", "number": "a number", "whole": "a whole number",
              "flag": "a flag of 1 or 0", "flag_or_empty": "a flag of 1, 0 or empty", "kind": "a kind", "text": "text"}
FORM_WORDS = {"flag": "a flag", "scale": "a number in another unit", "date": "a date", "trim": "trimmed text"}


class CorrectionError(ValueError):
    """A correction cannot be used. The message is a sentence for the page and never quotes the dictionary."""


def _plural(n, one, many):
    return f"{n:,} {one if n == 1 else many}"


def _and(items, word="and"):
    items = [str(i) for i in items]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {word} " + items[-1]


def _clean(text, limit=60):
    """A value as it may stand in a sentence of the map: on one line, with no question or exclamation mark."""
    text = " ".join(str(text).split()).replace("?", ".").replace("!", ".").replace("$(", "$ (")
    return text[:limit]


def _fit(sentence):
    sentence = " ".join(sentence.split())
    if len(sentence) > 400:
        sentence = sentence[:396].rsplit(" ", 1)[0].rstrip(" ,;:.") + " ..."
    if not sentence.endswith("."):
        sentence += "."
    return sentence


# The tables and columns that a binding names.

def named_columns(binding):
    """Every (table, column) that a binding names, in order: its column, the columns of each join, and the columns of
    its joined rows or its filters."""
    if not binding:
        return []
    found = []
    for step in binding.get("path") or []:
        for a, b in propose.step_pairs(step):
            found += [(step[0], a), (step[2], b)]
    if binding.get("column"):
        derive = binding.get("derive") or {}
        if derive.get("form") == "key":
            found += [(binding["table"], other) for other in derive.get("with") or []]
        found.append((binding["table"], binding["column"]))
    joined = binding.get("joined")
    if joined:
        found += [(joined["table"], joined[k]) for k in ("link", "text", "order")]
    for item in binding.get("filter") or []:
        for step in item.get("path") or []:
            for a, b in propose.step_pairs(step):
                found += [(step[0], a), (step[2], b)]
        found.append((item["table"], item["column"]))
    return list(dict.fromkeys(found))


def named_tables(binding):
    tables = [binding["table"]] if binding and binding.get("table") else []
    return list(dict.fromkeys(tables + [t for t, _ in named_columns(binding)]))


def check_shape(binding, where):
    """Raises rolemap.MapError unless a binding's further forms have the shape that this module writes."""
    def bad(problem):
        raise rolemap.MapError(rolemap.WORDING["map_shape"].format(where=where, problem=problem))

    def names(*values):
        if not all(isinstance(v, str) and NAME.match(v) for v in values):
            bad("each table and column of a binding is a plain name")

    def path_of(path):
        if not isinstance(path, list) or len(path) > MOST_STEPS + 2:
            bad("a path is a list of steps")
        for step in path:
            if not isinstance(step, list) or len(step) not in (4, 5):
                bad("a step of a path is [table, column, table, column] with, optionally, further pairs of columns")
            names(*step[:4])
            for pair in (step[4] if len(step) > 4 else []):
                if not isinstance(pair, list) or len(pair) != 2:
                    bad("a further pair of a step is [column, column]")
                names(*pair)

    if binding is None:
        return
    if not isinstance(binding, dict) or "table" not in binding:
        bad("a binding names its table")
    names(binding["table"])
    if "path" in binding:
        path_of(binding["path"])
    derive = binding.get("derive")
    if derive is not None:
        if isinstance(derive, dict) and derive.get("form") == "key":
            # A key made from several columns of the binding's own table, which the proposer writes and no form offers.
            if not isinstance(derive.get("with"), list) or not derive["with"]:
                bad("a key made from several columns lists the columns besides its own")
            names(*derive["with"])
        elif not isinstance(derive, dict) or derive.get("form") not in DERIVED:
            bad(f"a derived value is one of {', '.join(DERIVED)}")
        if derive["form"] == "flag" and (not isinstance(derive.get("values"), list) or not derive["values"]):
            bad("a derived flag lists its values")
        if derive["form"] == "scale" and not all(isinstance(derive.get(k, 0), (int, float)) for k in ("factor", "offset")):
            bad("a change of unit gives its factor and offset as numbers")
    if "window" in binding:
        bad("a binding never attributes a row to an anaesthetic by a time window; a row whose source carries only its "
            "stay keeps the stay and an empty anaesthetic key, and a question attributes it")
    joined = binding.get("joined")
    if joined is not None:
        if not isinstance(joined, dict) or not {"table", "link", "text", "order"} <= set(joined):
            bad("joined rows name their table, link, text and order")
        names(joined["table"], joined["link"], joined["text"], joined["order"])
    for item in binding.get("filter") or []:
        if not isinstance(item, dict) or not {"table", "column", "path", "values"} <= set(item) or not item["values"]:
            bad("a filter names its table, column, path and values")
        names(item["table"], item["column"])
        path_of(item["path"])


# Building a correction into a binding.

def _about(state, about):
    rows_of = re.fullmatch(r"(role_\w+) rows", about or "")
    column_of = re.fullmatch(r"(role_\w+)\.(\w+)", about or "")
    view_name = (rows_of or column_of).group(1) if (rows_of or column_of) else None
    if view_name not in state.views:
        raise CorrectionError(WORDING["unknown_about"].format(about=about))
    role = (state.data or {}).get("roles", {}).get(view_name)
    if role is None or not role["rows"].get("binding"):
        raise CorrectionError(WORDING["not_drafted"].format(view=rolemap.view_title(view_name, False)))
    spec = state.views[view_name]
    column = None
    if column_of:
        column = next((c for c in spec["columns"] if c["name"] == column_of.group(2)), None)
        if column is None:
            raise CorrectionError(WORDING["unknown_about"].format(about=about))
    return view_name, spec, role, column


def _column(state, table, column):
    """A table and column in the dictionary's spelling, with its data type, which must also be in the result of the
    tables and columns query once that is pasted."""
    if state.dictionary is None:
        raise CorrectionError(WORDING["no_dictionary"])
    if not (isinstance(table, str) and NAME.match(table)):
        raise CorrectionError(WORDING["no_table"].format(name=_clean(table or "")))
    held = state.dictionary.table(table)
    if held is None:
        raise CorrectionError(WORDING["no_table"].format(name=table))
    if not (isinstance(column, str) and NAME.match(column)) or held.column(column) is None:
        raise CorrectionError(WORDING["not_found"].format(name=f"{held.name}.{_clean(column or '')}"))
    entry = held.column(column)
    if state.catalogue is not None:
        known = state.catalogue.table(held.name)
        if known is None or known.column(entry.name) is None:
            raise CorrectionError(WORDING["not_in_result"].format(name=f"{held.name}.{entry.name}"))
    return held.name, entry.name, entry.data_type or ""


def _table(state, table):
    if state.dictionary is None:
        raise CorrectionError(WORDING["no_dictionary"])
    held = state.dictionary.table(table) if isinstance(table, str) and NAME.match(table) else None
    if held is None:
        raise CorrectionError(WORDING["no_table"].format(name=_clean(table or "")))
    return held.name


def _graph(state):
    if state.proposer is not None:
        return state.proposer.graph
    if getattr(state, "_graph", None) is None:
        state._graph = propose._Graph(state.dictionary)
    return state._graph


def path_to(state, role, table):
    """The joins by which a view's table reaches another table: those that the view already makes, or else the
    shortest that the dictionary's keys show."""
    known = propose._known_paths(role)
    if table.upper() in known:
        return [list(s) for s in known[table.upper()]]
    reach = _graph(state).reach(role["rows"]["binding"]["table"])
    if table.upper() not in reach:
        raise CorrectionError(WORDING["unreachable"].format(table=table))
    return [list(s) for s in reach[table.upper()][1]]


def _steps(state, base, steps, pair=False):
    """The steps that a person chose, as a path, each of whose tables and columns is in the dictionary."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MOST_STEPS:
        raise CorrectionError(WORDING["steps"].format(most=MOST_STEPS))
    path, at = [], base
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise CorrectionError(WORDING["steps"].format(most=MOST_STEPS))
        start = step.get("start") or at
        if start.upper() != at.upper():
            raise CorrectionError(WORDING["step_start"].format(table=base) if number == 1 else
                                  WORDING["step_chain"].format(number=number, table=at, before=number - 1))
        a_table, a, _ = _column(state, at, step.get("from"))
        b_table, b, _ = _column(state, step.get("table"), step.get("to"))
        item = [a_table, a, b_table, b]
        also = []
        for extra in step.get("also") or []:
            if not isinstance(extra, (list, tuple)) or len(extra) != 2 or not all(extra):
                continue
            _, a2, _ = _column(state, a_table, extra[0])
            _, b2, _ = _column(state, b_table, extra[1])
            also.append([a2, b2])
        if also:
            item.append(also)
        path.append(item)
        at = b_table
    if pair and not any(len(s) > 4 for s in path):
        raise CorrectionError(WORDING["pair_needed"])
    return path


def _replacement(text):
    """A replacement written as TABLE.COLUMN, optionally with its links, as the page lists an alternative:
    "T.C, by A.a = B.b, then B.c = C.d" or "T.C via A.a = B.b". Returns (table, column, steps)."""
    match = re.fullmatch(r"\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*(?:(?:,\s*by|via)\s+(.+))?", str(text or ""))
    if not match:
        raise CorrectionError(WORDING["not_a_name"])
    table, column, joins = match.groups()
    steps = []
    for part in re.split(r",\s*then\s+", (joins or "").strip()) if joins else []:
        step = re.fullmatch(r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", part.strip())
        if not step:
            return table, column, None
        steps.append({"start": step.group(1), "from": step.group(2), "table": step.group(3), "to": step.group(4)})
    return table, column, steps


def _values(values):
    if not isinstance(values, list):
        raise CorrectionError(WORDING["values"].format(most=MOST_VALUES))
    found = list(dict.fromkeys(" ".join(str(v).split()) for v in values if str(v).strip()))
    if not 1 <= len(found) <= MOST_VALUES or any(len(v) > 254 for v in found):
        raise CorrectionError(WORDING["values"].format(most=MOST_VALUES))
    return found


def _how(path, middle=False):
    if not path:
        return ""
    return WORDING["say_how"].format(path=propose._path_text(path)) + ("," if middle else "")


def _shown_values(values):
    shown = [_clean(v, 40) for v in values[:8]]
    if len(values) > 8:
        shown.append(f"{len(values) - 8} more")
    return _and(shown, "or")


def build(state, correction):
    """A correction checked and written as data: {"about", "view", "column", "form", "binding" | "filter" | "chosen",
    "sentence", "from"}. Raises CorrectionError with a sentence for the page."""
    if not isinstance(correction, dict) or correction.get("form") not in FORMS:
        raise CorrectionError(WORDING["unknown_form"].format(form=_clean((correction or {}).get("form", ""))))
    form = correction["form"]
    about = correction.get("about", "")
    if form == "rows":
        rows_of = re.fullmatch(r"(role_\w+) rows", about or "")
        if not rows_of or rows_of.group(1) not in state.views:
            raise CorrectionError(WORDING["unknown_about"].format(about=about))
        view_name = rows_of.group(1)
        given = correction.get("table") or str(correction.get("replacement") or "").split(".")[0].split(",")[0].strip()
        table = _table(state, given)
        return {"about": about, "view": view_name, "column": None, "form": "rows", "correction": correction, "table": table,
                "source": table, "sentence": _fit(WORDING["say_rows"].format(
                    view=rolemap.view_title(view_name, False), table=table, what=state.views[view_name]["one_row_per"]))}
    view_name, spec, role, column = _about(state, about)
    base = role["rows"]["binding"]["table"]
    links = {link["column"]: link["to"] for link in spec.get("links", [])}
    built = {"about": about, "view": view_name, "column": column["name"] if column else None, "form": form,
             "correction": correction}
    if form == "filter":
        if column is not None:
            raise CorrectionError(WORDING["filter_rows"])
        table, name, data_type = _column(state, correction.get("table"), correction.get("column"))
        path = path_to(state, role, table)
        values = _values(correction.get("values"))
        item = {"table": table, "column": name, "path": path, "values": values}
        built.update(filter=item, sentence=_fit(WORDING["say_filter"].format(
            view=rolemap.view_title(view_name, False), source=f"{table}.{name}", values=_shown_values(values), how=_how(path))),
            source=f"{table}.{name}")
        return built
    if column is None:
        raise CorrectionError(WORDING["filter_rows"] if form != "column" else WORDING["unknown_about"].format(about=about))
    type_words = TYPE_WORDS[column["type"]]
    subject = rolemap.plain_about(about, True)
    if form == "codes":
        if column["type"] != "kind":
            raise CorrectionError(WORDING["codes_kind"].format(about=subject, type_words=type_words))
        entry = state._vocabulary(about)
        chosen = {}
        for code, kind in (correction.get("chosen") or {}).items():
            code = " ".join(str(code).split())
            if not code:
                continue
            if kind not in entry["kinds"] or kind == "other":
                raise CorrectionError(WORDING["codes_unknown"].format(kind=_clean(kind)))
            chosen[code[:100]] = kind
        if not chosen:
            raise CorrectionError(WORDING["values"].format(most=MOST_VALUES))
        meanings = entry.get("meanings") or {}
        pairs = [f"{_clean(c, 30)} to {_meaning(meanings.get(k), k)}" for c, k in list(chosen.items())[:10]]
        built.update(chosen=chosen, source=entry["bound"], sentence=_fit(WORDING["say_codes"].format(
            codes="code" if len(chosen) == 1 else "codes", source=entry["bound"] or about, are="is" if len(chosen) == 1 else "are",
            pairs=_and(pairs))))
        return built
    if form in ("column", "derived"):
        path = None
        if form == "column" and correction.get("replacement"):
            table, name, steps = _replacement(correction["replacement"])
            table, name, data_type = _column(state, table, name)
            if steps and len(steps) <= MOST_STEPS:
                try:
                    path = _steps(state, base, steps)
                except CorrectionError:
                    path = None
                if path and path[-1][2].upper() != table.upper():
                    path = None
        else:
            table, name, data_type = _column(state, correction.get("table"), correction.get("column"))
        path = path if path is not None else path_to(state, role, table)
        binding = {"table": table, "column": name, "path": path, "data_type": data_type}
        source = f"{table}.{name}"
        if form == "column":
            sentence = WORDING["say_column"].format(column=subject, source=source, how=_how(path))
        else:
            derive = dict(correction.get("derive") or {})
            kind = derive.get("form")
            if kind not in DERIVED or column["type"] not in DERIVED_TYPES[kind]:
                raise CorrectionError(WORDING["derive_type"].format(about=subject, type_words=type_words,
                                                                    form_words=FORM_WORDS.get(kind, "that")))
            if kind == "flag":
                values = _values(derive.get("values"))
                binding["derive"] = {"form": "flag", "values": values}
                sentence = WORDING["say_flag_or_empty" if column["type"] == "flag_or_empty" else "say_flag"].format(
                    column=subject, source=source, values=_shown_values(values), how=_how(path, True))
            elif kind == "scale":
                try:
                    factor, offset = float(derive.get("factor")), float(derive.get("offset") or 0)
                except (TypeError, ValueError):
                    raise CorrectionError(WORDING["factor"]) from None
                if not factor or factor != factor or abs(factor) > 1e9 or abs(offset) > 1e9:
                    raise CorrectionError(WORDING["factor"])
                binding["derive"] = {"form": "scale", "factor": factor, "offset": offset}
                shown_offset = "" if not offset else WORDING["say_offset_plus" if offset > 0 else "say_offset_minus"].format(
                    offset=propose._number_text(abs(offset)))
                sentence = WORDING["say_scale"].format(column=subject, source=source,
                                                       factor=propose._number_text(factor), offset=shown_offset, how=_how(path, True))
            else:
                binding["derive"] = {"form": kind}
                sentence = WORDING[f"say_{kind}"].format(column=subject, source=source, how=_how(path))
        built.update(binding=binding, sentence=_fit(sentence), source=propose._from_text(binding))
        return built
    if form in ("path", "pair"):
        if column["name"] not in links and column["type"] != "key":
            raise CorrectionError(WORDING["not_a_link"].format(about=subject))
        path = _steps(state, base, correction.get("steps"), pair=form == "pair")
        table, name, data_type = _column(state, path[-1][2], correction.get("column"))
        binding = {"table": table, "column": name, "path": path, "data_type": data_type}
        steps = WORDING["say_steps"].format(steps=", then ".join(
            WORDING["say_step"].format(table=s[2], pairs=propose._match_text(s)) for s in path))
        built.update(binding=binding, source=propose._from_text(binding), sentence=_fit(WORDING["say_path"].format(
            column=subject, source=f"{table}.{name}", base=base, steps=steps)))
        return built
    if form == "joined":
        if column["type"] != "text":
            raise CorrectionError(WORDING["joined_type"].format(about=subject, type_words=type_words))
        if not all(correction.get(k) for k in ("on_table", "on_column", "table", "link", "text", "order")):
            raise CorrectionError(WORDING["joined_fields"])
        on_table, on_column, data_type = _column(state, correction["on_table"], correction["on_column"])
        path = path_to(state, role, on_table)
        x_table = _table(state, correction["table"])
        joined = {"table": x_table}
        for wanted in ("link", "text", "order"):
            joined[wanted] = _column(state, x_table, correction[wanted])[1]
        separator = correction.get("separator", " ")
        if not isinstance(separator, str) or len(separator) > 10 or "\n" in separator or "\r" in separator:
            raise CorrectionError(WORDING["separator"])
        joined["separator"] = separator
        binding = {"table": on_table, "column": on_column, "path": path, "data_type": data_type, "joined": joined}
        shown = WORDING["space"] if separator == " " else WORDING["nothing"] if separator == "" else f"'{_clean(separator, 10)}'"
        built.update(binding=binding, source=f"{x_table}.{joined['text']}", sentence=_fit(WORDING["say_joined"].format(
            column=subject, text=joined["text"], table=x_table, link=joined["link"], on=f"{on_table}.{on_column}",
            order=joined["order"], separator=shown)))
        return built
    raise CorrectionError(WORDING["unknown_form"].format(form=form))


def apply(state, built, record):
    """Writes a built correction into the map that state holds, with the person's record of it."""
    if built["form"] == "rows":
        state.repropose(built["view"], built["table"], record.get("date"))
        item = state.data["roles"][built["view"]]["rows"]
        item["says"] = built["sentence"]
        item["confirmation"] = record
        item["provenance"] = PERSON
        return
    role = state.data["roles"][built["view"]]
    if built["form"] == "codes":
        held = dict((state.codes.get(built["about"]) or {}).get("chosen") or {})
        held.update(built["chosen"])
        state.choose_codes(built["about"], held, record.get("date"), actor=record.get("by"))
        item = role["columns"][built["column"]]
        item["says"] = built["sentence"]
        item["status"] = "person"
        item.pop("question", None)
        item["confirmation"] = record
        item["provenance"] = PERSON
        return
    if built["form"] == "filter":
        binding = role["rows"]["binding"]
        kept = [f for f in binding.get("filter") or []
                if (f["table"].upper(), f["column"].upper()) != (built["filter"]["table"].upper(), built["filter"]["column"].upper())]
        binding["filter"] = kept + [built["filter"]]
        item = role["rows"]
    else:
        item = role["columns"][built["column"]]
        item["binding"] = built["binding"]
        item["from"] = built["source"]
        item["says"] = built["sentence"]
    item["status"] = "person"
    item.pop("question", None)
    item["confirmation"] = record
    item["provenance"] = PERSON


# The report of a check, from what the role shadow (roleshadow.run_check) found.

def _meaning(meaning, kind):
    """A kind in plain words with its code after it, as "a heart rate, in beats a minute (heart_rate)"."""
    if not meaning:
        return kind
    text = meaning.rstrip(". ")
    return f"{text[:1].lower()}{text[1:]} ({kind})"


def _views_text(count):
    return _plural(count, "part", "parts")


def _problems_text(count):
    return _plural(count, "problem", "problems")


def fingerprint(state):
    return hashlib.sha256(json.dumps([state.data, state.codes], sort_keys=True, default=str).encode("utf-8")).hexdigest()


def report(before, after, change=True):
    """The plain report of a check, comparing the map with the change against the map as it stands."""
    new = [p for p in after["problems"] if p not in before["problems"]]
    old = [p for p in after["problems"] if p in before["problems"]]
    mended = [p for p in before["problems"] if p not in after["problems"]]
    passed = not new
    if not change:
        sentence = WORDING["check_whole_now"].format(views=_views_text(after["views"])) if not after["problems"] \
            else WORDING["check_now_breaks"].format(count=_problems_text(len(after["problems"])))
        return {"passed": not after["problems"], "sentence": sentence, "problems": after["problems"], "remaining": [],
                "mended": "", "notes": after["notes"], "seconds": after["seconds"], "views": after["views"],
                "about": dict(after.get("about") or {})}
    if new:
        sentence = WORDING["check_breaks"].format(count=_plural(len(new), "place", "places"))
    elif old:
        sentence = WORDING["check_kept_old"].format(count=_problems_text(len(old)), were="was" if len(old) == 1 else "were",
                                                    remain="remains" if len(old) == 1 else "remain")
    else:
        sentence = WORDING["check_whole"].format(views=_views_text(after["views"]))
    mends = WORDING["check_mends"].format(count=_problems_text(len(mended)), were="was" if len(mended) == 1 else "were") if mended else ""
    return {"passed": passed, "sentence": sentence, "problems": new, "remaining": old if new or old else [],
            "mended": mends, "notes": after["notes"], "seconds": round(before.get("seconds", 0) + after["seconds"], 1),
            "views": after["views"], "about": {**(before.get("about") or {}), **(after.get("about") or {})}}


def outcome(found):
    """The outcome of a check as it is recorded with a confirmation: passed, or failed with the first problem."""
    if found["passed"]:
        # A change that breaks nothing new still leaves any problem that was there before it, and the record says so.
        old = len(found.get("remaining") or [])
        if old:
            return f"passed: broke nothing new; {_problems_text(old)} {'was' if old == 1 else 'were'} there before it and {'remains' if old == 1 else 'remain'}"
        return "passed"
    return "failed: " + " ".join(found["problems"][:2])[:300]
