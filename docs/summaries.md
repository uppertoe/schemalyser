# The named summaries

Section 4 of `docs/contract.md` names three summaries of confidential material that the owner may show the developer's model, and nothing else of that material: the proposal scoreboard, the summary at the top of a comparison report, and an adapter run's counts of what it read, could not read and could not parse. `core/schemalyser/summaries.py` holds one exporter for each. This page gives the format of each summary and the counters that each may hold.

## What the owner does before showing a summary

A summary is safe to show because the owner has read the exact text that the exporter wrote and has decided to show it, and not because it holds only numbers. Each is a small disclosure that the owner makes knowingly. Open the `.md` file, or the `.json` file if that is what you mean to show, read every line, and show that file as it stands. If anything in it looks as though it names something of the hospital's, do not show it, and have the exporter corrected first.

No summary enters the public workspace. The export of the workspace takes allowlisted public definitions alone, and invariant 8 allows a question's fixed status there and nothing else.

## How each summary is written

Each exporter takes the output of the module that holds the material and keeps only the counter fields that its allowlist names in code. It then checks every value that remains. A value may be a number, a boolean, a word of a fixed vocabulary or a date written as YYYY-MM-DD, and nothing else. If a field that the allowlist does not name is present among the counters, or any value is of another kind, the exporter refuses and writes nothing. The refusal says where in the summary the problem lies, but it never quotes the field or the value, since either might name a table.

The summary is written alone to a file of its own, in two forms: a JSON file with a format name, a version and a fixed note, and a short Markdown rendering of the same counts in fixed sentences. The Markdown is built from the checked counters alone, so the two files always agree.

The module sits in the shared tier rather than in orchestration. The reference adapters that write two of the three summaries may import only layers 1 and 3 and the shared tier, and the scoreboard belongs to layer 2, so the exporters import nothing from the package and take each output as data.

## The proposal scoreboard

The format is `schemalyser-scoreboard-summary`, version 1, written as `scoreboard-summary.json` and `scoreboard-summary.md` by `write_scoreboard` from what `rolemap.scoreboard` returns. The scoreboard's sentences and the parts' titles are left out, and the Markdown is written afresh from the counts.

Each group of how the proposals fared holds six counts: `proposals`, `as_proposed`, `listed`, `unlisted`, `not_sure` and `unanswered`. The summary holds one such group under `overall`, one under `reference` for the proposals that rested on a reference conversion, one for each part under `parts`, and one for each category of column under `categories`. A part is named only by its role view, such as `role_patient`, from the views of `contract.json`, and a category only by one of `keys`, `links`, `timestamps`, `codes` and `descriptive`. Under `levels`, each of `high`, `medium` and `low` holds `answered` and `corrected`. Under `nothing`, `count` and `chosen` give the columns for which the page proposed nothing and those for which a person has since chosen one.

The `scoreboard` command of `python -m schemalyser.rolemap` (`rolemap/__main__.py`) writes this summary beside the saved schema or the `map.json` that it was given, or inside the folder where it was given one. The `scoreboard` command of `python -m schemalyser.describe` prints the same summary for a saved schema, and with `--write` writes it beside the saved schema as well. The workbench does not yet call `summaries.write_scoreboard`.

## The comparison summary

The format is `schemalyser-comparison-summary`, version 1, written as `comparison-summary.json` and `comparison-summary.md` in the report's folder by the `report` command of `compare.py`. It holds, under `counts`, the same seven counts as `summary` in `report.json`: `targets_compared`, `agreeing`, `differing`, `only_ours`, `only_theirs`, `routes_we_lack` and `uncertainty_items`. The detail stays in `report.md` and `report.json`.

```json
{
 "format": "schemalyser-comparison-summary",
 "version": 1,
 "note": "This summary holds counts alone and names no table, column, code or file. Read the exact text before you show it to the developer's model, and keep it out of the public workspace.",
 "counts": {"targets_compared": 15, "agreeing": 3, "differing": 2, "only_ours": 9, "only_theirs": 1,
            "routes_we_lack": 6, "uncertainty_items": 6}
}
```

## The run summary of the reference command

The format is `schemalyser-reference-run-summary`, version 1, written beside the lineage as `NAME-run-summary.json` and `NAME-run-summary.md` for a lineage written to `NAME.json`, so that the runs over our conversion and over the reference keep a summary each.

`kind` is one of `conversion`, `dbt` and `plain`. Under `counts` the summary holds `files_read`, `files_parsed`, `files_not_read` (files whose Jinja the reader does not render), `files_not_parsed` (files whose SQL does not parse), `files_not_followed` (files that parsed but held a query that could not be traced to its columns), `files_not_recognised`, `statements_ignored`, `omop_tables`, `source_tables` and `source_columns`. Under `errors`, each of `ParseError`, `TokenError`, `NoStatement` and `other` that occurred holds `files`, the number of files that met that error. No file name and no error message is kept.

## The run summary of the transplant command

The format is `schemalyser-transplant-run-summary`, version 1, written as `transplant-run-summary.json` and `transplant-run-summary.md` in the conversion folder. It holds the `date` of the run and, under `counts`, the same eleven counts as `transplant-report.json`: `written`, `complete`, `incomplete`, `not_written`, `skipped`, `placeholders`, `fields_mapped`, `fields_left_empty`, `fields_awaiting_a_decision`, `filters_held_back` and `not_in_dictionary`.

## How the exporters are tested

`core/tests/test_summaries.py` plants names in every input, in the way that `test_workspace.py` plants a reference: the source tables and columns of the invented reference are renamed, a code and a description are written into each file, a file that does not parse and a model that is not read are added under planted file names, the whole is kept under a planted path, and the dictionary is planted to match. Each test runs the real command over the planted input, checks that the private output does name the planted tables, and asserts that no planted name reaches the summary's files. Further tests check that an unexpected field, a string where a count belongs, a word outside its vocabulary or a malformed date is refused with nothing written, and that the comparison summary's counts equal those under `summary` in `report.json`.

A new counter reaches a summary only when it is added to that summary's allowlist in `summaries.py` and to this page.
