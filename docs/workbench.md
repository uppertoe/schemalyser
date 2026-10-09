# The workbench

The workbench is a local web server over a project folder. It keeps the saved hospital schemas, the questions, the audits and the runs of the test on made-up rows in one place, starts the core's commands, and renders the reports they write. The offline page, Describe the record, stays separate and unchanged, and the workbench serves a built copy of it at `/describe/` when one exists.

    python -m schemalyser.workbench --project FOLDER [--port 8765]

Run from `core/`, it needs the packages that `tools/workbench/requirements.txt` pins. The image in `tools/workbench/` holds them, and its README says how to build and run it.

## The project folder

The project folder is a plain folder, which the hospital may keep as a git repository.

- `workbench.json` holds the folder's name and the tool's version, and nothing else.
- `schemas/` holds saved hospital schemas, such as `hospital-schema-SCHEMA_ID.schemalyser.zip`, whose name carries the version. The workbench refuses to replace a saved schema with a different version under the same name.
- `questions/` holds one `.sql` file for each question over the parts of the record, with its title as the leading comment.
- `audits/NAME/` holds one audit: the request, the clinicians' decisions, the log of the build, any estimated plans, and `package/`, which is the folder that `schemalyser.audit build` wrote and that goes to the database analyst.
- `runs/NAME/` holds one run: its settings in `run.json`, its log, and `out/`, which is the folder that `schemalyser.testbed run` wrote.
- `.tmp/` holds temporary files, which may be deleted once the workbench is closed.
- `.cache/` holds the working copy of the Athena vocabulary that a run makes. The testbed keeps its working copy beside the folder it reads, so a run reads an Athena download that lies outside the project through `.cache/athena/`, a folder of links to the download's files, and the copy is made in `.cache/athena-working-copy/`. The download itself is only read. The cache may be deleted, and the next run with the Athena vocabulary builds it again.

Everything the workbench writes stays in the project folder, because a saved hospital schema and anything made from it name the hospital's tables.

## The screens

- **The overview** lists the saved hospital schemas, with the readiness of the three parts the audits read and the scoreboard's first line, then the questions, the audits and the runs, with forms to add each.
- **Can this be answered** reads a question against a saved hospital schema and shows the verdict, each requirement with its state, and the evidence requests with their SQL ready to copy. With no question chosen, it shows the programme of every question in the project.
- **An audit** builds the execution package from a question and a saved hospital schema, with an optional period, decisions and the name of the clinician who made them, then shows the class and what it needs, the README, the specification, the script, the safety report by rule, the answer on made-up rows, a form for the estimated plan, and the manifest's hashes. If `query.sql` has changed since the build, the page says that the review is voided.
- **The test on made-up rows** starts a run and lists the runs with their outcomes. A run's page shows the summary, the checks, the planted scenarios, the four groups of the reconciliation, the gates and counts, the Data Quality Dashboard and release equivalence. Each scenario and discrepancy links to the steps behind it, and a step's page shows its reconciliation and its SQL.

## How the workbench calls the core

The audit's build, the plan review and the test on made-up rows run as subprocesses of `python -m schemalyser.audit` and `python -m schemalyser.testbed`, with their output in the job's own folder. While a command runs, htmx asks for its progress every two seconds and shows the end of `log.txt`; once it has finished, the server tells the page to reload, and the page shows the report. The feasibility report, the programme, the readiness and the scoreboard write nothing, so the workbench calls the core's functions for them directly. Whether a package still stands is read with `audit.status`, which is also `python -m schemalyser.audit status FOLDER`.

## What the workbench never does

The workbench listens on 127.0.0.1, refuses a request whose Host is not this computer and a form from another site, and forbids its pages any other connection. It listens on 0.0.0.0 only inside its container, which it recognises by the file `/.dockerenv` that Docker writes at the root of every container; elsewhere it refuses `--host 0.0.0.0` and says why. The image in `tools/workbench/` needs nothing further, and compose publishes its port on 127.0.0.1 alone. It makes no request of its own and serves htmx and Alpine.js itself. It never connects to a hospital database: the database analyst runs the package under the hospital's own controls. Every verdict, class, state and outcome on its pages comes from a report that the core wrote, read by name: the scoreboard's overall line is the one that the core's wording for it opens, and the planted cases are summed up by the verdict that the correctness report gives them. A decision is recorded with the name that the form gives, and as "not recorded" when the form leaves it empty; the workbench never supplies a name of its own.

`core/tests/test_workbench.py` checks each screen on the invented world, the voided review, the author of a decision, the refusal of another site's form and of another host by the workbench's own list of trusted hosts, the refusal of 0.0.0.0 outside a container, and that nothing is written outside the project folder or beside an Athena download.
