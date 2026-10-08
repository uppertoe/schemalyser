# Roadmap

The order below follows one principle from the reviews of 8 October 2026: validation comes before breadth. Nothing new is built on the hospital schema until one audit, made through it, has been shown to be clinically right on the real database. Each item states what "done" means.

## 1. The first meeting, as the acceptance test of the hospital schema

The first meeting with the colleague who has database access is the test of screen 1. The two of them work through the page on production and save the hospital schema. Its scoreboard then shows, as counts that name nothing, how well the proposer did and so whether the dictionary-led approach holds at a real hospital.

Done means that a saved hospital schema exists on hospital storage in which all three contract views have reached the state *checked against the database*, and that its scoreboard has been read and any lesson for the proposer recorded in the public repository without naming the vendor's model.

## 2. Screen 2, an audit, built narrowly for the neonatal audit

Screen 2 is built for one audit only, the neonatal low mean pressure audit, rather than as a general audit builder. It reads the saved hospital schema, builds a shadow from it, plants the standard neonatal cases, runs the audit on the made-up rows, and compiles the two-part script for production. The colleague runs the script once.

The result is then clinically validated. A clinician reconciles a sample of anaesthetics against the clinical record, including some from each band of the answer and some that fell into the band in which nothing was recorded, and every disagreement is explained, either as a fault in the hospital schema, which is corrected, or as a limit of the record, which the result states.

Done means that the neonatal audit has run once on production through the hospital schema, that the reconciliation has been completed and its findings written down, and that the three contract views can then be recorded as *clinically validated*. Until that is so, no second audit is started.

## 3. The OMOP layer, as an OHDSI-compatible testbed

The conversion to OMOP is re-pointed from the source tables onto the roles, and the OMOP stage becomes a testbed in the way OHDSI practice expects, with Rabbit-in-a-Hat's testing framework and dbt-synthea as precedents. Four properties define it. One command runs the whole conversion on the invented world and writes a machine-readable result. The DataQualityDashboard is run on the output rather than reimplemented. The expected outputs are written independently of the conversion, as the planted scenarios already are, so that a mistake in a step cannot also be a mistake in its test. And the transformation that runs in the testbed is exactly the one that the release script carries into the hospital.

Done means that one command gives a pass or a failure with a result file that includes the DataQualityDashboard's findings, and that a test fails if the testbed and the release script would run different SQL. Screen 3 is then built on it.

## 4. The export for language-model work

A command writes an exported workspace that a coding agent can work on, rather than a language model being built into the tool. The export is a product in its own right: an allowlist of what may appear, deterministic renaming of anything else, checks that refuse to write a workspace holding a name outside the allowlist, and an uncertainty register that lists what the export does not know. The standing rule still holds, so the export carries no hospital data, metadata or dictionary text, and the checks are what demonstrate it.

Done means that the command exists, that its checks run in the test suite against invented inputs with planted names, and that a planted name never survives into an export.

## 5. A local service for the testbed

The offline page stays as the tool for hospital-facing work. Once the first audit has been validated, and not before, a local Python service may be added for the developer-facing testbed, with files as the interface between the two, so that neither depends on the other's internals.

Done means that the two exchange only files, and that the page still runs offline without the service.

## 6. dbt, considered then

Whether the conversion should be written as dbt models is considered once the testbed has been used. Done means that a written decision exists, with its reasons.

## 7. Retiring the earlier modules

The modules of the earlier design remain in the core. Screen 2 takes from them what it needs, such as the two-part scripts and the planted scenarios, and the rest are then removed from the core and its tests.

Done means that screen 2 is built, that no module it uses depends on a retired one, and that the retired modules and their tests are gone from the repository.

## 8. No new roles until the three have earned it

The fourteen draft views stay drafts. No view joins the contract, and no audit reads a draft view, until the three contract views have been clinically validated at a hospital and a second audit needs a further view. This item is a rule rather than a task, and it holds until both conditions are met.
