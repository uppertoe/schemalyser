# Implementation plan for Schemalyser, from 14 October 2026

This plan turns three reviews written today (`architecture.md`, `goals.md` and `examination.md`, in this folder) into work that can be run through in order. It reads them against `docs/roadmap.md`, `docs/ask.md` and `ask/README.md` on branch `naive-pass` at commit 47fbae9. It reopens none of the owner's decisions: the roles stay in the repository and are embedded in the service at build; the model may draft against every part with drafts marked; a question package is a JSON file or a secret address, never a folder; the server keeps Python minimal; and Ask stays.

References to a review are written as A (architecture), G (goals) and E (examination) with the finding or section number, so that A4 means finding 4 of the architecture review and E9 means section 9 of the examination design.

## Where the reviews disagree or overlap, and the resolution

**The status of a package.** The roadmap's single ladder of four levels mixes facts about the package with facts about one hospital (A1). The examination design keeps the ladder but derives every level from evidence records, never from a field in the package (E7), and the goals review asks that each level be named for what it shows and record who granted it (G7, G5). The resolution is two ladders. The public ladder belongs to the package, is held in signed evidence reports beside it rather than inside it, and has four levels: drafted, well formed, logic examined, and examined against held-out cases. The hospital ladder belongs to the hospital's project, never travels with the package and never returns to the service, and has two levels: mapping supported at this hospital, and production validated at this hospital. "Rehearsed" is a property of a part of the contract, not a level of a package. This keeps invariant 8 of the contract intact and still gives the hospital its gates.

**What a free question is examined against.** The roadmap asks for planted cases with held-out answers for every question, which nobody can write for fifty askers (A5, G4). The architecture review proposes typed decisions from which boundary cases are generated; the goals review proposes worked examples confirmed by the asker, valued by a second model; the examination design proposes a structured specification (`examinable`) and a deterministic oracle in Python. The resolution combines the three. Decisions are typed, and the typed form is the `examinable` part of the package. The oracle in Python, not a second model, computes the expected answer, because a second model can share the first model's misreading and is not reproducible. Boundary cases generated from the typed decisions, with the oracle's values, are shown to the asker in clinical words for confirmation, which is the check on meaning that no oracle can make. A free question can therefore reach "logic examined" without anyone writing a case by hand. Hand-written held-out cases are kept for the catalogue's capabilities and for the measurement of models, and only they grant "examined against held-out cases". Where the oracle cannot cover a question (E6 lists the cases), the package says so and a person writes its cases, which is honest about the cost.

**Where the held-out answers live.** The repository is public, so `fixtures/held-out/` is held out of the workspace and the service but not out of a model trained on public code (E1). The resolution follows E4. The answers of the existing capabilities stay where they are. The cases and answers of free questions and of the measurement live in a private examiner root outside the repository (by default a folder in the private reference area, or the folder that `SCHEMALYSER_EXAMINATION` names), and the repository commits only a register of their hashes with the date each was written.

**The dialect.** The roadmap plans a portable subset that round-trips to DuckDB and SQL Server; the architecture review calls that the wrong remedy (A11); the examination design defines the dialect as semantic rules of the role policy over T-SQL (E9); the goals review folds it into the examination. These agree more than they differ. The resolution is to keep T-SQL as the authoring dialect, to call the existing allowlist the dialect, and to add the semantic rules of E9 (explicit casts on division and averages, text equality only on kinds and keys, ordered windows, the median as a windowed `PERCENTILE_CONT`) as a versioned `dialect` rule of the role policy. Every examination runs on DuckDB, and the six reference questions run once on SQL Server whenever the dialect rule or the compiled script changes. No separate portable dialect is built.

**The rehearsed ceiling.** The architecture review would allow rehearsed parts on the stand-in only until a dated contract amendment admits them (A6); the examination design offers the owner two readings (E8.3); the goals review asks for an honest caveat (G7). This is the owner's to decide and is listed below. The plan builds the mechanism so that either reading is a one-line change in the admission function.

**The order of the examination against the page's import.** The roadmap puts the page's import (step 4) before the examination (step 5); the goals review reverses them (G9 and its roadmap point 4); the architecture review asks that the file be the default and the address wait for the hospital's approval (A9). The resolution is the goals review's order. Without production access the page's import has no user for some months, while the examination decides whether the service's output is worth anything. The hospital side reads the package by file through the workspace import early (package 10), because that costs a day and keeps one reader honest, and the page's import by file follows the examination. Fetching by address is last and waits for the hospital's approval.

**When to measure models.** The architecture review would measure ten questions right after the package format, with structural checks only (A recommendation 5); the examination design measures at the end with registered cards and two arms (E11); the goals review wants the questions to come from the real question log (G1, G9). The resolution is two measurements. An early measurement, a day's work after promotion, runs ten logged questions through Ask and counts only structural outcomes, which decides how much of the oracle is worth building. The full measurement, with registered cards and held-out answers, follows the examination.

**What the first question is.** The architecture review would prove the path first on the three contract parts and make the transfusion question the acceptance test of promotion (A recommendation 2); the goals review keeps the transfusion question first. Both happen, in that order: a question over the contract parts runs end to end in Phase 2, and the transfusion question is the acceptance test of Phase 3. Where the owner writes both the role query and the comparator, the result is reported as a consistency check, not an independent one (A5, G7).

**Ask and the catalogue.** Both reviews find that Ask does not see the mapping views or the catalogue (A3, G3). The resolution is that the package can name capabilities with parameters, the reader inlines each capability's tested SQL as a common table expression, and the model's own SQL is only the outer computation. This is the most effective single change for safety and for examination cost.

**The word "specification".** It names three things (A2). The resolution is that the clinician's words in the package become the field `statement`, the typed decisions become `decisions` and `examinable`, and "specification" means only the export's `schemalyser-specification/1`.

**Linking by time.** Metamorphic check 3 forbids attributing an outcome to an anaesthetic by time (E5), while rule 13 of `roles.md` lets a question attribute a stay-only row by its own logic. The resolution is that check 3 moves a stay-only row to another stay of the same patient rather than to another anaesthetic, as E5 already does for laboratory results, and that the stay route of package 20 carries the stay in the cohort so that such logic has a key to start from.

**OMOP.** The goals review would defer it (G11); the architecture review would gate conversion steps on part status now and re-scope the source binding as a round-trip test (A12). Both are adopted: the gate is a few lines inside package 18, the conventions a third-party OMOP must meet are written down in package 46, and the source binding waits for a second site or a research need.

## Decisions taken

On 14 October 2026 the owner took the first three decisions as recommended. The rehearsed ceiling is option (b): a package over a rehearsed part may reach mapping supported and run on production, and production validated requires a reconciliation that covers each rehearsed part by name, to be written into the contract as a dated amendment under package 17. The examiner runs on the owner's own machine for now, with its signed report uploaded to the service, and moves to a container beside the service once colleagues use it. The service opens to colleagues only once package 34 runs the examination on its output and package 35 records the asker's signature of meaning; until then the owner uses it alone. The question log began the same day with six requests, classified in `docs/demand.md`. The fifth decision was taken the same day as recommended: `docs/product.md` puts the catalogue first, the export of sections second and free questions third, and a standing monthly run of the catalogue waits for the question log.

## Progress

On 14 October 2026 packages 4, 5, 6 and 13 were done, and packages 7, 8 and 9 the same day: parts carry semantic hashes and versions with the lock file `core/schemalyser/rolemodel/parts.lock.json`, the question package has its schema beside the contract and its one reader `question.py`, and Ask writes that package and publishes each version at an address of its own with the hash of its bytes. Package 10 followed the same day: the workspace import and the audit build read the package file, and the folder form is gone. Packages 11 and 12 followed: the prompt renders the mapping views, the catalogue with each capability's columns and parameters, the parts' standing and the two-part rule, and a package composes a composable capability as a common table expression named after it, which the role policy checks as one question and which gives the capability's held-out answer on its planted rows. Package 14 followed: the role policy's versioned dialect rule, with the audit and the five capabilities choosing rows by an order that ends in a key; the binary collation on compiled kind comparisons was left out, because the kind values a compiled view returns are the contract's own constants, so a collation cannot change a match, and the change would alter every compiled script's hash and the DuckDB translation for no answer that differs. Packages 15 and 16 wait on the owner and on SQL Server. Package 17 followed: the fluids, the stays, the operations and the anaesthetic's details are rehearsed, each with its evidence from the stand-in of 14 October 2026, the checker requires that evidence, and the contract records the ceiling as decided; the drugs are rehearsed as well, because the conversion's step over the roles reads them. Package 18 followed: the role policy's three outcomes through the one admission function, with the ceiling and any admitted draft in its report, columns checked on every part, the audit build running the policy and keeping its report in the manifest, and the compiler and a conversion step asking the same function. Package 19 followed: the compiler's reach is derived from the contract's links, with no table of its own, so an audit over a rehearsed part linked to the anaesthetic compiles on the invented hospital. The next package in order is 20, the stay route.

## Decisions for the owner

1. **The rehearsed ceiling.** Option (a): a package over a rehearsed part can reach "logic examined" but cannot be compiled for any hospital. Option (b): it can reach "mapping supported" and run on production, and "production validated" requires a reconciliation that covers each rehearsed part by name. Option (c): rehearsed parts are admitted on the stand-in only until a dated contract amendment says otherwise. I recommend (b), written as the dated amendment that (c) asks for, because (a) means the transfusion question can never run, and (b) puts the check where it belongs, in a person's reconciliation. Package 17 needs the choice.
2. **Where the examiner runs.** Option (a): a second container beside Ask, holding Python and the private case root, which Ask calls. Option (b): the owner runs the examiner on their own machine and uploads its signed report to Ask, and Ask shows every unexamined package as "drafted, not yet checked". Option (c): Ask labels everything "not yet checked" and the page does all checking. I recommend (b) for as long as Ask is used by the owner alone, and (a) from the day it opens to colleagues, because (b) keeps the server free of Python now and (a) is the only option that scales. Package 34 needs the choice.
3. **When Ask opens to colleagues.** I recommend that it opens only when package 34 runs the examination on its output and package 35 records the asker's signature of meaning, and that the owner uses it alone until then, to fill the question log and the early measurement (G9).
4. **Whether a package is ever fetched by address in the hospital.** I recommend the file as the default and the address only after the hospital's information security team approves the mechanism in writing (A9, G9). Package 45 waits for that approval.
5. **The product's shape.** I recommend that `docs/product.md` puts the catalogue first, the export of sections second and free questions third, with a free question usually ending as a composition of capabilities (A8, G6). Whether to build a standing monthly run of the catalogue (G8) should wait for the question log.
6. **The catalogue's open questions, and postmenstrual age.** The thresholds, age bands, scales and factors in `docs/capabilities.md` are clinical decisions. I recommend that the owner drafts a default with a published source for each and circulates them to two or three colleagues, and that each capability states which age it uses, offering postmenstrual age where gestation is recorded (G10).
7. **Who writes the comparator for the hand-run questions.** I recommend asking the one clinician to write the comparator for the transfusion question against the stand-in's published shape, if they have an hour; otherwise the result is reported as a consistency check.

## The work packages

Sizes are for one engineer with a coding agent, unless a package says it is the owner's time. Each package touches only the files it names, adds the tests it names, and is done only when its statement is true. Verification follows the house practice: DuckDB day to day, and SQL Server small (`--rows 50 --skip-safeguards`) only when T-SQL changes.

### Phase 0. What is asked, and documents that agree

This phase proves nothing technical. It establishes what the fifty actually ask and how long it takes today, starts the one dependency the project does not control, and makes every document describe the same product, so that later work is measured against something real.

**1. The question log.** The owner writes down the requests the one clinician has received over the last one to two years, from email and memory, one sentence each, with the date, the purpose, the rough hours and where the hours went. Each request is classified as a catalogue capability with parameters, a free question over contract parts, a free question needing draft parts, or not answerable from the anaesthesia record. The log names colleagues and may describe cases, so it lives in the private root; only the counts by class go into the repository.
- Files: a private log in the examiner root; `docs/demand.md` (new) with the counts by class and by purpose.
- Tests: none.
- Done means: at least thirty requests are logged and classified, and `docs/demand.md` states the share of each class.
- Depends on: nothing. Runs in parallel with Phase 1.
- Size: owner's time, one to two days spread over a fortnight.

**2. The baseline.** For the next ten real questions handled by hand, the one clinician records minutes under the roadmap's five headings: pinning down meaning, finding where things live, writing joins, checking, and redoing for a variant. These ten questions are also candidates for the measurement's cards.
- Files: a private sheet; a summary table appended to `docs/demand.md`.
- Tests: none.
- Done means: ten questions have minutes recorded under all five headings, and the summary is in `docs/demand.md`.
- Depends on: nothing. Runs over several weeks.
- Size: a few minutes per question of the clinician's time; an hour of the owner's to arrange.

**3. The access track.** The owner asks for approval to run the dictionary-only query of `docs/first-ask-note.md` (step 1) on a training or reporting copy, which reads SQL Server's own catalogue and no patient data (G12). The request, its date, the person it went to and the next action are recorded.
- Files: `docs/first-ask-note.md` gains a dated "Status of the request" paragraph.
- Tests: none.
- Done means: a request has been sent to a named person, and the paragraph records the date and the next action.
- Depends on: nothing.
- Size: owner's time, two hours.

**4. The documents brought into agreement.** `docs/product.md` gains a section on how the catalogue, the export and free questions relate (decision 5). `docs/roadmap.md` is replaced by this plan in the house voice, with the layers named rather than numbered so that "layer 3" no longer means two things (A8). `docs/contract.md` gains a dated amendment to sections 2, 4 and 7: a question arrives as a package, a JSON file or an address, and the folder form is retired (A2). `docs/success.md`, `docs/roadmap.md` and `docs/architecture.md` are made to agree on how many parts agree through the naive schema (nine or sixteen) by rerunning the realistic pass's equivalence and quoting its output, and the audit screen's state is corrected (A14). `docs/ask.md` replaces the sentence that the service "has nothing of a hospital to show one" with the statement that clinicians may type hospital information into it, and the rule they are given (A10). `README.md` describes the product as `docs/product.md` does.
- Files: `docs/product.md`, `docs/roadmap.md`, `docs/contract.md`, `docs/success.md`, `docs/architecture.md`, `docs/ask.md`, `README.md`.
- Tests: none new; `python -m schemalyser.contractcheck` still passes, since the roles document is not touched.
- Done means: every document gives the same part count, the same layer names and the same description of a package, and the contract carries the dated amendment.
- Depends on: decision 5.
- Size: one day.

**5. Ask held to the owner's use, and hardened.** The question page gains a sentence beside the text box, in the house voice, that no patient's details are to be typed. Messages are screened before they reach the model for record-number-like strings, dates of birth and upper-case table-like names, using the pattern already in `rolemodel_test.go`, and a message that matches is held back with a sentence saying why. The `cli` backend runs with tools and external servers disabled, in an empty temporary working directory, without the developer's settings or memory (A10). The Caddy snippet strips `Remote-Email` on `/q/*` (A14). An allowlist of one address, the owner's, admits users until decision 3 is taken.
- Files: `ask/internal/server/server.go`, `ask/internal/server/templates.go`, `ask/internal/model/cli.go`, `ask/internal/config/config.go`, `ask/deploy/ask.caddy`, `ask/README.md`.
- Tests: a Go test that a message with a record-number-like string is not sent to the fake backend and the clinician sees the sentence; a Go test of the `cli` command's arguments and working directory; a Go test that a user outside the allowlist is refused.
- Done means: the fake backend never receives a screened message, the `cli` backend's command line carries the flags that disable tools, and only the owner can sign in.
- Depends on: nothing.
- Size: half a day.

### Phase 1. One package format and one gate

This phase proves that the service and the core read and write one package, that a published version cannot change under the person who received it, and that one function decides which parts a question may read.

**6. The contract's readers in layer 1, and one admission function.** The functions that read `contract.json` (`contract`, `views`, `public_views`, `statuses`, `capabilities`, `mapping_views`, `part_hashes`) move from `rolemap/__init__.py` into a new layer-1 module, and `rolemap` re-exports them so that callers change gradually (A13). The `required` flag is removed: every gate reads the view's `status` through one function, `admission(part, purpose)`, which returns admitted, admitted with a ceiling, or refused (A6).
- Files: `core/schemalyser/contract.py` (new), `core/schemalyser/rolemap/__init__.py`, `core/schemalyser/rolemodel/contract.json` (drop `required`), `core/schemalyser/contractcheck.py`, `core/schemalyser/LAYERS.toml`.
- Tests: `core/tests/test_contract.py` already checks layers; a new test in `core/tests/test_contractcheck.py` that `contract.json` has no `required` key and that `admission` gives the expected answer for a contract part and a draft.
- Done means: no module reads `required`, every reader of the contract imports from the layer-1 module or through `rolemap`'s re-export, and the full suite passes.
- Depends on: nothing.
- Size: one day.

**7. Semantic part hashes and per-part versions.** `part_hashes` hashes a projection of each part (name, key, links, and columns with type, vocabulary, mapping and optionality) rather than the whole view object, so that a description, a search word or a promotion no longer makes evidence stale (A7). Vocabulary changes stale only the code-translation dimension of the column that uses them. Each part gains an integer `version` in `contract.json`, and `contractcheck` compares the semantic hashes with a committed lock file and refuses a change without a bump.
- Files: `core/schemalyser/contract.py`, `core/schemalyser/contractcheck.py`, `core/schemalyser/rolemodel/contract.json`, `core/schemalyser/rolemodel/parts.lock.json` (new), `core/schemalyser/evidence.py` where staleness is decided.
- Tests: in `test_contractcheck.py`, a changed description leaves the hash unchanged; a renamed column without a bump is refused; in `test_migration.py`, a promotion from draft to contract leaves every binding of that part current.
- Done means: the three tests pass and the existing migration acceptance test still passes.
- Depends on: 6.
- Size: one day.

**8. The package schema and its one Python reader.** The package format is written once as a JSON Schema beside the contract (A2). Its fields are the title; `statement` (the clinician's words for cohort, outcome and result); `decisions`, each typed (`about`, `choice`, a typed `value` where one exists, the sentence, `by` and `date`), in the shape `audit.py` already reads; an optional `examinable` object in the vocabulary of E6, required later by the examiner for any level above well formed; `capabilities`, each with name, version and parameters; `question_sql`; `parts`, each with its version from package 7 and its admission; `proposals`; `contract_version`; provenance (model, time). Status is not a field. The format is `schemalyser-question/1`, unchanged in name because no package has been compiled yet. A new layer-3 module is the only reader and validator in Python, and its command `python -m schemalyser.question check FILE` prints every failed rule.
- Files: `core/schemalyser/rolemodel/question.schema.json` (new), `core/schemalyser/question.py` (new), `core/schemalyser/LAYERS.toml`, `docs/specification.md` (one paragraph separating the two formats).
- Tests: `core/tests/test_question.py` (new) with a valid package, one per failed rule, and the check that `parts` equals the parts the SQL reads.
- Done means: the reader accepts a hand-written package for the neonatal audit and refuses each malformed variant with a sentence naming the rule.
- Depends on: 7.
- Size: one day.

**9. Ask writes the schema's package, at immutable addresses.** The Go side validates its output against the embedded schema before saving, using a small Go JSON Schema validator, and writes the fields of package 8. Each saved version is published at `/q/TOKEN/N`, never changed, and the address the asker copies carries `#sha256=` of the exact bytes served; `/q/TOKEN` remains as a convenience on the asker's own screen (A4). The struct hash in `package.go` is removed. The embedded `contract.json` and the schema are generated at build from the repository and no longer committed in `ask/`, and the Go test fails rather than skips when they are missing (A14). The forbidden-word check strips block comments and string literals before it looks (A14).
- Files: `ask/internal/question/package.go`, `ask/internal/server/server.go`, `ask/internal/store/store.go` (versions are rows, never updated), `ask/internal/rolemodel/rolemodel.go`, `ask/Dockerfile`, a `go:generate` step or Makefile target, `.gitignore`.
- Tests: Go tests that a second version leaves the first's bytes and address unchanged, that the served bytes hash to the fragment, that a block comment containing CREATE is accepted, and that a package failing the schema is not saved; a Python test in `test_question.py` that reads a package produced by the Go fake backend, committed as a fixture, and accepts it.
- Done means: the Python reader accepts every package the Go service writes, and no published version's bytes can change.
- Depends on: 8.
- Size: one day.

**10. The workspace import and the audit build read the package.** `workspace.import_question` reads a package file through `question.py` rather than a folder of three files, and the audit build takes its decisions from the package. The three import words keep their meaning, and the A7 test (one package, two schemas, one status) is kept. The folder form is removed with its wording.
- Files: `core/schemalyser/workspace.py`, `core/schemalyser/audit.py`, `tools/workspace/` where it builds folders, `docs/workspace.md`.
- Tests: `core/tests/test_workspace.py` rewritten for packages; `core/tests/test_audit.py` reads decisions from a package.
- Done means: a package written by Ask's fake backend imports into the invented hospital's project and builds an audit, and no code path accepts a folder.
- Depends on: 8, 9.
- Size: one day.

**11. Ask's prompt matches what the core admits.** The rendered contract gains the mapping views and the capability catalogue with each capability's parameters and purpose (A3, G3). The prompt states the two-part form as a rule of the SQL, states the dialect's median, and says which parts are contract, which rehearsed and which drafts, and that a question over a draft part waits.
- Files: `ask/internal/prompt/prompt.go`, `ask/internal/rolemodel/rolemodel.go`.
- Tests: Go tests that the rendered prompt names every mapping view and capability in the embedded contract and states the two-part rule.
- Done means: `go run . -prompt` prints the mapping views, the catalogue and the two-part rule, and the tests pass.
- Depends on: 9. The median sentence is updated again after 13.
- Size: half a day.

**12. A package may compose capabilities.** A package names capabilities with parameters in `capabilities`, and `question.py` fills each with `capability.fill` and inlines it as a common table expression named after it, so that the model's SQL reads `transfusion` rather than rewriting it. The role policy then checks the whole as one question. The prompt tells the model to use a capability wherever one fits and to write free SQL only for the outer computation.
- Files: `core/schemalyser/question.py`, `core/schemalyser/rolepolicy.py` (the capabilities rule reads the package's list as well as the comment), `ask/internal/prompt/prompt.go`.
- Tests: in `test_question.py`, a package naming a capability with parameters expands to SQL that the role policy accepts and that gives the capability's held-out answer on its planted rows; a package naming an unknown capability is refused.
- Done means: the two tests pass, and the fake backend's sample package for a median across a capability's output is accepted.
- Depends on: 8, 11.
- Size: one day.

### Phase 2. One question by hand, over the contract parts

This phase proves the riskiest claim, that a question over the roles compiled through a saved schema answers as a hand-written query does, on parts that need no promotion, and it fixes the arithmetic that would otherwise make the shadow and SQL Server disagree.

**13. The runner's arithmetic and the median.** `roleshadow.duckdb_runner` passes `whole_columns` from the contract's whole and flag types, so that a division of whole numbers gives SQL Server's answer (E3, E9). `PERCENTILE_CONT` is admitted in `policy.FUNCTIONS` as a windowed function only (A11, E9).
- Files: `core/schemalyser/roleshadow.py`, `core/schemalyser/policy.py`, `ask/internal/prompt/prompt.go` (one sentence).
- Tests: in `test_policy.py`, `PERCENTILE_CONT` without `OVER` is refused; a new test that a proportion of counts and a median give the same answer on the shadow as written by hand; the capability tests rerun.
- Done means: the new tests pass and `core/tests/test_capabilities.py` still passes.
- Depends on: nothing in this plan; can run in Phase 1.
- Size: half a day.

**14. The dialect rule.** The role policy gains a versioned `dialect` rule with the constraints of E9: division only with an explicit cast on one side, `AVG` only of a number or a cast, text equality only between a kind and a word of its vocabulary or between keys, `CONCAT` rather than `+` on text, ordered windows ending in a key, and `DATEDIFF` in day, minute and second. Each refusal says what to write instead. Kind comparisons in the compiled SQL carry a binary collation (A11).
- Files: `core/schemalyser/rolepolicy.py`, `core/schemalyser/compiler.py`, `docs/policy.md`.
- Tests: in `core/tests/test_policy.py` or a new `test_rolepolicy.py`, one refused and one accepted example per constraint; the five capabilities and the neonatal audit pass the rule.
- Done means: every capability and the neonatal audit pass the dialect rule, and each refusal's sentence names its remedy.
- Depends on: 13.
- Size: one day.

**15. A question over the contract parts, end to end on the stand-in.** The owner chooses a question over patients, anaesthetics and readings (for example the proportion of neonatal anaesthetics with a mean pressure below a threshold for a stated time), writes it as a package by hand, imports it, compiles it through the realistic pass's saved schema and runs it on the stand-in. The comparator, a query written as an analyst would against the stand-in's tables, lives in the private reference folder because it names those tables. The equivalence tool compares the two.
- Files: the package under `fixtures/questions/NAME/question.json` (public, roles only); the comparator and the run's output in the private reference folder; a short dated note in `docs/success.md` with the result and whether it was a consistency or an independent check.
- Tests: none added to the suite, since the stand-in is private; the steps are recorded as one command line in the note.
- Done means: the two answers agree, or every difference is explained as a decision the package states, and the note says who wrote each side.
- Depends on: 10, 14.
- Size: half a day.

**16. The cross-engine check.** The package of 15 and the five capabilities run on SQL Server through `tools/sqlserver/` with the small settings, and their answers are compared with DuckDB's on the planted rows.
- Files: a test in `core/tests/test_package_on_sqlserver.py` (skipped where SQL Server is absent, as now).
- Tests: the one above.
- Done means: all six give identical answers on both engines.
- Depends on: 14, 15.
- Size: half a day.

### Phase 3. Promotion of the parts a question needs

This phase proves that a part can be admitted on the stand-in's evidence by one rule that the policy, the compiler, the audit build and the conversion all obey, and it ends with the transfusion question, which needs a promoted part, the stay route and a median.

**17. The rehearsed status.** `contractcheck.STATUSES` becomes contract, rehearsed and draft. A rehearsed part carries an `evidence` object (a neutral name for the stand-in, the date, the equivalence counts, the commit), which the checker requires. The admission function of package 6 implements the owner's choice of ceiling (decision 1), and `docs/contract.md` records it as a dated amendment. The parts the transfusion question needs (fluids, stays, operations and the anaesthetic's detail) are promoted first, each with its evidence; the others follow only when a question needs them.
- Files: `core/schemalyser/contractcheck.py`, `core/schemalyser/contract.py`, `core/schemalyser/rolemodel/contract.json`, `core/schemalyser/rolemodel/roles.md`, `parts.lock.json` (bumps), `docs/contract.md`.
- Tests: in `test_contractcheck.py`, a rehearsed part without evidence is refused; `admission` gives the chosen ceiling.
- Done means: the four parts are rehearsed with evidence, the checker passes, and the amendment is dated.
- Depends on: 6, 7, decision 1.
- Size: half a day, plus the owner's decision.

**18. The role policy's three outcomes, applied everywhere.** `contract_views` returns the columns of every view with its status, so that columns are checked on drafts too (E8.2). The tables rule returns admitted, admitted with a ceiling, or refused through `admission`; drafts are admitted only when the examiner passes `admit="draft"`. `audit build` calls the role policy, as the import and the export already do (A6). Conversion steps over the roles pass through the same gate (A12).
- Files: `core/schemalyser/rolepolicy.py`, `core/schemalyser/audit.py`, `core/schemalyser/compiler.py` (`compile_query` and `check_audit`), `core/schemalyser/conversion.py`.
- Tests: the transfusion SQL over `role_fluid` passes with the ceiling in its report; the same over a draft part is refused outside the examiner and admitted inside it; an invented column on a draft is refused; a conversion step over a draft part is refused.
- Done means: the four tests pass, and no command admits a draft that the policy refuses.
- Depends on: 17.
- Size: one day.

**19. The compiler's reach derived from the contract's links.** The hard-coded `REACHED` table in `compiler.py` is replaced by the links in `contract.json`, so that every admitted part is reachable from `#cohort` by the column the contract names.
- Files: `core/schemalyser/compiler.py`, `core/schemalyser/contract.py`.
- Tests: in `core/tests/test_audit.py`, an audit over `role_fluid` with an anaesthetic link compiles on the invented hospital; the existing two-part tests pass unchanged.
- Done means: `REACHED` no longer exists and the new test passes.
- Depends on: 18.
- Size: half a day.

**20. The stay route.** Where a question needs a part whose rows may carry only the stay, `#cohort` carries `stay_key` from the anaesthetic's detail, the part is reached through the stay's distinct values, and the coverage step of the series counts it (A6). The policy's coverage rule accepts the route.
- Files: `core/schemalyser/compiler.py`, `core/schemalyser/policy.py`, `docs/policy.md`.
- Tests: in `test_audit.py`, a fluid row with an empty `anaesthetic_key` and a stay is reached and counted in coverage; a child with two anaesthetics in one stay does not double the row.
- Done means: both tests pass and the compiled script passes the policy at class B.
- Depends on: 19.
- Size: one day.

**21. Every part on the role-level shadow.** A contract-driven generator, `generated_further`, writes plausible rows for every further part without a sitting (E3.1): stays, ASA grades, weights by age, fluids in both amount and running-total forms with red cells in a share of anaesthetics, drugs, events, devices, staff, laboratory results and diagnoses before and after the anaesthetic, and some amended rows. `role_shadow` gains `further=True`, and `rehearse --question FILE` runs any question without the neonatal audit's banding (E3.4).
- Files: `core/schemalyser/roleshadow.py`, `core/schemalyser/rolemap/__main__.py`.
- Tests: in `core/tests/test_rolemap.py`, every part has rows on a seeded shadow; the five capabilities and the transfusion SQL give a non-empty answer; `rehearse --question` runs a question that is not the neonatal audit.
- Done means: the three tests pass.
- Depends on: 13.
- Size: one and a half days.

**22. The transfusion question by hand, end to end.** The median red cell volume per kilogram of neonates having surgery is written as a package that composes the `transfusion` and `total_volume_per_kg` capabilities, compiled through the realistic pass's saved schema with the stay route, run on the stand-in and compared with a comparator (decision 7). This is the acceptance test of Phase 3.
- Files: `fixtures/questions/transfusion_neonates/question.json`; the comparator and output in the private reference folder; a dated note in `docs/success.md`.
- Tests: the package passes `question check`, the role policy with its ceiling, and runs on the shadow of 21.
- Done means: the answers agree on the stand-in or every difference is a stated decision, and the note says who wrote each side.
- Depends on: 12, 16, 20, 21.
- Size: one day.

**23. The early measurement.** Ten questions from the log (package 1) are put to Ask through the `api` backend by the owner, playing the asker. For each, the owner records the turns to a settled question, whether the package passes `question check`, the role policy, the dialect rule and the compilation through the realistic pass's schema, whether it runs on the shadow, and whether it used a capability where one fitted. No correctness is measured.
- Files: `docs/measurement.md` (new), counts only.
- Tests: none.
- Done means: ten questions are counted under those headings, and the note says which failures recur.
- Depends on: 1 (at least ten logged), 11, 12, 18.
- Size: one day, mostly the owner's.

### Phase 4. The examination

This phase proves that a model-written package can be judged on evidence the model never saw, that the judgement catches deliberately wrong versions, and that the asker can confirm meaning without reading SQL.

**24. The examinable specification and the static stage.** Ask's prompt asks the model for `examinable` in the vocabulary of E6, and Ask validates it against the schema. The examiner's first stage runs `question.py`'s check, the role policy with the dialect rule, the two-part form check of `workspace.py`, the declared result columns against the SQL's, and the part versions against the contract (E2, stage 1). The package `core/schemalyser/examination/` is created with its command line.
- Files: `core/schemalyser/examination/__init__.py`, `static.py`, `__main__.py` (new); `LAYERS.toml`; `ask/internal/prompt/prompt.go`.
- Tests: `core/tests/test_examination.py` (new): the transfusion package and the five capabilities as packages are well formed; a package whose declared columns differ from its SQL's is refused with a sentence.
- Done means: `python -m schemalyser.examination run PACKAGE` grants well formed to the six and refuses the variant.
- Depends on: 12, 14, 18.
- Size: one day.

**25. The private examiner root and the planted stage.** The examiner reads cases from the root named by `SCHEMALYSER_EXAMINATION`; the repository commits `examination-register.json` with each case's name, hashes and date; `workspace.DENIED` gains the root. Each run applies a fresh bijection to keys and a common shift to dates, with the expected answer transformed alike (E4), and compares with `equivalence.compare_answers`. The role policy refuses a string literal compared with a key column.
- Files: `core/schemalyser/examination/planted.py` (new), `core/schemalyser/workspace.py`, `core/schemalyser/rolepolicy.py`, `examination-register.json` (new, at the repository root or under `fixtures/`).
- Tests: a case whose files no longer match the register is refused; a question that names a planted key fails under the transform; the neonatal audit passes its existing planted cases through the new stage.
- Done means: the three tests pass.
- Depends on: 24.
- Size: one day.

**26. Planted cases for six questions.** The owner writes cases for the transfusion question on each side of every decision, in the shape of `fixtures/questions/saturation_below_90/`, and carries over the five capabilities' existing held-out answers as packages. The transfusion question's cases go in the private root, since it is the first free question.
- Files: the private root; `examination-register.json`.
- Tests: the six pass stage 4.
- Done means: every decision of the transfusion package has a case on each side, and all six questions pass their planted cases under a fresh transform.
- Depends on: 22, 25.
- Size: owner's time, half a day for the transfusion question.

**27. Synthetic execution.** Stage 2 runs a package on the shadow of package 21 at two seeds and two insertion orders, refuses one that does not run, returns the wrong columns, differs between orders, or returns nothing where the generator guarantees qualifying anaesthetics (E2).
- Files: `core/schemalyser/examination/shadow.py` (new).
- Tests: a question using `ROW_NUMBER` without a total order fails; the six pass.
- Done means: both tests pass.
- Depends on: 21, 24.
- Size: half a day.

**28. The three metamorphic checks.** An unrelated patient added changes nothing; a duplicate is handled as `examinable` says; an outcome row moved to another anaesthetic of the same patient, or for a stay-only row to another stay, leaves the cohort (E5, with the resolution above).
- Files: `core/schemalyser/examination/metamorphic.py` (new).
- Tests: for each check a correct question passes and a deliberately wrong one (no test-patient filter, a sum over running totals, a join by time) fails only that check.
- Done means: the six questions pass all three checks on two seeds, and each wrong question fails exactly its check.
- Depends on: 27.
- Size: one day.

**29. The oracle, cohort and outcome.** `oracle.py` evaluates `examinable` over rows in Python: the cohort's bounds and exclusions, the outcome's part, kinds, forms, flags and window by key link, the duplicate and amendment rules, and the measures `exists`, `count`, `sum`, `min`, `max` and `first_time` per grain (E6).
- Files: `core/schemalyser/examination/oracle.py` (new).
- Tests: the oracle's per-anaesthetic rows agree with `specification.compile` for the neonatal cohort; the saturation question's per-anaesthetic held-out rows are reproduced.
- Done means: both tests pass.
- Depends on: 24.
- Size: one day.

**30. The oracle, statistics, capabilities and the differential run.** The oracle adds the statistics `count`, `proportion`, `sum`, `median` and `rows` with bands, and a capability measure computed through `capability.fill` on a shadow of the planted rows. The differential run compares the package's answer with the oracle's on three seeds. A package whose `examinable` says `"oracle": "none"` is reported as needing hand-written cases.
- Files: `core/schemalyser/examination/oracle.py`.
- Tests: the saturation and transfusion answers are reproduced from their `examinable`; the six questions pass the differential run on three seeds.
- Done means: both tests pass.
- Depends on: 22, 29.
- Size: one day.

**31. The mutants.** `mutants.py` applies the fixed mutations of E12 to a correct question's SQL and reports which stage killed each. A surviving mutant is marked equivalent by a person or answered by a new planted case.
- Files: `core/schemalyser/examination/mutants.py` (new).
- Tests: every non-equivalent mutant of the six questions is killed.
- Done means: the mutation report for the six shows no survivor that is not marked equivalent.
- Depends on: 26, 28, 30.
- Size: one day.

**32. The status derivation and the signed report.** `status.py` derives the public level from the stages passed: well formed from stage 1; logic examined from stages 2 and 3, the oracle's differential run where it covers the question, and the asker's confirmation of the worked examples (package 33); examined against held-out cases when stage 4 also passes on cases written before the first examination. The report names the package's hash, the contract, policy and examiner versions and the register's hash, and is signed with the examiner's key. A change of any of those versions voids the levels above well formed. `audit.py` refuses to build a production execution package from a question below logic examined, and the hospital side checks the signature.
- Files: `core/schemalyser/examination/status.py` (new), `core/schemalyser/audit.py`, `docs/policy.md`.
- Tests: a report with a wrong signature is refused; a production build from a well-formed package is refused; a contract version change voids logic examined.
- Done means: the six questions reach their levels with verified reports, and the three refusals hold.
- Depends on: 25, 28, 30, 31.
- Size: one day.

**33. Worked examples for the asker, and the signature of meaning.** From the typed decisions, the examiner generates boundary cases (a baby one day either side of each age bound, a missing weight, no blood given, a duplicate reading) and values them with the oracle, and writes each as a sentence in clinical words (G4). Ask shows them to the asker, who confirms each or says which is wrong; a correction returns to the model as a change of decision. The confirmation, with the asker's name and the date, is recorded beside the package as the signature of meaning.
- Files: `core/schemalyser/examination/examples.py` (new), `ask/internal/server/server.go`, `ask/internal/server/templates.go`, `ask/internal/store/store.go`.
- Tests: the transfusion package yields one example per decision boundary, each valued by the oracle; a Go test that a confirmation is stored with name and date and that a rejection reaches the model's next turn.
- Done means: the asker can confirm or reject each example in Ask, and the confirmation is part of the evidence that grants logic examined.
- Depends on: 30, and 34 for the route by which examples reach Ask.
- Size: one day.

### Phase 5. The service's checks and governance

This phase proves that the service publishes only what the examination passed, that a result carries the record of who answered for its meaning, logic and run, and that small groups and order statistics are protected.

**34. The examination of the service's packages.** Under decision 2 option (b), the owner runs `python -m schemalyser.examination run` over a downloaded package and uploads the signed report; Ask verifies the signature with the examiner's public key, shows the level, and publishes at `/q/TOKEN/N` only a package at logic examined or above. Ask returns to the model only the bounded feedback of E10, and allows three examinations per question. Under option (a), the same interface is a call to a second container with the private root mounted read-only.
- Files: `ask/internal/server/server.go`, `ask/internal/store/store.go`, `ask/internal/config/config.go`, `core/schemalyser/examination/__main__.py` (an `--upload` option), `ask/README.md`.
- Tests: a Go test that an unsigned or wrongly signed report is refused; that a package below logic examined is not published; that the response to the model never holds case detail; that a fourth examination is refused.
- Done means: the four tests pass, and no published package lacks a verified report.
- Depends on: 32, decision 2.
- Size: one day.

**35. The signatures, the approval reference and the declared use.** Beside the package, three signatures are recorded: meaning by the asker (package 33), logic by the custodian, at first the owner, who confirms the examination and accepts any rehearsed part for the question's purpose, and the run by the analyst, which exists. The package carries the approval reference for the audit or research it serves and the declared use, which is never the care of an individual patient (G5). The hospital side refuses a production build without the custodian's signature and an approval reference.
- Files: `core/schemalyser/rolemodel/question.schema.json` (approval and use fields), `core/schemalyser/question.py`, `core/schemalyser/audit.py`, Ask's templates for the custodian's sign-off, `docs/audit.md`.
- Tests: a production build without the custodian's signature or an approval reference is refused.
- Done means: the refusal test passes and `docs/audit.md` names each signature and who gives it.
- Depends on: 32, 33.
- Size: one day.

**36. Attrition in every compiled package.** The compiled script reports the cohort count at each step the package's decisions define (in the age range, with an anaesthetic, with an operation, with a weight, with the outcome) and the number excluded at each with the reason, beside the coverage of each recording pathway for the period, under the existing rounding (G5).
- Files: `core/schemalyser/compiler.py`, `core/schemalyser/results.py`, `core/schemalyser/policy.py` where the series is checked.
- Tests: in `test_audit.py`, the transfusion package's compiled script on the invented hospital emits the attrition rows with the expected counts after rounding.
- Done means: the test passes and the policy classes the script unchanged.
- Depends on: 20, 22.
- Size: one day.

**37. Small cells and order statistics.** The policy treats a median or any order statistic over fewer than the suppression threshold as a small cell, so that a median of a handful of neonates is suppressed as a count would be (G5).
- Files: `core/schemalyser/policy.py`, `core/schemalyser/blanking.py`, `docs/policy.md`.
- Tests: a median over a group below the threshold is blanked; above it, it is reported.
- Done means: both tests pass.
- Depends on: 13.
- Size: half a day.

**38. The triage of contract proposals, and the catalogue's defaults.** `docs/contract.md` or `docs/ask.md` states how proposals from askers are triaged: who reads them, how often, and that a proposal is accepted only through `contractcheck` and `describe try`. The owner drafts defaults with sources for the catalogue's open questions and circulates them (decision 6).
- Files: `docs/ask.md`, `docs/capabilities.md`.
- Tests: none.
- Done means: the triage rule is written, and each open question in `docs/capabilities.md` has a default with a source and the names of the colleagues who agreed it.
- Depends on: nothing; the circulation runs in parallel.
- Size: two hours of writing; the owner's time for the circulation.

**39. The hospital's approval of the service.** The owner asks for Ask to be recorded under the hospital's AI policy, since clinicians will use it about hospital work, with the screening of package 5 and the rule on patient details described. The reply is recorded in `docs/ask.md`.
- Files: `docs/ask.md`.
- Tests: none.
- Done means: a request has gone to a named person, and its outcome or next action is recorded.
- Depends on: 5.
- Size: owner's time.

### Phase 6. The measurement of models

This phase proves how often a model's package is right without help, and how often with the service's bounded feedback, and it names the mistakes that recur.

**40. Ten registered cards.** The owner writes ten cards from the question log and the baseline, spanning the risks in E11 (three over contract parts including a median, four over rehearsed parts, two with temporal logic a capability covers, one the contract cannot answer). Each card holds the request, the answers a clinician would give, the expected columns, the expected `examinable`, the correct SQL and the planted cases with answers. The cards live in the private root and their hashes are registered before any model runs.
- Files: the private root; `examination-register.json`.
- Tests: each card's correct SQL passes the examination at examined against held-out cases, and its mutants are killed.
- Done means: ten cards are registered and all ten correct answers pass.
- Depends on: 31, 32.
- Size: owner's time, two days.

**41. The harness and the report.** `tools/measure/` drives Ask with a scripted asker that answers only from the card, runs each model three times per card in two arms (no feedback, and bounded feedback up to three examinations), and writes the table of E11 with Wilson intervals and the mistakes by class. A backend for another provider is added only if the owner wants one compared.
- Files: `tools/measure/` (new), `ask/internal/model/` for any further backend, `docs/measurement.md`.
- Tests: the harness runs end to end against the fake backend in the suite.
- Done means: every chosen model has run every card three times in each arm, and `docs/measurement.md` holds the pass rates with their intervals and the recurring mistakes with their counts.
- Depends on: 34, 40.
- Size: one and a half days, plus model time.

### Phase 7. The hospital side and the return path

This phase proves that the page in the hospital opens an examined package, compiles it through the hospital schema with the hospital's own feasibility kept private, and that the asker receives an answer they can read.

**42. The page opens a package by file.** Step 1 of the page accepts a package file, verifies its signed report, shows the statement, decisions and worked examples in words before the SQL, checks it against the policy, compiles it through the loaded schema, and reports the parts the schema lacks with what the stored dictionary proposes, as the reopening under a newer contract does. The page is the one hospital-side surface that compiles a package; the workbench's export screen keeps the export of sections (A8).
- Files: `core/schemalyser/browser.py` (a `describe_` function over `question.py`), `site/public/worker.js`, `site/src/describe.ts`, `site/src/describe-strings.ts`, `docs/screens.md`.
- Tests: a Playwright test in `site/e2e/describe.spec.ts` that opens the transfusion package, shows its decisions, produces the compiled script, and refuses a package whose report is unsigned; the existing network test still passes.
- Done means: the browser test passes and the page still seals itself from the network.
- Depends on: 32, 36.
- Size: one and a half days.

**43. Mapping supported at this hospital.** The hospital side records stage 5 privately: the feasibility report with every part bound at the readiness the package needs, the compiled script's class, and the compiled question on the map's shadow giving the same answer as the question on the role rows directly (E2, stage 5). The import still returns only its public word.
- Files: `core/schemalyser/roleshadow.py` (`run_check` for any package), `core/schemalyser/workspace.py`, `core/schemalyser/feasibility.py`.
- Tests: the transfusion package reaches mapping supported in the invented hospital's project and not in a project whose schema lacks fluids, and both imports return the same public word.
- Done means: both tests pass.
- Depends on: 42.
- Size: one day.

**44. The result note, and the asker's view of where the question stands.** The analyst's results step writes a one-page note in the asker's language: the statement, the decisions, the attrition, the result, what was rounded or suppressed, the package's public level, the hospital levels it reached, the date of the data and the triangulation figure the analyst compared it with (G5, G8). Ask shows each question's state (drafted, examined, sent to the analyst, answered), which the owner sets by hand, since nothing of a hospital flows back automatically.
- Files: `core/schemalyser/results.py`, a note template, `ask/internal/store/store.go`, `ask/internal/server/templates.go`, `docs/audit.md`.
- Tests: a Python test that the note for the transfusion package on the invented hospital holds every heading and no unrounded count; a Go test that the state can be set only by the owner.
- Done means: both tests pass, and the note contains nothing the policy would not release.
- Depends on: 36, 37, 43.
- Size: one day.

**45. Fetching a package by address.** Only after decision 4's approval: the page's policy admits one origin pinned at build, the fetch is allowed only in the state before any dictionary, schema or paste is loaded, enforced by the core's state, and the fragment hash is checked before anything parses the body (A4, A9). The file stays the default.
- Files: `site/index.html`, `site/public/worker.js`, `core/schemalyser/browser.py`, `site/e2e/network.ts`.
- Tests: a Playwright test that the fetch works before loading, is refused after a schema is loaded, and refuses a body whose hash is wrong; the network seal test is updated for the one origin.
- Done means: the three behaviours hold and the network test passes.
- Depends on: 42, decision 4.
- Size: one day.

### Phase 8. OMOP, kept small

This phase records what OMOP as a source would require and keeps the target honest; the source binding itself waits for a reason.

**46. The conventions for OMOP as a source.** A short document says which conventions a third-party OMOP must meet before a hospital schema can bind the contract parts to it (a visit detail per anaesthetic, test patients retained, measurements linked to the visit detail), and renames the roadmap's step 8 as the round-trip test it is (A12).
- Files: `docs/omop-source.md` (new), `docs/roadmap.md`.
- Tests: none.
- Done means: the document exists and the roadmap's OMOP item is described as a round trip.
- Depends on: 4.
- Size: two hours.

**47. Deferred: the OMOP-bound round trip and ATLAS cohorts.** The schema bound to the testbed's OMOP patients, the transfusion question run through it, and the conversion steps over the roles promoted in the private conversion. This starts when a second site or a research need asks for it (G11).
- Size: not estimated.

### Phase 9. When a database becomes available

The clinical track resumes as the roadmap describes it: the dictionary-only query from package 3, the hospital schema built on the training copy with the page, the transfusion package compiled and run on production, a sample of anaesthetics reconciled against the clinical record, and production validated recorded in the hospital's journal for that question at that hospital only. Nothing here is sized until access is granted.

## The size of the whole

Phases 0 to 5 hold about twenty-nine days of engineering (one and a half in Phase 0, six and a half in Phase 1, two and a half in Phase 2, six and a half in Phase 3, eight and a half in Phase 4 and nearly four in Phase 5), Phase 6 about one and a half, and Phase 7 about four and a half, so the work to the end of Phase 7 is roughly seven weeks of one engineer with a coding agent. Phase 7's address fetch (package 45) may never be needed, which would save a day. The owner's time is separate: about a week across the question log, the cases, the cards, the circulation of the catalogue's defaults and the approvals.

## What to do first this week

The owner starts three things that run on their own clock: the question log (package 1), the request for the dictionary-only query (package 3), and the arrangement with the one clinician to record minutes on the next ten questions (package 2). The owner also takes decisions 1 and 2, since packages 17 and 34 wait on them, and decision 5, which package 4 needs.

On the engineering side, the week runs as follows. On the first day, package 5 closes the gaps in Ask and package 4 brings the documents into agreement. On the second day, package 6 puts the contract's readers and the one admission function in layer 1, and package 13 fixes the runner's arithmetic and admits the median, since it depends on nothing. On the third day, package 7 gives parts their semantic hashes and versions. On the fourth day, package 8 writes the package schema and its Python reader. On the fifth day, package 9 makes Ask write that package at immutable addresses. By the end of the week the service and the core read one format, a published question can no longer change under the person who received it, and Phase 2 can begin with the workspace import of package 10.
