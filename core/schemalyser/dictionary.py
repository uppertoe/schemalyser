"""The data dictionary of a conversion: what each table and field that it writes holds, and how.

The dictionary is written for the person, or the LLM, who writes a target query, and for the
static review of a target query in target.review. It is built from the conversion folder alone:
conversion.json, the step SQL, tables.json and the mapping rows, with the names of concepts taken
from an Athena download when one is given.

    python -m schemalyser.dictionary CONVERSION [--vocabulary FOLDER] --out FOLDER

It writes dictionary.json and dictionary.md. It covers every table that the anaesthesia and
derived layers write, and the three core tables that a query joins to, person, visit_occurrence and death.
For each field it gives how each step fills it (a fixed value, a value copied from the source, a
concept looked up through a mapping vocabulary, a value taken from another OMOP table, or a value
computed from others), the concepts that a concept field can hold, the units of each value field
by concept, how rows link to an anaesthetic, the rule of each field of a custom table from
tables.json, and the conventions that a query author must know.

What the dictionary may hold. Only names of OMOP and custom tables and fields, concept numbers,
concept names and other attributes from the public vocabulary, the names of mapping vocabularies
(SITE_...), step file names, the conversion's own descriptions in tables.json, text that a step
itself writes into an output field as a fixed value, and the fixed wording below. It never holds a
source table or column name, a source code, a mapping row's source_code_description, a value from
check results, or a step's comments. build() checks its own output for the source names, the
text codes and the descriptions of the mapping rows, and refuses to return a dictionary that holds
one. A text constant that a step writes into an output field, such as the procedure_source_value
of the anaesthetic's own row, is the conversion's own wording and is kept, even where the
conversion also looks that text up under a mapping vocabulary of its own; a code that a step
compares with a source column, or that only the mapping rows hold, is never written.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import convert
from . import questions as register
from . import target as targeting
from .extract import decode

CORE_TABLES = ("person", "visit_occurrence", "death")
# The tables whose rows stand for an anaesthetic, besides the custom tables.
ANAESTHETIC_TABLES = ("visit_detail", "procedure_occurrence")
VALUE_FIELDS = {"measurement": ("value_as_number", "measurement_concept_id"),
                "observation": ("value_as_number", "observation_concept_id")}

# The fixed wording of the dictionary. It awaits the clinical lead's approval.
WORDING = {
    "title": "Data dictionary of the conversion",
    "intro": [
        "This dictionary describes what the conversion writes into the OMOP tables and its custom tables, so that a target query can be written against them.",
        "Schemalyser built it from the conversion's steps, its mapping rows and tables.json, and it holds no name or code from the source database.",
        "A query reads each table as omop.<table>.",
    ],
    "links_title": "Links to an anaesthetic",
    "links_intro": "A row belongs to an anaesthetic only through the links below. A hospital visit may hold more than one anaesthetic, so visit_occurrence_id does not identify an anaesthetic, and neither does a person with a window of time. A row of the anaesthetic's own record carries the anaesthetic's visit_detail_id, and a measurement or an observation also points at the anaesthetic through its event field when it was taken during the anaesthetic, within the margin that the conventions below give.",
    "same_identifier": "These fields hold the identifier that {root} holds, so a query links two rows by comparing two of them: {fields}.",
    "event_concept": "Where {table}.{field} is filled, {table}.{concept_field} holds the concept {concept}, which says that {field} holds a procedure_occurrence_id.",
    "conventions_title": "Conventions",
    "tables_title": "Tables",
    "cdm_table": "This is a table of CDM 5.4, and the {layer} layer writes it with {steps}.",
    "core_table": "This is a table of CDM 5.4 that the hospital's core OMOP database writes. The conversion's core layer stands in for the core with {steps}, and the dictionary describes what that stand-in writes.",
    "custom_table": "This is a custom table that tables.json defines, and the derived layer writes it with {steps}. tables.json describes it as follows: {description}",
    "fills": "The step {step} fills it with {how}.",
    "leaves_empty": "The step {step} leaves it empty.",
    "rule": "tables.json gives the rule for this field: {rule}",
    "domain": "The field expects a concept of the {domain} domain.",
    "concepts": "The field can hold these concepts: {concepts}.",
    "concepts_none": "The steps write no concept in this field that is known in advance.",
    "open": "The field can also hold other concepts, because {reasons}.",
    "open_derived": "the mapping rows under {vocabularies} are proposed from a vocabulary download, so their concepts are not known in advance",
    "open_source": "a step copies the value from the source",
    "open_omop": "a step takes the value from a table that no step of the conversion writes",
    "open_computed": "a step computes the value",
    "not_written": "No step writes these fields, so they are empty in the rows that the dictionary describes: {fields}.",
    "units_title": "Units of {field}",
    "units": "Where {concept_field} is {concept}, {field} is in {units}.",
    "units_none": "no unit, because the step writes the concept 0 in unit_concept_id or leaves it empty",
    "units_mixed": "{table}.{field} holds values in more than one unit, so a query that compares it with a number also restricts {concept_field} to concepts of one unit, or restricts unit_concept_id.",
    "concepts_title": "Concepts",
    "concept_line": "{concept}: {about}. The conversion writes it in {where}.",
    "concept_about": "{name}, a concept of the {domain} domain in {vocabulary}",
    "concept_unnamed": "its name is not given, because the dictionary was built without a vocabulary download",
    "concept_zero": "no matching concept, which means that no mapping row was found",
    "no_field": "no field",
    # How a step fills a field, as a noun phrase.
    "how": {
        "constant": "the fixed value {value}",
        "text": "the fixed text '{value}'",
        "numbered": "a number that the step gives each row",
        "source": "a value copied from the source",
        "mapping": "the concept that the mapping rows under {vocabularies} give",
        "mapping_proposed": "the concept that the mapping rows under {vocabularies} give, which are proposed from a vocabulary download",
        "omop": "the value of {field}",
        "first_of": "{first}",
        "otherwise": "{before}, or where that is empty, {after}",
        "one_of": "one of {parts}, as a condition in the step decides",
        "computed": "a value computed from {parts}",
        "computed_source": "the source",
        "computed_mapping": "the mapping rows under {vocabularies}",
        "computed_nothing": "fixed values",
        "nothing": "an empty value",
    },
    # The conventions that the steps show, each found in the step SQL.
    "convention": {
        "unmapped": "Where the step {step} finds no mapping row, it writes the concept 0 in {table}.{field} and keeps the source's own identifier in {table}.{source_field}.",
        "gated": "The step {step} writes a row only where the source's code has a mapping row under {vocabularies}.",
        "flagged": "The step {step} writes only the source rows that a flag in the source does not mark to be left out, such as a reading that was not accepted or a test patient.",
        "numeric": "The step {step} writes only values that can be read as a number.",
        "start_end": "The step {step} writes a row only where the source gives a start, and an end that is not before the start.",
        "start_open_end": "The step {step} writes a row only where the source gives a start and no end before it, and it leaves the end empty where the source gives none.",
        "still_in_place": "The step {step} leaves {table}.{field} empty where the source gives a removal time in or after the year {year}, which is how the source says that the device is still in place.",
        "periods": "The step {step} writes one row for each period at one rate: a period ends at the next action for the same medicine; where no later action follows, the end is left empty, because the source does not say when the infusion stopped.",
        "converted": "The step {step} writes values in metric units: it writes {pairs} in {table}.{field}, and converts {table}.{value_field} to match.",
        "converted_pair": "the unit {to} where the mapping rows give {source}",
        "event_outside": "A row that the step {step} writes outside an anaesthetic's own record has no {table}.{field}.",
        "event_window": "The step {step} fills {table}.{field} only for a row of an anaesthetic's own record that was taken from the anaesthetic's start to its end, with {margin} either side, or from the start less that margin where the anaesthetic has no end. The mapping row under {vocabularies} sets the margin, and a row of the record outside the window has no {table}.{field}, although it still carries the anaesthetic's visit_detail_id.",
        "margin_known": "a margin of {minutes} minutes",
        "margin_unknown": "a margin in minutes",
    },
    "and": " and ",
    "or": " or ",
}


class DictionaryError(ValueError):
    """The dictionary could not be built, or it would hold a name or code from the source."""


def _join(items, word="and"):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + WORDING[word] + items[-1]


def _unwrap(node):
    while isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    return node


def _subquery(step, alias):
    """The derived table of a step that has this alias, or None."""
    for subquery in step.tree.find_all(exp.Subquery):
        if subquery.alias and subquery.alias.upper() == alias:
            return subquery
    return None


def _projections(subquery, name):
    """The expressions that each branch of a derived table gives for one of its columns."""
    found = []
    for branch in targeting._branches(subquery.this):
        for projection in branch.expressions:
            if projection.alias_or_name.upper() == name.upper():
                found.append(projection.unalias())
    return found


class _Context:
    """The conversion as the dictionary reads it: its steps, mapping rows and the conditions on each OMOP alias."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.steps = targeting._steps(folder, None)
        self.mapping = targeting._mapping_rows(folder)
        self.derived = set(register._derived(folder))
        self.custom = convert.read_tables(folder)
        self.fields = dict(convert.cdm_fields(), **targeting.custom_fields(folder))
        self.conditions = {}
        for step in self.steps:
            found = {}
            if step.tree is not None:
                for select in step.tree.find_all(exp.Select):
                    own = {node.alias_or_name.upper(): node.name.lower() for node, _ in targeting._own_tables(select)
                           if targeting._is_omop(node) and node.name.lower() in self.fields}
                    known = lambda a, own=own: {f for f, _, _ in self.fields[own[a]]} if a in own else None  # noqa: E731
                    for alias, condition in targeting._conditions(select, known).items():
                        found.setdefault(alias, condition)
            self.conditions[step.index] = found

    def writers(self, step, alias, field):
        """The earlier steps that can write the rows that an OMOP alias of a step reads, and that write the field."""
        table = step.omop_aliases[alias]
        condition = self.conditions[step.index].get(alias, {"write": set(), "values": {}})
        condition = {"write": set(condition["write"]) | {field}, "values": condition["values"]}
        definite, maybe = targeting._writers(table, condition, step.earlier, self.mapping, self.derived)
        return definite + [found for found, _, _ in maybe]

    # The values that an expression can give.

    def values(self, step, node, depth=0):
        """(known, open): the fixed values that an expression of a step can give, and why others cannot be told."""
        node = _unwrap(node)
        if node is None or isinstance(node, exp.Null):
            return set(), set()
        if targeting._literal(node) is not None:
            return {targeting._norm(targeting._literal(node))}, set()
        if isinstance(node, exp.Coalesce):
            parts = [node.this, *node.expressions]
        elif isinstance(node, exp.Case):
            parts = [branch.args.get("true") for branch in node.args.get("ifs") or []] + [node.args.get("default")]
        else:
            parts = None
        if parts is not None:
            known, open_ = set(), set()
            for part in parts:
                k, o = self.values(step, part, depth)
                known |= k
                open_ |= o
            return known, open_
        if isinstance(node, exp.Column) and node.table and depth < 8:
            alias = node.table.upper()
            entry = step.mappings.get(alias)
            if entry is not None and node.name.lower() == "target_concept_id":
                known = {target for vocabulary in entry.vocabularies for _, target in self.mapping.get(vocabulary, [])}
                proposed = entry.unresolved or any(v in self.derived for v in entry.vocabularies)
                return known, {"derived"} if proposed else set()
            subquery = _subquery(step, alias)
            if subquery is not None and alias not in step.omop_aliases:
                known, open_ = set(), set()
                for projection in _projections(subquery, node.name):
                    k, o = self.values(step, projection, depth + 1)
                    known |= k
                    open_ |= o
                return known, open_
            if alias in step.omop_aliases:
                writers = [w for w in self.writers(step, alias, node.name.lower()) if w.tree is not None]
                if not writers:
                    return set(), {"omop"}
                known, open_ = set(), set()
                for writer in writers:
                    k, o = self.values(writer, writer.outputs[node.name.lower()], depth + 1)
                    known |= k
                    open_ |= o
                return known, open_
            return set(), {"source"}
        return set(), {"computed" if not isinstance(node, exp.Column) else "source"}

    # How a step fills a field.

    def fill(self, step, node, depth=0):
        """A plain description of an expression: {"kind", ...}, with kind one of constant, text, nothing, numbered,
        source, mapping, omop, first_of, one_of and computed."""
        node = _unwrap(node)
        if node is None or isinstance(node, exp.Null):
            return {"kind": "nothing"}
        literal = targeting._literal(node)
        if literal is not None:
            is_text = isinstance(node, exp.Literal) and node.is_string
            return {"kind": "text" if is_text else "constant", "value": literal if is_text else targeting._norm(literal)}
        if isinstance(node, exp.Window) or (isinstance(node, exp.Add) and node.find(exp.Window)):
            return {"kind": "numbered"}
        if isinstance(node, exp.Column) and node.table and depth < 8:
            alias = node.table.upper()
            entry = step.mappings.get(alias)
            if entry is not None and node.name.lower() == "target_concept_id":
                return {"kind": "mapping", "vocabularies": sorted(entry.vocabularies),
                        "proposed": entry.unresolved or any(v in self.derived for v in entry.vocabularies)}
            subquery = _subquery(step, alias)
            if subquery is not None and alias not in step.omop_aliases:
                parts = [self.fill(step, projection, depth + 1) for projection in _projections(subquery, node.name)]
                return self._combine("one_of", parts)
            if alias in step.omop_aliases:
                return {"kind": "omop", "field": f"{step.omop_aliases[alias]}.{node.name.lower()}"}
            return {"kind": "source"}
        if isinstance(node, exp.Column):
            return {"kind": "source"}
        if isinstance(node, exp.Coalesce):
            return self._combine("first_of", [self.fill(step, part, depth) for part in [node.this, *node.expressions]])
        if isinstance(node, exp.Case):
            parts = [self.fill(step, branch.args.get("true"), depth) for branch in node.args.get("ifs") or []]
            if node.args.get("default") is not None:
                parts.append(self.fill(step, node.args["default"], depth))
            return self._combine("one_of", parts)
        # Anything else computes a value from the columns inside it.
        fields, vocabularies, source = [], [], False
        for column in node.find_all(exp.Column):
            inner = self.fill(step, column, depth + 1)
            for leaf in self._leaves(inner):
                if leaf["kind"] == "omop" and leaf["field"] not in fields:
                    fields.append(leaf["field"])
                elif leaf["kind"] == "mapping":
                    vocabularies += [v for v in leaf["vocabularies"] if v not in vocabularies]
                elif leaf["kind"] == "source":
                    source = True
        return {"kind": "computed", "fields": fields, "vocabularies": vocabularies, "source": source}

    def _leaves(self, fill):
        if fill["kind"] in ("first_of", "one_of"):
            for part in fill["parts"]:
                yield from self._leaves(part)
        elif fill["kind"] == "computed":
            for field in fill["fields"]:
                yield {"kind": "omop", "field": field}
            if fill["vocabularies"]:
                yield {"kind": "mapping", "vocabularies": fill["vocabularies"]}
            if fill["source"]:
                yield {"kind": "source"}
        else:
            yield fill

    @staticmethod
    def _combine(kind, parts):
        unique = []
        for part in parts:
            if part not in unique:
                unique.append(part)
        if kind == "one_of":
            unique = [part for part in unique if part["kind"] != "nothing"] or unique
        return unique[0] if len(unique) == 1 else {"kind": kind, "parts": unique}

    # Where an identifier comes from.

    def origins(self, step, node, depth=0):
        """The OMOP fields that an expression carries unchanged: through casts, COALESCE and derived tables."""
        node = _unwrap(node)
        if isinstance(node, exp.Coalesce):
            found = []
            for part in [node.this, *node.expressions]:
                found += [o for o in self.origins(step, part, depth) if o not in found]
            return found
        if not (isinstance(node, exp.Column) and node.table) or depth > 8:
            return []
        alias = node.table.upper()
        subquery = _subquery(step, alias)
        if subquery is not None and alias not in step.omop_aliases:
            found = []
            for projection in _projections(subquery, node.name):
                found += [o for o in self.origins(step, projection, depth + 1) if o not in found]
            return found
        if alias in step.omop_aliases:
            return [(step.omop_aliases[alias], node.name.lower())]
        return []

    def roots(self, table, field, depth=0):
        """The fields that first give the identifier that a field holds, following each step that writes it."""
        writers = [s for s in self.steps if s.table == table and field in s.outputs and s.tree is not None]
        found = set()
        for writer in writers:
            origins = self.origins(writer, writer.outputs[field])
            if not origins or depth > 6:
                found.add((table, field))
            for origin in origins:
                if origin != (table, field):
                    found |= self.roots(*origin, depth=depth + 1)
        return found or {(table, field)}


def _evaluate(node, env):
    """The value of an expression when each mapping alias gives one target concept, or raises LookupError."""
    node = _unwrap(node)
    if node is None or isinstance(node, exp.Null):
        return None
    literal = targeting._literal(node)
    if literal is not None:
        return targeting._norm(literal)
    if isinstance(node, exp.Column) and node.table.upper() in env and node.name.lower() == "target_concept_id":
        return env[node.table.upper()]
    if isinstance(node, exp.Coalesce):
        for part in [node.this, *node.expressions]:
            value = _evaluate(part, env)
            if value is not None:
                return value
        return None
    if isinstance(node, exp.Case):
        subject = node.args.get("this")
        for branch in node.args.get("ifs") or []:
            if subject is not None:
                hit = _evaluate(subject, env) is not None and _evaluate(subject, env) == _evaluate(branch.this, env)
            else:
                hit = _condition(branch.this, env)
            if hit:
                return _evaluate(branch.args.get("true"), env)
        return _evaluate(node.args.get("default"), env)
    raise LookupError


def _condition(node, env):
    node = _unwrap(node)
    if isinstance(node, exp.Not) and isinstance(_unwrap(node.this), exp.Is):
        return not _condition(node.this, env)
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        return _evaluate(node.this, env) is None
    if isinstance(node, exp.EQ):
        return _evaluate(node.this, env) == _evaluate(node.expression, env)
    raise LookupError


def _mapping_key(step, alias):
    """The SQL of the expression that a mapping alias compares its source_code with."""
    for comparison in step.tree.find_all(exp.EQ):
        for one, other in ((comparison.this, comparison.expression), (comparison.expression, comparison.this)):
            if isinstance(one, exp.Column) and one.table.upper() == alias and one.name.lower() == "source_code":
                return other.sql(dialect="tsql")
    return None


def _unit_pairs(context, step, concept_field, unit_field):
    """(concept, unit) pairs that one step can write, pairing the mapping rows by their source code where it can."""
    concept_expression = step.outputs.get(concept_field)
    unit_expression = step.outputs.get(unit_field)
    aliases = set()
    for expression in (concept_expression, unit_expression):
        for column in (expression.find_all(exp.Column) if expression is not None else []):
            if column.table.upper() in step.mappings and column.name.lower() == "target_concept_id":
                aliases.add(column.table.upper())
    keys = {_mapping_key(step, alias) for alias in aliases}
    many = [alias for alias in aliases if len(step.mappings[alias].vocabularies) > 1]
    if aliases and len(keys) == 1 and None not in keys and (not many or len(aliases) == 1):
        try:
            pairs = set()
            if many:
                alias = many[0]
                envs = [{alias: target} for v in step.mappings[alias].vocabularies for _, target in context.mapping.get(v, [])]
            else:
                by_code = {}
                for alias in aliases:
                    for vocabulary in step.mappings[alias].vocabularies:
                        for code, target in context.mapping.get(vocabulary, []):
                            by_code.setdefault(code, {})[alias] = target
                envs = list(by_code.values())
            # A code without any mapping row, where every lookup is a left join.
            if not any(step.mappings[alias].inner for alias in aliases):
                envs.append({})
            for env in envs:
                env = {alias: env.get(alias) for alias in aliases}
                concept = _evaluate(concept_expression, env)
                if concept is not None:
                    pairs.add((concept, _evaluate(unit_expression, env) if unit_expression is not None else None))
            return pairs
        except LookupError:
            pass
    concepts, _ = context.values(step, concept_expression)
    units, _ = context.values(step, unit_expression) if unit_expression is not None else ({None}, set())
    return {(concept, unit) for concept in concepts for unit in (units or {None})}


# The conventions that the steps show.

def _source_column(step, column):
    """True where a column of a step reads a source table, directly or through a derived table."""
    if not column.table:
        return False
    alias = column.table.upper()
    if alias in step.omop_aliases or alias in step.mappings:
        return False
    return True


def _event_window(context, step, join):
    """The margin of the window of time that a join to the anaesthetic holds, as (phrase, vocabularies), or None.

    The window is a DATEADD in the join's condition whose number is the target of a mapping row, so that the
    margin is set in one place. The phrase gives the number where the mapping rows hold exactly one.
    """
    condition = join.args.get("on") if join is not None else None
    if condition is None:
        return None
    adds = list(condition.find_all(exp.DateAdd))
    if not adds:
        return None
    vocabularies = sorted({v for add in adds for column in add.find_all(exp.Column) if (column.table or "").upper() in step.mappings
                           for v in step.mappings[column.table.upper()].vocabularies})
    if not vocabularies:
        return None
    # Where the step compares the mapping row's source code with fixed text, only those rows set the margin.
    aliases = {column.table.upper() for add in adds for column in add.find_all(exp.Column) if (column.table or "").upper() in step.mappings}
    codes = set()
    for node in step.tree.find_all(exp.EQ):
        for one, other in ((node.this, node.expression), (node.expression, node.this)):
            if isinstance(one, exp.Column) and one.table.upper() in aliases and one.name.lower() == "source_code" \
                    and isinstance(other, exp.Literal) and other.is_string:
                codes.add(str(other.this))
    targets = {target for v in vocabularies for code, target in context.mapping.get(v, []) if not codes or code in codes}
    c = WORDING["convention"]
    phrase = c["margin_known"].format(minutes=next(iter(targets))) if len(targets) == 1 else c["margin_unknown"]
    return phrase, _join(vocabularies, "or")


def _conventions(context, step, scope):
    found = []
    c = WORDING["convention"]
    if step.tree is None or step.table not in scope or step.layer == "core" and step.table not in CORE_TABLES:
        return found
    table = step.table
    for field, expression in step.outputs.items():
        node = _unwrap(expression)
        # An unmapped value: COALESCE(a mapping's target, ..., 0), with the source's identifier kept beside it.
        if field.endswith("_concept_id") and isinstance(node, exp.Coalesce):
            parts = [_unwrap(p) for p in [node.this, *node.expressions]]
            if targeting._literal(parts[-1]) == "0" and any(
                    isinstance(p, exp.Column) and p.table.upper() in step.mappings for p in parts):
                source_field = field[:-len("_concept_id")] + "_source_value"
                kept = step.outputs.get(source_field)
                if kept is not None and context.fill(step, kept)["kind"] in ("source", "computed"):
                    found.append(c["unmapped"].format(step=step.file, table=table, field=field, source_field=source_field))
        # A device still in place: CASE WHEN YEAR(removal) < year ... END in an end field.
        if field.endswith("_end_datetime") and isinstance(node, exp.Case):
            for branch in node.args.get("ifs") or []:
                for less in branch.this.find_all(exp.LT):
                    if isinstance(_unwrap(less.this), exp.Year) and targeting._literal(less.expression) is not None:
                        found.append(c["still_in_place"].format(step=step.file, table=table, field=field,
                                                                year=targeting._literal(less.expression)))
        # Periods at one rate: the end is the next action, from LEAD, and is left empty where there is none.
        if field.endswith("_end_datetime"):
            first = _unwrap(node.this) if isinstance(node, exp.Coalesce) else _unwrap(node)
            subquery = _subquery(step, first.table.upper()) if isinstance(first, exp.Column) and first.table else None
            if subquery is not None and any(isinstance(p, exp.Window) and isinstance(p.this, exp.Lead)
                                             for p in _projections(subquery, first.name)):
                found.append(c["periods"].format(step=step.file))
        # Units converted: CASE unit WHEN from THEN to ... END.
        if field == "unit_concept_id" and isinstance(node, exp.Case) and node.args.get("this") is not None:
            pairs = [(targeting._literal(b.this), targeting._literal(b.args.get("true"))) for b in node.args.get("ifs") or []]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None and a != b]
            value_field = VALUE_FIELDS.get(table, ("value_as_number",))[0]
            if pairs:
                found.append(c["converted"].format(
                    step=step.file, table=table, field=field, value_field=value_field,
                    pairs=_join(c["converted_pair"].format(to=b, source=a) for a, b in pairs)))
        # An event field from a left join: rows outside an anaesthetic's own record have none, and where the join
        # holds a window of time, rows of the record outside that window have none either.
        if field.endswith("_event_id"):
            for column in [node] if isinstance(node, exp.Column) else []:
                for table_node, join in targeting._own_tables(step.tree):
                    if table_node.alias_or_name.upper() == (column.table or "").upper() and targeting._side(join) == "LEFT":
                        found.append(c["event_outside"].format(step=step.file, table=table, field=field))
                        window = _event_window(context, step, join)
                        if window:
                            found.append(c["event_window"].format(step=step.file, table=table, field=field, margin=window[0],
                                                                  vocabularies=window[1]))
    # A row only where the code has a mapping row.
    gating = sorted({v for entry in step.mappings.values() if entry.inner and entry.columns and not entry.constant
                     for v in entry.vocabularies})
    if gating:
        found.append(c["gated"].format(step=step.file, vocabularies=_join(gating, "or")))
    flagged = numeric = start_end = open_end = False
    for select in step.tree.find_all(exp.Select):
        where = select.args.get("where")
        for clause in targeting._conjuncts(where.this if where is not None else None):
            if isinstance(clause, (exp.NEQ, exp.EQ)) and any(
                    isinstance(side, exp.Literal) and side.is_string and not side.this.startswith("SITE_")
                    for side in (clause.this, clause.expression)) and any(
                    _source_column(step, column) for column in clause.find_all(exp.Column)):
                flagged = True
            if isinstance(clause, exp.Not) and isinstance(_unwrap(clause.this), exp.Is) and isinstance(_unwrap(clause.this.this), exp.TryCast):
                numeric = True
            if isinstance(clause, exp.GTE) and all(isinstance(side, exp.Column) and _source_column(step, side)
                                                   for side in (clause.this, clause.expression)):
                start_end = True
            # An end that may be empty: (end IS NULL OR end >= start), between source columns.
            either = _unwrap(clause)
            if isinstance(either, exp.Or):
                parts = [_unwrap(part) for part in (either.this, either.expression)]
                if any(isinstance(part, exp.Is) and isinstance(part.this, exp.Column) and _source_column(step, part.this)
                       for part in parts) and any(
                        isinstance(part, exp.GTE) and all(isinstance(side, exp.Column) and _source_column(step, side)
                                                          for side in (part.this, part.expression)) for part in parts):
                    open_end = True
    if flagged:
        found.append(c["flagged"].format(step=step.file))
    if numeric:
        found.append(c["numeric"].format(step=step.file))
    if start_end:
        found.append(c["start_end"].format(step=step.file))
    if open_end:
        found.append(c["start_open_end"].format(step=step.file))
    return found


# The vocabulary.

def concept_names(vocabulary, wanted):
    """{concept: {name, domain, vocabulary, standard}} for the wanted concepts, read from CONCEPT.csv in an Athena download."""
    path = Path(vocabulary)
    path = path / "CONCEPT.csv" if path.is_dir() else path
    wanted = {str(c) for c in wanted}
    found = {}
    if not path.exists() or not wanted:
        return found
    with open(path, encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n").lower().split("\t")
        at = {name: header.index(name) for name in ("concept_id", "concept_name", "domain_id", "vocabulary_id", "standard_concept")}
        for line in f:
            head = line.split("\t", 1)[0]
            if head in wanted:
                row = line.rstrip("\r\n").split("\t")
                found[head] = {"name": row[at["concept_name"]], "domain": row[at["domain_id"]],
                               "vocabulary": row[at["vocabulary_id"]], "standard": row[at["standard_concept"]]}
    return found


# Building the dictionary.

def _how_text(fill):
    h = WORDING["how"]
    kind = fill["kind"]
    if kind == "constant":
        return h["constant"].format(value=fill["value"])
    if kind == "text":
        return h["text"].format(value=fill["value"])
    if kind in ("numbered", "source", "nothing"):
        return h[kind]
    if kind == "mapping":
        return h["mapping_proposed" if fill["proposed"] else "mapping"].format(vocabularies=_join(fill["vocabularies"], "or"))
    if kind == "omop":
        return h["omop"].format(field=fill["field"])
    if kind == "first_of":
        text = _how_text(fill["parts"][0])
        for part in fill["parts"][1:]:
            text = h["otherwise"].format(before=text, after=_how_text(part))
        return h["first_of"].format(first=text)
    if kind == "one_of":
        return h["one_of"].format(parts=_join((_how_text(p) for p in fill["parts"]), "or"))
    parts = list(fill["fields"])
    if fill["vocabularies"]:
        parts.append(h["computed_mapping"].format(vocabularies=_join(fill["vocabularies"], "or")))
    if fill["source"]:
        parts.append(h["computed_source"])
    return h["computed"].format(parts=_join(parts or [h["computed_nothing"]]))


def _vocabularies(fill):
    if fill["kind"] == "mapping" or (fill["kind"] == "computed" and fill["vocabularies"]):
        yield from fill["vocabularies"]
    for part in fill.get("parts", []):
        yield from _vocabularies(part)


def _forbidden(context):
    """The names and codes that the dictionary may not hold: (names, codes, descriptions)."""
    allowed_fields = {f for fields in context.fields.values() for f, _, _ in fields} | set(context.fields)
    allowed_fields |= {"source_code", "source_vocabulary_id", "target_concept_id", "source_code_description"}
    names, literals, constants = set(), set(), set()
    for step in context.steps:
        try:
            tree = sqlglot.parse_one(step.sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        aliases = {a.alias.upper() for a in tree.find_all(exp.Alias)} | {t.alias.upper() for t in tree.find_all(exp.TableAlias) if t.name}
        for table in tree.find_all(exp.Table):
            if not targeting._is_omop(table):
                names.add(table.name)
        for column in tree.find_all(exp.Column):
            if column.name.lower() not in allowed_fields and column.name.upper() not in aliases:
                names.add(column.name)
        for alias in tree.find_all(exp.Alias):
            # A text that a step writes as the value of a column is its own wording, not a source code.
            if isinstance(alias.this, exp.Literal) and alias.this.is_string and isinstance(alias.parent, exp.Select):
                constants.add(alias.this.this)
        for literal in tree.find_all(exp.Literal):
            if not literal.is_string or literal.this.startswith("SITE_"):
                continue
            parent = literal.parent
            others = [c for c in (parent.find_all(exp.Column) if parent is not None else [])]
            if others and all((c.table or "").upper() in step.omop_aliases and c.name.lower() != "source_code" for c in others):
                continue        # compared with an OMOP field, such as the anaesthetic's own procedure_source_value
            literals.add(literal.this)
    descriptions = set()
    for row in convert.mapping_dicts(context.folder):
        code = (row.get("source_code") or "").strip()
        if code:
            literals.add(code)
        if (row.get("source_code_description") or "").strip():
            descriptions.add(row["source_code_description"].strip())
    codes = {code for code in literals - constants if not re.fullmatch(r"-?[0-9.]+", code) and len(code) > 1}
    return names, codes, descriptions - constants


def _check(text, forbidden, allowed_text):
    """Raises DictionaryError where the text holds a source name, a text code or a mapping row's description."""
    names, codes, descriptions = forbidden
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_$#@]*", text))
    for name in names | codes:
        if name in tokens or (not re.fullmatch(r"\w+", name) and name in text):
            raise DictionaryError("the dictionary would hold a name or a code from the source, so it has not been written")
    for description in descriptions:
        if len(description) > 2 and re.search(rf"(?<!\w){re.escape(description)}(?!\w)", text) \
                and not re.search(rf"(?<!\w){re.escape(description)}(?!\w)", allowed_text):
            raise DictionaryError("the dictionary would hold the description of a mapping row, so it has not been written")


def build(folder, vocabulary=None):
    """The data dictionary of a conversion folder, as a plain dictionary that json.dumps can write.

    vocabulary, when given, is an Athena download (or its CONCEPT.csv), from which the names,
    domains and vocabularies of the concepts are taken.
    """
    try:
        context = _Context(folder)
    except (targeting.TargetError, convert.TablesError) as error:
        raise DictionaryError(str(error)) from None
    steps = context.steps
    custom = {table["name"]: table for table in context.custom}
    written = list(dict.fromkeys(s.table for s in steps if s.layer in ("anaesthesia", "derived")))
    scope = [t for t in CORE_TABLES if any(s.table == t for s in steps)] + [t for t in written if t not in custom] \
        + [t for t in written if t in custom]
    domains = {}
    with open(convert.FIELDS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            domains[(row["table"], row["field"])] = row["concept_domain"]

    tables, concept_places, used_vocabularies = {}, {}, {}
    for table in scope:
        writers = [s for s in steps if s.table == table and s.tree is not None]
        layers = list(dict.fromkeys(s.layer for s in writers))
        kind = "custom" if table in custom else "core" if table in CORE_TABLES else "cdm"
        entry = {"kind": kind, "layers": layers, "steps": [s.file for s in writers],
                 "description": custom[table]["description"] if table in custom else None,
                 "primary_key": next((f["name"] for f in custom[table]["fields"] if f["primary_key"]), None) if table in custom
                 else context.fields[table][0][0], "fields": {}, "not_written": []}
        rules = {f["name"]: f["description"] for f in custom[table]["fields"]} if table in custom else {}
        for field, _, kind_of in context.fields[table]:
            fillers = [s for s in writers if field in s.outputs]
            if not fillers:
                entry["not_written"].append(field)
                continue
            info = {"type": next((f["type"] for f in custom[table]["fields"] if f["name"] == field), "") if table in custom
                    else next((row_type for row_type in [kind_of]), ""),
                    "domain": domains.get((table, field), ""), "rule": rules.get(field), "written_by": []}
            known, open_ = set(), set()
            for step in fillers:
                fill = context.fill(step, step.outputs[field])
                info["written_by"].append({"step": step.file, "layer": step.layer, "fill": fill,
                                           "text": (WORDING["leaves_empty"].format(step=step.file) if fill["kind"] == "nothing"
                                                    else WORDING["fills"].format(step=step.file, how=_how_text(fill)))})
                for name in _vocabularies(fill):
                    used_vocabularies[name] = name in context.derived
                if field.endswith("_concept_id"):
                    k, o = context.values(step, step.outputs[field])
                    known |= k
                    open_ |= o
            if field.endswith("_concept_id"):
                info["concepts"] = sorted(known, key=lambda v: (len(v), v))
                info["open"] = sorted(open_)
                derived = sorted({v for w in info["written_by"] for v in _vocabularies(w["fill"]) if v in context.derived})
                info["open_vocabularies"] = derived
            entry["fields"][field] = info
        tables[table] = entry

    # The units of each value field, by concept.
    units = {}
    for table, (value_field, concept_field) in VALUE_FIELDS.items():
        if table not in tables or value_field not in tables[table]["fields"]:
            continue
        by_concept = {}
        for step in steps:
            if step.table != table or step.tree is None or value_field not in step.outputs \
                    or isinstance(_unwrap(step.outputs[value_field]), exp.Null):
                continue
            for concept, unit in _unit_pairs(context, step, concept_field, "unit_concept_id"):
                by_concept.setdefault(concept, set()).add(unit if unit not in (None, "0") else "none")
        units[table] = {"value_field": value_field, "concept_field": concept_field,
                        "by_concept": {c: sorted(u) for c, u in sorted(by_concept.items(), key=lambda i: (len(i[0]), i[0]))}}
        # The units that the steps pair with their concepts are more exact than every value the unit field's expression names.
        field = tables[table]["fields"].get("unit_concept_id")
        if field is not None and by_concept:
            found = {"0" if u == "none" else u for units_of in by_concept.values() for u in units_of}
            field["concepts"] = sorted(found, key=lambda v: (len(v), v))
    for table, entry in tables.items():
        for field, info in entry["fields"].items():
            for concept in info.get("concepts", []):
                concept_places.setdefault(concept, []).append(f"{table}.{field}")

    # The identifiers: where each field's identifier first comes from, and those that stand for an anaesthetic.
    identifiers = {}
    for table in scope:
        for field in tables[table]["fields"]:
            if field.endswith("_id") and not field.endswith("_concept_id"):
                identifiers[f"{table}.{field}"] = sorted(f"{t}.{f}" for t, f in context.roots(table, field))
    anaesthetic_roots = set()
    for table in scope:
        if table in custom and tables[table]["primary_key"]:
            key = tables[table]["primary_key"]
            if f"{table}.{key}" in identifiers and identifiers[f"{table}.{key}"] != [f"{table}.{key}"]:
                anaesthetic_roots |= set(identifiers[f"{table}.{key}"])
    if "visit_detail" in tables and "visit_detail_id" in tables["visit_detail"]["fields"]:
        anaesthetic_roots |= set(identifiers.get("visit_detail.visit_detail_id", ["visit_detail.visit_detail_id"]))
    links = []
    for root in sorted(anaesthetic_roots):
        fields = [name for name, roots in identifiers.items() if root in roots and name != root]
        links.append({"root": root, "fields": fields,
                      "text": WORDING["same_identifier"].format(root=root, fields=_join([root] + fields))})
    for table in scope:
        for field, info in tables[table]["fields"].items():
            if not field.endswith("_event_id"):
                continue
            concept_field = next((f for f in tables[table]["fields"] if f.endswith("event_field_concept_id")), None)
            if concept_field and tables[table]["fields"][concept_field].get("concepts"):
                for concept in tables[table]["fields"][concept_field]["concepts"]:
                    links.append({"root": None, "fields": [f"{table}.{field}", f"{table}.{concept_field}"],
                                  "text": WORDING["event_concept"].format(table=table, field=field, concept_field=concept_field,
                                                                          concept=concept)})

    conventions = []
    for step in steps:
        for sentence in _conventions(context, step, scope):
            if sentence not in conventions:
                conventions.append(sentence)

    names = concept_names(vocabulary, [c for c in concept_places if c != "0"]) if vocabulary else {}
    concepts = {}
    for concept in sorted(concept_places, key=lambda v: (len(v), v)):
        about = names.get(concept, {})
        concepts[concept] = {"name": "No matching concept" if concept == "0" else about.get("name", ""),
                             "domain": about.get("domain", ""), "vocabulary": about.get("vocabulary", ""),
                             "standard": about.get("standard", ""), "written_in": sorted(concept_places[concept])}
    result = {"format": 1, "wording": "Schemalyser data dictionary",
              "steps": [{"file": s.file, "table": s.table, "layer": s.layer} for s in steps],
              "tables": tables, "units": units, "identifiers": identifiers,
              "anaesthetic_roots": sorted(anaesthetic_roots), "links": links, "conventions": conventions,
              "concepts": concepts,
              "vocabularies": {v: {"proposed": proposed} for v, proposed in sorted(used_vocabularies.items())}}
    allowed = " ".join(t["description"] + " " + " ".join(f["description"] for f in t["fields"]) for t in context.custom)
    allowed += " " + " ".join(c["name"] for c in concepts.values())
    forbidden = _forbidden(context)
    _check(json.dumps(result), forbidden, allowed)
    _check(markdown(result), forbidden, allowed)
    return result


def _concept(dictionary, concept):
    about = dictionary["concepts"].get(str(concept), {})
    return f"{concept} ({about['name']})" if about.get("name") else str(concept)


def markdown(dictionary):
    """The dictionary as Markdown, for a person or an LLM to read."""
    w = WORDING
    lines = [f"# {w['title']}", "", " ".join(w["intro"]), "", f"## {w['links_title']}", "", w["links_intro"], ""]
    lines += [f"- {link['text']}" for link in dictionary["links"]]
    lines += ["", f"## {w['conventions_title']}", ""] + [f"- {text}" for text in dictionary["conventions"]]
    lines += ["", f"## {w['tables_title']}"]
    for table, entry in dictionary["tables"].items():
        steps = _join(entry["steps"])
        lines += ["", f"### {table}", ""]
        if entry["kind"] == "custom":
            lines.append(w["custom_table"].format(steps=steps, description=entry["description"]))
        elif entry["kind"] == "core":
            lines.append(w["core_table"].format(steps=steps))
        else:
            lines.append(w["cdm_table"].format(layer=_join(entry["layers"]), steps=steps))
        for field, info in entry["fields"].items():
            lines += ["", f"#### {table}.{field}", ""]
            if info.get("rule"):
                lines.append(f"- {w['rule'].format(rule=info['rule'])}")
            lines += [f"- {item['text']}" for item in info["written_by"]]
            if field.endswith("_concept_id"):
                if info.get("domain"):
                    lines.append(f"- {w['domain'].format(domain=info['domain'])}")
                if info["concepts"]:
                    lines.append(f"- {w['concepts'].format(concepts=_join(_concept(dictionary, c) for c in info['concepts']))}")
                else:
                    lines.append(f"- {w['concepts_none']}")
                if info["open"]:
                    reasons = []
                    for reason in info["open"]:
                        if reason == "derived":
                            reasons.append(w["open_derived"].format(vocabularies=_join(info["open_vocabularies"], "or")))
                        else:
                            reasons.append(w[f"open_{reason}"])
                    lines.append(f"- {w['open'].format(reasons=_join(reasons))}")
        if entry["not_written"]:
            lines += ["", w["not_written"].format(fields=_join(entry["not_written"]))]
        unit = dictionary["units"].get(table)
        if unit:
            lines += ["", f"#### {w['units_title'].format(field=unit['value_field'])}", ""]
            for concept, found in unit["by_concept"].items():
                named = _join([w["units_none"] if u == "none" else _concept(dictionary, u) for u in found], "or")
                lines.append(f"- {w['units'].format(concept_field=unit['concept_field'], concept=_concept(dictionary, concept), field=unit['value_field'], units=named)}")
            if len({u for found in unit["by_concept"].values() for u in found}) > 1:
                lines.append(f"- {w['units_mixed'].format(table=table, field=unit['value_field'], concept_field=unit['concept_field'])}")
    lines += ["", f"## {w['concepts_title']}", ""]
    for concept, about in dictionary["concepts"].items():
        if concept == "0":
            text = w["concept_zero"]
        elif about["name"]:
            text = w["concept_about"].format(name=about["name"], domain=about["domain"], vocabulary=about["vocabulary"])
        else:
            text = w["concept_unnamed"]
        where = _join(about["written_in"]) if about["written_in"] else w["no_field"]
        lines.append(f"- {w['concept_line'].format(concept=concept, about=text, where=where)}")
    return "\n".join(lines) + "\n"


def write(dictionary, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dictionary.json").write_text(json.dumps(dictionary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "dictionary.md").write_text(markdown(dictionary), encoding="utf-8")


def load(path):
    """A dictionary written by write(), from its JSON file or the folder that holds it."""
    path = Path(path)
    path = path / "dictionary.json" if path.is_dir() else path
    return json.loads(decode(path.read_bytes()))


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.dictionary")
    parser.add_argument("conversion", type=Path)
    parser.add_argument("--vocabulary", type=Path, help="an Athena download, or its CONCEPT.csv, for the names of the concepts")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        dictionary = build(args.conversion, args.vocabulary)
    except DictionaryError as error:
        print(f"schemalyser.dictionary: {error}", file=sys.stderr)
        return 1
    write(dictionary, args.out)
    print(f"{len(dictionary['tables'])} tables, {sum(len(t['fields']) for t in dictionary['tables'].values())} fields written, "
          f"{len(dictionary['concepts'])} concepts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
