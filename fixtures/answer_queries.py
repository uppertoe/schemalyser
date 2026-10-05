"""Answers Schemalyser's plain queries from the invented stand-in database, as SQL Server would, for the tests.

The stand-in database is the sandbox that the conversion runner builds for the invented world, with the
conversion's own SQL included, which is what the SQL Server harness loads into clarity_shadow. A plain
query runs in DuckDB through the project's translation, with the hint WITH (NOLOCK) removed, because
DuckDB cannot read it, and with whole-number division written as DuckDB writes it. The table sizes query
reads SQL Server's own records, which DuckDB does not have, so it is answered by counting the rows of each
table that it names; a name that the stand-in database does not hold comes back as unrecorded.

The answer is tab-separated with a header row, as SQL Server Management Studio copies its results grid.

    cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../fixtures/answer_queries.py < queries.json

reads a JSON list of queries on standard input and prints the answer to all of them.
"""
import json
import re
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
sys.path.insert(0, str(FIXTURES))
sys.path.insert(0, str(FIXTURES.parent / "core"))

from schemalyser import convert  # noqa: E402
from schemalyser.checks import LAYOUT, UNRECORDED  # noqa: E402
from schemalyser.translate import to_duckdb  # noqa: E402

import make_checks  # noqa: E402

NAMED = re.compile(r"\(N'([^']*)', N'([^']+)'\)")
_con = None


def stand_in():
    """The invented stand-in database, built once."""
    global _con
    if _con is None:
        _con = convert.run(make_checks.WORLD, FIXTURES / "conversion", 500)[0].con
    return _con


def _cell(value):
    return "NULL" if value is None else str(value)


def rows(sql, con=None):
    """The rows that one plain query gives, as lists of text, with NULL for an empty value."""
    con = con or stand_in()
    if "sys.partitions" in sql:
        found = []
        held = {name.upper() for (name,) in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}
        for _, name in sorted(NAMED.findall(sql), key=lambda pair: pair[1]):
            if name.upper() in held:
                count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                found.append(["rows", name, None, None, None, count // 10 * 10, None, None, None])
            else:
                found.append(["skipped", name, None, "rows", UNRECORDED, None, None, None, None])
        return [[_cell(v) for v in row] for row in found]
    result = []
    for statement in to_duckdb(sql.replace(" WITH (NOLOCK)", "")):
        statement = re.sub(r"(?<![/*]) / (?=\d)", " // ", statement)
        result = con.execute(statement).fetchall()
    return [[_cell(v) for v in row] for row in result]


def answer(queries, con=None):
    """The answers to several plain queries, as one tab-separated text with a header row."""
    lines = ["\t".join(LAYOUT)]
    for sql in queries:
        lines += ["\t".join(row) for row in rows(sql, con)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(answer(json.load(sys.stdin)))
