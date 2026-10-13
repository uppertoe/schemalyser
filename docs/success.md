# How success is measured

The pipeline begins from the naive state, a dictionary and nothing else, and ends when a question asked of the hospital's database through the roles gives the same answer as the same question written by hand against the database's own tables. Success is measured at four points along that line, each as counts that name no table, so that the numbers can be shared. Every number is taken on a stand-in built from a public specification, until the hospital's own analyst takes the same measures on production.

## 1. The proposal, before anyone answers

`python -m schemalyser.gold GOLD SCHEMA` scores the proposer against a gold standard graded by what decides each binding: documented, where the vendor's own words settle it; corroborated, where another conversion or a real export agrees; and hospital-only, where nothing public decides it. The score counts, per grade, the bindings proposed exactly, listed among the alternatives, bound to something else, or left empty, and whether each fact the test rests on was asked. The gold standard is held out and never used to tune the proposer. The measure is taken at each state of knowledge, from the specification alone to the database's own dictionary with its descriptions, the shared identifier and a reference, so that the value of each input is a number.

## 2. The cost of answering, by kind of decision

The journal of the saved schema records what the analyst and the clinician did, and the measure keeps the kinds apart rather than adding them up, because a count of rows is not the same work as finding how a medication reaches its anaesthetic.

- **Queries the analyst ran and pasted back**, by kind: the dictionary, the tables, a code search, a count or a probe. This is the burden on the hospital's database.
- **SQL a person wrote**: today none, because the page writes every statement; any that appears is a gap in the page.
- **Proposals confirmed as proposed**, which cost a reading and a click.
- **Corrections to a column the page listed**, which cost a choice, and **corrections with a typed route**, which cost knowledge of the database that the page did not have. The second is the number to drive down.
- **Columns declared not recorded**, and **columns marked not sure**, which are the decisions the tool could not make and a person could not either: the unresolved semantic decisions.
- **Facts answered, corrections in forms, code translations and counts judged**, each a decision of its own kind.
- Over time, **parts validated once and reused** by a later question or a later project, which is the measure that should rise while the others fall.

## 3. The description, after the answers

The readiness report of the saved schema says which parts of the record run on made-up rows, which have been checked against the database by counts and probes the clinician judged, and which have been clinically validated. The measure is the number of parts at each state, and the first target is the three parts every audit reads at the state runs, then checked against the database.

## 4. Equivalence, the end of the line

`python -m schemalyser.equivalence SCHEMA_A SCHEMA_B --hospital FOLDER --catalogue FILE --question QUERY.sql` runs every part of the record through two schemas on one stand-in and compares the rows and the keys, then compiles each question over the roles through both schemas and compares the answers row by row. The first schema is the one built from the naive state; the second is the one written by hand against the database's tables, or, for the OMOP layer, the conversion step written directly. The pipeline succeeds for a part when the keys and rows agree, and for a question when the answers are the same rows. The same comparison stands behind the OMOP layer: a step over the roles compiled through the naive schema must write the same rows as the direct step, which the testbed's release equivalence takes to SQL Server.

## What the numbers looked like on 14 October 2026

On the realistic stand-in, against the gold standard of the public specification, the proposer from the database's own dictionary with the vendor's descriptions bound 26 of 33 documented bindings exactly and the other 7 as listed alternatives, with none wrong and none empty, and 3 of 5 corroborated ones exactly. The clinician then gave seventy-three answers, three corrections in forms, seven translations of codes and three judgements, and the analyst ran eight queries. Nine parts reached the state runs: patients, anaesthetics, readings, stays, operations, events, drugs, devices and staff. Through the naive schema each of the nine was identical to the hand-written map, every row, with the local keys of drugs and units compared as filled or empty because each schema salts them, and the neonatal audit gave the same answer through both. The measurement step over the roles, compiled through the naive schema, wrote the same mean-pressure rows as the direct step. The other nine parts of the record were still drafts.
