"""A vendor's data dictionary, read from a plain table: what each table and column of the hospital's database holds.

The dictionary is the vendor's own description of its tables and columns, which a hospital's colleague exports as a
CSV or a tab-separated file with a row of headings. It is licensed, so it is read in the browser or on the hospital's
own machine only, it is never sent anywhere, and no model sees it. This module reads it, checks it as the catalogue is
checked, and holds its descriptions as text for the proposer to compare with the role model.

What the module may write. A description is held in memory and given back only through Dictionary.description, for
the proposer's evidence in a draft map, which is the hospital's own and is kept with the dictionary in a private
folder. No error, no summary and no representation of a Dictionary holds a description: they give names, counts and
row numbers only.

The headings. Each of the five fields is found under any of the headings below, compared without regard to case,
spaces, hyphens or underscores, and a caller may name a heading of its own for any field. A table and a column are
required; a description, a data type and a key flag are each optional. A row with a table and no column describes the
table itself. A second file of tables, with a table, a description and a primary key that lists its columns, may give
the tables' own descriptions and keys.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from .catalogue import DATA_TYPE, NAME
from .extract import decode

HEADINGS = {
    "table": ("TABLE_NAME", "TABLE", "TABLENAME", "TABLE_NM", "TAB_NAME", "ENTITY", "ENTITY_NAME", "OBJECT_NAME"),
    "column": ("COLUMN_NAME", "COLUMN", "COLUMNNAME", "COLUMN_NM", "COL_NAME", "FIELD", "FIELD_NAME", "ATTRIBUTE",
               "ATTRIBUTE_NAME", "ELEMENT_NAME"),
    "description": ("DESCRIPTION", "COLUMN_DESCRIPTION", "FIELD_DESCRIPTION", "TABLE_DESCRIPTION", "DESC", "DEFINITION",
                    "BUSINESS_DEFINITION", "COMMENT", "COMMENTS", "REMARKS", "NOTES"),
    "data_type": ("DATA_TYPE", "DATATYPE", "TYPE", "COLUMN_TYPE", "SQL_TYPE", "TYPE_NAME", "FORMAT"),
    "key": ("IS_PRIMARY_KEY", "PRIMARY_KEY", "PRIMARY_KEY_FLAG", "IS_PK", "PK", "PK_FLAG", "KEY", "IS_KEY", "KEY_FLAG",
            "KEY_COLUMNS"),
}
REQUIRED = ("table", "column")
TRUE = {"Y", "YES", "TRUE", "T", "1", "X", "PK", "PRIMARY", "PRIMARY KEY"}
# The hard limits: a file larger than this is refused, a dictionary with more rows than this is refused, and a longer
# description is cut to this many characters.
MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 500_000
MAX_DESCRIPTION = 4000
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

WORDING = {
    "too_large": "The dictionary file holds {size} bytes, and Schemalyser reads at most {limit}, so the file has not been read.",
    "too_many": "The dictionary holds more than {limit} rows, so Schemalyser has not read it.",
    "headings": "Schemalyser could not find a heading for the {fields} in the dictionary's first row. Please name the heading with --heading {example}=HEADING.",
    "own_heading": "The dictionary has no heading {heading}, which was named for the {field}.",
    "empty": "The dictionary holds no table and column that Schemalyser could read.",
    "unknown_field": "{field} is not a field of a dictionary; the fields are table, column, description, data_type and key.",
}


class DictionaryError(ValueError):
    """The dictionary file is too large, or Schemalyser cannot find its headings. The message never holds a description."""


def _heading(text):
    return re.sub(r"[^A-Z0-9]+", "_", (text or "").upper()).strip("_")


def _clean(text):
    text = CONTROL.sub(" ", text or "")
    text = " ".join(text.split())
    return text[:MAX_DESCRIPTION]


def _rows(text):
    """The rows of a CSV or tab-separated text, with the delimiter judged from its first line."""
    first = text.split("\n", 1)[0]
    delimiter = max(("\t", ",", ";", "|"), key=first.count)
    if not first.count(delimiter):
        delimiter = ","
    return csv.reader(io.StringIO(text), delimiter=delimiter)


def _find(headings, field, own):
    """The position of a field's heading, by the caller's own heading where one is named, or by the known ones."""
    plain = [_heading(h) for h in headings]
    if own.get(field):
        wanted = _heading(own[field])
        if wanted not in plain:
            raise DictionaryError(WORDING["own_heading"].format(heading=own[field], field=field.replace("_", " ")))
        return plain.index(wanted)
    for name in HEADINGS[field]:
        if name in plain:
            return plain.index(name)
    return None


@dataclass(frozen=True)
class Entry:
    """One column of the dictionary. The description is held privately and given back only by Dictionary.description."""
    table: str
    name: str
    data_type: str = ""
    key: bool | None = None
    position: int = 0
    _description: str = field(default="", repr=False, compare=False)


@dataclass
class DictTable:
    name: str
    columns: dict = field(default_factory=dict)
    key: tuple = ()
    _description: str = field(default="", repr=False)

    def column(self, name):
        return self.columns.get(name.upper())

    def primary_key(self):
        """The table's primary key as a tuple of column names: from the tables' file where it gives one, and otherwise
        from the columns that the dictionary flags as keys."""
        if self.key:
            return self.key
        return tuple(c.name for c in self.columns.values() if c.key)


class Dictionary:
    """A data dictionary, read and checked. Names are looked up without regard to case."""

    def __init__(self, tables, skipped=0, cut=0):
        self._tables = tables
        self.skipped = skipped      # rows left out because a name is not a plain name
        self.cut = cut              # descriptions cut to MAX_DESCRIPTION characters

    def __repr__(self):
        return f"Dictionary({len(self._tables)} tables, {self.column_count()} columns)"

    def table(self, name):
        return self._tables.get(name.upper())

    def tables(self):
        return self._tables.values()

    def column_count(self):
        return sum(len(t.columns) for t in self._tables.values())

    def description(self, table, column=None):
        """The vendor's description of a table, or of one of its columns, as text, or the empty text where there is none."""
        found = self.table(table)
        if found is None:
            return ""
        if column is None:
            return found._description
        entry = found.column(column)
        return entry._description if entry is not None else ""

    def restricted_to(self, catalogue):
        """A dictionary of only the tables and columns that the catalogue holds, in the catalogue's spelling, with the
        number of the dictionary's columns that the catalogue does not hold. The dictionary describes the vendor's
        whole product, and the catalogue says what this hospital's database actually holds."""
        kept, missing = {}, 0
        for table in self._tables.values():
            known = catalogue.table(table.name)
            if known is None:
                missing += len(table.columns)
                continue
            columns = {}
            for entry in table.columns.values():
                column = known.column(entry.name)
                if column is None:
                    missing += 1
                    continue
                columns[column.name.upper()] = Entry(known.name, column.name, entry.data_type or column.data_type,
                                                     entry.key, entry.position, entry._description)
            key = tuple(known.column(k).name for k in table.key if known.column(k) is not None)
            kept[known.name.upper()] = DictTable(known.name, columns, key if len(key) == len(table.key) else (), table._description)
        restricted = Dictionary(kept, self.skipped, self.cut)
        restricted.missing = missing
        return restricted


def _read_text(source):
    if isinstance(source, bytes):
        data = source
    elif isinstance(source, Path) or ("\n" not in source and Path(source).is_file()):
        path = Path(source)
        if path.stat().st_size > MAX_BYTES:
            raise DictionaryError(WORDING["too_large"].format(size=path.stat().st_size, limit=MAX_BYTES))
        data = path.read_bytes()
    else:
        data = source.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise DictionaryError(WORDING["too_large"].format(size=len(data), limit=MAX_BYTES))
    return decode(data)


def _flag(text):
    text = (text or "").strip().upper()
    if not text:
        return None
    return text in TRUE


def load(source, tables=None, headings=None):
    """Reads a dictionary. source is a path, the bytes of a file or its text; tables, when given, is a second file of
    the tables' own descriptions and keys; headings, when given, is {field: heading} for any heading that the file
    names in its own way. Returns a Dictionary, and raises DictionaryError when the file is too large, too long or has
    no heading for a table or a column."""
    own = dict(headings or {})
    for name in own:
        if name not in HEADINGS:
            raise DictionaryError(WORDING["unknown_field"].format(field=name))
    rows = _rows(_read_text(source))
    first = next(rows, None) or []
    where = {name: _find(first, name, own) for name in HEADINGS}
    lacking = [name for name in REQUIRED if where[name] is None]
    if lacking:
        raise DictionaryError(WORDING["headings"].format(fields=" and the ".join(lacking), example=lacking[0]))
    found, skipped, cut, count = {}, 0, 0, 0
    for row in rows:
        count += 1
        if count > MAX_ROWS:
            raise DictionaryError(WORDING["too_many"].format(limit=MAX_ROWS))
        cell = lambda name: (row[where[name]] if where[name] is not None and where[name] < len(row) else "").strip()  # noqa: E731
        table, column = cell("table"), cell("column")
        if not table and not column:
            continue
        if not NAME.match(table) or (column and not NAME.match(column)):
            skipped += 1
            continue
        raw = cell("description")
        description = _clean(raw)
        cut += len(" ".join(raw.split())) > MAX_DESCRIPTION
        held = found.setdefault(table.upper(), DictTable(table))
        if not column:
            held._description = description
            continue
        data_type = cell("data_type")
        held.columns[column.upper()] = Entry(held.name, column, data_type if DATA_TYPE.match(data_type) else "",
                                             _flag(cell("key")), len(held.columns) + 1, description)
    if tables is not None:
        _load_tables(tables, found, own)
    if not any(t.columns for t in found.values()):
        raise DictionaryError(WORDING["empty"])
    return Dictionary(found, skipped, cut)


def _load_tables(source, found, own):
    """Adds the tables' own descriptions and primary keys from a second file of tables."""
    rows = _rows(_read_text(source))
    first = next(rows, None) or []
    at_table = _find(first, "table", {k: v for k, v in own.items() if k == "table"})
    at_description = _find(first, "description", {})
    at_key = _find(first, "key", {})
    if at_table is None:
        raise DictionaryError(WORDING["headings"].format(fields="table", example="table"))
    for count, row in enumerate(rows, 1):
        if count > MAX_ROWS:
            raise DictionaryError(WORDING["too_many"].format(limit=MAX_ROWS))
        cell = lambda at: (row[at] if at is not None and at < len(row) else "").strip()  # noqa: E731
        name = cell(at_table)
        if not NAME.match(name):
            continue
        held = found.get(name.upper())
        if held is None:
            continue
        if at_description is not None:
            held._description = _clean(cell(at_description))
        key = [part for part in re.split(r"[\s,;]+", cell(at_key)) if part]
        if key and all(NAME.match(part) and held.column(part) is not None for part in key):
            held.key = tuple(held.column(part).name for part in key)
