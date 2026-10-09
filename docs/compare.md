# Comparing a conversion with a reference

`compare.py` sets our conversion to OMOP beside a reference conversion written elsewhere, and reports where the two read the record alike and where they part company. It reads SQL alone and never connects to a database.

## What the tool extracts

The `reference` command reads a folder of SQL and writes its lineage to one file. The folder may be a conversion folder with a `conversion.json`, a plain folder of SQL files, or a dbt-style project with its models under `models/`. In a plain folder, a file writes an OMOP table when it inserts into one, when its name is the table's name, or when the columns of its final SELECT are mostly that table's fields. A table made by SELECT INTO, CREATE TABLE AS or CREATE VIEW is an intermediate step. In a dbt-style project, `ref` and `source` are resolved by name, `config` blocks are dropped with any alias kept, and YAML is read only for names and aliases. A model that holds any other Jinja, such as a loop or a macro, is recorded as not read.

For each OMOP table, the lineage holds the source tables it reads, the joins between them with their columns, the filters with every literal replaced by its type (as `<int>` or `<str>`), the source columns behind each field, and its aggregations. A column read from an intermediate step or from another OMOP table is followed back to the source column behind it, so that a join made through `omop.visit_occurrence` and a join made straight to the visit table count as the same join. The lineage also lists every source table and column seen. A file that does not parse is recorded with the class of its error and never with its text.

## What the report says

The `report` command compares two lineage files, ours and the reference's. For each OMOP table written by either, it says whether the two agree or differ on the source tables, the joins, the columns that the filters test and the fields filled, or whether only one of them writes the table. It lists the routes that the reference uses and ours does not, and for the anaesthesia tables it writes an uncertainty item for each disagreement. When a saved hospital schema is given, the report adds the source tables that its proposed bindings name for each table, and the uncertainty items say which pathway those proposals follow.

Agreement is supporting evidence and not proof. Workflows and configuration differ between hospitals, and two conversions can share a mistake. A disagreement is a prompt to look, not a finding that either conversion is wrong, and the report says nothing about how many rows either would write.

## What may be shared

The lineage of a reference names its tables and columns, so it is private wherever the reference is. The report's summary, at the top of `report.md` and under `summary` in `report.json`, holds counts alone and names nothing, and only that part may be shared or shown to a language model. The detail below it names tables and columns, and it stays with the reference on the hospital's own storage.

## Transplanting the reference's routes

The `transplant` command writes a conversion folder from a lineage, with one step for each OMOP table that the reference writes, or for those named with `--targets`. Each step follows the conventions of `convert.py`: one SELECT in T-SQL that names its columns as the OMOP fields and reads the source tables, with the OMOP tables already written read as `omop.<table>`. The step starts from the table behind the target's own identifier and joins the other tables along the lineage's joins, including those of the OMOP tables that the target reads through. A join is written as an inner join where a required field or a filter needs its table, and as a left join otherwise, because the lineage does not say which the reference used. A join that does not cover the key of the table it reaches would repeat rows, so it is not made, and the step says so.

A field whose expression the lineage gives in full is written from it. An identifier of another OMOP table, such as `person_id`, is looked up in that table by its source value, where the lineage says that the source value comes from the same column. A field that the lineage gives as a bare literal is held by a named placeholder, `{{decision:TARGET.FIELD}}`, and is written meanwhile as the concept 0 for a concept field and as NULL otherwise. Any other field that the lineage cannot reproduce, because it reads a mapping table, an OMOP table that the lineage could not follow, or a column that the lineage could not pin down, is written as NULL, or as the concept 0 for a concept field, with a comment that gives the lineage's expression. A filter without a literal is applied. A filter whose literals were redacted is held back as a comment in the step, with each literal replaced by `{{decision:TARGET.filter_N}}`, so that the step runs as it stands and the owner writes the filter in once its values are known.

Every table and column is checked against the dictionary given with `--dictionary`, which is meant to be the vendor's public specification. A table or column that the dictionary lacks is reported and the step is marked incomplete. Nothing is invented in its place, and a step whose own rows would come from a missing table is not written at all.

Beside the steps the folder receives `conversion.json`, `catalogue.csv` with the tables and columns that the steps read in the layout of a world's catalogue, so that the testbed's sandbox can build rows for them, `decisions.json` with every placeholder, its column, its type and a sentence that asks for its value, and `transplant-report.json` with `transplant-report.md`, which say for each table whether its step was written, written incomplete or skipped, and count the fields mapped, the fields left empty and the placeholders. With `--existing`, the existing conversion is copied into the folder and its steps are kept. Where it already writes a table, the transplanted step is written beside it as `TABLE_from_reference.sql` and offered as that step's alternative, which a run takes with `--alternative`, so that the owner chooses between them.

Every step that the transplant writes is marked in `conversion.json` as a step on the direct route, from the source tables, with the reference it came from and the reason, and with no review, because no person has reviewed it. A transplanted alternative offered beside an existing step is marked in the same way. The folder also receives `draft.json`, which marks the whole conversion as a draft. The runner and the testbed report the folder as a draft, and the release script refuses it while any step lacks its review and while `draft.json` remains, so that a transplant never reaches a release unreviewed. Once the owner has reviewed a step, they record the review in its entry as `"review": {"by": ..., "on": "YYYY-MM-DD"}`, and once every step is reviewed they remove `draft.json`.

The folder names the reference's tables and columns, so it stays wherever the reference is kept.

## A reference's lineage as evidence for the proposals

The proposer can read a lineage beside the dictionary, with `--reference lineage.json` on `rolemap propose`, or as the optional third file under the upload way at step 2 of the page. Each part of the role model names in `contract.json` the OMOP tables onto which it projects, as the table in `roles.md` gives them. A column that the lineage reads for one of those tables becomes a candidate for a column of the part whose meaning the OMOP field shares: a start or a stop for a time, a source value for a code, a value for a number, and a column that the reference's filter tests for a flag. Its evidence is the sentence "A conversion at another hospital reads this table for VISIT_DETAIL, and fills visit_detail_start_datetime from ANAES_RECORD.ANAES_START_TS." A link between parts gains as candidates the columns that the lineage joins to the other part's key.

The reference's candidates rank after every candidate that the dictionary's own words support, so they lead only where the dictionary names nothing that fits, and then with low confidence. A listed candidate that the reference also reads keeps its place and gains the reference's sentence. A proposal that rests on the reference records "a reference conversion" under `proposed_from` in `map.json`, which the saved hospital schema gives as its provenance until a person answers. The answer's record and `confirmations.csv` keep it afterwards, and the scoreboard counts these proposals apart. The page keeps the lineage in the browser and in the saved hospital schema, as `dictionary/reference-lineage.json`, and the journal records only its file name, its size and its hash.

## The command lines

```
python -m schemalyser.compare reference FOLDER --out lineage.json
python -m schemalyser.compare report --ours ours.json --theirs theirs.json [--schema SCHEMA.zip] --out FOLDER
python -m schemalyser.compare transplant --lineage lineage.json --dictionary DICT.csv --out FOLDER
                                         [--tables TABLES.csv] [--targets T1,T2] [--existing CONVERSION]
python -m schemalyser.rolemap propose DICT.csv --catalogue CATALOGUE.csv --out FOLDER --reference lineage.json
```

Run the first command on our conversion folder and on the reference, then run the second on the two files. The third writes a conversion folder from the reference's lineage, and the fourth reads the lineage as evidence for the proposals. The tests use an invented reference in `fixtures/compare`, written once as plain SQL and once as a dbt-style project, and the tests of the transplant run its steps in the testbed on a sandbox built from the catalogue that the transplant writes.
