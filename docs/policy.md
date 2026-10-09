# The static policy

`core/schemalyser/policy.py` reads the final text of a script, exactly as the database analyst will run it, and decides what the script may do to the hospital's database. It reads the text statement by statement with sqlglot in the SQL Server dialect, checks it against an allowlist, and derives the script's execution class from the rules it passes and fails. It never relies on how the script was made: a script that Schemalyser compiled and one that a person wrote by hand are read in the same way, and anything the parser cannot understand is rejected.

The policy is one of the three reports that `docs/contract.md` keeps apart. It says whether a script may run and in what class. It says nothing about whether the hospital schema can answer the question, which is the feasibility report's to say, or whether the question gives the right answer on made-up rows, which layer 4 establishes.

## The policy's version

`POLICY_VERSION`, now `"1"`, is recorded in every report the policy writes, as `policy_version`, so that the package's manifest can record which policy derived the class. Any change to a rule, a threshold or a class makes a new version. Under section 3 of the contract, a change of version voids every class that an earlier version derived, and the class has to be derived again.

## The rules

Each rule is reported as passed, or as failed with the fragment of the script that broke it. In the report, each rule also says whether it applies to the script; a rule that does not apply has `passed` set to null and is never counted as a failure.

- **parse.** Every statement is one that the parser understands. A statement that sqlglot cannot read, or reads only as an opaque command, fails.
- **statements.** The script holds only SELECT statements, the session settings that the compiler writes (`SET NOCOUNT ON`, the read-uncommitted isolation level, the lock timeout, the low deadlock priority, `SET ANSI_WARNINGS OFF` and `SET XACT_ABORT ON`), and the cohort's temporary table: `SELECT ... INTO #name`, `CREATE TABLE #name`, `INSERT INTO #name SELECT`, `ALTER TABLE #name ADD PRIMARY KEY`, `DROP TABLE #name`, and the guard that drops a temporary table if it exists.
- **dynamic.** The script runs no EXEC, no dynamic SQL and no procedure, uses no OPENQUERY, OPENROWSET, OPENDATASOURCE or OPENXML, and holds none of WAITFOR, SHUTDOWN, RECONFIGURE, GRANT, REVOKE, DENY, BACKUP, RESTORE, DBCC or KILL outside a string.
- **names.** The script names no other database and no linked server, and no schema other than those it is allowed (dbo, unless the caller names others).
- **tables.** The script reads only the tables that the hospital schema names, temporary tables, its own common table expressions, and the server's own records of its tables in INFORMATION_SCHEMA and sys.
- **functions.** The script uses only aggregates, date arithmetic, CAST, COALESCE and ISNULL, NULLIF, ROUND, FLOOR, CEILING, ABS, CASE and CONCAT, and uses ROW_NUMBER, LEAD and LAG only with a window.
- **joins.** The script has no cross join, comma join or APPLY, and every join carries an equality between two named columns, one of them of the table joined.
- **cohort_keys.** In a SELECT that starts from the cohort, every join carries an equality of bare columns with a source already reached from the cohort.
- **large_from_cohort.** Every large table is reached from the cohort: it is joined, never read first, in a SELECT that starts from the cohort, by equality with a column already joined.
- **cohort_cap.** Every statement that fills a temporary table carries TOP (n), with n at most 5,000.
- **cohort_period.** Every statement that fills a temporary table tests a column against a first and a last date.
- **series.** A script that reads a large table reaches it through the series described below.
- **counts_only.** No result returns a value of a large table that is not aggregated.

A table is large when the tables and columns query gave it 10,000,000 rows or more, or gave no figure for it at all.

## The series

The contract requires that anything reaching a large table is compiled as a series of bounded operations of increasing size, each of which the database analyst can stop after. The compiler writes each step of the series in part 1 of the script as one statement, with a comment line of its own immediately before it, in this form and this order:

    -- series: count
    -- series: coverage
    -- series: rows

The policy recognises a marker only in exactly that form, standing between two statements. A comment line that begins `-- series:` in any other form, a marker that stands inside a statement, and two markers before the same statement each fail the rule.

The policy does not trust the comment alone. For a script that reads any large table, it finds the first statement that does so and checks the markers that come at or before it.

1. **The count.** The statement after `-- series: count` is a SELECT that returns its result, with a COUNT among the columns it returns, that starts from the cohort, and that tests a column against both a first and a last date.
2. **The coverage.** The statement after `-- series: coverage` is a SELECT that returns its result, with a COUNT among its columns, that starts from the cohort, and that joins a link: a join on an equality of two columns, or an EXISTS whose subquery tests one. It counts the cohort's rows that have a matching row through each link the question uses.
3. **The rows.** The statement after `-- series: rows` is a SELECT that returns its result. It is the first statement allowed to read a large table, and any later statement may read one too.

The markers at or before the first read of a large table must be exactly count, coverage and rows, in that order. If the script reads a large table with no marker before it, if the markers are out of order, or if a large table is read before the rows step, the rule fails with a fragment that says which of these happened. A script that reads no large table needs no series, and its report says that the series was not required.

The report's `series` field gives whether the series was required, each marked step with the number of its statement, and the first statement that reads a large table with the tables it reads.

## The classes

The class comes from the text of the script, never from a setting.

- **A, metadata only.** The script reads only the server's own records of its tables.
- **B, bounded validation.** The script reads small tables, or large tables reached from a bounded cohort through the series, and returns counts only. Before the first run, the plan must be reviewed and a time limit set.
- **C, large clinical extraction.** A result returns rows of a large table, the cohort is above the cap or has no period, or the script reaches a large table without the series. The script needs the database team's approval, and they run it in isolation and watch it.
- **D, not permitted.** Any other rule fails, and the script is not to be run.

Only the four rules cohort_cap, cohort_period, series and counts_only can make a script class C. A failure of any other rule makes it class D, whatever else it passes.

## What the report holds

`policy.check(sql, tables, sizes)` returns the report as a dictionary, which the package writes as `safety-report.json`. It holds `policy_version`; `purpose`; `rules`, each with its `id`, its wording, whether it `applies`, whether it `passed`, and its `fragments`; `execution_class` and `class_says`; `outcome`, which is passed only when no rule that applies has failed; `cannot_establish`, the sentences that say what the policy could not establish about this script; `series`; the large tables, the clinical tables and the server's records that the script reads; the number of statements; and the thresholds it used.

## A script with no cohort

A script that fills no cohort can still be checked. Where it reads the hospital's tables, the report says in `cannot_establish` that the policy cannot establish that what the script reads is bounded by a capped cohort and a period. The rules apply as they stand, so such a script that reaches a large table fails large_from_cohort and is class D.

## A conversion step and the release script

A conversion step, or a gate of the release script, is checked with `purpose="conversion"`:

    policy.check(step_sql, tables, sizes, schemas=("dbo", "omop"), purpose="conversion")

Here `tables` holds the source tables that the hospital schema names and the target tables that the step reads, such as the core's visits and the mapping rows, and `schemas` names the schema of those target tables. A conversion step is one SELECT, or several, with no cohort. The policy then applies every rule except large_from_cohort, cohort_cap, cohort_period, series and counts_only, which do not apply to a step that reads its source whole and returns the rows it writes; the report marks each of those as not applying and says so in `cannot_establish`. Two allowances are made for a conversion alone. A step may use LEFT, RIGHT, SUBSTRING, UPPER, LOWER, TRIM, LEN, CHARINDEX, REPLACE and DATEFROMPARTS, and DENSE_RANK and RANK with a window. A join's equality may cast one of its columns to text, because a target table is joined on its source value. Any statement other than a SELECT, including a SELECT INTO, fails the statements rule. A step that passes is class C at best, because nothing bounds what it reads; one that fails a rule that applies is class D.

The release script as a whole is not given to the policy. It writes the anaesthesia layer's tables by design, with CREATE, DELETE, INSERT and EXEC statements and sqlcmd lines that no read-only allowlist admits, and `release.py` governs those through its own refusals. The read side of the release is checked instead: before `release.py` wraps them, each step's SELECT and each gate's SELECT are passed to `policy.check` with `purpose="conversion"`, and the release records each report, with its `policy_version`, beside the script. A step or gate of class D is refused, and the release script as a whole takes the class of its worst step, which is C at best.

The profile of a core OMOP database is to be checked in the same way, statement by statement, with `purpose="conversion"` for each statement that reads a table whole.
