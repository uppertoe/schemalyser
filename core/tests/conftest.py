"""The marker that separates the fast suite from the full one. pyproject.toml defines "slow"; a test in a file of its own
can carry it directly, and the tests named here, each measured at more than thirty seconds in one of two full runs on 10
October 2026, are given it at collection so that their files need not change. The fast suite is python -m pytest -m "not slow",
and docs/testing.md gives the times of both."""
import pytest

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
