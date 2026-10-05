"""Rebuilds fixtures/invented-checks.csv.

It builds an invented stand-in database with designed values (invented-design.sql), answers every
planned check from it, and saves the answers in the layout of the check script.

    cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../fixtures/make_checks.py
"""
from pathlib import Path

from schemalyser import harness

FIXTURES = Path(__file__).resolve().parent
REQUESTS = FIXTURES / "requests"
WORLD = harness.World(FIXTURES / "invented-catalogue.csv", REQUESTS, FIXTURES / "invented-site-rules.json",
                      FIXTURES / "invented-design.sql", FIXTURES / "planted-values.txt")

analysis = WORLD.analysis
inventory_zip = harness.inventory_zip
truth = WORLD.truth


def run_checks(result, con):
    rows, failed = harness.run_checks(result, con)
    assert not failed, failed
    return rows


def main():
    first = analysis()
    rows = run_checks(first, truth(first))
    (FIXTURES / "invented-checks.csv").write_text(harness.checks_csv(rows))
    print(len(rows), "check rows written")
    print(harness.scorecard(WORLD), end="")


if __name__ == "__main__":
    main()
