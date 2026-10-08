# Architecture

Schemalyser helps a clinician write an audit of anaesthetic records, test it on invented data, and have it run once on the hospital's real database by a database analyst who has access. It describes the anaesthetic record once, in terms that belong to no vendor, as a set of parts. For each hospital, the clinician and the database analyst then confirm in one sitting where that hospital's database keeps each part, and save the result as the hospital schema. An audit written against the parts is tested on made-up rows and compiled, through the hospital schema, into a script that is safe on production. This document describes what exists today. `docs/history.md` records the designs that came before it, and `docs/roadmap.md` sets out the order of the work still to come.

## The standing rules

Four rules stand over every part of the project.

- No language model ever sees hospital data, hospital metadata or the vendor's licensed data dictionary.
- Everything is built and tested on invented data and on the vendor's public specification, never on a hospital's own data.
- Anything that names the vendor's data model once a person has confirmed it, which includes the hospital schema, a shadow database built from it and any query written against the source tables, stays on hospital systems.
- The public repository holds no real table or column name, no hospital's name, no vendor's name and no credential.

The role model, the tool, the OMOP side and the invented worlds are public, and a language model may help with them. The hospital schema and everything made from it are on the other side of the line.

## The three screens and the one saved file

The page has three screens, and the hospital schema is the one thing that passes between them.

1. **Describe the record** is built and published at https://uppertoe.github.io/schemalyser/ . It is the only page the site now serves. The clinician, who leads the audit, and the database analyst, who runs the queries, use it once for each hospital to make the hospital schema.
2. **An audit** is to come. It will read the hospital schema and an audit, build a shadow from the schema, plant the standard cases, run the audit on made-up rows and compile the safe script for production.
3. **The OMOP layer** is to come. It will read the hospital schema and write the release script for the anaesthesia layer of the hospital's OMOP database.

The page saves the hospital schema as a single file, `hospital-schema.schemalyser.zip`, which the clinician keeps on the hospital's own storage. It holds a copy of the data dictionary, every answer and correction, one SQL view for each part, the exact text of every query offered with its pasted result, the chosen codes and the judgement of each count, and a `README.md` that explains each part in plain words. Opening the file at step 3 of the page restores the sitting where it was left.

## The role model

`core/schemalyser/rolemodel/roles.md` describes the anaesthetic record in words, with no hospital or vendor in mind, and `contract.json` holds the same model as data, which the code reads. Each role view is one part of the record, from patients, anaesthetics and readings to drugs, devices, events, notes and outcomes. Ten rules of the record apply to every hospital; the most consequential is that a reading or an event belongs to an anaesthetic by its link to that anaesthetic, and never because its time falls within it.

Version 1.0 of the contract is three views, which every hospital schema must supply and on which an audit may rely:

- `role_patient` (patient_key, birth_date, death_date, is_test);
- `role_anaesthetic` (anaesthetic_key, patient_key, start_time, stop_time);
- `role_reading` (anaesthetic_key, kind, reading_time, value, accepted, reading_key, value_text).

The fourteen further views are drafts that no audit reads yet. A change to the three contract views is a new version of the contract.

The roles are an audit interface, not a data model of their own. They are filled from the source tables today, and could be filled from an OMOP database later. `roles.md` states for each view the OMOP table it projects onto, and where a role is not a plain projection it says exactly what it adds: for example, `role_reading` links each reading to its anaesthetic by the record on which it was charted, where OMOP's MEASUREMENT links it only to a visit, and keeps whether a value was accepted.

The neonatal audit, `rolemodel/neonatal_low_mean_pressure.sql`, is one SELECT over the three contract views: it asks how long each neonatal anaesthetic spent with a mean arterial pressure below 40, and how many of the children died within 90 days.

## The hospital schema

The hospital schema is a map from the parts of the record to one hospital's tables and columns. In code it is a map folder (`rolemap.py`): `map.json`, which records every binding with its evidence, and one SQL view for each role, written from the bindings. Screen 1 makes it in nine steps, and `core/schemalyser/describe.py` does the work behind each.

**The proposal.** The page reads the vendor's data dictionary in one of three ways: made from the database by one query that the page gives, which reads only the server's own records; uploaded as an export from the vendor's dictionary tool; or, to try the page, the invented dictionary. `datadict.py` reads the file and holds each description only in memory. `propose.py` then proposes, for each view, its table and, for each column, the table and column that play it, by plain, deterministic word matching against the dictionary's descriptions and names. Each proposal carries a confidence, up to three alternatives, and the description with the words that matched. No model is involved.

**Confirmation.** A query of the server's own records says which proposed tables exist and how large they are. The database analyst then answers each column yes, no with a replacement, or not sure. A column whose name suggests that it identifies a person is never offered.

**Corrections.** Where a binding needs more than a table and a column, the page offers structured forms rather than SQL (`corrections.py`), such as a value derived from a column, a filter on the view's rows, a link through up to three tables, a link by shared key with a time window, and the translation of a column's local codes into the role's kinds. Each correction is stored in `map.json` as data, never as SQL, and `propose.view_sql` writes the SQL from it.

**The test on made-up rows.** Before any correction is kept, Schemalyser builds a small shadow shaped as the bindings name the tables, with invented rows and the planted neonates, compiles each view, checks it against the contract and the rules of the record, and runs the neonatal audit against each planted case's expected answer. A correction that fails can be kept only with a recorded reason.

**The codes and the counts.** The codes of each kind are chosen from a list of what was actually charted on the anaesthetics of one year. Three counting queries then check the schema against the database: the anaesthetics of each year, with how many have a patient, a stop, any reading and an accepted mean pressure; any patient or anaesthetic on more than one row; and the readings of one year by kind. The two people judge whether each looks right. A wrong map rarely fails outright; it moves children silently into the band in which nothing was recorded, and these counts are what show it.

**Provenance.** Every fact in the saved file says whether it came from complete data, a sample, metadata, a person, or an inference that no person has yet answered.

**Readiness.** Each part of the schema records the date on which it reached each of three states. A part *runs* once it compiles and passes the test on made-up rows. It is *checked against the database* once the counts that read it have been run on a production database and judged to look right. It is *clinically validated* only once a sample of anaesthetics has been reconciled against the clinical record. The page cannot do that reconciliation, so it never records the third state. The schema as a whole stands at the lowest state of the three contract views.

**The scoreboard.** `rolemap.scoreboard` reads a saved hospital schema and reports how many proposals were confirmed as proposed, corrected to a listed alternative, corrected to something unlisted, or left open, and the share corrected at each level of confidence. It prints counts and the plain names of the parts only.

## The invented hospital and the realistic stand-in

Two made-up worlds serve different purposes.

**The invented hospital** is public. `fixtures/` holds an invented catalogue, data dictionary, conversion and planted values, and `fixtures/make_hospital.py` writes the invented world's tables to `fixtures/hospital/`. When a person chooses the invented dictionary, the worker builds a DuckDB database from these files that answers every query on the screen as SQL Server would, so the whole of screen 1 can be tried with nothing from any hospital. A schema made this way is marked as practice.

**The realistic stand-in** is private and lives outside version control. It is built from the vendor's public table specification (`tools/ehi_spec_to_reference.py` converts a downloaded copy into a reference catalogue), so that the tool meets tables shaped as a real installation's are. The proposer's agreement with an independently written map on that specification, 13 of 16 bindings for the three contract views, comes from this stand-in, and the test that checks it keeps its expectations in the private folder.

## Safe scripts for the real database

Every query that the page offers for production reads only the server's own records, or only the small tables, or is a two-part script (`scripts.py`, and the same form in `describe.py`). Part 1 selects at most 5,000 anaesthetics of one period into a temporary table, `#cohort`, with a primary key. Part 2 reaches the larger tables from `#cohort` by key alone, with the table of readings joined last. SQL Server cannot reorder joins across statements, so the cohort is fixed before any reading is touched. This form was adopted after a single long query filled a test server's temporary database to about 96 GB in a rehearsal. A cohort that joins on an expression hiding a key is refused.

Each table is read `WITH (NOLOCK)`, which takes no row locks but still holds a schema lock, so the scripts should not run during the nightly load. Every count is rounded down to ten, and a group of fewer than ten rows is left out. The rounding has limits: the minimum applies to rows, not to people, so one patient with many rows can fill a group; and counts are rounded rather than randomised, so the difference between two runs can still reveal a small number. An audit's own result leaves blank any count from 1 to 4 unless its approval allows exact small numbers.

## The conversion to OMOP

The conversion predates the role model and still reads the source tables directly. `convert.py` runs a conversion folder over a synthetic database in three layers: a core layer that stands in for the hospital's existing OMOP database and is never deployed; the anaesthesia layer, which is what would ship; and a derived layer of custom tables, such as one row for each anaesthetic. Each step is one T-SQL SELECT named as the fields of an OMOP CDM 5.4 table. The runner checks every row against the CDM field list, runs gates that list rule breaches, and plants scenarios: hand-written source rows, each with the OMOP rows and answers it must produce. `fixtures/conversion/` is the invented world's conversion; the conversion for the real source is private.

`release.py` writes the release script for the anaesthesia layer, to run with `sqlcmd` after the core refresh. It keeps the layer's rows in a schema of its own, publishes a complete CDM as views that OHDSI tools can read, runs inside one transaction and changes no core table. Everything it carries is checked first, because `sqlcmd` substitutes `$(name)` wherever it appears.

`tools/sqlserver/harness.py` runs the same T-SQL on SQL Server 2022 in a container, over the same synthetic rows, runs the release script as an operator would, tries its safeguards, and reports every place where SQL Server and DuckDB disagree. `export_published.py` exports the result for ATLAS.

## The page's isolation

The page is static. A content security policy in `site/index.html` forbids any connection to another site. The core runs in Python under Pyodide, with DuckDB and sqlglot, inside one web worker, all served from the site itself. The page starts the worker from a blob, so that it inherits the page's policy, and the worker confirms that the policy refuses another site before it starts; Safari does not report that refusal, so the page will not start there. Once loaded, the worker removes its own means of making a request.

The page accepts no file and no paste until the browser reports that the tab is offline; the only thing it fetches while online is the invented dictionary with the invented hospital, from the site itself. Taking only the tab offline leaves the SQL window on the same computer connected. If the tab goes back online, the page ends the worker and lets go of the dictionary and of everything pasted. The page uses no cookies and no browser storage. Nothing leaves the browser except the saved hospital schema, which the person downloads.

These safeguards do not protect against a tampered host or against browser extensions; the defences there are a reviewed release served from a host the hospital controls and a clean browser profile.

## How it is tested

Three jobs in `.github/workflows/tests.yml` run on every push to the main branch and on every pull request, and all of them use invented data only:

- the core tests, with pytest and DuckDB, over the invented world, including tests that fail if any planted value reaches an output;
- the page tests, with Playwright in Chromium and Firefox, which walk screen 1 offline with the invented hospital answering every query, through to a complete save and reopening it;
- the SQL Server harness, on a throwaway SQL Server with a password made up for the run.

A second workflow publishes the page to GitHub Pages. Runs on the realistic stand-in are private.

## The command line

The command line serves the developer rather than the meeting. `python -m schemalyser.rolemap` checks and compiles a map, rehearses an audit on a role-level shadow, proposes and confirms a map, and prints the scoreboard of a saved schema. `schemalyser.convert`, `schemalyser.release` and the harness serve the OMOP side, and `schemalyser.testbed` runs them together on a synthetic world and reports the result, as `docs/testbed.md` describes.

The modules of the earlier design remain in the core and its tests: the analyser of SQL repositories, the check script, the register of open questions, the target queries with their checklists, and the boundary command with its container. No page uses them, and screen 2 will take what it needs from them before they are retired.

## The modules of the core

Every module in `core/schemalyser` falls into one of four classes. A module of the *active workflow* does the work of screen 1 or of the hospital schema that it saves. A module of *shared infrastructure* is used by the active workflow and by the OMOP side alike. A *candidate for the testbed* belongs to the conversion to OMOP and to the one-command test run on the invented source. A *superseded* module belongs to the earlier design, which `docs/history.md` describes; no page uses it, and it is to be retired once nothing outside its own class depends on it. The table gives the class of each module and, where it matters, the dependency that holds it in place.

| Module | Class | What it does |
| --- | --- | --- |
| `browser.py` | Active workflow | The functions that the page's worker calls. The page now calls only its functions for describing the record; the rest serve the earlier pages. |
| `describe.py` | Active workflow | Screen 1, describing the record, and the saved hospital schema. |
| `propose.py` | Active workflow | The proposer, which drafts a map from the role model and the data dictionary by plain matching. |
| `corrections.py` | Active workflow | The structured corrections of screen 1, each tested on made-up rows before it is kept. |
| `datadict.py` | Active workflow | Reads the vendor's data dictionary and holds its descriptions in memory only. |
| `rolemap.py` | Active workflow | The roles, the maps, the compiled audit, the standard counts and the scoreboard. It still takes the blanking of small counts from `target.py`, and its comparison with the OMOP target runs through `target.py` as well. |
| `hospital.py` | Active workflow | The invented hospital, on which the page runs its own queries when the invented dictionary is in use. |
| `first_ask.py` | Active workflow | The query of the server's own records, which screen 1 uses for the tables and columns and for the data dictionary made from the database. |
| `feasibility.py` | Active workflow | Whether a question over the parts of the record can be answered from a saved hospital schema: its requirements, the state of each, the evidence requests that would move them, and the programme view over many questions. `docs/feasibility.md` describes it. |
| `__init__.py` | Shared infrastructure | Marks the package. It still exports the earlier analyser, which keeps that module in place. |
| `catalogue.py` | Shared infrastructure | The tables and columns that exist, and the allowlist for names. |
| `extract.py` | Shared infrastructure | Reads files safely for every module. Its finding of facts in a request belongs to the earlier design. |
| `statements.py` | Shared infrastructure | Prepares T-SQL for the parser. |
| `translate.py` | Shared infrastructure | Translates T-SQL into statements that DuckDB can run. |
| `sandbox.py` | Shared infrastructure | Builds the synthetic DuckDB database from a catalogue. It still reads the earlier check script's results. |
| `realistic.py` | Shared infrastructure | Writes realistic values into the synthetic database from public reference data. |
| `realism/build_data.py` | Shared infrastructure | Regenerates the growth reference files that the realistic values use. |
| `roles.py` | Shared infrastructure | Says what a column means, so that the synthetic database can fill it realistically. |
| `tuning.py` | Shared infrastructure | The tunable numbers behind the realistic values. |
| `rules.py` | Shared infrastructure | The site rules file. |
| `vocabulary.py` | Shared infrastructure | The fixed words that the tool may write. |
| `memo.py` | Shared infrastructure | Keeps the results of pure work by the content of their inputs. |
| `testbed.py` | Candidate for the testbed | The one-command run over a synthetic world, which builds, converts, checks and reconciles. |
| `convert.py` | Candidate for the testbed | Runs a conversion to OMOP over the synthetic database, checks what it writes and exports it. |
| `release.py` | Candidate for the testbed | Writes the release script for the anaesthesia layer. |
| `harness.py` | Candidate for the testbed | Runs the pipeline over a world. The test run uses its worlds, although it was written for the earlier design. |
| `mapping.py` | Candidate for the testbed | Proposes mapping rows from the labels of local codes to standard concepts. |
| `concepts.py` | Candidate for the testbed | The names of the concepts that a conversion uses. |
| `dictionary.py` | Candidate for the testbed | The data dictionary of a conversion, which the export for work with a language model is likely to draw on. |
| `profile.py` | Candidate for the testbed | The profile of a core OMOP database that the anaesthesia layer cannot see. |
| `routes.py` | Candidate for the testbed | Chooses, for each step of a conversion, a route whose tables and columns the catalogue holds. |
| `__main__.py` | Superseded | Runs the earlier analysis from the command line. |
| `analysis.py` | Superseded | Runs the extraction over a set of requests and writes the inventory pack. |
| `boundary.py` | Superseded | Runs everything that reads confidential input in one run, for the earlier design. |
| `checks.py` | Superseded | The check script and the reading of its results. The synthetic database still depends on it. |
| `charted.py` | Superseded | The list of what is charted, composed over the source tables. `describe.py` now writes its own. |
| `facts.py` | Superseded | Facts that a person confirmed, for the earlier checklist. |
| `questions.py` | Superseded | The register of open questions about the real hospital. |
| `restructure.py` | Superseded | Rewrites a target query's source query to start from the cohort. |
| `scripts.py` | Superseded | The two-part scripts of the earlier design. Screen 2 takes their form, which `describe.py` already shares. |
| `skeleton.py` | Superseded | Rebuilds an expression from a request out of allowlisted parts. `extract.py` still depends on it. |
| `sql_evidence.py` | Superseded | What the team's SQL showed, kept for the earlier checklist. |
| `target.py` | Superseded | Ties the earlier project to one target query and its checklist. `rolemap.py` still depends on it, so it is retired last. |

Nine modules are active workflow, thirteen are shared infrastructure, nine are candidates for the testbed and twelve are superseded.
