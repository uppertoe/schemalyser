# The three screens

This page describes each screen before it is built, so that the shape can be agreed in words first. It follows `docs/product.md`: the tool is the anaesthesia record export, with audits and the OMOP anaesthesia layer as two further consumers of the one mapping. Every screen draws what the core reports and decides nothing of its own, and anything a screen does the command line can do with the same files. A screen explains every file or pasted result it asks for: what it is, where it comes from, what it is for, and what the screen does with it.

## Where each screen runs

Describe the record runs as the offline page in the browser, as it does today, because it is the screen that meets the hospital's material and must let nothing leave the browser but the saved file. The export and the OMOP layer run in the workbench, which serves pages over a project folder on the clinician's computer or in a container and starts the core's commands. The workbench never connects to a hospital database and writes only inside its project folder. Whether the export should also run in the browser, so that a clinician without the workbench can build a specification, is a question for after the first export has been used.

## Screen 1. Describe the record

This screen exists. It builds the hospital schema from the vendor's dictionary and the analyst's pasted results, step by step, and saves it as one file. What changes is what the contract now records that the page does not yet ask for or show.

- **Who is answering.** The page asks once, at the start of a sitting, for the name of the person answering, and every confirmation, judgement and choice of codes records it. The hospital's name is asked for in the same place. Until given, the saved file says "not recorded".
- **The time zone.** The page proposes the computer's zone and says that it has, and a person confirms or changes it.
- **Concept translations.** A new step, beside the translation of reading kinds, takes the hospital's list of drug, unit, procedure, diagnosis and laboratory codes with their descriptions and standard concepts, and records each as mapped, unmapped or ambiguous. The local codes stay in the saved file and never reach the compiled views, which carry opaque keys.
- **Several pathways to one part.** Where the hospital records a part by more than one route, the correction form lets a second pathway be added with its source kind, and each pathway shows its own evidence and coverage.
- **What the evidence rests on.** Each binding shows its five dimensions as recorded, with the date and the person or the result that established each, and shows stale where the binding, a link, the codes or the contract changed since. The saved file's version, its parent, and the contract version it was made against are shown on the save step.
- **The page's words.** A blank count is shown as blank with the core's reason where the query suppressed it, never as "under 10" by inference. The readiness summary names the three states and never says complete.

## Screen 2. The anaesthesia record export

This screen is new. It takes a saved hospital schema, a list of episodes and the clinician's choice of sections, and produces the package the analyst runs. It is built one section at a time, with readings first, and the neonatal audit is its first analysis.

- **The hospital schema.** The screen opens with the saved file and shows which sections of the chart this hospital supports and to what state of evidence, from the feasibility report, so that the clinician sees before choosing what can and cannot yet be asked for.
- **The episodes.** The clinician gives the episodes as a file of anaesthetic keys, or as patient and date pairs with the window in hours and the rule for several anaesthetics in the window. The screen says that the file is patient-derived, stays in the project folder and is recorded by its hash, and shows the share of pairs that resolved to exactly one anaesthetic once the package has run.
- **The sections.** The chart's sections are shown as a tree in the clinician's words: the anaesthetic, readings by kind, drugs, techniques and blocks, fluids and blood products, devices, events, staff, operations, notes. For each chosen section the clinician sets the window relative to the anaesthetic's start and stop and the flags the section allows, and nothing finer. A section the hospital has not mapped is shown as not currently mapped, with the investigation that would move it, and cannot be chosen.
- **The derived sections.** The catalogue's capabilities are offered beside the raw sections, each with its parameters as fields, including the tables for thresholds by age band and factors by agent. A capability that needs a part this hospital lacks is shown as not currently supported, with the evidence request.
- **The output.** The clinician chooses rows or an aggregate, how the keys are pseudonymised, and which sections may leave, with notes excluded unless named. The screen says plainly that an export of rows is identifiable data and that each section's leaving is the hospital's approval under its own rules.
- **The specification.** The screen writes the specification file from the choices, shows it, and lets it be saved for reuse and loaded again. It holds no hospital material and can be shared.
- **The package.** One action compiles the specification: the feasibility report per section with its two claims, the SQL over the roles if the clinician asks to see it, the series the analyst can stop after, the safety report with its class, and the package folder with its manifest. The approval is recorded here by the analyst with their name, and the screen shows when a change has voided it.
- **The results.** When the analyst returns the outcome, the plan and the result files, the screen imports them as evidence through the schema's import and shows the results package: the output's shape, the coverage limitations for the period, and what was rounded or suppressed.

## Screen 3. The OMOP layer

This screen is new and has no clinical input of its own. It shows the state of the anaesthesia layer's conversion over the roles and runs the test and the release.

- **The conversion.** Each step with its route, roles or direct, its reference, reason and review where direct, and its class under the policy; the share of steps on each route; and whether the folder is a draft from a transplant.
- **The test run.** One action runs the testbed on the invented source, fast or full, and the screen shows the report by section: every planted scenario against its held-out rows, the reconciliation's four groups by name, the dashboard's findings with the permitted failures, the rewrites between the two engines, and the release equivalence.
- **The release.** The release script for the anaesthesia layer, compiled through the hospital schema, with its header stating the classes and routes, ready for the analyst, and the execution package's manifest for it.
- **Equivalence per question.** Once the roles can be populated from the hospital's core OMOP, a table of each question with whether its answer over the roles from the source and from OMOP has been shown equivalent, which is never assumed from the conversion passing its tests.

## What is decided here and what is not

The screens above decide what each shows and asks for. They do not decide wording, which follows the house voice and is written when each screen is built, nor layout. Two questions are open: whether the export should also run in the browser, and whether the OMOP layer screen is needed before the first export has run, since its command line already does everything it would show.
