"""Rebuilds a computed expression from allowlisted parts.

The expression in a request is never copied. A new expression is built node by
node: a resolved column becomes the catalogue's own name, a literal becomes a
placeholder, and a function, keyword, date part or type survives only if the
fixed vocabulary lists it. Anything else makes the whole expression unwritable.
The rebuilt text is then checked word by word before it is returned.
"""
import re

from sqlglot import exp
from sqlglot.tokens import Tokenizer

from . import vocabulary as v

WORD = re.compile(r"^[A-Za-z_@#$][\w@#$]*$")
QUERY = (exp.Select, exp.SetOperation, exp.Subquery, exp.Exists)
COLLAPSE = (exp.In, exp.Tuple)


class Unwritable(Exception):
    pass


def _placeholder(name):
    return exp.Var(this=name)


def _rebuild(node, resolve):
    if isinstance(node, exp.Column):
        found = resolve(node)
        if found is None:
            return _placeholder(v.P_COLUMN)
        return exp.column(found.name, table=found.table)
    if isinstance(node, exp.Literal):
        return _placeholder(v.P_STRING if node.is_string else v.P_NUMBER)
    if isinstance(node, (exp.Parameter, exp.SessionParameter, exp.Placeholder)):
        return _placeholder(v.P_VARIABLE)
    if isinstance(node, QUERY):
        return _placeholder(v.P_SUBQUERY)
    if isinstance(node, exp.Null):
        return exp.Null()
    if isinstance(node, exp.Boolean):
        return exp.Boolean(this=bool(node.this))
    if isinstance(node, exp.Star):
        return exp.Star()
    if isinstance(node, exp.TimeStrToTime):
        # sqlglot wraps the arguments of date functions in this node; it is not in the request.
        return _rebuild(node.this, resolve)
    if isinstance(node, exp.Var):
        word = node.name.upper()
        if word not in v.DATE_PARTS:
            raise Unwritable
        return exp.Var(this=next(w for w in v.DATE_PARTS if w == word))
    if isinstance(node, exp.DataType):
        if not isinstance(node.this, exp.DataType.Type):
            raise Unwritable
        return exp.DataType(this=node.this)
    if isinstance(node, exp.Anonymous):
        word = node.name.upper()
        if word not in v.FUNCTIONS:
            raise Unwritable
        name = next(w for w in v.FUNCTIONS if w == word)
        return exp.Anonymous(this=name, expressions=[_rebuild(a, resolve) for a in node.expressions])
    if isinstance(node, (exp.Identifier, exp.Dot, exp.Command)):
        raise Unwritable

    args = {}
    for key, value in node.args.items():
        if isinstance(node, exp.Ordered) and key == "nulls_first":
            # Set to SQL Server's own default, so that the generator adds no code to emulate another.
            args[key] = not node.args.get("desc")
            continue
        if value is None or isinstance(value, bool):
            args[key] = value
        elif isinstance(value, exp.Expression):
            args[key] = _rebuild(value, resolve)
        elif isinstance(value, list):
            items = []
            for item in value:
                if not isinstance(item, exp.Expression):
                    raise Unwritable
                items.append(_rebuild(item, resolve))
            if isinstance(node, COLLAPSE):
                items = _collapse(items)
            args[key] = items
        elif isinstance(value, str) and value.upper() in v.KEYWORDS:
            args[key] = next(w for w in v.KEYWORDS if w == value.upper())
        else:
            raise Unwritable
    return type(node)(**args)


def _collapse(items):
    """A list of identical placeholders becomes one, so a list's length is not disclosed."""
    kept = []
    for item in items:
        if kept and isinstance(item, exp.Var) and item.name in v.PLACEHOLDERS and item == kept[-1]:
            continue
        kept.append(item)
    return kept


def _check(text, catalogue_names):
    for token in Tokenizer(dialect="tsql").tokenize(text):
        # The tokeniser returns some keyword pairs, such as ORDER BY, as one token.
        for word in token.text.split():
            if WORD.match(word):
                if word.upper() not in catalogue_names and word.upper() not in v.WORDS:
                    raise Unwritable
            elif any(ch.isalnum() for ch in word):
                raise Unwritable


def write(node, resolve, catalogue_names):
    """Returns the rebuilt expression as text, or None if it cannot be written safely."""
    try:
        text = _rebuild(node, resolve).sql(dialect="tsql", comments=False)
        _check(text, catalogue_names)
    except Unwritable:
        return None
    for sentinel, shown in v.PLACEHOLDERS.items():
        text = text.replace(sentinel, shown)
    return text
