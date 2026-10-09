"""Compares the lineage of two conversions to OMOP: ours, and a reference conversion written elsewhere.

The reference command reads a folder of SQL and writes its lineage: for each OMOP table that the folder writes, the
source tables it reads, the joins between them, the filters it applies, the source behind each target field, and its
aggregations. The folder may be one of three kinds.

    A conversion folder, with a conversion.json that names the OMOP table of each step file, as fixtures/conversion is.
    A dbt-style project, with .sql models under models/. {{ ref('x') }} is read as the model x, {{ source('a', 'b') }}
    as the table that the sources file names, and {{ config(...) }} is dropped, keeping any alias. A model that holds
    any other Jinja is recorded as not read. YAML files are read only for the names of sources, models and aliases.
    A plain folder of SQL files. A target is recognised by INSERT INTO an OMOP table, by the file's name, or by a final
    SELECT whose columns are mostly the fields of one OMOP table. SELECT INTO, CREATE TABLE AS and CREATE VIEW make an
    intermediate table, which is followed through to the tables that it reads.

A file that does not parse, under the SQL Server dialect or the generic one, is recorded with the class of its error and
never with its text. Literal values in filters and expressions are replaced by their type, as <int> or <str>.

The source tables of a target are the base tables that it reads, those that every intermediate it reads reads in turn,
and those behind each column that it takes from another OMOP table. The joins, filters and aggregations of an
intermediate count as the target's own; those of another OMOP table count under that table only. A column read from an
intermediate or from another OMOP table is followed to the source column behind it, so that a join through
omop.visit_occurrence and a join straight to the visit table are written alike.

The report command compares two lineage files, and optionally the proposed bindings of a saved hospital schema, and
writes report.md and report.json. The summary at the top holds counts alone and may be shared. Everything below it
names tables and columns and stays where the reference is kept.

    python -m schemalyser.compare reference FOLDER --out lineage.json
    python -m schemalyser.compare report --ours lineage.json --theirs lineage.json [--schema SCHEMA.zip] --out FOLDER

Agreement between two conversions is supporting evidence and not proof: workflows and configuration differ between
hospitals, and two conversions can share a mistake.
"""
import argparse
import csv
import itertools
import json
import re
import sys
import zipfile
from functools import lru_cache
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from . import statements
from .extract import decode

FORMAT = "schemalyser-lineage/1"
REPORT_FORMAT = "schemalyser-comparison/1"
CDM_FIELDS = Path(__file__).parent / "omop" / "cdm54_fields.csv"
VOCABULARY = {"concept", "concept_relationship", "concept_ancestor", "concept_synonym", "concept_class", "vocabulary",
              "domain", "relationship", "drug_strength", "source_to_concept_map", "source_to_standard_vocab_map"}
OMOP_SCHEMA = re.compile(r"omop|cdm", re.IGNORECASE)
ANAESTHESIA = {
    "visit_detail": "the anaesthetic episode (VISIT_DETAIL)",
    "measurement": "intraoperative observations (MEASUREMENT)",
    "drug_exposure": "drug administrations (DRUG_EXPOSURE)",
    "procedure_occurrence": "procedures and anaesthetics (PROCEDURE_OCCURRENCE)",
    "device_exposure": "airway and other devices (DEVICE_EXPOSURE)",
    "observation": "anaesthetic events and other observations (OBSERVATION)",
    "person": "patients (PERSON)",
    "visit_occurrence": "hospital visits (VISIT_OCCURRENCE)",
}
# The OMOP tables that each part of the role model feeds, for the third view from a saved hospital schema.
ROLE_TARGETS = {
    "role_patient": ["person"], "role_patient_detail": ["person"], "role_stay": ["visit_occurrence"],
    "role_anaesthetic": ["visit_detail", "procedure_occurrence"], "role_anaesthetic_detail": ["visit_detail"],
    "role_unit_stay": ["visit_detail"], "role_reading": ["measurement"], "role_lab": ["measurement"],
    "role_operation": ["procedure_occurrence"], "role_event": ["observation", "procedure_occurrence"],
    "role_drug": ["drug_exposure"], "role_fluid": ["drug_exposure"], "role_device": ["device_exposure"],
    "role_diagnosis": ["condition_occurrence"], "role_finding": ["observation"], "role_note": ["observation"],
    "role_staff": ["provider"],
}
PRIVATE_NOTE = ("This lineage names the reference's tables and columns, so it is private wherever the reference is "
                "private. Keep it with the reference, and do not share it or show it to a language model.")


class CompareError(ValueError):
    """An input that the comparison cannot read."""


@lru_cache(maxsize=1)
def _cdm():
    """The tables of CDM 5.4, as table -> [field], in the published order."""
    tables = {}
    with CDM_FIELDS.open(newline="") as handle:
        for row in csv.DictReader(handle):
            tables.setdefault(row["table"].lower(), []).append(row["field"].lower())
    return tables


def cdm_tables():
    """The tables of CDM 5.4, as table -> [field], in the published order."""
    return {t: list(f) for t, f in _cdm().items()}


# Reading the folder.

GO = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE | re.MULTILINE)
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
REF = re.compile(r"\{\{\s*ref\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
SOURCE = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.DOTALL)
ALIAS = re.compile(r"alias\s*=\s*['\"]([^'\"]+)['\"]")


class JinjaError(Exception):
    """A model holds Jinja other than ref, source and config."""


def _yaml(text):
    """The YAML of a dbt project, read with PyYAML where it is installed and otherwise by a reader of the block subset
    that sources and models files use: mappings, lists of mappings, and plain scalars."""
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(text)
    except ImportError:
        pass
    lines = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if line.strip() and line.strip() != "---":
            lines.append((len(line) - len(line.lstrip(" ")), line.strip()))

    def scalar(value):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            return value[1:-1]
        return value

    def block(i, indent):
        if i >= len(lines):
            return None, i
        if lines[i][1].startswith("- "):
            items = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                inner = indent + 2
                rest = lines[i][1][2:]
                lines[i] = (inner, rest)
                if ":" in rest and not rest.startswith(("'", '"')):
                    value, i = mapping(i, inner)
                else:
                    value, i = scalar(rest), i + 1
                items.append(value)
            return items, i
        return mapping(i, indent)

    def mapping(i, indent):
        found = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            key, _, value = lines[i][1].partition(":")
            i += 1
            if value.strip() in ("", ">", "|", ">-", "|-"):
                if i < len(lines) and lines[i][0] > indent:
                    if value.strip():
                        while i < len(lines) and lines[i][0] > indent:
                            i += 1
                        found[key.strip()] = ""
                    else:
                        found[key.strip()], i = block(i, lines[i][0])
                elif i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                    found[key.strip()], i = block(i, indent)
                else:
                    found[key.strip()] = None
            else:
                found[key.strip()] = scalar(value)
        return found, i

    return block(0, lines[0][0])[0] if lines else None


def _dbt_names(folder):
    """The sources as (source, table) -> table name, and each model's alias, from every YAML file of the project."""
    sources, aliases = {}, {}
    for path in sorted(folder.rglob("*.yml")) + sorted(folder.rglob("*.yaml")):
        try:
            data = _yaml(decode(path.read_bytes()))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for source in data.get("sources") or []:
            if not isinstance(source, dict):
                continue
            for table in source.get("tables") or []:
                if isinstance(table, dict) and table.get("name"):
                    sources[(str(source.get("name")), str(table["name"]))] = str(table.get("identifier") or table["name"])
        for model in data.get("models") or []:
            if isinstance(model, dict) and model.get("name"):
                config = model.get("config") if isinstance(model.get("config"), dict) else {}
                alias = model.get("alias") or config.get("alias")
                if alias:
                    aliases[str(model["name"]).lower()] = str(alias).lower()
    return sources, aliases


def _render_jinja(text, sources):
    """A dbt model as plain SQL, with its alias, or JinjaError where it holds anything but ref, source and config."""
    text = JINJA_COMMENT.sub("", text)
    alias = None
    for match in CONFIG.finditer(text):
        found = ALIAS.search(match.group(1))
        alias = found.group(1).lower() if found else alias
    text = CONFIG.sub("", text)
    text = REF.sub(lambda m: m.group(1), text)
    text = SOURCE.sub(lambda m: sources.get((m.group(1), m.group(2)), m.group(2)), text)
    if "{{" in text or "{%" in text:
        raise JinjaError()
    return text, alias


def _parse(text):
    """The statements of a file, under the SQL Server dialect and then the generic one, or the error class."""
    found, error = [], None
    for batch in GO.split(text):
        if not batch.strip():
            continue
        parsed = statements.parse(batch, "tsql")
        if not parsed or all(isinstance(s, exp.Command) for s in parsed):
            try:
                generic = [s for s in sqlglot.parse(batch) if s is not None]
            except Exception as problem:  # the class alone is kept, never the message, which quotes the text
                error = type(problem).__name__
                generic = None
            if generic and not all(isinstance(s, exp.Command) for s in generic):
                parsed = generic
            elif parsed is None:
                error = error or "ParseError"
                return None, error
        found.extend(parsed or [])
    if not any(not isinstance(s, exp.Command) for s in found):
        return None, error or "NoStatement"
    return found, None


class _Piece:
    """One query that writes rows: the unit it writes, and its parsed SELECT with the names of its columns."""

    def __init__(self, unit, query, names, file):
        self.unit, self.query, self.names, self.file = unit, query, names, file


class _Unit:
    def __init__(self, name):
        self.name, self.target, self.files, self.pieces = name, None, [], []
        self.fields, self.tables, self.joins, self.filters, self.aggregations, self.lookups = {}, set(), [], [], [], set()


def _table_name(table):
    name = table.name
    return name.lower()


def _written(statement):
    """(name, query, column names or None, written to an OMOP table by INSERT) for a statement that writes rows, or None."""
    if isinstance(statement, exp.Insert):
        target = statement.this
        columns = None
        if isinstance(target, exp.Schema):
            columns = [c.name.lower() for c in target.expressions]
            target = target.this
        if isinstance(target, exp.Table) and isinstance(statement.expression, exp.Query):
            return _table_name(target), statement.expression, columns
    if isinstance(statement, exp.Create) and isinstance(statement.expression, exp.Query):
        target = statement.this.this if isinstance(statement.this, exp.Schema) else statement.this
        if isinstance(target, exp.Table):
            return _table_name(target), statement.expression, None
    if isinstance(statement, exp.Query):
        into = statement.args.get("into")
        if into is not None and isinstance(into.this, exp.Table):
            query = statement.copy()
            query.set("into", None)
            return _table_name(into.this), query, None
        return None, statement, None
    return None


def _selects(query):
    if isinstance(query, exp.SetOperation):
        return _selects(query.left) + _selects(query.right)
    if isinstance(query, exp.Subquery):
        return _selects(query.this)
    return [query] if isinstance(query, exp.Select) else []


def _is_star(projection):
    return isinstance(projection, exp.Star) or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))


def _outputs(query):
    """The names of a query's columns, with a SELECT * expanded over the subqueries and CTEs that it reads."""
    first = _selects(query)
    if not first:
        return []
    names = []
    for projection in first[0].expressions:
        if not _is_star(projection):
            names.append(projection.alias_or_name.lower())
            continue
        try:
            root = traverse_scope(query)[-1]
        except Exception:
            return names + ["*"]
        for _, source in root.selected_sources.values():
            if isinstance(source, exp.Table):
                names.append("*")
            else:
                inner = _selects(source.expression)
                names += [p.alias_or_name.lower() for p in inner[0].expressions] if inner else []
    return names


def _target_by_name(name, tables):
    name = name.lower()
    if name in tables:
        return name
    fits = [t for t in tables if name.startswith(t + "_")]
    return max(fits, key=len) if fits else None


def _target_by_columns(columns, tables):
    columns = [c for c in columns if c != "*"]
    if len(columns) < 3:
        return None
    scored = sorted(((sum(c in set(fields) for c in columns), t) for t, fields in tables.items()), reverse=True)
    best, table = scored[0]
    if best >= 3 and best >= 0.6 * len(columns) and (len(scored) == 1 or scored[1][0] < best):
        return table
    return None


def _files(folder):
    """(mode, [(relative name, unit name, target or None, text or None, error or None)])."""
    tables = cdm_tables()
    if (folder / "conversion.json").is_file():
        steps = json.loads((folder / "conversion.json").read_text())
        found = []
        for step in steps:
            path = folder / step["file"]
            table = step["table"].lower()
            found.append((step["file"], Path(step["file"]).stem.lower(), table if table in tables else None,
                          decode(path.read_bytes()), None))
        return "conversion", found
    models = folder / "models"
    if (folder / "dbt_project.yml").is_file() or models.is_dir():
        sources, aliases = _dbt_names(folder)
        found = []
        for path in sorted((models if models.is_dir() else folder).rglob("*.sql")):
            name = path.stem.lower()
            relative = path.relative_to(folder).as_posix()
            try:
                text, alias = _render_jinja(decode(path.read_bytes()), sources)
            except JinjaError:
                found.append((relative, name, None, None, "Jinja"))
                continue
            alias = alias or aliases.get(name)
            found.append((relative, name, _target_by_name(alias, tables) if alias else _target_by_name(name, tables), text, None))
        return "dbt", found
    found = []
    for path in sorted(folder.rglob("*.sql")):
        found.append((path.relative_to(folder).as_posix(), path.stem.lower(), None, decode(path.read_bytes()), None))
    return "plain", found


# Following columns.

class _Statement:
    """The lineage of one query, with every column traced to a reference: (kind, table, column), where kind is base,
    unit (an intermediate or an OMOP table that the folder writes), omop (one it does not write), vocabulary or unknown."""

    def __init__(self, query, units, owner, tables_written):
        self.query, self.units, self.owner, self.tables_written = query, units, owner, tables_written
        self.scopes = traverse_scope(query)
        self.by_select = {id(scope.expression): scope for scope in self.scopes if isinstance(scope.expression, exp.Select)}
        self.memo, self.busy = {}, set()

    def classify(self, table):
        name = _table_name(table)
        schema = table.db or ""
        if name in VOCABULARY or "vocab" in schema.lower():
            return ("vocabulary", name)
        if name in self.units and name != self.owner:
            return ("unit", name)
        if name in self.tables_written and (name != self.owner or OMOP_SCHEMA.search(schema)):
            return ("unit", name)
        if name in _cdm() and OMOP_SCHEMA.search(schema):
            return ("omop", name)
        return ("base", table.name.upper())

    def own_scope(self, node, scope=None):
        """The scope of the nearest SELECT that holds a node, so that a column of a subquery is read in its own scope."""
        parent = node.parent
        while parent is not None:
            if isinstance(parent, exp.Select) and id(parent) in self.by_select:
                return self.by_select[id(parent)]
            parent = parent.parent
        return scope or self.scopes[-1]

    def source_of(self, scope, column):
        selected = scope.selected_sources
        alias = column.table
        if alias:
            found = next((v for k, v in selected.items() if k.lower() == alias.lower()), None)
            return found[1] if found else None
        if len(selected) == 1:
            return next(iter(selected.values()))[1]
        name = column.name.lower()
        holders = [s for _, s in selected.values() if not isinstance(s, exp.Table) and name in self.outputs(s)]
        return holders[0] if len(holders) == 1 else None

    @staticmethod
    def branches(scope):
        inner = getattr(scope, "union_scopes", None) or getattr(scope, "set_operation_scopes", None)
        if not inner:
            return [scope]
        return [leaf for branch in inner for leaf in _Statement.branches(branch)]

    def outputs(self, scope):
        return {p.alias_or_name.lower() for select in _selects(scope.expression) for p in select.expressions}

    def refs(self, scope, column):
        scope = self.own_scope(column, scope)
        source = self.source_of(scope, column)
        name = column.name
        if source is None:
            select = scope.expression if isinstance(scope.expression, exp.Select) else None
            projection = next((p for p in (select.expressions if select else []) if p.alias and p.alias.lower() == name.lower()
                               and not isinstance(p.this, exp.Column)), None)
            if projection is not None and id(projection) not in self.busy:
                self.busy.add(id(projection))
                try:
                    return self.within(scope, projection.this)
                finally:
                    self.busy.discard(id(projection))
            return {("unknown", "?", name.upper())}
        if isinstance(source, exp.Table):
            kind, table = self.classify(source)
            return {(kind, table, name.upper() if kind == "base" else name.lower())}
        return self.output(source, name.lower())

    def output(self, scope, name):
        key = (id(scope), name)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = set()
        found = set()
        branches = self.branches(scope)
        first = _selects(branches[0].expression)
        names = [p.alias_or_name.lower() for p in first[0].expressions] if first else []
        for branch in branches:
            for select in _selects(branch.expression):
                for i, projection in enumerate(select.expressions):
                    if _is_star(projection):
                        continue
                    if (names[i] if i < len(names) else projection.alias_or_name.lower()) == name:
                        found |= self.within(branch, projection)
        self.memo[key] = found
        return found

    def within(self, scope, node):
        found = set()
        if node is None:
            return found
        for column in ([node] if isinstance(node, exp.Column) else list(node.find_all(exp.Column))):
            found |= self.refs(scope, column)
        return found


def _redacted(node, statement, scope):
    """An expression with each literal replaced by its type and each column by a numbered slot, with the slots' references."""
    copy = node.copy()
    originals = [node] if isinstance(node, exp.Column) else list(node.find_all(exp.Column))
    copies = [copy] if isinstance(copy, exp.Column) else list(copy.find_all(exp.Column))
    slots = []
    for i, (original, held) in enumerate(zip(originals, copies)):
        slots.append(sorted(statement.refs(scope, original)))
        replacement = exp.column(f"__slot{i}__")
        if held is copy:
            copy = replacement
        else:
            held.replace(replacement)
    for literal in list(copy.find_all(exp.Literal)) if not isinstance(copy, exp.Literal) else [copy]:
        if literal.is_string:
            kind = "<str>"
        else:
            kind = "<float>" if re.search(r"[.eE]", literal.this) else "<int>"
        replacement = exp.var(kind)
        if literal is copy:
            copy = replacement
        else:
            literal.replace(replacement)
    return copy.sql(), slots


def _conjuncts(node):
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    if isinstance(node, exp.Paren) and isinstance(node.this, exp.And):
        return _conjuncts(node.this)
    return [node] if node is not None else []


def _extract(piece, units, tables_written):
    """The raw lineage of one piece: tables, joins, filters, fields and aggregations, with unresolved references."""
    statement = _Statement(piece.query, units, piece.unit.name, tables_written)
    unit = piece.unit
    for scope in statement.scopes:
        for _, source in scope.selected_sources.values():
            if isinstance(source, exp.Table):
                unit.tables.add(statement.classify(source))
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        conditions = []
        for join in select.args.get("joins") or []:
            conditions += _conjuncts(join.args.get("on"))
        conditions += _conjuncts((select.args.get("where") or exp.Where()).this)
        conditions += _conjuncts((select.args.get("having") or exp.Having()).this)
        for condition in conditions:
            if condition is None:
                continue
            if isinstance(condition, exp.EQ):
                left = list(condition.left.find_all(exp.Column)) if not isinstance(condition.left, exp.Column) else [condition.left]
                right = list(condition.right.find_all(exp.Column)) if not isinstance(condition.right, exp.Column) else [condition.right]
                if len(left) == 1 and len(right) == 1 and (left[0].table or "").lower() != (right[0].table or "").lower():
                    unit.joins.append((statement.refs(scope, left[0]), statement.refs(scope, right[0])))
                    continue
            test, slots = _redacted(condition, statement, scope)
            if slots:
                unit.filters.append((test, slots))
        for node in select.find_all(exp.AggFunc):
            if isinstance(node.parent, exp.Window) or statement.own_scope(node, scope) is not scope:
                continue
            unit.aggregations.append(_redacted(node, statement, scope))
        group = select.args.get("group")
        if group is not None and group.expressions:
            text, slots = _redacted(exp.Tuple(expressions=[e for e in group.expressions]), statement, scope)
            unit.aggregations.append(("GROUP BY " + text[1:-1], slots))
    root = statement.scopes[-1]
    branches = statement.branches(root)
    names = piece.names
    for branch in branches:
        for select in _selects(branch.expression):
            own = names or [p.alias_or_name.lower() for p in select.expressions]
            for i, projection in enumerate(select.expressions):
                if i >= len(own):
                    break
                if _is_star(projection):
                    _star(unit, statement, branch, projection)
                    continue
                text, slots = _redacted(projection.this if isinstance(projection, exp.Alias) else projection, statement, branch)
                unit.fields.setdefault(own[i], []).append((text, slots))
            names = names or own


def _star(unit, statement, scope, star):
    """A SELECT *, expanded over each source whose columns are known here, and kept as a star for a written table."""
    table = star.table if isinstance(star, exp.Column) else None
    for alias, (_, source) in scope.selected_sources.items():
        if table and alias.lower() != table.lower():
            continue
        if isinstance(source, exp.Table):
            kind, name = statement.classify(source)
            unit.fields.setdefault("*", []).append(("*", [[(kind, name, "*")]]))
        else:
            for branch in statement.branches(source)[:1]:
                for select in _selects(branch.expression):
                    for projection in select.expressions:
                        if _is_star(projection):
                            continue
                        node = projection.this if isinstance(projection, exp.Alias) else projection
                        unit.fields.setdefault(projection.alias_or_name.lower(), []).append(_redacted(node, statement, branch))


# Resolving references through intermediates and other OMOP tables.

class _Resolver:
    def __init__(self, units, by_target):
        self.units, self.by_target = units, by_target
        self.memo = {}

    def owners(self, name):
        if name in self.units:
            return [self.units[name]]
        return self.by_target.get(name, [])

    def field_refs(self, unit, column, stack):
        held = unit.fields.get(column)
        if held is None:
            star = unit.fields.get("*")
            if not star:
                return None
            found = set()
            for _, slots in star:
                for slot in slots:
                    for kind, name, _ in slot:
                        if kind == "unit":
                            found |= self.resolve(("unit", name, column), stack)
                        elif kind == "base":
                            found.add(("base", name, column.upper()))
                        else:
                            found.add((kind, name, column))
            return found
        return {r for _, slots in held for slot in slots for ref in slot for r in self.resolve(ref, stack)}

    def resolve(self, ref, stack=()):
        kind, name, column = ref
        if kind != "unit":
            return {ref}
        key = (name, column)
        if key in self.memo:
            return self.memo[key]
        found = set()
        known = False
        for unit in self.owners(name):
            if unit.name in stack:
                continue
            refs = self.field_refs(unit, column, stack + (unit.name,))
            if refs is not None:
                known = True
                found |= refs
        found = {r for r in found if r[0] != "vocabulary"} or set()
        if not found:
            target = next((u.target for u in self.owners(name) if u.target), None)
            found = {("unit", target or name, column)} if known or target else {("unit", name, column)}
        if not stack:
            self.memo[key] = found
        return found

    def intermediates(self, unit, seen=None):
        """The intermediates that a unit reads, directly and through one another."""
        seen = set() if seen is None else seen
        for kind, name in unit.tables:
            if kind == "unit":
                for other in self.owners(name):
                    if other.target is None and other.name not in seen:
                        seen.add(other.name)
                        self.intermediates(other, seen)
        return [self.units[n] for n in sorted(seen) if n in self.units]


def _name(ref):
    kind, table, column = ref
    return f"{table}.{column}"


def _fill(text, slots, resolver):
    for i, slot in enumerate(slots):
        refs = sorted({_name(r) for ref in slot for r in resolver.resolve(ref)})
        text = text.replace(f"__slot{i}__", refs[0] if len(refs) == 1 else "{" + " | ".join(refs) + "}")
    return text


def _columns(slots, resolver):
    return sorted({_name(r) for slot in slots for ref in slot for r in resolver.resolve(ref) if r[0] != "vocabulary"})


def lineage(folder):
    """The lineage of a folder of SQL, as a dictionary ready for lineage.json."""
    folder = Path(folder)
    if not folder.is_dir():
        raise CompareError(f"{folder.name} is not a folder.")
    tables = cdm_tables()
    mode, found = _files(folder)
    units, unparsed, unrecognised, ignored = {}, [], [], 0
    parsed_files = []
    for relative, name, target, text, error in found:
        if error:
            unparsed.append({"file": relative, "error": error})
            continue
        parsed, error = _parse(text)
        if parsed is None:
            unparsed.append({"file": relative, "error": error})
            continue
        parsed_files.append((relative, name, target, parsed))
    pieces = []
    for relative, name, target, parsed in parsed_files:
        writes = [w for w in (_written(s) for s in parsed) if w is not None]
        ignored += sum(1 for s in parsed if _written(s) is None and not isinstance(s, exp.Command))
        bare = [w for w in writes if w[0] is None]
        for written, query, columns in writes:
            if written is None:
                if bare[-1][1] is not query:
                    continue  # only the final SELECT of a file writes the file's own table
                unit_name = name
                unit_target = target if mode != "plain" else (_target_by_name(name, tables) or _target_by_columns(_outputs(query), tables))
                if mode == "dbt" and unit_target is None:
                    unit_target = _target_by_columns(_outputs(query), tables)
                if unit_target is None and mode == "plain":
                    unrecognised.append(relative)
            else:
                unit_name, unit_target = written, (written if written in tables else None)
            unit = units.setdefault(unit_name, _Unit(unit_name))
            unit.target = unit.target or unit_target
            if relative not in unit.files:
                unit.files.append(relative)
            pieces.append(_Piece(unit, query, columns, relative))
    by_target = {}
    for unit in units.values():
        if unit.target:
            by_target.setdefault(unit.target, []).append(unit)
    for piece in pieces:
        try:
            _extract(piece, units, set(by_target))
        except Exception as problem:  # a query that the tracer cannot follow is recorded, never quoted
            unparsed.append({"file": piece.file, "error": type(problem).__name__})
    resolver = _Resolver(units, by_target)
    targets, catalogue = {}, {}

    def note(name):
        table, _, column = name.partition(".")
        if table.isupper() and column:
            catalogue.setdefault(table, set()).add(column)

    for target in sorted(by_target):
        members = by_target[target]
        own = list(members)
        for unit in members:
            own += [u for u in resolver.intermediates(unit) if u not in own]
        source_tables, reads, others = set(), set(), set()
        for unit in own:
            for kind, name in unit.tables:
                if kind == "base":
                    source_tables.add(name)
                if unit in members:
                    if kind == "unit":
                        owner = next((u.target for u in resolver.owners(name) if u.target), None)
                        if owner and owner != target:
                            others.add(owner)
                    elif kind in ("omop", "vocabulary"):
                        reads.add(f"{kind}:{name}")
        joins = {}
        for unit in own:
            for left, right in unit.joins:
                for a, b in itertools.product(sorted({r for ref in left for r in resolver.resolve(ref)}),
                                              sorted({r for ref in right for r in resolver.resolve(ref)})):
                    if "vocabulary" in (a[0], b[0]):
                        base = a if b[0] == "vocabulary" else b
                        if base[0] != "vocabulary":
                            reads.add(f"lookup:{_name(base)}")
                        continue
                    if a[1] == b[1] or "unknown" in (a[0], b[0]):
                        continue
                    pair = tuple(sorted([_name(a), _name(b)]))
                    tables_ = tuple(sorted([a[1], b[1]]))
                    joins.setdefault(tables_, set()).add(pair)
        filters = {}
        for unit in own:
            for test, slots in unit.filters:
                columns = _columns(slots, resolver)
                if columns:
                    filters.setdefault(_fill(test, slots, resolver), columns)
        fields = {}
        for unit in members:
            for field, held in unit.fields.items():
                if field == "*":
                    continue
                entry = fields.setdefault(field, {"columns": set(), "expressions": []})
                for text, slots in held:
                    entry["columns"] |= set(_columns(slots, resolver))
                    filled = _fill(text, slots, resolver)
                    filled = filled if len(filled) <= 300 else filled[:297] + "..."
                    if filled not in entry["expressions"]:
                        entry["expressions"].append(filled)
        aggregations = sorted({_fill(t, s, resolver) for unit in own for t, s in unit.aggregations})
        # The base tables behind every column taken from another OMOP table.
        for entry in fields.values():
            for name in entry["columns"]:
                if name.split(".")[0].isupper():
                    source_tables.add(name.split(".")[0])
        for pair_set in joins.values():
            for pair in pair_set:
                for name in pair:
                    if name.split(".")[0].isupper():
                        source_tables.add(name.split(".")[0])
        for columns in filters.values():
            for name in columns:
                if name.split(".")[0].isupper():
                    source_tables.add(name.split(".")[0])
        targets[target] = {
            "files": sorted({f for u in members for f in u.files}),
            "source_tables": sorted(source_tables),
            "other_omop_tables": sorted(others),
            "lookups": sorted(reads),
            "joins": [{"tables": list(t), "on": [list(p) for p in sorted(pairs)]} for t, pairs in sorted(joins.items())],
            "filters": [{"test": t, "columns": c} for t, c in sorted(filters.items())],
            "fields": {f: {"columns": sorted(e["columns"]), "expressions": e["expressions"]} for f, e in sorted(fields.items())},
            "aggregations": aggregations,
        }
        for item in targets[target]["fields"].values():
            for name in item["columns"]:
                note(name)
        for join in targets[target]["joins"]:
            for pair in join["on"]:
                for name in pair:
                    note(name)
        for item in targets[target]["filters"]:
            for name in item["columns"]:
                note(name)
        for table in source_tables:
            catalogue.setdefault(table, set())
    for unit in units.values():
        for kind, name in unit.tables:
            if kind == "base":
                catalogue.setdefault(name, set())
    return {
        "format": FORMAT,
        "private": PRIVATE_NOTE,
        "kind": mode,
        "files": {"read": len(found), "parsed": len(parsed_files), "unparsed": sorted(unparsed, key=lambda u: u["file"]),
                  "not_recognised": sorted(set(unrecognised)), "statements_ignored": ignored},
        "targets": targets,
        "sources": {t: sorted(c) for t, c in sorted(catalogue.items())},
    }


# Comparing two lineages.

def _load(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise CompareError(f"{Path(path).name} is not a lineage file.") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise CompareError(f"{Path(path).name} is not a lineage file.")
    return data


def schema_view(path):
    """The source tables, columns and joins that a saved hospital schema proposes for each OMOP table."""
    path = Path(path)
    if path.is_dir():
        candidate = path / "map" / "map.json"
        text = (candidate if candidate.is_file() else path / "map.json").read_text()
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith("map/map.json") or n == "map.json"]
            if not names:
                raise CompareError(f"{path.name} holds no hospital schema.")
            text = archive.read(names[0]).decode("utf-8")
    else:
        text = path.read_text()
    try:
        roles = json.loads(text).get("roles") or {}
    except ValueError:
        raise CompareError(f"{path.name} holds no hospital schema.") from None
    view = {}
    named = re.compile(r"\b([A-Z][A-Z0-9_]{2,})(?:\.([A-Z][A-Z0-9_]*))?\b")

    def add(target, item):
        held = view.setdefault(target, {"tables": set(), "columns": set(), "joins": set()})
        binding = (item or {}).get("binding")
        if isinstance(binding, dict) and binding.get("table"):
            held["tables"].add(binding["table"].upper())
            if binding.get("column"):
                held["columns"].add(f"{binding['table'].upper()}.{binding['column'].upper()}")
            for step in binding.get("path") or []:
                if isinstance(step, list) and len(step) >= 4:
                    held["tables"] |= {step[0].upper(), step[2].upper()}
                    pairs = [(step[1], step[3])] + [tuple(p) for p in (step[4] if len(step) > 4 else [])]
                    for a, b in pairs:
                        held["joins"].add(tuple(sorted([f"{step[0].upper()}.{a.upper()}", f"{step[2].upper()}.{b.upper()}"])))
        elif isinstance((item or {}).get("from"), str) and not item["from"].startswith("nothing"):
            for table, column in named.findall(item["from"]):
                held["tables"].add(table)
                if column:
                    held["columns"].add(f"{table}.{column}")

    for role, body in roles.items():
        for target in ROLE_TARGETS.get(role, []):
            if not isinstance(body, dict):
                continue
            add(target, body.get("rows"))
            for item in (body.get("columns") or {}).values():
                add(target, item)
    return {t: {"tables": sorted(v["tables"]), "columns": sorted(v["columns"]), "joins": [list(j) for j in sorted(v["joins"])]}
            for t, v in view.items()}


def _join_map(entry):
    return {tuple(j["tables"]): {tuple(p) for p in j["on"]} for j in (entry or {}).get("joins", [])}


def _filter_keys(entry):
    return {tuple(f["columns"]) for f in (entry or {}).get("filters", [])}


def _side(a, b):
    a, b = set(a), set(b)
    return {"shared": sorted(a & b), "only_ours": sorted(a - b), "only_theirs": sorted(b - a)}


def _and(items):
    items = list(items)
    if not items:
        return "nothing"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _route(edges):
    """Tables joined in a chain, as A through B to C, or as a list of the pairs joined."""
    if not edges:
        return None
    degree, links = {}, {}
    for a, b in edges:
        degree[a], degree[b] = degree.get(a, 0) + 1, degree.get(b, 0) + 1
        links.setdefault(a, []).append(b)
        links.setdefault(b, []).append(a)
    ends = sorted(t for t, d in degree.items() if d == 1)
    if len(ends) == 2 and all(d <= 2 for d in degree.values()) and len(edges) == len(degree) - 1:
        chain, previous = [ends[0]], None
        while len(chain) < len(degree):
            following = [t for t in links[chain[-1]] if t != previous]
            previous = chain[-1]
            chain.append(following[0])
        if len(chain) == 2:
            return f"{chain[0]} joined directly to {chain[1]}"
        return f"{chain[0]} through {_and(chain[1:-1])} to {chain[-1]}"
    return _and(f"{a} joined to {b}" for a, b in sorted(edges))


def _pathway(tables, edges):
    route = _route(edges)
    if route:
        return route
    return _and(sorted(tables)) if tables else "no source table"


def _compare_target(target, ours, theirs, schema):
    row = {"target": target, "label": ANAESTHESIA.get(target, target.upper())}
    if ours is None or theirs is None:
        row["status"] = "only_theirs" if ours is None else "only_ours"
        present = theirs or ours
        row["source_tables"] = present["source_tables"]
        row["routes_we_lack"] = ({"tables": present["source_tables"], "joins": [j["tables"] for j in present["joins"]]}
                                 if ours is None else {"tables": [], "joins": []})
    else:
        row["source_tables"] = _side(ours["source_tables"], theirs["source_tables"])
        ours_joins, theirs_joins = _join_map(ours), _join_map(theirs)
        joins = {"shared": [], "different_columns": [], "only_ours": [], "only_theirs": []}
        for pair in sorted(set(ours_joins) | set(theirs_joins)):
            if pair in ours_joins and pair in theirs_joins:
                if ours_joins[pair] == theirs_joins[pair]:
                    joins["shared"].append({"tables": list(pair), "on": [list(p) for p in sorted(ours_joins[pair])]})
                else:
                    joins["different_columns"].append({"tables": list(pair), "ours": [list(p) for p in sorted(ours_joins[pair])],
                                                       "theirs": [list(p) for p in sorted(theirs_joins[pair])]})
            elif pair in ours_joins:
                joins["only_ours"].append({"tables": list(pair), "on": [list(p) for p in sorted(ours_joins[pair])]})
            else:
                joins["only_theirs"].append({"tables": list(pair), "on": [list(p) for p in sorted(theirs_joins[pair])]})
        row["joins"] = joins
        row["filters"] = {k: [list(c) for c in v] for k, v in _side(_filter_keys(ours), _filter_keys(theirs)).items()}
        row["fields"] = _side(ours["fields"], theirs["fields"])
        row["fields"]["fed_differently"] = sorted(
            f for f in set(ours["fields"]) & set(theirs["fields"])
            if ours["fields"][f]["columns"] and theirs["fields"][f]["columns"]
            and not set(ours["fields"][f]["columns"]) & set(theirs["fields"][f]["columns"]))
        row["routes_we_lack"] = {"tables": row["source_tables"]["only_theirs"], "joins": [j["tables"] for j in joins["only_theirs"]]}
        differs = (row["source_tables"]["only_ours"] or row["source_tables"]["only_theirs"] or joins["different_columns"]
                   or joins["only_ours"] or joins["only_theirs"] or row["filters"]["only_ours"] or row["filters"]["only_theirs"]
                   or row["fields"]["only_ours"] or row["fields"]["only_theirs"])
        row["status"] = "differ" if differs else "agree"
    if schema is not None:
        row["schema"] = schema.get(target) or {"tables": [], "columns": [], "joins": []}
    row["uncertainty"] = _uncertainty(target, row, ours, theirs, schema) if target in ANAESTHESIA else []
    return row


def _schema_says(row, ours_edges, theirs_edges):
    """What the hospital schema's proposals say about two pathways, judged by the tables and joins that set them apart."""
    schema = row.get("schema")
    if not schema or not schema.get("tables"):
        return "The hospital schema has not established whether both are needed."
    proposed = set(schema["tables"])
    pairs = {tuple(sorted(p.split(".")[0] for p in join)) for join in schema.get("joins", [])}
    ours_tables = set(row["source_tables"]["only_ours"]) | {t for e in ours_edges for t in e}
    theirs_tables = set(row["source_tables"]["only_theirs"]) | {t for e in theirs_edges for t in e}
    mine = bool(proposed & (ours_tables - theirs_tables)) or bool(pairs & {tuple(sorted(e)) for e in ours_edges})
    yours = bool(proposed & (theirs_tables - ours_tables)) or bool(pairs & {tuple(sorted(e)) for e in theirs_edges})
    if mine and yours:
        return "The hospital schema proposes tables from both pathways, and it has not established whether both are needed."
    if mine:
        return "The hospital schema proposes our pathway, and it has not established whether the reference's is also needed."
    if yours:
        return "The hospital schema proposes the reference's pathway, and it has not established whether ours is also needed."
    return "The hospital schema has not established whether both are needed."


def _uncertainty(target, row, ours, theirs, schema):
    label, items = ANAESTHESIA[target], []
    check = "Validate their coverage separately before accepting either as complete."
    if row["status"] == "only_theirs":
        items.append(f"Only the reference writes {label}, which it reads from {_and(theirs['source_tables']) if theirs['source_tables'] else 'no source table'}. "
                     f"Our conversion does not write it, and the hospital schema has not established whether it is needed. "
                     f"Validate its coverage before relying on our conversion for this table.")
        return items
    if row["status"] == "only_ours":
        items.append(f"Only our conversion writes {label}, which it reads from {_and(ours['source_tables']) if ours['source_tables'] else 'no source table'}. "
                     f"The reference does not write it, so the reference offers no supporting evidence for this table. "
                     f"Validate its coverage against the clinical record before accepting it as complete.")
        return items
    joins = row["joins"]
    ours_edges = [tuple(j["tables"]) for j in joins["only_ours"]] + [tuple(j["tables"]) for j in joins["different_columns"]]
    theirs_edges = [tuple(j["tables"]) for j in joins["only_theirs"]] + [tuple(j["tables"]) for j in joins["different_columns"]]
    tables = row["source_tables"]
    if tables["only_ours"] or tables["only_theirs"] or joins["only_ours"] or joins["only_theirs"] or joins["different_columns"]:
        if joins["different_columns"] and not (joins["only_ours"] or joins["only_theirs"]):
            ours_text = _and(f"{a} = {b}" for j in joins["different_columns"] for a, b in j["ours"])
            theirs_text = _and(f"{a} = {b}" for j in joins["different_columns"] for a, b in j["theirs"])
            route_ours, route_theirs = f"joins on {ours_text}", f"joins on {theirs_text}"
        else:
            route_ours = "reads " + _pathway(tables["only_ours"], ours_edges)
            route_theirs = "reads " + _pathway(tables["only_theirs"], theirs_edges)
        items.append(f"Two candidate pathways exist for {label}: ours {route_ours}, the reference {route_theirs}. "
                     f"{_schema_says(row, ours_edges, theirs_edges)} {check}")
    filters = row["filters"]
    if filters["only_ours"] or filters["only_theirs"]:
        ours_text = _and(_and(c) for c in filters["only_ours"]) if filters["only_ours"] else "no further column"
        theirs_text = _and(_and(c) for c in filters["only_theirs"]) if filters["only_theirs"] else "no further column"
        items.append(f"The two conversions choose the rows of {label} differently: ours tests {ours_text}, the reference tests "
                     f"{theirs_text}. The hospital schema has not established which test matches the hospital's workflow. "
                     f"Validate the rows that each test keeps and leaves out before accepting either as complete.")
    fields = row["fields"]
    if fields["only_ours"] or fields["only_theirs"] or fields["fed_differently"]:
        parts = []
        if fields["only_ours"]:
            parts.append(f"only ours fills {_and(fields['only_ours'])}")
        if fields["only_theirs"]:
            parts.append(f"only the reference fills {_and(fields['only_theirs'])}")
        if fields["fed_differently"]:
            parts.append(f"the two fill {_and(fields['fed_differently'])} from different columns")
        items.append(f"The two conversions fill the fields of {label} differently: {'; '.join(parts)}. "
                     f"The hospital schema has not established which source is right for each. "
                     f"Validate each of these fields against the clinical record before accepting either as complete.")
    return items


def compare(ours, theirs, schema=None):
    """The comparison of two lineages, with the counts-only summary and the private detail."""
    targets = sorted(set(ours["targets"]) | set(theirs["targets"]))
    rows = [_compare_target(t, ours["targets"].get(t), theirs["targets"].get(t), schema) for t in targets]
    count = lambda status: sum(1 for r in rows if r["status"] == status)  # noqa: E731
    summary = {
        "targets_compared": len(rows), "agreeing": count("agree"), "differing": count("differ"),
        "only_ours": count("only_ours"), "only_theirs": count("only_theirs"),
        "routes_we_lack": sum(len(r["routes_we_lack"]["tables"]) + len(r["routes_we_lack"]["joins"]) for r in rows),
        "uncertainty_items": sum(len(r["uncertainty"]) for r in rows),
    }
    return {
        "format": REPORT_FORMAT,
        "summary": {"shareable": True, "note": "This summary holds counts alone and names nothing, so it may be shared or "
                                               "shown to a language model.", "counts": summary},
        "detail": {"private": True, "note": "The detail names the reference's tables and columns. It stays wherever the "
                                            "reference is kept, and it is not shared or shown to a language model.",
                   "schema_given": schema is not None,
                   "unparsed": {"ours": len(ours["files"]["unparsed"]), "theirs": len(theirs["files"]["unparsed"])},
                   "targets": rows},
    }


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def summary_lines(counts):
    """The summary, in sentences that name nothing."""
    c = counts
    return [
        f"The comparison covered {_plural(c['targets_compared'], 'OMOP table', 'OMOP tables')}.",
        f"The two conversions agree on {c['agreeing']} of them and differ on {c['differing']}.",
        f"Only our conversion writes {_plural(c['only_ours'], 'table', 'tables')}, and only the reference writes {c['only_theirs']}.",
        f"The reference reads {_plural(c['routes_we_lack'], 'route', 'routes')} that our conversion does not, counting each "
        f"source table and each pair of joined tables once for each OMOP table.",
        f"The comparison raised {_plural(c['uncertainty_items'], 'uncertainty item', 'uncertainty items')} for the anaesthesia tables.",
    ]


def _code(items):
    items = list(items)
    return ", ".join(f"`{i}`" for i in items) if items else "none"


def markdown(report):
    counts = report["summary"]["counts"]
    out = ["# Mapping comparison", "", "## Summary, which may be shared", "",
           "This summary holds counts alone and names no table or column, so it may be shared or shown to a language model.", ""]
    out += [f"- {line}" for line in summary_lines(counts)]
    out += ["", "## Detail, which is private", "",
            "Everything below names the reference's tables and columns. Keep it wherever the reference is kept, and do not "
            "share it or show it to a language model. Agreement between the two conversions is supporting evidence and not "
            "proof, because workflows and configuration differ between hospitals and two conversions can share a mistake.", ""]
    detail = report["detail"]
    if detail["unparsed"]["ours"] or detail["unparsed"]["theirs"]:
        out += [f"Some files could not be read: {detail['unparsed']['ours']} of ours and {detail['unparsed']['theirs']} of the "
                f"reference's. Their lineage is missing from this comparison, and the lineage files list them.", ""]
    words = {"agree": "The two conversions agree.", "differ": "The two conversions differ.",
             "only_ours": "Only our conversion writes this table.", "only_theirs": "Only the reference writes this table."}
    for row in detail["targets"]:
        out += [f"### {row['target'].upper()}", "", words[row["status"]], ""]
        if row["status"] in ("agree", "differ"):
            st, jn, fl, fd = row["source_tables"], row["joins"], row["filters"], row["fields"]
            out += [f"- Source tables read by both: {_code(st['shared'])}.",
                    f"- Source tables read only by ours: {_code(st['only_ours'])}.",
                    f"- Source tables read only by the reference: {_code(st['only_theirs'])}."]
            out += [f"- Joins made by both: {_code(' = '.join(p) for j in jn['shared'] for p in j['on'])}."]
            for j in jn["different_columns"]:
                out += [f"- Both join {_code(j['tables'])}, ours on {_code(' = '.join(p) for p in j['ours'])} and the "
                        f"reference on {_code(' = '.join(p) for p in j['theirs'])}."]
            out += [f"- Joins made only by ours: {_code(' = '.join(p) for j in jn['only_ours'] for p in j['on'])}.",
                    f"- Joins made only by the reference: {_code(' = '.join(p) for j in jn['only_theirs'] for p in j['on'])}.",
                    f"- Columns tested by both: {_code(' and '.join(c) for c in fl['shared'])}.",
                    f"- Columns tested only by ours: {_code(' and '.join(c) for c in fl['only_ours'])}.",
                    f"- Columns tested only by the reference: {_code(' and '.join(c) for c in fl['only_theirs'])}.",
                    f"- Fields filled by both: {len(fd['shared'])}. Fields filled only by ours: {_code(fd['only_ours'])}. "
                    f"Fields filled only by the reference: {_code(fd['only_theirs'])}.",
                    f"- Fields that the two fill from different columns: {_code(fd['fed_differently'])}."]
        else:
            out += [f"- Source tables: {_code(row['source_tables'])}."]
        if "schema" in row:
            out += [f"- The hospital schema proposes these source tables: {_code(row['schema']['tables'])}."]
        out += [""]
        if row["uncertainty"]:
            out += ["Uncertainty items:", ""] + [f"1. {item}" for item in row["uncertainty"]] + [""]
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.compare", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    reference = commands.add_parser("reference", help="write the lineage of a folder of SQL")
    reference.add_argument("folder")
    reference.add_argument("--out", required=True)
    report = commands.add_parser("report", help="compare two lineage files")
    report.add_argument("--ours", required=True)
    report.add_argument("--theirs", required=True)
    report.add_argument("--schema")
    report.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "reference":
            data = lineage(args.folder)
            Path(args.out).write_text(json.dumps(data, indent=1) + "\n")
            files = data["files"]
            print(f"The lineage covers {_plural(len(data['targets']), 'OMOP table', 'OMOP tables')} from "
                  f"{_plural(files['parsed'], 'file', 'files')}, and {_plural(len(files['unparsed']), 'file', 'files')} could not be read.")
            print(PRIVATE_NOTE)
        else:
            schema = schema_view(args.schema) if args.schema else None
            found = compare(_load(args.ours), _load(args.theirs), schema)
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.json").write_text(json.dumps(found, indent=1) + "\n")
            (out / "report.md").write_text(markdown(found) + "\n")
            for line in summary_lines(found["summary"]["counts"]):
                print(line)
            print("Only the summary at the top of the report may be shared. The detail names the reference's tables and "
                  "columns, and it stays wherever the reference is kept.")
    except (CompareError, OSError) as problem:
        print(problem if isinstance(problem, CompareError) else "A file could not be read or written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
