"""Translates T-SQL into statements that DuckDB can run.

sqlglot does most of the translation. This module rewrites the T-SQL habits that have a plain
equivalent (variables, temp tables, table variables, three-part names, APPLY) and refuses the
ones that have none (stored procedures, dynamic SQL, IF blocks).

to_duckdb gives, beside the statements, the names of the rewrites it applied, so that a report
can say how the form that DuckDB ran differs from the T-SQL that SQL Server runs. Each name is
one of REWRITES, and sqlglot's own change of dialect, which applies to every statement, is not
listed.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel
from sqlglot.tokens import TokenType

from .statements import drop_old_hints, parse

GO = re.compile(r"^\s*GO\s*;?\s*$", re.I | re.M)
DROP_IF_EXISTS = re.compile(
    r"IF\s+OBJECT_ID\s*\(\s*N?'(?:tempdb\.\.)?(#\w+)'\s*\)\s+IS\s+NOT\s+NULL\s+DROP\s+TABLE\s+#\w+[ \t]*;?", re.I)
COMPACT_DATE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
NAMED_DATE = re.compile(r"^(\d{1,2})[- ]([A-Za-z]{3})[A-Za-z]*[- ](\d{4})$")
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}
OMOP_SCHEMA = "omop"
# SQL Server's own records of its tables, which the practice database keeps in a schema of this name (see
# Sandbox._server_records), each under the name below, so that a query that reads the size of each table, or the
# list of its columns, returns there what SQL Server would.
SERVER_SCHEMA = "sqlserver"
SERVER_RECORDS = {("SYS", "TABLES"): "sys_tables", ("SYS", "PARTITIONS"): "sys_partitions",
                  ("INFORMATION_SCHEMA", "COLUMNS"): "information_schema_columns",
                  ("INFORMATION_SCHEMA", "TABLES"): "information_schema_tables"}
UNSUPPORTED_KEYS = {"ifblock", "whileblock", "execute", "executesql"}
# The rewrites that to_duckdb may apply, each by name, with what it does.
REWRITES = {
    "old_hints_dropped": "A table hint of the old form, such as (NOLOCK) without WITH, is removed.",
    "drop_if_exists": "IF OBJECT_ID(...) IS NOT NULL DROP TABLE becomes DROP TABLE IF EXISTS.",
    "real_as_float_24": "A real is written as float(24), which is single precision in both.",
    "float_as_double": "A float without a size, or with a size above 24, becomes DOUBLE.",
    "server_records": "A read of SQL Server's records of its tables reads the practice database's copy of them.",
    "source_qualifier_dropped": "A database or schema that names the source is removed, and only the omop schema is kept.",
    "table_variable": "A table variable becomes a temporary table.",
    "temp_table": "A temp table keeps a prefix of its own, so that it cannot collide with a real table.",
    "compact_date": "A date written as 20190101 is written in the ISO form.",
    "named_date": "A date written as 01-Jan-2017 is written in the ISO form.",
    "stuff_for_xml_path": "STUFF over FOR XML PATH, which joins values into one string, becomes string_agg.",
    "like_as_ilike": "LIKE becomes ILIKE, because SQL Server compares text without regard to case by default.",
    "quotename_join": "Text joined to QUOTENAME with + is joined with ||.",
    "dateadd_from_zero": "DATEADD(unit, n, 0) counts from the first of January 1900.",
    "variable": "A variable is read with getvariable.",
    "cross_apply": "CROSS APPLY becomes JOIN LATERAL.",
    "outer_apply": "OUTER APPLY becomes LEFT JOIN LATERAL.",
    "declare": "DECLARE becomes SET VARIABLE, or a temporary table for a table variable.",
    "set_variable": "SET of a variable becomes SET VARIABLE.",
    "session_setting_dropped": "A session setting, such as SET NOCOUNT ON, is removed.",
    "whole_division": "A division of whole numbers becomes DuckDB's //, as SQL Server gives a whole number.",
    "print_dropped": "PRINT is removed.",
    "index_dropped": "CREATE INDEX is removed.",
}


class Translated(list):
    """The DuckDB statements, in order, with the names of the rewrites applied, in the order first applied."""

    def __init__(self, statements=(), rewrites=()):
        super().__init__(statements)
        self.rewrites = list(rewrites)


def _applied(found, name):
    """Records that a rewrite was applied, once, where a list is kept."""
    if found is not None and name not in found:
        found.append(name)
UNSUPPORTED_KINDS = {"PROCEDURE", "FUNCTION", "TRIGGER"}


class Unreadable(Exception):
    """The text could not be read as T-SQL."""


class Unsupported(Exception):
    """The statement is valid T-SQL that the sandbox has no way to run."""


def _sql(node):
    return node.sql(dialect="duckdb", unsupported_level=ErrorLevel.IGNORE, comments=False)


def _variable_name(item):
    target = item.this[0] if isinstance(item.this, list) else item.this
    return target.name


DATE_CONTEXT = (exp.Cast, exp.TryCast, exp.TimeStrToTime, exp.DateAdd, exp.DateDiff, exp.TsOrDsAdd)
COMPARES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In)


def _is_date(literal, date_columns):
    """True where a string is used as a date: inside a date function or a cast, or compared with a date column."""
    parent = literal.parent
    if isinstance(parent, DATE_CONTEXT):
        return True
    if isinstance(parent, COMPARES):
        return any(column.name.upper() in date_columns for column in parent.find_all(exp.Column))
    return False


def _mark_real(sql):
    """Writes each T-SQL real as float(24), its exact equivalent, so that it stays single precision.

    The parser reads real and float as the same type, but in SQL Server a float without a size is
    double precision. The tokenizer finds the type names, so text inside strings, comments and
    brackets is left alone.
    """
    try:
        tokens = sqlglot.tokenize(sql, read="tsql")
    except Exception:
        return sql
    for token in reversed(tokens):
        if token.token_type == TokenType.FLOAT and token.text.upper() == "REAL":
            sql = sql[:token.start] + "float(24)" + sql[token.end + 1:]
    return sql


def _widen(node, found=None):
    """A T-SQL float without a size, or with a size above 24, is double precision, as DOUBLE is in DuckDB."""
    for data_type in node.find_all(exp.DataType):
        if data_type.this != exp.DataType.Type.FLOAT:
            continue
        _applied(found, "float_as_double")
        size = data_type.expressions[0].this if data_type.expressions else None
        size = int(size.this) if isinstance(size, exp.Literal) and str(size.this).isdecimal() else None
        data_type.set("expressions", [])
        if size is None or size > 24:
            data_type.set("this", exp.DataType.Type.DOUBLE)
    return node


def _quotename(node):
    return isinstance(node, exp.Anonymous) and str(node.this).upper() == "QUOTENAME"


def _joins_quotename(node):
    """Whether an addition joins text that QUOTENAME made, as in QUOTENAME(schema) + N'.' + QUOTENAME(name)."""
    return any(_joins_quotename(n) if isinstance(n, exp.Add) else _quotename(n) for n in (node.this, node.expression))


def _rewrite(node, date_columns=frozenset(), server_records=False, found=None):
    """Rewrites one statement in place, and returns it. found, where given, is a list to which the name of each
    rewrite that was applied is added once."""
    _widen(node, found)
    for table in node.find_all(exp.Table):
        record = SERVER_RECORDS.get(((table.db or "").upper(), table.name.upper())) if server_records else None
        if record is not None and not table.catalog:
            # The practice database keeps a copy of SQL Server's own records of its tables.
            table.set("db", exp.to_identifier(SERVER_SCHEMA))
            table.set("this", exp.to_identifier(record))
            _applied(found, "server_records")
            continue
        # The OMOP tables live in a schema of their own, so that schema is kept. Every other
        # qualifier names the source database, which the sandbox holds without one.
        if (table.db or "").upper() != OMOP_SCHEMA.upper():
            if table.db:
                _applied(found, "source_qualifier_dropped")
            table.set("db", None)
        if table.catalog:
            _applied(found, "source_qualifier_dropped")
        table.set("catalog", None)
        this = table.this
        if isinstance(this, exp.Parameter):
            # A table variable becomes a temporary table.
            table.set("this", exp.to_identifier(f"tablevar_{this.name}"))
            _applied(found, "table_variable")
        elif isinstance(this, exp.Identifier) and this.args.get("temporary"):
            # A temp table keeps a prefix, so that it cannot collide with a real table.
            this.set("this", f"temp_{this.name.lstrip('#')}")
            _applied(found, "temp_table")
    for literal in node.find_all(exp.Literal):
        if not literal.is_string or not _is_date(literal, date_columns):
            continue
        # SQL Server reads '20190101' and '01-Jan-2017' as dates. DuckDB needs the ISO form.
        if COMPACT_DATE.match(literal.this):
            literal.set("this", f"{literal.this[:4]}-{literal.this[4:6]}-{literal.this[6:]}")
            _applied(found, "compact_date")
        elif (named := NAMED_DATE.match(literal.this)) and named.group(2).upper() in MONTHS:
            literal.set("this", f"{named.group(3)}-{MONTHS[named.group(2).upper()]:02d}-{int(named.group(1)):02d}")
            _applied(found, "named_date")
    for stuff in list(node.find_all(exp.Stuff)):
        # STUFF((SELECT ', ' + x FROM ... FOR XML PATH('')), 1, 2, '') joins the values of x into one
        # string and removes the leading separator. It is rewritten only in exactly that form.
        inner = stuff.this.this if isinstance(stuff.this, exp.Dot) else stuff.this
        select = inner.this if isinstance(inner, exp.Subquery) else None
        if not (isinstance(select, exp.Select) and select.args.get("for_") and len(select.expressions) == 1):
            continue
        joined = select.expressions[0].unalias()
        start, length = stuff.args.get("start"), stuff.args.get("length")
        if not (isinstance(joined, exp.Add) and isinstance(joined.this, exp.Literal) and joined.this.is_string
                and isinstance(start, exp.Literal) and start.this == "1"
                and isinstance(length, exp.Literal) and length.this == str(len(joined.this.this))):
            continue
        separator = _sql(joined.this)
        order = select.args.get("order")
        if order is not None:
            separator += " ORDER BY " + ", ".join(_sql(_rewrite(o.copy(), date_columns, found=found)) for o in order.expressions)
        replacement = select.copy()
        replacement.set("for_", None)
        replacement.set("order", None)
        replacement.set("expressions", [exp.Anonymous(this="string_agg", expressions=[
            exp.cast(joined.expression.copy(), "varchar"), exp.Var(this=separator)])])
        stuff.replace(exp.Subquery(this=replacement))
        _applied(found, "stuff_for_xml_path")
    for like in list(node.find_all(exp.Like)):
        # SQL Server compares text without regard to case by default, so LIKE becomes ILIKE.
        _applied(found, "like_as_ilike")
        ignoring_case = exp.ILike(this=like.this, expression=like.expression, escape=like.args.get("escape"))
        # The parser keeps the NOT of NOT LIKE on the LIKE itself, so it is carried across.
        like.replace(exp.Not(this=exp.Paren(this=ignoring_case)) if like.args.get("negate") else ignoring_case)
    if server_records:
        for added in list(node.find_all(exp.Add)):
            # SQL Server joins text with +, and DuckDB with ||.
            if _joins_quotename(added):
                added.replace(exp.DPipe(this=added.this, expression=added.expression))
                _applied(found, "quotename_join")
    for added in node.find_all(exp.DateAdd):
        # DATEADD(unit, n, 0) counts from the first of January 1900.
        if isinstance(added.this, exp.Literal) and not added.this.is_string:
            added.set("this", exp.cast(exp.Literal.string("1900-01-01"), "timestamp"))
            _applied(found, "dateadd_from_zero")
    for parameter in list(node.find_all(exp.Parameter)):
        parameter.replace(exp.func("getvariable", exp.Literal.string(f"var_{parameter.name}")))
        _applied(found, "variable")
    for join in node.find_all(exp.Join):
        lateral = join.this
        if isinstance(lateral, exp.Lateral) and lateral.args.get("cross_apply") is not None:
            # CROSS APPLY becomes JOIN LATERAL, and OUTER APPLY becomes LEFT JOIN LATERAL.
            _applied(found, "cross_apply" if lateral.args["cross_apply"] else "outer_apply")
            if not lateral.args["cross_apply"]:
                join.set("side", "LEFT")
            lateral.set("cross_apply", None)
            join.set("on", exp.true())
    return node


def _declare(statement, found=None):
    out = []
    _applied(found, "declare")
    for item in statement.expressions:
        name = _variable_name(item)
        kind = item.args.get("kind")
        if isinstance(kind, exp.Expression):
            _widen(kind, found)
        if isinstance(kind, exp.Schema):
            columns = ", ".join(_sql(column) for column in kind.expressions)
            out.append(f"CREATE OR REPLACE TEMPORARY TABLE tablevar_{name} ({columns})")
            continue
        default = item.args.get("default")
        value = _sql(_rewrite(default, found=found)) if isinstance(default, exp.Expression) else "NULL"
        if kind is not None:
            value = f"CAST({value} AS {_sql(kind)})"
        out.append(f"SET VARIABLE var_{name} = {value}")
    return out


def _set(statement, found=None):
    out = []
    for item in statement.expressions:
        assignment = item.this
        if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Parameter):
            out.append(f"SET VARIABLE var_{assignment.this.name} = {_sql(_rewrite(assignment.expression, found=found))}")
            _applied(found, "set_variable")
        else:
            # Anything else is a session setting such as NOCOUNT, which has no meaning here.
            _applied(found, "session_setting_dropped")
    return out


def _assigns_in_select(statement):
    """True for SELECT @n = ..., which gives a variable a value and returns no rows."""
    return any(isinstance(projection.unalias(), exp.EQ) and isinstance(projection.unalias().this, exp.Parameter)
               for select in statement.find_all(exp.Select) for projection in select.expressions)


WHOLE_TYPES = {exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.SMALLINT, exp.DataType.Type.TINYINT}
WHOLE_FUNCTIONS = (exp.Count, exp.Year, exp.Month, exp.Day, exp.DateDiff, exp.IntDiv)


def _whole(node, statement, whole_columns, depth=0):
    """Whether SQL Server would give a whole number for an expression: a whole literal, a count, a date part, a cast to a
    whole type, arithmetic on whole numbers, or a column that is one, through a common table expression or a derived
    table of the same statement. Where it cannot tell, it says no, so that the division stays as it is."""
    if node is None or depth > 12:
        return False
    if isinstance(node, (exp.Paren, exp.Neg, exp.Alias)):
        return _whole(node.this, statement, whole_columns, depth + 1)
    if isinstance(node, exp.Null):
        return True
    if isinstance(node, exp.Literal):
        return not node.is_string and str(node.this).isdecimal()
    if isinstance(node, WHOLE_FUNCTIONS):
        return True
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return node.to.this in WHOLE_TYPES
    if isinstance(node, (exp.Sum, exp.Min, exp.Max)):
        return _whole(node.this, statement, whole_columns, depth + 1)
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Mod, exp.Div)):
        return _whole(node.this, statement, whole_columns, depth + 1) and \
            _whole(node.expression, statement, whole_columns, depth + 1)
    if isinstance(node, exp.Coalesce):
        return all(_whole(e, statement, whole_columns, depth + 1) for e in [node.this, *node.expressions])
    if isinstance(node, exp.Case):
        branches = [i.args.get("true") for i in node.args.get("ifs") or []] + [node.args.get("default") or exp.Null()]
        return all(_whole(b, statement, whole_columns, depth + 1) for b in branches)
    if isinstance(node, exp.Column):
        derived = {cte.alias.upper(): cte.this for cte in statement.find_all(exp.CTE)}
        derived.update({s.alias.upper(): s.this for s in statement.find_all(exp.Subquery) if s.alias})
        source = derived.get(node.table.upper()) if node.table else None
        if source is None and not node.table:
            select = node.find_ancestor(exp.Select)
            from_ = select.args.get("from_") or select.args.get("from") if select is not None else None
            sources = ([from_.this] if from_ is not None else []) + [j.this for j in select.args.get("joins") or []]
            if len(sources) == 1 and isinstance(sources[0], exp.Subquery):
                source = sources[0].this
            elif len(sources) == 1 and isinstance(sources[0], exp.Table):
                source = derived.get(sources[0].name.upper())
        if isinstance(source, exp.Select):
            found = next((p for p in source.expressions if p.alias_or_name.upper() == node.name.upper()), None)
            return found is not None and _whole(found.unalias(), statement, whole_columns, depth + 1)
        return node.name.upper() in whole_columns
    return False


def _whole_division(statement, whole_columns, found=None):
    """SQL Server divides a whole number by a whole number to give a whole number, so that (COUNT(*) / 10) * 10 rounds a
    count down to the nearest ten. DuckDB's / gives a fraction, so each such division becomes DuckDB's //."""
    for division in reversed(list(statement.find_all(exp.Div))):
        if _whole(division.this, statement, whole_columns) and _whole(division.expression, statement, whole_columns):
            division.replace(exp.IntDiv(this=division.this, expression=division.expression))
            _applied(found, "whole_division")
    return statement


def to_duckdb(sql, date_columns=frozenset(), whole_columns=None, server_records=False):
    """Returns the DuckDB statements for a piece of T-SQL, in order, as a Translated list whose rewrites attribute names
    each rewrite of REWRITES that was applied, once, in the order first applied.

    date_columns holds the names of the columns that hold dates, so that a string compared with one
    of them can be read as a date. Where whole_columns is given, as the names of the columns that hold
    whole numbers, a division of whole numbers gives a whole number, as it does in SQL Server; the
    practice database asks for this, so that it returns what SQL Server would. With server_records, a read
    of sys.tables, sys.partitions, INFORMATION_SCHEMA.COLUMNS or INFORMATION_SCHEMA.TABLES reads the practice
    database's copy of those records, in the schema SERVER_SCHEMA, and text joined to QUOTENAME with + is
    joined with ||.
    """
    found = []
    stripped = sql.lstrip("\ufeff")
    sql = drop_old_hints(stripped)
    if sql != stripped:
        _applied(found, "old_hints_dropped")
    sql, dropped = DROP_IF_EXISTS.subn(lambda m: f"DROP TABLE IF EXISTS {m.group(1)};", sql)
    if dropped:
        _applied(found, "drop_if_exists")
    out = Translated()
    for batch in GO.split(sql):
        if not batch.strip():
            continue
        marked = _mark_real(batch)
        if marked != batch:
            _applied(found, "real_as_float_24")
        statements = parse(marked, "tsql")
        if statements is None:
            raise Unreadable
        for statement in statements:
            if statement is None or isinstance(statement, (exp.Semicolon, exp.Use)):
                continue
            if isinstance(statement, exp.Command):
                if str(statement.this).upper() == "PRINT":
                    _applied(found, "print_dropped")
                    continue
                raise Unsupported
            if statement.key in UNSUPPORTED_KEYS or any(n.key in UNSUPPORTED_KEYS for n in statement.walk()):
                raise Unsupported
            if any(isinstance(n, exp.Command) for n in statement.walk()) or _assigns_in_select(statement):
                raise Unsupported
            if isinstance(statement, exp.Create):
                kind = (statement.kind or "").upper()
                if "INDEX" in kind:
                    _applied(found, "index_dropped")
                    continue
                if kind in UNSUPPORTED_KINDS:
                    raise Unsupported
            if isinstance(statement, exp.Declare):
                out.extend(_declare(statement, found))
            elif isinstance(statement, exp.Set):
                out.extend(_set(statement, found))
            else:
                rewritten = _rewrite(statement, date_columns, server_records, found)
                if whole_columns is not None:
                    rewritten = _whole_division(rewritten, whole_columns, found)
                out.append(_sql(rewritten))
    out.rewrites = found
    return out
