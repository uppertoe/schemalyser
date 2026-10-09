# Testing Schemalyser

Every test runs on invented material: the invented world in `fixtures/`, the invented dictionary, and the planted cases and planted names that the tests make for themselves. No test reads a hospital's data, the vendor's material or anything under `reference/`. This page says how to run each suite, what the slow marker leaves out, and how long each suite took when it was last measured.

## The core tests

The core tests are run from `core/`. `uv run` installs the dependency group `dev` of `core/pyproject.toml`, which holds pytest, DuckDB 1.5.1 and Hypothesis, and the workbench's tests need five further packages, which are given on the command line. Without them, the workbench's tests are skipped rather than failed.

The fast suite leaves out every test marked slow, and is the one to run day to day:

    cd core
    uv run --with starlette --with jinja2 --with markdown --with python-multipart --with httpx \
        python -m pytest -q -m "not slow"

The full suite is the same command without `-m "not slow"`, and is the one to run before a change is committed:

    uv run --with starlette --with jinja2 --with markdown --with python-multipart --with httpx python -m pytest -q

A single file, or the tests whose names match some words, can be run by adding the file's path or `-k WORDS`, as pytest allows.

### The slow marker

`core/pyproject.toml` defines the marker `slow`. A test carries it when it takes more than thirty seconds, or when it runs the testbed's full profile or SQL Server. A test in a file of its own can carry the marker directly, as the test of invariant 7 that runs the workbench does. `core/tests/conftest.py` gives the marker, at collection, to the tests of other files that were measured at more than thirty seconds in either of two full runs, so that those files need not change: the testbed's fast run with the Athena vocabulary, its check that no run writes into the held-out fixtures, which runs the testbed again, the proposer's agreement with the earlier map of the public specification, and the workbench's two runs of the testbed. Times near thirty seconds vary with what else the computer is doing, so the list is a judgement made from those measurements, and a test that grows slow can be added to it. When this page was written, no test in `core/tests/` ran the full profile or SQL Server itself, because the SQL Server harness below is run on its own; a test that comes to do either is to carry the marker.

Some of the time of the fast suite is spent setting up fixtures that several tests share, such as the testbed's own run over the invented world, which takes close to a minute. That cost is not marked, because the tests that read the run are the ones that check the testbed.

### The invariants

`core/tests/test_invariants.py` holds one test for each invariant of `docs/contract.md`, section 5, that no other test held, and each test is named after its invariant. The test of invariant 2 sweeps every file under `core/`, `site/src`, `site/public`, `fixtures/`, `docs/` and `tools/` that git tracks or would track, for the patterns and names in `core/tests/invariant2-denylist.json`. That file holds only invented stand-ins, and the test shows on planted text that each entry is caught before it sweeps the repository. On the hospital's side, a private copy of the file with real names can be kept outside version control and named by the environment variable `SCHEMALYSER_PRIVATE_DENYLIST`, and the same test then reads it as well.

## The page specs

The page bundles a copy of the core, so the page is built again after any change to the core, and the specs are run from `site/`:

    cd site
    npm run build
    npx playwright test --project=chromium

Playwright starts the preview server itself, or uses one already listening on port 4173. The workflow in `.github/workflows/tests.yml` runs the same specs in Chromium and Firefox. When a spec asserts that nothing leaves the browser while the tab is offline, it watches the requests of the whole browser context with a route handler and the context's request event (`site/e2e/network.ts`), so that the requests of the page's worker are seen as well as the page's own. Each such spec first checks that the watch saw the worker fetch the core while the tab was online, so that a silence afterwards means something.

## The SQL Server harness

`tools/sqlserver/harness.py` runs the generated T-SQL on a real SQL Server over the invented world, and `tools/sqlserver/README.md` says how to start the container and run it. The run to use day to day is the small one:

    cd core
    uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/sqlserver/harness.py --sample-vocabulary \
        --rows 50 --skip-safeguards

The harness is worth running only when a change touches the T-SQL that Schemalyser writes, because the core tests already cover everything else on DuckDB. A run at full size, or with the safeguards, is worth its time only when the question is how long a step takes on many rows, or whether the release script's guards still hold.

## Measured times

The times below were measured on 10 October 2026 on the developer's Mac. Another agent's suites ran beside the fast suite for part of its run, so its time is an upper bound; the full suite and the page specs ran on a quiet machine. Much of the fast suite's time goes to the testbed's run over the invented world and to the saved schemas that several files build for themselves, which is why the slow marker saves less than its share of tests suggests.

| Suite | Command | Tests | Time |
| --- | --- | --- | --- |
| The fast core suite | `python -m pytest -q -m "not slow"` | 775 passed, 7 deselected | 12 minutes 22 seconds |
| The full core suite | `python -m pytest -q` | 782 passed | 16 minutes 51 seconds |
| The page specs in Chromium | `npx playwright test --project=chromium` | 8 passed | 3 minutes 43 seconds |
| The SQL Server harness, small | as above | not run in this round | not measured, since no change in this round touched the T-SQL |
