# Running the generated T-SQL on SQL Server

Schemalyser tests its T-SQL by translating it for DuckDB. The harness in this folder runs the same T-SQL on a real SQL Server, over the same synthetic rows, and reports every place where the two engines disagree. It runs the core steps of a conversion as they are written, runs the release script for the anaesthesia layer with `sqlcmd` as an operator would, tries the release script's safeguards, runs the check script, and compares each result with what DuckDB produced.

Everything here uses the invented world in `fixtures/` unless you name another world.

## Starting the container

The harness expects a container named `schemalyser-mssql`, listening on 127.0.0.1 only. The password for the `sa` login lives in `reference/mssql-password.txt`, which git ignores. Create it once, from the root of the repository:

```sh
umask 077
python3 -c "import secrets, string; a = string.ascii_letters + string.digits; print('Sx' + ''.join(secrets.choice(a) for _ in range(28)) + '#9a', end='')" > reference/mssql-password.txt
```

On an Intel machine, or on a Mac whose Docker Desktop has "Use Rosetta for x86_64/amd64 emulation" turned on, run SQL Server 2022 Developer edition:

```sh
MSSQL_SA_PASSWORD="$(cat reference/mssql-password.txt)" docker run -d --name schemalyser-mssql \
  --platform linux/amd64 -e ACCEPT_EULA=Y -e MSSQL_PID=Developer -e MSSQL_SA_PASSWORD \
  -p 127.0.0.1:14330:1433 --memory 4g -v schemalyser-mssql-data:/var/opt/mssql \
  mcr.microsoft.com/mssql/server:2022-latest
```

Running this command accepts Microsoft's licence terms for SQL Server Developer edition. The image ships the ODBC `sqlcmd` at `/opt/mssql-tools18/bin/sqlcmd`, which the harness uses, and the harness is tested against this image. The container keeps UTC, and the harness compares a date taken from the clock, such as the release date in CDM_SOURCE, as "today" on both engines, so the container's time zone does not need to change.

Under Docker Desktop's older QEMU emulation, SQL Server 2022 stops at once with a segmentation fault. If that happens, remove the container and its volume (see below) and run Azure SQL Edge instead, which is built for Apple Silicon:

```sh
MSSQL_SA_PASSWORD="$(cat reference/mssql-password.txt)" docker run -d --name schemalyser-mssql \
  -e ACCEPT_EULA=Y -e MSSQL_PID=Developer -e MSSQL_SA_PASSWORD \
  -p 127.0.0.1:14330:1433 --memory 4g -v schemalyser-mssql-data:/var/opt/mssql \
  mcr.microsoft.com/azure-sql-edge:latest
```

Azure SQL Edge does not ship `sqlcmd`, so you need to copy in the Go version of `sqlcmd`, which reads the same scripts:

```sh
curl -sL -o /tmp/sqlcmd.tar.bz2 https://github.com/microsoft/go-sqlcmd/releases/download/v1.10.0/sqlcmd-linux-arm64.tar.bz2
mkdir -p /tmp/go-sqlcmd && tar -xjf /tmp/sqlcmd.tar.bz2 -C /tmp/go-sqlcmd
docker exec -u 0 schemalyser-mssql mkdir -p /opt/go-sqlcmd
docker cp /tmp/go-sqlcmd/sqlcmd schemalyser-mssql:/opt/go-sqlcmd/sqlcmd
```

The harness finds `sqlcmd` in either image by itself. Azure SQL Edge is built from SQL Server 2019, so a difference that depends on a feature added in SQL Server 2022 will not show up there, and the Go `sqlcmd` has not been tried with the current harness.

## Running the harness

Run it from the `core` folder, as the tests are run:

```sh
cd core
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/sqlserver/harness.py --sample-vocabulary
```

With `--sample-vocabulary`, a handful of public concepts stand in for an Athena download, so that the conversion derives some mapping rows and the harness can check that they reach SQL Server. To use a real Athena download, pass `--vocabulary FOLDER` instead; reading a full download takes a minute or two. To run another world and conversion, name the world's folder and pass `--conversion FOLDER`. With `--out FOLDER`, the harness keeps every script that it ran, the release script, the check script and the check results in that folder. For a private world, give a folder under `reference/`, so that nothing it writes leaves the private part of the repository:

```sh
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/sqlserver/harness.py ../reference/worlds/WORLD \
  --conversion ../etl/CONVERSION --vocabulary ../reference/athena --out ../reference/harness-WORLD \
  --rows 50 --skip-safeguards
```

A large world should be run small, as that command does. Whether a step is correct does not depend on the number of rows, and the planted scenarios are loaded in full at any size. The safeguards do not depend on the world, so the run on the invented world covers them, and `--skip-safeguards` leaves them out. A run at full size is worth its time only when the question is how long a step takes on many rows.

The harness loads each source table from a bulk file, and falls back to `INSERT` statements for a table that holds a value the bulk file cannot carry faithfully. When the load has finished, the harness keeps the source database as a backup inside the container, under a fingerprint of the catalogue and of every row that it loaded. A later run over the same rows restores that backup and loads nothing, and says so in its report. The harness keeps the four newest backups and removes the rest. With `--fresh-load`, the harness loads the rows again whether or not a backup exists.

The harness makes two databases afresh on each run, restoring the first from its backup where it can: `clarity_shadow`, which holds the sandbox's source tables with the catalogue's own types, and `omop_shadow`, which holds the CDM 5.4 tables in `dbo`, typed as a core with `INT` identifiers. The core steps run into `dbo`, and the release script runs with a `-v` for each of its variables, exactly as an operator would run it, carrying every mapping row that DuckDB held, the derived ones included. The harness then compares the core's rows, the anaesthesia layer's own rows and the published views with DuckDB's, telling the core's rows from the layer's by the identifier offset. The release script ends by reporting each of the conversion's counts, such as the anaesthetics that the layer has left out, and the harness compares each number with DuckDB's.

The harness then tries the release script's safeguards, unless `--skip-safeguards` is given. It checks that a second run writes the same rows with the same identifiers; that a missing variable, two equal schemas, a schema name holding a bracket and a source prefix holding SQL each stop the script before any change, with the guard's own message; that a core identifier at the identifier offset stops it; that a change of identifier type reaches the tables of an earlier run; and that a failed gate leaves the previous rows with `on_failure` set to `keep` and empty tables with `empty`. A last run restores the normal rows.

The harness compares the custom tables that the derived layer writes in the anaesthesia schema and through their published views. It then runs each target query in `fixtures/targets/`, or each one given with `--target`, against the published schema, and the source-side draft that `schemalyser.target` composes for it against `clarity_shadow`, and compares each answer with DuckDB's. A hand-written query against the source tables, given with `--source-query`, is run on both engines in the same way.

The harness plants the conversion's scenarios on both engines. It loads `clarity_shadow` with the sandbox's rows as they were before any scenario was planted, runs each scenario's `rows.sql` on SQL Server as written, and, once the release script has run, evaluates each expectation on the published schema and reports whether it is met on each engine. `--scenarios` names the scenarios to plant, and `--no-scenarios` plants none.

The harness prints what it found and exits with 1 if any table, gate, count, safeguard or check row differs, or if the DuckDB run itself was not clean. A difference is a finding to be explained, not a fault in the harness. The scripts are removed from the container at the end of a run, and the two databases stay until the next run.

## Exporting the published schema for OHDSI

Once the harness has run, `omop_shadow` holds what the release script published: a complete CDM as views in `anaes_pub`, with the views of the custom tables. `export_published.py` reads every one of those views that holds rows, apart from the vocabulary tables, and writes one CSV file for each, with `counts.csv` and a psql script, `load_postgresql.sql`, that loads them into a PostgreSQL schema that WebAPI reads. Run it from the `core` folder, as the harness is run:

```sh
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/sqlserver/export_published.py \
  --conversion ../fixtures/conversion --out ../reference/atlas-load
cd ../reference/atlas-load && psql -h 127.0.0.1 -p PORT -U postgres -f load_postgresql.sql
```

The load script works in one transaction. It empties every clinical table of CDM 5.4 in the `cdm` schema and leaves the vocabulary tables as they are. It widens to `BIGINT` each column that SQL Server types as `BIGINT`, because the anaesthesia layer's identifiers start at 5,000,000,000, which is beyond the range of `INTEGER`. It creates each custom table from the conversion's `tables.json` where it is missing, with every integer field as `BIGINT`, loads the files, and stops without committing if any table does not then hold the number of rows that SQL Server gave. An empty value in a file is read as NULL, and an empty string stays an empty string. For a private world, give an output folder under `reference/`.

## Stopping and removing everything

To stop the container and keep its databases, run `docker stop schemalyser-mssql`, and `docker start schemalyser-mssql` brings it back. To remove the container, its volume and the images:

```sh
docker rm -f schemalyser-mssql
docker volume rm schemalyser-mssql-data
docker image rm mcr.microsoft.com/azure-sql-edge:latest mcr.microsoft.com/mssql/server:2022-latest
rm reference/mssql-password.txt
```
