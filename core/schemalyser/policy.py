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
    series             a script that reads a large table reaches it through a series of bounded steps of increasing
                       size, each marked by a comment line of its own just before its statement: "-- series: count", a
                       SELECT COUNT over the cohort bounded by a first and a last date; then "-- series: coverage", a
                       SELECT COUNT over the cohort that joins each link; then "-- series: rows", and no statement
                       before the rows step reads a large table. The structure behind each marker is checked, not
                       only the comment.
    counts_only        no result returns a value of a large table that is not aggregated

The execution class is derived from the rules, never from a flag:

    A  metadata only: the script reads only the server's own records of its tables;
    B  bounded validation: small tables, or large tables reached from a bounded cohort, and counts only;
    C  large clinical extraction: a result returns rows of a large table, a cohort is above the cap or unbounded by
       a period, or a large table is reached without the series;
    D  not permitted: any other rule fails.

A table is large when the tables and columns query gave it at least LARGE rows, or gave no figure for it at all.

The same check reads a conversion step, or a gate of the release script, given as text with purpose="conversion". Such
a text is one or more SELECT statements over the source and target tables with no cohort, so the rules about the cohort,
the series and counts are reported as not applying, the report names what the policy therefore cannot establish, and
the class is C at best. docs/policy.md says how the release script is checked.

POLICY_VERSION is recorded in every report, so that the package's manifest can record the policy that derived its class.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

# The version of the policy: a change to any rule, threshold or class makes a new version, which voids every class
# that an earlier version derived.
POLICY_VERSION = "1"
LARGE = 10_000_000
CAP = 5000
# The purposes of a script that the policy reads: an audit's two-part script, or a conversion step or gate.
PURPOSES = ("audit", "conversion")
# The markers of the series, in the order in which the steps must come.
SERIES = ("count", "coverage", "rows")
SERIES_MARKER = re.compile(r"^[ \t]*--[ \t]*series[ \t]*:.*$", re.I | re.M)

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
# What a conversion step may use besides: the text functions that shape a source value into a field of the target, and
# the ranks that number its rows, each with a window. CHARINDEX is read as StrPosition and LEN as Length.
CONVERSION_FUNCTIONS = {"Left", "Right", "Substring", "Upper", "Lower", "Trim", "Length", "StrPosition", "Replace",
                        "DateFromParts", "DenseRank", "Rank"}
CONVERSION_WINDOWED = {"DenseRank", "Rank"}
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
    "series": "Every large table is reached through the series: a count of the cohort over the period, then the coverage of each link, then the rows.",
    "counts_only": "No result returns a value of a large table that is not aggregated.",
}
# The rules whose failure makes a script a large clinical extraction rather than one that is not permitted.
EXTRACTION = ("cohort_cap", "cohort_period", "series", "counts_only")
# The rules that do not apply to a conversion step or gate, which has no cohort and returns the rows it writes, and
# what the policy therefore cannot establish about it.
COHORT_RULES = ("large_from_cohort", "cohort_cap", "cohort_period", "series", "counts_only")
CANNOT = {
    "no_cohort": "The script fills no cohort, so the policy cannot establish that what it reads from the hospital's tables is bounded by a capped cohort and a period.",
    "conversion": "The script is a conversion step or gate, which reads its source tables whole and returns the rows it writes, so the policy cannot establish that it is bounded by a cohort, reaches large tables through the series, or returns counts only.",
}
CLASSES = {
    "A": "Class A: metadata only. The script reads only the server's own records of its tables.",
    "B": "Class B: bounded validation. The script reads small tables, or large tables reached from a bounded cohort through the series, and returns counts only.",
    "C": "Class C: large clinical extraction. The script returns rows of a large table, its cohort is above the cap or not limited to a period, or it reaches a large table without the series.",
    "D": "Class D: not permitted. The script breaks a rule that no approval can set aside, and is not to be run.",
}


def _fragment(node_or_text, limit=200):
    text = node_or_text if isinstance(node_or_text, str) else node_or_text.sql(dialect="tsql")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def spans(sql):
    """Where each statement of a script begins and ends, as [(first, last)] offsets into the text, split at each
    semicolon outside a string, a bracket or a comment, with the tokens. Raises sqlglot's error where the text cannot
    be read into tokens."""
    tokens = sqlglot.tokenize(sql, dialect="tsql")
    out, first, last = [], None, None
    for token in tokens:
        if token.token_type.name == "SEMICOLON":
            if first is not None:
                out.append((first, last))
            first = last = None
            continue
        if first is None:
            first = token.start
        last = token.end
    if first is not None:
        out.append((first, last))
    return out, tokens


def split(sql):
    """The statements of a script as [text], split at each semicolon outside a string, a bracket or a comment. Raises
    sqlglot's error where the text cannot be read into tokens."""
    found, tokens = spans(sql)
    return [sql[first:last + 1] for first, last in found], tokens


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


def _bounds(statement):
    """Whether a statement tests a column against a first date and a last date, as (first, last)."""
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
    return lower, upper


class _Checker:
    def __init__(self, tables, sizes, large, cap, schemas, purpose="audit"):
        self.tables = {t.upper() for t in tables}
        self.sizes = {k.upper(): v for k, v in (sizes or {}).items()}
        self.large = large
        self.cap = cap
        self.schemas = {s.lower() for s in schemas}
        self.purpose = purpose
        self.failures = {rule: [] for rule in RULES}
        self.clinical, self.metadata_read, self.large_read = set(), set(), set()
        self.temps_filled = []
        # The statement being read, by its place in the script; what each statement reads of large tables; and the
        # tree of each statement that is a query, for the series.
        self.index = 0
        self.large_by_statement = {}
        self.trees = {}
        conversion = purpose == "conversion"
        self.functions = FUNCTIONS | CONVERSION_FUNCTIONS if conversion else FUNCTIONS
        self.windowed = WINDOWED | CONVERSION_WINDOWED if conversion else WINDOWED

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
            self.trees[self.index] = tree
            if into is not None:
                target = into.this
                if not _is_temp(target) or self.purpose == "conversion":
                    self.fail("statements", _fragment(flat))
                    return
                self.filled(tree, tree, target)
            self.query(tree, returns=into is None)
            return
        if self.purpose == "conversion":
            # A conversion step or gate is read as its SELECT statements alone; release.py writes what wraps them.
            self.fail("statements", _fragment(flat))
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
        lower, upper = _bounds(statement)
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
                self.large_by_statement.setdefault(self.index, set()).add(table.name.upper())
        for func in tree.find_all(exp.Func):
            name = type(func).__name__
            # sqlglot counts the operators AND and OR, and each branch of a CASE, among its functions.
            if isinstance(func, (exp.Connector, exp.Binary, exp.Predicate)) or (isinstance(func, exp.If) and isinstance(func.parent, exp.Case)):
                continue
            if isinstance(func, exp.Anonymous) or name not in self.functions:
                self.fail("functions", _fragment(func))
            elif name in self.windowed and not isinstance(func.parent, exp.Window):
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
                if self.purpose == "conversion":
                    # A conversion step joins a target table on its source value, which is text, so a column cast to
                    # text still names the column on which it joins.
                    one, other = _uncast(one), _uncast(other)
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

    # The series: a count, then the coverage of each link, then the rows, before any large table is read.

    def series(self, sql, places):
        """Checks the series of a script whose statements stand at places, [(first, last)] offsets into sql, and
        returns {"required", "steps": [{"step", "statement"}], "first_large_read"}."""
        marks, problems = {}, []
        for found in SERIES_MARKER.finditer(sql):
            line = found.group(0).strip()
            if line not in {f"-- series: {name}" for name in SERIES}:
                problems.append(f"{line} is not one of the markers -- series: count, -- series: coverage and -- series: rows")
                continue
            at = found.start()
            following = next((n for n, (first, _) in enumerate(places) if first > at), None)
            inside = any(first <= at <= last for first, last in places)
            if following is None or inside:
                problems.append(f"{line} stands where no statement follows it")
                continue
            if following in marks:
                problems.append(f"{line} marks a statement that another marker already marks")
                continue
            marks[following] = line[len("-- series: "):]
        steps = [{"step": step, "statement": n + 1} for n, step in sorted(marks.items())]
        reading = sorted(self.large_by_statement)
        first = reading[0] if reading else None
        found = {"required": first is not None, "steps": steps,
                 "first_large_read": {"statement": first + 1, "tables": sorted(self.large_by_statement[first])} if first is not None else None}
        for problem in problems:
            self.fail("series", _fragment(problem))
        if first is None:
            return found
        tables = _and_names(sorted(self.large_by_statement[first]))
        before = [(n, step) for n, step in sorted(marks.items()) if n <= first]
        order = [step for _, step in before]
        if not order:
            self.fail("series", _fragment(f"the script reads {tables} with no series of a count, a coverage and the rows before it"))
            return found
        if order != list(SERIES[:len(order)]) or len(order) > len(SERIES):
            self.fail("series", _fragment(f"the steps before the first read of {tables} are marked {', '.join(order)}, where "
                                          "count, coverage and rows are wanted, in that order"))
            return found
        if len(order) < len(SERIES):
            self.fail("series", _fragment(f"{tables} is read before the {SERIES[len(order)]} step of the series"))
            return found
        for n, step in before:
            tree = self.trees.get(n)
            if tree is None or tree.args.get("into") is not None:
                self.fail("series", _fragment(f"the {step} step is not a SELECT that returns its result"))
                continue
            if step == "rows":
                continue
            outer = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
            ctes = {cte.alias.lower(): cte.this for cte in tree.find_all(exp.CTE)}
            source = (outer.args.get("from_") or outer.args.get("from")) if outer is not None else None
            if outer is None or not any(p.find(exp.Count) is not None for p in outer.expressions):
                self.fail("series", _fragment(f"the {step} step returns no COUNT"))
            if source is None or not self.anchored(source.this, ctes, set()):
                self.fail("series", _fragment(f"the {step} step does not start from the cohort"))
            if step == "count" and not all(_bounds(tree)):
                self.fail("series", _fragment("the count step is not bounded by a first and a last date"))
            if step == "coverage" and not _links(tree):
                self.fail("series", _fragment("the coverage step joins no link to the cohort"))
        return found

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


def _uncast(node):
    return _bare(node.this) if isinstance(node, (exp.Cast, exp.TryCast)) else node


def _and_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _links(tree):
    """Whether a statement joins a link: a join on an equality of two columns, or an EXISTS whose subquery tests one."""
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None and any(isinstance(c, exp.EQ) and isinstance(_bare(c.this), exp.Column) and isinstance(_bare(c.expression), exp.Column)
                                  for c in _conjuncts(on)):
            return True
    for exists in tree.find_all(exp.Exists):
        for c in exists.find_all(exp.EQ):
            if isinstance(_bare(c.this), exp.Column) and isinstance(_bare(c.expression), exp.Column):
                return True
    return False


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


def check(sql, tables, sizes, large=LARGE, cap=CAP, schemas=("dbo",), purpose="audit"):
    """The policy on the final text of a script. tables are the tables that the hospital schema names (and, for a
    conversion step, the target tables it reads), and sizes is {table: rows} from the tables and columns query.
    purpose is "audit" for an audit's script, or "conversion" for a conversion step or a gate of the release script,
    to which the rules about the cohort, the series and counts do not apply.

    Returns {"policy_version", "purpose", "rules": [{"id", "rule", "applies", "passed", "fragments"}],
    "execution_class", "class_says", "outcome", "cannot_establish", "series", "large_tables", "tables_read",
    "metadata_read", "statements", "thresholds"}. A rule that does not apply has passed None."""
    if purpose not in PURPOSES:
        raise ValueError(f"The purpose of a script is one of {', '.join(PURPOSES)}.")
    checker = _Checker(tables, sizes, large, cap, schemas, purpose)
    try:
        places, tokens = spans(sql)
        statements = [sql[first:last + 1] for first, last in places]
    except sqlglot.errors.SqlglotError:
        checker.fail("parse", "the script cannot be read into tokens")
        places, statements, tokens = [], [], []
    for token in tokens:
        if token.token_type.name in ("STRING", "NATIONAL_STRING"):
            continue
        word = token.text.upper().strip("[]")
        if word in FORBIDDEN_WORDS or re.match(r"^(SP|XP)_\w+", word):
            checker.fail("dynamic", word)
    if not statements:
        checker.fail("parse", "the script holds no statement")
    for index, text in enumerate(statements):
        checker.index = index
        checker.statement(text)
    series = checker.series(sql, places)
    applies = {rule: purpose == "audit" or rule not in COHORT_RULES for rule in RULES}
    rules = [{"id": rule, "rule": RULES[rule].format(cap=f"{cap:,}"), "applies": applies[rule],
              "passed": not checker.failures[rule] if applies[rule] else None,
              "fragments": checker.failures[rule] if applies[rule] else []} for rule in RULES]
    failed = {r["id"] for r in rules if r["passed"] is False}
    cannot = []
    if purpose == "conversion":
        cannot.append(CANNOT["conversion"])
    elif checker.clinical and not checker.temps_filled:
        cannot.append(CANNOT["no_cohort"])
    if failed - set(EXTRACTION):
        grade = "D"
    elif failed:
        grade = "C"
    elif not checker.clinical:
        grade = "A"
    elif purpose == "conversion":
        # Nothing bounds what a conversion step reads, so it is a large clinical extraction at best.
        grade = "C"
    else:
        grade = "B"
    return {"policy_version": POLICY_VERSION, "purpose": purpose, "rules": rules, "execution_class": grade,
            "class_says": CLASSES[grade], "outcome": "passed" if not failed else "failed", "cannot_establish": cannot,
            "series": series if purpose == "audit" else None,
            "large_tables": sorted(checker.large_read), "tables_read": sorted(checker.clinical),
            "metadata_read": sorted(checker.metadata_read), "statements": len(statements),
            "thresholds": {"large_table_rows": large, "cohort_cap": cap}}
