# The examination of a question package: a design

Written on 14 October 2026 against the branch `naive-pass` at commit 47fbae9. This is a design for step 5 of `docs/roadmap.md`, with the parts of steps 2, 3, 6 and 7 that the examination depends on. It names no hospital's table and quotes nothing from `reference/`.

## 1. What the examination is for, and the one rule it keeps

A question package (`schemalyser-question/1`, `ask/internal/question/package.go`) is written by a language model from a clinician's free text. The examination decides how far a package may advance, and it grants each status only on evidence that the model could not have produced or seen. The rule that governs every stage is that the model never sees, and can never write, the material that judges it: the planted cases' expected answers, and the results of the private stages, stay with the examiner.

Three facts about the repository shape the design.

- The repository is public, and `fixtures/held-out/` is tracked in it. "Held out" today means held out of the public workspace (`core/schemalyser/workspace.py`, `DENIED` at line 46 and `DENIED_FILES` at line 49) and of the Ask service, whose image copies only `contract.json` (`ask/Dockerfile`). It does not mean held out of a model trained on public code. The expected answers that judge model-written questions, and above all those of the ten-question measurement, must therefore live in a private root outside the repository, beside `reference/`, with only their hashes committed.
- The planted neonates' expectations are in a tracked public file, `core/schemalyser/rolemodel/planted_neonates.json`, and are stripped only on export (`workspace.planted_inputs`, line 379). That is adequate for the neonatal audit, which a person wrote, and inadequate as a model for free questions.
- The package's `specification` is free-form JSON in the clinician's terms (`Package.Specification` is a `json.RawMessage`; the prompt in `ask/internal/prompt/prompt.go` asks only for cohort, outcome and result). It is not the export format `schemalyser-specification/1` of `core/schemalyser/specification.py`. Section 6 proposes a structured form so that an expected answer can be derived from it.

## 2. The stages and what each catches

The examination runs six stages in order. A stage that fails stops the stages after it, except that the metamorphic checks and the planted cases both run when synthetic execution passes, so that one report shows both.

| Stage | Where it runs | What it catches | Status it contributes to |
| --- | --- | --- | --- |
| 1. Static validation | Public; service and examiner | A package that is not well formed, SQL outside the contract or the dialect, a part above its admission, a hash that does not match | well formed |
| 2. Synthetic execution | Public; examiner | SQL that does not run, returns the wrong shape, is not deterministic, or returns nothing on a plausible world | synthetic tests passed |
| 3. Metamorphic checks | Public; examiner | Logic that depends on unrelated patients, counts a duplicate the specification collapses, or links by time instead of by key | synthetic tests passed |
| 4. Planted cases with held-out answers | Private to the examiner | A wrong answer on cases written to sit on each side of every decision | synthetic tests passed |
| 5. Hospital feasibility | Private to the hospital's project | A question the hospital schema cannot answer, or whose compiled script is of a class that cannot run | hospital mapping supported |
| 6. Production reconciliation | Private to the hospital; a person | A question that runs but disagrees with the clinical record | production validated |

### Stage 1, static validation

This stage reads the package alone, with the contract, and needs no data.

- The service's own checks, which already run in `question.Parse` (`package.go`, lines 70 to 140): valid JSON, a title, a specification, a single SELECT, no forbidden word, no invented `role_` name, proposals in the contract's shape.
- The core's role policy, `rolepolicy.check` (`core/schemalyser/rolepolicy.py`, line 206), which parses with sqlglot and checks tables, columns, functions, kinds and capabilities. Today it refuses every draft view at line 124; section 8 says what changes.
- The form of the two-part script, `audit.public_form`, as `workspace.check_folder` applies it (`workspace.py`, lines 505 to 527), so that the static result and the workspace import's `malformed`, `requires_private_review` and `accepted` remain one function.
- The portable dialect rule of section 9, which is new.
- The structured specification of section 6, checked for shape: every part, kind and flag it names is a word of the contract, the result's declared columns are exactly the columns the SQL returns, and every decision that the examiner needs (cohort bounds, the duplicate rule, the window) is present.
- The package's hash recomputed from its content, the contract version equal to the examiner's, and the parts used equal to the parts the SQL reads.

The stage catches malformed packages and invented columns cheaply, and its report can go back to the model in full, because nothing in it is held out.

### Stage 2, synthetic execution

The question runs on the role-level shadow built from a seed (section 3), twice, with two seeds and with the rows inserted in two different orders. The stage fails if the SQL does not translate or does not run, if the columns are not the declared columns, if the two runs at the same seed in different orders give different answers (which catches a ROW_NUMBER or TOP without a total order), or if a question whose result is a count gives zero or nothing on a world that the generator guarantees holds qualifying anaesthetics. This stage catches runtime and dialect errors and nondeterminism. It says nothing about correctness.

### Stage 3, metamorphic checks

Section 5. These need no expected answer: each compares the question's answer on two worlds whose relation the specification fixes. They catch the commonest structural mistakes without anyone writing a case.

### Stage 4, planted cases with held-out answers

Section 4. A small world of planted rows, written for this question by a person from its decisions, with the whole expected result set written by hand and held out. The answer is compared as a multiset of rows with the comparison of `equivalence.compare_answers` (`core/schemalyser/equivalence.py`, line 113), which already rounds numbers to twelve significant figures and keeps duplicates. This is the only stage that checks the answer itself.

### Stage 5, hospital feasibility

This stage runs inside the hospital's project and never reports back to the service. It is the import that exists (`workspace.import_question`, line 549), extended to read a package: the feasibility report against the saved schema says whether every part, column and kind the question uses is bound and at what readiness; the question is compiled through the schema; the compiled script passes `policy.py` at class B, or at class C with the database team's approval; and the question run on the map's shadow (`roleshadow.run_check`, line 1065, which writes invented rows into tables shaped as the map's bindings name them) gives the same answer as the same question run on those role rows directly. That last comparison is new and small: `run_check` already builds both sides for the standard counts and the neonatal audit.

### Stage 6, production reconciliation

A person runs the compiled package on production, draws a sample of anaesthetics from the cohort, and reconciles each one's contribution to the answer against the clinical record. The record of that reconciliation, with who did it, the sample size and each discrepancy and its explanation, is journalled in the saved schema's project. No code grants this status.

## 3. Running question_sql on the role-level shadow, beyond the three contract parts

### What exists

- `roleshadow.role_shadow` (`core/schemalyser/roleshadow.py`, lines 190 to 217) creates a DuckDB table for every view of the contract, drafts and mapping views included (lines 197 to 202), with the column types of `ROLE_TYPES` (line 119). It fills only `role_patient`, `role_anaesthetic` and `role_reading`, from `generated_rows` (lines 134 to 187), adds the planted neonates and the planted concept rows when asked (lines 207 to 212), and fills any other view only from its `extra` argument (lines 213 to 214). A draft part is therefore an empty table unless the caller supplies its rows.
- `duckdb_runner` (lines 46 to 60) translates a T-SQL question with `translate.to_duckdb` and runs it. It passes no `whole_columns`, so a division of whole numbers is not rewritten (section 9).
- I ran a question over `role_fluid` on `role_shadow(anaesthetics=0, with_planted=False, extra=...)` and it returned the expected sum; `rolepolicy.check` refused the same question at the tables rule. The machinery runs drafts already; only the policy and the generator are missing.
- `further_rows` (lines 981 to 1027) generates rows for every further view, but only inside the map's shadow: it needs a sitting's state for its kind codes (`state.codes`, line 983) and the map's roles, and its rows are generic, two per anaesthetic, three and ten minutes after the start, with a random kind.
- `role_rows` (lines 948 to 978) adds pairs of anaesthetics of one patient in one encounter, which is the shape the third metamorphic check wants.
- The role scenarios of the conversion (`convert.py`, lines 725 onward, and line 820) already run on a shadow that holds only a scenario's rows, of any public view, and compare the whole result with held-out rows. That is the pattern stage 4 follows.
- The `rehearse` command (`core/schemalyser/rolemap/__main__.py`, lines 118 to 120) runs `roleshadow.result`, which needs the neonatal audit's `banded` step (`band_years_query`, lines 68 to 80), so it fails with a `MapError` on any other question. `expected_output` (line 1304) catches that error and falls back to the plain rows.

### What is needed

1. **A contract-driven generator for the further parts**, `generated_further(seed, anaesthetics, patients, model)` in `roleshadow.py`, beside `generated_rows`, with no sitting. It reads the contract's vocabularies for every kind column, rather than `state.codes`, and it writes rows that are plausible enough for a clinical question to find something: a stay for each anaesthetic or pair of anaesthetics, with `role_anaesthetic_detail.stay_key` pointing to it; ASA grades from 1 to 5 and weights and heights by age; fluids with both `amount` and `running_total` forms and red cells in a share of anaesthetics; drugs with `action` words and units from the planted concepts; events, devices and staff inside the anaesthetic's times; laboratory results and diagnoses on the patient and the stay, some before and some after the anaesthetic; and a share of rows with an `amends_key` pointing to an earlier row and a different `source_kind`. `further_rows` keeps its place in the map's shadow; the two share a per-type value function. About 250 lines with its tests.
2. **`role_shadow` gains `further=True`**, which fills every further view from that generator. About 15 lines.
3. **`duckdb_runner` passes `whole_columns`** from the contract's types `whole`, `flag` and `flag_or_empty`, so that a division of whole numbers gives SQL Server's answer. About 10 lines; the change affects existing callers and needs the capability tests rerun.
4. **A general rehearsal**: `python -m schemalyser.rolemap rehearse --question FILE` runs any question and prints the rows, without the banded reading. About 30 lines.

## 4. Planted cases per question, with answers derived independently and held out

### How a case is written

The cases are written after the specification and its decisions are settled, and before the question's SQL is run on them. The author is a person: the owner, or the one clinician. A second model may draft rows from the specification and the decisions, provided it never sees `question_sql` and a person reads every case before it is used; a drafting model shares the clinician's words with the writing model and can share its misreading, so the person's reading is the safeguard, not the second model.

Every decision in the package yields at least one case on each side of its boundary. For a typical question that means:

- age at the boundary in days, by calendar date (27 and 28 days for a neonatal cohort);
- the period's first and last day, and an anaesthetic one minute outside;
- a test patient, an anaesthetic with no start, one with no stop, and one with a stop before its start;
- for each outcome part, a row at exactly the window's edges, a row linked by key to an anaesthetic but timed outside it, and a row inside the anaesthetic's times but linked to another anaesthetic;
- a value not accepted, a value empty with text held, and two rows at the same instant;
- for a part with `amends_key`, a row amended by a later one;
- for `role_fluid`, a `running_total` series beside single amounts, since summing a running total is the most likely clinical error over that part;
- a patient whose outcome is absent, so that the answer must say unknown rather than zero where the decisions say so.

The case is written as two files, in the existing shape of `fixtures/questions/saturation_below_90/rows.json` (`{"description", "roles": {view: [row, ...]}}`, rows in contract column order) and `fixtures/held-out/questions/saturation_below_90/expected.json` (`{"question", "says", "per_<grain>": {"columns", "rows"}, "answer": {"says", "columns", "rows"}}`). The expected file's `says` is written sentence by sentence from the description, as the saturation file's is, so that a reader can check each number without running anything. Both the per-grain rows and the final answer are given, because a wrong final answer is far easier to diagnose against the per-anaesthetic rows.

### Deriving the answer independently

The expected answer is written by hand. Where the specification is inside the oracle's subset (section 6), the oracle also computes it from the rows in Python, and the two are compared before the question is examined; a disagreement is resolved by a person and is evidence about the case, the oracle or the specification, never about the model's SQL. The SQL is never run to produce or check an expected answer.

### Holding the answers out

- Catalogue questions and the transfusion question keep the present layout: rows public under `fixtures/questions/NAME/`, answers under `fixtures/held-out/questions/NAME/`, which the workspace export already refuses.
- Free questions from Ask, and every question of the measurement in section 11, keep both rows and answers in a private examiner root, by default `reference/examination/questions/NAME/` or the folder that `SCHEMALYSER_EXAMINATION` names. The repository commits only `examination-register.json`, listing each question's name, the SHA-256 of its rows and its answers, and the date written, so that a case cannot be changed after a model has been examined against it without the register showing it. `workspace.DENIED` gains the private root, as a belt to the braces of its being untracked.
- The Ask image never holds the examiner. The examiner is a separate process (section 10), and only its verdict returns.

### Defeating hard-coded answers

Because catalogue rows are public, a model could in principle special-case a planted key or date. The examiner therefore applies a fresh transform to every case before it runs it: every value of a column of type `key` is replaced through a random bijection, and every date and time is shifted by the same random whole number of days, with the expected answer transformed in the same way (keys and dates in the answer columns mapped, durations and counts unchanged). A question that names a literal key or date in the planted range fails. The role policy also gains a fragment for any string literal compared with a key column, which no clinical question needs. About 80 lines.

### Size

A reader of case folders and the comparison, `examination/planted.py`, about 150 lines, mostly lifted from `convert.py`'s role scenarios and `core/tests/test_capabilities.py` (`_comparable`, `_rows`). The work per question is the writing itself: about two hours for a person for a question of the saturation question's size.

## 5. The three metamorphic checks, over the shadow

Each check takes the shadow of stage 2, applies a mutation inside a DuckDB transaction (`BEGIN`, the mutation, the question, `ROLLBACK`), and compares two answers with `equivalence.compare_answers`. Mutations are applied to a sample of rows chosen by seed, twenty for each check, so that the check is not a single lucky case. The outcome parts are the parts used other than `role_patient` and `role_anaesthetic`, and their link column comes from `specification.link_column` (`specification.py`, line 156).

### Check 1, an unrelated patient added changes nothing

The examiner adds a whole patient: a `role_patient` row, an anaesthetic, and rows of every outcome part linked to it, copied from an existing in-cohort anaesthetic with new keys. The patient is made unrelated in one of the ways the specification says excludes it: `is_test = 1`, which every question must exclude by the prompt's rule; an anaesthetic with no start; an age outside the cohort's bounds; or a start outside the period. The answer must be identical. For a result whose grain is the anaesthetic or the patient, a second form adds an in-cohort patient and requires every existing row of the answer to be unchanged and exactly one row added. This catches a missing exclusion, a join that fans out across patients, and an aggregate taken before the cohort is applied.

### Check 2, duplicates handled as the specification says

The structured specification carries a `duplicates` decision for each outcome part: `collapse` (two rows of one kind at one instant count once, with a stated rule for which value is kept, as the saturation question keeps the lower) or `count` (each row counts). The examiner duplicates a sampled outcome row with a new key and, for a part that has them, a different `source_kind` and `documented_time`. Under `collapse` with the kept value unchanged, the answer must be identical. Under `count` the answer must change, which is weaker, and the planted cases carry the exact expectation. For a part with `amends_key`, a third form inserts a corrected row that amends the sampled one; where the specification says amended rows are replaced, the answer must equal the answer with the sampled row's value replaced. This check runs also with the shadow's rows inserted in reverse order, so that a tie broken by physical order shows.

### Check 3, a reading moved to another encounter leaves the cohort

The examiner takes a sampled outcome row linked to an in-cohort anaesthetic A and computes two worlds: in one the row is deleted; in the other its link is changed to an anaesthetic B of the same patient, made out of cohort by a start outside the period, while the row's time is left inside A's times. The two answers must be identical. This catches the most dangerous structural error over the roles, which is joining an outcome to an anaesthetic by time rather than by key, against the contract's rule. It applies to the parts linked by `anaesthetic_key`: readings, events, drugs, devices, fluids, staff, techniques, operations and notes where linked. Laboratory results and diagnoses belong to the patient and the stay, and a question chooses its own window around the anaesthetic; for them the check moves the row to another stay of the same patient, outside the specification's window, and requires the same identity.

### Size and module

`examination/metamorphic.py`, about 250 lines, with a test for each check that runs a correct question and a deliberately wrong one (no test-patient filter; a join on time; a sum over running totals) and asserts that only the wrong one fails. The mutations need the stay and same-patient pairs that the generator of section 3 writes.

## 6. Using the specification to derive an expected answer

### A structured specification

The prompt today asks for cohort, outcome and result in the clinician's terms and nothing more. The package's specification gains a structured part beside the words, `examinable`, which the model writes and the clinician never sees as such:

    "examinable": {
      "cohort":  {"grain": "anaesthetic", "age_days": [0, 28], "period": ["2024-01-01", "2024-12-31"],
                  "exclude": ["test_patient", "no_start", "stop_before_start"], "requires": [{"part": "role_operation", "flags": {}}]},
      "outcome": {"part": "role_fluid", "kinds": ["red_cells"], "forms": ["amount"], "window": {"from": "start", "to": "stop", "to_minutes": 60},
                  "measure": "sum", "of": "volume_ml", "per": "weight_kg", "duplicates": "collapse", "amended": "replaced",
                  "absent": "zero"},
      "result":  {"statistic": "median", "over": "anaesthetic", "by": [], "columns": ["anaesthetics", "median_ml_per_kg"]}
    }

The vocabulary of `measure` is small: `exists`, `count`, `sum`, `min`, `max`, `first_time`, and a capability of the catalogue by name and version with its parameters, as `specification.py` already accepts in its `derived` sections. The vocabulary of `statistic` is `count`, `proportion`, `sum`, `median`, `rows`, with `by` naming bands that are themselves written as a table.

### The oracle

`examination/oracle.py` evaluates the structured specification over the planted rows in Python, with no SQL: it filters the cohort, keeps the outcome rows by key link, kind, form, flags and window, applies the duplicate and amendment rules, computes the measure per grain and the statistic. For a capability measure it uses the capability's SQL through `capability.fill` on a shadow that holds only the planted rows, which is independent of the model's SQL because a person wrote and tested the capability against its own held-out answers (`core/tests/test_capabilities.py`, line 66). For the episode and section part of a specification, `specification.compile` is a second, human-written generator of SQL over the roles (`specification.py`, line 848): the oracle's outcome rows per anaesthetic can be checked against the compiled sections with the cohort's anaesthetic keys as the episode list, which tests the oracle itself. About 400 lines, with a test that reproduces the saturation question's held-out answer from a hand-written `examinable`.

Beyond the planted world, the oracle allows a differential test on the generated shadow: the model's answer and the oracle's are compared on several seeds. This catches errors no case anticipated, and its failures are reported like a planted failure, without detail, because the generated worlds are reproducible from the seed and the seed is public.

### Where the specification cannot give the answer

- The oracle checks the SQL against the specification. If the model misread the clinician and wrote both the specification and the SQL to the misreading, the two agree and the oracle passes. Only the clinician's reading of the decisions, and cases written by a person from those decisions, catch that.
- Temporal logic beyond the vocabulary: a value that stands until the next reading, intervals of exposure, the first of two events within a time of each other, a sequence of transfers. Where a capability covers it, the oracle uses the capability; otherwise the expected answer is written by hand only.
- Free text in notes and findings, and any measure over `value_text`.
- Rows of episodes with derived columns the vocabulary cannot name.
- A package that waits for the contract, whose proposal has no rows to examine.

For these the package's `examinable` says `"oracle": "none"` with a sentence saying why, and stage 4 rests on hand-written answers alone.

## 7. Status levels, and exactly the evidence that grants each

A package's status is never a field the package asserts. `Package.Status` is outside the hash already (`hashOf`, `package.go` lines 205 to 217), and it stays a display. The status is derived from evidence records, each naming the package's hash, the contract version, the examiner's version and, where cases were used, the register's hash of the cases. The page shows the highest status whose evidence it holds and verifies.

| Status | Granted by | Evidence | Who can see it |
| --- | --- | --- | --- |
| drafted | The service, on parsing | The package and its hash | Anyone with the address |
| well formed | Stage 1 passed | `static-report.json`: every rule with its fragments, the policy version, the dialect version, the hash | Anyone; may go back to the model in full |
| synthetic tests passed | Stages 1 to 4 passed, with at least one planted case per decision, written before the first examination of this question, every part used at contract or rehearsed status, and, where the oracle covers the specification, the oracle's differential run passed | `examination-report.json`: the stages, the seeds, the metamorphic checks with their sample counts, the number of planted cases and how many matched, the register's hash of the cases, the examiner's version, and a signature over these by the examiner's key | The report without case detail is public; the case detail stays with the examiner |
| hospital mapping supported | Stage 5 passed inside a hospital's project, at synthetic tests passed or above | The private validation report beside the audit's package: the feasibility report with every part used bound at the state runs or better, the compiled script's policy class, and the compiled-versus-direct comparison on the map's shadow | The hospital only; the import returns only its status word, as today |
| production validated | Stage 6 recorded by a person | A reconciliation record in the project's journal | The hospital only |

A change of the contract version, the policy version or the examiner's version voids the statuses above well formed that were granted under the old one, as `docs/policy.md` already says of the policy's class. The workspace import's three words keep their meaning beneath this ladder: `malformed` is a failure of stage 1, and `accepted` and `requires_private_review` are both well formed, the second needing a person's review of its form before stage 5.

The roadmap's step 3 says a package below a given status cannot be compiled for production. I recommend that the line is synthetic tests passed: `audit.py` refuses to build an execution package for production from a question package whose examination report it cannot verify at that level.

## 8. What the policy must change to admit draft parts under "rehearsed"

1. **The contract.** `contractcheck.STATUSES` (`core/schemalyser/contractcheck.py`, line 30) becomes `("contract", "rehearsed", "draft")`. A rehearsed view carries an `evidence` object: the stand-in it was rehearsed on (by a neutral name, never a hospital's), the date, the equivalence counts that showed it identical row for row, and the commit. The checker refuses a rehearsed view without that object. `contract.json`'s `versioning` sentence is rewritten to say what each status means.
2. **The role policy.** `contract_views` (`rolepolicy.py`, lines 51 to 59) today gives columns only for views at contract status (line 54), so a draft view's columns are never checked: `_holds` returns true for a table it does not know (lines 179 to 184). It should return the columns of every view, with its status, so that the columns rule applies to drafts and rehearsed parts alike. The tables rule (line 124) changes to three outcomes: a contract view passes; a rehearsed view passes and adds `"ceiling": "synthetic tests passed"` to the report, which the status derivation reads; a draft view fails as now, unless the caller passes `admit="draft"`, which only the examiner's stages 1 to 3 use, so that a package over a draft part can be rehearsed and reach well formed but never synthetic tests passed. `check` returns the ceiling and the parts that set it. The kinds rule reads the vocabularies through `feasibility.requirements`, which should be confirmed to cover the draft views' kind columns; a test over `role_fluid.kind` settles it.
3. **The ceiling.** The roadmap admits a rehearsed part "for a question whose own status is no higher than synthetic tests passed". Read literally, that stops any question over `role_fluid`, including the transfusion question, from being compiled for a hospital until the part is clinically validated at one. I recommend that the owner decides between two readings: the literal one, or a ceiling at hospital mapping supported, with production validated requiring every part used to be at contract status or the reconciliation of stage 6 to cover the rehearsed part explicitly. The second is what lets the first question run once a database is available.
4. **The service.** The prompt tells the model it may use draft parts (`prompt.go`, "Draft parts"). It should say which parts are rehearsed and that a question over a draft part will wait. `question.Parse` marks `draft_parts_used` today; it should mark the status of each.

About 60 lines in `rolepolicy.py`, 40 in `contractcheck.py`, the evidence objects in `contract.json` for the sixteen candidate parts, and 30 lines of Go.

## 9. The portable dialect

### What the translation handles

The question is T-SQL as sqlglot 30.21.0 reads it, and the shadow runs DuckDB through `translate.to_duckdb` (`core/schemalyser/translate.py`, line 344). I ran the constructs a clinical question uses through it. DATEDIFF becomes DATE_DIFF, which counts boundaries crossed as SQL Server does (a minute boundary one second apart gives 1 on both). TOP becomes LIMIT. ORDER BY gains NULLS FIRST, matching SQL Server's ordering of nulls. CAST to date, DATEADD, CASE, COALESCE, LEAD, LAG and ROW_NUMBER translate directly. PERCENTILE_CONT ... WITHIN GROUP ... OVER () becomes QUANTILE_CONT, which runs on DuckDB and gives the median.

### Where it does not give SQL Server's answer

- **Division of whole numbers.** SQL Server gives a whole number; DuckDB a fraction. `to_duckdb` can rewrite this (`_whole_division`, lines 334 to 341) only when given `whole_columns`, and `roleshadow.duckdb_runner` never passes them (line 54). `SELECT COUNT(*)/2` reaches DuckDB unchanged. A proportion written as a count over a count is therefore right on the shadow and zero on SQL Server.
- **AVG of a whole column.** SQL Server's AVG of an integer is an integer, truncated; DuckDB's is a fraction. `asa_grade` is `whole`, and every flag is an integer.
- **Text comparison.** SQL Server's usual collation ignores case and trailing spaces; DuckDB does neither. The kinds rule protects kind columns, and `to_duckdb` turns LIKE into ILIKE; equality on any other text column is not protected.
- **String concatenation with +.** Valid T-SQL, refused by DuckDB at run time.
- **TOP or ROW_NUMBER without a total order.** Both engines run it and may differ.
- **The median.** `policy.FUNCTIONS` (`core/schemalyser/policy.py`, lines 75 to 78) has no percentile, so the role policy refuses the very statistic the prompt offers and the roadmap's first question needs.

### What a question may use

The dialect is fixed as a rule of the role policy, `dialect`, with its own version recorded in the report:

- the functions of `policy.FUNCTIONS`, with PERCENTILE_CONT added as a windowed function used only as `PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY x) OVER (PARTITION BY ...)`, which SQL Server requires;
- division only where the divisor or the dividend is cast to float or multiplied by 1.0, so that intent is explicit and both engines agree (the rule refuses a division whose two sides `translate._whole` reads as whole);
- AVG only of a number column or of an expression cast to float;
- text equality only between a kind column and a word of its vocabulary, or between two key columns;
- CONCAT for text, and no + between text;
- ROW_NUMBER, LEAD, LAG and TOP only with an ORDER BY that ends in a key column of the row's part;
- DATEDIFF with the units day, minute and second only, and DATEDIFF in seconds only over spans that a question about an anaesthetic can reach.

Each refusal gives a sentence that says what to write instead, so that the service can hand it to the model. `duckdb_runner` passes `whole_columns` in any case, as defence in depth. SQL Server stays the final gate of a compiled package, run through `tools/sqlserver/` only when the dialect rule or the compiled script changes, as the standing practice keeps it short.

About 120 lines in `rolepolicy.py`, a function and a window entry in `policy.py`, and a test that runs every catalogue capability and every examined package through both engines once, with the planted rows, compared with `equivalence.compare_answers`.

## 10. Where the examiner runs, and what returns to the model

The examiner is a Python command, `python -m schemalyser.examination run PACKAGE.json [--cases ROOT] [--out FOLDER]`, in a new package `core/schemalyser/examination/` (`static.py`, `shadow.py`, `metamorphic.py`, `planted.py`, `oracle.py`, `status.py`, `__main__.py`). On the server it runs as a second container beside Ask, with the private case root mounted read-only and the examiner's signing key; Ask posts a package to it and receives the report. Nothing of the case root is in Ask's image or volume.

What returns to the model through the service is bounded:

- stage 1: every rule and fragment;
- stage 2: whether the SQL ran, and the database's error in plain words, as `roleshadow._error` writes it;
- stage 3: which check failed and which mutation, since the mutations are public in principle;
- stage 4 and the oracle's differential run: only "n of m planted cases disagree", never which case, which row or which value.

The service allows a small number of examinations per question, three by default, so that the count of disagreements cannot be used to search for the answers. The clinician sees the same summary in plain words.

## 11. Ten questions across several models

### The question set

The owner writes ten requests in a clinician's words, chosen to span the risks: three over the three contract parts, including one with a median; four over rehearsed parts (fluids, drugs, stays and operations); two with temporal logic a capability covers; and one the contract cannot answer, for which a proposal and no SQL is the correct outcome. Each request has a card: the request text, the clinician's answer to each question a model might reasonably ask (with the default to accept where the card is silent), the required result columns, the structured specification the owner expects, and the planted cases with their answers, written and registered before any model runs. The cards live in the private examiner root; their hashes go into `examination-register.json` at the start, so that the measurement is registered in advance.

### The runs

A small harness, `tools/measure/`, drives the Ask service with a scripted clinician: a fixed small model that answers only from the card, or for the strictest arm, answers chosen by a person once and replayed. Each model runs each question three times, so that a pass rate has a variance. Two arms are run: one in which the model never sees an examination result, which measures the first package; and one in which the service returns the bounded feedback of section 10, up to three examinations, which measures the service as it will be used. The Ask service's backends today are the Anthropic API, the Claude Code command line and a fake (`ask/internal/model/`); a model from another provider needs a backend of its own, about 100 lines each.

### What is counted

For each run: the turns to a settled question, the blocks the service rejected and why, the status reached, each stage passed, the number of planted cases matched, and the tokens and time. A recurring mistake is classified by a person from the failing stage and the SQL, against a fixed list: link by time, a missing exclusion, the stop or a null mishandled, a duplicate or running total counted, age by an elapsed day rather than the calendar, a whole division, a wrong denominator, a dialect error, an invented column, a part used above its status, and a misread decision.

### How it is reported

A table of models by questions, each cell the passes out of three at synthetic tests passed; the pass rate per model with its 95 per cent Wilson interval, stated plainly, since thirty runs per model give an interval about thirty points wide; the same for each arm; and the recurring mistakes with their counts. Before the models are run, each card's correct SQL, written by the owner, must pass, and a set of deliberately wrong versions of it must fail, so that the reported pass rate measures the models and not the examination. The report is counts only and goes into `docs/` as `docs/measurement.md`.

About 300 lines for the harness and the report, and the owner's time to write ten cards, which is the larger cost: about two days.

## 12. The examination of the examination

Roadmap step 5's done statement asks that a deliberately wrong version of each question fails. That is a mutation test, and it is the measure of the examination's power. `examination/mutants.py` applies fixed mutations to a correct question's SQL with sqlglot: drop the test-patient filter; replace a key join by a time window; turn `>=` into `>` at each boundary; drop the accepted flag; drop a ROW_NUMBER's tie-break; change a LEFT JOIN to an inner join; drop a DISTINCT; remove a CAST to float before a division. The report gives, for each question, how many mutants each stage killed and which survived. A surviving mutant means either an equivalent mutation, which a person marks, or a missing planted case, which a person writes. About 150 lines.

## Order of work and what done means for each step

1. **The dialect fix in the runner and the median in the policy.** `duckdb_runner` passes `whole_columns`, and PERCENTILE_CONT is admitted as a windowed function. Done means that a test of a proportion of counts and of a median gives the same answer on the shadow as on SQL Server with the planted rows, and that the capability tests still pass.
2. **The rehearsed status.** The contract gains the status with its evidence, the checker enforces it, and the role policy admits rehearsed parts with a ceiling, checks draft and rehearsed columns, and admits drafts only for the examiner. Done means that the transfusion question over `role_fluid` passes the policy with the ceiling in its report, that the same question over a draft part is refused outside the examiner, and that the owner has chosen between the two readings of the ceiling.
3. **The further parts on the shadow.** The contract-driven generator, `role_shadow(further=True)`, and the general rehearsal. Done means that every part of the contract has rows on a seeded shadow, that each catalogue capability and the transfusion question run on it with a non-empty answer, and that `rehearse --question` runs a question that is not the neonatal audit.
4. **The structured specification and static validation.** The `examinable` part in the prompt and in `question.Parse`, and `examination/static.py`. Done means that the service writes `examinable` for the transfusion question, that stage 1 grants well formed to it and to the five capabilities written as packages, and that a package whose declared columns differ from its SQL's is refused with a sentence.
5. **Planted cases for the first six questions.** Cases and hand-written answers for the transfusion question and the five capabilities as packages (the capabilities' existing held-out answers carry over), the register of hashes, the key and date transform, and stage 4. Done means that all six pass their planted cases under a fresh transform on each run, and that a copy of each with one literal planted key in it fails.
6. **The three metamorphic checks and synthetic execution.** Stages 2 and 3. Done means that the six questions pass all three checks on two seeds and two insertion orders, and that each of the three deliberately wrong questions in the checks' tests fails exactly the check meant to catch it.
7. **The oracle.** `examination/oracle.py` over the vocabulary of `examinable`. Done means that the oracle reproduces the saturation question's held-out answer and the transfusion question's from their structured specifications, that its outcome rows agree with `specification.compile` for the same cohort, and that each question it covers passes the differential run on three seeds.
8. **Status derivation and the mutation report.** `examination/status.py`, the signed report, the page's verification, the refusal in `audit.py` below synthetic tests passed, and `examination/mutants.py`. Done means that the six questions reach synthetic tests passed with a verified report, that every non-equivalent mutant of each is killed by some stage, and that `audit.py` refuses to build a production package from a question at well formed.
9. **The examiner beside the service.** The examiner's container with the private root mounted, the service's call to it, the bounded feedback and the limit on examinations. Done means that the service shows a package's status from a verified report, that a held-out answer cannot be reached from the service's image, volume or responses (a test asserts what the response holds), and that the service publishes only packages at synthetic tests passed.
10. **Hospital mapping supported.** The import extended to read a package, and the compiled-versus-direct comparison on the map's shadow. Done means that the transfusion package imported into the invented hospital's project reaches hospital mapping supported, that one imported into a project whose schema lacks the fluids does not, and that both imports return the same word to the workspace as today's tests require.
11. **The measurement.** Ten cards registered, the harness, two arms, and `docs/measurement.md`. Done means that the owner's correct SQL for each card passes and its mutants fail, that every model has run every card three times in each arm, and that the pass rates with their intervals and the recurring mistakes with their counts are written down.
12. **Production reconciliation**, when a database becomes available. Done means that a person has reconciled a sample of anaesthetics for the first question and recorded it in the journal, and that the page shows production validated for that question at that hospital only.
