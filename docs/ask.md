# Ask: the clinician-facing service

Ask is the clinician-facing service of the roadmap, the public service for the fifty anaesthetists who want answers from the record and the one clinician who can run queries. It takes a question in the asker's words, settles it in a conversation with a language model, and publishes it as a question package over the public roles at a secret address. The page Describe the record opens the package in the hospital and compiles it through the hospital schema. The service is described in `ask/README.md`, and this page says where it sits.

## What it may know

The role contract, embedded at build time from the repository, and what clinicians type. No hospital schema, no dictionary and no result ever reaches it, and it holds no table name of its own. What a clinician types is the one way information about the hospital can reach the model, so the page says beside the text box that no patient's details are to be typed, every message is screened for a record number, a date of birth or a table-like name before it is sent, and a message that matches is held back and explained. The rule for whoever uses it: write about groups of patients, in clinical words, and never paste from the hospital's systems.

## What it produces

A question package, `schemalyser-question/1`, holding the clinician's specification, the decisions made in the conversation, the SQL over the roles, the parts used with the drafts marked, any proposal for a part or a column the contract lacks, the contract version, the status and a hash. The model never invents a role in the SQL: where the contract lacks something, the package carries a proposal in the contract's own shape, the question waits, and the owner runs the proposal through `python -m schemalyser.contractcheck` and `python -m schemalyser.describe try`.

## What is not yet done

The page does not yet open a package by address; that is step 4 of the roadmap, and until it exists the package is downloaded as a file. The examination of step 5 does not yet run on a package, so its status stays at `drafted`. The policy admits the three contract parts only, so a question over a draft part compiles only once that part is promoted under step 2.
