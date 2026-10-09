"""Transplants the routes of a reference conversion, from its lineage, into a conversion folder of our own.

The lineage (compare.py) says, for each OMOP table that a reference conversion writes, which source tables it reads,
how it joins them, which columns its filters test, and which source columns and expression shape fill each field, with
every literal replaced by its type. This module writes one step file for each of those tables that follows the
conventions of convert.py: one SELECT in T-SQL, naming its columns as the OMOP fields, which reads the source tables
and the OMOP tables already written as omop.<table>.

    python -m schemalyser.compare transplant --lineage lineage.json --dictionary DICT.csv --out FOLDER
                                             [--tables TABLES.csv] [--targets T1,T2] [--existing CONVERSION]

How each step is made.

    The step starts from the table that holds its rows: the table behind the target's own identifier. It joins the
    other tables along the lineage's joins, and along the joins of the OMOP tables that the target reads through, as
    an inner join where a required field or a filter needs the table and as a left join otherwise. A join to a table
    whose key the join does not cover would repeat rows, so it is not made, and the fields behind it are left empty.

    A field whose expression the lineage gives in full is written from it, with each source column read through its
    table's alias and each length of text set to 50. An identifier of another OMOP table, such as person_id, is looked
    up in that table by its source value, where the lineage says that the source value is the same column. A field
    that is a bare literal in the lineage is held by a named placeholder, {{decision:TARGET.FIELD}}, and written as the
    concept 0 for a concept field or as NULL otherwise. Any other field whose expression cannot be reproduced, because
    it reads a mapping table, an OMOP table that the lineage could not follow, or an ambiguous column, is written as
    NULL (or as the concept 0 for a concept field) with a comment that gives the lineage's expression shape.

    A filter without a literal is applied. A filter whose literals the lineage has redacted is held back as a comment
    in the step, with each literal replaced by a placeholder, {{decision:TARGET.filter_N}} (or filter_N_M where the
    filter has several), so that the step runs as it stands and the owner writes the filter in once its values are
    known. decisions.json lists every placeholder with its column, its type and a sentence that asks for its value.

    Every table and column is checked against the dictionary, which is the public specification's list of columns.
    A table or column that the dictionary lacks is reported and the step is marked incomplete, and nothing is
    invented in its place.

Every step that the transplant writes is marked in conversion.json as a step on the direct route, from the source tables,
with the reference it came from and the reason, and with no review, because no person has reviewed it; the release
script refuses such a step until the owner records the review. The folder also receives draft.json, which marks the
whole conversion as a draft, so that the runner reports it as one and the release script refuses it until the owner
has reviewed every step and removed the file.

Beside the steps the folder receives conversion.json, catalogue.csv with the tables and columns that the steps read in
the layout of a world's catalogue, so that the sandbox can build rows for them, decisions.json,
transplant-report.json with transplant-report.md, and the run's counts alone in transplant-run-summary.json and
transplant-run-summary.md, written by summaries.py, which are the only part of the folder that names nothing. With --existing, the existing conversion is copied into the folder,
an existing step for a target is kept, and the transplanted step is written beside it as TARGET_from_reference.sql and
offered as that step's alternative, so that the owner chooses.

The lineage names the reference's tables and columns, and so does everything written here, which stays wherever the
reference is kept.
"""
import csv
import datetime as dt
import io
import json
import re
import shutil
from collections import deque
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import convert, summaries

REPORT_FORMAT = "schemalyser-transplant/1"
DECISIONS_FILE = "decisions.json"
CATALOGUE_FILE = "catalogue.csv"
REPORT_FILE = "transplant-report"
DRAFT_FILE = convert.DRAFT_FILE
SUFFIX = "_from_reference"
TEXT_LENGTH = 50
LITERAL = re.compile(r"<(int|str|float)>")
SLOT = re.compile(r"__literal_(\d+)__")
LENGTHS = re.compile(r"\b(N?VARCHAR|N?CHAR)\(<int>\)", re.IGNORECASE)
# The source value by which each OMOP table that another table refers to can be looked up.
SOURCE_VALUES = {"person": "person_source_value", "visit_occurrence": "visit_source_value",
                 "visit_detail": "visit_detail_source_value", "provider": "provider_source_value",
                 "care_site": "care_site_source_value", "location": "location_source_value"}
# The order in which the core's tables are written, so that each is written before the tables that look it up.
CORE_ORDER = ("location", "care_site", "person", "provider", "visit_occurrence", "observation_period", "death")
KINDS = {"int": "whole number", "float": "number", "str": "text"}

WORDING = {
    "header": "-- {target}: transplanted by Schemalyser from a reference conversion's lineage on {date}.",
    "header_incomplete": "-- This step is incomplete, because {reasons}. Nothing has been invented in its place.",
    "header_review": "-- No person has reviewed this step yet. The lineage does not say whether a join is inner or outer, so each join "
                     "is written as an inner join where a required field or a filter needs its table, and as a left join otherwise.",
    "header_existing": "-- The existing conversion already writes {target} in {file}, which is kept. This step is offered beside it as "
                       "an alternative, so that the owner chooses between them.",
    "header_lookup": "-- The identifiers of {tables} are looked up by their source values in the OMOP tables already written.",
    "header_held": "-- {count} of the reference's filters are held back below as comments, because their values are placeholders "
                   "that decisions.json lists. Each is written into the step once its values are known.",
    "header_held_one": "-- One of the reference's filters is held back below as a comment, because its values are placeholders "
                       "that decisions.json lists. It is written into the step once its values are known.",
    "header_not_reproduced": "-- The reference also tests {tests}, which this step does not reproduce, because {why}.",
    "fan_out": "-- {table} is not joined, because the join on {columns} does not cover its key and would repeat rows.",
    "unreached": "-- {table} is not joined, because the lineage gives no join that reaches it from {root}.",
    "through_fan_out": "-- {table} is not joined, because the lineage reaches it only through {other}, which is not joined.",
    "lookup_mapping": "-- The reference looks up {columns} in the mapping table, and the lineage does not say under which vocabulary.",
    "field_decision": "{{{{decision:{name}}}}}: the reference writes a literal of type {kind} here.",
    "field_shape": "not reproduced from the lineage's expression {shape}",
    "field_missing": "not reproduced, because the dictionary lacks {names}",
    "field_unreached": "not reproduced, because {names} is not joined",
    "field_no_owner": "not reproduced, because no step of this conversion writes {table} from {column}",
    "field_absent": "the reference does not fill this required field",
    "held_filter": "--   AND {test}",
    "question": "Please give the {kind} that takes the place of {{{{decision:{name}}}}} in the reference's test of {columns} for "
                "{target}, so that the filter can be written into the step.",
    "question_field": "Please give the {kind} that {target}.{field} should hold, in place of {{{{decision:{name}}}}}.",
    "reason_missing_table": "the dictionary does not list the table {names}",
    "reason_missing_tables": "the dictionary does not list the tables {names}",
    "reason_missing_columns": "the dictionary does not list {names}",
    "reason_no_root": "the table that holds its rows is not in the dictionary",
    "why_ambiguous": "the lineage could not tell which column the test reads",
    "why_unreached": "the column it tests is not joined",
    "why_missing": "the dictionary lacks the column it tests",
    "why_other": "the lineage does not give it in a form that can be written again",
    "skipped_unknown": "The lineage holds no table of that name.",
    "skipped_not_cdm": "The table is not one of CDM 5.4, so the runner cannot write it.",
    # The report for a person.
    "md_title": "# Transplant from a reference conversion",
    "md_private": "Everything below names the reference's tables and columns, so it stays wherever the reference is kept.",
    "md_counts": "Schemalyser wrote {written}, of which {incomplete} incomplete. It could not write {unwritten}, and it skipped {skipped}. The steps map {mapped} "
                 "from the lineage and leave {empty} empty or at the concept 0, and they hold {placeholders} that decisions.json lists.",
    "md_missing": "The dictionary does not list {count} of the tables and columns that the reference reads: {names}.",
    "md_missing_none": "The dictionary lists every table and column that the transplanted steps read.",
    "md_written": "Schemalyser wrote this step. It maps {mapped}, leaves {empty} empty or at the concept 0, and holds {placeholders}.",
    "md_incomplete": "Schemalyser wrote this step, and it is incomplete, because {reasons}. It maps {mapped}, leaves {empty} empty or at the concept 0, and holds {placeholders}.",
    "md_unwritten": "Schemalyser has not written this step, because {reasons}.",
    "md_skipped": "Schemalyser skipped this table. {reason}",
    "md_alternative": "The existing conversion already writes this table in {file}, so the transplanted step is offered beside it as {alternative}.",
    "md_empty_fields": "The fields left empty or at the concept 0 are {fields}.",
}


# The route record of a transplanted step, and the draft marker of the folder.
ROUTE_REASON = "The step was transplanted from the reference's lineage, and no person has reviewed it yet."
DRAFT_SAYS = ("This conversion was transplanted from a reference conversion's lineage. No person has reviewed its steps, so the "
              "release script refuses the folder until the owner has reviewed each step, recorded the review in "
              "conversion.json, and removed this file.")


def _route(reference):
    return {"route": "direct", "reference": reference, "reason": ROUTE_REASON}


class TransplantError(ValueError):
    """An input that the transplant cannot work with."""


def _and(items):
    items = list(items)
    if not items:
        return "nothing"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _n(count, one, many):
    return f"{count} {one if count == 1 else many}"


def _are(count):
    return "none is" if count == 0 else f"{count} is" if count == 1 else f"{count} are"


def _code(items):
    return ", ".join(f"`{i}`" for i in items) if items else "none"


def _split(name):
    table, _, column = name.partition(".")
    return table, column


def _is_base(name):
    """Whether a lineage reference names a source column, which the lineage writes as TABLE.COLUMN in capitals."""
    table, column = _split(name)
    return bool(column) and table.isupper()


# What the CDM says of each table.

def _fields():
    return {table: [(name, required, kind) for name, required, kind in fields] for table, fields in convert.cdm_fields().items()}


def _keys():
    """The primary key field of each table of CDM 5.4, and the table that each such field identifies."""
    keys = {}
    for row in convert._definitions():
        if row["primary_key"] == "Y":
            keys[row["table"]] = row["field"]
    owners = {field: table for table, field in keys.items()}
    return keys, owners


# Expressions.

def _rewrite(text, aliases):
    """The lineage's expression written as T-SQL over the step's aliases: (sql, literal kinds, problem). sql holds a
    slot __literal_N__ for each redacted literal. problem is None, or ("shape" | "unreached", detail)."""
    if "{" in text or "|" in text:
        return None, [], ("shape", text)
    held = LENGTHS.sub(lambda m: f"{m.group(1)}({TEXT_LENGTH})", text)
    kinds = []

    def slot(match):
        kinds.append(match.group(1))
        return f"__literal_{len(kinds) - 1}__"
    held = LITERAL.sub(slot, held)
    try:
        tree = sqlglot.parse_one(held)
    except sqlglot.errors.SqlglotError:
        return None, kinds, ("shape", text)
    columns = [tree] if isinstance(tree, exp.Column) else list(tree.find_all(exp.Column))
    for column in columns:
        if SLOT.fullmatch(column.name):
            continue
        table = column.table
        if not table or not table.isupper():
            return None, kinds, ("shape", text)
        alias = aliases.get(table)
        if alias is None:
            return None, kinds, ("unreached", table)
        column.set("table", exp.to_identifier(alias))
    sql = tree.sql(dialect="tsql")
    return sql, kinds, None


def _has_aggregate(sql):
    tree = sqlglot.parse_one(SLOT.sub("0", sql), dialect="tsql")
    return any(not isinstance(node.parent, exp.Window) and node.find_ancestor(exp.Window) is None
               for node in tree.find_all(exp.AggFunc))


def _outside_aggregates(sql):
    """The column references of an expression that lie outside every aggregate, as text, for its GROUP BY."""
    tree = sqlglot.parse_one(SLOT.sub("0", sql), dialect="tsql")
    found = []
    for column in ([tree] if isinstance(tree, exp.Column) else tree.find_all(exp.Column)):
        if column.find_ancestor(exp.AggFunc) is None:
            text = column.sql(dialect="tsql")
            if text not in found:
                found.append(text)
    return found


# The join graph.

class _Graph:
    def __init__(self, dictionary):
        self.dictionary = dictionary
        self.edges = {}       # table -> [(other, [(column here, column there)])]

    def add(self, pairs):
        """Adds a join of two tables on the pairs of columns, [(A.a, B.b), ...], each pair in the lineage's order."""
        tables = {}
        for left, right in pairs:
            (a, ca), (b, cb) = _split(left), _split(right)
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            pair = (ca, cb) if a < b else (cb, ca)
            tables.setdefault(key, []).append(pair)
        for (a, b), joined in tables.items():
            for here, there, columns in ((a, b, joined), (b, a, [(y, x) for x, y in joined])):
                held = self.edges.setdefault(here, [])
                found = next((item for item in held if item[0] == there), None)
                if found is None:
                    held.append((there, list(columns)))
                else:
                    found[1].extend(c for c in columns if c not in found[1])

    def to_one(self, table, columns):
        """Whether a join to table on its columns reaches at most one of its rows: the columns cover its key. A table whose
        key the dictionary does not give is taken to be reached at most once."""
        held = self.dictionary.table(table)
        if held is None:
            return False
        key = {k.upper() for k in held.primary_key()}
        return not key or key <= {c.upper() for c in columns}

    def tree(self, root):
        """A spanning tree from root over the joins that reach one row: {table: (parent, [(parent column, column)])},
        and the tables that a join reaches only by repeating rows, as {table: (parent, columns)}."""
        found, fan = {root: (None, [])}, {}
        queue = deque([root])
        while queue:
            table = queue.popleft()
            for other, columns in sorted(self.edges.get(table, []), key=lambda item: item[0]):
                if other in found:
                    continue
                if self.dictionary.table(other) is None:
                    continue
                if not self.to_one(other, [c for _, c in columns]):
                    fan.setdefault(other, (table, columns))
                    continue
                found[other] = (table, columns)
                queue.append(other)
        return found, {t: v for t, v in fan.items() if t not in found}


def _alias(table, used):
    parts = [p for p in table.lower().split("_") if p]
    base = "".join(p[0] for p in parts) or "t"
    if base in ("as", "on", "or", "in", "is", "by", "if", "of", "to", "go", "do") or not base[0].isalpha():
        base = "t" + base
    alias, n = base, 1
    while alias in used:
        n += 1
        alias = f"{base}{n}"
    used.add(alias)
    return alias


# One step.

class _Step:
    def __init__(self, target, entry, lineage, dictionary, keys, owners, written, date):
        self.target, self.entry, self.lineage, self.dictionary = target, entry, lineage, dictionary
        self.keys, self.owners, self.written, self.date = keys, owners, written, date
        self.fields = {name: (required, kind) for name, required, kind in _fields()[target]}
        self.missing, self.reasons, self.notes = [], [], []
        self.lines, self.placeholders, self.held, self.mapped, self.empty, self.decided = [], [], [], [], [], []
        self.needs = set()        # the OMOP tables whose identifiers the step looks up
        self.sql = None

    # Checking against the dictionary.

    def references(self):
        names = set(self.entry.get("source_tables") or [])
        for item in (self.entry.get("fields") or {}).values():
            names |= {n for n in item.get("columns") or [] if _is_base(n)}
        for join in self.entry.get("joins") or []:
            names |= {n for pair in join["on"] for n in pair if _is_base(n)}
        for item in self.entry.get("filters") or []:
            names |= {n for n in item.get("columns") or [] if _is_base(n)}
        return names

    def check(self):
        tables, columns = set(), set()
        for name in sorted(self.references()):
            table, column = _split(name) if "." in name else (name, "")
            held = self.dictionary.table(table)
            if held is None:
                tables.add(table)
            elif column and held.column(column) is None:
                columns.add(name)
        self.missing = sorted(tables) + sorted(columns)
        if tables:
            self.reasons.append(WORDING["reason_missing_table" if len(tables) == 1 else "reason_missing_tables"].format(names=_and(sorted(tables))))
        if columns:
            self.reasons.append(WORDING["reason_missing_columns"].format(names=_and(sorted(columns))))

    def known(self, name):
        table, column = _split(name)
        held = self.dictionary.table(table)
        return held is not None and (not column or held.column(column) is not None)

    # The tables and their joins.

    def _graph(self):
        graph = _Graph(self.dictionary)
        seen, queue = {self.target}, deque([self.target])
        while queue:
            entry = self.lineage["targets"].get(queue.popleft()) or {}
            for join in entry.get("joins") or []:
                graph.add([tuple(pair) for pair in join["on"] if all(_is_base(n) and self.known(n) for n in pair)])
            for other in entry.get("other_omop_tables") or []:
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        return graph

    def _root(self, graph):
        own = (self.entry.get("fields") or {}).get(self.keys.get(self.target, ""), {})
        behind = [t for t in dict.fromkeys(_split(n)[0] for n in own.get("columns") or [] if _is_base(n))]
        candidates = [t for t in behind if self.dictionary.table(t) is not None]
        if behind and not candidates:
            return None     # the table behind the target's own identifier is not in the dictionary
        if not candidates:
            counts = {}
            for item in (self.entry.get("fields") or {}).values():
                for name in item.get("columns") or []:
                    if _is_base(name) and self.dictionary.table(_split(name)[0]) is not None:
                        counts[_split(name)[0]] = counts.get(_split(name)[0], 0) + 1
            candidates = sorted(counts, key=lambda t: (-counts[t], t))
        if not candidates:
            candidates = [t for t in self.entry.get("source_tables") or [] if self.dictionary.table(t) is not None]
        if not candidates:
            return None
        # The table from which every other candidate is reached at most once holds the rows.
        best = max(candidates, key=lambda t: (sum(1 for c in candidates if c in graph.tree(t)[0]), -candidates.index(t)))
        return best

    # Writing.

    def build(self):
        self.check()
        graph = self._graph()
        root = self._root(graph)
        if root is None:
            self.reasons.append(WORDING["reason_no_root"])
            return
        tree, fan = graph.tree(root)
        fields_ = self.entry.get("fields") or {}
        own_key = self.keys.get(self.target)
        # The tables that the step needs: those behind its fields and filters, and those on the way to them.
        wanted, required_tables = set(), set()
        for name, item in fields_.items():
            tables = {_split(n)[0] for n in item.get("columns") or [] if _is_base(n)}
            wanted |= tables
            if self.fields.get(name, (False, ""))[0]:
                required_tables |= tables
        for item in self.entry.get("filters") or []:
            tables = {_split(n)[0] for n in item.get("columns") or [] if _is_base(n)}
            wanted |= tables
            required_tables |= tables
        kept = set()
        for table in wanted & set(tree):
            while table is not None and table not in kept:
                kept.add(table)
                table = tree[table][0]
        kept.add(root)

        def below(table):
            return {t for t in kept if self._ancestor(tree, t, table)}
        used = set()
        aliases = {root: _alias(root, used)}
        order = [t for t in tree if t in kept and t != root]
        for table in order:
            aliases[table] = _alias(table, used)
        for table in sorted(wanted - set(tree)):
            if self.dictionary.table(table) is None:
                continue
            if table in fan:
                parent, columns = fan[table]
                self.notes.append(WORDING["fan_out"].format(table=table, columns=_and(f"{parent}.{p} = {table}.{c}" for p, c in columns)))
            elif self._through(graph, fan, table):
                self.notes.append(WORDING["through_fan_out"].format(table=table, other=self._through(graph, fan, table)))
            else:
                self.notes.append(WORDING["unreached"].format(table=table, root=root))
        lookups = [n.split(":", 1)[1] for n in self.entry.get("lookups") or [] if n.startswith("lookup:")]
        if lookups:
            self.notes.append(WORDING["lookup_mapping"].format(columns=_and(lookups)))
        # The fields, in the CDM's order.
        lookup_joins, lines, group = {}, [], []
        aggregated = False
        names = [f for f in self.fields if f in fields_ or f == own_key or (self.fields[f][0] and f not in fields_)]
        for field in names:
            required, kind = self.fields[field]
            item = fields_.get(field)
            expression, comment, state = self._field(field, item, required, aliases, lookup_joins)
            if state == "mapped":
                self.mapped.append(field)
                if _has_aggregate(expression):
                    aggregated = True
                else:
                    group += [c for c in _outside_aggregates(expression) if c not in group]
            elif state == "decision":
                self.decided.append(field)
            else:
                self.empty.append(field)
            lines.append((f"{expression} AS {field}", comment))
        # The filters.
        applied, held, not_reproduced, why = [], [], [], set()
        for number, item in enumerate(self.entry.get("filters") or [], start=1):
            test = item["test"]
            if any(not self.known(n) for n in item.get("columns") or [] if _is_base(n)):
                not_reproduced.append(test)
                why.add(WORDING["why_missing"])
                continue
            sql, kinds, problem = _rewrite(test, aliases)
            if problem is not None:
                not_reproduced.append(test)
                why.add(WORDING["why_ambiguous"] if problem[0] == "shape" else WORDING["why_unreached"])
                continue
            if not kinds:
                applied.append(sql)
                continue
            names_ = [f"{self.target}.filter_{number}"] if len(kinds) == 1 else \
                [f"{self.target}.filter_{number}_{i}" for i in range(1, len(kinds) + 1)]
            text = SLOT.sub(lambda m: "{{decision:" + names_[int(m.group(1))] + "}}", sql)
            held.append(WORDING["held_filter"].format(test=text))
            for name, literal in zip(names_, kinds):
                self.placeholders.append({
                    "placeholder": "{{decision:" + name + "}}", "name": name, "target": self.target, "kind": "filter",
                    "column": _and(item.get("columns") or []), "columns": item.get("columns") or [], "type": literal,
                    "test": test, "value": None,
                    "question": WORDING["question"].format(kind=KINDS[literal], name=name, columns=_and(item.get("columns") or []),
                                                           target=self.target.upper())})
        # Only the tables that the step reads are joined, with every table on the way to them.
        text = "\n".join([t for t, _ in lines] + applied + held + [c for _, c, _ in lookup_joins.values()])
        read = {t for t in order if re.search(rf"\b{re.escape(aliases[t])}\.", text)}
        needed = set()
        for table in read:
            while table is not None and table != root and table not in needed:
                needed.add(table)
                table = tree[table][0]
        order = [t for t in order if t in needed]
        joins = []
        for table in order:
            parent, columns = tree[table]
            kind = "JOIN" if below(table) & required_tables else "LEFT JOIN"
            on = " AND ".join(f"{aliases[table]}.{c} = {aliases[parent]}.{p}" for p, c in columns)
            joins.append(f"{kind} {table} {aliases[table]} ON {on}")
        for table, (alias, column, needed_) in lookup_joins.items():
            joins.append(f"{'JOIN' if needed_ else 'LEFT JOIN'} {convert.OMOP_SCHEMA}.{table} {alias} ON "
                         f"{alias}.{SOURCE_VALUES[table]} = CAST({column} AS varchar({TEXT_LENGTH}))")
        header = [WORDING["header"].format(target=self.target.upper(), date=self.date)]
        if self.reasons:
            header.append(WORDING["header_incomplete"].format(reasons=_and(self.reasons)))
        header.append(WORDING["header_review"])
        if lookup_joins:
            header.append(WORDING["header_lookup"].format(tables=_and(sorted(t.upper() for t in lookup_joins))))
        if held:
            header.append(WORDING["header_held_one" if len(held) == 1 else "header_held"].format(count=len(held)))
        if not_reproduced:
            header.append(WORDING["header_not_reproduced"].format(tests=_and(not_reproduced), why=_and(sorted(why))))
        header += self.notes
        body = ["SELECT " + self._select(lines), f"FROM   {root} {aliases[root]}"]
        body += [f"       {join}" for join in joins]
        if applied:
            body.append("WHERE  " + "\n  AND  ".join(applied))
        body += held
        if aggregated and group:
            body.append("GROUP BY " + ", ".join(group))
        self.held = held
        self.not_reproduced = not_reproduced
        self.tables = {t: aliases[t] for t in [root] + order}
        self.sql = "\n".join(header + body) + "\n"

    @staticmethod
    def _through(graph, fan, table):
        """The table that repeats rows through which the lineage alone reaches table, or None."""
        for start in sorted(fan):
            seen, queue = {start}, deque([start])
            while queue:
                here = queue.popleft()
                if here == table:
                    return start
                for other, _ in graph.edges.get(here, []):
                    if other not in seen:
                        seen.add(other)
                        queue.append(other)
        return None

    @staticmethod
    def _ancestor(tree, table, ancestor):
        while table is not None:
            if table == ancestor:
                return True
            table = tree[table][0]
        return False

    @staticmethod
    def _select(lines):
        out = []
        for i, (text, comment) in enumerate(lines):
            comma = "," if i < len(lines) - 1 else ""
            out.append(f"{text}{comma}" + (f"  -- {comment}" if comment else ""))
        return "\n       ".join(out)

    def _empty_value(self, field, kind):
        return "0" if field.endswith("_concept_id") else "NULL"

    def _field(self, field, item, required, aliases, lookup_joins):
        """(expression, comment, state) for one field, where state is mapped, decision or empty."""
        own_key = self.keys.get(self.target)
        kind = self.fields[field][1]
        if item is None:
            if field == own_key:
                first = next(iter(aliases.items()))
                key = self.dictionary.table(first[0]).primary_key() or ()
                order = ", ".join(f"{first[1]}.{k}" for k in key) or "(SELECT NULL)"
                return f"ROW_NUMBER() OVER (ORDER BY {order})", None, "mapped"
            return self._empty_value(field, kind), WORDING["field_absent"], "empty"
        expressions = item.get("expressions") or []
        shape = expressions[0] if expressions else ""
        columns = [n for n in item.get("columns") or [] if _is_base(n)]
        missing = [n for n in columns if not self.known(n)]
        if missing:
            return self._empty_value(field, kind), WORDING["field_missing"].format(names=_and(missing)), "empty"
        # An identifier of another OMOP table, looked up by its source value.
        owner = self.owners.get(field)
        if owner and owner != self.target:
            return self._lookup(field, owner, columns, required, aliases, lookup_joins, shape)
        if len(expressions) != 1:
            return self._empty_value(field, kind), WORDING["field_shape"].format(shape=" or ".join(expressions)), "empty"
        if LITERAL.fullmatch(shape.strip()):
            name = f"{self.target}.{field}"
            literal = LITERAL.fullmatch(shape.strip()).group(1)
            self.placeholders.append({
                "placeholder": "{{decision:" + name + "}}", "name": name, "target": self.target, "kind": "field",
                "column": field, "columns": [], "type": literal, "test": None, "value": None,
                "question": WORDING["question_field"].format(kind=KINDS[literal], target=self.target.upper(), field=field, name=name)})
            return self._empty_value(field, kind), WORDING["field_decision"].format(name=name, kind=KINDS[literal]), "decision"
        sql, kinds, problem = _rewrite(shape, aliases)
        if problem is not None or kinds:
            if problem and problem[0] == "unreached":
                return self._empty_value(field, kind), WORDING["field_unreached"].format(names=problem[1]), "empty"
            return self._empty_value(field, kind), WORDING["field_shape"].format(shape=shape), "empty"
        return sql, None, "mapped"

    def _lookup(self, field, owner, columns, required, aliases, lookup_joins, shape):
        kind = self.fields[field][1]
        entry = self.lineage["targets"].get(owner) or {}
        value = SOURCE_VALUES.get(owner)
        theirs = [n for n in ((entry.get("fields") or {}).get(value) or {}).get("columns") or [] if _is_base(n)]
        if not value or owner not in self.written or len(columns) != 1 or theirs != columns:
            return self._empty_value(field, kind), WORDING["field_no_owner"].format(table=owner, column=_and(columns) if columns else shape), "empty"
        table, column = _split(columns[0])
        if table not in aliases:
            return self._empty_value(field, kind), WORDING["field_unreached"].format(names=table), "empty"
        held = lookup_joins.get(owner)
        if held is None:
            used = set(aliases.values()) | {a for a, _, _ in lookup_joins.values()}
            alias = _alias("o_" + owner, used)
            held = lookup_joins[owner] = (alias, f"{aliases[table]}.{column}", required)
        elif required and not held[2]:
            lookup_joins[owner] = held = (held[0], held[1], True)
        self.needs.add(owner)
        return f"{held[0]}.{field}", None, "mapped"


# The whole transplant.

def _order(targets, needs):
    """The targets in the order in which they can run: the core's tables first, each after the tables it looks up."""
    def layer(t):
        return "core" if t in convert.CORE_ONLY_TABLES else "anaesthesia"
    rank = {t: (0 if layer(t) == "core" else 1, CORE_ORDER.index(t) if t in CORE_ORDER else len(CORE_ORDER), t) for t in targets}
    done, order = set(), []
    pending = sorted(targets, key=lambda t: rank[t])
    while pending:
        ready = [t for t in pending if not (needs.get(t, set()) - {t}) & (set(pending) - done) or
                 all(n in done or n not in targets for n in needs.get(t, set()) - {t})]
        pick = (ready or pending)[0]
        order.append(pick)
        done.add(pick)
        pending.remove(pick)
    return order


def _catalogue_rows(steps, dictionary):
    """The tables and columns that the steps read, with each table's key, in the layout of a world's catalogue."""
    read = {}
    for step in steps:
        for table in step.tables:
            read.setdefault(table, set())
        for name in step.references():
            if _is_base(name) and step.known(name) and _split(name)[0] in step.tables:
                read.setdefault(_split(name)[0], set()).add(_split(name)[1])
        for table in step.tables:
            sql = step.sql or ""
            for column in re.findall(rf"\b{re.escape(step.tables[table])}\.([A-Za-z_][A-Za-z0-9_]*)", sql):
                read[table].add(column.upper())
    rows = []
    for table in sorted(read):
        held = dictionary.table(table)
        if held is None:
            continue
        key = [k.upper() for k in held.primary_key()]
        columns = [c for c in held.columns.values() if c.name.upper() in read[table] or c.name.upper() in key]
        columns.sort(key=lambda c: (c.name.upper() not in key, key.index(c.name.upper()) if c.name.upper() in key else 0, c.position))
        for position, column in enumerate(columns, start=1):
            data_type, length, precision, scale = _sql_type(column.data_type)
            nullable = "NO" if position == 1 and len(key) == 1 and column.name.upper() == key[0] else "YES"
            rows.append(["dbo", held.name, column.name, position, data_type, length, precision, scale, nullable])
    return rows


def _sql_type(data_type):
    """(DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE) for a dictionary's data type."""
    text = (data_type or "").strip().upper()
    sized = re.match(r"(N?VARCHAR|N?CHAR)\s*\(\s*(\d+)\s*\)", text)
    if sized:
        return sized.group(1).lower(), sized.group(2), "", ""
    if text.startswith(("NVARCHAR", "VARCHAR", "CHAR", "NCHAR", "TEXT", "STRING")) or not text:
        return "varchar", str(TEXT_LENGTH), "", ""
    if text.startswith(("BIGINT",)):
        return "bigint", "", "19", "0"
    if text.startswith(("INT", "SMALLINT", "TINYINT")):
        return "int", "", "10", "0"
    if text.startswith(("NUMERIC", "DECIMAL", "NUMBER")):
        return "numeric", "", "18", "0"
    if text.startswith(("FLOAT", "REAL", "DOUBLE")):
        return "float", "", "53", ""
    if text.startswith("DATETIME") or text.startswith("TIMESTAMP"):
        return "datetime", "", "", ""
    if text.startswith("DATE"):
        return "date", "", "", ""
    if text.startswith("TIME"):
        return "time", "", "", ""
    if text.startswith("BIT"):
        return "bit", "", "", ""
    return "varchar", str(TEXT_LENGTH), "", ""


def transplant(lineage, dictionary, out, targets=None, existing=None, date=None, reference=None):
    """Writes a conversion folder from a lineage, checked against a dictionary. Returns the report as data.

    reference names the reference conversion in the route record of each step and in draft.json; without it, the
    folder names the lineage and the date of the transplant."""
    date = date or dt.date.today().isoformat()
    reference = reference or f"a reference conversion's lineage, transplanted on {date}"
    out = Path(out)
    keys, owners = _keys()
    cdm = _fields()
    every = list(lineage.get("targets") or {})
    wanted = every if not targets else list(dict.fromkeys(t.strip().lower() for t in targets if t.strip()))
    skipped = []
    chosen = []
    for target in wanted:
        if target not in lineage.get("targets", {}):
            skipped.append({"target": target, "reason": WORDING["skipped_unknown"]})
        elif target not in cdm:
            skipped.append({"target": target, "reason": WORDING["skipped_not_cdm"]})
        else:
            chosen.append(target)
    existing_steps = []
    if existing is not None:
        existing = Path(existing)
        existing_steps = json.loads((existing / "conversion.json").read_text())
    existing_tables = {str(s.get("table", "")).lower() for s in existing_steps}
    written = set(chosen) | existing_tables
    steps = {}
    for target in chosen:
        step = _Step(target, lineage["targets"][target], lineage, dictionary, keys, owners, written, date)
        step.build()
        steps[target] = step
    order = _order(chosen, {t: s.needs for t, s in steps.items()})
    out.mkdir(parents=True, exist_ok=True)
    if existing is not None and existing.resolve() != out.resolve():
        for path in existing.iterdir():
            if path.is_dir():
                shutil.copytree(path, out / path.name, dirs_exist_ok=True)
            else:
                shutil.copy2(path, out / path.name)
    conversion = [dict(s) for s in existing_steps]
    report_targets = []
    for target in order:
        step = steps[target]
        layer = "core" if target in convert.CORE_ONLY_TABLES else "anaesthesia"
        kept_step = next((s for s in conversion if str(s.get("table", "")).lower() == target), None)
        file = f"{target}{SUFFIX}.sql" if kept_step is not None else f"{target}.sql"
        entry = {"target": target, "file": file if step.sql else None, "layer": layer,
                 "status": "written" if step.sql and not step.reasons else "incomplete",
                 "reasons": list(step.reasons), "fields_mapped": step.mapped, "fields_left_empty": step.empty,
                 "fields_awaiting_a_decision": step.decided,
                 "placeholders": [p["name"] for p in step.placeholders], "filters_held_back": len(step.held),
                 "filters_not_reproduced": len(getattr(step, "not_reproduced", [])),
                 "not_in_dictionary": step.missing, "alternative_of": kept_step["file"] if kept_step else None}
        report_targets.append(entry)
        if not step.sql:
            continue
        sql = step.sql
        if kept_step is not None:
            lines = sql.split("\n")
            lines.insert(1, WORDING["header_existing"].format(target=target.upper(), file=kept_step["file"]))
            sql = "\n".join(lines)
            offered = list(kept_step.get("alternatives") or [])
            if file not in [o.get("file") if isinstance(o, dict) else o for o in offered]:
                offered.append({"file": file, **_route(reference)})
            kept_step["alternatives"] = offered
        else:
            new = {"table": target, "file": file, "layer": layer, **_route(reference)}
            at = len(conversion)
            if layer == "core":
                at = next((i for i, s in enumerate(conversion) if s.get("layer") != "core"), len(conversion))
            else:
                at = next((i for i, s in enumerate(conversion) if s.get("layer") == "derived"), len(conversion))
            conversion.insert(at, new)
        (out / file).write_text(sql, encoding="utf-8")
    for item in skipped:
        report_targets.append({"target": item["target"], "file": None, "status": "skipped", "reasons": [item["reason"]]})
    (out / "conversion.json").write_text("[\n" + ",\n".join("  " + json.dumps(s) for s in conversion) + "\n]\n", encoding="utf-8")
    (out / DRAFT_FILE).write_text(json.dumps({"draft": True, "reference": reference, "date": date, "says": DRAFT_SAYS},
                                             indent=1) + "\n", encoding="utf-8")
    placeholders = [p for t in order for p in steps[t].placeholders]
    (out / DECISIONS_FILE).write_text(json.dumps({
        "format": "schemalyser-decisions/1",
        "note": "Each placeholder stands for a literal that the reference's lineage redacted. Give its value under value, "
                "then write it into the step in place of the placeholder.",
        "decisions": placeholders}, indent=1) + "\n", encoding="utf-8")
    rows = _catalogue_rows([steps[t] for t in order if steps[t].sql], dictionary)
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE", "CHARACTER_MAXIMUM_LENGTH",
                     "NUMERIC_PRECISION", "NUMERIC_SCALE", "IS_NULLABLE"])
    writer.writerows(rows)
    (out / CATALOGUE_FILE).write_text(text.getvalue(), encoding="utf-8")
    missing = sorted({n for t in order for n in steps[t].missing})
    done = [e for e in report_targets if e["status"] != "skipped"]
    report = {
        "format": REPORT_FORMAT,
        "private": "This report names the reference's tables and columns, so it stays wherever the reference is kept.",
        "date": date,
        "counts": {"written": sum(1 for e in done if e["file"]), "complete": sum(1 for e in done if e["status"] == "written"),
                   "incomplete": sum(1 for e in done if e["status"] == "incomplete" and e["file"]),
                   "not_written": sum(1 for e in done if not e["file"]),
                   "skipped": len(skipped), "placeholders": len(placeholders),
                   "fields_mapped": sum(len(e["fields_mapped"]) for e in done),
                   "fields_left_empty": sum(len(e["fields_left_empty"]) for e in done),
                   "fields_awaiting_a_decision": sum(len(e["fields_awaiting_a_decision"]) for e in done),
                   "filters_held_back": sum(e["filters_held_back"] for e in done),
                   "not_in_dictionary": len(missing)},
        "not_in_dictionary": missing,
        "targets": report_targets,
    }
    (out / f"{REPORT_FILE}.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (out / f"{REPORT_FILE}.md").write_text(markdown(report) + "\n", encoding="utf-8")
    summaries.write_transplant(report, out)
    return report


def markdown(report):
    c = report["counts"]
    out = [WORDING["md_title"], "", WORDING["md_private"], "",
           WORDING["md_counts"].format(written=_n(c["written"], "step", "steps"), incomplete=_are(c["incomplete"]),
                                       unwritten=_n(c["not_written"], "step", "steps"),
                                       skipped=_n(c["skipped"], "table", "tables"), mapped=_n(c["fields_mapped"], "field", "fields"),
                                       empty=c["fields_left_empty"] + c["fields_awaiting_a_decision"],
                                       placeholders=_n(c["placeholders"], "placeholder", "placeholders")), ""]
    out += [WORDING["md_missing"].format(count=len(report["not_in_dictionary"]), names=_code(report["not_in_dictionary"]))
            if report["not_in_dictionary"] else WORDING["md_missing_none"], ""]
    for entry in report["targets"]:
        out += [f"## {entry['target'].upper()}", ""]
        if entry["status"] == "skipped":
            out += [WORDING["md_skipped"].format(reason=entry["reasons"][0]), ""]
            continue
        counts = {"mapped": _n(len(entry["fields_mapped"]), "field", "fields"),
                  "placeholders": _n(len(entry["placeholders"]), "placeholder", "placeholders"),
                  "empty": len(entry["fields_left_empty"]) + len(entry["fields_awaiting_a_decision"])}
        if not entry["file"]:
            out.append(WORDING["md_unwritten"].format(reasons=_and(entry["reasons"])))
        elif entry["status"] == "incomplete":
            out.append(WORDING["md_incomplete"].format(reasons=_and(entry["reasons"]), **counts))
        else:
            out.append(WORDING["md_written"].format(**counts))
        if entry.get("alternative_of"):
            out.append(WORDING["md_alternative"].format(file=f"`{entry['alternative_of']}`", alternative=f"`{entry['file']}`"))
        emptied = entry["fields_left_empty"] + entry["fields_awaiting_a_decision"]
        if emptied:
            out.append(WORDING["md_empty_fields"].format(fields=_code(emptied)))
        out.append("")
    return "\n".join(out).rstrip("\n")
