"""The marker that separates the fast suite from the full one. pyproject.toml defines "slow"; a test in a file of its own
can carry it directly, and the tests named here, each measured at more than thirty seconds in one of two full runs on 10
October 2026, are given it at collection so that their files need not change. The fast suite is python -m pytest -m "not slow",
and docs/testing.md gives the times of both.

The tests of the workbench and its screens skip, rather than fail, when the packages the workbench needs are not
installed. Where the environment variable SCHEMALYSER_WORKBENCH_TESTS is "required", as the workflow sets it, a missing
package fails the run instead, so that a run which skipped them cannot pass as though it had run them."""
import importlib
import os

import pytest

WORKBENCH_PACKAGES = ("starlette", "jinja2", "markdown", "multipart", "httpx")

SLOW = {
    "test_testbed.py": {"test_a_fast_run_with_the_athena_vocabulary_records_its_release",
                        "test_no_run_writes_into_the_held_out_root"},
    "test_propose.py": {"test_the_proposer_agrees_with_the_earlier_map_of_the_public_specification"},
    "test_workbench.py": {"test_a_fast_run_of_the_test_on_made_up_rows_and_its_report",
                          "test_a_run_with_the_athena_vocabulary_keeps_its_working_copy_inside_the_project"},
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if getattr(item, "originalname", item.name) in SLOW.get(item.path.name, ()):
            item.add_marker(pytest.mark.slow)


def pytest_sessionstart(session):
    if os.environ.get("SCHEMALYSER_WORKBENCH_TESTS") != "required":
        return
    missing = []
    for name in WORKBENCH_PACKAGES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise pytest.UsageError("SCHEMALYSER_WORKBENCH_TESTS is required, and the workbench's tests would skip without "
                                + ", ".join(missing) + "; docs/testing.md gives the command that installs them.")
