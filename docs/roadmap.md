# Roadmap

This is the plan of record from 14 October 2026. It replaces the roadmap of 9 October, which was written around a first meeting on the hospital's production database. That meeting is not available for now, so the plan below is built to go as far as it can on what exists: the public role contract, the invented world, the realistic stand-in built from the vendor's published specification of its export, and the knowledge that the hospital's real database follows that specification closely. Each item states what done means, and an item is done only when that statement is true.

On the evening of 14 October three reviews of this roadmap were written, of the architecture, of the goals and product, and of the examination (`docs/reviews/2026-10-14/`), and an implementation plan was drawn from them (`docs/work-plan.md`): forty-seven work packages in ten phases, each with what done means, and seven decisions for the owner. The plan is the work list; this roadmap remains the statement of the shape and the order of proof.

The architecture contract (`docs/contract.md`) remains the standard the code is held to, and `docs/alignment.md` remains the register of where the code falls short of it. This roadmap says what is built, in what order the rest is built, and where each layer of the intended product sits.

## The problem

One clinician has access to the hospital's database and an intermediate knowledge of SQL. Fifty anaesthetists want answers from the record. The clinician's time goes to five things: pinning down what an asker means, working out where the thing lives in the database, writing and debugging the joins, checking the result looks right, and doing it all again for the next variant of the same question. Only the last two need the database, and only checking needs a person. Schemalyser's purpose is to take the other three off the one person and to make the two that remain short.

## The shape

One private file, the hospital schema, and six pieces around it, named rather than numbered, because the contract's numbered layers (`docs/contract.md`) are the code's layers and are a different thing.

1. **The role model.** The public contract of parts, columns, kinds and capabilities, the same at every hospital, with a checker of its shape and a try command for whoever adds to it.
2. **The hospital schema.** Built once per hospital on the page Describe the record, from the database's own dictionary and a handful of analyst queries, with the evidence for every fact and its readiness. The only private thing.
3. **Questions.** A question is a package: a specification in the clinician's terms, SQL over the roles, the contract version it was written against, and a status. The capability catalogue is the set of parameterised questions chosen from a list; a free question is a package written for one asker.
4. **The examination.** Invented data with planted cases whose answers are held out, the role-level shadow, the test on made-up rows, the equivalence of two schemas, and metamorphic checks. Everything here runs with no hospital.
5. **Compilation and execution.** The policy over a question, its compilation through the hospital schema into the hospital's own SQL, the execution package the analyst runs, and the equivalence of the compiled script on the target engine.
6. **The public service.** A web application for the anaesthetists, with a language model behind it, that turns a free-text question into a specification and a package, and publishes the package at an address. It sees the role model and the invented world and nothing of any hospital.

**Where OMOP comes in.** Twice, as a target and as a source. As a target, the anaesthesia layer of OMOP is written by conversion steps over the roles, so that a validated hospital schema fills the anaesthesia tables beside the hospital's core OMOP; three such steps exist. As a source, a hospital whose anaesthesia record is already in OMOP binds the same contract to OMOP tables, and every question runs there unchanged. A cohort defined in ATLAS is a third, later entry: an input to a question's cohort rather than a way of writing the question.

## What is built

| Piece | Where | State on 14 October 2026 |
| --- | --- | --- |
| The role contract, 18 parts, 23 vocabularies, the capability catalogue | `core/schemalyser/rolemodel/` | Version 1.1; three parts at contract status, fifteen drafts |
| The checker of the contract's shape, run by the tests | `core/schemalyser/contractcheck.py` | Built |
| The try command, which opens a saved schema under a candidate contract | `python -m schemalyser.describe try` | Built |
| The page Describe the record, and the same operations on the command line | `site/`, `core/schemalyser/describe/` | Built; nine steps; one saved file per version |
| A saved schema reopened under a newer contract, with the added fields proposed from its own dictionary and nothing asked of the database | `core/tests/test_migration.py` | Built and proven end to end |
| The invented world and its dictionary | `fixtures/` | Built; the examination's public half |
| The realistic stand-in from the vendor's published specification, with a hand-written reference map | private reference folder | 56 tables; sixteen parts run through the naive schema and agree with the reference row for row |
| The measure of success: gold score, cost by kind of decision, readiness, equivalence | `docs/success.md`, `gold.py`, `equivalence.py` | Built; the realistic pass scores 26 of 33 documented bindings exact and 0 wrong |
| The policy over a question, the two-part script, the feasibility report, the execution package | `policy.py`, `rolepolicy.py`, `feasibility.py`, `audit.py` | Built for the three contract parts |
| The specification compiled into SQL over the roles | `specification.py` | Built, in the export's shape of sections per episode |
| The question import into a hospital's project, with its three statuses | `python -m schemalyser.workspace import` | Built; the workspace export holds out the planted answers |
| The role-level shadow and the planted neonates | `roleshadow.py`, `fixtures/held-out/` | Built; the neonatal audit runs on it with no map |
| The OMOP testbed on the invented world and on the stand-in | `python -m schemalyser.testbed` | Built; the stand-in's fast profile passes with 22 steps and 20 planted scenarios |
| Three conversion steps over the roles: measurements, visit details, procedures | private conversion folder | Built; identical rows to the direct steps on the stand-in |
| The SQL Server harness and the release equivalence | `tools/sqlserver/` | Built; run only when the T-SQL changes |
| The workbench, a local service over a project folder | `core/schemalyser/workbench/` | Built |
| Ask, the clinician-facing service: a conversation with a model that writes a question package over the roles and publishes it at a secret address | `ask/`, described in `docs/ask.md` | First version built on 14 October 2026; the page does not yet open a package by address, and no examination runs on one |

What is not built: the package's status levels beyond drafted, the page's import of a package by file or address, metamorphic checks, the portable dialect in the policy, the examination of a package, and a hospital schema bound to OMOP as a source. The public service exists in a first version, built out of the roadmap's order at the owner's decision, and its package format is the one step 3 fixes.

## The order of work

The order proves the risky claim first, which is that a question over the roles answers as well as a query written by hand against the database, and it leaves the language model to last, because it is the easiest part to add and the hardest to validate.

### 1. One question end to end, by hand

The median blood transfusion requirement of neonates having surgery, written as a specification and as SQL over the roles, compiled through the saved schema of the realistic pass, run on the stand-in, and compared with a query written by hand the way an analyst would write it against the stand-in's tables. The equivalence tool does the comparison.

Done means that the two answers agree on the stand-in, or that every difference is explained as a choice the specification makes, and that the question's package holds everything the comparison needed.

### 2. Promotion of parts on the stand-in's evidence

The rule that no draft joins the contract until the three contract parts are clinically validated at a hospital was written for a world with a first meeting. Without one, a middle status is needed: a part that runs through a naive schema on the stand-in and agrees with a hand-written reference row for row is *rehearsed*, and the policy admits a rehearsed part for a question whose own status is no higher than synthetic tests passed. The sixteen parts that agree today are the candidates, and the parts a question needs are promoted first.

Done means that the contract has the status, that the checker enforces it, that the policy admits rehearsed parts under that rule, and that the transfusion question passes the policy.

### 3. The question package

The package format: `specification.json` in the clinician's terms, with a cohort, an outcome and a result grain; `question.sql` over the roles; the contract version; a hash; `note.md`; and a status with four levels: well formed, synthetic tests passed, hospital mapping supported, production validated. The import that exists is extended to read it, and the catalogue's capabilities are written in the same form so that a chosen capability and a free question are one kind of thing.

Done means that the transfusion question and the five capabilities of the catalogue's first list are packages, that the import gives each its status, and that a package at a lower status cannot be compiled for production.

### 4. The page opens a question

Step 1 of the page accepts a package as a file, shows its specification and its SQL in words before anything else, checks it against the policy, compiles it through the loaded hospital schema, and reports the parts the schema lacks with what the stored dictionary proposes for them, as the reopening under a newer contract does today. Then the address: the page fetches a package from an address that carries its hash, before it goes offline, and refuses a package whose hash does not match; the file remains as the fallback where the network is closed.

Done means that the browser test opens the transfusion package both ways, refuses a package whose hash is wrong, and produces the compiled script, and that the page still seals itself from the network afterwards.

### 5. The examination

Planted cases for each question with their answers held out, as the planted neonates are; a metamorphic suite of three checks, an unrelated patient added changing nothing, a duplicated reading handled as the specification says, and a reading moved to another encounter leaving the cohort; and the status synthetic tests passed granted only by this suite. The examination is public except for the held-out answers.

Done means that the transfusion question and the five capabilities each pass their planted cases and the three checks, and that a deliberately wrong version of each fails.

### 6. The portable dialect

The role query dialect is fixed as the subset of SQL that round-trips to DuckDB and to SQL Server, with the temporal and window constructs a clinical question needs either standardised or refused, and the policy rejects what is outside it. SQL Server keeps its place as the final gate of a compiled package, run only when the compiled script changes.

Done means that every package of steps 1 to 5 is inside the dialect, that the policy refuses a construct outside it with a sentence saying so, and that the testbed's release equivalence still passes on SQL Server.

### 7. The public service

The web application for the anaesthetists: a conversation that turns a free-text question into a specification, a model writing SQL over the roles against the public contract and the invented world, the examination run on its output, and the package published at an address with its hash. Before any anaesthetist uses it, ten questions are given to several models and the iterations, the recurring mistakes and the pass rate on the held-out cases are counted.

Done means that the measured pass rate and the recurring mistakes are written down, and that the service publishes only packages the examination has passed.

### 8. OMOP as a source, and the anaesthesia layer promoted

A hospital schema that binds the contract to the invented OMOP patients the testbed makes, so that the transfusion question runs there unchanged; and the conversion steps over the roles promoted in the private conversion, with the full testbed run. ATLAS cohorts follow as an input to a question's cohort.

Done means that the transfusion question gives the same answer through the OMOP-bound schema as through the stand-in's, and that the full testbed passes with the steps over the roles in place of the direct ones.

### When the database becomes available

The clinical track of the earlier roadmap resumes at that point and is unchanged in substance: the hospital schema is built on the training database with the page, the first question runs on production through it, and a sample of anaesthetics is reconciled against the clinical record. Everything above is designed so that this takes a day rather than a project, because the schema will be built from a dictionary whose shape the stand-in has rehearsed, with answers the page already knows how to ask for.

## Decisions for the owner

- Whether to adopt the rehearsed status and the rule under which the policy admits it, which step 2 needs.
- Whether the first question is the transfusion question or one the three contract parts already cover, which makes step 1 a morning rather than a day.
- Whether the package is published at an address at all, or only as a file, which step 4 needs before its second half.
- The open questions of the capability catalogue in `docs/capabilities.md`, which the five capabilities of step 3 need answered.

## Standing rules

These do not change. The repository is public and holds no name of a hospital's table or column; the realistic stand-in and the reference map stay in the private reference folder. No language model sees anything of a hospital: the public service and any coding agent see the role model and the invented world only, and the planted answers are held out of every export. The page works offline and sends nothing anywhere; the one inbound fetch it may make is of a package whose hash it checks. The execution package is a plain file the analyst reads in full before it is run, and no result leaves the hospital without the policy's rounding. Clinical validation is a person reconciling records, and no status below production validated claims it.
