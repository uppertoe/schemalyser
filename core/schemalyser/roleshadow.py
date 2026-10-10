"""The role-level shadow: made-up rows of the role views and the mapping views, on which a question, a hospital schema's
map and a correction are tested, and from which the correctness report of an execution package is written.

This is layer 4's work, as docs/contract.md places it. It holds three things that were written beside the layers they
test, and that run on DuckDB:

    the role shadow     role_shadow, a DuckDB database of the role views and the mapping views, filled with generated
                        rows from a seed and the planted neonates; duckdb_runner, which runs a query over the role views
                        on it, or compiled through a map on a world's hospital-shaped shadow (hospital_run); and result,
                        the audit's answer with its coverage by year
    the map's shadow    run_check, the test of a hospital schema on made-up rows: invented rows of every role, written
                        into tables shaped as the map's bindings name them, through which every view, the standard counts
                        and the neonatal audit are run and compared with what the bindings say they should give
    the correctness     expected_output and write_correctness, the question run on the role shadow with its planted
    report              cases, written as the package's expected-output.json, which audit.py reads

The hospital schema (describe.py) records what a test found but never runs one. The functions at the end of this module
run the test that a sitting of screen 1 owes and hand its result back to the sitting as data: test, check_model,
correction_check, correction_keep, run_owed_test, save and save_zip. The page's bridge (browser.py), the command line
(describe/__main__.py) and the public workspace's invented schema (workspace.py) call them in place of the sitting's
own methods, so that the page and the command line do what they did when the sitting ran the test itself.

Nothing here comes from any hospital: the rows are invented, and the tables and columns are only the names that a map
gives.
"""
import copy
import datetime as dt
import json
import random
import re
import time
from collections import Counter

import sqlglot
from sqlglot import exp

from . import compiler, propose, rolemap
# The shadow's sizes and codes and its wording belong to the corrections that it tests, and are read from there.
from .corrections import (ENCOUNTER_PAIRS, FURTHER_ANAESTHETICS, NOT_IN_LIST, OTHER_CODE, SHADOW_ANAESTHETICS, SHADOW_SEED,
                          THING, TYPE_WORDS, WORDING, _and, _clean, _fit, _plural, named_columns)
from .translate import to_duckdb

# Running queries over the role views, on a role-level shadow or, compiled through a map, on a world's hospital-shaped
# shadow.

def duckdb_runner(con, date_columns=frozenset(), roles_map=None):
    """A function that runs a T-SQL query over the role views in DuckDB and returns (columns, rows).

    With a map, the query is first compiled with it, so that it runs over a hospital-shaped shadow's source tables;
    without one, the role views are the tables of a role-level shadow."""

    def run(sql):
        text = compiler.compile_query(sql, roles_map) if roles_map is not None else sql
        statements = to_duckdb(text, date_columns)
        if len(statements) != 1:
            raise rolemap.MapError("the query did not translate to one statement")
        cursor = con.execute(statements[0])
        rows = cursor.fetchall()
        return [d[0] for d in cursor.description], rows
    return run


def run_counts(run, least=rolemap.MINIMUM_COUNT, step=rolemap.MINIMUM_COUNT):
    """Runs every standard count with run, which takes a query over the role views. Returns {name: (columns, rows)}."""
    return {name: run(item["sql"]) for name, item in rolemap.count_queries(least, step).items()}


def band_years_query(audit_sql):
    """The audit's own rows of the band in which nothing was recorded, counted by the year of the anaesthetic's start.

    The audit must have a common table expression named banded, with the columns band and start_year, as the neonatal
    audit has. The query keeps the audit's common table expressions and replaces its final SELECT."""
    tree = compiler.check_audit(audit_sql)
    names = {cte.alias.lower(): cte for cte in tree.find_all(exp.CTE)}
    if "banded" not in names or not {"band", "start_year"} <= {n.lower() for n in names["banded"].this.named_selects}:
        raise rolemap.MapError("the audit has no banded common table expression with the columns band and start_year")
    final = sqlglot.parse_one(f"SELECT x.start_year, COUNT(*) AS anaesthetics FROM banded x WHERE x.band = {rolemap.NOTHING_BAND} "
                              f"GROUP BY x.start_year ORDER BY x.start_year", dialect="tsql")
    final.set("with_" if "with_" in final.arg_types else "with", (tree.args.get("with_") or tree.args.get("with")).copy())
    return final.sql(dialect="tsql", pretty=True)


def result(run, audit_sql=None, least=rolemap.MINIMUM_COUNT, step=rolemap.MINIMUM_COUNT, blank=False):
    """The audit's answer, which always carries its coverage by year.

    Returns {"columns", "rows", "coverage", "findings", "flagged_years", "counts", "notes": {band: sentence}}. The
    sentence beside the band in which nothing was recorded says how many of its anaesthetics come from years that the
    counts flag. With blank, a year with one to four such anaesthetics is left blank, as in the audit's own result.
    """
    audit_sql = audit_sql if audit_sql is not None else rolemap.AUDIT.read_text(encoding="utf-8")
    columns, rows = run(audit_sql)
    counts = run_counts(run, least, step)
    read = rolemap.read_counts(counts, step)
    _, by_year = run(band_years_query(audit_sql))
    flagged_years = read["flagged_years"]
    nothing = next((row for row in rows if row[0] == "no mean pressure recorded"), None)
    notes = {}
    if nothing is not None:
        total = int(nothing[1] or 0)
        shown = [(year, int(n)) for year, n in by_year if year in flagged_years]
        if blank:
            shown = [(year, n) for year, n in shown if n > 4]
        flagged = sum(n for _, n in shown)
        years = sorted({year for year, _ in shown} | ({y for y, _ in by_year if y in flagged_years}))
        if not total:
            notes[nothing[0]] = rolemap.WORDING["nothing_empty"]
        elif not years:
            notes[nothing[0]] = rolemap.WORDING["nothing_clear" if total > 1 else "nothing_clear_one"].format(count=total)
        else:
            notes[nothing[0]] = rolemap.WORDING["nothing_blank" if blank else "nothing_flagged"].format(
                count=total, flagged=flagged, verb="comes" if flagged == 1 else "come", years=rolemap._years_text(years),
                band_words=f"the {total} anaesthetics" if total > 1 else "the one anaesthetic")
    return {"columns": columns, "rows": rows, "coverage": read["coverage"], "findings": read["findings"],
            "flagged_years": flagged_years, "counts": counts, "notes": notes, "band_years": by_year}


# The role-level shadow.

ROLE_TYPES = {"key": "VARCHAR", "local_key": "VARCHAR", "date": "DATE", "datetime": "TIMESTAMP", "number": "DOUBLE", "whole": "INTEGER", "flag": "INTEGER",
              "flag_or_empty": "INTEGER", "kind": "VARCHAR", "text": "VARCHAR"}


def planted():
    """The planted neonates, written once as rows of the three role views, with their expectations."""
    return json.loads(rolemap.PLANTED.read_text(encoding="utf-8"))


def planted_concepts():
    """The planted rows of the mapping views, with the drug events that name them: a key mapped, one unmapped, one
    ambiguous between two concepts, and one that the view does not list."""
    return json.loads(rolemap.PLANTED_CONCEPTS.read_text(encoding="utf-8"))


def generated_rows(seed=1, anaesthetics=400, first_year=2019, last_year=2025):
    """Plausible synthetic rows of the three views of the contract, from a seed: {view: [row, ...]}.

    A share of the anaesthetics are neonatal; every anaesthetic has a cuff mean every three to five minutes, and some
    have an arterial mean every minute; a few readings were not accepted, a few anaesthetics have no stop, and a few
    patients are test patients. Nothing here comes from any hospital."""
    rng = random.Random(seed)
    patients, rows_a, rows_r = [], [], []
    span = (dt.date(last_year, 12, 31) - dt.date(first_year, 1, 1)).days
    previous = None
    for number in range(1, anaesthetics + 1):
        day = dt.date(first_year, 1, 1) + dt.timedelta(days=rng.randrange(span))
        start = dt.datetime.combine(day, dt.time(rng.randrange(7, 18), rng.choice((0, 15, 30, 45))))
        if previous is not None and rng.random() < 0.08:
            patient, birth = previous
            start = max(start, dt.datetime.combine(birth, dt.time(8)) + dt.timedelta(days=1))
        else:
            neonate = rng.random() < 0.15
            age = rng.randrange(0, 28) if neonate else rng.randrange(28, 16 * 365)
            birth = start.date() - dt.timedelta(days=age)
            patient = f"P{number:06d}"
            age_days = (start.date() - birth).days
            death = None
            if rng.random() < (0.08 if age_days < 28 else 0.01):
                death = start.date() + dt.timedelta(days=rng.randrange(0, 150))
            patients.append([patient, birth.isoformat(), death.isoformat() if death else None, 1 if rng.random() < 0.02 else 0])
        previous = (patient, birth)
        key = f"A{number:06d}"
        minutes = rng.choice((30, 45, 60, 75, 90, 120, 150, 180, 240))
        stop = start + dt.timedelta(minutes=minutes)
        recorded_stop = None if rng.random() < 0.03 else (start - dt.timedelta(minutes=60) if rng.random() < 0.01 else stop)
        rows_a.append([key, patient, start.isoformat(sep=" "), recorded_stop.isoformat(sep=" ") if recorded_stop else None])
        age_days = (start.date() - birth).days
        level = rng.gauss(40, 6) if age_days < 28 else rng.gauss(55 + min(age_days / 365, 14) * 2, 8)
        cuff_every = rng.choice((3, 4, 5))
        moment = start - dt.timedelta(minutes=5 if rng.random() < 0.3 else 0)
        while moment <= stop:
            accepted = 0 if rng.random() < 0.02 else 1
            value = round(max(15, min(120, rng.gauss(level, 5))), 1) if accepted else round(rng.uniform(0, 20), 1)
            rows_r.append([key, "map_cuff", moment.isoformat(sep=" "), value, accepted])
            moment += dt.timedelta(minutes=cuff_every)
        if rng.random() < (0.4 if age_days < 28 else 0.15):
            moment = start + dt.timedelta(minutes=rng.randrange(5, 15))
            while moment <= stop:
                rows_r.append([key, "map_arterial", moment.isoformat(sep=" "), round(max(15, min(120, rng.gauss(level, 4))), 1), 1])
                moment += dt.timedelta(minutes=1)
        moment = start
        while moment <= stop:
            rows_r.append([key, "other", moment.isoformat(sep=" "), float(rng.randrange(80, 170)), 1])
            moment += dt.timedelta(minutes=5)
    # Each reading has its own key, as the hospital's own identifier of a charted value would be, and every value
    # here is a number, so none keeps its text.
    rows_r = [row + [f"R{number:07d}", None] for number, row in enumerate(rows_r, 1)]
    return {"role_patient": patients, "role_anaesthetic": rows_a, "role_reading": rows_r}


def role_shadow(seed=1, anaesthetics=400, with_planted=True, extra=None):
    """A DuckDB database that holds the role views and the mapping views as tables, the three that every map supplies
    filled from a seed and, with_planted, with the planted neonates and the planted rows of the mapping views and the
    drug events that name them, and the further views otherwise empty. extra, when given, is
    {view: [row, ...]} of further rows for any view. Returns the connection."""
    import duckdb
    con = duckdb.connect()
    model = rolemap.contract()
    shape = {view["name"]: view["columns"] for view in model["views"] + model.get("mapping_views", [])}
    for name, columns in shape.items():
        con.execute(f"CREATE TABLE {name} (" + ", ".join(f"{c['name']} {ROLE_TYPES[c['type']]}" for c in columns) + ")")
    rows = {name: [] for name in shape}
    if anaesthetics:
        rows.update(generated_rows(seed, anaesthetics))
    if with_planted:
        for cases in (planted(), planted_concepts()):
            for name in shape:
                if name in cases:
                    rows[name] = rows[name] + cases[name]["rows"]
    for name, more in (extra or {}).items():
        rows[name] = rows[name] + more
    for name, columns in shape.items():
        kinds_of = [ROLE_TYPES[c["type"]] for c in columns]
        for first in range(0, len(rows[name]), 2000):
            values = ",\n".join("(" + ", ".join(_role_literal(v, k) for v, k in zip(row, kinds_of)) + ")"
                                for row in rows[name][first:first + 2000])
            con.execute(f"INSERT INTO {name} VALUES {values}")
    return con


def _role_literal(value, kind):
    """A value of the role-level shadow as a DuckDB literal. The values are the module's own synthetic rows and the
    planted rows, and text is quoted with any quote doubled."""
    if value is None:
        return "NULL"
    if kind in ("INTEGER", "DOUBLE"):
        return repr(float(value)) if kind == "DOUBLE" else str(int(value))
    text = "'" + str(value).replace("'", "''") + "'"
    return f"CAST({text} AS {kind})" if kind in ("DATE", "TIMESTAMP") else text


def per_anaesthetic(run, audit_sql=None):
    """Each anaesthetic that the audit counts, with its minutes below 40 and whether the child died within 90 days,
    as {anaesthetic_key: (minutes, died)}. It reads the audit's banded common table expression, for checking the
    planted cases on synthetic rows."""
    tree = compiler.check_audit(audit_sql if audit_sql is not None else rolemap.AUDIT.read_text(encoding="utf-8"))
    final = sqlglot.parse_one("SELECT x.anaesthetic_key, x.minutes_below_40, x.died FROM banded x", dialect="tsql")
    final.set("with_" if "with_" in final.arg_types else "with", (tree.args.get("with_") or tree.args.get("with")).copy())
    _, rows = run(final.sql(dialect="tsql"))
    return {str(key): (None if minutes is None else float(minutes), int(died)) for key, minutes, died in rows}


# The hospital-shaped shadow of a world.

def hospital_run(world, conversion, roles_map, audit_sql=None, rows=500, scenarios=None):
    """Builds a world's hospital-shaped shadow with its planted scenarios, as convert.run does, and runs the audit and
    the counts through the map. The conversion's own report and its OMOP tables come back with the run, so that a
    caller can set the answer of an OMOP query on the same rows beside the audit's. Returns {"result", "report", "run",
    "conversion"}."""
    from . import convert
    converted, report = convert.run(world, conversion, rows, scenarios=scenarios)
    run = duckdb_runner(converted.con, converted.sandbox.date_columns, roles_map)
    return {"result": result(run, audit_sql), "report": report, "run": run, "conversion": converted}


# The map's shadow: invented rows of every role, written into tables shaped as the map's bindings name them, and the
# test of a hospital schema on them.

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
    for name, whole in state.data["roles"].items():
        spec = state.views[name]
        for _, role in rolemap.pathways(name, whole):
          for column in spec["key"]:
            binding = role["columns"][column].get("binding")
            if binding and not binding.get("path") and not binding.get("derive") and len(spec["key"]) == 1:
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
        if column["type"] == "local_key":
            return rolemap.concept_codes(self.state.data, column.get("mapping"))
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
        if op == "held_text":
            return value
        if op == "local_key":
            for code, key in step[1]:
                if key == value:
                    return code
            return NOT_IN_LIST
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

    def place(self, view_name, values, decoy=None, role=None):
        """Writes one row of a role view into the tables, as the bindings of one of its pathways name them (the first
        unless role is given). values is {column: value}."""
        state, source = self.state, self.source
        role = role or state.data["roles"][view_name]
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
            if not binding.get("path") and not binding.get("joined"):
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
        later.sort(key=lambda item: item[0]["name"] not in links)
        for column, binding in later:
            value = values.get(column["name"])
            if binding.get("joined"):
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
        for view_name, whole in self.state.data["roles"].items():
            for _, item in rolemap.role_items(view_name, whole):
                binding = item.get("binding") or {}
                steps = list(binding.get("path") or []) + [s for f in binding.get("filter") or [] for s in f["path"]]
                for step in steps:
                    for a, b in propose.step_pairs(step):
                        if step[0].upper() == table.upper():
                            joined.add(a.upper())
                        if step[2].upper() == table.upper():
                            joined.add(b.upper())
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
        for name, whole in self.state.data["roles"].items():
            for _, role in rolemap.pathways(name, whole):
                self.source.table(role["rows"]["binding"]["table"])
            for _, item in rolemap.role_items(name, whole):
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
        """The rows that a view should give, by the model of its bindings, over the shadow's tables: for each pathway,
        one for each row of its own table that passes its filters, with each join reaching at most one row and the
        pathway's source kind on each. A join that reaches more is what the comparison with the view's own rows finds.
        Returns (rows, 0); the second figure once counted rows left out by a time window, which no binding holds now."""
        found = []
        held = rolemap.pathways(view_name, self.state.data["roles"][view_name])
        for _, role in held:
            found += self._expected(view_name, role)
        if len(held) > 1:
            # A part with several pathways gives every key as text, as its view casts them.
            keys = [i for i, c in enumerate(self.state.views[view_name]["columns"]) if c["type"] == "key"]
            found = [tuple(_text(v) if i in keys else v for i, v in enumerate(row)) for row in found]
        return found, 0

    def _expected(self, view_name, role):
        state, source = self.state, self.source
        spec = state.views[view_name]
        base = role["rows"]["binding"]["table"]
        anchor = None
        for column in spec["columns"]:
            if column["name"] in rolemap.anchors(spec) and role["columns"][column["name"]].get("binding"):
                anchor = column["name"]
                break
        found = []
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
            out = {}
            for column in spec["columns"]:
                if column.get("per_pathway"):
                    out[column["name"]] = role.get("source_kind")
                    continue
                binding = role["columns"][column["name"]].get("binding")
                if not binding:
                    empty = propose._empty(column)
                    out[column["name"]] = None if empty == "NULL" else int(empty)
                    continue
                end = self.follow(ctx, binding["path"])
                raw = end.get(binding["column"].upper()) if end is not None else None
                if binding.get("joined"):
                    out[column["name"]] = self._joined(binding, raw)
                elif (binding.get("derive") or {}).get("form") == "key":
                    # A key made from several columns: each as text, joined by a hyphen, an empty one as nothing.
                    parts = [*(binding["derive"].get("with") or []), binding["column"]]
                    out[column["name"]] = "-".join(_text(end.get(part.upper())) or "" if end is not None else "" for part in parts)
                else:
                    out[column["name"]] = evaluate(propose.plan(column, binding, self.codes_of(view_name, column)), raw)
            if anchor is not None and out.get(anchor) is None:
                continue
            found.append(tuple(out[c["name"]] for c in spec["columns"]))
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
    if op == "held_text":
        return None if raw is None or _float(raw) is not None else _text(raw)
    if op == "local_key":
        if raw is None:
            return None
        return dict(step[1]).get(_text(raw), rolemap.UNLISTED)
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
    generated = generated_rows(seed, anaesthetics)
    cases = planted()
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
                                         "value": round(rng.gauss(60, 5), 1), "accepted": 1,
                                         "reading_key": f"{key}-{len(rows['role_reading'])}", "value_text": None})
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
                elif column.get("per_pathway"):
                    value = None
                elif kind == "local_key":
                    listed = sorted(set(rolemap.concept_codes(state.data, column.get("mapping")).values()))
                    value = rng.choice(listed + [rolemap.UNLISTED])
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


def _keyed(values, view, links, suffix):
    """A row with every text key of its own, other than a link, given a suffix, so that it is a row of its own."""
    found = dict(values)
    for column in view["key"]:
        if isinstance(found.get(column), str) and column not in links:
            found[column] = f"{found[column]}{suffix}"
    return found


def _cases(count):
    return _plural(count, "row", "rows")


def _error(error):
    """A database's error in plain words, without the name of the kind of error that DuckDB puts first."""
    text = " ".join(str(error).split("\n")[0].split())[:200]
    text = re.sub(r"^[A-Z][a-z]+ Error:\s*", "", text)[:160]
    return _clean(text, 160).rstrip(" .") or "an error"


COUNT_TITLES = {"coverage_by_year": "the anaesthetics of each year", "repeated_keys": "rows that appear twice",
                "readings_by_kind_and_year": "the readings of each kind and year",
                "readings_outside_anaesthetic": "readings outside an anaesthetic",
                "gaps_between_readings": "the gaps between readings"}


def _contract_problem(error, view, source):
    """A rule of a view that its SQL breaks, from rolemap's message, in plain words."""
    text = str(error).split(": ", 1)[-1].rstrip(".")
    for before, after in (("the catalogue", source), ("the contract asks for", "the description of the record asks for"),
                          ("the view gives", "the SQL gives"), ("a role view", "the SQL of a part")):
        text = text.replace(before, after)
    return _fit(WORDING["contract"].format(view=rolemap.view_title(view, False), problem=rolemap.plain(text)))


def run_check(state, seed=SHADOW_SEED, anaesthetics=SHADOW_ANAESTHETICS):
    """Builds the shadow of the map that state holds and checks it. Returns {"problems": [sentence], "notes":
    [sentence], "about": {sentence: binding}, "views": number, "seconds": number}. Every sentence names the part of the
    record in plain words, about gives the binding (role_view rows or role_view.column) that each concerns, and none
    quotes the dictionary."""
    began = time.perf_counter()
    problems, notes = [], []
    about = {}
    title = lambda name: rolemap.view_title(name, False)  # noqa: E731
    data = copy.deepcopy(state.data)
    kinds = data["kinds"]
    missing = [k for k in rolemap.MEAN_KINDS if not (kinds.get(k) or {}).get("codes")]
    for number, kind in enumerate(missing):
        kinds.setdefault(kind, {})["codes"] = [str(9901 + number)]
    if missing:
        plain = {"map_arterial": "the mean arterial pressure from an arterial line", "map_cuff": "the mean pressure from a cuff"}
        notes.append(WORDING["placeholders"].format(kinds=_and([plain.get(k, k) for k in missing]), them="it an invented code" if len(missing) == 1 else "each an invented code"))
    vocabularies = {name: state._vocabulary_codes(name) for name in data["roles"]}
    views = {}
    for name in data["roles"]:
        role = dict(data["roles"][name])
        role["_date"] = ""
        views[name] = propose.view_sql(name, role, kinds, state.model, vocabularies[name], propose.concepts_of(data))
    for name, sql in views.items():
        source = "the dictionary"
        try:
            rolemap.check_view(sql, name, state.dictionary)
            if state.catalogue is not None:
                source = "the database"
                rolemap.check_view(sql, name, state.catalogue)
        except rolemap.MapError as error:
            problems.append(_contract_problem(error, name, source))
            about[problems[-1]] = f"{name} rows"
    held = copy.copy(state)
    held.data = data
    shadow = Shadow(held, views, kinds, vocabularies)
    shadow.register()
    rows = role_rows(held, seed, anaesthetics)
    order = [v for v in ("role_patient", "role_anaesthetic") if v in data["roles"]] + \
            [v for v in data["roles"] if v not in ("role_patient", "role_anaesthetic")]
    for name in order:
        links = {l["column"] for l in state.views[name].get("links", [])}
        for at, (_, role) in enumerate(rolemap.pathways(name, data["roles"][name])):
            # Each further pathway reads a table of its own, so its rows carry keys of their own.
            for values in rows.get(name, []):
                shadow.place(name, _keyed(values, state.views[name], links, f"P{at}") if at else values, role=role)
            if role["rows"]["binding"].get("filter"):
                for number, values in enumerate(rows.get(name, [])[:3]):
                    decoy = _keyed(values, state.views[name], links, f"D{number}" + (f"P{at}" if at else ""))
                    shadow.place(name, decoy, decoy=True, role=role)
    shadow.pair_encounters()
    con, date_columns = shadow.database()
    try:
        return _run_views(state, data, views, order, shadow, con, date_columns, problems, notes, about, began)
    finally:
        # The database is in memory and holds every table of the shadow; closing it gives the memory back, which matters
        # in the browser, where a sitting runs many checks.
        con.close()


def _run_views(state, data, views, order, shadow, con, date_columns, problems, notes, about, began):
    title = lambda name: rolemap.view_title(name, False)  # noqa: E731
    results = {}
    for name in order:
        spec = state.views[name]
        try:
            statements = to_duckdb(views[name], date_columns)
            cursor = con.execute(statements[0])
            got = cursor.fetchall()
        except Exception as error:  # noqa: BLE001 - any failure to run is the finding
            problems.append(WORDING["runs_not"].format(view=title(name), error=_error(error)))
            about[problems[-1]] = f"{name} rows"
            continue
        results[name] = got
        expected, _ = shadow.expected(name)
        for text, concerns in _compare(name, spec, got, expected, state):
            problems.append(text)
            about[text] = concerns
    if "role_anaesthetic" in results:
        held_keys = {_normal(r[0]) for r in results["role_anaesthetic"]}
        for name, got in results.items():
            columns = [c["name"] for c in state.views[name]["columns"]]
            if name == "role_anaesthetic" or "anaesthetic_key" not in columns:
                continue
            at = columns.index("anaesthetic_key")
            orphans = sum(1 for r in got if r[at] is not None and _normal(r[at]) not in held_keys)
            if orphans:
                notes.append(WORDING["orphans"].format(count=_plural(orphans, "row", "rows").split(" ", 1)[0], view=title(name)))
                about[notes[-1]] = f"{name}.anaesthetic_key"
    lacking = [v for v in rolemap.views() if v not in results]
    roles_map = {"data": data, "views": views}
    if not lacking:
        run = duckdb_runner(con, date_columns, roles_map)
        for count_name, item in rolemap.count_queries(1, 1).items():
            try:
                run(item["sql"])
            except Exception:  # noqa: BLE001
                problems.append(WORDING["count_fails"].format(name=COUNT_TITLES.get(count_name, count_name.replace("_", " "))))
        try:
            found = per_anaesthetic(run)
            problems += _neonates(found)
        except Exception as error:  # noqa: BLE001
            problems.append(WORDING["neonatal_fails"].format(error=_error(error)))
    else:
        problems.append(WORDING["neonatal_missing"].format(views=_and([title(v) for v in lacking])))
    return {"problems": list(dict.fromkeys(problems)), "notes": list(dict.fromkeys(notes)), "views": len(views),
            "about": about, "seconds": round(time.perf_counter() - began, 1)}


def _compare(name, spec, got, expected, state):
    """The ways in which a view's rows differ from what its bindings should give, as [(sentence, binding)]."""
    problems = []
    view = rolemap.view_title(name, False)
    rows = f"{name} rows"
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
                problems.append((WORDING["doubled_reading"].format(view=view, count=f"{doubled:,}"), f"{name}.anaesthetic_key"))
            else:
                link = columns[links[0]]["name"] if links else "key"
                target = next((l["to"].split(".")[0] for l in spec.get("links", []) if l["column"] == link), "row")
                problems.append((WORDING["doubled"].format(view=view, count=f"{doubled:,}", link=rolemap.column_title(name, link),
                                                           target=THING.get(target, target.replace("role_", "").replace("_", " "))),
                                 f"{name}.{link}" if links else rows))
        if other:
            problems.append((WORDING["extra"].format(view=view, count=f"{other:,}"), rows))
    if missing:
        problems.append((WORDING["missing"].format(view=view, count=f"{sum(missing.values()):,}", total=f"{sum(wanted.values()):,}"), rows))
    keys = [i for i, c in enumerate(columns) if c["name"] in spec["key"]]
    repeated = Counter(tuple(_normal(r[i]) for i in keys) for r in got)
    count = sum(1 for n in repeated.values() if n > 1)
    if count:
        problems.append((WORDING["keys"].format(count=f"{count:,}", view=view), f"{name}.{spec['key'][0]}" if len(spec["key"]) == 1 else rows))
    allowed = _allowed_kinds(state, name)
    for i, column in enumerate(columns):
        values = [r[i] for r in got]
        if column["type"] == "flag":
            empty = sum(1 for v in values if v is None)
            if empty:
                problems.append((WORDING["flag_empty"].format(subject=rolemap.plain_about(f"{name}.{column['name']}", True),
                                                              count=_cases(empty)), f"{name}.{column['name']}"))
        wrong = next((v for v in values if v is not None and not _suits(column["type"], v, allowed.get(column["name"]))), None)
        if wrong is not None:
            problems.append((WORDING["type"].format(subject=rolemap.plain_about(f"{name}.{column['name']}", True),
                                                    found=_found_words(wrong), wanted=TYPE_WORDS[column["type"]]),
                             f"{name}.{column['name']}"))
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
    for case in planted()["expectations"]:
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


# The correctness report of an execution package: the question run on the role shadow with its planted cases.

# The most rows of the answer on made-up rows that expected-output.json keeps; rows_total says how many there were.
EXPECTED_ROWS = 1000


def expected_output(audit_sql, blank):
    """The audit run on the role-level shadow with the planted cases."""
    con = role_shadow()
    try:
        run = duckdb_runner(con)
        per = None
        try:
            found = result(run, audit_sql, blank=blank)
        except rolemap.MapError:
            # A question other than the neonatal audit has no bands to read by year, so its rows are read as they are,
            # and the planted neonates, whose expected answers are the neonatal audit's, do not apply to it.
            columns, rows = run(audit_sql)
            found = {"columns": columns, "rows": rows, "coverage": None, "notes": []}
        else:
            try:
                per = per_anaesthetic(run, audit_sql)
            except rolemap.MapError:
                per = None
    finally:
        con.close()
    cases = []
    if per is not None:
        # The expected answers are held out of the public workspace, where the planted rows come without them.
        for case in planted().get("expectations", []):
            held = per.get(case["anaesthetic_key"])
            if not case["counted"]:
                ok = held is None
            else:
                ok = held is not None and held[1] == case["died_within_90_days"] and (
                    (held[0] is None and case["minutes_below_40"] is None)
                    or (held[0] is not None and case["minutes_below_40"] is not None and abs(held[0] - case["minutes_below_40"]) < 1e-6))
            cases.append({"anaesthetic_key": case["anaesthetic_key"], "says": case["says"], "counted": case["counted"],
                          "expected": {"minutes_below_40": case["minutes_below_40"], "died_within_90_days": case["died_within_90_days"]},
                          "found": None if held is None else {"minutes_below_40": held[0], "died_within_90_days": held[1]},
                          "matches": ok})
    return {"made_up": True,
            "says": "These figures come from made-up rows: the role-level shadow, with generated anaesthetics from 2019 to 2025 and the planted cases. They show the shape of the answer and nothing about the hospital. The period of the package is not applied to them, and their counts are not blanked, because no row belongs to a child.",
            "columns": found["columns"], "rows": [list(r) for r in found["rows"][:EXPECTED_ROWS]],
            "rows_total": len(found["rows"]), "coverage": found["coverage"],
            "notes": found["notes"], "planted": cases, "planted_match": all(c["matches"] for c in cases) if cases else None}


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n"


def write_correctness(audit_sql, blank, path):
    """Runs the question on the role shadow with its planted cases, as expected_output does, and writes the report to
    path, from which the package reads it. Returns the path."""
    from pathlib import Path
    path = Path(path)
    path.write_text(_json(expected_output(audit_sql, blank)), encoding="utf-8")
    return path


# The tests that a sitting of screen 1 owes, run here and handed back to the sitting as data.

def _baseline(sitting):
    """Runs the test of the map as it stands where the sitting has no current one, and hands it to the sitting."""
    if sitting.baseline_owed():
        sitting.record_baseline(run_check(sitting))


def test(sitting, date=None):
    """Runs the test on made-up rows of the map as it stands, and records it in the sitting as a test run, which sets the
    tested dimension of every binding, link and code translation. Returns the journal entry of the run."""
    _baseline(sitting)
    return sitting.test(date)


def run_owed_test(sitting, date=None):
    """Runs the test on made-up rows where the hospital schema has changed since its last test, as a save does. Returns
    the journal entry of the run, or None where no test was owed."""
    return test(sitting, date) if sitting.test_owed() else None


def check_model(sitting, date=None):
    """The check of the map as it stands, with no change, which the journal records as a test run."""
    if sitting.data is not None:
        _baseline(sitting)
    return sitting.check_model(date)


def correction_check(sitting, correction):
    """Tests a correction on invented rows: the whole map with the change, against the map as it stands, and hands both
    results to the sitting, which reports what the change breaks or mends."""
    trial = sitting.correction_trial(correction)
    began = time.perf_counter()
    _baseline(sitting)
    after = run_check(trial)
    return sitting.record_check(correction, after, round(time.perf_counter() - began, 1))


def correction_keep(sitting, correction, although=False, reason="", date=None, actor=None):
    """Keeps a correction, testing it first where the sitting holds no test of it, as the sitting did before it kept one."""
    if not sitting.check_recorded(correction):
        correction_check(sitting, correction)
    return sitting.correction_keep(correction, although, reason, date, actor)


def save(sitting, date=None):
    """Saves a new version of the hospital schema, running first the test on made-up rows that it owes. Returns
    {path: bytes}."""
    run_owed_test(sitting, date)
    return sitting.save(date)


def save_zip(sitting, date=None):
    """save() as the bytes of the one file, whose name the sitting's file_name() then gives."""
    run_owed_test(sitting, date)
    return sitting.save_zip(date)
