# Roadmap

The work runs in two tracks side by side. The clinical track shows that the hospital schema, and one audit made through it, are right on the real database. The engineering track shows, on invented data alone, that the conversion to OMOP and its release script do what they claim, and that a coding agent can work on the project without seeing anything from a hospital. Neither track waits for the other until they meet, and the principle from the reviews of 8 October 2026 still holds: validation comes before breadth, so nothing new is built on the hospital schema until one audit made through it has been shown to be clinically right.

Each item below states what done means. An item is done only when that statement is true, and not when the code for it has been written.

## The clinical track

### 1. The first meeting confirms the hospital schema

The clinician and the database analyst work through the page Describe the record on production at the first meeting, and save the hospital schema. The counts at step 8 then give two things that the saved file keeps apart: the clinician's judgement of whether each count looks right, and the coverage that the count measured, such as the share of anaesthetics with a patient and the share with a reading of a kind that the audit needs. The scoreboard then shows, as counts that name nothing, how well the proposer did, overall and for each category of column, so that a wrong link is not hidden among descriptive columns that were right.

Done means that a saved hospital schema exists on the hospital's own storage in which all three contract views have reached the state *checked against the database*, with the measured coverage recorded beside each judgement, and that its scoreboard has been read and any lesson for the proposer has been recorded in the public repository without naming the vendor's model.

### 2. The neonatal audit runs

Screen 2 is built for one audit only, the neonatal low mean pressure audit, rather than as a general audit builder. It reads the saved hospital schema, builds a shadow from it, plants the standard neonatal cases, runs the audit on the made-up rows, and compiles the two-part script for production. The database analyst then runs the script once on production.

Done means that the neonatal audit has run once on production through the hospital schema, that every planted case gave its expected answer on the shadow beforehand, and that the result carries its coverage by year, so that a year in which the schema reaches too few readings is visible beside the answer.

### 3. A sample of anaesthetics is reconciled against the clinical record

A clinician reconciles a sample of anaesthetics against the clinical record, including some from each band of the audit's answer and some that fell into the band in which nothing was recorded. Each disagreement is explained, either as a fault in the hospital schema, which is corrected and the counts run again, or as a limit of the record, which the result states.

Done means that the reconciliation has been completed and its findings written down, that every disagreement has an explanation, and that the three contract views can then be recorded as *clinically validated*. Until that is so, no second audit is started.

## The engineering track

### 4. A one-command OMOP test run on the invented source

One command runs the whole OMOP side on the invented source. It builds an OMOP CDM 5.4 instance from the published field list, runs the real conversion and the real release script, checks the planted scenarios against their expected rows, reconciles the source with the target, runs the DataQualityDashboard on the output, and writes one machine-readable report. The expected rows are written independently of the conversion, as the planted scenarios already are, so that a mistake in a step cannot also be a mistake in its test. The DataQualityDashboard is run rather than reimplemented, and the transformation that runs in the test is exactly the one that the release script carries into the hospital.

Done means that the command gives a pass or a failure with one report file, that its full profile runs with the pinned Athena vocabulary and records the release that it used, that the report holds the outcome of every planted scenario, the reconciliation of source to target and the DataQualityDashboard's findings, that a test fails if the test run and the release script would run different SQL, and that the command runs in the project's continuous testing.

### 5. The export for work with a language model

A command writes an exported workspace that a coding agent can work on, rather than a language model being built into the tool. The export is a product in its own right: it holds an allowlist of what may appear, renames anything else deterministically, refuses to write a workspace that holds a name outside the allowlist, and lists in an uncertainty register what the export does not know. The standing rule still holds, so the export carries no hospital data, metadata or dictionary text, and its checks are what demonstrate that.

Done means that a coding agent given only the exported workspace can implement a held-out transformation, one that the export does not contain, and that the implementation passes tests written independently of the agent and kept outside the workspace. The export's checks must also run in the test suite against invented inputs with planted names, and a planted name must never survive into an export.

### 6. A local service and dbt, considered

The offline page stays as the tool for work that faces the hospital. Once the one-command test run exists and the first audit has been validated, a local Python service may be added for the developer's side, with files as the only interface between the service and the page. Whether the conversion should then be written as dbt models is considered at the same time, once the test run has been used in earnest.

Done means that a written decision exists for each, with its reasons. Where the service is built, done also means that the two exchange only files and that the page still runs offline without it.

## Where the tracks meet

The tracks meet once the three contract views are clinically validated and the one-command test run is in place. The conversion to OMOP is then re-pointed from the source tables onto the roles, so that it reads the same validated hospital schema that the audit reads, and screen 3, the OMOP layer, is built on the test run.

Done means that the release script written from a validated hospital schema passes the one-command test run on the invented source, and that, for the reconciled sample of anaesthetics, the OMOP rows agree with what the clinician found in the clinical record.

## Standing rules for both tracks

The earlier modules are retired as the tracks need them to be. `docs/architecture.md` classifies every module of the core as active workflow, shared infrastructure, a candidate for the test run, or superseded. Screen 2 and the test run take what they need from the superseded modules, such as the form of the two-part scripts and the planted scenarios, and the rest are then removed. This is done once no active module, shared module or test-run module depends on a superseded one, and the superseded modules and their tests are gone from the repository.

The fourteen draft views stay drafts. No view joins the contract, and no audit reads a draft view, until the three contract views have been clinically validated at a hospital and a second audit needs a further view. This is a rule rather than a task, and it holds until both conditions are met.
