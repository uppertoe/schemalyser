# The OMOP testbed

The testbed runs the whole conversion to OMOP on a synthetic world in one command, checks it, and writes one report. It adds no conversion logic of its own.

```sh
cd core
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python -m schemalyser.testbed run --world fixtures --out ../../testbed-out --rows 200
```

That command runs the fast profile, which is the default. Add `--profile full` for the full profile, which the next section describes.

Give an output folder outside the repository, or under `reference/` for a private world, because the report names the source tables. A run on the invented world at 200 rows takes about 25 seconds.

## The two profiles

The fast profile builds, converts, checks and reconciles the world on DuckDB, writes the release script and the dashboard's inputs, and passes when every judged check passes. It does not run the Data Quality Dashboard, and it records the dashboard and release equivalence as not run without failing for either.

The full profile does everything that the fast profile does, then loads the tables into the OMOP database and runs the Data Quality Dashboard, as the section on the dashboard below describes, and records its results under `dqd` in `report.json`. Both stages are mandatory in the full profile. If the dashboard cannot run, the outcome is "failed: the Data Quality Dashboard did not run", and if release equivalence has not passed, the outcome says so too. Release equivalence needs SQL Server, so the full profile can pass only with `--engine sqlserver`.

## What it does

The testbed builds the synthetic source into a fresh DuckDB database, creates the tables of OMOP CDM 5.4 from the published field list, loads the small vocabulary subset that the SQL Server harness uses, plants the scenarios and runs every step of the conversion. It then checks each scenario against the rows that its `scenario.json` states, which were written by hand and are never taken from the output.

The reconciliation runs each step's own SELECT again with its output replaced by the row number of the table that the step starts from, first with every inner join made a left join and no WHERE, then with each join and condition restored in turn. Every source row that does not reach the target is put down to the join or condition that left it out. A gate removes no rows; it fails the run. A world's `testbed.json` names the steps that may write several rows for one source row, such as the blood pressure step; every other step is expected to write at most one. Without the file, a step that writes several rows for one source row is counted as an unexplained discrepancy, because nothing says whether it may.

The reconciliation then sorts the steps into four groups, by count and by name: the steps traced with every excluded row accounted for; the steps traced with the fan-out that `testbed.json` allows confirmed; the steps that could not be traced, such as those that aggregate or start from a union, each with its reason; and the steps or tables with a discrepancy that it cannot explain. The run can pass only when the last group is empty. The summary never counts an untraced step as reconciled, and on the invented world it reads in this form: "The reconciliation traced N of M steps and accounted for every excluded row in them; K steps could not be traced (names), so their rows are not reconciled."

It then runs the gates and counts, and confirms that the release script carries every step that ran.

## What it reports

`report.json` holds the versions of the tool, CDM, vocabulary and DuckDB; the world's checksums; the engine for each stage; each step; each scenario with its expected and found rows; the reconciliation by step, source table and target table, with its coverage; the release script; the dashboard's inputs, and its results in the full profile; every check, release equivalence among them, with its outcome; and a plain summary. `report.md` sets out the same for a person.

## The Data Quality Dashboard

The dashboard needs R and its package, which the Broadsea image `broadsea-broadsea-run-dqd` holds; Broadsea builds it with its `dqd` profile. In the full profile, the testbed runs the two commands below itself. It needs `psql`, Docker with that image, the OMOP database listening on 127.0.0.1 at port 55440, or at the port that `SCHEMALYSER_PG_PORT` names, and the database's password in `reference/pg-password.txt`, or in the file that `SCHEMALYSER_PG_PASSWORD_FILE` names. If any of these is missing, the report says which, and the full profile does not pass. When the dashboard runs, the report counts its checks as passed, failed, could not run and not applicable, in total and by category, and keeps its results in `dqd/out/dqd_results.json`. The dashboard's own failures are reported but do not fail the run, because some of them concern tables that the conversion does not build yet.

In the fast profile, the report marks the dashboard as not run, and you can run it afterwards in the Broadsea environment. The output folder holds `testbed_cdm.duckdb`, with every table in its `cdm` schema, and `csv/`, with one file for each table and `load_postgresql.sql`. The load script replaces its own `testbed` schema and leaves the database's `cdm` schema, with its full vocabulary, untouched. From the output folder, load the tables, then run the dashboard's image with the script that the testbed wrote:

```sh
(cd csv && psql -h 127.0.0.1 -p PORT -U postgres -f load_postgresql.sql)
docker run --rm --platform linux/amd64 -e PG_PORT=PORT -v "$PWD/dqd/run_dqd.R:/run_dqd.R:ro" \
  -v "PASSWORD_FILE:/run/pg-password.txt:ro" -v jdbc-drivers-data:/jdbc -v "$PWD/dqd/out:/out" \
  broadsea-broadsea-run-dqd Rscript /run_dqd.R
```

## SQL Server

With `--engine sqlserver`, the testbed also runs `tools/sqlserver/harness.py` at the same size, with the sample vocabulary and without the safeguards. The report keeps the harness's summary and says which stages ran on which engine. The reconciliation and the dashboard's inputs always come from DuckDB.

The report carries a check named "release equivalence", which reads the harness's summary. The check has passed only when the harness reports that the release script, executed on SQL Server, ran to its end and produced the same derived rows as DuckDB in every OMOP object compared, and that every expectation of the planted scenarios was met on both engines. If the harness reports any difference, or gives no summary, the check has failed. With `--engine duckdb`, the check is recorded as "not run on SQL Server", which the fast profile accepts and the full profile does not.

## What done means for this milestone

The quality milestone is done when the full profile passes on the invented world, with `--engine sqlserver`: every step runs cleanly, every gate, count and planted scenario passes, the reconciliation finds no unexplained discrepancy, the release script carries every step, the Data Quality Dashboard runs, and release equivalence passes, all recorded in `report.json`. A fast profile that passes is a good sign but not that milestone. `core/tests/test_testbed.py` checks the fast profile at 60 rows, and checks the rules of the full profile and of release equivalence against stand-in results.
