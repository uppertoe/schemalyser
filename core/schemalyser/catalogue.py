"""The catalogue: the tables and columns that exist, and the allowlist for names."""
import csv
import io
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from sqlglot.schema import MappingSchema

REQUIRED_COLUMNS = ("TABLE_NAME", "COLUMN_NAME", "DATA_TYPE")
# The order of the columns in the catalogue query. SQL Server Management Studio saves results
# without headers unless an option is turned on, so a file in this order is accepted without them.
QUERY_ORDER = ("TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE",
               "CHARACTER_MAXIMUM_LENGTH", "NUMERIC_PRECISION", "NUMERIC_SCALE", "IS_NULLABLE")
REQUIRED_POSITIONS = 5
# Names are written into the check script and into the sandbox's statements, so only plain names
# are accepted. A row with any other name is left out of the catalogue.
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@ ]{0,127}$")
DATA_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ()]{0,63}$")


class CatalogueError(ValueError):
    """The catalogue file does not have the expected layout."""


def _whole(text):
    text = (text or "").strip()
    return int(text) if text.lstrip("-").isdecimal() else None


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    data_type: str
    max_length: int | None = None
    scale: int | None = None
    position: int | None = None      # the ordinal position in its table, where the catalogue gives it
    nullable: bool | None = None     # whether the column may be empty, where the catalogue says so


@dataclass
class Table:
    name: str
    columns: dict = field(default_factory=dict)
    schema: str = ""

    def column(self, name):
        return self.columns.get(name.upper())

    def first_column(self):
        """The column at the lowest ordinal position, or the first one listed where no positions are given."""
        if not self.columns:
            return None
        listed = list(self.columns.values())
        placed = [c for c in listed if c.position is not None]
        return min(placed, key=lambda c: c.position) if placed else listed[0]


class Catalogue:
    """Looks names up without regard to case and answers with the catalogue's own spelling."""

    def __init__(self, tables):
        self._tables = tables
        self._schemas = {}
        self._names = None
        self.assumed_headers = False

    @classmethod
    def from_csv(cls, text):
        tables, clashes = {}, set()
        assumed_headers = False
        reader = csv.DictReader(io.StringIO(text))
        if not set(REQUIRED_COLUMNS) <= set(reader.fieldnames or ()):
            first = reader.fieldnames or ()
            if len(first) < REQUIRED_POSITIONS or not first[3].strip().isdecimal():
                raise CatalogueError("The catalogue file needs the columns " + ", ".join(REQUIRED_COLUMNS))
            names = list(QUERY_ORDER[:len(first)]) + [f"_{i}" for i in range(len(first) - len(QUERY_ORDER))]
            reader = csv.DictReader(io.StringIO(text), fieldnames=names)
            assumed_headers = True
        for row in reader:
            cell = lambda key: (row.get(key) or "").strip()  # noqa: E731
            name, column, schema, data_type = cell("TABLE_NAME"), cell("COLUMN_NAME"), cell("TABLE_SCHEMA"), cell("DATA_TYPE")
            if not NAME.match(name) or not NAME.match(column) or (schema and not NAME.match(schema)):
                continue
            if not DATA_TYPE.match(data_type):
                data_type = ""
            table = tables.setdefault(name.upper(), Table(name, schema=schema))
            if table.schema.upper() != schema.upper():
                # The same table name in two schemas cannot be told apart in a request, so neither is used.
                clashes.add(name.upper())
                continue
            nullable = {"YES": True, "NO": False}.get(cell("IS_NULLABLE").upper())
            table.columns[column.upper()] = Column(table.name, column, data_type,
                                                   _whole(cell("CHARACTER_MAXIMUM_LENGTH")), _whole(cell("NUMERIC_SCALE")),
                                                   _whole(cell("ORDINAL_POSITION")), nullable)
        for name in clashes:
            tables.pop(name, None)
        catalogue = cls(tables)
        catalogue.assumed_headers = assumed_headers
        return catalogue

    def table(self, name):
        return self._tables.get(name.upper())

    def tables(self):
        return self._tables.values()

    def names(self):
        """Every table and column name, upper-cased, for checking what the tool writes."""
        if self._names is None:
            names = set(self._tables)
            for table in self._tables.values():
                names.update(table.columns)
            self._names = frozenset(names)
        return self._names

    def data_types(self):
        return {c.data_type for t in self._tables.values() for c in t.columns.values()}

    def without(self, patterns):
        """A catalogue with the tables matching any SQL LIKE pattern held back."""
        globs = [p.upper().replace("%", "*").replace("_", "?") for p in patterns]
        held_back = {n for n in self._tables if any(fnmatchcase(n, g) for g in globs)}
        catalogue = Catalogue({n: t for n, t in self._tables.items() if n not in held_back})
        catalogue.assumed_headers = self.assumed_headers
        return catalogue, held_back

    def sqlglot_schema(self, dialect="tsql"):
        # Built once and reused: rebuilding it for every request is slow at full scale.
        if dialect not in self._schemas:
            mapping = {t.name: {c.name: "varchar" for c in t.columns.values()} for t in self._tables.values()}
            self._schemas[dialect] = MappingSchema(mapping, dialect=dialect)
        return self._schemas[dialect]
