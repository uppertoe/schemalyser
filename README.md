# Schemalyser

Schemalyser helps a clinician write an audit of anaesthetic records and test it on invented data before a colleague with access to the hospital's database runs it once for real.

The hospital's record system keeps its data in thousands of tables that few people understand, and patient data may not be shown to an AI model. Schemalyser therefore describes the anaesthetic record once, without reference to any vendor, as a set of parts: patients, anaesthetics, readings, drugs, devices, events, notes and outcomes. For each hospital, a clinician and a colleague who can query the database sit together once and confirm the hospital schema: where that hospital's database keeps each part, proposed from the vendor's data dictionary in the browser and confirmed column by column. Audits are then written against the parts, tested on made-up rows, and run once on the real database as scripts that are safe by construction.

Everything in this repository is the tool itself and invented examples. It holds no hospital's data, no table or column name from any real system, no vendor's data dictionary, and no credentials.

## The page

The page is published at https://uppertoe.github.io/schemalyser/ . It is a static page. Once it has loaded, it runs entirely in the browser, and it will not read a dictionary or a saved schema until the browser tab is offline. The data dictionary that a hospital loads stays in the browser and goes into nothing but the saved hospital schema, which is one file that stays on hospital storage.

The page's first screen, describing the record, is built. The screens for writing an audit and for the OMOP layer are to follow.

## The parts

- `core/` is the Python core: the role model of the anaesthetic record, the dictionary loader and proposer, the hospital schema and its test on made-up rows, the synthetic database and the OMOP conversion.
- `site/` is the browser page, which runs the same core in the browser.
- `fixtures/` is an invented hospital database with invented queries, used by the tests.
- `tools/` holds the harness that checks the tool's SQL on a real SQL Server, and a container for running the tool unattended.
- `docs/architecture.md` describes how the parts fit together.

## Running the tests

```sh
cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 --with pytest python -m pytest -q
cd site && npm ci && npm run build && npx playwright test
```
