"""Runs the analysis from the command line, for use outside the browser.

    python -m schemalyser REQUESTS_DIR --catalogue catalogue.csv [--rules site-rules.json] [--dialect tsql] --out OUT_DIR
"""
import argparse
from pathlib import Path

from . import Analysis
from .extract import decode


def main():
    parser = argparse.ArgumentParser(prog="schemalyser")
    parser.add_argument("requests", type=Path)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--dialect", default="tsql", help="the SQL dialect of the requests (default: tsql)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    analysis = Analysis(args.catalogue.read_text(encoding="utf-8"),
                        args.rules.read_text(encoding="utf-8") if args.rules else None, args.dialect)
    for path in sorted(args.requests.rglob("*.sql")):
        analysis.add_request(path.relative_to(args.requests).as_posix(), decode(path.read_bytes()))

    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in analysis.pack().items():
        (args.out / name).write_text(text, encoding="utf-8")
    print(analysis.pack()["coverage.txt"], end="")


if __name__ == "__main__":
    main()
