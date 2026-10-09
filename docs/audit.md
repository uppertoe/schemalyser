# The audit's execution package

`core/schemalyser/audit.py` builds, from an audit and the saved hospital schema, the folder that the database analyst runs once on production.

    python -m schemalyser.audit build hospital-schema.schemalyser.zip AUDIT.sql --out FOLDER [--period FROM TO] [--decisions decisions.json]
    python -m schemalyser.audit plan FOLDER plan.sqlplan
    python -m schemalyser.audit status FOLDER [--schema hospital-schema.schemalyser.zip]
    python -m schemalyser.audit approve FOLDER --by NAME [--refuse] [--note TEXT] [--date YYYY-MM-DD]
    python -m schemalyser.audit export hospital-schema.schemalyser.zip SPECIFICATION.json --episodes EPISODES.csv --out FOLDER [--period FROM TO]

Without `--period`, the package covers the year that the hospital schema records. The decisions file lists the clinicians' decisions.

## The package

The package keeps three reports apart. The feasibility report (`feasibility.md`) says whether the hospital schema can answer the question. If the question needs parts that the role model does not describe, Schemalyser stops and writes no script. The answer on made-up rows (`expected-output.json`), with the planted cases, shows only the answer's shape. The safety report (`safety-report.json`, then `plan-review.json`) says what the script may do to the database.

`query.sql` is the script. Part 1 puts at most 5,000 anaesthetics of the audit's cohort in the period into `#cohort`, from the patients' and anaesthetics' tables, and reports whether it reached that limit. Part 2 is the audit, with every larger table reached from `#cohort` by key. `question.sql` is the question over the parts of the record from which the script was compiled, and `decisions.json` holds the clinicians' decisions. `specification.md` states the script in the database's own terms, `manifest.json` records what the next section lists, and `README.md` says what the clinician and the database analyst each do.

## The series

The script reaches a large table only through a series of three steps of increasing size, and the database analyst can stop after any of them. Each step is one statement, and the line just before it is a comment that names it, exactly as the policy reads it.

1. `-- series: count` marks a `SELECT COUNT` of the anaesthetics in `#cohort`, bounded by the first and last dates of the period.
2. `-- series: coverage` marks a `SELECT COUNT` over `#cohort` that counts, for each part that part 2 reaches, how many of the cohort's anaesthetics have a matching row through the part's link. The step follows each link only as far as its last table that is not large, so it reads no large table. Where a link begins at a large table, its coverage cannot be measured before the rows are read, and the script says so in a comment.
3. `-- series: rows` marks part 2, which is the first statement to read a large table.

The analyst runs part 1, which ends with the coverage step, and gives the two counts to the clinician before part 2 is planned or run.

## The manifest

`manifest.json` records:

- the hash of `query.sql`, and the hash and `schema_id` of the saved hospital schema it was compiled from;
- the role contract's version, and the hash of each part that the question reads;
- the question, the cohort's step, cap, columns and conditions, the period and the decisions;
- the execution class, the policy's outcome and the policy's version (`"not recorded"` where the policy states none);
- the permissions the script needs, which are SELECT on each table it reads and a temporary table in tempdb;
- the expected output's columns, which of them are counts, whether the result is counts only and how many rows it gave on made-up rows, with the cohort cap and the resource assumptions (the lock timeout, the time limit that the analyst sets, and the four safeguards below);
- the plan review's state and the hash of the plan it reviewed;
- the correctness report it carries, which is `expected-output.json`, by name and hash;
- `inputs`, the hashes of every file the package was built from, of the schema, of the contract's parts and of the policy's version;
- `approval`, which reads not approved until the database analyst records otherwise.

An export section's manifest also carries the specification's hash and format, the output class of every section, and the episode list's form, count and hash.

## Approval and voiding

The package is not approved for production until the database analyst records an approval, whatever the reports say. The analyst runs `python -m schemalyser.audit approve FOLDER --by NAME`, or adds `--refuse` to record a refusal, and the manifest records who gave it, the date, any note, and a hash of every input as it then stood. Schemalyser refuses to record an approval for a package that has changed since it was built, or for a script of class D.

`python -m schemalyser.audit status FOLDER` rechecks every hashed input: each file that the manifest records, the plan that was reviewed (kept in the package as `plan.sqlplan`), the parts of the role contract that the question reads, the policy's version, and the saved hospital schema when `--schema` names it. A change to any of them voids the plan review and the class, and returns the approval to not approved, which Schemalyser writes back into the manifest so that no approval outlives what it approved. A new plan review also returns the approval to not approved, because the approval was given for what the package held before it.

## The results package

A production run's output goes into a results package, which `core/schemalyser/results.py` writes from an approved execution package and the output file:

    python -m schemalyser.results write PACKAGE OUTPUT --out FOLDER [--reconciliation FILE] [--by NAME]

`results.json` records the approved query's hashes and approval, the cohort's definition, the coverage of the recording pathways that the feasibility report records for the period, the disclosure-control state (which counts were left blank, that none was rounded, and what that does and does not protect), the output's file and hash, and the reconciliation of a sample against the clinical record once there is one. The output itself is kept unchanged in `output/`. A results package is patient-derived material and stays inside the hospital. Schemalyser writes none for a package that is not approved or has changed, and none inside a public workspace.

## The export of a specification

`python -m schemalyser.audit export` takes a specification and its private episode list (`docs/specification.md`), compiles each section into a statement over the roles, and takes each through the role policy, the feasibility report and the compilation through the hospital schema to a package of its own in `FOLDER/sections/NAME/`. `FOLDER/export.json` records the outcome of every section: packaged with its class, refused by the role policy with the rules it broke, or declared in the catalogue without SQL. Without `--period`, a list of patient and date pairs gives the period that holds every anaesthetic the pairs can resolve to, and a list of anaesthetic keys takes the year that the hospital schema records. The count step of each section's series shows how many of the listed anaesthetics fall in the period.

## The policy

`core/schemalyser/policy.py` reads the final text of `query.sql` with sqlglot in the SQL Server dialect, and records each rule as passed, or as failed with the offending fragment.

- The script holds only SELECT statements, fixed session settings, and the filling, keying and dropping of the cohort's temporary table.
- It runs no EXEC, dynamic SQL or procedure, uses no OPENQUERY, and names no other database, linked server or schema.
- It reads only the tables that the hospital schema names, temporary tables and its own steps.
- It uses only aggregates, date arithmetic, CAST, COALESCE, NULLIF, ROUND, CASE, CONCAT and TOP, and uses ROW_NUMBER, LEAD and LAG only with a window.
- It has no cross join, comma join or APPLY. Every join carries an equality between two named columns, and every join out of the cohort is on key equality.
- Every large table, of 10,000,000 rows or more or of unknown size, is joined from a SELECT that starts from the cohort.
- A script that reads a large table reaches it through the series above, marked and in order.
- The cohort carries TOP (n) with n at most 5,000, and a first and a last date.
- No result returns an unaggregated value of a large table.

Anything that the parser cannot understand is rejected.

## The classes

The class comes from the text of the script, never from a setting.

- **A, metadata only.** The script reads only the server's own records of its tables.
- **B, bounded validation.** The script reads small tables, or large tables reached from a bounded cohort, and returns counts only. Before the first run, the plan must be reviewed and a time limit set.
- **C, large clinical extraction.** A result returns rows of a large table, the cohort is above the cap or has no period, or a large table is reached without the series. The script needs the database team's approval, and they run it in isolation and watch it.
- **D, not permitted.** Some other rule fails, and the script is not to be run.

The neonatal audit on the invented world is class B.

## The plan review

SQL Server cannot estimate part 2 until `#cohort` exists, so the database analyst runs part 1, which reads only small tables, then selects part 2, presses Display Estimated Execution Plan (Ctrl+L) and saves the plan as a `.sqlplan` file. `core/schemalyser/plan.py` reads that file. The review rejects a scan of a large table, a join without a predicate, or an estimated intermediate result above 10,000,000 rows. A memory grant above 1 GB, or a spool or sort over a large input, goes to the database team. A seek out of `#cohort` is encouraging but not conclusive. The review records the plan's hash and the hash of the script that it describes, and the package keeps the plan as `plan.sqlplan`. Any change to `query.sql`, or to any other hashed input, voids both the review and the class.

An estimated plan is an estimate from the statistics that SQL Server held at the time, and the plan that runs can differ. The review makes a runaway query, like the one that filled a test server's tempdb to about 96 GB, less likely, but cannot rule one out.

## What the hospital enforces

Schemalyser cannot stop a running script, so the hospital needs four safeguards of its own:

- a restricted account that can read the reporting tables and nothing else;
- a time limit, set in Management Studio;
- Resource Governor, where the SQL Server edition has it, to cap memory and CPU for that account;
- a reporting replica to run the script on, rather than the live clinical database.
