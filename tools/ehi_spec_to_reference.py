"""Converts a downloaded copy of a vendor's published table specification into CSV.

The specification is a folder of HTML pages, one per table. This script reads
them and writes three files beside the folder:

    ehi-tables.csv            one row per table: name, primary key, description
    ehi-catalogue.csv         one row per column, in the layout of catalogue.csv
                              with four further columns
    ehi-category-entries.csv  one row per published category entry

Usage:  python tools/ehi_spec_to_reference.py reference/epic-ehi/spec

The specification is the vendor's copyright. Keep it and these outputs out of
version control.
"""
import csv
import html
import re
import sys
from pathlib import Path

NAME = re.compile(r'<table class="Header2"><tbody><tr><td>(.*?)</td>')
DESCRIPTION = re.compile(r'<td class="T1Value" style="white-space: normal;">(.*?)</td>')
KEY_ROW = re.compile(r"<td>([A-Z0-9_]+)</td> <td>(\d+)</td>")
COLUMN_ROW = re.compile(
    r'<td class="T1Head" style="padding: 5px;">(\d+)</td> '
    r'<td class="T1Head" style="padding-right: 12px;">(.*?)</td> '
    r'<td class="T1Head" style="padding-right: 12px;">(.*?)</td> '
    r"<td>(.*?)</td>"
)
COLUMN_DESCRIPTION = re.compile(r'<td style="white-space: normal;">(.*?)</td>')
ORG_SPECIFIC = re.compile(r"May contain organization-specific values: (Yes|No)")
CATEGORY_ENTRY = re.compile(r'<td style="padding-left: 100px;[^"]*">(.*?)</td>')


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def read_table(path):
    page = re.sub(r"\s+", " ", path.read_text(encoding="utf-8", errors="replace"))
    name = NAME.search(page)
    if name is None:
        return None
    key_start = page.find("Primary Key</td>")
    columns_start = page.find("Column Information</td>")
    description = DESCRIPTION.search(page[: key_start if key_start != -1 else columns_start])
    key = []
    if key_start != -1:
        key = [m.group(1) for m in KEY_ROW.finditer(page[key_start:columns_start])]
    columns = []
    matches = list(COLUMN_ROW.finditer(page, columns_start))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page)
        block = page[m.end():end]
        col_description = COLUMN_DESCRIPTION.search(block)
        org_specific = ORG_SPECIFIC.search(block)
        columns.append({
            "position": int(m.group(1)),
            "name": clean(m.group(2)),
            "type": clean(m.group(3)),
            "discontinued": clean(m.group(4)),
            "description": clean(col_description.group(1)) if col_description else "",
            "org_specific": org_specific.group(1) if org_specific else "",
            "entries": [clean(e) for e in CATEGORY_ENTRY.findall(block)],
        })
    return {
        "name": clean(name.group(1)),
        "description": clean(description.group(1)) if description else "",
        "key": key,
        "columns": columns,
    }


def main(spec_dir):
    spec_dir = Path(spec_dir)
    out_dir = spec_dir.parent
    tables = []
    skipped = []
    for path in sorted(spec_dir.glob("*.htm")):
        if path.name.startswith("_"):
            continue
        table = read_table(path)
        if table is None or not table["columns"]:
            skipped.append(path.name)
            continue
        tables.append(table)

    with open(out_dir / "ehi-tables.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TABLE_NAME", "PRIMARY_KEY", "DESCRIPTION"])
        for t in tables:
            w.writerow([t["name"], " ".join(t["key"]), t["description"]])

    with open(out_dir / "ehi-catalogue.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE",
                    "IS_NULLABLE", "IS_PRIMARY_KEY", "DISCONTINUED", "ORG_SPECIFIC", "DESCRIPTION"])
        for t in tables:
            for c in t["columns"]:
                w.writerow(["", t["name"], c["name"], c["position"], c["type"], "",
                            "YES" if c["name"] in t["key"] else "NO",
                            c["discontinued"], c["org_specific"], c["description"]])

    with open(out_dir / "ehi-category-entries.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TABLE_NAME", "COLUMN_NAME", "ENTRY"])
        for t in tables:
            for c in t["columns"]:
                for entry in c["entries"]:
                    w.writerow([t["name"], c["name"], entry])

    n_columns = sum(len(t["columns"]) for t in tables)
    n_entries = sum(len(c["entries"]) for t in tables for c in t["columns"])
    print(f"{len(tables)} tables, {n_columns} columns, {n_entries} category entries")
    print(f"{sum(1 for t in tables if not t['key'])} tables without a primary key")
    if skipped:
        print(f"{len(skipped)} pages skipped: {', '.join(skipped[:10])}")


if __name__ == "__main__":
    main(sys.argv[1])
