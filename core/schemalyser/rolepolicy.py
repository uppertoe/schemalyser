"""The static policy on a question written over the role views, before it is let into a hospital's project.

policy.py reads the final script over the hospital's own tables. This module reads the question one level up, as a
person or a coding agent wrote it over the parts of the record, and decides whether it may be imported at all. It reads
the text with sqlglot in the SQL Server dialect and checks it against the role contract, never against a hospital:

    parse       the text is one statement that the parser understands
    statements  the statement is one SELECT, with common table expressions and set operations, that fills no table
    dynamic     no EXEC, no dynamic SQL, no procedure, no OPENQUERY, OPENROWSET or OPENDATASOURCE, no variable, and no
                table read through a function
    names       no name of another database, of a linked server or of a schema: a role view is named alone
    tables      only the role views the contract admits for a question, the mapping views, and the question's own
                common table expressions; no temporary table or source table. The contract's admission decides: a
                contract part is admitted, a rehearsed part is admitted with a ceiling, which the report records, and a
                draft is refused unless the examiner asks for it with admit="draft", which the report records as well
    columns     every column read from a role view, of whatever status, is a column of that view in the contract, and
                every column read from one of the question's own steps is a column that the step returns
    functions   only the functions that policy.py allows in a script, with ROW_NUMBER, LEAD and LAG used with a window
    kinds       every literal compared with a column of a kind is a kind that the contract's vocabulary knows
    capabilities  every capability that the question names, in a leading comment -- capability: NAME, is one of the
                catalogue's
    dialect     the question keeps the portable dialect, at DIALECT_VERSION, so that the role-level shadow on DuckDB
                and SQL Server give one answer: a division has one side cast to float or written as a fraction, AVG
                takes a number or a cast, text is compared with = only where it is a kind or a key, text is joined
                with CONCAT and never +, a ROW_NUMBER window and a TOP carry an ORDER BY that ends in a key column so
                that the row chosen is the same on every engine, LEAD and LAG carry an ORDER BY, DATEDIFF counts
                days, minutes or seconds, and no key is compared with a literal; each refusal says what to write
                instead

Each rule is passed, or failed with the fragment that broke it. Anything the parser cannot understand is refused.

    python -m schemalyser.rolepolicy QUESTION.sql
"""
import json
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from . import contract, feasibility, policy, rolemap, translate

RULES = {
    "parse": "The question is one statement that the parser understands.",
    "statements": "The question is one SELECT, which may use common table expressions, and fills no table.",
    "dynamic": "The question runs no procedure and no dynamic SQL, uses no variable and reaches no other server.",
    "names": "The question names no other database, no linked server and no schema.",
    "tables": "The question reads only the role views of the contract, the mapping views and its own steps.",
    "columns": "Every column that the question reads is a column of the contract or of one of its own steps.",
    "functions": "The question uses only the functions of the allowlist.",
    "kinds": "Every kind that the question names is a kind of the contract's vocabularies.",
    "capabilities": "Every capability that the question names is one of the catalogue's.",
    "dialect": "The question keeps the portable dialect: a division with one side cast or a fraction, AVG of a number or a cast, "
               "text equality only on kinds and keys, CONCAT for text, a chosen row's order ending in a key, and DATEDIFF in days, minutes or seconds.",
}
# The version of the dialect rule, recorded in every report, so that a package examined under one version says so.
DIALECT_VERSION = 1
DATEDIFF_UNITS = ("day", "minute", "second")
FLOAT_TYPES = {"FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL"}
# Statements that change something, wherever they appear inside the one statement.
CHANGES = tuple(getattr(exp, name) for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
                                                "Command", "TruncateTable") if hasattr(exp, name))


def contract_views():
    """{view: {column, ...}} for every view of the role model, whatever its status, so that a column is checked on a
    draft as on a contract part, and {view: status} for every view."""
    model = rolemap.contract()
    views = {v["name"]: {c["name"] for c in v["columns"]} for v in model["views"]}
    # A mapping view translates a hospital's codes into standard concepts and names none of them, so any question may
    # read it, as it reads the parts of the contract.
    views.update({name: {c["name"] for c in v["columns"]} for name, v in rolemap.mapping_views(model).items()})
    status = {v["name"]: v.get("status") for v in model["views"]}
    return views, status


def column_types():
    """{column name: {type, ...}} over every part and mapping view of the contract, by which a column of a question is
    read where its source is a view, and the names of the key columns and of the whole-number columns."""
    model = rolemap.contract()
    types = {}
    for view in model["views"] + model.get("mapping_views", []):
        for column in view["columns"]:
            types.setdefault(column["name"].lower(), set()).add(column["type"])
    keys = {name for name, held in types.items() if "key" in held}
    whole = {name.upper() for name, held in types.items() if held <= {"whole", "flag", "flag_or_empty"}}
    return types, keys, whole


class _Checker:
    def __init__(self, admit=None):
        self.views, self.status = contract_views()
        self.types, self.keys, self.whole = column_types()
        self.view_types = rolemap.contract_column_types()
        self.failures = {rule: [] for rule in RULES}
        # What the examiner asks for: "draft" admits the draft parts, which the report records.
        self.admit = admit
        self.admission = {"ceiling": set(), "drafts": set()}

    def fail(self, rule, fragment):
        fragment = policy._fragment(fragment)
        if fragment not in self.failures[rule]:
            self.failures[rule].append(fragment)

    def tokens(self, sql):
        try:
            statements, tokens = policy.split(sql)
        except sqlglot.errors.SqlglotError:
            self.fail("parse", "the question cannot be read into tokens")
            return []
        for token in tokens:
            if token.token_type.name in ("STRING", "NATIONAL_STRING"):
                continue
            word = token.text.upper().strip("[]")
            if word in policy.FORBIDDEN_WORDS or word.startswith(("SP_", "XP_")):
                self.fail("dynamic", word)
            if token.text.startswith("@"):
                self.fail("dynamic", token.text)
        return statements

    def tree(self, sql):
        statements = self.tokens(sql)
        if not statements:
            self.fail("parse", "the question holds no statement")
            return None
        if len(statements) > 1:
            self.fail("statements", f"the question holds {len(statements)} statements")
            return None
        try:
            tree = sqlglot.parse_one(statements[0], dialect="tsql", error_level=sqlglot.ErrorLevel.RAISE)
        except sqlglot.errors.SqlglotError:
            self.fail("parse", statements[0])
            return None
        if tree is None or isinstance(tree, exp.Command) or tree.find(exp.Command) is not None:
            self.fail("parse", statements[0])
            return None
        if not isinstance(tree, (exp.Select, exp.SetOperation)) or tree.find(*CHANGES) is not None:
            self.fail("statements", tree)
            return None
        for node in tree.find_all(exp.Into):
            self.fail("statements", node)
        return tree

    def tables(self, tree, ctes):
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                self.fail("dynamic", table)
                continue
            if table.catalog or table.db:
                self.fail("names", table)
                continue
            name = table.name.lower()
            if name in ctes:
                continue
            if table.this.args.get("temporary") or table.name.startswith("#"):
                self.fail("tables", f"#{table.name} is a temporary table")
            elif name not in self.views:
                self.fail("tables", f"{table.name} is not a role view of the contract")
            elif name in self.status:
                admission = contract.admission(name, "question")
                if admission == contract.CEILING:
                    self.admission["ceiling"].add(name)
                elif admission == contract.REFUSED and self.admit == "draft":
                    self.admission["drafts"].add(name)
                elif admission == contract.REFUSED:
                    self.fail("tables", f"{name} is a draft, which no question reads until it is rehearsed or joins the contract")

    def functions(self, tree):
        for func in tree.find_all(exp.Func):
            name = type(func).__name__
            if isinstance(func, (exp.Connector, exp.Binary, exp.Predicate)) or (isinstance(func, exp.If) and isinstance(func.parent, exp.Case)):
                continue
            if isinstance(func, exp.Anonymous) or name not in policy.FUNCTIONS:
                self.fail("functions", func)
            elif name in policy.WINDOWED and not policy._windowed(func):
                self.fail("functions", func)

    def columns(self, tree, ctes):
        try:
            scopes = list(traverse_scope(tree))
        except Exception:  # noqa: BLE001 - a question whose scopes cannot be built is not understood
            self.fail("parse", tree)
            return
        for scope in scopes:
            select = scope.expression if isinstance(scope.expression, exp.Select) else None
            aliases = {p.alias.lower() for p in (select.expressions if select is not None else []) if p.alias}
            sources = {k.lower(): v for k, v in scope.sources.items()}
            for column in scope.columns:
                name = column.name.lower()
                if isinstance(column.this, exp.Star):
                    continue
                if column.table:
                    source = sources.get(column.table.lower())
                    if source is None:
                        if not self._outer(scope, column.table.lower()):
                            self.fail("columns", f"{column.sql(dialect='tsql')} names no source of its SELECT")
                        continue
                    if not self._holds(source, name, ctes):
                        self.fail("columns", f"{column.sql(dialect='tsql')} is not a column of {self._title(source)}")
                    continue
                if name in aliases or not sources:
                    continue
                if not any(self._holds(source, name, ctes) for source in sources.values()):
                    self.fail("columns", f"{column.sql(dialect='tsql')} is not a column of any source of its SELECT")

    def _outer(self, scope, alias):
        parent = scope.parent
        while parent is not None:
            if alias in {k.lower() for k in parent.sources}:
                return True
            parent = parent.parent
        return False

    def _title(self, source):
        return source.name if isinstance(source, exp.Table) else "the step it reads"

    def _holds(self, source, name, ctes):
        if isinstance(source, exp.Table):
            view = source.name.lower()
            if view in self.views:
                return name in self.views[view]
            # A table that is not a role view has already failed the rule on tables.
            return True
        outputs = set()
        expression = source.expression
        selects = [expression] if isinstance(expression, exp.Select) else list(expression.find_all(exp.Select))
        for select in selects[:1] if isinstance(expression, exp.Select) else selects:
            for projection in select.expressions:
                if isinstance(projection, exp.Star) or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)):
                    return True
                outputs.add(projection.alias_or_name.lower())
        return name in outputs

    # The dialect rule.

    def dialect(self, tree):
        aliases = {}
        for table in tree.find_all(exp.Table):
            if table.name.lower() in self.views and table.alias:
                aliases.setdefault(table.alias.lower(), set()).add(table.name.lower())
        for node in tree.find_all(exp.Div):
            if translate._whole(node.this, tree, self.whole) and translate._whole(node.expression, tree, self.whole):
                self.fail("dialect", f"{node.sql(dialect='tsql')} divides two whole numbers, which SQL Server rounds down: "
                                     f"write CAST({node.this.sql(dialect='tsql')} AS float) / {node.expression.sql(dialect='tsql')} for a fraction")
        for node in tree.find_all(exp.Avg):
            if translate._whole(node.this, tree, self.whole):
                self.fail("dialect", f"{node.sql(dialect='tsql')} averages a whole number, which SQL Server truncates: "
                                     f"write AVG(CAST({node.this.sql(dialect='tsql')} AS float))")
        for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike):
            sides = [node.this] + ([node.expression] if not isinstance(node, exp.In) else list(node.expressions))
            if any(self._column_type(side, aliases) == "key" for side in sides) and \
                    any(isinstance(side, exp.Literal) for side in sides):
                self.fail("dialect", f"{node.sql(dialect='tsql')} compares a key with a literal, which no clinical question needs: "
                                     f"choose rows by what the record says of them, never by a key")
            if not isinstance(node, (exp.EQ, exp.NEQ)):
                continue
            for side in (node.this, node.expression):
                if self._column_type(side, aliases) == "text":
                    self.fail("dialect", f"{node.sql(dialect='tsql')} compares free text with =, which the engines read differently: "
                                         f"read free text with LIKE, and compare only a kind with its word or a key with a key")
                    break
        for node in tree.find_all(exp.Add):
            if isinstance(node.parent, exp.Add):
                continue
            if any((isinstance(side, exp.Literal) and side.is_string) or self._column_type(side, aliases) in ("text", "kind")
                   for side in (node.this, node.expression)):
                self.fail("dialect", f"{node.sql(dialect='tsql')} joins text with +, which DuckDB refuses: "
                                     f"write CONCAT({node.this.sql(dialect='tsql')}, {node.expression.sql(dialect='tsql')})")
        for node in tree.find_all(exp.Window):
            function = node.this
            order = node.args.get("order")
            if isinstance(function, exp.RowNumber):
                if not self._ends_in_key(order, aliases):
                    self.fail("dialect", f"{node.sql(dialect='tsql')} chooses a row: end its ORDER BY in a key column of the row's part, "
                                         f"such as reading_key, so that every engine chooses the same row")
            elif isinstance(function, (exp.Lead, exp.Lag)) and order is None:
                self.fail("dialect", f"{node.sql(dialect='tsql')} reads a neighbouring row, so it carries an ORDER BY")
        for select in tree.find_all(exp.Select):
            if select.args.get("limit") is not None and not self._ends_in_key(select.args.get("order"), aliases):
                self.fail("dialect", f"TOP chooses rows, so the ORDER BY of {select.sql(dialect='tsql')[:60]} ends in a key column of the row's part")
        for node in tree.find_all(exp.DateDiff):
            unit = node.args.get("unit")
            name = (unit.name if isinstance(unit, exp.Expression) else str(unit or "")).lower()
            if name not in DATEDIFF_UNITS:
                self.fail("dialect", f"{node.sql(dialect='tsql')} counts {name or 'an unnamed unit'}: write DATEDIFF with day, minute or second")

    def _column_type(self, node, aliases):
        """The contract's type of a column of a question, where it can be read: the one type the column has in the
        views its alias names, or in every view where it has no alias; None where it cannot be read."""
        if not isinstance(node, exp.Column):
            return None
        name = node.name.lower()
        if node.table:
            views = aliases.get(node.table.lower())
            if not views:
                return None
            held = {self.view_types.get(view, {}).get(name) for view in views} - {None}
        else:
            held = self.types.get(name) or set()
        return next(iter(held)) if len(held) == 1 else None

    def _ends_in_key(self, order, aliases):
        if order is None or not order.expressions:
            return False
        last = order.expressions[-1]
        last = last.this if isinstance(last, exp.Ordered) else last
        return isinstance(last, exp.Column) and last.name.lower() in self.keys

    def kinds(self, sql):
        try:
            needs = feasibility.requirements(sql)
        except Exception:  # noqa: BLE001 - a question that cannot be traced is not understood
            self.fail("parse", "the question cannot be traced to the role views")
            return
        for gap in needs["gaps"]:
            if gap["form"] == "kind":
                self.fail("kinds", f"'{gap['kind']}' is not a kind of {gap['view']}.{gap['column']}")


def check(sql, capabilities=(), admit=None):
    """The role-level policy on the text of a question, with the capabilities a package names beside those its leading
    comment names. With admit="draft", as the examiner asks, a draft part is admitted and recorded rather than
    refused. Returns {"rules": [{"id", "rule", "passed", "fragments"}], "outcome": "passed" or "failed", "failed":
    [rule ids], "dialect_version", "admission": {"ceiling": [the rehearsed parts read], "drafts": [the drafts
    admitted]}}."""
    checker = _Checker(admit)
    try:
        tree = checker.tree(sql)
        if tree is not None:
            ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
            checker.tables(tree, ctes)
            checker.functions(tree)
            checker.columns(tree, ctes)
            checker.dialect(tree)
            if not any(checker.failures[r] for r in ("parse", "statements", "dynamic")):
                checker.kinds(sql)
    except (RecursionError, sqlglot.errors.SqlglotError):
        # A question built to exhaust the parser, such as one nested thousands of brackets deep, is refused as one the
        # policy cannot read, and the error itself goes nowhere.
        checker.fail("parse", "the policy cannot read the question, which may be nested more deeply than it reads")
    for name in dict.fromkeys(list(feasibility.named_capabilities(sql)) + list(capabilities)):
        if name not in rolemap.capabilities():
            checker.fail("capabilities", f"{name} is not a capability of the catalogue")
    rules = [{"id": rule, "rule": RULES[rule], "passed": not checker.failures[rule], "fragments": checker.failures[rule]}
             for rule in RULES]
    failed = [r["id"] for r in rules if not r["passed"]]
    return {"rules": rules, "outcome": "failed" if failed else "passed", "failed": failed, "dialect_version": DIALECT_VERSION,
            "admission": {key: sorted(held) for key, held in checker.admission.items()}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Give one question: python -m schemalyser.rolepolicy QUESTION.sql", file=sys.stderr)
        return 2
    found = check(Path(argv[0]).read_text(encoding="utf-8"))
    print(json.dumps(found, indent=2))
    return 0 if found["outcome"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
