"""The page's bridge, browser.py, as the page's worker calls it.

Carried from the earlier boundary's tests (B8). What they protected now lives in the page's offline rule: a hostile
saved file puts nothing of its own into what the worker returns or into the file that the page saves next, and the
bridge's import and a sitting through it open no network module.
"""
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from schemalyser import browser, workspace

ROOT = Path(__file__).resolve().parents[2]
DICTIONARY = ROOT / "fixtures" / "dictionary" / "invented-dictionary.csv"
TABLES = ROOT / "fixtures" / "dictionary" / "invented-tables.csv"
DATE = "2026-10-07"
HOSTILE_WORDS = ("zanzibarine", "xylophonist", "quartermaine")


def _hostile_schema():
    """The invented saved schema, with entries planted beside its own that no saved schema holds."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(workspace.invented_schema(ROOT, DATE))) as given, zipfile.ZipFile(out, "w") as archive:
        for info in given.infolist():
            archive.writestr(info, given.read(info))
        archive.writestr("../Zanzibarine.sql", "SELECT 'Xylophonist' AS Quartermaine")
        archive.writestr("notes/Xylophonist.md", "Quartermaine asked for this.")
        archive.writestr("map/Zanzibarine.json", json.dumps({"Xylophonist": "Quartermaine"}))
        archive.writestr("queries/Zanzibarine.sql", "SELECT 1 AS Xylophonist")
    return out.getvalue()


def test_a_hostile_saved_file_puts_nothing_of_its_own_into_what_the_worker_returns_or_saves():
    browser.describe_begin("test")
    try:
        reply = browser.describe_schema_open(_hostile_schema())
        assert json.loads(reply)["ok"]
        files = browser.describe_schema_files()
        saved = browser.describe_schema_zip()
        with zipfile.ZipFile(io.BytesIO(saved)) as archive:
            names = archive.namelist()
            held = b"\n".join(archive.read(name) for name in names).decode("utf-8", "replace")
        for text in (reply, files, "\n".join(names), held):
            assert not [word for word in HOSTILE_WORDS if word in text.lower()]
    finally:
        browser.describe_begin("test")


def test_the_bridge_and_a_sitting_through_it_open_no_network_module():
    # What the interpreter and the parser import is not the tool's doing: on Linux, sqlglot reads its own version
    # through a standard module that pulls in the email package, which imports socket without opening a connection.
    # So only what the bridge adds beyond its dependency is counted.
    code = (
        "import json, sys\n"
        "network = ('socket', 'ssl', 'http.client', 'urllib.request', 'urllib3', 'requests', 'httpx')\n"
        "import sqlglot\n"
        "at_start = {m for m in network if m in sys.modules}\n"
        "import schemalyser.browser as b\n"
        "b.describe_begin('test')\n"
        f"reply = b.describe_dictionary(open({str(DICTIONARY)!r}, 'rb').read(), open({str(TABLES)!r}, 'rb').read(), '{{}}', "
        "'invented-dictionary.csv', 'invented-tables.csv', '2', True)\n"
        "assert json.loads(reply)['ok'], reply\n"
        "assert json.loads(b.describe_propose(lambda done, total: None))['ok']\n"
        "assert json.loads(b.describe_tables_query('5'))['ok']\n"
        "assert b.describe_schema_zip()[:2] == b'PK'\n"
        "print(sorted(m for m in network if m in sys.modules and m not in at_start))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT / "core",
                          env={**os.environ, "PYTHONPATH": str(ROOT / "core")}, timeout=300)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "[]"


def test_the_bridge_carries_the_person_s_name_and_the_hospital_s_into_every_entry_it_makes():
    browser.describe_begin("test")
    try:
        assert json.loads(browser.describe_settings(json.dumps({"hospital": "The Invented Hospital"})))["ok"]
        reply = json.loads(browser.describe_dictionary_upload(DICTIONARY.read_bytes(), TABLES.read_bytes(), "{}",
                                                              "invented-dictionary.csv", "invented-tables.csv", "2"))
        assert reply["ok"] and reply["model"]["settings"]["hospital_recorded"] == "The Invented Hospital"
        assert json.loads(browser.describe_propose(lambda done, total: None))["ok"]
        browser.describe_confirm(json.dumps({"about": "role_patient.birth_date", "answer": "yes", "actor": "Dr A"}))
        found = json.loads(browser.describe_concepts(json.dumps({"mapping": "map_unit_concept", "actor": "Dr A",
                                                                 "text": "code\tconcept_id\tstatus\nMMHG\t8876\tmapped\n"})))
        assert found["ok"] and found["translated"]["statuses"]["mapped"] == 1
        added = json.loads(browser.describe_pathway(json.dumps({"view": "role_drug", "table": "OBS_READING",
                                                                "sourceKind": "charted_value", "actor": "Dr B"})))
        assert added["ok"] and added["pathway"] == "charted_value"
        kind = json.loads(browser.describe_source_kind(json.dumps({"about": "role_drug@charted_value", "kind": "result", "actor": "Dr B"})))
        assert kind["ok"]
        refused = json.loads(browser.describe_source_kind(json.dumps({"about": "role_drug", "kind": "nothing"})))
        assert not refused["ok"] and "contract" not in refused["problem"]
        entries = browser._describe.log.entries()
        assert {e["scope"]["hospital"] for e in entries} == {"The Invented Hospital"}
        assert [e["actor"] for e in entries if e["kind"] in ("answer", "concepts translated")][-4:] == ["Dr A", "Dr A", "Dr B", "Dr B"]
    finally:
        browser.describe_begin("test")
