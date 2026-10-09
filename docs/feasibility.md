# Can this be answered

`core/schemalyser/feasibility.py` reads a question written over the parts of the record, as the neonatal audit is, with a saved hospital schema, and says whether the hospital schema can answer it and, if not, what has to happen first.

    python -m schemalyser.feasibility report hospital-schema.schemalyser.zip QUESTION.sql --out report.md
    python -m schemalyser.feasibility programme hospital-schema.schemalyser.zip QUESTIONS/ --out programme.md

An output whose name ends in `.json` holds the same report as data.

## What the report holds

Schemalyser reads the question's SQL and lists what it needs: the parts and columns it reads, the links on which it joins, the conditions it tests, the kinds of reading it names, its time arithmetic and the columns behind its result. It then records what the saved hospital schema says about each requirement, and nothing that the file does not say.

The report opens with one of three verdicts: the question can be answered from the hospital schema as it stands; it is expressible but cannot yet be answered reliably; or it needs parts the role model does not yet describe, because it names a part, a column, a link or a kind that Schemalyser does not know. A table then gives each requirement with its state and what the hospital schema records about it, followed by what is missing and the evidence requests.

## The states

- **Not currently mapped.** The hospital schema does not yet say where the hospital's database keeps this.
- **Proposed, not confirmed.** The page proposed where it is kept, and no person has confirmed it, or a person marked it Not sure, or its codes have not yet been translated.
- **Confirmed, not yet checked against the database.** A person confirmed it on a recorded date and the part runs on made-up rows, but the counts that read the part have not yet been run on the production database and judged to look right.
- **Checked against the database.** Those counts have been run and the clinician judged them to look right. For a link, a test query counted on the production database also counts.
- **Clinically validated.** A sample of anaesthetics has been reconciled against the clinical record. The page cannot do this, so the hospital schema never records it.

The JSON keeps the evidence behind each state apart, such as who confirmed a column and when, whether its codes are translated, the coverage that the counts measured, and a link's test query with its figures.

## Evidence requests

For each requirement short of checked against the database, the report asks for the smallest piece of work that would move it, and says whether the clinician or the database analyst acts. Each request is something that screen 1 already offers: an answer at step 6, a question for the database team that states the page's proposal, a query of values for a flag, a list of what is charted with the codes chosen at step 7, a test query for a link, or a count at step 8. Where the saved schema can write the query, the request carries its text. Because the queries name the hospital's tables, the report stays on the hospital's own storage.

Each request in the report's JSON also carries what the evidence import needs to take its result back: its `format`, the `schema_id` of the version it was made from, a stable `request_id`, and, for a request that a result answers (a count, a test query, a list of what is charted, a query of values or a reconciliation against the clinical record), each of its queries with the columns and types of the result it expects. A request answered on the page, such as an answer at step 6, carries none. The database analyst's result enters the hospital schema with `python -m schemalyser.describe import-evidence SCHEMA.zip REQUEST.json RESULT.tsv`, which refuses a result of the wrong shape or a request made from another hospital schema, records the result in the journal under the request, and saves a new version. The readiness that the report reads is worked out from the evidence that the file records, and is never read as a stored state.

## The programme view

Over a folder of questions, the programme view counts how many questions need each requirement and lists the unresolved ones in the order of how many questions they hold back, so that the work that unblocks most comes first. Question coverage is the number of questions whose every requirement has been checked against the database. Structural coverage is the number of parts and columns for which the hospital schema gives a place. The first figure is the one that matters.

## What the report cannot say

Where a requirement is not currently mapped, the hospital schema does not yet say where the database keeps it; that is not evidence that the hospital's database lacks it. Where a part has been checked against the database, its route runs and its counts looked right; that is not evidence that the route captures every record. Only a reconciliation against the clinical record shows that.
