"""The static policy on a question written over the role views, before it is let into a hospital's project.

policy.py reads the final script over the hospital's own tables. This module reads the question one level up, as a
person or a coding agent wrote it over the parts of the record, and decides whether it may be imported at all. It reads
the text with sqlglot in the SQL Server dialect and checks it against the role contract, never against a hospital:

    parse       the text is one statement that the parser understands
    statements  the statement is one SELECT, with common table expressions and set operations, that fills no table
    dynamic     no EXEC, no dynamic SQL, no procedure, no OPENQUERY, OPENROWSET or OPENDATASOURCE, no variable, and no
                table read through a function
    names       no name of another database, of a linked server or of a schema: a role view is named alone
    tables      only the role views of the contract, the mapping views, and the question's own common table
                expressions; no draft view, temporary table or source table
    columns     every column read from a role view is a column of that view in the contract, and every column read from
                one of the question's own steps is a column that the step returns
    functions   only the functions that policy.py allows in a script, with ROW_NUMBER, LEAD and LAG used with a window
    kinds       every literal compared with a column of a kind is a kind that the contract's vocabulary knows
    capabilities  every capability that the question names, in a leading comment -- capability: NAME, is one of the
                catalogue's

Each rule is passed, or failed with the fragment that broke it. Anything the parser cannot understand is refused.

    python -m schemalyser.rolepolicy QUESTION.sql
"""
import json
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from . import feasibility, policy, rolemap

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
}
# Statements that change something, wherever they appear inside the one statement.
CHANGES = tuple(getattr(exp, name) for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
                                                "Command", "TruncateTable") if hasattr(exp, name))


def contract_views():
    """{view: {column, ...}} for the views of the contract, and {view: status} for every view of the role model."""
    model = rolemap.contract()
    views = {v["name"]: {c["name"] for c in v["columns"]} for v in model["views"] if v.get("status") == "contract"}
    # A mapping view translates a hospital's codes into standard concepts and names none of them, so any question may
    # read it, as it reads the parts of the contract.
    views.update({name: {c["name"] for c in v["columns"]} for name, v in rolemap.mapping_views(model).items()})
    status = {v["name"]: v.get("status") for v in model["views"]}
    return views, status


class _Checker:
    def __init__(self):
        self.views, self.status = contract_views()
        self.failures = {rule: [] for rule in RULES}

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
            elif self.status.get(name) == "draft":
                self.fail("tables", f"{name} is a draft view, which no audit may read until it joins the contract")
            elif name not in self.views:
                self.fail("tables", f"{table.name} is not a role view of the contract")

    def functions(self, tree):
        for func in tree.find_all(exp.Func):
            name = type(func).__name__
            if isinstance(func, (exp.Connector, exp.Binary, exp.Predicate)) or (isinstance(func, exp.If) and isinstance(func.parent, exp.Case)):
                continue
            if isinstance(func, exp.Anonymous) or name not in policy.FUNCTIONS:
                self.fail("functions", func)
            elif name in policy.WINDOWED and not isinstance(func.parent, exp.Window):
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

    def kinds(self, sql):
        try:
            needs = feasibility.requirements(sql)
        except Exception:  # noqa: BLE001 - a question that cannot be traced is not understood
            self.fail("parse", "the question cannot be traced to the role views")
            return
        for gap in needs["gaps"]:
            if gap["form"] == "kind":
                self.fail("kinds", f"'{gap['kind']}' is not a kind of {gap['view']}.{gap['column']}")


def check(sql):
    """The role-level policy on the text of a question. Returns {"rules": [{"id", "rule", "passed", "fragments"}],
    "outcome": "passed" or "failed", "failed": [rule ids]}."""
    checker = _Checker()
    tree = checker.tree(sql)
    if tree is not None:
        ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
        checker.tables(tree, ctes)
        checker.functions(tree)
        checker.columns(tree, ctes)
        if not any(checker.failures[r] for r in ("parse", "statements", "dynamic")):
            checker.kinds(sql)
    for name in feasibility.named_capabilities(sql):
        if name not in rolemap.capabilities():
            checker.fail("capabilities", f"{name} is not a capability of the catalogue")
    rules = [{"id": rule, "rule": RULES[rule], "passed": not checker.failures[rule], "fragments": checker.failures[rule]}
             for rule in RULES]
    failed = [r["id"] for r in rules if not r["passed"]]
    return {"rules": rules, "outcome": "failed" if failed else "passed", "failed": failed}


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
