# Can this be answered

`core/schemalyser/feasibility.py` reads a question written over the parts of the record, as the neonatal audit is, with a saved hospital schema, and says whether the hospital schema can answer it and, if not, what has to happen first.

    python -m schemalyser.feasibility report hospital-schema.schemalyser.zip QUESTION.sql [--period FROM TO] --out report.md
    python -m schemalyser.feasibility programme hospital-schema.schemalyser.zip QUESTIONS/ [--period FROM TO] --out programme.md

An output whose name ends in `.json` holds the same report as data. Without `--period`, the question's period is the year that the hospital schema records, as the audit's package takes it.

## Two claims, kept apart

The report makes two claims and never merges them into one. The first is that every declared requirement of the question is mapped and checked against the database. The states below establish it, or say how far short of it each requirement falls. The second is that the coverage of the pathways by which the hospital's database records each part the question reads has been assessed for the question's period. A question can rest on requirements that are all checked and still miss a pathway that no part of the hospital schema knows of, so the first claim never stands in for the second. Only a person's assessment, recorded as evidence with its scope, establishes the second.

## What the report holds

Schemalyser reads the question's SQL and lists what it needs: the parts and columns it reads, the links on which it joins, the conditions it tests, the kinds of reading it names, its time arithmetic and the columns behind its result. A question may also name the capabilities of the catalogue that it computes, one on each line of its leading comment as `-- capability: NAME`; the report resolves each into the parts, columns, kinds, links and mapping views that the catalogue says it requires, adds them to the question's own, and names, for each capability, its version and the lowest state among its requirements. A capability that the catalogue does not hold is a gap of the role model, and an unsupported requirement of one that it holds is an ordinary outcome with its evidence request. It then records what the saved hospital schema says about each requirement, and nothing that the file does not say.

A mapping view is a requirement of its own. It is not currently mapped until the hospital schema translates its codes, proposed while the only concepts came from a reference conversion, the hospital's own conversion or an inference, and confirmed once a person gave any of them; no count yet checks a mapping view against the database. Its evidence request asks the clinician and the database analyst to translate the codes in the hospital schema.

The report opens with its verdict, which states both claims by name. There are four verdicts.

- **model.** "This question needs parts the role model does not yet describe." The question names a part, a column, a link, a kind or a capability that Schemalyser does not know.
- **not_yet.** "This question is expressible but cannot yet be answered reliably. Not every requirement of the question is yet mapped and checked against the database, and …", followed by what has been established about the coverage of the recording pathways.
- **requirements_checked.** "Every requirement of this question is mapped and checked against the database; the coverage of the recording pathways has not been assessed." Where some parts have been assessed, or an assessment found a pathway that the hospital schema has not mapped, the second half says so instead.
- **requirements_and_coverage.** "Every requirement of this question is mapped and checked against the database, and a person has assessed the coverage of the recording pathways of every part it reads over the question's period, and found every pathway mapped."

No verdict says that a question is answerable, and none says that it is complete, which only a clinical validation could support. A table then gives each requirement with its state and what the hospital schema records about it, followed by what has not yet been established, the coverage of the recording pathways, and the evidence requests.

## The states

- **Not currently mapped.** The hospital schema does not yet say where the hospital's database keeps this.
- **Proposed, not confirmed.** The page proposed where it is kept, and no person has confirmed it, or a person marked it Not sure, or its codes have not yet been translated.
- **Confirmed, not yet checked against the database.** A person confirmed it on a recorded date and the part runs on made-up rows, but the counts that read the part have not yet been run on the production database and judged to look right.
- **Checked against the database.** Those counts have been run and the clinician judged them to look right. For a link, a test query counted on the production database also counts.
- **Clinically validated.** A sample of anaesthetics has been reconciled against the clinical record. The page cannot do this, so the hospital schema never records it.

The JSON keeps the evidence behind each state apart, such as who confirmed a column and when, whether its codes are translated, the coverage that the counts measured, and a link's test query with its figures.

## The coverage of the recording pathways

For each part the question reads, the report says whether a person has assessed its recording pathways over the question's period, in one of five states.

- **Not assessed.** No assessment of the part overlaps the question's period.
- **Assessed for part of the period.** The assessments that hold do not cover every day of the period between them.
- **Assessed, and the pathways mapped have changed since.** Every assessment that overlaps the period was made before the hospital schema last changed the pathways it maps for the part, or before the contract changed the part, so each is stale.
- **Assessed, and a pathway found is not mapped.** The person found more pathways than the hospital schema has mapped.
- **Assessed, and every pathway found is mapped.** This is the only state in which the part counts towards the second claim.

The second claim holds when every part the question reads is in the last state. Where it does not, the report asks the clinician, with the database analyst, for an assessment of the parts that fall short over the question's period.

The assessment enters the hospital schema through the evidence import, as the evidence kind "pathway coverage assessed", from a request of the form `pathway coverage`. Its result has one row for each part assessed, with the columns `part` (the part's name, such as role_reading), `period_from` and `period_to` (dates written as 2024-01-31), `pathways_found` and `pathways_mapped` (whole numbers), and an optional `note`. The import refuses an assessment without the person's name as its actor, one that names a part the request did not ask about, a period whose first date is after its last, and one that says more pathways are mapped than were found. It records the assessment in the journal, scoped to the period it spans, and then on each part, never on a binding, in `dimensions.json` under `parts`: the date, the person, the journal entry, the period, the two figures, the note, and the hashes of the pathways that the hospital schema mapped for the part at the time.

## Evidence requests

For each requirement short of checked against the database, the report asks for the smallest piece of work that would move it, and says whether the clinician or the database analyst acts. Each request is something that screen 1 already offers: an answer at step 6, a question for the database team that states the page's proposal, a query of values for a flag, a list of what is charted with the codes chosen at step 7, a test query for a link, or a count at step 8. Where the saved schema can write the query, the request carries its text. Because the queries name the hospital's tables, the report stays on the hospital's own storage.

Each request in the report's JSON also carries what the evidence import needs to take its result back: its `format`, the `schema_id` of the version it was made from, a stable `request_id`, and, for a request that a result answers (a count, a test query, a list of what is charted, a query of values, a reconciliation against the clinical record or an assessment of the recording pathways), each of its queries with the columns and types of the result it expects. A request for an assessment of the recording pathways also carries the `parts` and the `period` it asks about. A request answered on the page, such as an answer at step 6, carries none. The database analyst's result enters the hospital schema with `python -m schemalyser.describe import-evidence SCHEMA.zip REQUEST.json RESULT.tsv`, which refuses a result of the wrong shape or a request made from another hospital schema, records the result in the journal under the request, and saves a new version. The readiness that the report reads is worked out from the evidence that the file records, and is never read as a stored state.

## The programme view

Over a folder of questions, the programme view counts how many questions need each requirement and lists the unresolved ones in the order of how many questions they hold back, so that the work that unblocks most comes first. Question coverage is the number of questions whose every requirement has been checked against the database. Pathway coverage, reported beside it, is the number of questions whose recording pathways have been assessed for their period with every pathway found mapped. Structural coverage is the number of parts and columns for which the hospital schema gives a place. The first figure is the one that matters.

## What the report cannot say

Where a requirement is not currently mapped, the hospital schema does not yet say where the database keeps it; that is not evidence that the hospital's database lacks it. Where a part has been checked against the database, its route runs and its counts looked right; that is not evidence that the route captures every record. A person's assessment of the recording pathways says whether every pathway that the person found is mapped, which is a separate claim and is shown separately. Only a reconciliation against the clinical record shows that the routes capture every record.

## The JSON

The JSON keeps the shape it had, with fields added. `claims` holds `requirements_checked` and `pathway_coverage_assessed`, one for each claim. `coverage` holds the question's `period`, each part with its `state`, its `assessments` (each marked `stale` with the reasons, where it is) and the `covering` entries that hold, whether the second claim is `established`, and the sentence that `says` so. The programme's JSON adds `pathway_coverage` beside `question_coverage`.
