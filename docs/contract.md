# The architecture contract

This page states the layers of Schemalyser, the goal of each, what each takes in and gives out, and the rules that govern how they interact. It is the contract that the code is held to; `docs/architecture.md` describes the parts, and this page says why they are arranged as they are. A test enforces the dependency rules in section 6.

Two kinds of dependency are kept apart throughout. **Import dependency** constrains code: a module may import only from the layers above it. **Workflow dependency** is the flow of outputs between layers through versioned files, each carrying its provenance, and may run in any direction the flows table allows. A layer never imports a layer below it to obtain that layer's results; it reads the file the lower layer wrote.

Schemalyser is an evidence-management system first and a query compiler second. Every decision the compiler makes traces to a clause of the role contract, a recorded piece of evidence in the hospital schema, or a stated limitation.

## 1. The layers

The layers are listed from the most stable to the most changeable. Each imports only from the layers above it.

### Layer 1. The role model

**Goal.** A stable, vendor-free description of the anaesthetic record that audits are written against and that any hospital can be mapped onto.

**Holds.** The parts of the record (patients, anaesthetics, readings, and the draft parts), their columns with types and meanings, the vocabularies of kinds, the mapping views, the rules of the record, and the relation of each part to OMOP.

**Interface.** `contract.json`, versioned, and `roles.md` in words. A query over the roles is SQL over the role views and nothing else.

**Changes.** Only by a new version of the contract, which invalidates the evidence of every schema and audit that depends on the changed parts until they are re-run. The three parts of version 1 change their columns for no reason short of a version 2.

**Two kinds of public interface.** The parts describe what the source recorded. The mapping views translate a hospital's local codes into standard concepts without exposing them. A closed vocabulary of kinds serves a small domain, such as readings, where the contract can name every kind; a mapping view serves an open one, such as drugs, procedures, diagnoses and laboratory tests, where it cannot. Every mapping view has the same shape: an opaque local key, the standard concept, a mapping status from a fixed vocabulary (mapped, unmapped, ambiguous) and the provenance of the mapping. The real local code and its description live only in the hospital schema, which populates the view. A row whose status is unmapped is an ordinary outcome that a query or a conversion step handles, never an error, and the invented world plants unmapped and ambiguous rows so that a transformation is tested against them.

**Grain.** A part records what the source recorded, at the grain the source recorded it, and never a derived interval or a reconciled outcome. An infusion is its start, rate-change and stop events, not the interval between them; a reading is each value the monitor recorded, not the one a person would keep. A part that records events carries a source kind: a column drawn from a public vocabulary of kinds of record, never from a vendor's table names, that says what kind of record the row came from, so that rows reaching one part by several pathways arrive marked and the query decides between them. The draft parts take this column as they are promoted.

**The capability catalogue.** Above the parts sits a catalogue of named clinical capabilities, each versioned and expressed only in the roles' terms: what it measures, at what grain, in what unit, and which parts, columns, kinds and links it requires (for example, intraoperative transfusion volume per anaesthetic requires the anaesthetic's identity, the fluids part, its volume, and the attribution of a fluid to an anaesthetic). A capability may be declared before any hospital supports it, and a question may require one that no hospital supports yet; the catalogue is how the clinical vocabulary grows without the role contract changing. A capability's requirements are what the feasibility report resolves, and an unsupported requirement is an ordinary outcome that produces an evidence request, never an error. A draft part becomes part of the stable contract when capabilities that depend on it have been validated at a hospital, and not before.

**Rule.** The role model and the catalogue never acquire a hospital's code, a vendor's description or a reference conversion's terminology. They are the same files at every hospital. They describe the anaesthetic record and no more: the core OMOP tables that a hospital's central team maintains are not the role model's concern beyond the keys that the anaesthesia layer joins onto, and a part is never added so that a conversion can write a table the core already writes.

**Imports.** Nothing.

### Layer 2. The hospital schema

**Goal.** One file per hospital that is the single authoritative record of where that hospital's database keeps each part of the record and of every piece of evidence about it: who established each fact, how, when, and within what scope.

**Holds.** The proposed and confirmed bindings and links, the code translations that populate the mapping views, the counts and judgements, probes and their results, an append-only journal of every query offered and every result returned, provenance for every fact (complete data, a sample, metadata, a person, an inference, a reference conversion), the evidence dimensions of every binding (section 3), and a copy of the data dictionary.

**Interface.** The saved hospital schema, one zip file with a version identifier, written only by layer 2's own operations and read by everything below. Each save is a new immutable version; reports and packages name the version they were made from and are stale once a newer version exists.

**Operations, all owned by layer 2.** Propose from a dictionary; confirm, correct or mark not sure; translate codes; record a count or probe result; and **import evidence**: an investigation that a lower layer requested (a probe, a values query, a count, a plan) is executed by the database analyst, and its result is validated and appended to the journal by layer 2, which then publishes a new version. No lower layer writes to a schema by any other route.

**Rule.** The hospital schema binds, translates and normalises, and it does nothing else. A binding says which of the hospital's tables and columns hold a part. A translation says which local code means which kind or which standard concept. A normalisation interprets the vendor's storage conventions as far as is needed to expose a faithful event of the part: it resolves an identifier through another table, pivots a tall record into the part's columns, decodes which kind of event a record is from the code the source stores, and names the column that holds the event's time. A normalisation that is no more than naming a column is recorded as a binding. One that is more, such as a pivot or a route through several tables, is recorded as a named normalisation of its own, versioned with the schema, with its inputs, its output grain, its SQL, its assumptions and its tests, so that complicated source logic is never hidden inside what looks like a column binding. Every binding, translation and normalisation bears the evidence of section 3, is tested on made-up rows, and carries its uncertainty where a person was not sure. What the schema never does is make a decision specific to an audit, a question or an OMOP destination: it does not collapse duplicates, select the events that matter to a study, infer an event's kind from the sequence it sits in, or choose between overlapping pathways by judgement. Where several pathways feed one part, every pathway's rows reach the part marked by their source kind, with the coverage of each measured and recorded. A binding that would need such a decision is not made. The decision is lifted into a column of a part or a capability of the catalogue, where a test on made-up rows can see it.

**Imports.** Layer 1.

### Layer 3. Compilation and reports

**Goal.** Turn a question over the roles and a hospital schema into artefacts that are honest about what each establishes.

**Holds.** Compilation of a role query into SQL over the hospital's tables; the feasibility report (what a question needs and the evidence state of each requirement, with the investigations that would move each gap, as requests for layer 2's evidence import; it keeps apart two claims that are never merged, that every declared requirement of the question is satisfied, and that the coverage of the source pathways relevant to the question has been independently assessed, since a question can rest on validated bindings and still miss a recording pathway no binding knows of, and only a person's assessment, recorded as evidence with its scope, establishes the second); the static policy on the final SQL and the execution class it derives; the specification; the OMOP conversion steps and the release script.

**Where the clinical logic lives.** Reconstructing an infusion's exposure intervals from its events, collapsing duplicate readings, attributing a record to an anaesthetic by a time window, and joining a mapping view and handling its unmapped rows are this layer's work and the question's. The logic is written over the roles and the mapping views, tested on made-up rows against independently written expected output, and never pushed down into a binding, where no test would see it. It never invents what the source does not say: a missing stop is not an infusion that ran to the end of the anaesthetic, a missing volume is not zero, and a gap in the monitoring is not a stable patient. What is unknown is carried as unknown, with its reason, and kept apart from what is known to be absent.

**Two transformation routes to OMOP.** A conversion step is written over the roles and the mapping views. That is the route for all new work, because it is the route an agent can take on invented data. A step may instead be written directly from the hospital's source tables to OMOP only where a reference implementation of that step already exists and gains nothing from the intermediate; such a step is recorded in the conversion as an exception, with the reference it rests on, the reason, and the review that accepted it, and the conversion's report states what share of its steps take each route, so that new work is seen to go through the roles. A well-founded exception may stand indefinitely, and finding a sound reference may rightly add one. Both routes carry the same obligations: the same evidence requirements in the hospital schema, the same planted scenarios with independently written expected rows, the same reconciliation, dashboard and release-equivalence checks, and the same approval. The direct route belongs to the private conversion only; a question written by a person or an agent for an audit is written over the roles and the catalogue and never over source tables.

**Progressive execution.** Anything that reaches a large table is compiled as a series of bounded operations of increasing size, each of which the analyst can stop after: first a count over a narrow interval, then the coverage of a link, then and only then the cohort's rows from the large table. The policy requires the series; a script that approaches a large table without the earlier steps is class C at best.

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

**Rules.** An adapter contributes candidate evidence through a defined file (a dictionary, a lineage, a draft conversion) that layer 2 or layer 3 reads as an input of stated provenance. The core never imports an adapter's implementation, and no ordinary audit depends on one at run time. Nothing an adapter holds or produces enters the public repository, the page's code, or the public workspace; the owner may load an adapter's file into the page as they load the dictionary, and the saved schema holds it as confidential material. A model never reads adapter material; tools read it, and the summaries named in section 4 are the only part of their output that the owner may show to the developer's model. An adapter's output is evidence for a proposal or a draft of a conversion, never a confirmation.

**Imports.** Layers 1 and 3 for their formats; imported by nothing.

### The public workspace

Not a layer but a projection. It is constructed entirely from allowlisted public definitions and invented data: the role contract, the invented world and its schema, the OMOP field list, the query conventions, and the commands that run on invented data. No hospital material, no reference material and no artefact produced from either is an input to the export. It is the same workspace whichever hospital Schemalyser is being used with. Its rules are invariants 3 and 8.

The invented material is of two kinds, and the distinction holds throughout the project. **Development fixtures** are the invented world and the commands that run on it, which an agent iterates against. **Held-out fixtures** are the planted scenarios and expected rows that judge an agent's work, written independently, kept outside the workspace, and never shown to the agent, so that a transformation cannot be fitted to the examples it can see. A clinical question is judged the same way as a conversion step.

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

### Classes of material

Three classes of material are kept apart, and each has one rule for where it may be and who may read it.

- **Public.** The role contract and the catalogue, the invented world and its schema, the OMOP field list, the query conventions, and the code and documents of the public repository. Anything in this class may be read by any model and is the only class that enters the public workspace.
- **Confidential definitions.** The vendor's dictionary and specification under its licence, a reference conversion, the hospital's own conversion, and everything derived from them: the saved hospital schema, the compiled scripts, the execution packages and the realistic stand-in. This class stays in the hospital's systems or in an environment the hospital has approved for it. Tools read it; no model does.
- **Patient-derived material.** Rows, counts, probe results, estimated plans, results packages and reconciliation findings. This class stays inside the hospital, and whether any of it leaves is an approval under the hospital's own rules.

The owner may show the developer's model a **named summary** of confidential material, and nothing else of it: the proposal scoreboard, the summary at the top of a comparison report, and an adapter run's counts of what it read, could not read and could not parse. Each has a fixed format, is produced by a tested exporter from permitted counters alone, and names nothing. A summary is safe to show because the owner has read the exact text generated and decided to show it, and not because it holds only numbers; each is a small disclosure that the owner makes knowingly. No named summary enters the public workspace, where invariant 8 allows a fixed status and nothing else.

### The transfer boundary

A question package arriving from the public workspace is untrusted input. The import accepts only declarative files (the question over the roles and the catalogue, a title, a note, and constrained test definitions), refuses any executable content, symlink, archive or dependency declaration, validates the question against the role policy before anything else reads it, and runs nothing the package supplies. The private environment is not reachable from an untrusted computer through any open interface; a package moves through a mechanism the hospital approves, and is scanned and validated on arrival.

### The results boundary

A production run's output is a results package, separate from the execution package and held inside the hospital: the approved query's version and hashes, the cohort definition, the coverage limitations the schema records for the period, the disclosure-control state (what was rounded or suppressed and what that does and does not protect), the output itself, and the reconciliation evidence. Whether any result leaves the hospital is a separate approval under the hospital's own rules. No result, aggregated or not, returns to the public workspace.

### The property this gives

Schemalyser does not promise that a language model can never touch confidential information, since a person can type anything into a model. It gives a testable property: **an external model has no technical path through Schemalyser to any hospital's material, and nothing that returns to it depends on which hospital is present.** The second half is tested directly: the same package imported into two projects with different hospital schemas, one of which supports the question and one of which does not, returns the same status. The first half is tested with planted identifiers: a private hospital schema is built with recognisable planted names, and the test asserts that neither the exported workspace nor any status returned to the agent contains one. The import boundary is tested the same way, with hostile question packages: one that tries to read a local file, one that names an external resource, one that carries a statement other than a query over the roles, and one built to provoke an error, and the test asserts that none is executed and that no diagnostic, path or name reaches the returned status.

## 5. The invariants

1. **No model sees the hospital's material.** No language model reads hospital data, hospital metadata, the licensed dictionary, the vendor's specification beyond what its terms allow, or a reference conversion. Tools read them. The only thing that comes back to a model is one of the named summaries of section 4, shown by the owner to the developer's model and never to the public workspace.
2. **What names the vendor's model stays in an environment the hospital approves.** The saved schema, the compiled scripts, the execution packages, the stand-in and the reference material are held in the hospital's systems or in an environment the hospital has approved for them, as section 4 classes them; the public repository holds no real name.
3. **A question is written over the roles and only over the roles.** An agent can write or change a question using the public contract but cannot see any hospital's mapping from that contract to its database, nor which parts any hospital supports.
4. **Evidence is never inferred.** A fact's state comes from the saved schema's record of who established it and how; a proposal is never shown as a confirmation; "not currently mapped" is never "not available"; the dimensions of section 3 are recorded apart.
5. **Three reports stay apart.** Feasibility, correctness on made-up rows, and execution safety are never merged into one verdict, and "complete" is never said for less than clinical validation.
6. **Production equivalence and cross-engine equivalence are separate guarantees.** The SQL Server harness executes the exact production artefact. DuckDB executes a translated form, and the translation's rewrites are named and the outputs compared. A change to the production SQL or to the resolved mappings it was compiled from invalidates the test and safety evidence that referred to it.
7. **Schemalyser never executes against a hospital database,** and no surface connects to one. Section 4 governs what may run and who runs it.
8. **The public workspace is built from allowlisted public inputs only,** and what returns to it is a fixed status from an allowlisted vocabulary: `accepted`, `malformed` with the names of the rules that failed, or `requires_private_review`. Every status is a function of the package and the public contract alone, never of any hospital schema, so that the same package returns the same status whichever hospital the project holds, and the public workspace's own check gives the same answer before the package is sent. Whether a hospital can answer the question is the private feasibility report's to say, and it is read by the owner and never returned. No count, verdict, class, requirement, table, column, code, error text or plan information returns. The exporter is tested with planted confidential identifiers, none of which may reach its output.
9. **A surface owns no logic.** Anything a surface does, the command line can do with the same files.
10. **Source normalisation is private; clinical logic is public.** The hospital schema interprets the vendor's storage conventions as far as a faithful event needs and holds no decision specific to an audit, a question or an OMOP destination; a question or a conversion step holds no hospital's name. A decision found in a binding is lifted into the role model or the catalogue, and a transformation that an agent writes over the roles is tested on invented rows before any hospital's mapping supplies it.

## 6. The dependency rules, which a test enforces

- Layer 1 imports nothing from the other layers.
- Layer 2 imports only from layer 1.
- Layer 3 imports from layers 1 and 2.
- Layer 4 imports from layers 1 to 3.
- Orchestration imports from layers 1 to 4.
- Layer 5 imports from layers 1 to 4 and orchestration, and is imported by nothing.
- Reference adapters import from layers 1 and 3 and are imported by nothing; the core obtains their outputs by reading the files they write.
- A **shared** tier of utilities with no knowledge of any layer (SQL parsing and translation, the catalogue reader, memoisation, vocabulary tables) may be imported by any layer and imports only from itself.
- Modules within one layer may import one another.
- One reference adapter may import another; neither is imported by the core.
- No module in the core imports from `site/`, `tools/` or the workbench; anything the core needs from `tools/` moves into the core.

The test reads each module's imports and fails on any edge that breaks these rules, so that a new module must declare its layer in `LAYERS.toml` and keep to it. Modules marked superseded are exempt as importers until they are retired, and an active module importing a superseded one is reported. Edges that broke the rules when the test was first written are listed in the test as known debts, each with its planned fix, so that the test fails on any new edge while the debts are paid down; the list only shrinks.

## 7. The acceptance test

The contract is met, and the product exists, when this runs end to end on invented material and can be repeated:

1. On a computer with no access to hospital resources, a coding agent is given the exported public workspace and nothing else.
2. It writes a new clinical question over the roles and the catalogue, checks it, and produces a question package.
3. The package is transferred into a private Schemalyser project and imported as untrusted input.
4. The feasibility report names the capabilities the question requires and the evidence state of each against the project's hospital schema.
5. The question passes independently specified planted scenarios on made-up rows, held out from the workspace the agent was given.
6. The compiler produces the two-part script for a private synthetic SQL Server, and the harness executes that exact artefact.
7. The static policy, the execution class and the plan review are produced.
8. The execution package is written, and nothing about the hospital returns to the agent beyond the fixed status, which is the status the workspace's own check already gave.
9. A database analyst inspects the package and records approval or refusal in its manifest.

### The boundary test

The second test judges whether the boundary between source normalisation and clinical logic holds for a genuinely difficult workflow, and it is run before any draft part beyond the first is promoted. The invented world holds an intraoperative infusion as the source records it: an order, an administration start, two rate changes, a pause and a restart, a missing stop, a second anaesthetic within the same admission, and a retrospective correction. The private side of the invented hospital normalises those records into the drug part's events with their source kinds. A coding agent given only the public workspace writes the transformation that reconstructs the exposure intervals and produces the OMOP rows, and its output is checked against expected rows written independently and kept outside the workspace. The test passes when the agent needs nothing beyond the contract to do it, and when its output represents the missing stop as an interval of unknown end rather than inventing a duration. If the agent repeatedly needs to know how the vendor represents a record, that is evidence that the role contract is missing a column, a kind or a capability, and never a reason to show the agent the vendor's model.

Every feature is judged by whether it makes these paths easier, safer or more honest. The shadow database, the evidence record, the role contract, the policy and the OMOP testbed are the mechanisms; the path is the product.
