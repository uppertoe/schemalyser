"""Equivalence: do two hospital schemas describe the same record, and does a question answered through each give the same
answer, on one stand-in database.

The measure of the whole pipeline is whether a schema built from the naive state, a bare dictionary and a person's
answers, says the same as a schema written by hand against the hospital's tables. This module runs each part of the
record through both schemas on a stand-in (the invented hospital, or any stand-in in its layout) and compares what
comes back: the rows, the keys, and the whole rows shared. It then compiles each question over the roles through both
schemas, runs both, and compares the answers row by row. Every figure is a count, so the summary can be shared; --names
prints the keys that only one side gives, which on a stand-in are invented values.

    python -m schemalyser.equivalence SCHEMA_A SCHEMA_B --hospital FOLDER --catalogue FILE [--question FILE ...] [--names] [--json]

A schema is a saved hospital schema or a map folder. A part that only one schema supplies is reported as such and
not compared. The comparison of a part is by the part's key, as the contract names it, and then by every column.
"""
import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import compiler, release, rolemap
from .hospital import HospitalError, InventedHospital, files_from

STAMP = re.compile(r"^--.*\n", re.M)
DECIMAL_TEXT = re.compile(r"-?\d+\.\d+(e-?\d+)?", re.I)


class EquivalenceError(ValueError):
    """The schemas or the stand-in cannot be read; the message says why."""


def _cell(value):
    """A value as the comparison sees it: a number is rounded to twelve significant figures, because two views written
    apart from each other may convert a unit by a division in one and a multiplication in the other and differ in the
    last binary digit, which is no difference in what the record says. Twelve figures leave that digit out and keep
    every figure a record holds, so that two equal values never fall on opposite sides of a rounding."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (float, Decimal)):
        return float(f"{float(value):.12g}")
    if isinstance(value, str) and DECIMAL_TEXT.fullmatch(value):
        # The stand-in gives every value as text, and a number with a decimal point is one.
        return float(f"{float(value):.12g}")
    return value


def _row(row):
    return tuple(_cell(v) for v in row)


def _keyed(columns, rows, key):
    at = [columns.index(k) for k in key if k in columns]
    keys = {tuple(_cell(r[i]) for i in at) for r in rows} if at else set()
    whole = Counter(_row(r) for r in rows)
    return keys, whole


def compare_rows(a, b, key, opaque=()):
    """(columns, rows) of two runs of one part, compared by its key and then whole: a dict of counts. Whole rows are
    compared as they come, duplicates included, so that a row given twice by one side and once by the other counts as
    one shared and one not. opaque names the columns that hold a local key, which each schema makes from the
    hospital's code with its own salt, so that two schemas never agree on them: they are compared only as filled or
    empty."""
    columns_a, rows_a = a
    columns_b, rows_b = b
    if opaque:
        rows_a = [[("filled" if v is not None else None) if c in opaque else v for c, v in zip(columns_a, r)] for r in rows_a]
        rows_b = [[("filled" if v is not None else None) if c in opaque else v for c, v in zip(columns_b, r)] for r in rows_b]
    keys_a, whole_a = _keyed(columns_a, rows_a, key)
    keys_b, whole_b = _keyed(columns_b, rows_b, key)
    shared = sum((whole_a & whole_b).values()) if columns_a == columns_b else 0
    return {"rows_a": len(rows_a), "rows_b": len(rows_b), "keys_a": len(keys_a), "keys_b": len(keys_b),
            "keys_shared": len(keys_a & keys_b), "keys_only_a": len(keys_a - keys_b), "keys_only_b": len(keys_b - keys_a),
            "rows_shared": shared, "same_columns": columns_a == columns_b,
            "_only_a": sorted(keys_a - keys_b)[:20], "_only_b": sorted(keys_b - keys_a)[:20]}


def compare_schemas(hospital, map_a, map_b, questions=()):
    """Runs every part both maps supply on the stand-in and compares, then each question compiled through each.
    Returns {"parts": {view: counts or {"only": "a"|"b"}}, "questions": {name: counts}}."""
    keys = {v["name"]: v["key"] for v in rolemap.contract()["views"]}
    opaque = {v["name"]: {c["name"] for c in v["columns"] if c["type"] == "local_key"} for v in rolemap.contract()["views"]}
    parts = {}
    for view in rolemap.all_views():
        in_a, in_b = view in map_a["views"], view in map_b["views"]
        if not in_a and not in_b:
            continue
        if in_a != in_b:
            parts[view] = {"only": "a" if in_a else "b"}
            continue
        try:
            got_a = hospital.run(STAMP.sub("", map_a["views"][view]))
            got_b = hospital.run(STAMP.sub("", map_b["views"][view]))
        except (HospitalError, ValueError) as error:
            parts[view] = {"error": str(error).splitlines()[0][:200]}
            continue
        parts[view] = compare_rows(got_a, got_b, keys.get(view, []), opaque.get(view, set()))
    answers = {}
    for name, sql in questions:
        try:
            # The comparison is a rehearsal instrument, so it reads draft parts, which is how a draft earns its evidence.
            got_a = hospital.run(compiler.compile_query(sql, map_a, admit="draft"))
            got_b = hospital.run(compiler.compile_query(sql, map_b, admit="draft"))
        except (HospitalError, ValueError, rolemap.MapError) as error:
            answers[name] = {"error": str(error).splitlines()[0][:200]}
            continue
        answers[name] = compare_answers(got_a, got_b)
    return {"parts": parts, "questions": answers}


def compare_answers(a, b):
    """(columns, rows) of one question answered through two schemas, compared row by row with duplicates kept: an
    answer that gives a row twice where the other gives it once is not the same answer, because the row stands for an
    event of the record and losing one loses the event."""
    columns_a, rows_a = a
    columns_b, rows_b = b
    whole_a, whole_b = Counter(_row(r) for r in rows_a), Counter(_row(r) for r in rows_b)
    return {"rows_a": len(rows_a), "rows_b": len(rows_b), "rows_shared": sum((whole_a & whole_b).values()),
            "same": whole_a == whole_b and columns_a == columns_b}


def summary(found):
    """The comparison as lines of counts."""
    lines = []
    for view, c in found["parts"].items():
        title = rolemap.view_title(view, False)
        if "only" in c:
            lines.append(f"{title}: only the {'first' if c['only'] == 'a' else 'second'} schema supplies it.")
        elif "error" in c:
            lines.append(f"{title}: could not be run on the stand-in.")
        else:
            lines.append(f"{title}: {c['rows_a']} rows and {c['keys_a']} keys through the first schema, {c['rows_b']} rows and "
                         f"{c['keys_b']} keys through the second; {c['keys_shared']} keys in both, {c['keys_only_a']} only in the first, "
                         f"{c['keys_only_b']} only in the second; {c['rows_shared']} whole rows the same"
                         + ("" if c["same_columns"] else "; the columns differ") + ".")
    for name, c in found["questions"].items():
        if "error" in c:
            lines.append(f"{name}: could not be compiled or run.")
        else:
            lines.append(f"{name}: {c['rows_a']} rows through the first schema, {c['rows_b']} through the second, {c['rows_shared']} the same"
                         + (": the answers agree." if c["same"] else ": the answers differ."))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.equivalence", description=__doc__.split("\n")[0])
    parser.add_argument("schema_a", help="a saved hospital schema or a map folder")
    parser.add_argument("schema_b", help="the other schema, such as one written by hand")
    parser.add_argument("--hospital", required=True, help="the stand-in's folder, in the invented hospital's layout")
    parser.add_argument("--catalogue", required=True, help="the stand-in's catalogue")
    parser.add_argument("--question", action="append", default=[], help="a question over the roles, as one SELECT; may repeat")
    parser.add_argument("--names", action="store_true", help="print the keys only one side gives")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        map_a = release.read_schema(None, schema=args.schema_a)
        map_b = release.read_schema(None, schema=args.schema_b)
        hospital = InventedHospital(files_from(args.hospital, args.catalogue))
    except (release.Refused, rolemap.MapError, HospitalError, OSError, ValueError, KeyError) as error:
        print(f"The comparison cannot start: {error}", file=sys.stderr)
        return 1
    questions = [(Path(q).stem, Path(q).read_text(encoding="utf-8")) for q in args.question]
    found = compare_schemas(hospital, map_a, map_b, questions)
    if args.json:
        print(json.dumps({"parts": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in found["parts"].items()},
                          "questions": found["questions"]}, indent=1))
        return 0
    print("\n".join(summary(found)))
    if args.names:
        for view, c in found["parts"].items():
            for side, held in (("first", c.get("_only_a")), ("second", c.get("_only_b"))):
                if held:
                    print(f"- {view}: keys only in the {side} schema: {', '.join('-'.join(k) for k in held)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
