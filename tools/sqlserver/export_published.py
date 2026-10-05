"""Exports the published schema of a release from SQL Server, for loading into PostgreSQL for OHDSI.

The release script publishes a complete CDM as views in a schema of its own: the core's rows, the
anaesthesia layer's rows and the custom tables that the derived layer writes. This tool reads every
view of that schema that holds rows, apart from the vocabulary tables, and writes one CSV file for
each, with a psql script that loads them in place of the clinical rows already there. It runs after
harness.py, from the core folder, as the harness does:

    cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/sqlserver/export_published.py \
        --conversion ../fixtures/conversion --out FOLDER

The load script, load_postgresql.sql, then runs from FOLDER with psql against the database that
WebAPI reads. It works in one transaction. It empties every clinical table of CDM 5.4 in the target
schema, so that no row of an earlier load remains, and leaves the vocabulary tables as they are. It
widens to BIGINT each column that SQL Server types as BIGINT, because the anaesthesia layer's
identifiers start above the range of INTEGER, and it creates each custom table from the conversion's
tables.json where it is missing, with every integer field as BIGINT for the same reason. It then
loads the files and compares each table's rows with the count that SQL Server gave, and stops if any
differs.

A value is written as CSV text: an empty value as nothing, an empty string as "", a date and time in
ISO form and a float in full precision, so that PostgreSQL reads back what SQL Server held. counts.csv
gives the rows of each exported table. The password for the sa login is read as harness.py reads it,
and never printed.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402

# The vocabulary tables of CDM 5.4, which the target database already holds from an Athena download.
VOCABULARY = {"concept", "vocabulary", "domain", "concept_class", "concept_relationship", "relationship",
              "concept_synonym", "concept_ancestor", "source_to_concept_map", "drug_strength"}
CUSTOM_TYPES = {"integer": "BIGINT", "float": "DOUBLE PRECISION", "date": "DATE", "datetime": "TIMESTAMP"}


def published(server, database, schema):
    """Each view or table of the schema, with its columns and their SQL Server types, in their order."""
    text = server.rows(
        "SELECT c.TABLE_NAME AS t, c.COLUMN_NAME AS c, c.DATA_TYPE AS d, CAST(c.ORDINAL_POSITION AS int) AS o "
        f"FROM INFORMATION_SCHEMA.COLUMNS c WHERE c.TABLE_SCHEMA = {harness.literal(schema, 'varchar')}",
        [("t", "varchar"), ("c", "varchar"), ("d", "varchar"), ("o", "int")], database)
    tables = {}
    for table, column, kind, order in sorted(text, key=lambda row: (row[0], int(row[3]))):
        tables.setdefault(table.lower(), []).append((column, kind.lower()))
    return tables


def custom_tables(conversion):
    path = Path(conversion) / "tables.json" if conversion else None
    if not path or not path.exists():
        return []
    found = json.loads(path.read_text(encoding="utf-8"))
    return found["tables"] if isinstance(found, dict) else found


def clinical_tables():
    """The tables of CDM 5.4 that hold clinical or derived rows, which the load empties first."""
    fields = harness.release._fields()
    return [table for table in fields if table not in VOCABULARY]


def main():
    parser = argparse.ArgumentParser(prog="export_published")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--conversion", type=Path, help="the conversion folder, for the custom tables in tables.json")
    parser.add_argument("--database", default=harness.OMOP_DATABASE)
    parser.add_argument("--schema", default=harness.SETTINGS["published_schema"])
    parser.add_argument("--target-schema", default="cdm")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in (args.database, args.schema, args.target_schema)):
        raise SystemExit("The database and the schemas must be plain names.")
    args.out.mkdir(parents=True, exist_ok=True)
    server = harness.Server(harness.CONTAINER, harness.PASSWORD_FILE.read_text().strip())
    custom = custom_tables(args.conversion)
    custom_names = [table["name"] for table in custom]
    counts, widened = {}, []
    try:
        for table, columns in published(server, args.database, args.schema).items():
            if table in VOCABULARY:
                continue
            source = f"{harness._bracket(args.schema)}.{harness._bracket(table)}"
            rows = int(server.scalar(f"SELECT COUNT_BIG(*) FROM {source};", args.database))
            if not rows:
                continue
            values = server.rows(f"SELECT * FROM {source}", columns, args.database)
            if len(values) != rows:
                raise SystemExit(f"{table}: SQL Server counted {rows} rows and returned {len(values)}.")
            with open(args.out / f"{table}.csv", "w", encoding="utf-8") as handle:
                handle.write(",".join(name.lower() for name, _ in columns) + "\n")
                for row in values:
                    # An empty value is written unquoted, which PostgreSQL reads as NULL, and every other
                    # value is quoted, so that an empty string stays an empty string.
                    handle.write(",".join("" if value is None else '"' + value.replace('"', '""') + '"'
                                          for value in row) + "\n")
            counts[table] = rows
            widened += [(table, name.lower()) for name, kind in columns if kind == "bigint" and table not in custom_names]
    finally:
        server.clean()
    with open(args.out / "counts.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["table", "rows"])
        writer.writerows(sorted(counts.items()))
    schema = args.target_schema
    lines = ["-- Loads the published schema of a release in place of the clinical rows of this schema.",
             "-- The vocabulary tables are left as they are.",
             "\\set ON_ERROR_STOP on", "BEGIN;",
             f"TRUNCATE {', '.join(f'{schema}.{t}' for t in clinical_tables())};"]
    for table, column in widened:
        lines.append(f"ALTER TABLE {schema}.{table} ALTER COLUMN {column} TYPE BIGINT;")
    for table in custom:
        fields = ",\n".join(
            f'  {field["name"]} {CUSTOM_TYPES.get(field["type"], field["type"].upper())}'
            f'{" NOT NULL" if field.get("required") else ""}{" PRIMARY KEY" if field.get("primary_key") else ""}'
            for field in table["fields"])
        lines.append(f"CREATE TABLE IF NOT EXISTS {schema}.{table['name']} (\n{fields}\n);")
        lines.append(f"TRUNCATE {schema}.{table['name']};")
    for table in sorted(counts):
        header = (args.out / f"{table}.csv").open(encoding="utf-8").readline().strip()
        lines.append(f"\\copy {schema}.{table} ({header}) FROM '{table}.csv' WITH (FORMAT csv, HEADER true)")
    checks = " UNION ALL ".join(f"SELECT '{t}' AS t, (SELECT COUNT(*) FROM {schema}.{t}) - {n} AS d"
                                for t, n in sorted(counts.items()))
    lines += [f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM ({checks}) q WHERE d <> 0) THEN "
              "RAISE EXCEPTION 'A table does not hold the rows that SQL Server gave.'; END IF; END $$;",
              "COMMIT;"]
    (args.out / "load_postgresql.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Exported {len(counts)} tables with rows from {args.database}.{args.schema} to {args.out}, "
          f"with load_postgresql.sql and counts.csv. {len(widened)} columns are widened to BIGINT by the load.")


if __name__ == "__main__":
    main()
