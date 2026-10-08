"""Rebuilds fixtures/hospital/, the invented hospital that the front page runs its queries on.

The invented hospital is the invented world exactly as the tests build it (convert.run over make_checks.WORLD with 200
rows): every source table it holds, with the type of each column and every row, one CSV file a table. The page publishes
these files beside the invented dictionary, under example/hospital/, and its worker builds the same database from them,
so that a person trying the page with the invented dictionary can run each query on it. Every value is invented.

    cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../fixtures/make_hospital.py
"""
import csv
import io
import json
import shutil
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
OUT = FIXTURES / "hospital"


def world():
    sys.path.insert(0, str(FIXTURES))
    import make_checks
    from schemalyser import convert
    converted, _ = convert.run(make_checks.WORLD, FIXTURES / "conversion", 200)
    return converted


def files(converted):
    """The files of the invented hospital, as {path: bytes}: manifest.json and tables/<TABLE>.csv."""
    con, sandbox = converted.con, converted.sandbox
    found, tables = {}, []
    for name in sandbox.tables:
        columns = [(row[0], row[1]) for row in con.execute(f'DESCRIBE "{name}"').fetchall()]
        rows = con.execute(f'SELECT * FROM "{name}" ORDER BY ALL').fetchall()
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow([c for c, _ in columns])
        for row in rows:
            # An empty cell is an empty value, so the generator must never give an empty text.
            assert all(value is None or str(value) != "" for value in row), name
            writer.writerow(["" if value is None else str(value) for value in row])
        found[f"tables/{name}.csv"] = out.getvalue().encode("utf-8")
        tables.append({"name": name, "columns": [[c, t] for c, t in columns], "rows": len(rows)})
    # The invented catalogue names one table that no request reads, so the world does not build it: the names of the
    # categories of sex. The hospital's database would hold it, so it is made here from the codes that the patients hold,
    # with a label in the world's own filler, so that the page's list of these codes runs as it would there.
    if "LK_SEX" not in sandbox.tables:
        codes = [row[0] for row in con.execute('SELECT DISTINCT "SEX_CAT" FROM "PERSON_MASTER" '
                                               'WHERE "SEX_CAT" IS NOT NULL ORDER BY 1').fetchall()]
        found["tables/LK_SEX.csv"] = ("SEX_CAT,LABEL\n" + "".join(f"{c},{c + 20}\n" for c in codes)).encode("utf-8")
        tables.append({"name": "LK_SEX", "columns": [["SEX_CAT", "BIGINT"], ["LABEL", "VARCHAR"]], "rows": len(codes)})
        tables.sort(key=lambda t: t["name"])
    manifest = {"about": "The invented hospital: every value is invented, and no row describes a real person.",
                "tables": tables, "date_columns": sorted(sandbox.date_columns),
                "whole_columns": sorted(sandbox.whole_columns)}
    found["manifest.json"] = (json.dumps(manifest, indent=1) + "\n").encode("utf-8")
    return found


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    for path, data in files(world()).items():
        (OUT / path).parent.mkdir(parents=True, exist_ok=True)
        (OUT / path).write_bytes(data)
    print("invented hospital written to", OUT)


if __name__ == "__main__":
    main()
