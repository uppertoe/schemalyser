"""A vendor's published specification of its export, as the zip of pages it offers for download, turned into the three
files that the page Describe the record takes at step 2: the dictionary of tables and columns with their descriptions,
the tables file with each table's description and primary key, and the list of the values that the specification
publishes for its category columns.

The specification is the vendor's and stays on the analyst's machine; this module ships no part of it and names no
table. It reads one fixed page layout: an index that links each table's page, and on each page the table's name,
its description, a Primary Key list, and a Column Information list in which each column has an ordinal, a name, a
type, whether it is discontinued, a description, whether it may hold organisation-specific values, and any published
category entries. A zip or an unpacked folder may be given.

    python -m schemalyser.vendorspec SPEC.zip|FOLDER --out FOLDER [--prefix NAME]

writes NAME-catalogue.csv, NAME-tables.csv and NAME-category-entries.csv (NAME defaults to the zip's stem), and
prints how many tables, columns and entries it found and how many pages it could not read.
"""
import argparse
import csv
import html
import io
import re
import sys
import zipfile
from pathlib import Path

from .catalogue import NAME

CATALOGUE_HEADINGS = ["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE", "IS_NULLABLE", "IS_PRIMARY_KEY",
                      "DISCONTINUED", "ORG_SPECIFIC", "DESCRIPTION"]
TABLES_HEADINGS = ["TABLE_NAME", "PRIMARY_KEY", "DESCRIPTION"]
ENTRIES_HEADINGS = ["TABLE_NAME", "COLUMN_NAME", "ENTRY"]

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_INDEX_LINK = re.compile(r'href="\./([A-Za-z0-9_]+)\.htm"')
_COLUMN_HEAD = re.compile(r'<td class="T1Head"[^>]*>\s*(\d+)\s*</td>\s*<td class="T1Head"[^>]*>\s*([A-Za-z0-9_]+)\s*</td>\s*'
                          r'<td class="T1Head"[^>]*>\s*([A-Za-z0-9_() ,]*?)\s*</td>\s*<td[^>]*>\s*(Yes|No)\s*</td>', re.S)
_ENTRY = re.compile(r'<td style="padding-left: 100px;[^"]*">(.*?)</td>', re.S)


def _text(fragment):
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _section(page, heading):
    """The HTML between a sub-heading and the next, or the empty text."""
    start = page.find(f"<td>{heading}</td>")
    if start < 0:
        return ""
    rest = page[start:]
    following = re.search(r'<table class="SubHeader3">', rest[1:])
    return rest[:following.start() + 1] if following else rest


def read_page(page):
    """One table's page as {"table", "description", "key": [columns], "columns": [{...}]}, or None where the page is
    not a table's page."""
    page = _SPACE.sub(" ", page)
    head = re.search(r'<table class="Header2"><tbody><tr><td>([A-Za-z0-9_]+)</td>', page)
    if not head or not NAME.match(head.group(1)):
        return None
    table = head.group(1)
    described = re.search(r'<td[^>]*class="T1Head">Description: </td>.*?<td class="T1Value" style="white-space: normal;">(.*?)</td>', page, re.S)
    description = _text(described.group(1)) if described else ""
    key = re.findall(r"<tr[^>]*>\s*<td>([A-Za-z0-9_]+)</td>\s*<td>(\d+)</td>", _section(page, "Primary Key"))
    key = [name for name, _ in sorted(key, key=lambda item: int(item[1]))]
    columns = []
    body = _section(page, "Column Information")
    heads = list(_COLUMN_HEAD.finditer(body))
    for at, found in enumerate(heads):
        ordinal, name, data_type, discontinued = found.groups()
        after = body[found.end():heads[at + 1].start() if at + 1 < len(heads) else len(body)]
        said = re.search(r'<td style="white-space: normal;">(.*?)</td>', after, re.S)
        org = re.search(r"May contain organization-specific values: (Yes|No)", after)
        entries = [_text(e) for e in _ENTRY.findall(after)] if "Category Entries:" in after else []
        columns.append({"ordinal": int(ordinal), "name": name, "type": data_type.strip(), "discontinued": discontinued,
                        "description": _text(said.group(1)) if said else "", "org_specific": org.group(1) if org else "",
                        "entries": [e for e in entries if e]})
    return {"table": table, "description": description, "key": key, "columns": columns}


def _pages(source):
    """(name, text) for every page of the zip or folder, the index first where it exists."""
    source = Path(source)
    if source.is_dir():
        for path in sorted(source.rglob("*.htm")):
            yield path.name, path.read_text(encoding="utf-8", errors="replace")
        return
    with zipfile.ZipFile(source) as held:
        for name in sorted(held.namelist()):
            if name.lower().endswith(".htm"):
                yield name.replace("\\", "/").rsplit("/", 1)[-1], held.read(name).decode("utf-8", errors="replace")


def convert(source):
    """Reads the specification and returns (catalogue rows, tables rows, entries rows, {"tables", "columns",
    "entries", "unread"}), each list of rows in the order of its headings."""
    catalogue, tables, entries = [], [], []
    unread = 0
    listed = None
    seen, without_columns, keys_without_columns = set(), [], []
    for name, text in _pages(source):
        if name == "_index.htm":
            listed = set(_INDEX_LINK.findall(text))
            continue
        found = read_page(text)
        if found is None:
            unread += 1
            continue
        if listed is not None and found["table"] not in listed:
            continue
        seen.add(found["table"])
        tables.append([found["table"], " ".join(found["key"]), found["description"]])
        keyed = {k.upper() for k in found["key"]}
        names = {c["name"].upper() for c in found["columns"]}
        if not found["columns"]:
            without_columns.append(found["table"])
        if keyed - names:
            keys_without_columns.append(found["table"])
        for column in found["columns"]:
            catalogue.append(["", found["table"], column["name"], column["ordinal"], column["type"], "",
                              "YES" if column["name"].upper() in keyed else "NO", column["discontinued"], column["org_specific"],
                              column["description"]])
            for entry in column["entries"]:
                entries.append([found["table"], column["name"], entry])
    # The checks of completeness: a table the index lists without a page read, a page read with no column, and a key
    # that names a column the page does not list. Each is a sign that the layout has changed or a page is damaged,
    # and each is reported by count, so that a short import is never taken for a whole one.
    missing_pages = sorted(listed - seen) if listed is not None else []
    return catalogue, tables, entries, {"tables": len(tables), "columns": len(catalogue), "entries": len(entries), "unread": unread,
                                        "missing_pages": missing_pages, "without_columns": without_columns,
                                        "keys_without_columns": keys_without_columns, "indexed": len(listed) if listed is not None else None}


def write(rows, headings, path):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headings)
    writer.writerows(rows)
    Path(path).write_text(out.getvalue(), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.vendorspec", description=__doc__.split("\n")[0])
    parser.add_argument("source", help="the specification as the zip the vendor offers for download, or that zip unpacked")
    parser.add_argument("--out", required=True, help="the folder to write the three files into")
    parser.add_argument("--prefix", help="the stem of the three files; the zip's own stem otherwise")
    args = parser.parse_args(argv)
    source = Path(args.source)
    if not source.exists():
        print(f"There is no file or folder at {source}.", file=sys.stderr)
        return 1
    prefix = args.prefix or source.stem
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    catalogue, tables, entries, counts = convert(source)
    if not tables:
        print("No table's page was found in the specification, so nothing was written.", file=sys.stderr)
        return 1
    write(catalogue, CATALOGUE_HEADINGS, out / f"{prefix}-catalogue.csv")
    write(tables, TABLES_HEADINGS, out / f"{prefix}-tables.csv")
    write(entries, ENTRIES_HEADINGS, out / f"{prefix}-category-entries.csv")
    print(f"Schemalyser read the specification: {counts['tables']:,} tables, {counts['columns']:,} columns and "
          f"{counts['entries']:,} published category entries, and {counts['unread']:,} pages that are not a table's. "
          f"The three files are in {out}, and the page takes the first two at step 2.")
    for line in warnings(counts):
        print(line, file=sys.stderr)
    return 0


def warnings(counts):
    """The sentences that say what the checks of completeness found, or none where the import is whole."""
    lines = []
    if counts.get("missing_pages"):
        lines.append(f"The index lists {len(counts['missing_pages']):,} tables whose pages were not read, so the dictionary lacks them; "
                     f"the download may be short or the layout changed. The first is {counts['missing_pages'][0]}.")
    if counts.get("without_columns"):
        lines.append(f"{len(counts['without_columns']):,} pages gave a table with no column, which the layout should never do; "
                     f"the first is {counts['without_columns'][0]}.")
    if counts.get("keys_without_columns"):
        lines.append(f"{len(counts['keys_without_columns']):,} tables name a key column that their page does not list; "
                     f"the first is {counts['keys_without_columns'][0]}.")
    return lines


if __name__ == "__main__":
    sys.exit(main())
