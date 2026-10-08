# The OMOP testbed

The testbed runs the whole conversion to OMOP on a synthetic world in one command, checks it, and writes one report. It adds no conversion logic of its own.

```sh
cd core
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python -m schemalyser.testbed run --world fixtures --out ../../testbed-out --rows 200
```

Give an output folder outside the repository, or under `reference/` for a private world, because the report names the source tables. A run on the invented world at 200 rows takes about 25 seconds.

## What it does

The testbed builds the synthetic source into a fresh DuckDB database, creates the tables of OMOP CDM 5.4 from the published field list, loads the small vocabulary subset that the SQL Server harness uses, plants the scenarios and runs every step of the conversion. It then checks each scenario against the rows that its `scenario.json` states, which were written by hand and are never taken from the output.

The reconciliation runs each step's own SELECT again with its output replaced by the row number of the table that the step starts from, first with every inner join made a left join and no WHERE, then with each join and condition restored in turn. Every source row that does not reach the target is put down to the join or condition that left it out. A gate removes no rows; it fails the run. A world's `testbed.json` names the steps that may write several rows for one source row, such as the blood pressure step; every other step is expected to write at most one. Without the file, the report states what it found without judging it.

It then runs the gates and counts, and confirms that the release script carries every step that ran.

## What it reports

`report.json` holds the versions of the tool, CDM, vocabulary and DuckDB; the world's checksums; the engine for each stage; each step; each scenario with its expected and found rows; the reconciliation by step, source table and target table; the release script; the dashboard's inputs; every check with its outcome; and a plain summary. `report.md` sets out the same for a person.

## Running the Data Quality Dashboard afterwards

R is not available here, so the report marks the dashboard as not run, and it runs afterwards in the Broadsea environment. The output folder holds `testbed_cdm.duckdb`, with every table in its `cdm` schema, and `csv/`, with one file for each table and `load_postgresql.sql`. The load script replaces its own `testbed` schema and leaves the database's `cdm` schema, with its full vocabulary, untouched. From the output folder, load the tables, then run the dashboard's image with the script that the testbed wrote:

```sh
(cd csv && psql -h 127.0.0.1 -p PORT -U postgres -f load_postgresql.sql)
docker run --rm --platform linux/amd64 -e PG_PORT=PORT -v "$PWD/dqd/run_dqd.R:/run_dqd.R:ro" \
  -v "PASSWORD_FILE:/run/pg-password.txt:ro" -v jdbc-drivers-data:/jdbc -v "$PWD/dqd/out:/out" \
  broadsea-broadsea-run-dqd Rscript /run_dqd.R
```

## SQL Server

With `--engine sqlserver`, the testbed also runs `tools/sqlserver/harness.py` at the same size, with the sample vocabulary and without the safeguards. The report keeps the harness's summary and says which stages ran on which engine. The reconciliation and the dashboard's inputs always come from DuckDB.

## What done means for this milestone

The milestone is done when one command on the invented world runs every step cleanly, passes every gate, count and planted scenario, reconciles every traced step with sums that agree and no unexpected fan-out, writes a release script that carries every step, and leaves the dashboard's inputs ready, all recorded in `report.json`. `core/tests/test_testbed.py` checks this at 60 rows.
