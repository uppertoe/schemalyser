# The command line of the hospital schema

Everything that a person does on the page Describe the record can be done with `python -m schemalyser.describe`, over the same files. Each command reads the saved hospital schema, performs one operation, and saves what that operation changed as a new version beside the file it read, under the name that carries the version's `schema_id`. A command reads and writes nothing else, so the saved file is the only state, and the next command reads the version that the last one wrote.

Where a command is given a folder in place of a file, it reads the latest version in that folder, which is the one from which no other version there was made. If the folder holds two versions of which neither was made from the other, Schemalyser asks for the file by name. `--out FOLDER` writes the new version into another folder.

Every command that changes the hospital schema records it in the journal, as the page does. `--actor NAME` names the person who answered, chose or judged, and without it the journal records "not recorded". Only `test`, the check and keep of a change, `save` and `import-evidence` run the test on made-up rows. Every other command saves its version with the test owed, and the version says so, as the page's sitting does until a person saves it. A change to the settings alone is written into the file of the same version, because a version is named by the hash of every file but `settings.json`.

Run the commands from the `core` folder:

    cd core
    uv run python -m schemalyser.describe start ../my-hospital DICTIONARY.csv --tables TABLES.csv

## The commands

- `start FOLDER DICTIONARY [--tables FILE] [--reference FILE] [--heading FIELD=HEADING] [--invented] [--hospital NAME]` reads a dictionary, with the optional tables file and a reference conversion's lineage, and saves the first version of a new hospital schema into an empty folder. `--hospital` names the hospital from the first entry of the journal, as the page does when the hospital's name is given before the dictionary.
- `add-descriptions SCHEMA VENDOR [--tables FILE]` adds the vendor's descriptions to a dictionary that was made from the result of the data dictionary query.
- `dictionary-query` prints the data dictionary query, which is the same for every hospital and needs no hospital schema.
- `propose SCHEMA` proposes where the hospital's database keeps each part of the record.
- `settings SCHEMA [--database NAME] [--year YEAR] [--hospital NAME] [--time-zone ZONE] [--daylight-saving yes|no] [--time-zone-from SOURCE] [--actor NAME]` records the database, the year of the lists, the hospital's name, which every later entry of the journal carries in its scope, or the time zone of the database's clocks. A zone given or confirmed by a person (`--time-zone-from "a person"`, the default) records `--actor` as the person who gave it; a zone proposed from this computer records no one.
- `query SCHEMA tables|codes|counts|values|probe` writes a query for the database analyst, records it in the journal and prints it, with `--key` and `--year` for the list of codes, `--year` for the counts, `--about`, `--table` and `--column` for a list of values, and `--about` for a test query.
- `paste SCHEMA tables|codes|count|values|probe RESULT` reads the result of a query from a file, as the results grid copies it with its headers, with `--key` and `--year` for the codes, `--name` for a count or values, and `--about` for a test query.
- `invented-run SCHEMA QUERY tables|codes|count|values|probe` runs a query on the invented hospital and reads its result as a paste would be read, when the dictionary is the invented one.
- `answer SCHEMA ABOUT yes|no|not-sure [--replacement TABLE.COLUMN] [--note TEXT]` records a person's answer to a proposal, such as `role_patient.birth_date` or `"role_patient rows"`.
- `translate-codes SCHEMA KEY CODE=KIND ...` records the local codes that a person chose for each kind of a column, such as `role_reading.kind 52=map_arterial 51=map_cuff`.
- `translate-concepts SCHEMA MAPPING ROWS` records the translation of a mapping view's local codes into standard concepts, from a CSV or tab-separated file with the headings code, concept_id and status, and optionally description and provenance, as the page's list of codes at step 7 does.
- `add-pathway SCHEMA PART TABLE KIND [--name NAME]` adds a further pathway to a part that records events, such as `role_drug OBS_READING charted_value`: the rows of another table, its columns proposed again from it, and the source kind that every row of it carries. Without `--name`, the pathway is named by its source kind.
- `source-kind SCHEMA PART KIND` records the source kind of a pathway, named as `role_drug` for the part's first table or `role_drug@NAME` for a further one.
- `judge SCHEMA COUNT yes|no [--note TEXT]` records the clinician's judgement of a count, such as `coverage_by_year`.
- `correction-preview SCHEMA CORRECTION.json` says what a change means and prints the SQL of the part that it changes.
- `correction-check SCHEMA CORRECTION.json` tries a change on made-up rows and reports what it breaks or mends, and saves nothing.
- `correction-keep SCHEMA CORRECTION.json [--although --reason TEXT]` keeps a change once it has been tried, and keeps a change that fails its trial only with `--although` and a reason.
- `correction-discard SCHEMA CORRECTION.json` discards a change after its trial, which leaves the hospital schema as it was, since a trial records nothing.
- `test SCHEMA` runs the test on made-up rows of the hospital schema as it stands, prints its report and records it in the journal.
- `save SCHEMA` runs the test on made-up rows that the hospital schema owes, if any, and saves the version that the page's Save would save.
- `show SCHEMA [--json]` opens a saved hospital schema and prints its README, which gives its readiness, then its stale evidence and its journal, or with `--json` the page's whole view, the readiness, the stale evidence, the journal and the files.
- `check SCHEMA` proposes the hospital schema again from its dictionary and its answers, and says whether it is the same.
- `compare SCHEMA NAME RESULT` compares a new result of a query with the result that the hospital schema holds.
- `lookup SCHEMA tables|columns|joins [TABLE]` lists the dictionary's tables, a table's columns, or the joins from a table, as the page's forms list them.
- `scoreboard SCHEMA [--write]` prints how the proposals fared, as counts only, and with `--write` writes the same summary beside the saved schema for the owner to read before showing it to anyone.
- `import-evidence SCHEMA REQUEST.json RESULT [RESULT ...] [--id REQUEST_ID] [--provenance SOURCE]` imports the result of an evidence request, from the feasibility report or from an execution package, and saves the new version.
- `walk CALLS.json [--out FOLDER]` is a convenience: it gives each call that the page made of its bridge to the command that carries it, one command at a time over the saved file, and saves the hospital schema that they make.

A change is given as a JSON file holding one correction, in the form that the page's correction forms send, such as `{"form": "column", "about": "role_anaesthetic.patient_key", "table": "THEATRE_CASE", "column": "PERSON_KEY"}`; `core/schemalyser/corrections.py` describes each form.

## How the page and the commands are kept the same

`BRIDGE` in `core/schemalyser/describe/__main__.py` names the command that carries each function of the page's bridge, `core/schemalyser/browser.py`, and the test of invariant 9 in `core/tests/test_invariants.py` fails if a function of the bridge has no command. `core/tests/test_describe_command.py` drives the bridge as the page does, once with every result pasted and once with the invented hospital answering every query, saves the hospital schema as the page saves it, and compares that file, file by file, with the one that the commands save from the same calls. The first page test in `site/e2e/describe.spec.ts` does the same with the calls that the page itself made. Each comparison sets aside only what cannot agree: the identifiers of entries, test runs and versions, the times, the seconds a test took, the versions from which entries were made, since every command saves a version and the page saves only at the end, and the salt of the concept translations with the keys made from it, which is drawn at random for each hospital schema. The walk holds a call that records the hospital's name before any dictionary is loaded until `start`, and gives the name to `start`, since no saved schema exists before it.

## The map folder, for development only

`python -m schemalyser.rolemap propose` and `python -m schemalyser.rolemap confirm` write and rewrite a map folder directly. They are for development only, and they produce no schema of record: no journal records what they write, and no evidence rests on it. The hospital schema of record is made with `start` and `propose` above, and answered with `answer`.
