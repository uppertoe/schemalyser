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

## The command lines

```
python -m schemalyser.compare reference FOLDER --out lineage.json
python -m schemalyser.compare report --ours ours.json --theirs theirs.json [--schema SCHEMA.zip] --out FOLDER
```

Run the first command on our conversion folder and on the reference, then run the second on the two files. The tests use an invented reference in `fixtures/compare`, written once as plain SQL and once as a dbt-style project.
