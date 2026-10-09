"""The static policy on the final text of an audit's script, before anyone runs it on the hospital's database.

The policy reads the script exactly as it will be run, statement by statement, with sqlglot in the SQL Server dialect,
and checks it against an allowlist. It never relies on how the script was made: a script that Schemalyser compiled
and one that a person wrote by hand are read in the same way, and anything the parser cannot understand is rejected.

The rules, each passed or failed with the fragment that broke it:

    parse              every statement is one that the parser understands
    statements         only SELECT, the session settings, and the cohort's temporary table: SELECT ... INTO #name,
                       CREATE TABLE #name, INSERT INTO #name SELECT, ALTER TABLE #name ADD PRIMARY KEY, DROP TABLE #name
    dynamic            no EXEC, no dynamic SQL, no procedure, no OPENQUERY, OPENROWSET or OPENDATASOURCE, no WAITFOR
    names              no name of another database or of a linked server, and no schema other than dbo
    tables             only the tables that the hospital schema names, temporary tables, the script's own common table
                       expressions, and the server's own records of its tables (INFORMATION_SCHEMA and sys)
    functions          only aggregates, date arithmetic, CAST, COALESCE (and ISNULL), NULLIF, ROUND, CASE, CONCAT, and
                       ROW_NUMBER, LEAD and LAG with a window
    joins              no cross join, comma join or APPLY, and every join carries an equality between two named columns,
                       one of them of the table joined
    cohort_keys        every join out of a temporary table, the cohort, is on equality of bare columns
    large_from_cohort  every large table is reached from the cohort: it is joined, never read first, in a SELECT that
                       starts from the cohort, by equality with a column already joined
    cohort_cap         every SELECT or INSERT that fills a temporary table carries TOP (n) with n at most the cap
    cohort_period      every statement that fills a temporary table tests a column against a first and a last date
    counts_only        no result returns a value of a large table that is not aggregated

The execution class is derived from the rules, never from a flag:

    A  metadata only: the script reads only the server's own records of its tables;
    B  bounded validation: small tables, or large tables reached from a bounded cohort, and counts only;
    C  large clinical extraction: a result returns rows of a large table, or a cohort is above the cap or unbounded by
       a period;
    D  not permitted: any other rule fails.

A table is large when the tables and columns query gave it at least LARGE rows, or gave no figure for it at all.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

LARGE = 10_000_000
CAP = 5000

# The session settings that a script may carry, as their text with single spaces and in capitals.
SESSION = ("SET NOCOUNT ON", "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED", "SET LOCK_TIMEOUT 10000",
           "SET DEADLOCK_PRIORITY LOW", "SET ANSI_WARNINGS OFF", "SET XACT_ABORT ON")
DROP_GUARD = re.compile(r"^IF\s+OBJECT_ID\s*\(\s*'tempdb\.\.(#\w+)'\s*\)\s+IS\s+NOT\s+NULL\s+DROP\s+TABLE\s+(#\w+)$", re.I)
METADATA = ("information_schema", "sys")

# The functions of the allowlist, by sqlglot's class. The parser writes some allowed forms with classes of its own: a
# DATEDIFF or DATEADD reads its arguments through TimeStrToTime and TsOrDsToDate, and ISNULL is read as COALESCE.
FUNCTIONS = {"Count", "Sum", "Min", "Max", "Avg", "Cast", "TryCast", "Coalesce", "Nullif", "Round", "Floor", "Ceil", "Abs",
             "Case", "Concat", "DateDiff", "DateAdd", "Year", "Month", "Day", "TimeStrToTime", "TsOrDsToDate",
             "TsOrDsAdd", "TsOrDsDiff", "RowNumber", "Lead", "Lag"}
WINDOWED = {"RowNumber", "Lead", "Lag"}
# Words that never belong in a script, wherever they stand outside a string or a comment.
FORBIDDEN_WORDS = {"EXEC", "EXECUTE", "SP_EXECUTESQL", "OPENQUERY", "OPENROWSET", "OPENDATASOURCE", "OPENXML", "WAITFOR",
                   "SHUTDOWN", "RECONFIGURE", "GRANT", "REVOKE", "DENY", "BACKUP", "RESTORE", "DBCC", "KILL"}

RULES = {
    "parse": "Every statement is one that the parser understands.",
    "statements": "The script holds only SELECT statements, the session settings and the cohort's temporary table.",
    "dynamic": "The script runs no procedure and no dynamic SQL, and reaches no other server.",
    "names": "The script names no other database, no linked server and no schema other than dbo.",
    "tables": "The script reads only the tables that the hospital schema names, temporary tables and its own steps.",
    "functions": "The script uses only the functions of the allowlist.",
    "joins": "Every join carries an equality between two named columns, and none is a cross join.",
    "cohort_keys": "Every join out of the cohort is on equality of its key.",
    "large_from_cohort": "Every large table is reached from the cohort by key.",
    "cohort_cap": "The cohort carries TOP (n) with n at most {cap}.",
    "cohort_period": "The cohort is limited to a period by a first and a last date.",
    "counts_only": "No result returns a value of a large table that is not aggregated.",
}
# The rules whose failure makes a script a large clinical extraction rather than one that is not permitted.
EXTRACTION = ("cohort_cap", "cohort_period", "counts_only")
CLASSES = {
    "A": "Class A: metadata only. The script reads only the server's own records of its tables.",
    "B": "Class B: bounded validation. The script reads small tables, or large tables reached from a bounded cohort, and returns counts only.",
    "C": "Class C: large clinical extraction. The script returns rows of a large table, or its cohort is above the cap or not limited to a period.",
    "D": "Class D: not permitted. The script breaks a rule that no approval can set aside, and is not to be run.",
}


def _fragment(node_or_text, limit=200):
    text = node_or_text if isinstance(node_or_text, str) else node_or_text.sql(dialect="tsql")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def split(sql):
    """The statements of a script as [text], split at each semicolon outside a string, a bracket or a comment. Raises
    sqlglot's error where the text cannot be read into tokens."""
    tokens = sqlglot.tokenize(sql, dialect="tsql")
    out, first, last = [], None, None
    for token in tokens:
        if token.token_type.name == "SEMICOLON":
            if first is not None:
                out.append(sql[first:last + 1])
            first = last = None
            continue
        if first is None:
            first = token.start
        last = token.end
    if first is not None:
        out.append(sql[first:last + 1])
    return out, tokens


def _bare(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _conjuncts(node):
    node = _bare(node)
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node] if node is not None else []


def _is_temp(table):
    return isinstance(table, exp.Table) and isinstance(table.this, exp.Identifier) and bool(table.this.args.get("temporary"))


def _temp_name(table):
    return "#" + table.name.lower()


def _metadata(table):
    return (table.db or "").lower() in METADATA


class _Checker:
    def __init__(self, tables, sizes, large, cap, schemas):
        self.tables = {t.upper() for t in tables}
        self.sizes = {k.upper(): v for k, v in (sizes or {}).items()}
        self.large = large
        self.cap = cap
        self.schemas = {s.lower() for s in schemas}
        self.failures = {rule: [] for rule in RULES}
        self.clinical, self.metadata_read, self.large_read = set(), set(), set()
        self.temps_filled = []

    def fail(self, rule, fragment):
        if fragment not in self.failures[rule]:
            self.failures[rule].append(fragment)

    def is_large(self, name):
        rows = self.sizes.get(name.upper())
        return rows is None or rows >= self.large

    # Each statement.

    def statement(self, text):
        flat = " ".join(text.split())
        if flat.upper() in SESSION:
            return
        guard = DROP_GUARD.match(flat)
        if guard:
            if guard.group(1).lower() != guard.group(2).lower():
                self.fail("statements", _fragment(flat))
            return
        try:
            tree = sqlglot.parse_one(text, dialect="tsql", error_level=sqlglot.ErrorLevel.RAISE)
        except sqlglot.errors.SqlglotError:
            self.fail("parse", _fragment(flat))
            return
        if tree is None or isinstance(tree, exp.Command) or tree.find(exp.Command) is not None:
            self.fail("parse", _fragment(flat))
            return
        if isinstance(tree, (exp.ExecuteSql,)) or type(tree).__name__ in ("Execute", "ExecuteSql"):
            self.fail("dynamic", _fragment(flat))
            return
        if isinstance(tree, exp.Set):
            self.fail("statements", _fragment(flat))
            return
        if isinstance(tree, exp.Query):
            into = tree.args.get("into")
            if into is not None:
                target = into.this
                if not _is_temp(target):
                    self.fail("statements", _fragment(flat))
                    return
                self.filled(tree, tree, target)
            self.query(tree, returns=into is None)
            return
        if isinstance(tree, exp.Insert):
            target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
            if not _is_temp(target) or not isinstance(tree.expression, exp.Query):
                self.fail("statements", _fragment(flat))
                return
            self.filled(tree, tree.expression, target)
            self.query(tree.expression, returns=False)
            return
        if isinstance(tree, exp.Create):
            target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
            if (tree.args.get("kind") or "").upper() != "TABLE" or not _is_temp(target) or tree.args.get("expression") is not None:
                self.fail("statements", _fragment(flat))
            return
        if isinstance(tree, exp.Alter):
            target = tree.this
            if not _is_temp(target) or not re.match(r"^ALTER TABLE #\w+ ADD PRIMARY KEY \(", flat, re.I):
                self.fail("statements", _fragment(flat))
            return
        if isinstance(tree, exp.Drop):
            targets = tree.args.get("tables") or ([tree.this] if tree.this is not None else [])
            if (tree.args.get("kind") or "").upper() != "TABLE" or not targets or not all(_is_temp(t) for t in targets):
                self.fail("statements", _fragment(flat))
            return
        self.fail("statements", _fragment(flat))

    # A statement that fills a temporary table: the cohort.

    def filled(self, statement, query, target):
        self.temps_filled.append(_temp_name(target))
        select = query if isinstance(query, exp.Select) else query.find(exp.Select)
        limit = select.args.get("limit") if select is not None else None
        n = None
        if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal) and not limit.expression.is_string:
            n = int(limit.expression.this) if limit.expression.this.isdigit() else None
        if limit is not None and (limit.args.get("percent") or "PERCENT" in limit.sql(dialect="tsql").upper()):
            n = None
        if n is None or n > self.cap:
            shown = f"TOP ({n})" if n is not None else "no TOP (n)"
            self.fail("cohort_cap", _fragment(f"{shown} in the statement that fills {_temp_name(target)}"))
        lower = upper = False
        for node in statement.find_all(exp.GTE, exp.GT, exp.LT, exp.LTE, exp.Between):
            if isinstance(node, exp.Between):
                if _dated(node.args.get("low")) and _dated(node.args.get("high")) and isinstance(_bare(node.this), exp.Column):
                    lower = upper = True
                continue
            one, other = _bare(node.this), _bare(node.expression)
            if isinstance(one, exp.Column) and _dated(other):
                op = type(node)
            elif isinstance(other, exp.Column) and _dated(one):
                op = {exp.GTE: exp.LTE, exp.GT: exp.LT, exp.LT: exp.GT, exp.LTE: exp.GTE}[type(node)]
            else:
                continue
            if op in (exp.GTE, exp.GT):
                lower = True
            else:
                upper = True
        if not (lower and upper):
            self.fail("cohort_period", _fragment(f"no first and last date in the statement that fills {_temp_name(target)}"))

    # A query, with everything that it reads.

    def query(self, tree, returns):
        ctes = {cte.alias.lower(): cte.this for cte in tree.find_all(exp.CTE)}
        for word in ("Lateral",):
            for node in tree.find_all(getattr(exp, word)):
                self.fail("joins", _fragment(node))
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                self.fail("dynamic", _fragment(table))
                continue
            if table.catalog or (table.args.get("db") is not None and isinstance(table.args.get("db"), exp.Dot)):
                self.fail("names", _fragment(table))
                continue
            if _is_temp(table):
                continue
            if not table.db and table.name.lower() in ctes:
                continue
            if _metadata(table):
                self.metadata_read.add(f"{table.db}.{table.name}")
                continue
            if table.db and table.db.lower() not in self.schemas:
                self.fail("names", _fragment(table))
            if table.name.upper() not in self.tables:
                self.fail("tables", _fragment(table.sql(dialect="tsql").split(" AS ")[0]))
                continue
            self.clinical.add(table.name.upper())
            if self.is_large(table.name):
                self.large_read.add(table.name.upper())
        for func in tree.find_all(exp.Func):
            name = type(func).__name__
            # sqlglot counts the operators AND and OR, and each branch of a CASE, among its functions.
            if isinstance(func, (exp.Connector, exp.Binary, exp.Predicate)) or (isinstance(func, exp.If) and isinstance(func.parent, exp.Case)):
                continue
            if isinstance(func, exp.Anonymous) or name not in FUNCTIONS:
                self.fail("functions", _fragment(func))
            elif name in WINDOWED and not isinstance(func.parent, exp.Window):
                self.fail("functions", _fragment(func))
        for select in tree.find_all(exp.Select):
            self.select(select, ctes)
        if returns:
            self.counts(tree)

    def select(self, select, ctes):
        source = select.args.get("from_") or select.args.get("from")
        if source is None:
            return
        chain = [source.this] + [j.this for j in select.args.get("joins") or []]
        head_anchored = self.anchored(chain[0], ctes, set())
        seen = [chain[0].alias_or_name.lower()]
        anchored_aliases = {chain[0].alias_or_name.lower()} if head_anchored else set()
        if isinstance(chain[0], exp.Table) and not _is_temp(chain[0]) and chain[0].name.lower() not in ctes \
                and not _metadata(chain[0]) and self.is_large(chain[0].name):
            self.fail("large_from_cohort", _fragment(f"{chain[0].name} is read first, not from the cohort"))
        for join in select.args.get("joins") or []:
            joined = join.this
            alias = joined.alias_or_name.lower()
            kind = (join.args.get("kind") or "").upper()
            on = join.args.get("on")
            if isinstance(joined, exp.Lateral):
                seen.append(alias)
                continue
            if kind == "CROSS" or join.args.get("using") or on is None:
                self.fail("joins", _fragment(join))
                seen.append(alias)
                continue
            keys = []
            for c in _conjuncts(on):
                if not isinstance(c, exp.EQ):
                    continue
                one, other = _bare(c.this), _bare(c.expression)
                if not (isinstance(one, exp.Column) and isinstance(other, exp.Column)):
                    continue
                a, b = one.table.lower(), other.table.lower()
                if alias in (a, b) and a != b and (a in seen or b in seen):
                    keys.append(b if a == alias else a)
            if not keys:
                self.fail("joins", _fragment(join))
            # In a SELECT that starts from the cohort, every join must carry an equality of bare columns with a source
            # already reached from the cohort, so that nothing is joined beside it.
            if head_anchored and not any(k in anchored_aliases for k in keys):
                self.fail("cohort_keys", _fragment(join))
            if isinstance(joined, exp.Table) and not _is_temp(joined) and joined.name.lower() not in ctes \
                    and not _metadata(joined) and self.is_large(joined.name):
                if not head_anchored or not keys or not any(k in anchored_aliases for k in keys):
                    self.fail("large_from_cohort", _fragment(join))
            if head_anchored and keys and any(k in anchored_aliases for k in keys):
                anchored_aliases.add(alias)
            seen.append(alias)

    def anchored(self, node, ctes, guard):
        """Whether a source of a SELECT starts from the cohort: a temporary table, or a step whose own first source does."""
        if isinstance(node, exp.Table):
            if _is_temp(node):
                return True
            body = ctes.get(node.name.lower()) if not node.db else None
            if body is None or id(body) in guard:
                return False
            return self.anchored(body, ctes, guard | {id(body)})
        if isinstance(node, exp.Subquery):
            return self.anchored(node.this, ctes, guard)
        if isinstance(node, exp.SetOperation):
            return self.anchored(node.this, ctes, guard) and self.anchored(node.expression, ctes, guard)
        if isinstance(node, exp.Select):
            source = node.args.get("from_") or node.args.get("from")
            return source is not None and self.anchored(source.this, ctes, guard)
        return False

    # Whether a result returns a value of a large table that is not aggregated.

    def counts(self, tree):
        try:
            root = build_scope(tree)
        except Exception:  # noqa: BLE001 - a query whose scopes cannot be built is not understood
            self.fail("parse", _fragment(tree))
            return
        if root is None:
            self.fail("parse", _fragment(tree))
            return
        for name, raw in self._outputs(root, set()).items():
            if raw:
                self.fail("counts_only", _fragment(f"{name} returns {', '.join(sorted(raw))} without aggregating it"))

    def _outputs(self, scope, guard):
        """{output column: the large tables whose values reach it unaggregated}."""
        if scope.is_set_operation:
            found = {}
            for part in scope.set_operation_scopes or []:
                for name, raw in self._outputs(part, guard).items():
                    found.setdefault(name, set()).update(raw)
            if not found and isinstance(scope.expression, exp.SetOperation):
                pass
            return found
        select = scope.expression
        if not isinstance(select, exp.Select):
            return {}
        out = {}
        for projection in select.expressions:
            out[projection.alias_or_name] = self._raw(scope, projection, guard)
        return out

    def _raw(self, scope, expression, guard):
        found = set()
        if any(not _aggregated(star, expression) for star in expression.find_all(exp.Star)):
            for source in scope.sources.values():
                found |= self._all_raw(source, guard)
        for column in expression.find_all(exp.Column):
            if _aggregated(column, expression):
                continue
            if column.table and column.table in scope.sources:
                sources = [scope.sources[column.table]]
            elif column.table:
                sources = []
            else:
                sources = list(scope.sources.values())
            for source in sources:
                if isinstance(source, exp.Table):
                    if not _is_temp(source) and not _metadata(source) and self.is_large(source.name):
                        found.add(source.name.upper())
                else:
                    key = (id(source), column.name.lower())
                    if key in guard:
                        continue
                    outputs = self._outputs(source, guard | {key})
                    match = next((raw for name, raw in outputs.items() if name.lower() == column.name.lower()), None)
                    found |= match if match is not None else self._all_raw(source, guard | {key})
        return found

    def _all_raw(self, source, guard):
        if isinstance(source, exp.Table):
            return {source.name.upper()} if not _is_temp(source) and not _metadata(source) and self.is_large(source.name) else set()
        if id(source) in guard:
            return set()
        found = set()
        for raw in self._outputs(source, guard | {id(source)}).values():
            found |= raw
        return found


def _aggregated(column, top):
    node = column.parent
    while node is not None and node is not top.parent:
        if isinstance(node, exp.AggFunc) and not isinstance(node.parent, exp.Window):
            return True
        node = node.parent
    return False


def _dated(node):
    node = _bare(node)
    if isinstance(node, (exp.Cast, exp.TryCast, exp.TimeStrToTime, exp.TsOrDsToDate)):
        node = _bare(node.this)
    return isinstance(node, exp.Literal) and node.is_string and re.match(r"^\d{4}-\d{2}-\d{2}", node.this) is not None


def check(sql, tables, sizes, large=LARGE, cap=CAP, schemas=("dbo",)):
    """The policy on the final text of a script. tables are the tables that the hospital schema names, and sizes is
    {table: rows} from the tables and columns query. Returns {"rules": [{"id", "rule", "passed", "fragments"}],
    "execution_class", "class_says", "outcome", "large_tables", "tables_read", "statements"}."""
    checker = _Checker(tables, sizes, large, cap, schemas)
    try:
        statements, tokens = split(sql)
    except sqlglot.errors.SqlglotError:
        checker.fail("parse", "the script cannot be read into tokens")
        statements, tokens = [], []
    for token in tokens:
        if token.token_type.name in ("STRING", "NATIONAL_STRING"):
            continue
        word = token.text.upper().strip("[]")
        if word in FORBIDDEN_WORDS or re.match(r"^(SP|XP)_\w+", word):
            checker.fail("dynamic", word)
    if not statements:
        checker.fail("parse", "the script holds no statement")
    for text in statements:
        checker.statement(text)
    rules = [{"id": rule, "rule": RULES[rule].format(cap=f"{cap:,}"), "passed": not checker.failures[rule],
              "fragments": checker.failures[rule]} for rule in RULES]
    failed = {r["id"] for r in rules if not r["passed"]}
    if failed - set(EXTRACTION):
        grade = "D"
    elif failed:
        grade = "C"
    elif not checker.clinical:
        grade = "A"
    else:
        grade = "B"
    return {"rules": rules, "execution_class": grade, "class_says": CLASSES[grade],
            "outcome": "passed" if not failed else "failed",
            "large_tables": sorted(checker.large_read), "tables_read": sorted(checker.clinical),
            "metadata_read": sorted(checker.metadata_read), "statements": len(statements),
            "thresholds": {"large_table_rows": large, "cohort_cap": cap}}
