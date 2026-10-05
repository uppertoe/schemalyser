"""Parser comparison, sqlglot side.

Reads the invented requests, parses each as T-SQL and writes one JSON object
per file to stdout: parse outcome, statement kinds, base tables and
table.column pairs resolved against the invented catalogue.
"""
import csv
import json
import re
import sys
import time
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GO = re.compile(r"^\s*GO\s*;?\s*$", re.I | re.M)
QUERY = (exp.Select, exp.SetOperation)


def load_catalogue():
    catalogue = {}
    with open(FIXTURES / "invented-catalogue.csv", newline="") as f:
        for row in csv.DictReader(f):
            catalogue.setdefault(row["TABLE_NAME"], {})[row["COLUMN_NAME"]] = row["DATA_TYPE"]
    return catalogue


def outermost_queries(stmt):
    if isinstance(stmt, QUERY):
        return [stmt]
    found = []
    for node in stmt.find_all(*QUERY):
        parent = node.parent
        while parent is not None and not isinstance(parent, QUERY):
            parent = parent.parent
        if parent is None:
            found.append(node)
    return found


def analyse(sql, catalogue):
    out = {"ok": True, "error": None, "statements": 0, "opaque": 0, "kinds": [],
           "tables": set(), "columns": set(), "qualify_errors": []}
    for batch in GO.split(sql):
        if not batch.strip():
            continue
        try:
            statements = sqlglot.parse(batch, read="tsql")
        except SqlglotError as e:
            out["ok"] = False
            out["error"] = type(e).__name__
            continue
        for stmt in statements:
            if stmt is None:
                continue
            out["statements"] += 1
            out["kinds"].append(stmt.key)
            if isinstance(stmt, exp.Command):
                out["opaque"] += 1
                continue
            cte_names = {c.alias_or_name.upper() for c in stmt.find_all(exp.CTE)}
            for table in stmt.find_all(exp.Table):
                name = table.name.upper()
                if name in catalogue and name not in cte_names:
                    out["tables"].add(name)
            for query in outermost_queries(stmt):
                try:
                    qualified = qualify(query.copy(), schema=catalogue, dialect="tsql",
                                        validate_qualify_columns=False,
                                        quote_identifiers=False, identify=False)
                    # scope.columns misses some subqueries (FOR XML PATH), so walk
                    # every column and resolve its alias through the enclosing scopes.
                    scopes = {id(s.expression): s for s in traverse_scope(qualified)}
                    for col in qualified.find_all(exp.Column):
                        node = col.parent
                        while node is not None and id(node) not in scopes:
                            node = node.parent
                        scope = scopes.get(id(node)) if node is not None else None
                        source = None
                        while scope is not None and source is None:
                            source = scope.sources.get(col.table)
                            scope = scope.parent
                        if not isinstance(source, exp.Table):
                            continue
                        t, c = source.name.upper(), col.name.upper()
                        if t in catalogue and c in catalogue[t]:
                            out["columns"].add(f"{t}.{c}")
                except Exception as e:  # the spike records the failure and moves on
                    out["qualify_errors"].append(type(e).__name__)
    out["tables"] = sorted(out["tables"])
    out["columns"] = sorted(out["columns"])
    return out


def main():
    catalogue = load_catalogue()
    results = {}
    for path in sorted((FIXTURES / "requests").rglob("*.sql")):
        rel = path.relative_to(FIXTURES / "requests").as_posix()
        start = time.perf_counter()
        results[rel] = analyse(path.read_text(), catalogue)
        results[rel]["ms"] = round((time.perf_counter() - start) * 1000, 1)
    json.dump({"parser": f"sqlglot {sqlglot.__version__}", "files": results}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
