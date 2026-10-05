"""Prepares T-SQL for the parser.

SQL Server does not require a semicolon between statements, and it accepts table hints without
the word WITH. The parser needs both. These functions change the text that is parsed and
nothing else: nothing here is ever written to an output.
"""
import sqlglot
from sqlglot import exp
from sqlglot.tokens import Tokenizer, TokenType

HINTS = {"NOLOCK", "READUNCOMMITTED", "READPAST", "HOLDLOCK", "UPDLOCK", "ROWLOCK", "TABLOCK", "TABLOCKX"}
STARTERS = {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "DECLARE", "IF", "EXEC", "EXECUTE", "PRINT",
            "TRUNCATE", "ALTER", "WITH", "SET", "USE", "MERGE", "RAISERROR", "RETURN", "WHILE"}
CARRIES_ON = {"UNION", "ALL", "EXCEPT", "INTERSECT", "AS", "ELSE", "BEGIN", "THEN", "EXISTS", "FOR"}
NOT_A_STATEMENT_AFTER_WITH = {"(", "ROLLUP", "CUBE", "TIES", "CHECK", "RECOMPILE", "NOCHECK"}
QUOTED = {TokenType.STRING, TokenType.IDENTIFIER, TokenType.NATIONAL_STRING}
NAMES = {TokenType.VAR, TokenType.IDENTIFIER}
BEFORE_A_TABLE = {"FROM", "JOIN", ",", "AS", "."}


def _tokens(sql):
    try:
        return Tokenizer(dialect="tsql").tokenize(sql)
    except Exception:
        return None


def drop_old_hints(sql):
    """Removes table hints written without WITH, such as FROM T t (nolock), which the parser misreads.

    It works on the tokens, so that text inside a string or a comment is left alone.
    """
    tokens = _tokens(sql)
    if tokens is None:
        return sql
    cuts = []
    for i in range(2, len(tokens) - 2):
        opening, hint, closing = tokens[i], tokens[i + 1], tokens[i + 2]
        table, before = tokens[i - 1], tokens[i - 2]
        if (opening.token_type == TokenType.L_PAREN and closing.token_type == TokenType.R_PAREN
                and hint.text.upper() in HINTS and table.token_type in NAMES
                and (before.token_type in NAMES or before.text.upper() in BEFORE_A_TABLE
                     or before.token_type == TokenType.R_BRACKET)):
            cuts.append((opening.start, closing.end + 1))
    for start, end in reversed(cuts):
        sql = sql[:start] + sql[end:]
    return sql


def separate(sql):
    """Puts a semicolon before each statement that follows another without one."""
    tokens = _tokens(sql)
    if tokens is None:
        return sql
    cuts, depth, pending, previous = [], 0, None, None
    for i, token in enumerate(tokens):
        word = token.text.upper()
        if token.token_type == TokenType.L_PAREN:
            depth += 1
        elif token.token_type == TokenType.R_PAREN:
            depth = max(0, depth - 1)
        elif (depth == 0 and token.token_type not in QUOTED and word in STARTERS
              # A bracketed or quoted name such as [SET], and a column such as t.print, are not statements.
              and sql[token.start:token.start + 1] not in ('[', '"')
              and sql[max(0, token.start - 1):token.start] not in ('[', '"')
              and (previous is None or previous.token_type != TokenType.DOT)):
            following = tokens[i + 1].text.upper() if i + 1 < len(tokens) else ""
            hint = word == "WITH" and following in NOT_A_STATEMENT_AFTER_WITH
            belongs = (
                previous is None or previous.token_type == TokenType.SEMICOLON
                or previous.text.upper() in CARRIES_ON or hint or pending == "IF"
                or (pending == "INSERT" and word in ("SELECT", "WITH", "EXEC", "EXECUTE"))
                or (pending == "UPDATE" and word == "SET")
                or (pending == "WITH" and word in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"))
            )
            if not belongs:
                cuts.append(token.start)
            if not hint:
                pending = word if word in ("INSERT", "UPDATE", "WITH", "IF") else None
        previous = token
    for position in reversed(cuts):
        sql = sql[:position] + "; " + sql[position:]
    return sql


def _read(sql, dialect):
    try:
        return [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception:
        # A parser error, or a query nested too deeply for the parser.
        return None


def parse(batch, dialect="tsql"):
    """Parses a batch, separating its statements first where that reads more of it.

    T-SQL needs no semicolon between statements, but the parser does: without one it either fails
    or keeps everything after the first statement as unread text. Returns None if the batch
    cannot be read at all.
    """
    statements = _read(batch, dialect)
    if dialect != "tsql":
        return statements
    understood = lambda found: sum(1 for s in found if not isinstance(s, exp.Command))  # noqa: E731
    if statements is None or any(isinstance(s, exp.Command) for s in statements):
        separated = _read(separate(batch), dialect)
        if separated is not None and (statements is None or understood(separated) > understood(statements)):
            return separated
    return statements
