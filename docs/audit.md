# The audit's execution package

`core/schemalyser/audit.py` builds, from an audit and the saved hospital schema, the folder that the database analyst runs once on production.

    python -m schemalyser.audit build hospital-schema.schemalyser.zip AUDIT.sql --out FOLDER [--period FROM TO] [--decisions decisions.json]
    python -m schemalyser.audit plan FOLDER plan.sqlplan

Without `--period`, the package covers the year that the hospital schema records. The decisions file lists the clinicians' decisions.

## The package

The package keeps three reports apart. The feasibility report (`feasibility.md`) says whether the hospital schema can answer the question. If the question needs parts that the role model does not describe, Schemalyser stops and writes no script. The answer on made-up rows (`expected-output.json`), with the planted cases, shows only the answer's shape. The safety report (`safety-report.json`, then `plan-review.json`) says what the script may do to the database.

`query.sql` is the script. Part 1 puts at most 5,000 anaesthetics of the audit's cohort in the period into `#cohort`, from the patients' and anaesthetics' tables, and reports whether it reached that limit. Part 2 is the audit, with every larger table reached from `#cohort` by key. `specification.md` states the script in the database's own terms, `manifest.json` records versions, hashes, the class and the state of the plan review, and `README.md` says what the clinician and the database analyst each do.

## The policy

`core/schemalyser/policy.py` reads the final text of `query.sql` with sqlglot in the SQL Server dialect, and records each rule as passed, or as failed with the offending fragment.

- The script holds only SELECT statements, fixed session settings, and the filling, keying and dropping of the cohort's temporary table.
- It runs no EXEC, dynamic SQL or procedure, uses no OPENQUERY, and names no other database, linked server or schema.
- It reads only the tables that the hospital schema names, temporary tables and its own steps.
- It uses only aggregates, date arithmetic, CAST, COALESCE, NULLIF, ROUND, CASE, CONCAT and TOP, and uses ROW_NUMBER, LEAD and LAG only with a window.
- It has no cross join, comma join or APPLY. Every join carries an equality between two named columns, and every join out of the cohort is on key equality.
- Every large table, of 10,000,000 rows or more or of unknown size, is joined from a SELECT that starts from the cohort.
- The cohort carries TOP (n) with n at most 5,000, and a first and a last date.
- No result returns an unaggregated value of a large table.

Anything that the parser cannot understand is rejected.

## The classes

The class comes from the text of the script, never from a setting.

- **A, metadata only.** The script reads only the server's own records of its tables.
- **B, bounded validation.** The script reads small tables, or large tables reached from a bounded cohort, and returns counts only. Before the first run, the plan must be reviewed and a time limit set.
- **C, large clinical extraction.** A result returns rows of a large table, or the cohort is above the cap or has no period. The script needs the database team's approval, and they run it in isolation and watch it.
- **D, not permitted.** Some other rule fails, and the script is not to be run.

The neonatal audit on the invented world is class B.

## The plan review

SQL Server cannot estimate part 2 until `#cohort` exists, so the database analyst runs part 1, which reads only small tables, then selects part 2, presses Display Estimated Execution Plan (Ctrl+L) and saves the plan as a `.sqlplan` file. `core/schemalyser/plan.py` reads that file. The review rejects a scan of a large table, a join without a predicate, or an estimated intermediate result above 10,000,000 rows. A memory grant above 1 GB, or a spool or sort over a large input, goes to the database team. A seek out of `#cohort` is encouraging but not conclusive. The review records the plan's hash and the hash of the script that it describes, and any change to `query.sql` voids both the review and the class.

An estimated plan is an estimate from the statistics that SQL Server held at the time, and the plan that runs can differ. The review makes a runaway query, like the one that filled a test server's tempdb to about 96 GB, less likely, but cannot rule one out.

## What the hospital enforces

Schemalyser cannot stop a running script, so the hospital needs four safeguards of its own:

- a restricted account that can read the reporting tables and nothing else;
- a time limit, set in Management Studio;
- Resource Governor, where the SQL Server edition has it, to cap memory and CPU for that account;
- a reporting replica to run the script on, rather than the live clinical database.
