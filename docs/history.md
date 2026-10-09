# History

Schemalyser went through several designs in its first week, and each gave way when something the project had assumed about the hospital turned out to be different. This document records them in order, so that a reader who meets an older module in the core knows where it came from.

## The premise of the first design

The owner set the project's boundaries on 3 October 2026: no language model may see hospital data, the hospital's metadata or the vendor's licensed material, and the public repository may hold no real table or column name. The analytics team would run only the occasional reviewed query.

From those constraints the first design took a strong premise: that the tool knew nothing about the hospital's database and could ask no one. Every fact had to be earned from evidence, which could be the data team's existing SQL, counts returned by the database, or a person's answer, and each fact was tracked item by item.

## The repository analyser and the check script

The first public commit, on 5 October, held a repository analyser. It read the data team's SQL repository and the hospital's catalogue of tables and columns, and wrote an inventory pack: the tables, columns, joins, filters and derivations that the team's queries used, with every string taken from the catalogue rather than from a query, so that nothing from a request could reach an output.

Beside it sat the check script, one long T-SQL script of counting checks, each rounded down to ten and written to be light on a production server. From the inventory and the check results, a sandbox built a synthetic database with realistic values from public growth charts, and a conversion to OMOP ran over it. A boundary command, also packaged as a container with no network, ran everything that read confidential input in one pass.

## The per-question checklist

The tool traced each target query back through the conversion and produced a checklist of everything the answer rested on, typically forty to sixty items for one audit question. Each item was settled by existing SQL, a check result, a profile of the core OMOP database or a person's answer. Routes and alternatives let a conversion step give way when the catalogue lacked a name, and a register gathered the open questions.

On 5 and 6 October the checklist was reshaped for a meeting. The whole check script gave way to short plain queries offered beside each item, one at a time. A first query could be asked without any catalogue. A count by year came before any audit query, codes were chosen from a list of what was actually charted, and decisions that only clinicians could make were set apart.

On 6 October, in a rehearsal on the test SQL Server, one long query filled the server's temporary database to about 96 GB. Every query that reaches the readings became a two-part script, with the cohort set apart in `#cohort` first. That form survives unchanged.

## The first page, with seven steps

By 7 October the page walked a clinician and a database analyst through seven steps, from taking the tab offline and choosing the audit's folder and SQL files to working through each checklist and saving the state as a zip to unzip into the folder. It could fetch its inputs from private GitHub repositories, recognised a training database with fictional patients, and offered a practice database. A reviewer new to the page found nineteen faults in the checklist step that day, and they were fixed.

## The simplification to roles, 7 October

On 7 October the owner read the published page closely for the first time, and two facts changed the premise. The database analyst confirmed that the production schema is essentially the vendor's documented one. And the hospital could supply the vendor's data dictionary for use inside the page. The structure of the record could then be described once, confirmed in one sitting and reused for every audit, and most of the page's eighteen kinds of interaction existed only to gather evidence that was no longer needed.

A prototype had already shown the alternative. The role model described the anaesthetic record with no vendor in mind, a small map supplied it at each hospital, and the neonatal audit written once against three role views gave the same band table as the OMOP target query through the conversion, in DuckDB and on SQL Server. A proposer drafted a map from a data dictionary by plain word matching, and on the vendor's public specification it agreed with an independently written map in 13 of 16 bindings.

The architecture was redrawn as three screens, two folders (one for the hospital, one for each audit) and one line that nothing confidential crosses. Screen 1 was built the same day, with corrections chosen from forms and tested on invented rows before they were kept.

## The single saved file, 8 October

On 8 October the hospital folder became one saved file, the hospital schema, with the dictionary inside it, so that a sitting could be resumed from a single download. The old checklist page was retired, and describing the record became the only published page. The data dictionary could now be made from the database by one query that the page gives. The practice database gave way to the invented hospital, which answers every query on the screen, so the whole of screen 1 can be tried with nothing from any hospital.

## The reviews of 8 October

The owner received three external reviews on 8 October, which made four points.

- The engineering is strong, but the clinical meaning is unvalidated. Readiness has three distinct states: the tool runs, it has been checked against the database, and it has been clinically validated. The decisive test is one clinically verified neonatal audit on the real database, reconciled against the charts, and validation should come before breadth.
- The roles are a lightweight audit interface, populated from the source now and from OMOP later, and not a competing data model. Each role should be a projection of OMOP, or should document what it adds.
- The OMOP stage should be an ETL testbed compatible with OHDSI practice: a one-command test run with a machine-readable result, the DataQualityDashboard run rather than reimplemented, expected outputs written independently of the ETL, and the same transformation in the testbed and in the hospital. Rabbit-in-a-Hat's testing framework and dbt-synthea are precedents, and dbt may be considered later.
- A safe export for language-model work should be a product in its own right, with an allowlist, deterministic renaming, checks and an uncertainty register. The offline page should be kept for hospital-facing work, with a local Python service later for the developer-facing testbed and files as the interface between them. A coding agent should work on an exported workspace, rather than a language model being built into the tool.

The same day, the hospital schema gained the three readiness states, provenance on every fact and a scoreboard of how the proposals fared, and `roles.md` gained its section on how each role relates to OMOP. The remaining points shape `docs/roadmap.md`.

## The retirement of the earlier design, 9 October

On 9 October the modules of the earlier design were removed from the core: the analyser's own command line, the boundary command and its container, the per-question checklist with its target queries, the register of open questions, the confirmed facts, the earlier two-part scripts and their rewriting from the cohort, the profile of a core OMOP database, and the conversion's data dictionary and concept names. Their tests went with them. What the synthetic world still needed was moved into the modules that use it, so that the testbed's stand-in database is built as before. The hostile-request and no-network tests of the boundary now hold against the import from the public workspace and the page's bridge, and the earlier scripts' test that the two parts give the same rows as one query now holds against the audit's compiled script. The decision to read the arterial line alone once it is running has no place in the audit over the roles, so its test was dropped with the target query's settings that held it; the planted scenario that shows the decision over the OMOP tables remains among the held-out fixtures.
