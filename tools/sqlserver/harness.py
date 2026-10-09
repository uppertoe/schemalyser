"""Runs a conversion and its check script on a real SQL Server, and compares what it gives with DuckDB.

Everything else in Schemalyser runs T-SQL by translating it for DuckDB. This harness runs the same
T-SQL on the real engine, over the same synthetic rows, and reports every place where the two
disagree. It needs the container that README.md in this folder describes.

    cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/sqlserver/harness.py

With no world named, the harness uses the invented world in fixtures/. A world is a folder in the
layout that schemalyser.harness reads. The harness then:

 1. builds the sandbox and runs the conversion in DuckDB, as schemalyser.convert does;
 2. creates clarity_shadow on SQL Server, holding every sandbox table with the catalogue's types and rows,
    or restores the copy that an earlier run over the same rows kept;
 3. creates omop_shadow, holding the tables of CDM 5.4, with the mapping rows that DuckDB held;
 4. runs each step of the core layer, as written, inserting its rows into omop_shadow.dbo;
 5. writes the release script for the anaesthesia layer, carrying every mapping row that DuckDB
    held, the derived ones included, and runs it with sqlcmd, giving each variable with -v, and
    compares each of the conversion's counts that the script reports with DuckDB's;
 6. compares every OMOP table with DuckDB's, by row count and by content, the core's rows below
    the identifier offset and the anaesthesia layer's above it;
 7. tries the release script's safeguards on the real engine: a second run gives the same rows,
    a missing variable or two equal schemas stop it before any change, a core identifier at the
    offset stops it, a change of identifier type reaches the existing tables, and a failed gate
    leaves the previous rows (on_failure keep) or empty tables (on_failure empty);
 8. runs each target query against the published schema, with any hand-written source query given
    with --source-query against clarity_shadow, and compares every answer with DuckDB's;
 9. runs the check script against clarity_shadow and compares its rows with DuckDB's answers;
10. prints a short report, writes summary.json with the same findings for a machine, and exits with 1
    if anything differs.

The conversion's planted scenarios are planted on both engines. DuckDB plants them as
schemalyser.convert does; clarity_shadow is loaded with the sandbox's rows as they were before any
was planted, and each scenario's rows.sql then runs on SQL Server, as written. Once the release
script has run, each expectation is evaluated on the published schema and compared with DuckDB's
answer. --scenarios names the scenarios to plant, and --no-scenarios plants none.

The custom tables that the derived layer writes are compared as the anaesthesia layer's own tables
are, in the anaesthesia schema and through their published views.

A date that either engine takes from the clock, such as the release date in CDM_SOURCE, is
compared as "today", because the container keeps UTC and DuckDB keeps the local time zone.

The password for the sa login is read from reference/mssql-password.txt, or from the file that
SCHEMALYSER_MSSQL_PASSWORD_FILE names. It is passed to sqlcmd through the environment and never printed.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "fixtures"))

from schemalyser import convert, harness, release, sample_vocabulary  # noqa: E402
from schemalyser.catalogue import QUERY_ORDER  # noqa: E402
from schemalyser.sandbox import LAYOUT, Checks, ChecksError  # noqa: E402
from schemalyser.extract import decode  # noqa: E402

CONTAINER = "schemalyser-mssql"
SOURCE_DATABASE = "clarity_shadow"
OMOP_DATABASE = "omop_shadow"
SETTINGS = dict(release.SETTINGS, omop_schema="dbo", anaesthesia_schema="anaes_cdm", published_schema="anaes_pub",
                source_prefix=f"{SOURCE_DATABASE}.dbo.")
# The stand-in core types its identifiers as INT, as many cores do, while the anaesthesia layer keeps BIGINT.
CORE_IDENTIFIER_TYPE = "INT"
TODAY = set()   # the dates that "today" means on either engine, filled in by main
PASSWORD_FILE = ROOT / "reference" / "mssql-password.txt"
# Where sqlcmd is found: the ODBC tools that the SQL Server 2022 image ships, or older or Go builds.
SQLCMD_PATHS = ("/opt/mssql-tools18/bin/sqlcmd", "/opt/mssql-tools/bin/sqlcmd", "/opt/go-sqlcmd/sqlcmd")
# Each run keeps its scripts in a folder of its own in the container, so that another tool that uses
# /tmp/schemalyser, and removes it when it finishes, cannot take a run's scripts away while it works.
WORK = f"/tmp/schemalyser-harness-{os.getpid()}"
# A loaded source database is kept as a backup beside SQL Server's own files, under a name that holds a
# fingerprint of the rows, so that a later run over the same rows restores it and does not load it again.
KEPT = "/var/opt/mssql/data/schemalyser-kept-"
KEPT_COPIES = 4
# The fingerprint also holds this number, which changes whenever the way the rows are loaded changes.
LOADER = "2"
# A bulk file separates its fields and its rows with characters that no synthetic value holds.
BULK_FIELD, BULK_ROW = "\x1f", "\x1e\n"
CHARACTER_TYPES = ("varchar", "nvarchar", "char", "nchar")
NULL_MARK, SEPARATOR, CR_MARK, LF_MARK = "\x02", "\x1f", "\x03", "\x04"
SAMPLE = 3


def _bracket(name):
    return "[" + name.replace("]", "]]") + "]"


def _text(value):
    return "N'" + str(value).replace("'", "''") + "'"


class SqlError(RuntimeError):
    """sqlcmd reported an error."""


class Server:
    """Talks to SQL Server through sqlcmd inside the container."""

    def __init__(self, container, password, local=None):
        self.container = container
        self.env = dict(os.environ, SQLCMDPASSWORD=password, GODEBUG="x509negativeserial=1")
        self.local = Path(local) if local else Path(tempfile.mkdtemp(prefix="schemalyser-mssql-"))
        self.local.mkdir(parents=True, exist_ok=True)
        self.files = 0
        self._docker("exec", self.container, "mkdir", "-p", WORK)
        self.sqlcmd = next((path for path in SQLCMD_PATHS
                            if self._docker("exec", self.container, "test", "-x", path, check=False).returncode == 0), None)
        if self.sqlcmd is None:
            raise SystemExit(f"No sqlcmd was found in the container {container}. README.md says how to add one.")

    def _docker(self, *args, check=True):
        result = subprocess.run(["docker", *args], env=self.env, capture_output=True, text=True, encoding="utf-8")
        if check and result.returncode:
            raise SystemExit(f"docker {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
        return result

    def run(self, sql, database="master", variables=None, separator=None, trim=False, name=None):
        """Runs a script with sqlcmd. Returns its standard output, and raises SqlError if sqlcmd reports an error.

        variables, when given, are passed with -v, as an operator passes them to the release script.
        Without them, sqlcmd is told not to substitute variables at all.
        """
        self.files += 1
        name = name or f"script_{self.files:04d}.sql"
        (self.local / name).write_text(sql, encoding="utf-8")
        self._docker("cp", str(self.local / name), f"{self.container}:{WORK}/{name}")
        command = ["exec", "-e", "SQLCMDPASSWORD", "-e", "GODEBUG", self.container, self.sqlcmd,
                   "-S", "localhost", "-U", "sa", "-C", "-b", "-d", database, "-i", f"{WORK}/{name}"]
        if variables is None:
            command.append("-x")
        else:
            for key, value in variables.items():
                command += ["-v", f"{key}={value}"]
        if separator:
            command += ["-s", separator]
        # The ODBC sqlcmd refuses -h with -y 0, and -y 0 prints no header of its own and keeps trailing spaces.
        command += ["-h", "-1", "-W"] if trim else ["-y", "0"]
        result = self._docker(*command, check=False)
        if result.returncode:
            raise SqlError(_first_error(result.stdout + "\n" + result.stderr))
        return result.stdout

    def bulk(self, table, text, database):
        """Loads a table from a bulk file, which is far quicker than INSERT statements over wide tables."""
        self.files += 1
        name = f"bulk_{self.files:04d}.dat"
        local = self.local / name
        local.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
        try:
            self._docker("cp", str(local), f"{self.container}:{WORK}/{name}")
            # With MAXERRORS = 0, one row that cannot be read stops the load, so that no row is dropped quietly.
            self.run(f"BULK INSERT [dbo].{_bracket(table)} FROM '{WORK}/{name}' WITH (DATAFILETYPE = 'widechar', "
                     f"FIELDTERMINATOR = '0x1f00', ROWTERMINATOR = '0x1e000a00', KEEPNULLS, TABLOCK, MAXERRORS = 0);",
                     database, name=f"bulk_{self.files:04d}.sql")
        finally:
            local.unlink(missing_ok=True)
            self._docker("exec", self.container, "rm", "-f", f"{WORK}/{name}", check=False)

    def restore_source(self, key):
        """Restores the source database that an earlier run kept under this fingerprint.

        Returns what that run found when it loaded the rows, or None when there is nothing to restore.
        """
        note = self._docker("exec", self.container, "cat", f"{KEPT}{key}.json", check=False)
        if note.returncode:
            return None
        try:
            kept = json.loads(note.stdout)
            self.run(f"IF DB_ID('{SOURCE_DATABASE}') IS NOT NULL BEGIN ALTER DATABASE [{SOURCE_DATABASE}] SET SINGLE_USER "
                     f"WITH ROLLBACK IMMEDIATE; DROP DATABASE [{SOURCE_DATABASE}]; END;\n"
                     f"RESTORE DATABASE [{SOURCE_DATABASE}] FROM DISK = N'{KEPT}{key}.bak' WITH REPLACE;")
        except (ValueError, SqlError):
            return None
        return kept["findings"], kept["loaded"]

    def keep_source(self, key, findings, loaded):
        """Keeps the source database as it stands, before any scenario is planted, for later runs to restore."""
        try:
            self.run(f"BACKUP DATABASE [{SOURCE_DATABASE}] TO DISK = N'{KEPT}{key}.bak' WITH INIT, COMPRESSION;")
        except SqlError:
            return False
        note = self.local / f"kept-{key}.json"
        note.write_text(json.dumps({"findings": findings, "loaded": loaded}), encoding="utf-8")
        self._docker("cp", str(note), f"{self.container}:{KEPT}{key}.json", check=False)
        note.unlink(missing_ok=True)
        # Only the newest copies are kept, so that they do not fill the container's disk.
        self._docker("exec", "-u", "0", self.container, "sh", "-c",
                     f"ls -t {KEPT}*.bak 2>/dev/null | tail -n +{KEPT_COPIES + 1} | "
                     'while read f; do rm -f "$f" "${f%.bak}.json"; done', check=False)
        return True

    def clean(self):
        """Removes the scripts that were copied into the container."""
        self._docker("exec", self.container, "rm", "-rf", WORK, check=False)

    def scalar(self, sql, database="master"):
        lines = [line for line in self.run("SET NOCOUNT ON;\n" + sql, database).splitlines() if line.strip()]
        return lines[-1].strip() if lines else None

    def rows(self, query, columns, database):
        """The rows of a query, each value as text or None. The query's columns are named with their types."""
        parts = []
        for name, kind in columns:
            column = _bracket(name)
            if kind in ("datetime", "datetime2", "smalldatetime", "date", "time", "datetimeoffset"):
                value = f"CONVERT(varchar(40), {column}, 126)"
            elif kind in ("float", "real"):
                value = f"CONVERT(varchar(40), {column}, 3)"
            else:
                value = (f"REPLACE(REPLACE(CONVERT(nvarchar(max), {column}), NCHAR(13), NCHAR(3)), "
                         f"NCHAR(10), NCHAR(4))")
            parts.append(f"COALESCE({value}, NCHAR(2))")
        joined = ", NCHAR(31), ".join(parts) + (", N''" if len(parts) == 1 else "")
        text = self.run(f"SET NOCOUNT ON;\nSELECT CONCAT({joined}) FROM ({query}) AS q;", database)
        found = []
        for line in text.split("\n"):
            if not line:
                continue
            values = line.split(SEPARATOR)
            if len(values) != len(columns):
                raise SqlError(f"a row came back with {len(values)} values where {len(columns)} were expected")
            found.append(tuple(None if value == NULL_MARK else value.replace(CR_MARK, "\r").replace(LF_MARK, "\n")
                               for value in values))
        return found


def _first_error(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.startswith("Msg ") or "error" in line.lower() or "not defined" in line:
            return " ".join(lines[index:index + 2])
    return " ".join(lines[-2:]) or "sqlcmd failed without a message"


# The source database.

def _catalogue_rows(text):
    """The rows of a catalogue file, keyed by (table, column) in capitals, with or without a header."""
    reader = csv.DictReader(io.StringIO(text))
    if "TABLE_NAME" not in (reader.fieldnames or ()):
        reader = csv.DictReader(io.StringIO(text), fieldnames=list(QUERY_ORDER))
    return {((row.get("TABLE_NAME") or "").strip().upper(), (row.get("COLUMN_NAME") or "").strip().upper()): row
            for row in reader}


def _whole(text):
    text = (text or "").strip()
    return int(text) if text.lstrip("-").isdecimal() else None


def sql_server_type(row):
    """The SQL Server type of a catalogue column, as a column definition would write it."""
    kind = (row.get("DATA_TYPE") or "varchar").strip().lower()
    if kind in ("varchar", "nvarchar", "char", "nchar", "varbinary", "binary"):
        length = _whole(row.get("CHARACTER_MAXIMUM_LENGTH"))
        return f"{kind}({'max' if length in (None, -1) else length})"
    if kind in ("numeric", "decimal"):
        return f"{kind}({_whole(row.get('NUMERIC_PRECISION')) or 18},{_whole(row.get('NUMERIC_SCALE')) or 0})"
    return kind


INTEGER_RANGES = {"tinyint": (0, 255), "smallint": (-2 ** 15, 2 ** 15 - 1), "int": (-2 ** 31, 2 ** 31 - 1),
                  "bigint": (-2 ** 63, 2 ** 63 - 1), "bit": (0, 1)}


def fits(value, kind):
    """Whether a sandbox value can be held by a column of the given SQL Server type."""
    if value is None:
        return True
    base = kind.split("(")[0]
    if base in ("varchar", "nvarchar", "char", "nchar"):
        size = kind[len(base) + 1:-1]
        return size == "max" or len(_plain(value)) <= int(size)
    if base in INTEGER_RANGES:
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return False
        low, high = INTEGER_RANGES[base]
        return number == number.to_integral_value() and low <= number <= high
    if base in ("numeric", "decimal"):
        precision, scale = (int(part) for part in kind[len(base) + 1:-1].split(","))
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return False
        return number.is_finite() and abs(number) < Decimal(10) ** (precision - scale)
    if base in ("datetime", "smalldatetime", "datetime2", "date"):
        return isinstance(value, (dt.datetime, dt.date)) and value.year >= (1753 if base == "datetime" else 1)
    if base in ("float", "real"):
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
    return True


def _plain(value):
    return value if isinstance(value, str) else str(value)


def literal(value, kind):
    """A sandbox value written as a T-SQL literal for a column of the given type."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, dt.datetime):
        # The full precision goes in as datetime2, and SQL Server rounds it to the column's own precision.
        return f"CAST('{value.isoformat(timespec='microseconds')}' AS datetime2(7))"
    if isinstance(value, dt.date):
        return f"'{value.isoformat()}'"
    if isinstance(value, dt.time):
        return f"'{value.isoformat()}'"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return _text(value)


def bulk_text(value, kind):
    """A sandbox value as a field of a bulk file, or None where only an INSERT statement loads it faithfully.

    An empty field is a missing value, and a field that holds the character 0 is an empty text.
    """
    if value is None:
        return ""
    base = kind.split("(")[0]
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, dt.datetime):
        if base == "datetime2":
            return value.isoformat(sep=" ", timespec="microseconds")
        if base == "datetime" and value.microsecond % 1000 == 0:
            return value.isoformat(sep=" ", timespec="milliseconds")
        return None
    if isinstance(value, dt.date):
        return value.isoformat() if base in ("date", "datetime", "datetime2", "smalldatetime") else None
    if isinstance(value, dt.time):
        return value.isoformat() if base == "time" else None
    if isinstance(value, int):
        return str(value) if base in INTEGER_RANGES or base in CHARACTER_TYPES or base in ("numeric", "decimal", "float", "real") else None
    if isinstance(value, Decimal):
        return str(value) if base in ("numeric", "decimal", "float", "real") and value.is_finite() else None
    if isinstance(value, float):
        return repr(value) if base in ("float", "real", "numeric", "decimal") and value == value and abs(value) != float("inf") else None
    if isinstance(value, str) and base in CHARACTER_TYPES:
        if "\x00" in value or "\x1f" in value or "\x1e" in value:
            return None
        return value or "\x00"
    return None


def bulk_file(data, kinds):
    """The text of a bulk file for a table's rows, or None where a value needs an INSERT statement."""
    lines = []
    for row in data:
        fields = [bulk_text(value, kind) for value, kind in zip(row, kinds)]
        if None in fields:
            return None
        lines.append(BULK_FIELD.join(fields))
    try:
        text = BULK_ROW.join(lines) + BULK_ROW
        text.encode("utf-16-le")
    except UnicodeEncodeError:
        return None
    return text


def _kept_rows(table, planted_from):
    before = (planted_from or {}).get(table.upper())
    return f" WHERE rowid < {int(before[1])}" if before else ""


def source_key(con, catalogue_text, tables, planted_from=None):
    """A fingerprint of everything that decides what load_source writes: the catalogue, each table's
    columns and the rows that it loads. Two runs with the same fingerprint load the same database."""
    digest = hashlib.sha256((LOADER + "\n" + catalogue_text).encode("utf-8"))
    for table in tables:
        columns = con.execute(f'DESCRIBE "{table}"').fetchall()
        count, mixed = con.execute(f'SELECT COUNT(*), bit_xor(hash(t)) FROM (SELECT * FROM "{table}"'
                                   f'{_kept_rows(table, planted_from)}) AS t').fetchone()
        digest.update(repr((table, [(c[0], c[1]) for c in columns], count, mixed)).encode("utf-8"))
    return digest.hexdigest()[:24]


def load_source(server, con, catalogue_text, tables, planted_from=None):
    """Creates the source database with the catalogue's types, and loads the sandbox's rows into it.

    planted_from, when given, holds for each table that a planted scenario wrote the rows it held
    before the scenario was planted, so that only those rows are loaded and the scenario's own
    rows.sql can then be run on SQL Server.

    Returns the findings: each column whose sandbox values the catalogue's type cannot hold. Such a
    column is widened so that the run can go on, and the finding says so.
    """
    server.run(f"IF DB_ID('{SOURCE_DATABASE}') IS NOT NULL BEGIN ALTER DATABASE [{SOURCE_DATABASE}] SET SINGLE_USER "
               f"WITH ROLLBACK IMMEDIATE; DROP DATABASE [{SOURCE_DATABASE}]; END;\nCREATE DATABASE [{SOURCE_DATABASE}];")
    types = _catalogue_rows(catalogue_text)
    findings, loaded = [], 0
    for table in tables:
        names = [row[0] for row in con.execute(f'DESCRIBE "{table}"').fetchall()]
        kept = _kept_rows(table, planted_from)
        data = con.execute(f'SELECT * FROM "{table}"' + (kept + " ORDER BY rowid" if kept else "")).fetchall()
        kinds = []
        for position, name in enumerate(names):
            kind = sql_server_type(types.get((table.upper(), name.upper()), {}))
            misfits = [row[position] for row in data if not fits(row[position], kind)]
            if misfits:
                wider = _wider(kind, [row[position] for row in data])
                findings.append(f"{table}.{name}: {len(misfits)} of {len(data)} sandbox values do not fit the "
                                f"catalogue's {kind}, for example {misfits[0]!r}; the column was created as {wider}")
                kind = wider
            kinds.append(kind)
        definitions = ",\n".join(f"    {_bracket(name)} {kind} NULL" for name, kind in zip(names, kinds))
        statements = [f"CREATE TABLE [dbo].{_bracket(table)} (\n{definitions}\n);"]
        columns = ", ".join(_bracket(name) for name in names)
        text = bulk_file(data, kinds) if data else None
        if text is not None:
            # The quick way. If SQL Server cannot read the file, the table is made again and loaded with INSERTs.
            try:
                server.run("SET NOCOUNT ON;\n" + statements[0], SOURCE_DATABASE)
                server.bulk(table, text, SOURCE_DATABASE)
                if int(server.scalar(f"SELECT COUNT(*) FROM [dbo].{_bracket(table)};", SOURCE_DATABASE)) == len(data):
                    loaded += len(data)
                    continue
            except SqlError:
                pass
            statements.insert(0, f"DROP TABLE IF EXISTS [dbo].{_bracket(table)};")
        for start in range(0, len(data), 1000):
            values = ",\n".join("(" + ", ".join(literal(value, kind) for value, kind in zip(row, kinds)) + ")"
                                for row in data[start:start + 1000])
            statements.append(f"INSERT INTO [dbo].{_bracket(table)} ({columns}) VALUES\n{values};")
        server.run("SET NOCOUNT ON;\n" + "\nGO\n".join(statements) + "\n", SOURCE_DATABASE)
        loaded += len(data)
    return findings, loaded


def _wider(kind, values):
    base = kind.split("(")[0]
    if base in INTEGER_RANGES and all(fits(value, "bigint") for value in values):
        return "bigint"
    if base in ("numeric", "decimal") and all(fits(value, "numeric(38,10)") for value in values):
        return "numeric(38,10)"
    if base in ("nvarchar", "nchar"):
        return "nvarchar(max)"
    return "varchar(max)"


# The OMOP database.

def create_omop(server):
    """Creates omop_shadow with the CDM 5.4 tables in dbo, typed as the release script types its own."""
    server.run(f"IF DB_ID('{OMOP_DATABASE}') IS NOT NULL BEGIN ALTER DATABASE [{OMOP_DATABASE}] SET SINGLE_USER "
               f"WITH ROLLBACK IMMEDIATE; DROP DATABASE [{OMOP_DATABASE}]; END;\nCREATE DATABASE [{OMOP_DATABASE}];")
    statements = []
    for table, fields in release._fields().items():
        definitions = ",\n".join(
            f"    {_bracket(row['field'])} {_omop_type(row['datatype'])}{' NOT NULL' if row['required'] == 'Y' else ' NULL'}"
            for row in fields)
        statements.append(f"CREATE TABLE [dbo].{_bracket(table)} (\n{definitions}\n);")
    server.run("\nGO\n".join(statements) + "\n", OMOP_DATABASE)
    # The steps name the OMOP tables as omop.<table>. In the source database a synonym of that name
    # reaches each table in omop_shadow.dbo, so that a core step runs exactly as it is written.
    synonyms = [f"CREATE SYNONYM [omop].{_bracket(table)} FOR [{OMOP_DATABASE}].[dbo].{_bracket(table)};"
                for table in release._fields()]
    server.run("CREATE SCHEMA [omop];\nGO\n" + "\n".join(synonyms) + "\n", SOURCE_DATABASE)


def _omop_type(datatype):
    return CORE_IDENTIFIER_TYPE if datatype == "integer" else release.SQL_TYPES.get(datatype, datatype.upper())


def copy_table(server, con, table, schema="dbo"):
    """Copies the rows of one DuckDB OMOP table into omop_shadow. Returns the number of rows."""
    fields = release._fields()[table]
    names = [row["field"] for row in fields]
    data = con.execute(f'SELECT {", ".join(chr(34) + n + chr(34) for n in names)} FROM omop."{table}"').fetchall()
    if not data:
        return 0
    columns = ", ".join(_bracket(name) for name in names)
    statements = []
    for start in range(0, len(data), 1000):
        values = ",\n".join("(" + ", ".join(literal(value, None) for value in row) + ")" for row in data[start:start + 1000])
        statements.append(f"INSERT INTO [{schema}].{_bracket(table)} ({columns}) VALUES\n{values};")
    server.run("SET NOCOUNT ON;\n" + "\nGO\n".join(statements) + "\n", OMOP_DATABASE)
    return len(data)


def run_core(server, folder, steps):
    """Runs each core step as written, in the source database, inserting into omop_shadow.dbo."""
    results = []
    for step in steps:
        sql = decode((folder / step["file"]).read_bytes()).strip().rstrip(";")
        try:
            _, columns = release.rewrite(sql, [])
        except Exception as error:  # the step cannot be read, which is itself a finding
            results.append({"file": step["file"], "status": "unreadable", "message": str(error)})
            continue
        statement = (f"SET NOCOUNT ON;\nINSERT INTO [{OMOP_DATABASE}].[dbo].{_bracket(step['table'].lower())} "
                     f"({', '.join(_bracket(c) for c in columns)})\n{sql}\n;\nSELECT @@ROWCOUNT;")
        try:
            lines = [line for line in server.run(statement, SOURCE_DATABASE).splitlines() if line.strip()]
            results.append({"file": step["file"], "status": "ok", "rows": int(lines[-1])})
        except SqlError as error:
            results.append({"file": step["file"], "status": "error", "message": str(error)})
    return results


def variables(settings=None):
    """The -v values that an operator gives the release script, from the settings."""
    chosen = settings or SETTINGS
    return {name: chosen[key] for key, name in release.VARIABLES}


def duck_mappings(con):
    """Every mapping row that DuckDB held, the derived ones included, in the order of the table's fields."""
    fields = [row["field"] for row in release._fields()["source_to_concept_map"]]
    return [list(row) for row in con.execute(f'SELECT {", ".join(fields)} FROM omop.source_to_concept_map').fetchall()]


def run_release(server, folder, con, workspace, derived=True, settings=None, name="release.sql"):
    """Writes the release script with the mapping rows that DuckDB held, the derived ones included, and runs it.

    With derived turned off, the script carries only the folder's own mapping rows, as release.py writes it by default.
    """
    chosen = dict(SETTINGS, **(settings or {}))
    text = release.script(folder, chosen, duck_mappings(con) if derived else None)
    (workspace / name).write_text(text, encoding="utf-8")
    try:
        output = server.run(text, OMOP_DATABASE, variables=variables(chosen), name=name)
        return {"status": "committed", "output": output.strip()}
    except SqlError as error:
        return {"status": "error", "message": str(error)}


def run_gates(server, folder, written):
    """Counts the rows of each gate on SQL Server, reading the tables as the release script places them."""
    places = {"SCHEMALYSER_OMOP": "[dbo].", "SCHEMALYSER_PUBLISHED": f"[{SETTINGS['published_schema']}].",
              "SCHEMALYSER_OWN": f"[{SETTINGS['anaesthesia_schema']}].", "SCHEMALYSER_SOURCE": f"[{SOURCE_DATABASE}].[dbo]."}
    results = []
    for path in sorted((folder / "gates").glob("*.sql")):
        text, _ = release.rewrite(decode(path.read_bytes()), written, where=path.name)
        for placeholder, place in places.items():
            text = text.replace(f"[{placeholder}].", place)
        try:
            rows = int(server.scalar(f"SELECT COUNT(*) FROM (\n{text}\n) AS gate;", OMOP_DATABASE))
        except SqlError as error:
            rows = None
            print(f"gate {path.name} could not be run on SQL Server: {error}")
        results.append({"gate": path.name, "rows": rows})
    return results


# The release script's safeguards, tried on the real engine.

def all_fields(folder):
    """The field rows of every table of CDM 5.4 and of the folder's custom tables, as release._fields gives them."""
    fields = dict(release._fields())
    for row in convert.custom_rows(convert.read_tables(folder)):
        fields.setdefault(row["table"], []).append(row)
    return fields


FIELDS = {}     # filled in by main with all_fields for the conversion folder


def _layer_rows(server, written):
    """The anaesthesia layer's own rows, table by table, as text, so that two runs can be compared."""
    fields = FIELDS or release._fields()
    found = {}
    for table in written:
        columns = [(row["field"], _server_kind(row["datatype"])) for row in fields[table]]
        found[table] = sorted(server.rows(f"SELECT * FROM [{SETTINGS['anaesthesia_schema']}].{_bracket(table)}", columns, OMOP_DATABASE),
                              key=lambda row: [value or "" for value in row])
    return found


def _identifier_type(server, table):
    key = release._key(release._fields()[table])
    return server.scalar(f"SELECT TYPE_NAME(c.user_type_id) FROM sys.columns c WHERE c.object_id = "
                         f"OBJECT_ID(N'[{SETTINGS['anaesthesia_schema']}].{_bracket(table)}') AND c.name = N'{key}';", OMOP_DATABASE)


def try_safeguards(server, folder, con, workspace, written):
    """Runs the release script in the ways that should stop it or change it, and checks what each one left behind.

    Returns [(what was tried, whether it behaved as it should, a detail)]. The last run restores the normal rows.
    """
    results = []
    before = _layer_rows(server, written)
    first = written[0]

    again = run_release(server, folder, con, workspace, name="release_again.sql")
    same = again["status"] == "committed" and _layer_rows(server, written) == before
    results.append(("a second run writes the same rows with the same identifiers", same, again.get("message", "")))

    text = (workspace / "release.sql").read_text(encoding="utf-8")
    missing = dict(variables())
    missing.pop("SourcePrefix")
    try:
        server.run(text, OMOP_DATABASE, variables=missing, name="release_missing_variable.sql")
        results.append(("a missing -v variable stops sqlcmd", False, "the script ran to its end"))
    except SqlError as error:
        results.append(("a missing -v variable stops sqlcmd before any change",
                        "not defined" in str(error) and _layer_rows(server, written) == before, str(error)))

    for label, values in (("an anaesthesia schema equal to the core's", dict(variables(), AnaesSchemaName="dbo")),
                          ("a published schema equal to the anaesthesia schema", dict(variables(), AnaesPubSchemaName=SETTINGS["anaesthesia_schema"])),
                          ("a schema name holding a bracket", dict(variables(), AnaesPubSchemaName="anaes]pub")),
                          ("a source prefix holding SQL", dict(variables(), SourcePrefix="x;DROP TABLE dbo.person;--"))):
        try:
            server.run(text, OMOP_DATABASE, variables=values, name="release_bad_variable.sql")
            results.append((f"{label} stops the script", False, "the script ran to its end"))
        except SqlError as error:
            # The script's own guard must say why, and not a syntax error from the rest of the script.
            results.append((f"{label} stops the script before any change, with the guard's message",
                            _layer_rows(server, written) == before and "before changing anything" in str(error), str(error)))

    # The stand-in core keeps INT identifiers, which can never reach the default offset, so a lower offset is tried.
    offset = 1_000_000_000
    held = next((table for table in written if table in release._fields() and int(server.scalar(f"SELECT COUNT(*) FROM [dbo].{_bracket(table)};", OMOP_DATABASE))), None)
    core_key = release._key(release._fields()[held]) if held else None
    if held:
        server.run(f"SET NOCOUNT ON; SELECT TOP (1) * INTO #row FROM [dbo].{_bracket(held)};\n"
                   f"UPDATE #row SET {_bracket(core_key)} = {offset};\n"
                   f"INSERT INTO [dbo].{_bracket(held)} SELECT * FROM #row;", OMOP_DATABASE)
    guarded = run_release(server, folder, con, workspace, settings={"identifier_offset": offset}, name="release_core_identifier.sql")
    if held:
        server.run(f"DELETE FROM [dbo].{_bracket(held)} WHERE {_bracket(core_key)} = {offset};", OMOP_DATABASE)
    results.append(("a core identifier at the offset stops the script before any change",
                    guarded["status"] == "error" and "identifier" in guarded.get("message", "") and _layer_rows(server, written) == before,
                    guarded.get("message", "")))

    smaller = run_release(server, folder, con, workspace, settings={"identifier_type": "INT", "identifier_offset": 1_000_000_000},
                          name="release_int.sql")
    narrowed = _identifier_type(server, first)
    wider = run_release(server, folder, con, workspace, name="release_bigint.sql")
    results.append(("a change of identifier type reaches the tables of an earlier run",
                    smaller["status"] == "committed" and narrowed == "int" and wider["status"] == "committed"
                    and _identifier_type(server, first) == "bigint" and _layer_rows(server, written) == before,
                    f"{smaller.get('message', '')} {wider.get('message', '')}".strip()))

    failing = workspace / "failing-conversion"
    shutil.rmtree(failing, ignore_errors=True)
    shutil.copytree(folder, failing)
    (failing / "gates" / "999_always_fails.sql").write_text("SELECT 1 AS always\n", encoding="utf-8")
    kept = run_release(server, failing, con, workspace, name="release_failing_keep.sql")
    results.append(("a failed gate with on_failure keep leaves the previous rows",
                    kept["status"] == "error" and _layer_rows(server, written) == before, kept.get("message", "")))
    emptied = run_release(server, failing, con, workspace, settings={"on_failure": "empty"}, name="release_failing_empty.sql")
    counts = _layer_rows(server, written)
    results.append(("a failed gate with on_failure empty leaves the tables empty",
                    emptied["status"] == "error" and not any(counts.values()), emptied.get("message", "")))
    restored = run_release(server, folder, con, workspace, name="release_restore.sql")
    results.append(("a normal run afterwards restores the same rows",
                    restored["status"] == "committed" and _layer_rows(server, written) == before, restored.get("message", "")))
    shutil.rmtree(failing, ignore_errors=True)
    return results


# Comparing.

def normalise(value, kind):
    """A value in a form that ignores harmless differences: trailing zeros, float noise and sub-second precision."""
    if value is None:
        return None
    if kind == "integer":
        try:
            return int(Decimal(str(value)))
        except InvalidOperation:
            return str(value)
    if kind == "float":
        try:
            return format(float(value), ".10g")
        except ValueError:
            return str(value)
    if kind in ("date", "datetime"):
        moment = value if isinstance(value, (dt.datetime, dt.date)) else _moment(str(value))
        if moment is None:
            return str(value)
        if kind == "date":
            day = moment.date() if isinstance(moment, dt.datetime) else moment
            # A date taken from the clock is "today" on both engines, whichever time zone each keeps.
            return "today" if day in TODAY else day.isoformat()
        if not isinstance(moment, dt.datetime):
            moment = dt.datetime.combine(moment, dt.time())
        # DuckDB keeps microseconds and the CDM's DATETIME2(0) keeps whole seconds, so both are rounded to the second.
        return (moment + dt.timedelta(microseconds=500000)).replace(microsecond=0).isoformat(sep=" ")
    text = str(value).rstrip()
    if re.fullmatch(r"-?\d+\.\d+", text):
        text = text.rstrip("0").rstrip(".")
    return text


def _moment(text):
    try:
        return dt.datetime.fromisoformat(text.replace("T", " "))
    except ValueError:
        return None


def checksum(rows):
    """A SHA-256 of a table's normalised rows, in a fixed order, so that two engines' tables can be compared by one value."""
    ordered = sorted(rows.elements(), key=lambda row: json.dumps(row, default=str))
    return hashlib.sha256(json.dumps(ordered, default=str).encode("utf-8")).hexdigest()


def compare_rows(label, kinds, ours, theirs, key=0):
    """Compares two lists of rows as multisets. Returns a plain dictionary of what differs, with samples."""
    left = Counter(tuple(normalise(v, k) for v, k in zip(row, kinds)) for row in ours)
    right = Counter(tuple(normalise(v, k) for v, k in zip(row, kinds)) for row in theirs)
    only_duck, only_server = left - right, right - left
    result = {"object": label, "duckdb": len(ours), "sqlserver": len(theirs),
              "same": not only_duck and not only_server, "only_duckdb": sum(only_duck.values()),
              "only_sqlserver": sum(only_server.values()),
              "checksum_duckdb": checksum(left), "checksum_sqlserver": checksum(right)}
    if result["same"]:
        return result
    result["sample_duckdb"] = list(only_duck)[:SAMPLE]
    result["sample_sqlserver"] = list(only_server)[:SAMPLE]
    # Rows that share a primary key show which fields disagree.
    by_key = {row[key]: row for row in only_server}
    fields = Counter()
    for row in only_duck:
        other = by_key.get(row[key])
        if other is not None:
            fields.update(i for i, (a, b) in enumerate(zip(row, other)) if a != b)
    result["fields"] = fields
    # Whether the rows agree once the identifiers that a step numbers itself are set aside.
    strip = lambda counter: Counter({row[:key] + row[key + 1:]: n for row, n in counter.items()})  # noqa: E731
    result["same_without_key"] = not (strip(left) - strip(right)) and not (strip(right) - strip(left))
    return result


def compare_omop(server, con, steps, results):
    """Compares each OMOP table that either engine wrote, with each part the release keeps apart."""
    fields = FIELDS or release._fields()
    keys = {table: next((i for i, row in enumerate(rows) if row["primary_key"] == "Y"), 0) for table, rows in fields.items()}
    anaesthesia_tables = {step["table"].lower() for step in steps if step["layer"] == "anaesthesia"}
    derived_tables = {step["table"].lower() for step in steps if step["layer"] == "derived"}
    offset = SETTINGS["identifier_offset"]
    comparisons = []
    for table, rows in fields.items():
        names = [row["field"] for row in rows]
        kinds = [row["datatype"] if row["datatype"] in ("integer", "float", "date", "datetime") else "text" for row in rows]
        duck = con.execute(f'SELECT {", ".join(chr(34) + n + chr(34) for n in names)} FROM omop."{table}" '
                           f'ORDER BY "{names[keys[table]]}"').fetchall()
        server_columns = [(name, _server_kind(row["datatype"])) for name, row in zip(names, rows)]
        # The core's rows sit below the identifier offset and the anaesthesia layer's above it.
        objects = [("dbo", duck)]
        if table in derived_tables:
            # A custom table has no core rows: the layer keeps all of them, and the view publishes them.
            objects = [(SETTINGS["anaesthesia_schema"], duck), (SETTINGS["published_schema"], duck)]
        elif table in anaesthesia_tables:
            core = [row for row in duck if row[keys[table]] is None or row[keys[table]] <= offset]
            layer = [row for row in duck if row[keys[table]] is not None and row[keys[table]] > offset]
            objects = [("dbo", core)]
            objects += [(SETTINGS["anaesthesia_schema"], layer), (SETTINGS["published_schema"], duck)]
        elif table == "source_to_concept_map":
            objects.append((SETTINGS["anaesthesia_schema"], duck))
        else:
            objects.append((SETTINGS["published_schema"], duck))
        for schema, expected in objects:
            try:
                found = server.rows(f"SELECT * FROM [{schema}].{_bracket(table)}", server_columns, OMOP_DATABASE)
            except SqlError as error:
                comparisons.append({"object": f"{schema}.{table}", "same": False, "duckdb": len(expected),
                                    "sqlserver": None, "error": str(error)})
                continue
            if not expected and not found:
                continue
            comparison = compare_rows(f"{schema}.{table}", kinds, expected, found, keys[table])
            comparison["names"] = names
            comparisons.append(comparison)
    return comparisons


# The target queries, their drafts, and hand-written source queries.

def _plain_value(value):
    """A value of a query's answer as text, so that the two engines' answers can be compared."""
    if value is None or value == "NULL":
        return None
    text = str(value).strip()
    try:
        number = Decimal(text)
        return str(number.normalize()) if number != number.to_integral_value() else str(int(number))
    except InvalidOperation:
        return text


def server_answer(server, sql, database):
    """The rows of one query on SQL Server, each value as text."""
    output = server.run("SET NOCOUNT ON;\n" + sql + "\n;", database, separator="|", trim=True)
    return [tuple(_plain_value(v) for v in line.split("|")) for line in output.splitlines() if line.strip()]


def duck_answer(con, conversion, sql):
    from schemalyser.translate import to_duckdb
    return [tuple(_plain_value(v) for v in row) for row in con.execute(to_duckdb(sql, conversion.sandbox.date_columns)[0]).fetchall()]


def published(sql, folder):
    """A target query with every OMOP table read from the release's published schema, for SQL Server, as the release
    rewrites a step. A custom table of the conversion is read from its published view, as a table of CDM 5.4 is."""
    custom = {row["table"] for row in convert.custom_rows(convert.read_tables(folder))}
    text, _ = release.rewrite(sql, set(convert.cdm_fields()) | custom, where="target query")
    return text.replace(f"[{release.PLACE['published']}].", f"[{SETTINGS['published_schema']}].")


def compare_targets(server, conversion, folder, targets, sources):
    """Runs each target query on the published schema beside DuckDB, and any hand-written source query given in
    sources on the source database in the same way.

    Returns [(label, DuckDB's answer, SQL Server's answer, error)].
    """
    con = conversion.con
    found = []
    for path in targets:
        sql = decode(path.read_bytes())
        label = f"{path.name} on the OMOP tables"
        try:
            found.append((label, duck_answer(con, conversion, sql), server_answer(server, published(sql, folder), OMOP_DATABASE), None))
        except (SqlError, Exception) as error:  # noqa: BLE001 - any failure is a finding to report
            found.append((label, None, None, str(error).splitlines()[0]))
    for path in sources:
        sql = decode(path.read_bytes())
        try:
            found.append((f"{path.name}, written by hand", duck_answer(con, conversion, sql), server_answer(server, sql, SOURCE_DATABASE), None))
        except (SqlError, Exception) as error:  # noqa: BLE001
            found.append((f"{path.name}, written by hand", None, None, str(error).splitlines()[0]))
    return found


def _server_kind(datatype):
    return {"integer": "int", "float": "float", "date": "date", "datetime": "datetime2"}.get(datatype, "varchar")


# The planted scenarios.

def plant_scenarios(server, scenarios):
    """Runs each scenario's rows.sql in clarity_shadow, as written. Returns {name: error or None}."""
    found = {}
    for scenario in scenarios:
        try:
            server.run("SET NOCOUNT ON;\n" + scenario["rows"], SOURCE_DATABASE, name=f"scenario_{scenario['name']}.sql")
            found[scenario["name"]] = None
        except SqlError as error:
            found[scenario["name"]] = str(error).splitlines()[0]
    return found


def evaluate_scenarios(server, folder, scenarios, report, planted):
    """Evaluates each expectation on the published schema. Returns [(scenario, says, DuckDB met, SQL Server met, their answer)]."""
    duck = {s["name"]: s for s in report["scenarios"]}
    found = []
    for scenario in scenarios:
        ours = duck[scenario["name"]]["expectations"]
        for number, item in enumerate(scenario["expectations"]):
            duck_met = number < len(ours) and ours[number]["met"]
            if planted.get(scenario["name"]):
                found.append((scenario["name"], item["says"], duck_met, False, f"not planted: {planted[scenario['name']]}"))
                continue
            text = published(item["query"], folder)
            try:
                rows = server_answer(server, text, OMOP_DATABASE)
                found.append((scenario["name"], item["says"], duck_met, convert.expectation_met(rows, item["result"]),
                              convert.shown_rows(rows)))
            except (SqlError, Exception) as error:  # noqa: BLE001 - any failure is a finding to report
                found.append((scenario["name"], item["says"], duck_met, False, str(error).splitlines()[0]))
    return found


# The check script.

def run_check_script(server, analysis, workspace):
    """Runs the check script on clarity_shadow as an analyst would, and returns its rows as CSV text without a header."""
    script = analysis.check_script()
    (workspace / "check_script.sql").write_text(script, encoding="utf-8")
    output = server.run(script, SOURCE_DATABASE, separator=",", trim=True)
    lines = [line for line in output.splitlines() if line.strip()]
    (workspace / "check_results.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines) + "\n"


def compare_checks(server_text, duck_rows):
    """Compares the check rows from SQL Server with DuckDB's, keyed by kind, table, column and value."""
    def tidy(row):
        cells = [cell.strip() for cell in row]
        return tuple("" if cell == "NULL" else cell for cell in cells)

    server_rows = [tidy(row) for row in csv.reader(io.StringIO(server_text)) if row]
    duck = Counter(tidy(row) for row in duck_rows)
    found = Counter(server_rows)
    only_duck, only_server = duck - found, found - duck
    return {"duckdb": sum(duck.values()), "sqlserver": sum(found.values()), "same": sum((duck & found).values()),
            "only_duckdb": list(only_duck.elements()), "only_sqlserver": list(only_server.elements())}


# The report.

def _show(row, names):
    return "(" + ", ".join(f"{name}={value}" for name, value in zip(names, row) if value is not None) + ")"


def main():
    parser = argparse.ArgumentParser(prog="harness.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("world", nargs="?", type=Path, help="a world folder; the invented world in fixtures/ when left out")
    parser.add_argument("--conversion", type=Path, default=ROOT / "fixtures" / "conversion")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--vocabulary", type=Path, help="an Athena download, so that derived mapping rows are proposed")
    parser.add_argument("--sample-vocabulary", action="store_true",
                        help="use a handful of public concepts, so that derived mapping rows are proposed for the invented world")
    parser.add_argument("--folder-mappings-only", action="store_true",
                        help="run the release script as release.py writes it, carrying only the folder's own mapping rows")
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--out", type=Path, help="a folder for every script that was run and the check results; "
                                                 "for a private world, give a folder under reference/")
    parser.add_argument("--skip-safeguards", action="store_true", help="do not try the release script's safeguards")
    parser.add_argument("--fresh-load", action="store_true",
                        help="load clarity_shadow again, even where an earlier run kept a copy of the same rows")
    parser.add_argument("--target", type=Path, action="append",
                        help="a target query to run on both engines; fixtures/targets/*.sql when left out")
    parser.add_argument("--source-query", type=Path, action="append", default=[],
                        help="a hand-written query against the source tables, to run on both engines")
    parser.add_argument("--scenarios", help="the planted scenarios to plant on both engines, separated by commas; "
                                            "every scenario that does not exist to make a gate fail when left out")
    parser.add_argument("--no-scenarios", action="store_true", help="plant no scenario")
    args = parser.parse_args()

    password_file = Path(os.environ.get("SCHEMALYSER_MSSQL_PASSWORD_FILE", PASSWORD_FILE))
    if not password_file.exists():
        raise SystemExit(f"The password file {password_file} does not exist. README.md says how to make it.")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    server = Server(args.container, password_file.read_text().strip(), args.out / "scripts" if args.out else None)
    workspace = args.out or server.local / "out"
    workspace.mkdir(parents=True, exist_ok=True)
    if args.world:
        world = harness.World.from_folder(args.world)
    else:
        import make_checks
        world = make_checks.WORLD
    vocabulary = args.vocabulary
    if args.sample_vocabulary:
        vocabulary = sample_vocabulary.write(server.local / "vocabulary")

    version = server.scalar("SELECT CONCAT(CAST(SERVERPROPERTY('Edition') AS nvarchar(200)), N', version ', "
                            "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)));")
    print(f"SQL Server: {version}")
    TODAY.update({dt.date.today(), dt.datetime.now(dt.timezone.utc).date(),
                  dt.date.fromisoformat(server.scalar("SELECT CONVERT(varchar(10), GETDATE(), 23);"))})

    # 1. DuckDB, exactly as the conversion runner does it.
    folder = args.conversion.resolve()
    steps = json.loads((folder / "conversion.json").read_text())
    FIELDS.update(all_fields(folder))
    names = [] if args.no_scenarios else [n.strip() for n in args.scenarios.split(",") if n.strip()] if args.scenarios else None
    scenarios = convert.chosen_scenarios(folder, names)
    conversion, report = convert.run(world, folder, args.rows, vocabulary, scenarios=[s["name"] for s in scenarios])
    con = conversion.con
    print(report["built"])
    derived = sum(item["matched"] for item in report["derived"])
    print(f"DuckDB: {sum(1 for s in report['steps'] if s['status'] == 'ok')} of {len(steps)} steps ran; "
          f"{report['mappings']} mapping rows were loaded from the folder and {derived} were derived.")
    duck_failures = convert.failures(report)
    for failure in duck_failures:
        print("  DuckDB: " + failure)

    # 2 and 3. The two databases.
    key = source_key(con, world.catalogue_text(), conversion.sandbox.tables, conversion.planted_from)
    restored = None if args.fresh_load else server.restore_source(key)
    if restored:
        findings, loaded = restored
        print("clarity_shadow was restored from the copy that an earlier run over the same rows kept.")
    else:
        findings, loaded = load_source(server, con, world.catalogue_text(), conversion.sandbox.tables, conversion.planted_from)
        if not server.keep_source(key, findings, loaded):
            print("clarity_shadow could not be kept for later runs, so the next run will load it again.")
    planted = plant_scenarios(server, scenarios)
    print(f"clarity_shadow holds {len(conversion.sandbox.tables)} tables and {loaded:,} rows, and "
          f"{sum(1 for e in planted.values() if e is None)} of {len(scenarios)} scenarios were planted on SQL Server.")
    for finding in findings:
        print("  type:", finding)
    create_omop(server)
    copied = {table: copy_table(server, con, table) for table in ("source_to_concept_map", "vocabulary")}
    print(f"omop_shadow holds the CDM 5.4 tables, with {copied['source_to_concept_map']} mapping rows "
          f"and {copied['vocabulary']} vocabulary rows copied from DuckDB.")

    # 4. The core layer, as written.
    core = [step for step in steps if step["layer"] == "core"]
    core_results = run_core(server, folder, core)
    # 5. The anaesthesia layer, through its release script.
    outcome = run_release(server, folder, con, workspace, not args.folder_mappings_only)
    written = list(dict.fromkeys(step["table"].lower() for step in steps if step["layer"] in ("anaesthesia", "derived")))
    gates = run_gates(server, folder, written)

    print("")
    print("STEPS")
    duck_steps = dict(zip((s["file"] for s in steps), report["steps"]))
    step_problems = 0
    for result in core_results:
        theirs = duck_steps[result["file"]]
        if result["status"] != "ok":
            step_problems += 1
            print(f"{result['file']}: failed on SQL Server: {result.get('message')}")
        elif theirs["status"] != "ok" or theirs["rows"] != result["rows"]:
            step_problems += 1
            print(f"{result['file']}: {result['rows']} rows on SQL Server and {theirs['rows']} in DuckDB ({theirs['status']})")
    print(f"core steps run on SQL Server: {sum(1 for r in core_results if r['status'] == 'ok')} of {len(core_results)}")
    if outcome["status"] == "committed":
        print("the release script ran to its end and committed. It reports:")
        for line in outcome["output"].splitlines():
            print("  " + line.strip())
    else:
        step_problems += 1
        print(f"the release script stopped: {outcome['message']}")
    # The conversion's counts, which the release script reports as sentences after the rows written.
    count_problems = 0
    for count in report.get("counts", []):
        if count["error"] or outcome["status"] != "committed":
            count_problems += 1
            print(f"count {count['name']}: could not be compared ({count['error'] or 'the release script stopped'})")
            continue
        same = count["says"].replace(convert.COUNT_MARK, str(count["rows"])) in outcome["output"]
        count_problems += not same
        print(f"count {count['name']}: {count['rows']} in DuckDB, {'the same' if same else 'NOT THE SAME'} on SQL Server")
    gate_problems = 0
    for ours, theirs in zip(gates, report["gates"]):
        verdict = lambda rows: "could not be run" if rows is None else "passed" if rows == 0 else f"failed with {rows} rows"  # noqa: E731
        agree = ours["rows"] == theirs["rows"]
        gate_problems += not agree or ours["rows"] != 0
        print(f"gate {ours['gate']}: {verdict(ours['rows'])} on SQL Server, {verdict(theirs['rows'])} in DuckDB")

    # 6. The OMOP tables.
    comparisons = compare_omop(server, con, steps, report["steps"])
    print("")
    print("OMOP TABLES")
    for item in comparisons:
        if item["same"]:
            continue
        if item.get("error"):
            print(f"{item['object']}: could not be read on SQL Server: {item['error']}")
            continue
        print(f"{item['object']}: {item['duckdb']} rows in DuckDB and {item['sqlserver']} on SQL Server; "
              f"{item['only_duckdb']} rows only in DuckDB and {item['only_sqlserver']} only on SQL Server.")
        if item["fields"]:
            print("  fields that differ in rows with the same identifier: " + ", ".join(
                f"{item['names'][i]} ({n})" for i, n in item["fields"].most_common()))
        if item["same_without_key"]:
            print("  the rows agree once the identifier is set aside, so only the numbering differs.")
        for row in item["sample_duckdb"]:
            print("  DuckDB:     " + _show(row, item["names"]))
        for row in item["sample_sqlserver"]:
            print("  SQL Server: " + _show(row, item["names"]))
    different = [item for item in comparisons if not item["same"]]
    print(f"objects compared: {len(comparisons)}; identical: {len(comparisons) - len(different)}; different: {len(different)}")

    # The planted scenarios, before the safeguards run the release script again.
    print("")
    print("SCENARIOS")
    expectations = evaluate_scenarios(server, folder, scenarios, report, planted) if outcome["status"] == "committed" else []
    scenario_problems = 0
    for name, says, duck_met, server_met, answer in expectations:
        scenario_problems += not (duck_met and server_met)
        verdict = lambda met: "met" if met else "NOT MET"  # noqa: E731
        print(f"{name}: {verdict(duck_met)} in DuckDB, {verdict(server_met)} on SQL Server: {says}"
              + ("" if server_met else f" (SQL Server gave {answer})"))
    print(f"expectations evaluated: {len(expectations)}; met on both engines: {len(expectations) - scenario_problems}")

    # 7. The release script's safeguards.
    safeguards = []
    if not args.skip_safeguards and outcome["status"] == "committed":
        print("")
        print("SAFEGUARDS")
        safeguards = try_safeguards(server, folder, con, workspace, written)
        for label, ok, detail in safeguards:
            print(f"{'as it should' if ok else 'NOT AS IT SHOULD'}: {label}" + ("" if ok or not detail else f" ({detail})"))
    unsafe = sum(1 for _, ok, _ in safeguards if not ok)

    # 8. The target queries and any hand-written source query.
    print("")
    print("TARGET QUERIES")
    targets = args.target or sorted((ROOT / "fixtures" / "targets").glob("*.sql"))
    answers = compare_targets(server, conversion, folder, targets, args.source_query) if outcome["status"] == "committed" else []
    target_problems = 0
    for label, duck, theirs, error in answers:
        if error:
            target_problems += 1
            print(f"{label}: could not be compared: {error}")
            continue
        same = Counter(duck) == Counter(theirs)
        target_problems += not same
        shown = lambda rows: "; ".join(", ".join("" if v is None else v for v in row) for row in rows[:6]) + (" ..." if len(rows) > 6 else "")  # noqa: E731
        print(f"{label}: {'the same on both engines' if same else 'DIFFERENT'}: DuckDB {shown(duck)}; SQL Server {shown(theirs)}")

    # 9. The check script.
    analysis = world.analysis()
    definitions = {}
    if world.rules_path:
        for rule in json.loads(world.rules_path.read_text()).get("definitionKeys", []):
            if isinstance(rule, dict) and rule.get("column") and rule.get("definitionTable"):
                definitions[rule["column"].upper()] = (rule["definitionTable"], rule.get("keyColumn") or rule["column"])
    analysis.add_request("conversion", convert.as_request(
        [(step["table"], decode((folder / step["file"]).read_bytes())) for step in steps], definitions))
    duck_rows, failed = harness.run_checks(analysis, con)
    print("")
    print("CHECKS")
    check_problems = 0
    try:
        server_text = run_check_script(server, analysis, workspace)
    except SqlError as error:
        server_text = None
        check_problems += 1
        print(f"the check script failed on SQL Server: {error}")
    if server_text is not None:
        compared = compare_checks(server_text, duck_rows)
        for label, rows in (("only in DuckDB", compared["only_duckdb"]), ("only on SQL Server", compared["only_sqlserver"])):
            for row in rows[:12]:
                print(f"  {label}: {','.join(row)}")
            if len(rows) > 12:
                print(f"  and {len(rows) - 12} more {label}")
        errors = [row for row in csv.reader(io.StringIO(server_text)) if row and row[0].strip() == "error"]
        for row in errors:
            print(f"  the check on {row[1].strip()} {row[2].strip()} raised SQL Server error {row[5].strip()}")
        for item in failed:
            print("  DuckDB could not answer: " + " ".join(item))
        check_problems += len(compared["only_duckdb"]) + len(compared["only_sqlserver"]) + len(errors) + len(failed)
        print(f"check rows compared: {max(compared['duckdb'], compared['sqlserver'])}; identical: {compared['same']}; "
              f"only in DuckDB: {len(compared['only_duckdb'])}; only on SQL Server: {len(compared['only_sqlserver'])}")
        for label, text in (("without a header", server_text), ("with a header", ",".join(LAYOUT) + "\n" + server_text)):
            try:
                accepted = Checks.from_csv(text, analysis.catalogue, analysis.rules)
                print(f"Checks.from_csv accepts the SQL Server output {label}: {len(accepted.columns)} columns, "
                      f"{len(accepted.rows)} tables, {sum(map(len, accepted.values.values()))} values and "
                      f"{sum(map(len, accepted.years.values()))} years.")
            except ChecksError:
                check_problems += 1
                print(f"Checks.from_csv refuses the SQL Server output {label}.")

    print("")
    print("SUMMARY")
    print(f"OMOP objects compared: {len(comparisons)}; identical: {len(comparisons) - len(different)}; different: {len(different)}.")
    print(f"Steps that failed or wrote a different number of rows: {step_problems}.")
    print(f"Gates passed on SQL Server: {sum(1 for g in gates if g['rows'] == 0)} of {len(gates)}; "
          f"gates whose outcome differs from DuckDB's or did not pass: {gate_problems}.")
    print(f"Counts that differ between the engines or could not be compared: {count_problems} of {len(report.get('counts', []))}.")
    print(f"Check rows that differ, and checks that failed: {check_problems}.")
    print(f"Source columns whose sandbox values do not fit the catalogue's type: {len(findings)}.")
    print(f"Reasons that the DuckDB run itself was not clean: {len(duck_failures)}.")
    print(f"Safeguards that behaved as they should: {len(safeguards) - unsafe} of {len(safeguards)}.")
    print(f"Target queries and source queries whose answers differ or could not be compared: {target_problems} of {len(answers)}.")
    print(f"Expectations of the planted scenarios not met on both engines: {scenario_problems} of {len(expectations)}.")
    exit_code = 1 if different or step_problems or gate_problems or check_problems or findings or duck_failures or unsafe \
        or target_problems or scenario_problems or count_problems else 0
    write_summary(workspace / "summary.json", {
        "versions": versions(version),
        "world": args.world.resolve().name if args.world else "fixtures", "conversion": folder.name, "rows": args.rows,
        "release": {"status": outcome["status"], "message": outcome.get("message")},
        "steps": [{"file": r["file"], "sqlserver": r["status"], "sqlserver_rows": r.get("rows"),
                   "duckdb": duck_steps[r["file"]]["status"], "duckdb_rows": duck_steps[r["file"]]["rows"],
                   "agree": r["status"] == "ok" and duck_steps[r["file"]]["status"] == "ok" and r.get("rows") == duck_steps[r["file"]]["rows"]}
                  for r in core_results],
        "tables": [{"object": item["object"], "duckdb_rows": item["duckdb"], "sqlserver_rows": item["sqlserver"],
                    "duckdb_checksum": item.get("checksum_duckdb"), "sqlserver_checksum": item.get("checksum_sqlserver"),
                    "agree": item["same"], "error": item.get("error")} for item in comparisons],
        "scenarios": scenario_outcomes(expectations, planted),
        "gates": [{"gate": ours["gate"], "sqlserver_rows": ours["rows"], "duckdb_rows": theirs["rows"],
                   "sqlserver": gate_outcome(ours["rows"]), "duckdb": gate_outcome(theirs["rows"]),
                   "agree": ours["rows"] == theirs["rows"] == 0} for ours, theirs in zip(gates, report["gates"])],
        "counts": {"compared": len(report.get("counts", [])), "differ": count_problems},
        "checks": {"differ": check_problems}, "type_findings": len(findings), "duckdb_failures": duck_failures,
        "safeguards": [{"safeguard": label, "as_it_should": ok} for label, ok, _ in safeguards],
        "target_queries": {"compared": len(answers), "differ": target_problems},
        "exit_code": exit_code,
    })
    print("The same summary, with a checksum for each table, is in summary.json beside the release script.")
    print(f"The scripts that were run are in {server.local}, and the release script and check results in {workspace}.")
    server.clean()
    return exit_code


def gate_outcome(rows):
    return "could not be run" if rows is None else "passed" if rows == 0 else "failed"


def scenario_outcomes(expectations, planted):
    """Each scenario's outcome on each engine: met when every one of its expectations was met there."""
    found = {}
    for name, says, duck_met, server_met, _ in expectations:
        entry = found.setdefault(name, {"scenario": name, "planted_on_sqlserver": planted.get(name) is None, "expectations": []})
        entry["expectations"].append({"says": says, "duckdb": "met" if duck_met else "not met",
                                      "sqlserver": "met" if server_met else "not met"})
    for entry in found.values():
        for engine in ("duckdb", "sqlserver"):
            entry[engine] = "met" if all(item[engine] == "met" for item in entry["expectations"]) else "not met"
        entry["agree"] = entry["duckdb"] == entry["sqlserver"] == "met"
    return list(found.values())


def versions(server_version):
    import duckdb
    import sqlglot
    return {"sqlserver": server_version, "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__,
            "python": sys.version.split()[0], "summary_format": 1}


def write_summary(path, summary):
    """Writes the machine-readable summary, which names no folder of this machine."""
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
