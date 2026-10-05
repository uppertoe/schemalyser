"""Settings shared by every test."""
import pytest

from schemalyser import browser


@pytest.fixture(autouse=True)
def _own_boundary_folder(monkeypatch, tmp_path_factory):
    """Gives each test its own folder for the page's boundary, so that two test runs at once cannot share one.

    The page runs in the browser's own file system and keeps the fixed folder.
    """
    monkeypatch.setattr(browser, "BOUNDARY_ROOT", str(tmp_path_factory.mktemp("worker")))
