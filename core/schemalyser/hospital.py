"""The invented hospital: a DuckDB database built from the files that fixtures/make_hospital.py writes, on which the
front page runs its own queries when the invented dictionary is in use.

The files hold the invented world's tables and rows exactly as the tests build them. The database is built once and
answers as the retired practice database did (sandbox.Sandbox): each query is translated from SQL Server with
translate.py, and SQL Server's own records of its tables (INFORMATION_SCHEMA and sys.partitions) are kept beside the
tables, so that the tables and columns query works here as it does there. A two-part script that fills #cohort runs as
two parts, part 1 and then part 2, in one session, as SQL Server Management Studio runs it. The result comes back as
the grid that Copy with Headers gives, so that the page reads it exactly as it reads a paste.
"""
import csv
import io
import json
import re

import duckdb

from .catalogue import Catalogue
from .sandbox import LOCKED_DOWN, Sandbox, _quoted
from .translate import Unreadable, Unsupported, to_duckdb

# The end of part 1 of a two-part script, after which part 2 reads #cohort.
PART_ONE_END = re.compile(r"^ALTER TABLE #cohort ADD PRIMARY KEY \(anaesthetic_key\);[ \t]*$", re.MULTILINE)
CHUNK = 500


class HospitalError(ValueError):
    """The files do not make the invented hospital, or a query cannot run on it."""


class InventedHospital:
    def __init__(self, files):
        """files is {published path: bytes}: manifest.json, catalogue.csv (the invented catalogue) and
        tables/<TABLE>.csv for each table that the manifest names."""
        try:
            manifest = json.loads(bytes(files["manifest.json"]).decode("utf-8"))
            catalogue = Catalogue.from_csv(bytes(files["catalogue.csv"]).decode("utf-8-sig"))
        except (KeyError, ValueError) as error:
            raise HospitalError("files") from error
        # The practice database's own machinery, without its generator: the tables are loaded from the files.
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.catalogue = catalogue
        sandbox.tables = [t["name"] for t in manifest["tables"]]
        sandbox.date_columns = frozenset(manifest["date_columns"])
        sandbox.whole_columns = frozenset(manifest["whole_columns"])
        sandbox.con = duckdb.connect()
        # One thread, as in the page's worker, so that rows which tie in a query's ORDER BY come back in the same order
        # on every run, and two runs of the same query give the same result. It is set before the settings are locked.
        try:
            sandbox.con.execute("SET threads TO 1")
        except duckdb.Error:
            pass  # a build of DuckDB without threads runs on one already
        for setting in LOCKED_DOWN:
            sandbox.con.execute(f"SET {setting}")
        for table in manifest["tables"]:
            name, columns = table["name"], table["columns"]
            sandbox.con.execute(f"CREATE TABLE {_quoted(name)} ({', '.join(f'{_quoted(c)} {t}' for c, t in columns)})")
            try:
                reader = csv.reader(io.StringIO(bytes(files[f"tables/{name}.csv"]).decode("utf-8")))
            except KeyError as error:
                raise HospitalError("files") from error
            next(reader, None)
            rows = list(reader)
            for start in range(0, len(rows), CHUNK):
                values = ", ".join("(" + ", ".join("NULL" if cell == "" else "'" + cell.replace("'", "''") + "'"
                                                   for cell in row) + ")" for row in rows[start:start + CHUNK])
                sandbox.con.execute(f"INSERT INTO {_quoted(name)} VALUES {values}")
        sandbox._server_records()
        self.sandbox = sandbox
        self.tables = len(sandbox.tables)

    def parts(self, sql):
        """The parts of a query as SQL Server Management Studio runs them: two for a script that fills #cohort, else one."""
        found = PART_ONE_END.search(sql)
        if not found:
            return [sql]
        return [sql[:found.start()], sql[found.end():]]

    def _statements(self, sql):
        # SQL Server reads ISNULL(a.anaesthetic_key, 0) whatever the type of the key, and DuckDB needs both of one type;
        # the cohort holds no empty key, so the plain key gives the same rows.
        sql = re.sub(r"ISNULL\(a\.anaesthetic_key, 0\)", "a.anaesthetic_key", sql)
        try:
            statements = to_duckdb(sql, self.sandbox.date_columns, self.sandbox.whole_columns, server_records=True)
        except (Unreadable, Unsupported):
            raise HospitalError("unreadable") from None
        except Exception:
            raise HospitalError("unreadable") from None
        return [s for s in statements if not s.lstrip().upper().startswith("ALTER TABLE")]

    def run(self, sql):
        """Runs a query or a two-part script, and returns (columns, rows) of its last result, each value as text or None."""
        con = self.sandbox.con
        self.sandbox._clear_temporary_tables()
        columns, rows = None, []
        try:
            for part in self.parts(sql):
                for statement in self._statements(part):
                    cursor = con.execute(statement)
                    if cursor.description and statement.lstrip().upper().startswith(("SELECT", "WITH", "FROM", "(")):
                        columns = [d[0] for d in cursor.description]
                        rows = [[None if v is None else str(v) for v in row] for row in cursor.fetchall()]
        except duckdb.Error as error:
            failed = HospitalError("database")
            # A table that the query names and the invented hospital does not hold is named, as SQL Server names it.
            missing = re.search(r"Table with name (\w+) does not exist", str(error))
            failed.table = missing.group(1) if missing else None
            raise failed from None
        finally:
            self.sandbox._clear_temporary_tables()
            con.execute("DROP TABLE IF EXISTS temp_cohort")
        if columns is None:
            raise HospitalError("no result")
        return columns, rows

    def grid(self, sql):
        """The result as SQL Server Management Studio's Copy with Headers gives it: tab-separated, NULL where empty."""
        columns, rows = self.run(sql)
        return "\n".join("\t".join("NULL" if v is None else v for v in row) for row in [columns, *rows]) + "\n"


def files_from(folder, catalogue):
    """The published files of the invented hospital, read from fixtures/hospital and the invented catalogue."""
    from pathlib import Path
    folder = Path(folder)
    found = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    found["catalogue.csv"] = Path(catalogue).read_bytes()
    return found
