"""Rebuilds fixtures/hospital/, the invented hospital that the front page runs its queries on.

The invented hospital is the invented world exactly as the tests build it (convert.run over make_checks.WORLD with 200
rows): every source table it holds, with the type of each column and every row, one CSV file a table. The page publishes
these files beside the invented dictionary, under example/hospital/, and its worker builds the same database from them,
so that a person trying the page with the invented dictionary can run each query on it. Every value is invented.

The world's generator fills the name column of each table of names (the kinds of reading, the routes, the sexes and the
other lists of codes) with filler, and leaves out any code that its rows use and its list does not hold. The page shows
these names beside each code, so here each list is given a row for every code in use, named as the world's own
conversion names it (fixtures/conversion/source_to_concept_map.csv) and otherwise with an invented name.

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

# Each table of names: its code column, its name column, the vocabularies of the world's conversion that name its
# codes, and the invented names given, in order, to the codes that the conversion does not name.
LOOKUPS = {
    "OBS_TYPE_DEF": ("OBS_TYPE_KEY", "OBS_LABEL", ("SITE_OBS",),
                     ["Temperature", "Respiratory rate", "End-tidal carbon dioxide", "Pain score", "Inspired oxygen"]),
    "EVENT_TYPE_DEF": ("EVENT_TYPE_KEY", "EVENT_LABEL", ("SITE_EVENT_OBS", "SITE_EVENT_PROC"), ["Other event"]),
    "LK_ROUTE": ("ROUTE_CAT", "LABEL", ("SITE_ROUTE",), ["Subcutaneous", "Intramuscular", "Topical"]),
    "LK_SEX": ("SEX_CAT", "LABEL", ("SITE_SEX",), ["Indeterminate", "Not stated"]),
    "LK_RISK_GRADE": ("RISK_GRADE_CAT", "LABEL", ("SITE_RISK_GRADE",), ["ASA class 6"]),
    "LK_CASE_STATUS": ("CASE_STATUS_CAT", "LABEL", ("SITE_CASE_DONE",), ["Booked", "Cancelled", "Postponed", "In theatre"]),
    "LK_SERVICE": ("SERVICE_CAT", "LABEL", (), ["General surgery", "Orthopaedic surgery", "Cardiac surgery", "Neurosurgery",
                                                  "Ear, nose and throat surgery", "Dental surgery", "Ophthalmology",
                                                  "Plastic surgery", "Urology", "Medical imaging"]),
    "WARD_DEF": ("WARD_KEY", "WARD_LABEL", (), ["Ward"]),
}
# The conversion's descriptions that are not plain names, given as names.
PLAIN = {"The status that means a case was carried out": "Carried out"}
# The units of the kinds of reading, by the unit's concept in the world's conversion.
UNITS = {"8541": "beats/min", "8554": "%", "9373": "oz", "8876": "mmHg"}


def world():
    sys.path.insert(0, str(FIXTURES))
    import make_checks
    from schemalyser import convert
    converted, _ = convert.run(make_checks.WORLD, FIXTURES / "conversion", 200)
    return converted


def _conversion_names():
    """{vocabulary: {code: name}} and {code: unit} from the world's conversion."""
    names, units = {}, {}
    with open(FIXTURES / "conversion" / "source_to_concept_map.csv", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            vocabulary, code, words = row["source_vocabulary_id"], row["source_code"], row["source_code_description"]
            if vocabulary == "SITE_OBS_UNIT":
                units[code] = UNITS.get(row["target_concept_id"], "")
            elif vocabulary == "SITE_OBS_SYSTOLIC":
                names.setdefault("SITE_OBS", {}).setdefault(code, "Blood pressure")
            else:
                names.setdefault(vocabulary, {})[code] = PLAIN.get(words, words)
    return names, units


def _named(name, columns, rows, used):
    """The rows of a table of names, with a row for every code in use and a plain name for each code."""
    key, label, vocabularies, invented = LOOKUPS[name]
    names, units = _conversion_names()
    known = {}
    for vocabulary in vocabularies:
        known.update(names.get(vocabulary, {}))
    at = {c: i for i, (c, _) in enumerate(columns)}
    held = {str(row[at[key]]): list(row) for row in rows}
    for code in used:
        if code not in held:
            row = [None] * len(columns)
            row[at[key]] = int(code) if columns[at[key]][1] in ("BIGINT", "INTEGER") else code
            held[code] = row
    spare = 0
    out = []
    for code in sorted(held, key=lambda c: (len(c), c)):
        row = held[code]
        if code in known:
            row[at[label]] = known[code]
        else:
            word = invented[spare % len(invented)]
            round_ = spare // len(invented)
            row[at[label]] = f"{word} {spare + 1}" if name == "WARD_DEF" else word if not round_ else f"{word} {round_ + 1}"
            spare += 1
        if name == "OBS_TYPE_DEF":
            row[at["UNIT_LABEL"]] = units.get(code) or None
        out.append(tuple(row))
    return out


def files(converted):
    """The files of the invented hospital, as {path: bytes}: manifest.json and tables/<TABLE>.csv."""
    con, sandbox = converted.con, converted.sandbox
    found, tables = {}, []
    shapes = {}
    for name in sandbox.tables:
        columns = [(row[0], row[1]) for row in con.execute(f'DESCRIBE "{name}"').fetchall()]
        rows = con.execute(f'SELECT * FROM "{name}" ORDER BY ALL').fetchall()
        shapes[name] = (columns, rows)
    # The invented catalogue names one table that no request reads, so the world does not build it: the names of the
    # categories of sex. The hospital's database would hold it, so it is made here, so that the page's list of these
    # codes runs as it would there.
    if "LK_SEX" not in shapes:
        shapes["LK_SEX"] = ([("SEX_CAT", "BIGINT"), ("LABEL", "VARCHAR")], [])
    for name, (key, _, _, _) in LOOKUPS.items():
        if name not in shapes:
            continue
        used = set()
        for other, (columns, rows) in shapes.items():
            at = next((i for i, (c, _) in enumerate(columns) if c == key), None)
            if other == name or at is None:
                continue
            used.update(str(row[at]) for row in rows if row[at] is not None)
        shapes[name] = (shapes[name][0], _named(name, *shapes[name], sorted(used)))
    for name in sorted(shapes):
        columns, rows = shapes[name]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow([c for c, _ in columns])
        for row in rows:
            # An empty cell is an empty value, so the generator must never give an empty text.
            assert all(value is None or str(value) != "" for value in row), name
            writer.writerow(["" if value is None else str(value) for value in row])
        found[f"tables/{name}.csv"] = out.getvalue().encode("utf-8")
        tables.append({"name": name, "columns": [[c, t] for c, t in columns], "rows": len(rows)})
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
