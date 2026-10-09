# The OMOP testbed

The testbed runs the whole conversion to OMOP on a synthetic world in one command, checks it, and writes one report. It adds no conversion logic of its own.

```sh
cd core
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python -m schemalyser.testbed run --world fixtures --out ../../testbed-out --rows 200
```

That command runs the fast profile, which is the default. Add `--profile full` for the full profile, which the next section describes, and `--vocabulary athena` or `--vocabulary sample` to choose the vocabulary, which the section after it describes.

Give an output folder outside the repository, or under `reference/` for a private world, because the report names the source tables. A run on the invented world at 200 rows takes about 25 seconds.

## The two profiles

The fast profile builds, converts, checks and reconciles the world on DuckDB, writes the release script and the dashboard's inputs, and passes when every judged check passes. It does not run the Data Quality Dashboard, and it records the dashboard and release equivalence as not run without failing for either.

The full profile does everything that the fast profile does, then loads the tables into the OMOP database and runs the Data Quality Dashboard, as the section on the dashboard below describes, and records its results under `dqd` in `report.json`. Both stages are mandatory in the full profile. If the dashboard cannot run, the outcome is "failed: the Data Quality Dashboard did not run". If the dashboard reports a failure that `dqd-expectations.json` does not permit, the outcome names that check, and if release equivalence has not passed, the outcome says so too. Release equivalence needs SQL Server, so the full profile can pass only with `--engine sqlserver`.

## The vocabulary

The fast profile uses the sample of five public concepts that the SQL Server harness uses, unless you pass `--vocabulary athena`. The full profile uses the pinned Athena vocabulary, which the testbed reads from `reference/athena/`, or from the folder that `SCHEMALYSER_ATHENA` names. If the download is not there, the full profile stops before it builds anything and says why. You can still run the full profile with the sample by passing `--vocabulary sample`, but the dashboard will then report every concept that the sample leaves at 0.

The Athena download is licensed, so it stays on this machine. The testbed never copies it into the output folder, and the report records only its release, read from the download's own `VOCABULARY.csv`, and the SHA-256 of that file. On the first run with a download, the testbed builds a working copy of the tables that the conversion's lookups read in `reference/athena-working-copy/`: CONCEPT, CONCEPT_SYNONYM, the small vocabulary tables, and the "Maps to" rows of CONCEPT_RELATIONSHIP. Building it from the release of 29 August 2026 takes about 20 seconds and 512 MB, and later runs reuse it while the download is unchanged. The derived mappings are then looked up in the whole release, and the CDM's own vocabulary tables receive only the concepts that its tables and the mapping table name, with their "Maps to" rows and synonyms, which takes about a second.

The dashboard reads the vocabulary from PostgreSQL. If the OMOP database's `cdm` schema holds the same release as the download, the testbed reuses it. If it holds another release, the testbed loads the dashboard's vocabulary tables from the download into its own schema, `testbed_vocabulary`, once, and reuses that schema on later runs. The report records which schema the dashboard read and the release it holds.

## What it does

The testbed builds the synthetic source into a fresh DuckDB database, creates the tables of OMOP CDM 5.4 from the published field list, loads the vocabulary that the run uses, plants the scenarios and runs every step of the conversion. It then checks each scenario against the rows that its `scenario.json` states, which were written by hand and are never taken from the output.

The reconciliation runs each step's own SELECT again with its output replaced by the row number of the table that the step starts from, first with every inner join made a left join and no WHERE, then with each join and condition restored in turn. Every source row that does not reach the target is put down to the join or condition that left it out. A gate removes no rows; it fails the run. A world's `testbed.json` names the steps that may write several rows for one source row, such as the blood pressure step; every other step is expected to write at most one. Without the file, a step that writes several rows for one source row is counted as an unexplained discrepancy, because nothing says whether it may.

The reconciliation then sorts the steps into four groups, by count and by name: the steps traced with every excluded row accounted for; the steps traced with the fan-out that `testbed.json` allows confirmed; the steps that could not be traced, such as those that aggregate or start from a union, each with its reason; and the steps or tables with a discrepancy that it cannot explain. The run can pass only when the last group is empty. The summary never counts an untraced step as reconciled, and on the invented world it reads in this form: "The reconciliation traced N of M steps and accounted for every excluded row in them; K steps could not be traced (names), so their rows are not reconciled."

It then runs the gates and counts, and confirms that the release script carries every step that ran.

## What it reports

`report.json` holds the versions of the tool, CDM, vocabulary and DuckDB; the world's checksums; the engine for each stage; each step; each scenario with its expected and found rows; the reconciliation by step, source table and target table, with its coverage; the release script; the dashboard's inputs, and its results in the full profile; every check, release equivalence among them, with its outcome; and a plain summary. Each scenario names the steps that write the OMOP tables its expectations read, and each table with an unexplained discrepancy names the steps that write it, so that a failure leads to the SQL behind it. `report.md` sets out the same for a person.

## The Data Quality Dashboard

The dashboard needs R and its package, which the Broadsea image `broadsea-broadsea-run-dqd` holds; Broadsea builds it with its `dqd` profile. In the full profile, the testbed runs the two commands below itself. It needs `psql`, Docker with that image, the OMOP database listening on 127.0.0.1 at port 55440, or at the port that `SCHEMALYSER_PG_PORT` names, and the database's password in `reference/pg-password.txt`, or in the file that `SCHEMALYSER_PG_PASSWORD_FILE` names. If any of these is missing, the report says which, and the full profile does not pass. Each of the dashboard's queries may run for 300 seconds, or for the number of seconds that `SCHEMALYSER_DQD_CHECK_SECONDS` names. After that, PostgreSQL cancels the query, the dashboard records the check as an error and moves on, and the report counts that check as did not finish, naming its table, field and, for a concept check, its concept. If the whole dashboard has not finished after an hour, the testbed stops its container and the report says so. On a Mac, the testbed keeps the machine awake while the dashboard runs, because a Mac that sleeps pauses Docker, and a check then appears to stall for as long as the machine slept. A closed lid on battery still sends the machine to sleep, so the report records any time asleep under `paused_seconds`. A stall of 57 minutes in `plausibleValueLow` on the stand-in world on 9 October 2026 was exactly such a sleep: run again, the same check took seven seconds. When the dashboard runs, the report counts its checks as passed, failed, could not run, did not finish and not applicable, in total and by category, and keeps its results in `dqd/out/dqd_results.json`. Each check that failed or could not run is then set against the world's expectations, as the next section describes.

## The dashboard's expected failures

`fixtures/dqd-expectations.json` lists the dashboard failures that the invented world permits, and it is kept under version control so that a change to it is reviewed like any other change. Each entry names the dashboard's check (`check`), the CDM table (`table`) and, where it matters, the field (`field`), each as a name or as a pattern with `*` and `?`, and gives the reason in a sentence (`reason`). An example is the entry for `measurePersonCompleteness` on `DRUG_ERA`, which is permitted because the conversion does not build the era tables.

In the full profile, the testbed reads the file named with `--dqd-expectations`, or else the `dqd-expectations.json` in the world's folder, or else the one in the conversion's folder. A check that failed or could not run and that matches an entry is reported as expected, with its reason. Any other such check fails the full profile, and the outcome names it. A world without the file permits no failure. Add an entry only for a table that the conversion does not build or a vocabulary table that it does not write; a failure in a table that the conversion builds is a finding to fix, not to excuse. The report lists the unexpected failures first and then the expected ones, grouped by reason.

In the fast profile, the report marks the dashboard as not run, and you can run it afterwards in the Broadsea environment. The output folder holds `testbed_cdm.duckdb`, with every table in its `cdm` schema, and `csv/`, with one file for each table and `load_postgresql.sql`. The load script replaces its own `testbed` schema and leaves the database's `cdm` schema, with its full vocabulary, untouched. From the output folder, load the tables, then run the dashboard's image with the script that the testbed wrote:

```sh
(cd csv && psql -h 127.0.0.1 -p PORT -U postgres -f load_postgresql.sql)
docker run --rm --platform linux/amd64 -e PG_PORT=PORT -v "$PWD/dqd/run_dqd.R:/run_dqd.R:ro" \
  -v "PASSWORD_FILE:/run/pg-password.txt:ro" -v jdbc-drivers-data:/jdbc -v "$PWD/dqd/out:/out" \
  broadsea-broadsea-run-dqd Rscript /run_dqd.R
```

## SQL Server

With `--engine sqlserver`, the testbed also runs `tools/sqlserver/harness.py` at the same size, with the same vocabulary as the DuckDB run and without the safeguards. With Athena, the harness reads the testbed's working copy. The report keeps the harness's summary and says which stages ran on which engine. The reconciliation and the dashboard's inputs always come from DuckDB.

The harness writes `summary.json` beside its printed report: for each OMOP object compared, the row counts on both engines, a SHA-256 of its normalised rows in a fixed order on each, and whether they agree; each core step's rows on both engines; each planted scenario's outcome on each engine, with its expectations; each gate's outcome on both engines; the counts, check rows, safeguards and target queries that differ; and the versions of SQL Server, DuckDB, sqlglot and Python. It names no folder of the machine.

The report carries a check named "release equivalence", which reads `summary.json`. The check has passed only when the harness reports that the release script, executed on SQL Server, ran to its end and produced the same derived rows as DuckDB in every OMOP object compared, and that every expectation of the planted scenarios was met on both engines. If the harness reports any difference, or gives no summary, the check has failed. If the harness wrote no `summary.json`, the check reads its printed summary instead and says so in its detail. With `--engine duckdb`, the check is recorded as "not run on SQL Server", which the fast profile accepts and the full profile does not.

## What done means for this milestone

The quality milestone is done when the full profile passes on the invented world with the pinned Athena vocabulary and `--engine sqlserver`: every step runs cleanly, every gate, count and planted scenario passes, the reconciliation finds no unexplained discrepancy, the release script carries every step, the Data Quality Dashboard runs with no failure that the expectations do not permit and no check that did not finish, and release equivalence passes, all recorded in `report.json` with the vocabulary's release. A fast profile that passes, or a full profile run with the sample, is a good sign but not that milestone. `core/tests/test_testbed.py` checks the fast profile at 60 rows with the sample and with a small download in Athena's layout, and checks the rules of the full profile, of the vocabulary option, of the per-check time limit, of the dashboard's expectations and of release equivalence against stand-in results, among them a constructed `summary.json`.

`docs/testbed-example/` holds the `report.json` and `report.md` of a run of the full profile on the invented world at 50 rows with `--engine sqlserver` and the Athena release v5.0 of 29 August 2026. The run took about eight and a half minutes, of which the dashboard took five and a half, and the full profile passed. Release equivalence passed, and the dashboard ran 2,374 checks, of which 1,188 passed, 6 failed, 33 could not run and none failed to finish. Each of the 39 checks that failed or could not run is permitted by the expectations, because each is in a table that the conversion does not build or a vocabulary table that it does not write.

An earlier run on the same world had seven failures that the expectations did not permit, each in a table that the conversion builds, and each was fixed in the invented world rather than excused:

- Five infusion periods in three planted scenarios named a medicine that the invented source did not define, and 100 readings had no observation type, because at 50 rows the generator left the type empty when the check results listed only the mean pressures that it writes itself. The planted scenarios now define their medicines, and such readings now take the types that the site rules name.
- Twelve of the invented procedure names, such as "REPAIR OF CLEFT PALATE", matched no standard procedure in Athena by exact name, or matched several. Each is now named as one standard procedure is named, such as "PRIMARY REPAIR OF CLEFT PALATE".
- The invented world assigned procedures and diagnoses without regard to sex, so the dashboard found, for example, an orchidopexy recorded for a female patient. The reference lists now mark the procedures and diagnoses that belong to one sex, and the generator gives each only to a child of that sex.

Anyone can reproduce the run with this command from `core/`:

```sh
uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python -m schemalyser.testbed run --world fixtures --rows 50 \
  --profile full --engine sqlserver --out ../../testbed-out
```
