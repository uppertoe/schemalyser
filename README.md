# Schemalyser

Schemalyser helps a clinician write an audit of anaesthetic records and test it on invented data before a colleague with access to the hospital's database runs it once for real.

The hospital's record system keeps its data in thousands of tables that few people understand, and patient data may not be shown to an AI model. Schemalyser therefore learns the layout of the database and not its contents. It reads the queries that a data team has already written, in a browser page with the network switched off, and shows a checklist of what it still needs to know. A person who knows the database answers the questions on the checklist, or runs a short query that returns only names or rounded counts.

Everything in this repository is the tool itself and invented examples. It holds no hospital's data, no table or column name from any real system, and no credentials.

## The page

The page is published at https://uppertoe.github.io/schemalyser/ . It is a static page. Once it has loaded, it runs entirely in the browser, and it will not read a file until the browser tab is offline.

## The parts

- `core/` is the Python core: the analyser, the checklist, the synthetic database and the conversion.
- `site/` is the browser page, which runs the same core in the browser.
- `fixtures/` is an invented hospital database with invented queries, used by the tests.
- `tools/` holds the harness that checks the tool's SQL on a real SQL Server, and a container for running the tool unattended.
- `docs/architecture.md` describes how the parts fit together.

## Running the tests

```sh
cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 --with pytest python -m pytest -q
cd site && npm ci && npm run build && npx playwright test
```
