"""The rule that a text of T-SQL is one SELECT, or one union of SELECTs, that only reads.

A step of a conversion, a gate, a role view of a hospital schema and a question over the roles are each held to it, so
the release script (release.py), the map's own checks (rolemap.py) and the compilation of a question (compiler.py) all
read it from here. It refuses a variable, a function that reaches another server or runs text as SQL, a statement or a
clause that changes something, a table named with more than three parts, and any text that sqlcmd would read as a
command or a variable. It knows nothing of any layer: it reads T-SQL and nothing else.
"""
import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

# Functions that reach another server or run text as SQL. A step or a gate may name none of them.
REFUSED_WORDS = {"OPENQUERY", "OPENROWSET", "OPENDATASOURCE", "OPENXML", "EXEC", "EXECUTE", "SP_EXECUTESQL", "XP_CMDSHELL"}
# Statements and clauses that change something. A step or a gate may hold none of them.
REFUSED_NODES = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "TruncateTable", "Alter", "Command", "Into", "Execute", "Use")
    if hasattr(exp, name))
VARIABLE_TOKENS = {getattr(TokenType, name) for name in ("PARAMETER", "SESSION_PARAMETER") if hasattr(TokenType, name)}
QUOTED_TOKENS = {TokenType.STRING, TokenType.NATIONAL_STRING, TokenType.IDENTIFIER}


class Refused(ValueError):
    """Something in the conversion folder or the settings cannot go into a release script, or a text that is not one read-only SELECT."""


def check_text(value, where):
    """Refuses text that could carry a sqlcmd command or variable: a line break, or $(."""
    if "\r" in value or "\n" in value or "$(" in value:
        raise Refused(f"{where}: a value may not hold a line break or $(, which sqlcmd would read as a command or a variable")
    return value


def _single_select(sql, where):
    """The parsed tree of one SELECT, or of a union of SELECTs, after every rule that keeps a script safe."""
    try:
        tokens = sqlglot.tokenize(sql, dialect="tsql")
        trees = [tree for tree in sqlglot.parse(sql, dialect="tsql") if tree is not None]
    except sqlglot.errors.SqlglotError as error:
        raise Refused(f"{where}: the SQL cannot be read ({str(error).splitlines()[0]})") from None
    for token in tokens:
        check_text(token.text, where)
        if token.token_type in VARIABLE_TOKENS or token.text.startswith("@"):
            raise Refused(f"{where}: a step or a gate may not use a variable")
        if token.token_type not in QUOTED_TOKENS and token.text.upper() in REFUSED_WORDS:
            raise Refused(f"{where}: a step or a gate may not use {token.text.upper()}")
    if len(trees) != 1:
        raise Refused(f"{where}: a step or a gate must be exactly one statement, and this holds {len(trees)}")
    tree = trees[0]
    selects = [tree] if isinstance(tree, exp.Select) else list(tree.find_all(exp.Select)) if isinstance(tree, exp.SetOperation) else []
    if not selects or (isinstance(tree, exp.SetOperation) and any(
            not isinstance(side, (exp.Select, exp.SetOperation, exp.Subquery)) for node in tree.find_all(exp.SetOperation)
            for side in (node.this, node.expression))):
        raise Refused(f"{where}: a step or a gate must be one SELECT, or a union of SELECTs")
    for node in tree.walk():
        if isinstance(node, REFUSED_NODES) or (isinstance(node, exp.Select) and node.args.get("into")):
            raise Refused(f"{where}: a step or a gate may only read, and this one holds {node.key.upper()}")
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier) and node.this is not None:
            raise Refused(f"{where}: a table may be named with at most three parts, and may not be a function")
    return tree


