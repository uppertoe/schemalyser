"""Corrections: the richer bindings that a person makes on screen 1 without writing SQL, and the check that tests each
one on invented rows before it is kept.

A binding in map.json was "this table, this column", reached from the view's own table by a path of joins on one
column each. A correction can now say more, each in a form with plain words on the page:

    column      another column, reached by the joins that the dictionary shows
    derived     a value worked out from a column: a flag that is 1 when the column holds one of a list of values, a
                number multiplied by a factor with an offset added (a change of unit), the date of a date and time, or
                text with the spaces at either end removed
    filter      only the rows of the role whose column holds one of a list of values
    path        a link through one, two or three tables, each step a join of one column to another
    pair        a link whose join matches two columns at once
    window      a link by the key that a row shares with its anaesthetic, and also by a time window around the
                anaesthetic's start and stop, with a margin in minutes before and after (rule 3 of the record requires
                the shared key, so a window without one is refused)
    joined      several rows joined back in order into one text, as the lines of a note
    codes       the local codes of a kind column, translated to the role's kinds

In map.json each is written into the binding as data, never as SQL:

    {"table", "column", "path", "data_type"}                 as before; a step of a path is [A, a, B, b], or
                                                             [A, a, B, b, [[a2, b2], ...]] where it joins on more pairs
    + "derive": {"form": "flag", "values": [...]}            or {"form": "scale", "factor": f, "offset": o},
                                                             {"form": "date"} or {"form": "trim"}
    + "window": {"table", "key", "output", "start", "stop",  where table, column and path name the row's side of the
                 "time", "before", "after"}                  shared key, and window names the anaesthetic's table
    + "joined": {"table", "link", "text", "order",           where table, column and path name the column of the
                 "separator"}                                view's own row that the joined rows carry
    rows binding + "filter": [{"table", "column", "path", "values"}]

propose.view_sql writes the SQL of each form, and propose.plan describes what it does with a column, so that the
check's own model of a binding (evaluate, below) reads the same description as the SQL writer.

The check builds a shadow from the map: invented rows of every role, the planted neonates among them, written into
tables shaped as the map's bindings name them, so that each view should give back what its bindings describe. It then
compiles every view and checks it against the contract, runs it, compares what it gives with what the model of its
bindings says it should give, checks the rules of the record that a query can check (a key identifies one row, a flag
is never empty, a reading links to exactly one anaesthetic), runs the standard counts, and runs the neonatal audit
through the compiled views and compares each planted case with its expected answer. Nothing in it comes from any
hospital: the rows are invented here, and the tables and columns are only the names that the map gives.
"""
import copy
import datetime as dt
import hashlib
import json
import random
import re
import time
from collections import Counter

from . import propose, rolemap
from .catalogue import NAME

FORMS = ("column", "derived", "filter", "path", "pair", "window", "joined", "codes")
DERIVED = ("flag", "scale", "date", "trim")
# Which derived forms suit which types of the role model.
DERIVED_TYPES = {"flag": ("flag", "flag_or_empty"), "scale": ("number", "whole"), "date": ("date",),
                 "trim": ("text", "kind", "key")}
MOST_STEPS = 3
MOST_VALUES = 50
MOST_MARGIN = 24 * 60
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
    "unknown_about": "The map holds nothing named {about}.",
    "not_drafted": "The map does not yet hold {view}, so it cannot be corrected here.",
    "no_dictionary": "Please load the dictionary in step 2 first, because every table and column of a correction must be in it.",
    "not_found": "The dictionary holds no column {name}.",
    "no_table": "The dictionary holds no table {name}.",
    "not_in_result": "The result of the tables and columns query holds no column {name}.",
    "unreachable": "This part's own table does not reach {table} by any link that the dictionary shows. To name the links yourself, choose A link through other tables.",
    "derive_type": "{about} is {type_words}, and a value worked out as {form_words} does not suit it.",
    "values": "Please give at least one value, and at most {most}.",
    "factor": "Please give the factor as a number other than nought, and the offset as a number.",
    "steps": "Please give from one to {most} steps, each joining a column of the table before to a column of the next table.",
    "step_start": "The first link starts from this part's own table, {table}.",
    "step_chain": "Step {number} starts from {table}, the table that step {before} reaches.",
    "pair_needed": "A join on two columns needs a second pair of columns in its step.",
    "not_a_link": "{about} is not a link to another role, so it cannot be a link through other tables.",
    "window_link": "A time window links a row to its anaesthetic, so it applies only to a link to role_anaesthetic.anaesthetic_key.",
    "window_key": "Rule 3 of the record says that a row belongs to an anaesthetic by its link to that anaesthetic, and never because its time falls within the anaesthetic, so a time window always needs the key that the row shares with its anaesthetic as well. Please choose that key on both sides.",
    "window_time": "The window reads the time of each row from {column}, so please bind {column} first.",
    "window_anaesthetic": "Please name the anaesthetic's table and its columns that hold the key, the start and the stop.",
    "margin": "Please give each margin as a whole number of minutes from 0 to {most}.",
    "joined_type": "Rows joined into one text suit a column of text, and {about} is {type_words}.",
    "joined_fields": "Please name the table of the rows, the column that links each to this row, the column of text and the column that gives their order.",
    "separator": "Please give a separator of at most ten characters, with no line break.",
    "filter_rows": "A filter applies to the rows of a role, so please choose it under One row of the role.",
    "codes_kind": "A translation of codes suits a column that holds a kind, and {about} is {type_words}.",
    "codes_unknown": "{kind} is not one of the kinds of this vocabulary.",
    # The sentences that each form means.
    "say_column": "{column} is {source}{how}.",
    "say_flag": "{column} is 1 where {source}{how} holds {values}, and 0 where it holds anything else or is empty.",
    "say_flag_or_empty": "{column} is 1 where {source}{how} holds {values}, 0 where it holds anything else, and empty where it is empty.",
    "say_scale": "{column} is {source}{how} multiplied by {factor}{offset}.",
    "say_offset_plus": ", with {offset} added",
    "say_offset_minus": ", with {offset} taken away",
    "say_date": "{column} is the date of {source}, without its time{how}.",
    "say_trim": "{column} is {source} with any spaces at either end removed{how}.",
    "say_filter": "{view} keeps only the rows in which {source} holds {values}{how}.",
    "say_path": "{column} is {source}, which {base} reaches {steps}.",
    "say_window": "A {thing} belongs to the anaesthetic whose {key} it shares ({pairs}), if its {time} lies between the anaesthetic's start and stop, {margin}.",
    "say_margin_same": "allowing {before} {minutes} either side",
    "say_margin": "allowing {before} {before_minutes} before the start and {after} {after_minutes} after the stop",
    "say_margin_none": "with no margin either side",
    "say_joined": "{column} is every {text} of the rows of {table} whose {link} matches {on}, joined in the order of {order} with {separator} between them.",
    "say_codes": "The local {codes} of {source} {are} translated to {pairs}, and every other code is other.",
    "say_how": ", reached {path}",
    "say_steps": "through {steps}",
    "say_step": "{table} ({pairs})",
    "space": "a space",
    "nothing": "nothing",
    # The check.
    "check_whole": "This change keeps the map whole: all {views} of the record run and give the rows they should, every identifying column is unique, every flag is filled, and the planted newborns give the expected answer.",
    "check_whole_now": "The map as it stands is whole: all {views} of the record run and give the rows they should, every identifying column is unique, every flag is filled, and the planted newborns give the expected answer.",
    "check_breaks": "This change breaks the map in {count}:",
    "check_kept_old": "This change breaks nothing that held before it. {count} {were} there before it and {remain}:",
    "check_mends": "This change also mends {count} that {were} there before it.",
    "check_now_breaks": "The map as it stands has {count}:",
    "placeholders": "The local codes of {kinds} are not chosen yet, so the check gives {them}.",
    "outside_window": "{view} leaves out {count} of the invented rows because their time lies outside the anaesthetic's window, as the window intends.",
    "orphans": "{count} rows of {view} link to an anaesthetic that role_anaesthetic does not hold, which can be right where role_anaesthetic leaves some anaesthetics out.",
    "contract": "{problem}",
    "runs_not": "{view} does not run on the invented rows ({error}).",
    "doubled": "{view} gives {count} of its rows more than once, each under a second {link}, so a row is linked to more than one {target}.",
    "doubled_reading": "role_reading gives {count} readings twice, each linked to a second anaesthetic, so a reading no longer links to exactly one anaesthetic.",
    "missing": "{view} leaves out {count} of the {total} rows that its bindings should give.",
    "extra": "{view} gives {count} rows that its bindings should not give.",
    "keys": "{count} keys of {view} repeat, so its key no longer identifies one row.",
    "flag_empty": "{view}.{column} is empty in {count}, and a flag is never empty.",
    "type": "{view}.{column} gives {found} where the contract asks for {wanted}.",
    "count_fails": "The standard count {name} does not run on the shadow.",
    "neonatal_missing": "The neonatal audit cannot run, because the map does not supply {views}.",
    "neonatal_fails": "The neonatal audit does not run on the shadow ({error}).",
    "neonatal_minutes": "The planted neonate {key} gives {found} minutes below 40, where {wanted} are expected.",
    "neonatal_died": "The planted neonate {key} gives {found} for a death within 90 days, where {wanted} is expected.",
    "neonatal_absent": "The planted neonate {key} is not counted, although the audit should count it.",
    "neonatal_present": "The planted neonate {key} is counted, although the audit should leave it out.",
    "neonatal_more": "{count} further planted neonates give an answer other than the expected one.",
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
    its window, its joined rows or its filters."""
    if not binding:
        return []
    found = []
    for step in binding.get("path") or []:
        for a, b in propose.step_pairs(step):
            found += [(step[0], a), (step[2], b)]
    if binding.get("column"):
        found.append((binding["table"], binding["column"]))
    window = binding.get("window")
    if window:
        found += [(window["table"], window[k]) for k in ("key", "output", "start", "stop")]
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
        if not isinstance(derive, dict) or derive.get("form") not in DERIVED:
            bad(f"a derived value is one of {', '.join(DERIVED)}")
        if derive["form"] == "flag" and (not isinstance(derive.get("values"), list) or not derive["values"]):
            bad("a derived flag lists its values")
        if derive["form"] == "scale" and not all(isinstance(derive.get(k, 0), (int, float)) for k in ("factor", "offset")):
            bad("a change of unit gives its factor and offset as numbers")
    window = binding.get("window")
    if window is not None:
        if not isinstance(window, dict) or not {"table", "key", "output", "start", "stop", "time"} <= set(window):
            bad("a time window names the anaesthetic's table, its key, output, start and stop, and the time of the row")
        names(window["table"], window["key"], window["output"], window["start"], window["stop"])
        if not binding.get("column"):
            bad("a time window needs the key that the row shares with its anaesthetic")
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
        raise CorrectionError(WORDING["not_drafted"].format(view=view_name))
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
            view=view_name, source=f"{table}.{name}", values=_shown_values(values), how=_how(path))),
            source=f"{table}.{name}")
        return built
    if column is None:
        raise CorrectionError(WORDING["filter_rows"] if form != "column" else WORDING["unknown_about"].format(about=about))
    type_words = TYPE_WORDS[column["type"]]
    if form == "codes":
        if column["type"] != "kind":
            raise CorrectionError(WORDING["codes_kind"].format(about=about, type_words=type_words))
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
        pairs = [f"{k} ({_clean(c, 30)})" for c, k in list(chosen.items())[:10]]
        built.update(chosen=chosen, source=entry["bound"], sentence=_fit(WORDING["say_codes"].format(
            codes="code" if len(chosen) == 1 else "codes", source=entry["bound"] or about, are="is" if len(chosen) == 1 else "are",
            pairs=_and(pairs))))
        return built
    if form in ("column", "derived"):
        table, name, data_type = _column(state, correction.get("table"), correction.get("column"))
        path = path_to(state, role, table)
        binding = {"table": table, "column": name, "path": path, "data_type": data_type}
        source = f"{table}.{name}"
        if form == "column":
            sentence = WORDING["say_column"].format(column=column["name"], source=source, how=_how(path))
        else:
            derive = dict(correction.get("derive") or {})
            kind = derive.get("form")
            if kind not in DERIVED or column["type"] not in DERIVED_TYPES[kind]:
                raise CorrectionError(WORDING["derive_type"].format(about=about, type_words=type_words,
                                                                    form_words=FORM_WORDS.get(kind, "that")))
            if kind == "flag":
                values = _values(derive.get("values"))
                binding["derive"] = {"form": "flag", "values": values}
                sentence = WORDING["say_flag_or_empty" if column["type"] == "flag_or_empty" else "say_flag"].format(
                    column=column["name"], source=source, values=_shown_values(values), how=_how(path, True))
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
                sentence = WORDING["say_scale"].format(column=column["name"], source=source,
                                                       factor=propose._number_text(factor), offset=shown_offset, how=_how(path, True))
            else:
                binding["derive"] = {"form": kind}
                sentence = WORDING[f"say_{kind}"].format(column=column["name"], source=source, how=_how(path))
        built.update(binding=binding, sentence=_fit(sentence), source=propose._from_text(binding))
        return built
    if form in ("path", "pair"):
        if column["name"] not in links and column["type"] != "key":
            raise CorrectionError(WORDING["not_a_link"].format(about=about))
        path = _steps(state, base, correction.get("steps"), pair=form == "pair")
        table, name, data_type = _column(state, path[-1][2], correction.get("column"))
        binding = {"table": table, "column": name, "path": path, "data_type": data_type}
        steps = WORDING["say_steps"].format(steps=", then ".join(
            WORDING["say_step"].format(table=s[2], pairs=propose._step_text(s)) for s in path))
        built.update(binding=binding, source=propose._from_text(binding), sentence=_fit(WORDING["say_path"].format(
            column=column["name"], source=f"{table}.{name}", base=base, steps=steps)))
        return built
    if form == "window":
        if links.get(column["name"]) != "role_anaesthetic.anaesthetic_key":
            raise CorrectionError(WORDING["window_link"])
        if not correction.get("table") or not correction.get("column") or not correction.get("key"):
            raise CorrectionError(WORDING["window_key"])
        table, name, data_type = _column(state, correction.get("table"), correction.get("column"))
        path = path_to(state, role, table)
        anaesthetic = (state.data["roles"].get("role_anaesthetic") or {})
        defaults = {}
        if anaesthetic:
            a_base = anaesthetic["rows"]["binding"]["table"]
            for wanted, column_name in (("output", "anaesthetic_key"), ("start", "start_time"), ("stop", "stop_time")):
                held = anaesthetic["columns"][column_name].get("binding")
                if held and not held.get("path") and not held.get("window") and held["table"].upper() == a_base.upper():
                    defaults[wanted] = held["column"]
            defaults["table"] = a_base
        a_table = correction.get("anaesthetic") or defaults.get("table")
        if not a_table:
            raise CorrectionError(WORDING["window_anaesthetic"])
        a_table = _table(state, a_table)
        fields = {}
        for wanted in ("key", "output", "start", "stop"):
            given = correction.get(wanted) or (defaults.get(wanted) if a_table.upper() == (defaults.get("table") or "").upper() else None)
            if not given:
                raise CorrectionError(WORDING["window_anaesthetic"] if wanted != "key" else WORDING["window_key"])
            fields[wanted] = _column(state, a_table, given)[1]
        time_column = correction.get("time") or next((c["name"] for c in spec["columns"] if c["type"] == "datetime"
                                                       and c["name"] in spec["key"]), None) \
            or next((c["name"] for c in spec["columns"] if c["type"] == "datetime"), None)
        if not time_column or not role["columns"].get(time_column, {}).get("binding"):
            raise CorrectionError(WORDING["window_time"].format(column=time_column or "a time"))
        margins = []
        for wanted in ("before", "after"):
            given = correction.get(wanted, 0)
            try:
                minutes = int(given)
            except (TypeError, ValueError):
                raise CorrectionError(WORDING["margin"].format(most=MOST_MARGIN)) from None
            if str(given).strip() not in (str(minutes), f"{minutes}.0") or not 0 <= minutes <= MOST_MARGIN:
                raise CorrectionError(WORDING["margin"].format(most=MOST_MARGIN))
            margins.append(minutes)
        before, after = margins
        window = {"table": a_table, **fields, "time": time_column, "before": before, "after": after}
        binding = {"table": table, "column": name, "path": path, "data_type": data_type, "window": window}
        minutes = lambda n: "minute" if n == 1 else "minutes"  # noqa: E731
        if before == after == 0:
            margin = WORDING["say_margin_none"]
        elif before == after:
            margin = WORDING["say_margin_same"].format(before=before, minutes=minutes(before))
        else:
            margin = WORDING["say_margin"].format(before=before, after=after, before_minutes=minutes(before), after_minutes=minutes(after))
        pairs = f"{table}.{name} = {a_table}.{fields['key']}"
        built.update(binding=binding, source=f"{a_table}.{fields['output']}", sentence=_fit(WORDING["say_window"].format(
            thing=THING.get(view_name, "row"), key=fields["key"], pairs=pairs, time=time_column, margin=margin)))
        return built
    if form == "joined":
        if column["type"] != "text":
            raise CorrectionError(WORDING["joined_type"].format(about=about, type_words=type_words))
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
            column=column["name"], text=joined["text"], table=x_table, link=joined["link"], on=f"{on_table}.{on_column}",
            order=joined["order"], separator=shown)))
        return built
    raise CorrectionError(WORDING["unknown_form"].format(form=form))


def apply(state, built, record):
    """Writes a built correction into the map that state holds, with the person's record of it."""
    role = state.data["roles"][built["view"]]
    if built["form"] == "codes":
        held = dict((state.codes.get(built["about"]) or {}).get("chosen") or {})
        held.update(built["chosen"])
        state.choose_codes(built["about"], held, record.get("date"))
        item = role["columns"][built["column"]]
        item["says"] = built["sentence"]
        item["status"] = "person"
        item.pop("question", None)
        item["confirmation"] = record
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


# The shadow: invented rows of every role, written into tables shaped as the map's bindings name them.

def _declared(data_type):
    text = (data_type or "").upper()
    if not text:
        return "text"
    if "DATE" in text or "TIME" in text:
        return "datetime" if "TIME" in text else "date"
    if "INT" in text or "BIT" in text:
        return "int"
    if any(w in text for w in ("NUMERIC", "DECIMAL", "FLOAT", "REAL", "MONEY", "NUMBER", "DOUBLE")):
        return "number"
    return "text"


def _when(value):
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value) if len(value) > 10 else dt.date.fromisoformat(value)
        except ValueError:
            return None
    return value


class Source:
    """The invented tables of a shadow: rows as dictionaries keyed by column name in capitals, with an index for each
    column that is searched."""

    def __init__(self, dictionary):
        self.dictionary = dictionary
        self.tables = {}
        self.indexes = {}
        self.keys = {}
        self.counter = 5_000_000
        self.anaesthetic_table = None
        self.rows_of = {}      # anaesthetic key -> its row of the anaesthetic's table

    def table(self, name):
        upper = name.upper()
        if upper not in self.tables:
            held = self.dictionary.table(name) if self.dictionary is not None else None
            self.tables[upper] = {"name": held.name if held is not None else name, "columns": {}, "rows": [],
                                  "key": tuple(k.upper() for k in held.primary_key()) if held is not None else ()}
        return self.tables[upper]

    def column(self, table, column):
        held = self.table(table)
        upper = column.upper()
        if upper not in held["columns"]:
            entry = self.dictionary.table(table).column(column) if self.dictionary is not None and self.dictionary.table(table) else None
            held["columns"][upper] = (entry.name if entry is not None else column, _declared(entry.data_type if entry is not None else ""))
        return upper

    def declared(self, table, column):
        return self.table(table)["columns"][self.column(table, column)][1]

    def convert(self, value, declared):
        """A value as a column of the declared type holds it."""
        if value is None:
            return None
        if isinstance(value, bool):
            value = int(value)
        if declared == "datetime":
            value = _when(value)
            if isinstance(value, dt.datetime):
                return value.replace(microsecond=0)
            if isinstance(value, dt.date):
                return dt.datetime.combine(value, dt.time(10, 30))
            return None
        if declared == "date":
            value = _when(value)
            return value.date() if isinstance(value, dt.datetime) else value if isinstance(value, dt.date) else None
        if declared in ("int", "number"):
            if isinstance(value, (int, float)):
                return int(round(value)) if declared == "int" or float(value) == int(value) else float(value)
            text = str(value).strip()
            if re.fullmatch(r"-?\d{1,15}", text):
                return int(text)
            if declared == "number" and re.fullmatch(r"-?\d+\.\d+", text):
                return float(text)
            if text not in self.keys:
                self.keys[text] = 1_000_000 + len(self.keys)
            return self.keys[text]
        if isinstance(value, dt.datetime):
            return value.isoformat(sep=" ")
        if isinstance(value, dt.date):
            return value.isoformat()
        if isinstance(value, float):
            return str(int(value)) if value == int(value) else repr(value)
        return str(value)

    def set(self, row, table, column, value):
        upper = self.column(table, column)
        value = self.convert(value, self.table(table)["columns"][upper][1])
        if row.get(upper) is not None or value is None:
            return
        row[upper] = value
        index = self.indexes.get((table.upper(), upper))
        if index is not None:
            index.setdefault(_hashable(value), []).append(row)

    def new(self, table, values=None):
        row = {}
        self.table(table)["rows"].append(row)
        for column, value in (values or {}).items():
            self.set(row, table, column, value)
        return row

    def find(self, table, column, value):
        upper = self.column(table, column)
        value = self.convert(value, self.table(table)["columns"][upper][1])
        if value is None:
            return []
        key = (table.upper(), upper)
        if key not in self.indexes:
            index = {}
            for row in self.table(table)["rows"]:
                if row.get(upper) is not None:
                    index.setdefault(_hashable(row[upper]), []).append(row)
            self.indexes[key] = index
        return self.indexes[key].get(_hashable(value), [])

    def match(self, table, pairs, values):
        found = self.find(table, pairs[0][1], values[0])
        for (_, b), value in zip(pairs[1:], values[1:]):
            wanted = self.convert(value, self.declared(table, b))
            found = [r for r in found if r.get(b.upper()) == wanted]
        return found

    def fresh(self, table, column, row=None):
        """A new value for a join column, or, for the second anaesthetic of a pair in one encounter, the value that
        the first holds, given to the first where it has none yet."""
        if row is not None and row.get("__same") is not None and self.anaesthetic_table \
                and table.upper() == self.anaesthetic_table.upper() and column.upper() not in self.table(table)["key"]:
            first = row["__same"]
            if first.get(column.upper()) is None:
                self.set(first, table, column, self.fresh(table, column))
            return first[column.upper()]
        self.counter += 1
        declared = self.declared(table, column)
        return self.counter if declared in ("int", "number") else f"F{self.counter}"


def _hashable(value):
    return value


def _key_columns(state):
    """(table, column) of every column bound as a whole key of a view in the view's own table, such as the key of an
    anaesthetic in the anaesthetic's table."""
    found = set()
    for name, role in state.data["roles"].items():
        spec = state.views[name]
        for column in spec["key"]:
            binding = role["columns"][column].get("binding")
            if binding and not binding.get("path") and not binding.get("window") and len(spec["key"]) == 1:
                found.add((binding["table"].upper(), binding["column"].upper()))
    return found


class Shadow:
    """The shadow of one map: its views, the role rows it was built from, the tables and the DuckDB database."""

    def __init__(self, state, views, kinds, vocabularies):
        self.state = state
        self.views = views                  # {view: sql}
        self.kinds = kinds                  # the map's kinds, with invented codes where none are chosen
        self.vocabularies = vocabularies    # {view: {column: {kind: [code]}}}
        self.source = Source(state.dictionary)
        self.keys = _key_columns(state)
        anaesthetic = state.data["roles"].get("role_anaesthetic")
        if anaesthetic:
            self.source.anaesthetic_table = anaesthetic["rows"]["binding"]["table"]

    # Codes.

    def codes_of(self, view_name, column):
        if view_name == "role_reading" and column["name"] == "kind":
            return {k: item.get("codes", []) for k, item in self.kinds.items()}
        if column["type"] == "kind":
            return self.vocabularies.get(view_name, {}).get(column["name"])
        return None

    def encode(self, step, value):
        """A value of the role as the column that the plan reads would hold it."""
        op = step[0]
        if value is None:
            return None
        if op == "date":
            return value
        if op in ("float", "int"):
            return str(value)
        if op == "const":
            return OTHER_CODE
        if op == "flag_in":
            _, yes, no, mode = step
            return yes[0] if int(value) == 1 else no[0]
        if op == "kind":
            _, given, translated = step
            for kind, codes in given:
                if kind == value and codes:
                    return codes[0]
            return OTHER_CODE
        if op == "derive_flag":
            values = step[1]
            if int(value) == 1:
                return values[0]
            return NOT_IN_LIST if NOT_IN_LIST not in values else "0" + NOT_IN_LIST
        if op == "scale":
            _, factor, offset, _ = step
            return (float(value) - offset) / factor
        if op == "trim":
            return f"  {value} "
        return value

    # Placing the role rows.

    def reach(self, ctx, path, final=None):
        """The row at the end of a path from the view's own row, made where it does not exist yet. final, when given,
        is (column, value) of the end's column, so that a path that ends at the key of a row that already exists, such
        as an anaesthetic's own row, reaches that row rather than a copy of it."""
        source = self.source
        base_row = ctx[()]
        prefix = ()
        whole = tuple(propose.step_key(s) for s in path)
        if path and whole not in ctx and final is not None and final[1] is not None \
                and (path[-1][2].upper(), final[0].upper()) in self.keys | self._primary(path[-1][2], final[0]) \
                and all(base_row.get(a.upper()) is None for a, _ in propose.step_pairs(path[0])) \
                and not any(tuple(propose.step_key(s) for s in path[:i]) in ctx for i in range(1, len(path))):
            existing = source.find(path[-1][2], final[0], final[1])
            if existing:
                rows = [None] * (len(path) + 1)
                rows[-1] = existing[0]
                for i in range(len(path) - 1, -1, -1):
                    step = path[i]
                    after = rows[i + 1]
                    values = []
                    for a, b in propose.step_pairs(step):
                        v = after.get(b.upper())
                        if v is None:
                            v = source.fresh(step[2], b, after)
                            source.set(after, step[2], b, v)
                            v = after.get(b.upper())
                        values.append((a, v))
                    if i == 0:
                        for a, v in values:
                            source.set(base_row, step[0], a, v)
                        rows[0] = base_row
                    else:
                        found = source.match(step[0], [(a, a) for a, _ in values], [v for _, v in values])
                        rows[i] = found[0] if found else source.new(step[0], dict(values))
                for i in range(1, len(path) + 1):
                    ctx[tuple(propose.step_key(s) for s in path[:i])] = rows[i]
                return rows[-1]
        row = base_row
        for step in path:
            following = prefix + (propose.step_key(step),)
            if following in ctx:
                row, prefix = ctx[following], following
                continue
            pairs = propose.step_pairs(step)
            for a, _ in pairs:
                if row.get(a.upper()) is None:
                    source.set(row, step[0], a, source.fresh(step[0], a, row))
            values = [row.get(a.upper()) for a, _ in pairs]
            found = source.match(step[2], pairs, values)
            row = found[0] if found else source.new(step[2], {b: v for (_, b), v in zip(pairs, values)})
            ctx[following] = row
            prefix = following
        return row

    def _primary(self, table, column):
        key = self.source.table(table)["key"]
        return {(table.upper(), column.upper())} if key == (column.upper(),) else set()

    def place(self, view_name, values, decoy=None):
        """Writes one row of a role view into the tables, as its bindings name them. values is {column: value}."""
        state, source = self.state, self.source
        role = state.data["roles"][view_name]
        spec = state.views[view_name]
        base = role["rows"]["binding"]["table"]
        base_row = {}
        plans = {}
        direct, later = [], []
        for column in spec["columns"]:
            binding = role["columns"][column["name"]].get("binding")
            if not binding:
                continue
            plans[column["name"]] = propose.plan(column, binding, self.codes_of(view_name, column))
            if not binding.get("path") and not binding.get("window") and not binding.get("joined"):
                direct.append((column, binding))
            else:
                later.append((column, binding))
        for column, binding in direct:
            # The row's own values are gathered before the row joins its table, and so outside every index.
            upper = source.column(base, binding["column"])
            value = source.convert(self.encode(plans[column["name"]], values.get(column["name"])), source.declared(base, binding["column"]))
            if value is not None and base_row.get(upper) is None:
                base_row[upper] = value
        held = source.table(base)
        row = None
        if held["key"] and all(base_row.get(k) is not None for k in held["key"]):
            found = source.match(base, [(k, k) for k in held["key"]], [base_row[k] for k in held["key"]])
            if found:
                row = found[0]
                for upper, value in base_row.items():
                    if row.get(upper) is None:
                        source.set(row, base, held["columns"][upper][0], value)
        if row is None:
            row = source.new(base)
            for upper, value in base_row.items():
                source.set(row, base, held["columns"][upper][0], value)
        if view_name == "role_anaesthetic" and not decoy:
            source.rows_of[values.get("anaesthetic_key")] = row
            if values.get("__same") in source.rows_of:
                row["__same"] = source.rows_of[values["__same"]]
        ctx = {(): row}
        links = {link["column"] for link in spec.get("links", [])}
        later.sort(key=lambda item: (item[0]["name"] not in links, bool(item[1].get("window"))))
        for column, binding in later:
            value = values.get(column["name"])
            if binding.get("window"):
                self._place_window(ctx, binding, value)
            elif binding.get("joined"):
                self._place_joined(ctx, binding, value)
            else:
                encoded = self.encode(plans[column["name"]], value)
                end = self.reach(ctx, binding["path"], (binding["column"], encoded))
                source.set(end, binding["table"], binding["column"], encoded)
        for item in (role["rows"]["binding"].get("filter") or []):
            end = self.reach(ctx, item["path"])
            source.set(end, item["table"], item["column"], (NOT_IN_LIST if NOT_IN_LIST not in item["values"] else "0" + NOT_IN_LIST)
                       if decoy else item["values"][0])
        return row

    def _place_window(self, ctx, binding, value):
        window, source = binding["window"], self.source
        if value is None:
            return
        found = source.find(window["table"], window["output"], value)
        if not found:
            return
        anaesthetic = found[0]
        shared = anaesthetic.get(window["key"].upper())
        if shared is None:
            source.set(anaesthetic, window["table"], window["key"], source.fresh(window["table"], window["key"], anaesthetic))
            shared = anaesthetic.get(window["key"].upper())
        end = self.reach(ctx, binding["path"])
        source.set(end, binding["table"], binding["column"], shared)

    def _place_joined(self, ctx, binding, value):
        joined, source = binding["joined"], self.source
        end = self.reach(ctx, binding["path"])
        on = end.get(binding["column"].upper())
        if on is None:
            source.set(end, binding["table"], binding["column"], source.fresh(binding["table"], binding["column"], end))
            on = end.get(binding["column"].upper())
        if value is None:
            return
        separator = joined.get("separator", " ")
        text = str(value)
        parts = text.split(separator) if separator and separator in text else [text]
        for order in range(len(parts), 0, -1):
            source.new(joined["table"], {joined["link"]: on, joined["order"]: order, joined["text"]: parts[order - 1]})

    def pair_encounters(self):
        """Gives the second anaesthetic of each pair every join column of the anaesthetic's table that the first
        holds, so that the pairs share their encounter whichever binding set the column."""
        table = self.source.anaesthetic_table
        if not table:
            return
        held = self.source.table(table)
        joined = set()
        for role in self.state.data["roles"].values():
            for item in [role["rows"], *role["columns"].values()]:
                binding = item.get("binding") or {}
                steps = list(binding.get("path") or []) + [s for f in binding.get("filter") or [] for s in f["path"]]
                for step in steps:
                    for a, b in propose.step_pairs(step):
                        if step[0].upper() == table.upper():
                            joined.add(a.upper())
                        if step[2].upper() == table.upper():
                            joined.add(b.upper())
                window = binding.get("window")
                if window and window["table"].upper() == table.upper():
                    joined.add(window["key"].upper())
        joined -= set(held["key"])
        for row in held["rows"]:
            first = row.get("__same")
            if first is None:
                continue
            for upper in joined:
                if first.get(upper) is None:
                    self.source.set(first, table, held["columns"][upper][0], self.source.fresh(table, held["columns"][upper][0]))
                if row.get(upper) is None:
                    self.source.set(row, table, held["columns"][upper][0], first[upper])

    def register(self):
        """Every table and column that a view names, so that each exists in the shadow although no row fills it."""
        for name, role in self.state.data["roles"].items():
            self.source.table(role["rows"]["binding"]["table"])
            for item in [role["rows"], *role["columns"].values()]:
                for table, column in named_columns(item.get("binding")):
                    self.source.column(table, column)

    # The model of the bindings.

    def follow(self, ctx, path):
        row = ctx[()]
        prefix = ()
        for step in path:
            following = prefix + (propose.step_key(step),)
            if following not in ctx:
                if row is None:
                    ctx[following] = None
                else:
                    pairs = propose.step_pairs(step)
                    values = [row.get(a.upper()) for a, _ in pairs]
                    found = self.source.match(step[2], pairs, values) if all(v is not None for v in values) else []
                    ctx[following] = found[0] if found else None
            row, prefix = ctx[following], following
        return row

    def expected(self, view_name):
        """The rows that a view should give, by the model of its bindings, over the shadow's tables: one for each row of
        its own table that passes its filters, with each join reaching at most one row. A join that reaches more is
        what the comparison with the view's own rows finds."""
        state, source = self.state, self.source
        role = state.data["roles"][view_name]
        spec = state.views[view_name]
        base = role["rows"]["binding"]["table"]
        links = {link["column"] for link in spec.get("links", [])}
        anchor = None
        for column in spec["columns"]:
            if column["name"] in links and column["name"] in spec["key"] and role["columns"][column["name"]].get("binding"):
                anchor = column["name"]
                break
        found, outside = [], 0
        for row in source.table(base)["rows"]:
            ctx = {(): row}
            passes = True
            for item in role["rows"]["binding"].get("filter") or []:
                end = self.follow(ctx, item["path"])
                value = _text(end.get(item["column"].upper())) if end is not None else None
                if value not in item["values"]:
                    passes = False
            if not passes:
                continue
            out, raws = {}, {}
            windows = []
            for column in spec["columns"]:
                binding = role["columns"][column["name"]].get("binding")
                if not binding:
                    empty = propose._empty(column)
                    out[column["name"]] = None if empty == "NULL" else int(empty)
                    continue
                end = self.follow(ctx, binding["path"])
                raw = end.get(binding["column"].upper()) if end is not None else None
                raws[column["name"]] = raw
                if binding.get("window"):
                    windows.append((column, binding, raw))
                elif binding.get("joined"):
                    out[column["name"]] = self._joined(binding, raw)
                else:
                    out[column["name"]] = evaluate(propose.plan(column, binding, self.codes_of(view_name, column)), raw)
            dropped = False
            for column, binding, shared in windows:
                window = binding["window"]
                time_value = raws.get(window["time"])
                matches = self._window(window, shared, time_value)
                out[column["name"]] = matches[0].get(window["output"].upper()) if matches else None
                if not matches and shared is not None:
                    dropped = True
            if anchor is not None and out.get(anchor) is None:
                outside += dropped
                continue
            found.append(tuple(out[c["name"]] for c in spec["columns"]))
        return found, outside

    def _window(self, window, shared, time_value):
        if shared is None or not isinstance(time_value, dt.datetime):
            return []
        found = []
        for row in self.source.find(window["table"], window["key"], shared):
            start, stop = row.get(window["start"].upper()), row.get(window["stop"].upper())
            if not isinstance(start, dt.datetime):
                continue
            if time_value < start - dt.timedelta(minutes=int(window.get("before", 0))):
                continue
            if isinstance(stop, dt.datetime) and time_value > stop + dt.timedelta(minutes=int(window.get("after", 0))):
                continue
            found.append(row)
        return found

    def _joined(self, binding, on):
        joined = binding["joined"]
        if on is None:
            return None
        rows = self.source.find(joined["table"], joined["link"], on)
        texts = [(r.get(joined["order"].upper()), _text(r.get(joined["text"].upper()))) for r in rows]
        texts = [t for t in sorted(texts, key=lambda item: (item[0] is not None, item[0] if item[0] is not None else 0)) if t[1] is not None]
        return joined.get("separator", " ").join(t for _, t in texts) if texts else None

    # The database.

    def database(self):
        import duckdb
        con = duckdb.connect()
        date_columns = set()
        for upper, held in self.source.tables.items():
            columns = []
            for column_upper, (name, declared) in held["columns"].items():
                values = [r.get(column_upper) for r in held["rows"] if r.get(column_upper) is not None]
                if declared == "datetime":
                    kind = "TIMESTAMP"
                elif declared == "date":
                    kind = "DATE"
                elif declared in ("int", "number"):
                    kind = "BIGINT" if all(isinstance(v, int) for v in values) else "DOUBLE"
                else:
                    kind = "VARCHAR"
                if kind in ("TIMESTAMP", "DATE"):
                    date_columns.add(column_upper)
                columns.append((column_upper, name, kind))
            if not columns:
                columns.append(("__EMPTY", "schemalyser_empty", "VARCHAR"))
            con.execute(f"CREATE TABLE {_quoted(held['name'])} (" + ", ".join(f"{_quoted(n)} {k}" for _, n, k in columns) + ")")
            rows = held["rows"]
            for first in range(0, len(rows), 1000):
                chunk = rows[first:first + 1000]
                if not chunk:
                    continue
                text = ",\n".join("(" + ", ".join(_literal(r.get(u), k) for u, _, k in columns) + ")" for r in chunk)
                con.execute(f"INSERT INTO {_quoted(held['name'])} VALUES {text}")
        return con, frozenset(date_columns)


def _quoted(name):
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else '"' + name.replace('"', '""') + '"'


def _literal(value, kind):
    if value is None:
        return "NULL"
    if kind in ("BIGINT", "DOUBLE"):
        return repr(float(value)) if kind == "DOUBLE" else str(int(value))
    if kind == "TIMESTAMP":
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if kind == "DATE":
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def _text(value):
    """A value cast to text, as DuckDB casts it, which is what the shadow's views run on."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _float(value):
    if value is None or isinstance(value, (dt.date, dt.datetime)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def evaluate(step, raw):
    """What a planned operation gives for one stored value, as the view's SQL gives it."""
    op = step[0]
    if op == "raw":
        return raw
    if op == "date":
        if isinstance(raw, dt.datetime):
            return raw.date()
        if isinstance(raw, dt.date) or raw is None:
            return raw
        return _when(str(raw)[:10])
    if op == "float":
        return _float(raw)
    if op == "int":
        number = _float(raw)
        return None if number is None else int(round(number))
    if op == "const":
        return step[1]
    if op == "flag_in":
        _, yes, no, mode = step
        held = raw if not isinstance(raw, float) or raw != int(raw) else int(raw)
        if mode == "plain":
            return 1 if held in yes else 0
        if mode == "if_empty":
            return 0 if held in no else 1
        return 1 if held in yes else 0 if held in no else None
    if op == "kind":
        _, given, translated = step
        if not translated:
            return None if raw is None else "other"
        text = _text(raw)
        for kind, codes in given:
            if text in codes:
                return kind
        return "other"
    if op == "derive_flag":
        _, values, or_empty = step
        if raw is None and or_empty:
            return None
        return 1 if _text(raw) in values else 0
    if op == "scale":
        _, factor, offset, whole = step
        number = _float(raw)
        if number is None:
            return None
        number = number * factor + offset
        return int(round(number)) if whole else number
    if op == "trim":
        return None if raw is None else _text(raw).strip(" ")
    return raw


def _normal(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        number = round(float(value), 3)
        return int(number) if number == int(number) else number
    if isinstance(value, dt.datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if hasattr(value, "as_tuple"):          # a Decimal
        return _normal(float(value))
    return str(value)


# The role rows of the shadow.

def _typed(view, row):
    values = {}
    for column, value in zip(view["columns"], row):
        if column["type"] in ("datetime", "date") and isinstance(value, str):
            value = _when(value)
        values[column["name"]] = value
    return values


def role_rows(state, seed=SHADOW_SEED, anaesthetics=SHADOW_ANAESTHETICS):
    """Invented rows of every view that the map supplies, as {view: [{column: value}]}: the three that every audit
    reads from rolemap's generator with the planted neonates, a few pairs of anaesthetics of one patient in one
    encounter, and a few rows of each further view for the first anaesthetics."""
    rng = random.Random(seed)
    generated = rolemap.generated_rows(seed, anaesthetics)
    cases = rolemap.planted()
    contract = {v["name"]: v for v in state.model["views"]}
    rows = {}
    for name in ("role_patient", "role_anaesthetic", "role_reading"):
        rows[name] = [_typed(contract[name], r) for r in generated[name] + cases[name]["rows"]]
    anaesthetic_rows = [r for r in rows["role_anaesthetic"] if not str(r["anaesthetic_key"]).startswith("99")]
    # Two anaesthetics of one patient a few hours apart, as in one admission.
    for row in [r for r in anaesthetic_rows if r["stop_time"] is not None and r["stop_time"] > r["start_time"]][:ENCOUNTER_PAIRS]:
        key = f"{row['anaesthetic_key']}B"
        start = row["stop_time"] + dt.timedelta(hours=3)
        stop = start + dt.timedelta(minutes=60)
        rows["role_anaesthetic"].append({"anaesthetic_key": key, "patient_key": row["patient_key"], "start_time": start,
                                         "stop_time": stop, "__same": row["anaesthetic_key"]})
        moment = start
        while moment <= stop:
            rows["role_reading"].append({"anaesthetic_key": key, "kind": "map_cuff", "reading_time": moment,
                                         "value": round(rng.gauss(60, 5), 1), "accepted": 1})
            moment += dt.timedelta(minutes=5)
    first = anaesthetic_rows[:FURTHER_ANAESTHETICS]
    for name, role in state.data["roles"].items():
        if name in rows:
            continue
        rows[name] = further_rows(state, contract[name], first, rng)
    return rows


def further_rows(state, view, anaesthetics, rng):
    per = 1 if len(view["key"]) == 1 else 2
    chosen = (state.codes.get(f"{view['name']}.kind") or {}).get("chosen") or {}
    found, keys = [], set()
    for i, anaesthetic in enumerate(anaesthetics):
        start = anaesthetic["start_time"]
        for j in range(per):
            row = {}
            for column in view["columns"]:
                name, kind = column["name"], column["type"]
                if name == "anaesthetic_key":
                    value = anaesthetic["anaesthetic_key"]
                elif name == "patient_key":
                    value = anaesthetic["patient_key"]
                elif name == "stay_key":
                    value = f"S{anaesthetic['anaesthetic_key']}"
                elif kind == "key":
                    value = f"K{i:03d}{j}"
                elif kind == "datetime":
                    value = start + dt.timedelta(minutes=7 * j + 3)
                elif kind == "date":
                    value = start.date()
                elif kind == "number":
                    value = round(rng.uniform(1, 100), 1)
                elif kind == "whole":
                    value = rng.randrange(1, 6)
                elif kind == "flag":
                    value = rng.choice((0, 1))
                elif kind == "flag_or_empty":
                    value = rng.choice((0, 1, None))
                elif kind == "kind":
                    kinds = sorted(set(chosen.values())) if name == "kind" else []
                    value = rng.choice(kinds + ["other"]) if kinds else "other"
                else:
                    value = f"text {i} {j}"
                row[name] = value
            key = tuple(str(row[c]) for c in view["key"])
            if key in keys:
                continue
            keys.add(key)
            found.append(row)
    return found


# The check.

def _views_text(count):
    return _plural(count, "part", "parts")


def _problems_text(count):
    return _plural(count, "problem", "problems")


def _cases(count):
    return _plural(count, "row", "rows")


def _error(error):
    text = " ".join(str(error).split("\n")[0].split())[:160]
    return _clean(text, 160).rstrip(" .") or "an error"


def run_check(state, seed=SHADOW_SEED, anaesthetics=SHADOW_ANAESTHETICS):
    """Builds the shadow of the map that state holds and checks it. Returns {"problems": [sentence], "notes":
    [sentence], "views": number, "seconds": number}. Every sentence names the view, and none quotes the dictionary."""
    from .translate import to_duckdb
    began = time.perf_counter()
    problems, notes = [], []
    data = copy.deepcopy(state.data)
    kinds = data["kinds"]
    missing = [k for k in rolemap.MEAN_KINDS if not (kinds.get(k) or {}).get("codes")]
    for number, kind in enumerate(missing):
        kinds.setdefault(kind, {})["codes"] = [str(9901 + number)]
    if missing:
        notes.append(WORDING["placeholders"].format(kinds=_and(missing), them="it an invented code" if len(missing) == 1 else "each an invented code"))
    vocabularies = {name: state._vocabulary_codes(name) for name in data["roles"]}
    views = {}
    for name in data["roles"]:
        role = dict(data["roles"][name])
        role["_date"] = ""
        views[name] = propose.view_sql(name, role, kinds, state.model, vocabularies[name])
    for name, sql in views.items():
        try:
            rolemap.check_view(sql, name, state.dictionary)
            if state.catalogue is not None:
                rolemap.check_view(sql, name, state.catalogue)
        except rolemap.MapError as error:
            problems.append(WORDING["contract"].format(problem=str(error)))
    held = copy.copy(state)
    held.data = data
    shadow = Shadow(held, views, kinds, vocabularies)
    shadow.register()
    rows = role_rows(held, seed, anaesthetics)
    order = [v for v in ("role_patient", "role_anaesthetic") if v in data["roles"]] + \
            [v for v in data["roles"] if v not in ("role_patient", "role_anaesthetic")]
    for name in order:
        for values in rows.get(name, []):
            shadow.place(name, values)
        if (data["roles"][name]["rows"]["binding"].get("filter")):
            for number, values in enumerate(rows.get(name, [])[:3]):
                decoy = dict(values)
                for column in state.views[name]["key"]:
                    if isinstance(decoy.get(column), str) and column not in {l["column"] for l in state.views[name].get("links", [])}:
                        decoy[column] = f"{decoy[column]}D{number}"
                shadow.place(name, decoy, decoy=True)
    shadow.pair_encounters()
    con, date_columns = shadow.database()
    results = {}
    for name in order:
        spec = state.views[name]
        try:
            statements = to_duckdb(views[name], date_columns)
            cursor = con.execute(statements[0])
            got = cursor.fetchall()
        except Exception as error:  # noqa: BLE001 - any failure to run is the finding
            problems.append(WORDING["runs_not"].format(view=name, error=_error(error)))
            continue
        results[name] = got
        expected, outside = shadow.expected(name)
        if outside:
            notes.append(WORDING["outside_window"].format(view=name, count=_cases(outside)))
        problems += _compare(name, spec, got, expected, state)
    if "role_anaesthetic" in results:
        held_keys = {_normal(r[0]) for r in results["role_anaesthetic"]}
        for name, got in results.items():
            columns = [c["name"] for c in state.views[name]["columns"]]
            if name == "role_anaesthetic" or "anaesthetic_key" not in columns:
                continue
            at = columns.index("anaesthetic_key")
            orphans = sum(1 for r in got if r[at] is not None and _normal(r[at]) not in held_keys)
            if orphans:
                notes.append(WORDING["orphans"].format(count=_plural(orphans, "row", "rows").split(" ", 1)[0], view=name))
    lacking = [v for v in rolemap.views() if v not in results]
    roles_map = {"data": data, "views": views}
    if not lacking:
        run = rolemap.duckdb_runner(con, date_columns, roles_map)
        for count_name, item in rolemap.count_queries(1, 1).items():
            try:
                run(item["sql"])
            except Exception:  # noqa: BLE001
                problems.append(WORDING["count_fails"].format(name=count_name))
        try:
            found = rolemap.per_anaesthetic(run)
            problems += _neonates(found)
        except Exception as error:  # noqa: BLE001
            problems.append(WORDING["neonatal_fails"].format(error=_error(error)))
    else:
        problems.append(WORDING["neonatal_missing"].format(views=_and(lacking)))
    return {"problems": list(dict.fromkeys(problems)), "notes": list(dict.fromkeys(notes)), "views": len(views),
            "seconds": round(time.perf_counter() - began, 1)}


def _compare(name, spec, got, expected, state):
    problems = []
    columns = spec["columns"]
    actual = Counter(tuple(_normal(v) for v in row) for row in got)
    wanted = Counter(tuple(_normal(v) for v in row) for row in expected)
    extra, missing = actual - wanted, wanted - actual
    links = [i for i, c in enumerate(columns) if c["name"] in {l["column"] for l in spec.get("links", [])}]
    if extra:
        rest = lambda row: tuple(v for i, v in enumerate(row) if i not in links)  # noqa: E731
        seen = Counter(rest(r) for r in wanted.elements())
        doubled = sum(n for r, n in extra.items() if seen.get(rest(r)))
        other = sum(extra.values()) - doubled
        if doubled:
            if name == "role_reading":
                problems.append(WORDING["doubled_reading"].format(count=f"{doubled:,}"))
            else:
                link = columns[links[0]]["name"] if links else "key"
                target = next((l["to"].split(".")[0] for l in spec.get("links", []) if l["column"] == link), "row")
                problems.append(WORDING["doubled"].format(view=name, count=f"{doubled:,}", link=link,
                                                          target=THING.get(target, target.replace("role_", "").replace("_", " "))))
        if other:
            problems.append(WORDING["extra"].format(view=name, count=f"{other:,}"))
    if missing:
        problems.append(WORDING["missing"].format(view=name, count=f"{sum(missing.values()):,}", total=f"{sum(wanted.values()):,}"))
    keys = [i for i, c in enumerate(columns) if c["name"] in spec["key"]]
    repeated = Counter(tuple(_normal(r[i]) for i in keys) for r in got)
    count = sum(1 for n in repeated.values() if n > 1)
    if count:
        problems.append(WORDING["keys"].format(count=f"{count:,}", view=name))
    allowed = _allowed_kinds(state, name)
    for i, column in enumerate(columns):
        values = [r[i] for r in got]
        if column["type"] == "flag":
            empty = sum(1 for v in values if v is None)
            if empty:
                problems.append(WORDING["flag_empty"].format(view=name, column=column["name"], count=_cases(empty)))
        wrong = next((v for v in values if v is not None and not _suits(column["type"], v, allowed.get(column["name"]))), None)
        if wrong is not None:
            problems.append(WORDING["type"].format(view=name, column=column["name"], found=_found_words(wrong),
                                                   wanted=TYPE_WORDS[column["type"]]))
    return problems


def _allowed_kinds(state, name):
    allowed = {}
    for column in state.views[name]["columns"]:
        if column["type"] != "kind":
            continue
        if name == "role_reading" and column["name"] == "kind":
            allowed[column["name"]] = {k["kind"] for k in state.model["kinds"]}
        else:
            allowed[column["name"]] = {k["kind"] for k in state.model["vocabularies"].get(column.get("vocabulary"), [])} | {"other"}
    return allowed


def _suits(kind, value, allowed=None):
    if hasattr(value, "as_tuple"):
        value = float(value)
    if kind == "date":
        return isinstance(value, dt.date) and not isinstance(value, dt.datetime)
    if kind == "datetime":
        return isinstance(value, dt.datetime)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "whole":
        return isinstance(value, int) or (isinstance(value, float) and value == int(value))
    if kind in ("flag", "flag_or_empty"):
        return isinstance(value, (int, float)) and value in (0, 1)
    if kind == "kind":
        return isinstance(value, str) and (not allowed or value in allowed)
    if kind == "text":
        return isinstance(value, str)
    return True


def _found_words(value):
    if isinstance(value, dt.datetime):
        return "a date and time"
    if isinstance(value, dt.date):
        return "a date"
    if isinstance(value, (int, float)):
        return "a number"
    return "text"


def _neonates(found):
    problems = []
    for case in rolemap.planted()["expectations"]:
        key = case["anaesthetic_key"]
        held = found.get(key) or found.get(f"{key}.0")
        if not case["counted"]:
            if held is not None:
                problems.append(WORDING["neonatal_present"].format(key=key))
            continue
        if held is None:
            problems.append(WORDING["neonatal_absent"].format(key=key))
            continue
        minutes, died = held
        wanted = case["minutes_below_40"]
        if (wanted is None) != (minutes is None) or (wanted is not None and abs(minutes - wanted) > 1e-6):
            problems.append(WORDING["neonatal_minutes"].format(key=key, found="no" if minutes is None else _normal(minutes),
                                                               wanted="none" if wanted is None else wanted))
        elif died != case["died_within_90_days"]:
            problems.append(WORDING["neonatal_died"].format(key=key, found=died, wanted=case["died_within_90_days"]))
    if len(problems) > 4:
        problems = problems[:3] + [WORDING["neonatal_more"].format(count=len(problems) - 3)]
    return problems


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
                "mended": "", "notes": after["notes"], "seconds": after["seconds"], "views": after["views"]}
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
            "views": after["views"]}


def outcome(found):
    """The outcome of a check as it is recorded with a confirmation: passed, or failed with the first problem."""
    if found["passed"]:
        return "passed"
    return "failed: " + " ".join(found["problems"][:2])[:300]
