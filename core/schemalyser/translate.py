"""Translates T-SQL into statements that DuckDB can run.

sqlglot does most of the translation. This module rewrites the T-SQL habits that have a plain
equivalent (variables, temp tables, table variables, three-part names, APPLY) and refuses the
ones that have none (stored procedures, dynamic SQL, IF blocks).
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
UNSUPPORTED_KEYS = {"ifblock", "whileblock", "execute", "executesql"}
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


def _widen(node):
    """A T-SQL float without a size, or with a size above 24, is double precision, as DOUBLE is in DuckDB."""
    for data_type in node.find_all(exp.DataType):
        if data_type.this != exp.DataType.Type.FLOAT:
            continue
        size = data_type.expressions[0].this if data_type.expressions else None
        size = int(size.this) if isinstance(size, exp.Literal) and str(size.this).isdecimal() else None
        data_type.set("expressions", [])
        if size is None or size > 24:
            data_type.set("this", exp.DataType.Type.DOUBLE)
    return node


def _rewrite(node, date_columns=frozenset()):
    _widen(node)
    for table in node.find_all(exp.Table):
        # The OMOP tables live in a schema of their own, so that schema is kept. Every other
        # qualifier names the source database, which the sandbox holds without one.
        if (table.db or "").upper() != OMOP_SCHEMA.upper():
            table.set("db", None)
        table.set("catalog", None)
        this = table.this
        if isinstance(this, exp.Parameter):
            # A table variable becomes a temporary table.
            table.set("this", exp.to_identifier(f"tablevar_{this.name}"))
        elif isinstance(this, exp.Identifier) and this.args.get("temporary"):
            # A temp table keeps a prefix, so that it cannot collide with a real table.
            this.set("this", f"temp_{this.name.lstrip('#')}")
    for literal in node.find_all(exp.Literal):
        if not literal.is_string or not _is_date(literal, date_columns):
            continue
        # SQL Server reads '20190101' and '01-Jan-2017' as dates. DuckDB needs the ISO form.
        if COMPACT_DATE.match(literal.this):
            literal.set("this", f"{literal.this[:4]}-{literal.this[4:6]}-{literal.this[6:]}")
        elif (named := NAMED_DATE.match(literal.this)) and named.group(2).upper() in MONTHS:
            literal.set("this", f"{named.group(3)}-{MONTHS[named.group(2).upper()]:02d}-{int(named.group(1)):02d}")
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
            separator += " ORDER BY " + ", ".join(_sql(_rewrite(o.copy(), date_columns)) for o in order.expressions)
        replacement = select.copy()
        replacement.set("for_", None)
        replacement.set("order", None)
        replacement.set("expressions", [exp.Anonymous(this="string_agg", expressions=[
            exp.cast(joined.expression.copy(), "varchar"), exp.Var(this=separator)])])
        stuff.replace(exp.Subquery(this=replacement))
    for like in list(node.find_all(exp.Like)):
        # SQL Server compares text without regard to case by default, so LIKE becomes ILIKE.
        ignoring_case = exp.ILike(this=like.this, expression=like.expression, escape=like.args.get("escape"))
        # The parser keeps the NOT of NOT LIKE on the LIKE itself, so it is carried across.
        like.replace(exp.Not(this=exp.Paren(this=ignoring_case)) if like.args.get("negate") else ignoring_case)
    for added in node.find_all(exp.DateAdd):
        # DATEADD(unit, n, 0) counts from the first of January 1900.
        if isinstance(added.this, exp.Literal) and not added.this.is_string:
            added.set("this", exp.cast(exp.Literal.string("1900-01-01"), "timestamp"))
    for parameter in list(node.find_all(exp.Parameter)):
        parameter.replace(exp.func("getvariable", exp.Literal.string(f"var_{parameter.name}")))
    for join in node.find_all(exp.Join):
        lateral = join.this
        if isinstance(lateral, exp.Lateral) and lateral.args.get("cross_apply") is not None:
            # CROSS APPLY becomes JOIN LATERAL, and OUTER APPLY becomes LEFT JOIN LATERAL.
            if not lateral.args["cross_apply"]:
                join.set("side", "LEFT")
            lateral.set("cross_apply", None)
            join.set("on", exp.true())
    return node


def _declare(statement):
    out = []
    for item in statement.expressions:
        name = _variable_name(item)
        kind = item.args.get("kind")
        if isinstance(kind, exp.Expression):
            _widen(kind)
        if isinstance(kind, exp.Schema):
            columns = ", ".join(_sql(column) for column in kind.expressions)
            out.append(f"CREATE OR REPLACE TEMPORARY TABLE tablevar_{name} ({columns})")
            continue
        default = item.args.get("default")
        value = _sql(_rewrite(default)) if isinstance(default, exp.Expression) else "NULL"
        if kind is not None:
            value = f"CAST({value} AS {_sql(kind)})"
        out.append(f"SET VARIABLE var_{name} = {value}")
    return out


def _set(statement):
    out = []
    for item in statement.expressions:
        assignment = item.this
        if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Parameter):
            out.append(f"SET VARIABLE var_{assignment.this.name} = {_sql(_rewrite(assignment.expression))}")
        # Anything else is a session setting such as NOCOUNT, which has no meaning here.
    return out


def _assigns_in_select(statement):
    """True for SELECT @n = ..., which gives a variable a value and returns no rows."""
    return any(isinstance(projection.unalias(), exp.EQ) and isinstance(projection.unalias().this, exp.Parameter)
               for select in statement.find_all(exp.Select) for projection in select.expressions)


def to_duckdb(sql, date_columns=frozenset()):
    """Returns the DuckDB statements for a piece of T-SQL, in order.

    date_columns holds the names of the columns that hold dates, so that a string compared with one
    of them can be read as a date.
    """
    sql = drop_old_hints(sql.lstrip("\ufeff"))
    sql = DROP_IF_EXISTS.sub(lambda m: f"DROP TABLE IF EXISTS {m.group(1)};", sql)
    out = []
    for batch in GO.split(sql):
        if not batch.strip():
            continue
        statements = parse(_mark_real(batch), "tsql")
        if statements is None:
            raise Unreadable
        for statement in statements:
            if statement is None or isinstance(statement, (exp.Semicolon, exp.Use)):
                continue
            if isinstance(statement, exp.Command):
                if str(statement.this).upper() == "PRINT":
                    continue
                raise Unsupported
            if statement.key in UNSUPPORTED_KEYS or any(n.key in UNSUPPORTED_KEYS for n in statement.walk()):
                raise Unsupported
            if any(isinstance(n, exp.Command) for n in statement.walk()) or _assigns_in_select(statement):
                raise Unsupported
            if isinstance(statement, exp.Create):
                kind = (statement.kind or "").upper()
                if "INDEX" in kind:
                    continue
                if kind in UNSUPPORTED_KINDS:
                    raise Unsupported
            if isinstance(statement, exp.Declare):
                out.extend(_declare(statement))
            elif isinstance(statement, exp.Set):
                out.extend(_set(statement))
            else:
                out.append(_sql(_rewrite(statement, date_columns)))
    return out
