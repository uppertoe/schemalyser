"""The source query restructured to start from the cohort, so that it is fit to run on a large database.

target.source_draft composes the steps of the conversion as they are: every row of every step, numbered with
DENSE_RANK and ROW_NUMBER over whole tables, joined to each other on those numbers and on keys cast to text,
with the question's own conditions applied at the end. This module rewrites that composition, one checked
rewrite at a time, so that it reads only what the answer needs, joins on the source keys, and applies the
question's conditions as early as they can go. Each rewrite keeps the answer exactly, and each is made only
where a syntactic condition shows that it does; where one cannot be shown, restructure returns None with the
reason, and the caller keeps the composition as it is.

The rewrites:

1. A surrogate number DENSE_RANK() OVER (ORDER BY k), the identifier that a step gives each row, is replaced
   by k itself, which is equal for two rows exactly when the number is, and orders them in the same way. The
   layer's offset, a constant added to it, goes with it. This needs one step to write the table.
2. A join on a key cast to text, A.f = CAST(e AS T), where the step that writes f writes it as CAST(c AS T),
   becomes A.f__key = e on the raw key, where c and e are both whole numbers or both text in the catalogue.
3. A column that no reader uses is dropped, and so is a LEFT JOIN that no remaining expression reads, where
   it is shown to give at most one row: a lookup of mapping rows whose codes are unique under its vocabulary,
   a derived table filtered to the first row of each key, or a table whose key is the first column of a single
   source table that the catalogue says may not be empty. A LEFT JOIN that cannot be shown so is kept, with a
   comment. A common table expression that nothing reads is dropped.
4. The question's conditions are pushed into the step that writes the table they test, where that table is
   read only there: a concept that the question compares with becomes a condition on the local codes that map
   to it, and a join of the question's cohort to a column of the step becomes a condition that the column is
   among the cohort's values. The common table expressions are then put in an order in which each follows what
   it reads, so that the cohort comes first.

The result is accepted only where no DENSE_RANK remains and every ROW_NUMBER either numbers within a key or
lies in a step whose rows the question's conditions restrict.
"""
import re

import sqlglot
from sqlglot import exp

WHOLE = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT"}
TEXT = {"CHAR", "VARCHAR", "NCHAR", "NVARCHAR"}


class Unsafe(Exception):
    """A rewrite could not be shown to keep the answer."""


def _sql(node):
    return node.sql(dialect="tsql", comments=False)


def _with(tree):
    return tree.args.get("with_") or tree.args.get("with")


def _wrapper(select):
    """The inner SELECT of a step that the layer's offset wraps, as FROM (...) AS step, or None."""
    source = select.args.get("from_") if isinstance(select, exp.Select) else None
    node = source.this if source is not None else None
    if isinstance(node, exp.Subquery) and node.alias == "step" and isinstance(node.this, exp.Select):
        return node.this
    return None


def _family(catalogue, table, column):
    entry = catalogue.table(table) if table else None
    field = entry.column(column) if entry is not None else None
    if field is None:
        return None
    kind = field.data_type.strip().upper()
    if kind in WHOLE or (kind in ("NUMERIC", "DECIMAL") and field.scale == 0):
        return "whole"
    if kind in TEXT:
        return "text"
    return None


def _aliases(select):
    """Alias -> table node or subquery node, for a SELECT's own FROM and joins."""
    found = {}
    source = select.args.get("from_")
    nodes = ([source.this] if source is not None else []) + [j.this for j in select.args.get("joins") or []]
    for node in nodes:
        found[node.alias_or_name.upper()] = node
    return found


def _ancestors(node):
    while node is not None:
        node = node.parent
        if node is not None:
            yield node


def _conjuncts(node):
    if node is None:
        return []
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    return [node]


class _Query:
    def __init__(self, text):
        self.tree = sqlglot.parse_one(text, dialect="tsql")
        self.ctes = {cte.alias: cte for cte in _with(self.tree).expressions}
        self.restricted = set()
        self.catalogue = None
        self.restricted_codes = {}      # (id of a step's SELECT, code expression) -> the codes that a pushed condition allows
        self.kept_comments = []

    # The common table expressions and what reads them.

    def references(self, name):
        """The table nodes, anywhere, that read the common table expression name."""
        return [t for t in self.tree.find_all(exp.Table) if t.name == name and not t.db]

    def writer(self, union):
        """The step that a union of one branch reads, or None."""
        select = self.ctes[union].this
        if not isinstance(select, exp.Select) or select.args.get("joins"):
            return None
        source = select.args.get("from_")
        node = source.this if source is not None else None
        return node.name if isinstance(node, exp.Table) and node.name in self.ctes else None

    def inner(self, name):
        select = self.ctes[name].this
        return _wrapper(select) or select

    # 1. Surrogate numbers.

    def surrogates(self):
        for name, cte in list(self.ctes.items()):
            outer = cte.this
            inner = _wrapper(outer) if isinstance(outer, exp.Select) else None
            select = inner or outer
            if not isinstance(select, exp.Select):
                continue
            for projection in select.expressions:
                value = projection.unalias()
                if isinstance(value, exp.Window) and isinstance(value.this, exp.RowNumber) and not value.args.get("partition_by") \
                        and self._only_tested_for_null(name, projection.alias_or_name):
                    # A number that the question only tests for being there is never empty, so 1 stands for it.
                    projection.set("this", exp.Literal.number(1))
                    continue
                if not (isinstance(value, exp.Window) and isinstance(value.this, exp.DenseRank)):
                    continue
                order = value.args.get("order")
                keys = order.expressions if order is not None else []
                if value.args.get("partition_by") or len(keys) != 1 or not isinstance(keys[0].this, exp.Column):
                    raise Unsafe(f"{name} numbers its rows by more than one column")
                readers = [t for t in self.references(name)]
                for table in readers:
                    union = table.find_ancestor(exp.CTE)
                    if union is not None and isinstance(union.this, exp.Union):
                        raise Unsafe(f"more than one step writes the table that {name} writes")
                projection.set("this", keys[0].this.copy())
                if inner is not None:
                    for out in outer.expressions:
                        if out.alias_or_name == projection.alias_or_name:
                            shifted = out.unalias()
                            if isinstance(shifted, exp.Add) and isinstance(shifted.expression, exp.Literal):
                                out.set("this", shifted.this.copy())
                            elif isinstance(shifted, exp.Add):
                                raise Unsafe(f"{name} is numbered after the rows of an earlier step")

    def _only_tested_for_null(self, step, column):
        """Whether a column of a step is read, beyond the step, its wrapper and the union that passes it on, only
        in a test of whether it is empty."""
        found = False
        for node in self.tree.find_all(exp.Column):
            if node.name.lower() != column.lower():
                continue
            cte = node.find_ancestor(exp.CTE)
            if cte is not None and (cte.alias == step or (cte.alias.startswith("omop_") and self.writer(cte.alias) == step)):
                continue
            if not isinstance(node.parent, exp.Is):
                return False
            found = True
        return found

    # 2. Keys cast to text.

    def casts(self, catalogue):
        for comparison in list(self.tree.find_all(exp.EQ)):
            for side, other in ((comparison.this, comparison.expression), (comparison.expression, comparison.this)):
                if not (isinstance(side, exp.Column) and isinstance(other, exp.Cast) and isinstance(other.this, exp.Column)):
                    continue
                select = comparison.find_ancestor(exp.Select)
                aliases = _aliases(select)
                node = aliases.get(side.table.upper())
                if not isinstance(node, exp.Table) or node.name not in self.ctes:
                    continue
                writer = self.writer(node.name) if node.name.startswith("omop_") else node.name
                if writer is None:
                    continue
                inner = self.inner(writer)
                written = next((p for p in inner.expressions if p.alias_or_name == side.name), None)
                value = written.unalias() if written is not None else None
                if not (isinstance(value, exp.Cast) and isinstance(value.this, exp.Column)
                        and _sql(value.args["to"]) == _sql(other.args["to"])):
                    continue
                inner_aliases = _aliases(inner)
                mine, theirs = inner_aliases.get(value.this.table.upper()), aliases.get(other.this.table.upper())
                if not (isinstance(mine, exp.Table) and isinstance(theirs, exp.Table)):
                    continue
                families = {_family(catalogue, mine.name, value.this.name), _family(catalogue, theirs.name, other.this.name)}
                if len(families) != 1 or None in families:
                    continue
                key = f"{side.name}__key"
                self._carry(node.name, writer, value.this.copy(), key)
                comparison.replace(exp.EQ(this=exp.column(key, table=side.table), expression=other.this.copy()))
                break

    def _carry(self, reader, writer, column, key):
        """Adds the raw key to the writer, its offset wrapper and the union that passes it on."""
        inner = self.inner(writer)
        if not any(p.alias_or_name == key for p in inner.expressions):
            inner.expressions.append(exp.alias_(column, key))
        outer = self.ctes[writer].this
        if _wrapper(outer) is not None and not any(p.alias_or_name == key for p in outer.expressions):
            outer.expressions.append(exp.alias_(exp.column(key, table="step"), key))
        if reader != writer:
            union = self.ctes[reader].this
            if not any(p.alias_or_name == key for p in union.expressions):
                union.expressions.append(exp.column(key))

    # 3. Columns, joins and expressions that nothing reads.

    def _columns(self, select):
        """The columns of a SELECT, leaving out those of the common table expressions that the outermost one holds."""
        holder = _with(self.tree)
        return [c for c in select.find_all(exp.Column) if select is not self.tree or not any(a is holder for a in _ancestors(c))]

    def _used(self, name):
        """The columns of a common table expression that its readers use, or None where a reader uses *."""
        used = set()
        for table in self.references(name):
            select = table.find_ancestor(exp.Select)
            alias = table.alias_or_name.upper()
            single = len(_aliases(select)) == 1
            for column in self._columns(select):
                if column.table.upper() == alias or (not column.table and single):
                    used.add(column.name.lower())
            if any(isinstance(p, exp.Star) for p in select.expressions):
                return None
            # An unqualified column in a SELECT that reads several tables may belong to this one.
            if not single:
                used |= {c.name.lower() for c in self._columns(select) if not c.table}
        return used

    def prune(self):
        changed = True
        while changed:
            changed = False
            for name in list(self.ctes):
                if not (name.startswith("step_") or name.startswith("omop_")):
                    continue
                used = self._used(name)
                if used is None:
                    continue
                select = self.ctes[name].this
                if not isinstance(select, exp.Select):
                    continue
                keep = [p for p in select.expressions if p.alias_or_name.lower() in used]
                if not keep:
                    keep = select.expressions[:1]
                if len(keep) < len(select.expressions):
                    select.set("expressions", keep)
                    changed = True
                inner = _wrapper(select)
                if inner is not None:
                    wanted = {c.name.lower() for p in select.expressions for c in p.find_all(exp.Column)}
                    kept = [p for p in inner.expressions if p.alias_or_name.lower() in wanted
                            or isinstance(p.unalias(), exp.Window) and p.alias_or_name.lower() in wanted]
                    if kept and len(kept) < len(inner.expressions):
                        inner.set("expressions", kept)
                        changed = True
            for name in list(self.ctes):
                if name not in ("mapping_rows",) and not self.references(name) and name != "result":
                    self.ctes[name].pop()
                    del self.ctes[name]
                    changed = True

    def joins(self, catalogue, mappings):
        """Drops each LEFT JOIN that nothing reads and that is shown to give at most one row."""
        changed = True
        while changed:
            changed = False
            for select in list(self.tree.find_all(exp.Select)):
                for join in list(select.args.get("joins") or []):
                    if (join.args.get("side") or "").upper() != "LEFT":
                        continue
                    alias = join.this.alias_or_name.upper()
                    on = join.args.get("on")
                    elsewhere = [c for c in self._columns(select) if c.table.upper() == alias
                                 and not (on is not None and c.find_ancestor(exp.Join) is join)]
                    if elsewhere:
                        continue
                    if self._single(join, alias, catalogue, mappings):
                        join.pop()
                        changed = True
                    elif not join.comments:
                        join.comments = [" kept, because Schemalyser cannot show that this join gives at most one row "]

    def _single(self, join, alias, catalogue, mappings):
        node, conjuncts = join.this, _conjuncts(join.args.get("on"))
        equal = {}
        for c in conjuncts:
            if isinstance(c, exp.EQ):
                for a, b in ((c.this, c.expression), (c.expression, c.this)):
                    if isinstance(a, exp.Column) and a.table.upper() == alias:
                        equal[a.name.lower()] = b
        # (a) A lookup of mapping rows whose codes are unique under a vocabulary named by a literal.
        if isinstance(node, exp.Table) and node.name == "mapping_rows":
            vocabulary = equal.get("source_vocabulary_id")
            if isinstance(vocabulary, exp.Literal) and "source_code" in equal:
                codes = [str(r.get("source_code")) for r in mappings if str(r.get("source_vocabulary_id")) == vocabulary.this]
                return len(codes) == len(set(codes))
            return False
        # (b) A derived table filtered to the first row of each key.
        if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select):
            for projection in node.this.expressions:
                value = projection.unalias()
                if isinstance(value, exp.Window) and isinstance(value.this, exp.RowNumber):
                    first = equal.get(projection.alias_or_name.lower())
                    partition = value.args.get("partition_by") or []
                    if isinstance(first, exp.Literal) and first.this == "1" and partition and all(
                            isinstance(p, exp.Column) and p.name.lower() in equal for p in partition):
                        return True
            return False
        # (c) A table whose key is the first column of one source table that may not be empty.
        if isinstance(node, exp.Table) and node.name in self.ctes:
            writer = self.writer(node.name) if node.name.startswith("omop_") else node.name
            if writer is None or len(equal) != 1:
                return False
            inner = self.inner(writer)
            if inner.args.get("joins") or inner.args.get("group") or not isinstance(inner.args.get("from_").this, exp.Table):
                return False
            field = next(iter(equal))
            written = next((p for p in inner.expressions if p.alias_or_name.lower() == field), None)
            value = written.unalias() if written is not None else None
            value = value.this if isinstance(value, exp.Cast) else value
            if not isinstance(value, exp.Column):
                return False
            table = catalogue.table(inner.args["from_"].this.name)
            first = table.first_column() if table is not None else None
            return first is not None and first.name.upper() == value.name.upper() and first.nullable is False
        return False

    def mapping_rows(self):
        """Leaves only the mapping rows that a lookup that remains can still find.

        A vocabulary that no lookup names is left out. Where every lookup of a vocabulary compares the code with
        a literal, or with an expression whose values a pushed condition restricts, only those codes are kept.
        """
        cte = self.ctes.get("mapping_rows")
        values = cte.this.find(exp.Values) if cte is not None else None
        if values is None:
            return
        allowed = {}        # vocabulary -> set of codes, or None for every code
        for table in self.references("mapping_rows"):
            join = table.find_ancestor(exp.Join)
            if join is None:
                return
            alias = table.alias_or_name.upper()
            equal = {}
            for c in _conjuncts(join.args.get("on")):
                if isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) and c.this.table.upper() == alias:
                    equal[c.this.name.lower()] = c.expression
            vocabulary, code = equal.get("source_vocabulary_id"), equal.get("source_code")
            if not isinstance(vocabulary, exp.Literal):
                return
            if isinstance(code, exp.Literal):
                codes = {code.this}
            else:
                select = join.find_ancestor(exp.Select)
                codes = self.restricted_codes.get((id(select), _sql(code))) if code is not None else None
            if vocabulary.this not in allowed or allowed[vocabulary.this] is not None:
                allowed[vocabulary.this] = None if codes is None or allowed.get(vocabulary.this, set()) is None \
                    else (allowed.get(vocabulary.this) or set()) | set(codes)
        kept = []
        for row in values.expressions:
            code, vocabulary = row.expressions[0], row.expressions[1]
            if not (isinstance(vocabulary, exp.Literal) and vocabulary.this in allowed):
                continue
            codes = allowed[vocabulary.this]
            if codes is None or (isinstance(code, exp.Literal) and code.this in codes):
                kept.append(row)
        if kept:
            values.set("expressions", kept)

    # 4. The question's conditions, pushed into the steps.

    def push(self, target_names, mappings):
        for select in [c.this for n, c in self.ctes.items() if n in target_names] + [self.tree]:
            if not isinstance(select, exp.Select):
                continue
            aliases = _aliases(select)
            inner_conditions = _conjuncts(select.args.get("where"))
            left = {}
            for join in select.args.get("joins") or []:
                if (join.args.get("side") or "") == "":
                    inner_conditions += _conjuncts(join.args.get("on"))
                elif (join.args.get("side") or "").upper() == "LEFT":
                    left[join.this.alias_or_name.upper()] = _conjuncts(join.args.get("on"))
            for alias, node in aliases.items():
                if not (isinstance(node, exp.Table) and node.name.startswith("omop_")) or len(self.references(node.name)) != 1:
                    continue
                writer = self.writer(node.name)
                if writer is None:
                    continue
                inner = self.inner(writer)
                # A condition of a LEFT JOIN's own ON on the joined table alone, or on it and the cohort, filters its
                # rows as a condition of the step would.
                conditions = inner_conditions + left.get(alias, [])
                for condition in conditions:
                    self._push_codes(condition, alias, inner, writer, mappings)
                    self._push_cohort(condition, alias, aliases, inner, writer, target_names)

    def _written(self, inner, field):
        written = next((p for p in inner.expressions if p.alias_or_name.lower() == field.lower()), None)
        return written.unalias() if written is not None else None

    def _push_codes(self, condition, alias, inner, writer, mappings):
        if isinstance(condition, exp.In) and isinstance(condition.this, exp.Column) and condition.this.table.upper() == alias:
            values = condition.expressions
        elif isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column) and condition.this.table.upper() == alias \
                and isinstance(condition.expression, exp.Literal):
            values = [condition.expression]
        else:
            return
        if not all(isinstance(v, exp.Literal) and not v.is_string for v in values):
            return
        concepts = {v.this for v in values}
        value = self._written(inner, condition.this.name)
        lookup = value.this if isinstance(value, exp.Coalesce) else value
        if isinstance(value, exp.Coalesce):
            others = value.expressions
            if len(others) != 1 or not isinstance(others[0], exp.Literal) or others[0].this in concepts:
                return
        if not (isinstance(lookup, exp.Column) and lookup.name.lower() == "target_concept_id"):
            return
        mapped = _aliases(inner).get(lookup.table.upper())
        join = mapped.find_ancestor(exp.Join) if mapped is not None else None
        if not (isinstance(mapped, exp.Table) and mapped.name == "mapping_rows" and join is not None):
            return
        equal = {}
        for c in _conjuncts(join.args.get("on")):
            if isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) and c.this.table.upper() == lookup.table.upper():
                equal[c.this.name.lower()] = c.expression
        vocabulary, code = equal.get("source_vocabulary_id"), equal.get("source_code")
        if not isinstance(vocabulary, exp.Literal) or code is None or len(equal) != 2:
            return
        codes = sorted({str(r.get("source_code")) for r in mappings if str(r.get("source_vocabulary_id")) == vocabulary.this
                        and str(r.get("target_concept_id")) in concepts})
        plain = code.copy()
        for node in plain.walk():
            node.comments = None
        literals = [exp.Literal.string(c) for c in codes]
        # Where the code is a key cast to text, the condition is on the key itself, so that its index can be used:
        # as text where the key is text, and as numbers where it is a whole number and every code is written as
        # that number would be cast to text.
        if isinstance(plain, exp.Cast) and isinstance(plain.this, exp.Column) and self.catalogue is not None:
            node = _aliases(inner).get(plain.this.table.upper())
            family = _family(self.catalogue, node.name, plain.this.name) if isinstance(node, exp.Table) else None
            if family == "text" and all(len(c) <= 50 for c in codes):
                plain = plain.this
            elif family == "whole" and all(re.fullmatch(r"0|[1-9]\d{0,17}", c) for c in codes):
                plain = plain.this
                literals = [exp.Literal.number(c) for c in codes]
        restriction = exp.In(this=plain, expressions=literals) if codes \
            else exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))
        inner.where(restriction, copy=False)
        self.restricted.add(writer)
        self.restricted_codes[(id(inner), _sql(code))] = codes
        self.restricted_codes[(id(inner), _sql(plain))] = codes

    def _push_cohort(self, condition, alias, aliases, inner, writer, target_names):
        if not isinstance(condition, exp.EQ):
            return
        for mine, theirs in ((condition.this, condition.expression), (condition.expression, condition.this)):
            if not (isinstance(mine, exp.Column) and isinstance(theirs, exp.Column) and mine.table.upper() == alias):
                continue
            cohort = aliases.get(theirs.table.upper())
            if not (isinstance(cohort, exp.Table) and cohort.name in target_names):
                continue
            value = self._written(inner, mine.name)
            if not isinstance(value, exp.Column):
                continue
            cohort_values = exp.select(exp.column(theirs.name)).from_(cohort.name)
            inner.where(exp.In(this=value.copy(), query=exp.Subquery(this=cohort_values)), copy=False)
            self.restricted.add(writer)
            return

    def order(self):
        """Puts the common table expressions in an order in which each follows what it reads, the earliest first."""
        names = list(self.ctes)
        reads = {n: {t.name for t in self.ctes[n].this.find_all(exp.Table) if t.name in self.ctes and t.name != n} for n in names}
        placed, ordered = set(), []
        while len(ordered) < len(names):
            ready = [n for n in names if n not in placed and reads[n] <= placed]
            if not ready:
                raise Unsafe("the common table expressions read each other in a circle")
            ordered.append(ready[0])
            placed.add(ready[0])
        _with(self.tree).set("expressions", [self.ctes[n] for n in ordered])
        return ordered

    def check(self):
        for window in self.tree.find_all(exp.Window):
            if isinstance(window.this, exp.DenseRank):
                raise Unsafe("a step still numbers the rows of a whole table")
            if isinstance(window.this, exp.RowNumber) and not window.args.get("partition_by"):
                cte = window.find_ancestor(exp.CTE)
                if cte is None or cte.alias not in self.restricted:
                    raise Unsafe(f"the step {cte.alias if cte is not None else 'of the question'} still numbers the rows "
                                 f"of a whole table, and the question's conditions cannot be pushed into it")


def restructure(draft, target_names, catalogue, mappings):
    """The composed draft, restructured to start from the cohort, as (SQL text, None), or (None, the reason).

    draft is target.source_draft's text without the blanking SELECT; target_names are the names of its common
    table expressions that come from the target query itself.
    """
    try:
        query = _Query(draft)
        query.catalogue = catalogue
        query.surrogates()
        query.casts(catalogue)
        query.prune()
        query.joins(catalogue, mappings)
        query.prune()
        query.push(target_names, mappings)
        query.mapping_rows()
        query.order()
        query.check()
    except Unsafe as reason:
        return None, str(reason)
    except (sqlglot.errors.SqlglotError, AttributeError, KeyError, TypeError, ValueError) as error:
        return None, f"the query could not be read ({type(error).__name__})"
    body = query.tree.copy()
    body.set("with_" if "with_" in body.arg_types else "with", None)
    ctes = _with(query.tree).expressions
    text = "WITH\n" + ",\n".join(f"{c.alias} AS (\n" + "\n".join("  " + line for line in c.this.sql(dialect="tsql", pretty=True).splitlines()) + "\n)"
                                 for c in ctes)
    text += "\n" + body.sql(dialect="tsql", pretty=True)
    text = re.sub(r"   WITH \(NOLOCK\)", " WITH (NOLOCK)", text)
    return text, None
