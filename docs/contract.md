# The architecture contract

This page states the layers of Schemalyser, the goal of each, what each takes in and gives out, and the rules that govern how they interact. It is the contract that the code is held to; `docs/architecture.md` describes the parts, and this page says why they are arranged as they are. A test enforces the dependency rules in section 5.

Two kinds of dependency are kept apart throughout. **Import dependency** constrains code: a module may import only from the layers above it. **Workflow dependency** is the flow of outputs between layers through versioned files, each carrying its provenance, and may run in any direction the flows table allows. A layer never imports a layer below it to obtain that layer's results; it reads the file the lower layer wrote.

Schemalyser is an evidence-management system first and a query compiler second. Every decision the compiler makes traces to a clause of the role contract, a recorded piece of evidence in the hospital schema, or a stated limitation.

## 1. The layers

The layers are listed from the most stable to the most changeable. Each imports only from the layers above it.

### Layer 1. The role model

**Goal.** A stable, vendor-free description of the anaesthetic record that audits are written against and that any hospital can be mapped onto.

**Holds.** The parts of the record (patients, anaesthetics, readings, and the draft parts), their columns with types and meanings, the vocabularies of kinds, the rules of the record, and the relation of each part to OMOP.

**Interface.** `contract.json`, versioned, and `roles.md` in words. A query over the roles is SQL over the role views and nothing else.

**Changes.** Only by a new version of the contract, which invalidates the evidence of every schema and audit that depends on the changed parts until they are re-run. The three parts of version 1 change their columns for no reason short of a version 2.

**Rule.** The role model never acquires a hospital's code, a vendor's description or a reference conversion's terminology. It is the same file at every hospital.

**Imports.** Nothing.

### Layer 2. The hospital schema

**Goal.** One file per hospital that is the single authoritative record of where that hospital's database keeps each part of the record and of every piece of evidence about it: who established each fact, how, when, and within what scope.

**Holds.** The proposed and confirmed bindings and links, the code translations, the counts and judgements, probes and their results, an append-only journal of every query offered and every result returned, provenance for every fact (complete data, a sample, metadata, a person, an inference, a reference conversion), the evidence dimensions of every binding (section 3), and a copy of the data dictionary.

**Interface.** The saved hospital schema, one zip file with a version identifier, written only by layer 2's own operations and read by everything below. Each save is a new immutable version; reports and packages name the version they were made from and are stale once a newer version exists.

**Operations, all owned by layer 2.** Propose from a dictionary; confirm, correct or mark not sure; translate codes; record a count or probe result; and **import evidence**: an investigation that a lower layer requested (a probe, a values query, a count, a plan) is executed by the database analyst, and its result is validated and appended to the journal by layer 2, which then publishes a new version. No lower layer writes to a schema by any other route.

**Imports.** Layer 1.

### Layer 3. Compilation and reports

**Goal.** Turn a question over the roles and a hospital schema into artefacts that are honest about what each establishes.

**Holds.** Compilation of a role query into SQL over the hospital's tables; the feasibility report (what a question needs and the evidence state of each requirement, with the investigations that would move each gap, as requests for layer 2's evidence import); the static policy on the final SQL and the execution class it derives; the specification; the OMOP conversion steps and the release script over the roles.

**Interface.** Commands with JSON reports and SQL files. Three reports are kept apart and never merged into one verdict: feasibility (is the schema known), correctness (do the planted cases pass on made-up rows, which layer 4 establishes), and execution safety (may this run, and in what class). Layer 3 produces the first and third; it never produces the second.

**Imports.** Layers 1 and 2.

### Layer 4. Test worlds and the testbed

**Goal.** Evidence on made-up rows: that a schema holds together, that a conversion preserves what its specification says, that SQL Server executes the production artefact as DuckDB's translated form predicts, and that the CDM produced passes the standard quality checks.

**Holds.** The invented world (public), the realistic stand-in (private, built from a public specification), the invented hospital that runs in the browser, the role-level shadow with its planted cases, the testbed with its fast and full profiles, the SQL Server harness, the Data Quality Dashboard stage, the dashboard's permitted failures, and the source-to-target reconciliation.

**Interface.** One command per run, one `report.json` per run with every section, and the reconciliation's four groups stated by name. Layer 4 consumes layer 3's SQL as files; it never imports layer 3's compilation to obtain it.

**Rules.** Expected outputs are written independently of the conversion and never derived from its output. A run reports what it could not trace; it never implies that untraced steps were reconciled.

**Imports.** Layers 1 to 3.

### Orchestration

**Goal.** Assemble what the layers produce into what a person acts on, without any layer importing a layer below it.

**Holds.** The execution package: layer 3's script, specification, feasibility report and safety report; layer 4's correctness report for that script; the plan review; and the manifest (section 4). The package assembler sits above layers 3 and 4, reads their files, and writes the package.

**Imports.** Layers 1 to 4.

### Layer 5. Surfaces

**Goal.** Let people operate the layers above without those layers knowing which surface is in use.

**Holds.** The offline page (Describe the record), the local workbench, and the command line.

**Rules.** A surface calls the core and reads its reports; it owns no logic of its own, holds no state outside the project folder or the saved schema, and never connects to a hospital database. The offline page runs with the tab offline and lets nothing leave the browser but the saved file. The workbench binds to localhost and writes only inside its project folder.

**Imports.** Layers 1 to 4 and orchestration; imported by nothing.

### Reference adapters

These are not a layer. They are adapters for material from outside the hospital: the vendor's public specification, the pinned vocabulary, a reference conversion and its extracted lineage, the comparison report, and the transplant of a reference's routes into a draft conversion.

**Rules.** An adapter contributes candidate evidence through a defined file (a dictionary, a lineage, a draft conversion) that layer 2 or layer 3 reads as an input of stated provenance. The core never imports an adapter's implementation, and no ordinary audit depends on one at run time. Nothing an adapter holds or produces enters the public repository, the page, or the public workspace. A model never reads adapter material; tools read it, and a counts-only summary that names nothing may be shown to a model. An adapter's output is evidence for a proposal or a draft of a conversion, never a confirmation.

**Imports.** Layers 1 and 3 for their formats; imported by nothing.

### The public workspace

Not a layer but a projection. It is constructed entirely from allowlisted public definitions and invented data: the role contract, the invented world and its schema, the OMOP field list, the query conventions, and the commands that run on invented data. No hospital material, no reference material and no artefact produced from either is an input to the export. It is the same workspace whichever hospital Schemalyser is being used with. Its rules are invariants 3 and 8.

## 2. What flows where

Every interface is a file, so that each flow can be inspected and so that a surface can be replaced without touching a layer.

| From | To | What | Form |
|---|---|---|---|
| The hospital's database | Layer 2 | The tables and columns query's result; the counts; the code lists; a probe's result; an estimated plan | Results returned by the database analyst, validated and appended to the journal by layer 2's evidence import |
| The vendor's dictionary | Layer 2 | Descriptions | A file loaded in the browser, kept only in the saved schema |
| A reference adapter | Layer 2 | A reference conversion's lineage as candidates | A lineage file, recorded by name and hash |
| A reference adapter | Layer 3 | A draft conversion transplanted from a reference | SQL files with placeholders for every decision, marked as a draft |
| Layer 2 | Layer 3 | The hospital schema | The saved file, by version |
| A person | Layer 3 | A question over the roles, a period, the clinical decisions | A `.sql` file and a `decisions.json` |
| Layer 3 | Layer 2 | Requests for evidence | The feasibility report's evidence requests, which become journal entries once the analyst returns a result |
| Layer 3 | Layer 4 | The conversion steps and the compiled script | SQL files that the testbed runs as they are on SQL Server and in translated form on DuckDB |
| Layer 4 | Orchestration | The correctness report | `report.json` |
| Orchestration | The database analyst | The execution package | A folder with the script, the reports and the manifest |
| The database analyst | Layer 2 | The outcome of a production run, the plan obtained, the reconciliation of a sample against the clinical record | Evidence import, as above |
| The public workspace | Layer 3 | A question written by an agent | A folder validated as untrusted input |
| Layer 3 | The public workspace | Whether the question is well formed over the contract | A fixed status from the allowlisted vocabulary in invariant 8, and nothing else |

Nothing flows from layer 2, a reference adapter, or the hospital's database to the public repository, the page's code, or the public workspace.

## 3. Evidence: dimensions, scope and invalidation

Evidence about a binding, a link or a code translation is recorded on separate dimensions that never collapse into one linear state:

- **confirmed**: a person with knowledge of the database said so, recorded with the date;
- **present**: the tables and columns query found it;
- **tested on made-up rows**: the schema holds together with it on the role-level shadow;
- **reconciled against the database**: a count or probe measured its coverage and the clinician judged it, recorded with the measured figure;
- **clinically validated**: a sample of anaesthetics was reconciled against the clinical record, recorded by a person.

The readiness labels (runs; checked against the database; clinically validated) are summaries of these dimensions for a part, never a substitute for them.

Every piece of evidence carries a **scope**: the hospital, the schema version, the period it was measured over, and where it matters the workflow or codes it covers. A binding may be reconciled from 2024 onward and unmeasured before it, and the feasibility report says so for a question whose period reaches further back.

Evidence is **invalidated** by a change to what it rests on: a change to a binding, a link, a code translation, the role contract's version, or the policy version invalidates the dimensions that depended on it, which the next report shows as stale until they are re-established.

## 4. The execution boundary and the manifest

Schemalyser never executes anything against a hospital database. Production execution is performed by the database analyst, or by a runner the hospital controls, under permissions and resource limits that the database enforces. Static validation and tests on made-up rows do not constitute permission to execute, and an execution package can say **not approved for production** while every test passes.

The package's manifest records: the hash of the resolved production SQL and of the hospital schema version it was compiled from (so that the approval binds to the whole resolved artefact, mappings included); the role contract's version; the question, the cohort bounds, the period and the decisions; the execution class and the policy version that derived it; the permissions the script needs; the expected output's shape and size and the resource assumptions; the plan review's state and the hash of the plan it reviewed; the correctness report it carries; and the analyst's approval state. A change to any hashed input voids the reviews and returns the approval state to not approved.

## 5. The invariants

1. **No model sees the hospital's material.** No language model reads hospital data, hospital metadata, the licensed dictionary, the vendor's specification beyond what its terms allow, or a reference conversion. Tools read them; a counts-only summary that names nothing may come back.
2. **What names the vendor's model stays on hospital systems.** The saved schema, the compiled scripts, the execution packages, the stand-in and the reference material are the hospital's or private; the public repository holds no real name.
3. **A question is written over the roles and only over the roles.** An agent can write or change a question using the public contract but cannot see any hospital's mapping from that contract to its database, nor which parts any hospital supports.
4. **Evidence is never inferred.** A fact's state comes from the saved schema's record of who established it and how; a proposal is never shown as a confirmation; "not currently mapped" is never "not available"; the dimensions of section 3 are recorded apart.
5. **Three reports stay apart.** Feasibility, correctness on made-up rows, and execution safety are never merged into one verdict, and "complete" is never said for less than clinical validation.
6. **Production equivalence and cross-engine equivalence are separate guarantees.** The SQL Server harness executes the exact production artefact. DuckDB executes a translated form, and the translation's rewrites are named and the outputs compared. A change to the production SQL or to the resolved mappings it was compiled from invalidates the test and safety evidence that referred to it.
7. **Schemalyser never executes against a hospital database,** and no surface connects to one. Section 4 governs what may run and who runs it.
8. **The public workspace is built from allowlisted public inputs only,** and what returns to it is a fixed status from an allowlisted vocabulary: `accepted`, `malformed` with the names of the rules that failed, or `requires_private_review`. No count, verdict, class, requirement, table, column, code, error text or plan information returns. The exporter is tested with planted confidential identifiers, none of which may reach its output.
9. **A surface owns no logic.** Anything a surface does, the command line can do with the same files.

## 6. The dependency rules, which a test enforces

- Layer 1 imports nothing from the other layers.
- Layer 2 imports only from layer 1.
- Layer 3 imports from layers 1 and 2.
- Layer 4 imports from layers 1 to 3.
- Orchestration imports from layers 1 to 4.
- Layer 5 imports from layers 1 to 4 and orchestration, and is imported by nothing.
- Reference adapters import from layers 1 and 3 and are imported by nothing; the core obtains their outputs by reading the files they write.
- No module in the core imports from `site/`, `tools/` or the workbench.

The test reads each module's imports and fails on any edge that breaks these rules, so that a new module must declare its layer and keep to it. Modules marked superseded in `docs/architecture.md` are exempt as importers until they are retired, and an active module importing a superseded one is reported.
