"""Turns the SQL of one request into findings.

A finding is a plain tuple whose strings come from the catalogue or from the
fixed vocabulary. Nothing in a finding is copied from the request.

    ("table", table)
    ("column", table, column, role)
    ("join", left_table, left_column, right_table, right_column, join_kind)
    ("filter", table, column, operator, value_kind, value)
    ("derivation", expression, columns)
"""
import re
from collections import Counter
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from . import skeleton
from .checks import VALUE_KINDS, VALUE_OPERATORS, is_numeric, normalise
from .statements import drop_old_hints, parse

GO = re.compile(r"^\s*GO\s*;?\s*$", re.I | re.M)
QUERY = (exp.Select, exp.SetOperation)
COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Like, exp.Between, exp.Is)
OPERATOR = {exp.EQ: "=", exp.NEQ: "<>", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<=",
            exp.In: "IN", exp.Like: "LIKE", exp.Between: "BETWEEN", exp.Is: "IS NULL"}
NEGATED = {"IN": "NOT IN", "LIKE": "NOT LIKE", "BETWEEN": "NOT BETWEEN", "IS NULL": "IS NOT NULL",
           "=": "<>", "<>": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
FLIPPED = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}
DYNAMIC = {"execute", "executesql"}


@dataclass
class RequestResult:
    statements: int = 0
    findings: set = field(default_factory=set)
    unresolved: Counter = field(default_factory=Counter)
    parsed: bool = True


def _is_base_table(table):
    """True for a real table reference: not a temp table and not a table variable."""
    this = table.this
    return isinstance(this, exp.Identifier) and not this.args.get("temporary") and not this.name.startswith("#")


def _names_something_else(table):
    """True where the parser uses a table node for a database, a procedure or an object being made."""
    parent = table.parent
    if isinstance(parent, (exp.Use, exp.Drop)) or parent.key in DYNAMIC:
        return True
    if isinstance(parent, (exp.Schema, exp.StoredProcedure)):
        parent = parent.parent
    return isinstance(parent, exp.Create)


def _outermost_queries(statement):
    if isinstance(statement, QUERY):
        return [statement]
    found = []
    for node in statement.find_all(*QUERY):
        parent = node.parent
        while parent is not None and not isinstance(parent, QUERY):
            parent = parent.parent
        if parent is None:
            found.append(node)
    return found


def _as_query(change):
    """An UPDATE or DELETE read as a query, so that its joins and filters are found like any other."""
    source = (change.args.get("from_") or exp.From(this=change.this.copy())).copy()
    query = exp.Select(expressions=[exp.Literal.number(1)], from_=source)
    # In UPDATE ... FROM a JOIN b, the parser hangs the joins on the first table.
    hung = source.this.args.get("joins") if isinstance(source.this, exp.Table) else None
    if hung:
        query.set("joins", [join.copy() for join in hung])
        source.this.set("joins", None)
    if change.args.get("joins"):
        query.set("joins", (query.args.get("joins") or []) + [join.copy() for join in change.args["joins"]])
    if change.args.get("where"):
        query.set("where", change.args["where"].copy())
    return query


def _owners(root):
    """For every node under a query, the SELECT it belongs to. Built in one walk."""
    owners, stack = {}, [(root, None)]
    while stack:
        node, select = stack.pop()
        owners[id(node)] = select
        inner = node if isinstance(node, exp.Select) else select
        stack.extend((child, inner) for child in node.iter_expressions())
    return owners


def _branches(expression):
    """The SELECTs of a query: one, or every branch of a UNION."""
    if isinstance(expression, exp.SetOperation):
        yield from _branches(expression.left.unnest())
        yield from _branches(expression.right.unnest())
    elif isinstance(expression, exp.Select):
        yield expression


def _negated(comparison):
    """True where an odd number of NOTs applies to the comparison, looking out through brackets."""
    count = 1 if comparison.args.get("negate") else 0
    node = comparison.parent
    while isinstance(node, (exp.Paren, exp.Not)):
        count += isinstance(node, exp.Not)
        node = node.parent
    return count % 2 == 1


class _Resolver:
    """Resolves a column to its catalogue entry through the scopes that enclose it."""

    def __init__(self, qualified, catalogue, temp_tables):
        self.catalogue = catalogue
        self.temp_tables = temp_tables
        self.scopes = {id(s.expression): s for s in traverse_scope(qualified)}

    def source(self, column):
        if not column.table:
            return None
        node = column.parent
        while node is not None and id(node) not in self.scopes:
            node = node.parent
        scope = self.scopes.get(id(node)) if node is not None else None
        while scope is not None:
            source = scope.sources.get(column.table)
            if source is not None:
                return source
            scope = scope.parent
        return None

    def __call__(self, column, depth=0):
        source = self.source(column)
        if isinstance(source, Scope):
            # A CTE or a derived table: follow the column back through its definition.
            return self.through(source, column.name, depth)
        if not isinstance(source, exp.Table):
            return None
        if not _is_base_table(source):
            return self.temp_tables.get(source.name.upper(), {}).get(column.name.upper())
        table = self.catalogue.table(source.name)
        return table.column(column.name) if table else None

    def through(self, scope, name, depth):
        if depth > 30:
            return None
        selects = list(_branches(scope.expression))
        if not selects:
            return None
        position = next((i for i, projection in enumerate(selects[0].expressions)
                         if projection.alias_or_name.upper() == name.upper()), None)
        if position is None:
            return None
        # Every branch of a UNION must lead to the same column. Otherwise the column is not attributed.
        found = set()
        for select in selects:
            if position >= len(select.expressions):
                return None
            inner = _passed_through(select.expressions[position].unalias())
            found.add(self(inner, depth + 1) if inner is not None else None)
        return found.pop() if len(found) == 1 else None

    def outputs(self, query):
        """The catalogue column behind each output column of a query, in order, with its name."""
        selects = list(_branches(query))
        if not selects:
            return []
        result = []
        for position, projection in enumerate(selects[0].expressions):
            found = set()
            for select in selects:
                inner = _passed_through(select.expressions[position].unalias()) if position < len(select.expressions) else None
                found.add(self(inner) if inner is not None else None)
            result.append((projection.alias_or_name.upper(), found.pop() if len(found) == 1 else None))
        return result


def _passed_through(node):
    """The single column that an output column carries unchanged in meaning, if there is one.

    A cast or a change of case does not alter what a column means, so it is looked through.
    Anything that computes a new value is not, because a filter on the result would then be
    reported against the wrong column.
    """
    while True:
        if isinstance(node, exp.Column):
            return node
        if isinstance(node, exp.Convert):
            node = node.expression
        elif isinstance(node, (exp.Upper, exp.Lower, exp.Trim, exp.Cast, exp.TryCast, exp.Paren)):
            node = node.this
        elif isinstance(node, exp.Coalesce) and all(isinstance(e, exp.Literal) for e in node.expressions):
            node = node.this
        else:
            return None


def _value_kind(node):
    if isinstance(node, exp.Literal):
        return "string" if node.is_string else "number"
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return "number"
    if isinstance(node, (exp.Parameter, exp.SessionParameter, exp.Placeholder)):
        return "variable"
    if isinstance(node, exp.Null):
        return "null"
    if isinstance(node, (exp.Subquery, exp.Select, exp.SetOperation)):
        return "subquery"
    return "expression"


def _values_kind(nodes):
    kinds = {_value_kind(n) for n in nodes}
    return kinds.pop() if len(kinds) == 1 else "expression"


def _join_kind(join):
    if join is None or isinstance(join.this, exp.Lateral):
        return "where"
    side = (join.args.get("side") or "").upper()
    if side in ("LEFT", "RIGHT", "FULL"):
        return side.lower()
    return "cross" if (join.args.get("kind") or "").upper() == "CROSS" else "inner"


class _Extractor:
    def __init__(self, catalogue, held_back, result, dialect, confirmed):
        self.dialect = dialect
        # The values that the check results list for each column. A literal is written only if it is among them.
        self.confirmed = confirmed
        self.catalogue = catalogue
        self.names = catalogue.names()
        self.held_back = held_back
        self.result = result
        # A temp table or table variable made earlier in the request: the catalogue column behind
        # each of its columns, and the order of its columns where the request declared them.
        self.temp_tables = {}
        self.temp_columns = {}
        self.owners = {}

    def add(self, *finding):
        self.result.findings.add(finding)

    def own_columns(self, node, select):
        """The columns under a node that belong to this SELECT and not to a nested one."""
        return [c for c in node.find_all(exp.Column) if self.owners.get(id(c)) is select]

    def use(self, column, role, resolve):
        found = resolve(column)
        if found is not None:
            self.add("column", found.table, found.name, role)
        elif not column.table:
            self.result.unresolved["column_not_attributed"] += 1

    def statement(self, statement):
        self.result.statements += 1
        if isinstance(statement, exp.Command):
            self.result.unresolved["opaque_statement"] += 1
            return
        for node in statement.walk():
            if node.key in DYNAMIC:
                self.result.unresolved["dynamic_sql"] += 1
            elif isinstance(node, exp.Command):
                # A part of the statement that the parser kept only as text. Anything inside it is unseen.
                self.result.unresolved["opaque_statement"] += 1
        self.declared_tables(statement)
        cte_names = {c.alias_or_name.upper() for c in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            if not _is_base_table(table) or table.name.upper() in cte_names or _names_something_else(table):
                continue
            if isinstance(table.parent, exp.Update) and table.parent.args.get("from_"):
                continue    # the target of UPDATE ... FROM is an alias, or a table named again in the FROM
            entry = self.catalogue.table(table.name)
            if entry is not None:
                self.add("table", entry.name)
            elif table.name.upper() in self.held_back:
                self.result.unresolved["local_table_held_back"] += 1
            else:
                self.result.unresolved["table_not_in_catalogue"] += 1
        queries = [(query, query) for query in _outermost_queries(statement)]
        queries += [(change, _as_query(change)) for change in statement.find_all(exp.Update, exp.Delete)]
        for original, query in queries:
            try:
                qualified = qualify(query.copy(), schema=self.catalogue.sqlglot_schema(self.dialect), dialect=self.dialect,
                                    validate_qualify_columns=False, quote_identifiers=False, identify=False)
                resolve = _Resolver(qualified, self.catalogue, self.temp_tables)
            except Exception:
                self.result.unresolved["qualify_error"] += 1
                continue
            self.owners = _owners(qualified)
            for select in qualified.find_all(exp.Select):
                self.select(select, resolve)
            if original is query:
                self.remember_temp_table(original, qualified, resolve)

    def declared_tables(self, statement):
        """Notes the columns of a temp table or table variable as declared, and forgets one that is dropped."""
        def note(table, schema):
            name = table.name.upper()
            self.temp_columns[name] = [column.name.upper() for column in schema.expressions if column.name]
            self.temp_tables[name] = {}

        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema):
            table = statement.this.this
            if isinstance(table, exp.Table) and not _is_base_table(table):
                note(table, statement.this)
        elif isinstance(statement, exp.Drop) and isinstance(statement.this, exp.Table) and not _is_base_table(statement.this):
            self.temp_tables.pop(statement.this.name.upper(), None)
            self.temp_columns.pop(statement.this.name.upper(), None)
        elif isinstance(statement, exp.Declare):
            for item in statement.expressions:
                target = item.this[0] if isinstance(item.this, list) else item.this
                if isinstance(item.args.get("kind"), exp.Schema) and target is not None:
                    self.temp_columns[target.name.upper()] = [c.name.upper() for c in item.args["kind"].expressions if c.name]
                    self.temp_tables[target.name.upper()] = {}

    def remember_temp_table(self, original, qualified, resolve):
        """Records what SELECT ... INTO #name or INSERT INTO #name SELECT put into a temp table."""
        target, names, inserted = None, None, False
        into = qualified.args.get("into") if isinstance(qualified, exp.Select) else None
        if into is not None and isinstance(into.this, exp.Table):
            target = into.this
        elif isinstance(original.parent, exp.Insert):
            target, inserted = original.parent.this, True
            if isinstance(target, exp.Schema):
                target, names = target.this, [e.name.upper() for e in target.expressions]
        if not isinstance(target, exp.Table) or _is_base_table(target):
            return
        name = target.name.upper()
        outputs = resolve.outputs(qualified)
        if inserted:
            # INSERT fills the columns in the order given, or in the order the table was declared with.
            names = names or self.temp_columns.get(name)
            if names is None:
                return
            mapping = {column: found for column, (_, found) in zip(names, outputs)}
            earlier = self.temp_tables.get(name)
            if earlier:
                # A second INSERT keeps only the columns on which both agree.
                mapping = {column: found if earlier.get(column) == found else None for column, found in mapping.items()}
            self.temp_tables[name] = mapping
        else:
            # SELECT ... INTO makes the table anew.
            self.temp_tables[name] = dict(outputs)

    def select(self, select, resolve):
        for projection in select.expressions:
            inner = projection.unalias()
            if isinstance(inner, exp.Column):
                self.use(inner, "selected", resolve)
                continue
            columns = self.own_columns(inner, select)
            for column in columns:
                self.use(column, "derived", resolve)
            resolved = sorted({f"{c.table}.{c.name}" for c in map(resolve, columns) if c is not None})
            if resolved:
                text = skeleton.write(inner, resolve, self.names)
                if text is None:
                    self.result.unresolved["derivation_withheld"] += 1
                else:
                    self.add("derivation", text, " ".join(resolved))
        for join in select.args.get("joins") or []:
            condition = join.args.get("on")
            if condition is not None:
                self.predicates(condition, select, resolve, join)
        where = select.args.get("where")
        if where is not None:
            self.predicates(where, select, resolve, None)
        having = select.args.get("having")
        if having is not None:
            self.predicates(having, select, resolve, None)
        group = select.args.get("group")
        if group is not None:
            for column in self.own_columns(group, select):
                self.use(column, "grouped", resolve)

    def predicates(self, condition, select, resolve, join):
        seen = set()
        for comparison in condition.find_all(*COMPARISONS):
            if self.owners.get(id(comparison)) is not select:
                continue
            left, right = comparison.this, comparison.args.get("expression")
            if isinstance(comparison, exp.EQ) and left is not None and right is not None:
                # A key that is cast or trimmed on one side of a join is still that key.
                left, right = _passed_through(left), _passed_through(right)
            if (isinstance(comparison, exp.EQ) and isinstance(left, exp.Column) and isinstance(right, exp.Column)
                    and not _negated(comparison)):
                a, b = resolve(left), resolve(right)
                if a is not None and b is not None and left.table != right.table:
                    self.join(left, a, right, b, join)
                    seen.update((id(left), id(right)))
                    continue
            self.comparison(comparison, resolve)
            self.filter(comparison, select, resolve)
        for column in self.own_columns(condition, select):
            if id(column) not in seen:
                self.use(column, "filtered", resolve)
            else:
                self.use(column, "joined", resolve)

    def comparison(self, comparison, resolve):
        """Records that one column is compared with another as earlier or smaller, as in A <= B."""
        if _negated(comparison):
            return
        pairs = []
        if isinstance(comparison, exp.Between):
            low, high = comparison.args.get("low"), comparison.args.get("high")
            pairs = [(low, "<=", comparison.this), (comparison.this, "<=", high)]
        elif isinstance(comparison, (exp.GT, exp.GTE)):
            pairs = [(comparison.expression, "<" if isinstance(comparison, exp.GT) else "<=", comparison.this)]
        elif isinstance(comparison, (exp.LT, exp.LTE)):
            pairs = [(comparison.this, "<" if isinstance(comparison, exp.LT) else "<=", comparison.expression)]
        for first, operator, second in pairs:
            first = _passed_through(first) if first is not None else None
            second = _passed_through(second) if second is not None else None
            if first is None or second is None:
                continue
            a, b = resolve(first), resolve(second)
            if a is not None and b is not None and a != b:
                self.add("comparison", a.table, a.name, operator, b.table, b.name)

    def join(self, left, a, right, b, join):
        kind = _join_kind(join)
        if kind in ("left", "right", "full") and left.table == join.alias_or_name:
            # The table being joined goes on the right, so that the optional side is clear.
            a, b = b, a
        elif kind not in ("left", "right", "full") and (b.table, b.name) < (a.table, a.name):
            a, b = b, a
        self.add("join", a.table, a.name, b.table, b.name, kind)

    def filter(self, comparison, select, resolve):
        operator = OPERATOR[type(comparison)]
        subject, others = comparison.this, []
        if isinstance(comparison, exp.In):
            others = comparison.expressions or [comparison.args.get("query") or comparison.args.get("unnest")]
        elif isinstance(comparison, exp.Between):
            others = [comparison.args["low"], comparison.args["high"]]
        else:
            others = [comparison.expression]
            if (not self.own_columns(subject, select) and self.own_columns(others[0], select)
                    and not isinstance(subject, exp.Column)):
                subject, others = others[0], [subject]
                operator = FLIPPED.get(operator, operator)
        # Only a column that is compared as itself counts. A comparison of a computed value, such as
        # COUNT(x) >= 10 or DATENAME(dw, x) = 'Saturday', is not a filter on the column.
        carried = _passed_through(subject)
        columns = [carried] if carried is not None and self.owners.get(id(carried)) is select else []
        others = [o for o in others if o is not None]
        if len(columns) != 1 or any(self.own_columns(o, select) or isinstance(o, exp.Column) for o in others):
            return
        found = resolve(columns[0])
        if found is None:
            return
        if isinstance(comparison, exp.Is) and not isinstance(comparison.expression, exp.Null):
            return
        if _negated(comparison):
            operator = NEGATED.get(operator, operator)
        kind = "null" if isinstance(comparison, exp.Is) else _values_kind(others)
        values = []
        if kind in VALUE_KINDS and operator in VALUE_OPERATORS:
            listed = self.confirmed.get((found.table.upper(), found.name.upper()), {})
            numeric = is_numeric(found)
            for other in others:
                literal = other.this if isinstance(other, exp.Neg) else other
                sign = "-" if isinstance(other, exp.Neg) else ""
                # The value written is the check results' own spelling, never the request's.
                values.append(listed.get(normalise(sign + str(literal.this), numeric))
                              if isinstance(literal, exp.Literal) else None)
        for value in values:
            if value is not None:
                self.add("filter", found.table, found.name, operator, kind, value)
        if not values or any(value is None for value in values):
            # The value stays blank where the check results do not confirm it.
            self.add("filter", found.table, found.name, operator, kind, "")


def decode(data):
    """Decodes the bytes of a .sql file. SQL Server's tools save UTF-8 with a byte order mark, or UTF-16."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def analyse_request(sql, catalogue, held_back=frozenset(), dialect="tsql", confirmed=None):
    """The findings of one request. Each request is read once for the same catalogue, rules and confirmed checks."""
    from . import memo
    key = (memo.digest(sql), memo.catalogue(catalogue), tuple(sorted(held_back)), dialect,
           memo.digest(sorted((repr(k), repr(v)) for k, v in (confirmed or {}).items())))
    kept = memo.remembered("request", key, lambda: _analyse_request(sql, catalogue, held_back, dialect, confirmed), copied=False)
    return RequestResult(kept.statements, set(kept.findings), Counter(kept.unresolved), kept.parsed)


def _analyse_request(sql, catalogue, held_back=frozenset(), dialect="tsql", confirmed=None):
    result = RequestResult()
    extractor = _Extractor(catalogue, held_back, result, dialect, confirmed or {})
    try:
        sql = drop_old_hints(sql.lstrip("﻿"))
    except Exception:
        pass
    # GO separates batches in SQL Server's tools and is not part of the language.
    for batch in GO.split(sql) if dialect == "tsql" else [sql]:
        if not batch.strip():
            continue
        statements = parse(batch, dialect)
        if statements is None:
            result.parsed = False
            result.unresolved["parse_error"] += 1
            continue
        for statement in statements:
            if statement is None or isinstance(statement, exp.Semicolon):
                continue
            try:
                extractor.statement(statement)
            except Exception:
                # One statement that the tool cannot handle must not end the run. It is counted as not analysed.
                result.unresolved["opaque_statement"] += 1
    return result
