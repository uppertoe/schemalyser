"""The command line of the hospital schema (describe/__main__.py): a command for each operation of the page, each of
which reads the saved hospital schema and writes a new version of it and nothing else.

The parity tests drive the page's bridge (browser.py) with a sequence of calls, as the page does, and save the hospital
schema as the page saves it; they then give the same calls to the commands, one command at a time over the saved file,
and compare the two saved files file by file. Only what cannot agree is set aside: the identifiers of entries, test runs
and versions, the times, the seconds a test took, and the versions from which the commands' entries were made, since
every command saves a version and the page saves only at the end.
"""
import json
import re
import zipfile
from pathlib import Path

import pytest

from schemalyser import browser, describe
from schemalyser.describe import __main__ as describe_main
from schemalyser.hospital import files_from
from test_describe import CATALOGUE, DICTIONARY, FIXTURES, TABLES, tables_result
from test_feasibility import CONFIRMED, COUNTS

CHARTED = "code\tcharted\tanaesthetics\tname\n52\t400\t40\tArterial mean pressure\n51\t300\t30\tCuff mean pressure\n10\t900\t90\tSaturation\n"
LINK_PROBE = "anaesthetics\twith_rows\twithout_rows\n1200\t1190\t10\n"
CORRECTION = {"form": "column", "about": "role_anaesthetic.patient_key", "table": "THEATRE_CASE", "column": "PERSON_KEY"}


def by_page(calls, base=Path(".")):
    """Gives each call to the page's bridge, as the page's worker does, and returns the bytes of the saved file and the
    numbers of the calls that the core refused."""
    browser.describe_begin("test")
    browser._hospital = None
    refused = []
    try:
        for number, given in enumerate(calls, 1):
            call, r = given["call"].removeprefix("describe_"), given.get("request") or {}

            def data(key="file"):
                return (base / r[key]).read_bytes() if r.get(key) else None
            reply = None
            if call == "dictionary_upload":
                reply = browser.describe_dictionary_upload(data(), data("tables"), json.dumps(r.get("headings") or {}),
                                                   Path(r["file"]).name, Path(r["tables"]).name if r.get("tables") else None,
                                                   r.get("step") or "")
            elif call == "dictionary":
                reply = browser.describe_dictionary(data(), data("tables"), "{}", Path(r["file"]).name, Path(r["tables"]).name,
                                            r.get("step") or "", bool(r.get("invented")))
            elif call == "hospital_build":
                files = files_from(base / r["folder"], base / r["catalogue"])
                names = sorted(files)
                assert json.loads(browser.describe_hospital_build(json.dumps(names), *[files[n] for n in names]))["ok"]
            elif call == "propose":
                reply = browser.describe_propose(lambda done, total: None)
            elif call == "tables_query":
                reply = browser.describe_tables_query(r.get("step") or "")
            elif call == "tables_read":
                reply = browser.describe_tables_read(r["text"])
            elif call in ("correction_preview", "correction_check"):
                reply = getattr(browser, f"describe_{call}")(json.dumps(r["correction"]))
            elif call == "model_check":
                reply = browser.describe_model_check()
            else:
                reply = getattr(browser, f"describe_{call}")(json.dumps(r))
            if reply is not None and not json.loads(reply)["ok"]:
                refused.append(number)
        return browser.describe_schema_zip(), refused
    finally:
        browser._describe = browser._hospital = None


def steady(data):
    """The files of a saved hospital schema, with what two saves of the same calls cannot share made into text that
    cannot differ."""
    with zipfile.ZipFile(data if isinstance(data, Path) else __import__("io").BytesIO(data)) as archive:
        held = {n: archive.read(n).decode("utf-8", "replace") for n in archive.namelist()}

    def plain(text):
        text = re.sub(r"\b[jr][0-9a-f]{12}\b", "ID", text)
        text = re.sub(r"\b[0-9a-f]{64}\b", "HASH", text)
        text = re.sub(r"\b[0-9a-f]{16}\b", "SCHEMA", text)
        text = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?(\.\d+)?(Z|[+-]\d\d:\d\d)?", "TIME", text)
        text = re.sub(r'"seconds": [\d.]+', '"seconds": N', text)
        # The version from which an entry was made, and the versions before this one: the commands save a version
        # after each operation, and the page saves only at the end.
        text = re.sub(r'"(schema_id|parent_id)": (null|"SCHEMA")', r'"\1": "SCHEMA"', text)
        return re.sub(r'"lineage": \[[^\]]*\]', '"lineage": []', text)
    return {name: plain(text) for name, text in held.items()}


def by_commands(calls, base, tmp_path):
    """Gives each call to the command that carries it, one command at a time over the saved file, and returns the path
    of the file saved at the end and the numbers of the calls that a command refused."""
    saved, refused = describe_main.walk(calls, base, "test", tmp_path / "out")
    return saved, [number for number, _ in refused]


def assert_same(page, commands):
    (page, page_refused), (commands, commands_refused) = page, commands
    assert commands_refused == page_refused
    page, commands = steady(page), steady(commands)
    assert sorted(commands) == sorted(page)
    for name in page:
        assert commands[name] == page[name], name


def pasted_calls():
    """A sitting with a dictionary of a person's own: the result of every query is pasted, a binding corrected and
    tried, the codes chosen from a pasted list, the counts read and judged, and a test query read."""
    confirm = [{"call": "confirm", "request": {"about": about, "answer": "yes", "actor": "Dr A"}} for about in CONFIRMED
               if about != "role_anaesthetic.patient_key"]
    return [
        {"call": "dictionary_upload", "request": {"file": str(DICTIONARY), "tables": str(TABLES), "step": "2"}},
        {"call": "propose", "request": {}},
        {"call": "settings", "request": {"database": "production"}},
        {"call": "settings", "request": {"year": 2024}},
        {"call": "tables_query", "request": {"step": "5"}},
        {"call": "tables_read", "request": {"text": tables_result({"OBS_READING": 400_000_000})}},
        {"call": "confirm", "request": {"about": "role_patient.death_date", "answer": "no", "replacement": "PERSON_MASTER.NO_SUCH"}},
        *confirm,
        {"call": "confirm", "request": {"about": "role_patient.death_date", "answer": "not sure", "note": "Ask the team."}},
        {"call": "correction_preview", "request": {"correction": CORRECTION}},
        {"call": "correction_check", "request": {"correction": CORRECTION}},
        {"call": "correction_keep", "request": {"correction": CORRECTION, "actor": "Dr B"}},
        {"call": "charted_query", "request": {"key": "role_reading.kind", "year": 2024, "step": "7"}},
        {"call": "charted_read", "request": {"key": "role_reading.kind", "year": 2024, "text": CHARTED}},
        {"call": "codes", "request": {"key": "role_reading.kind", "chosen": {"52": "map_arterial", "51": "map_cuff", "10": "spo2"},
                                      "actor": "Dr A"}},
        {"call": "counts", "request": {"year": 2024, "step": "8"}},
        {"call": "count_read", "request": {"name": "coverage_by_year", "text": COUNTS["coverage_by_year"]}},
        {"call": "count_read", "request": {"name": "repeated_keys", "text": COUNTS["repeated_keys"]}},
        {"call": "count_judge", "request": {"name": "coverage_by_year", "looksRight": "yes", "actor": "Dr A"}},
        {"call": "count_judge", "request": {"name": "repeated_keys", "looksRight": "no", "note": "Too few."}},
        {"call": "probe_query", "request": {"about": "role_anaesthetic.patient_key", "year": 2024}},
        {"call": "probe_read", "request": {"about": "role_anaesthetic.patient_key", "text": LINK_PROBE}},
        {"call": "model_check", "request": {}},
        {"call": "settings", "request": {"timeZone": "Australia/Sydney", "daylightSaving": True, "timeZoneFrom": "a person"}},
    ]


def test_the_commands_save_the_hospital_schema_that_the_page_saves_from_the_same_pasted_results(tmp_path):
    calls = pasted_calls()
    assert_same(by_page(calls), by_commands(calls, Path("."), tmp_path))


def invented_calls():
    """A sitting with the invented dictionary, whose every query the invented hospital answers."""
    return [
        {"call": "dictionary", "request": {"file": str(DICTIONARY), "tables": str(TABLES), "invented": True, "step": "2"}},
        {"call": "hospital_build", "request": {"folder": str(FIXTURES / "hospital"), "catalogue": str(CATALOGUE)}},
        {"call": "propose", "request": {}},
        {"call": "tables_query", "request": {"step": "5"}},
        {"call": "hospital_run", "request": {"query": "tables-and-columns", "read": "tables"}},
        {"call": "charted_query", "request": {"key": "role_reading.kind", "year": 2024}},
        {"call": "hospital_run", "request": {"query": "charted-role_reading-kind", "read": "charted", "key": "role_reading.kind",
                                             "year": 2024}},
        {"call": "codes", "request": {"key": "role_reading.kind", "chosen": {"52": "map_arterial", "51": "map_cuff"}}},
        {"call": "counts", "request": {"year": 2024}},
        *[{"call": "hospital_run", "request": {"query": f"count-{name}", "read": "count", "name": name}}
          for name in ("coverage_by_year", "repeated_keys", "readings_by_kind")],
        {"call": "count_judge", "request": {"name": "coverage_by_year", "looksRight": "yes"}},
        {"call": "values_query", "request": {"about": "role_patient.is_test", "table": "PERSON_MASTER", "column": "TEST_PERSON_FLAG",
                                             "year": 2024}},
        {"call": "hospital_run", "request": {"query": "values-role_patient-PERSON_MASTER-TEST_PERSON_FLAG", "read": "values"}},
        {"call": "probe_query", "request": {"about": "role_patient.is_test", "year": 2024}},
        {"call": "hospital_run", "request": {"query": "probe-role_patient-is_test", "read": "probe", "about": "role_patient.is_test"}},
    ]


def test_the_commands_save_the_hospital_schema_that_the_page_saves_with_the_invented_hospital_answering(tmp_path):
    calls = invented_calls()
    assert_same(by_page(calls), by_commands(calls, Path("."), tmp_path))


def run(capsys, *argv):
    """Runs one command, and returns its exit code with what it wrote to standard output and to standard error."""
    code = describe_main.main([*map(str, argv), "--tool-version", "test"])
    said = capsys.readouterr()
    return code, said.out, said.err


def versions(folder):
    return sorted(Path(folder).glob("hospital-schema-*.schemalyser.zip"))


def test_each_command_reads_the_latest_version_and_saves_a_new_one_beside_it(tmp_path, capsys):
    folder = tmp_path / "hospital"
    code, out, _ = run(capsys, "start", folder, DICTIONARY, "--tables", TABLES)
    assert code == 0 and "Schemalyser has read the dictionary invented-dictionary.csv" in out and len(versions(folder)) == 1
    # A second start in the same folder would leave two histories side by side, so it is refused.
    assert run(capsys, "start", folder, DICTIONARY)[0] == 1
    assert run(capsys, "propose", folder)[0] == 0
    code, out, err = run(capsys, "query", folder, "tables")
    assert code == 0 and out.startswith("-- Written by Schemalyser test") and "tables-and-columns" in err
    (tmp_path / "tables.tsv").write_text(tables_result(), encoding="utf-8")
    assert run(capsys, "paste", folder, "tables", tmp_path / "tables.tsv")[0] == 0
    code, out, _ = run(capsys, "answer", folder, "role_patient.birth_date", "yes", "--actor", "Dr A")
    assert code == 0 and "given by Dr A" in out and WORDING_OWED in out
    # A refusal of the core is given in its own words, and nothing is written.
    before = versions(folder)
    code, _, err = run(capsys, "answer", folder, "role_patient.death_date", "no", "--replacement", "PERSON_MASTER.NO_SUCH")
    assert code == 1 and "The dictionary holds no column PERSON_MASTER.NO_SUCH." in err and versions(folder) == before
    # Each version names the one it was made from, and the folder's latest is the one that no other was made from.
    latest = describe_main.latest(folder)
    sitting = describe.Describe()
    sitting.restore(describe._read_saved(latest))
    assert len(versions(folder)) == 5 and len(sitting.identity["lineage"]) == 4
    assert [c["by"] for c in sitting.confirmations] == ["Dr A"]
    # The test on made-up rows runs when the hospital schema is saved, and the saved version no longer owes it.
    code, out, _ = run(capsys, "save", folder)
    assert code == 0 and WORDING_OWED not in out
    saved = describe_main.latest(folder)
    assert "test_owed" not in json.loads(zipfile.ZipFile(saved).read("settings.json"))
    assert run(capsys, "save", folder)[1].startswith("Nothing has changed since the version")


def test_a_change_to_the_settings_alone_is_written_into_the_file_of_the_same_version(tmp_path, capsys):
    folder = tmp_path / "hospital"
    run(capsys, "start", folder, DICTIONARY, "--tables", TABLES)
    run(capsys, "propose", folder)
    before = versions(folder)
    code, out, _ = run(capsys, "settings", folder, "--time-zone", "Australia/Sydney", "--daylight-saving", "yes")
    assert code == 0 and "the time zone of the database's clocks as Australia/Sydney" in out
    assert versions(folder) == before
    held = json.loads(zipfile.ZipFile(describe_main.latest(folder)).read("settings.json"))
    assert held["time_zone"] == "Australia/Sydney" and held["daylight_saving"] is True


def test_a_change_is_tried_kept_or_discarded_and_only_a_kept_one_is_saved(tmp_path, capsys):
    folder = tmp_path / "hospital"
    run(capsys, "start", folder, DICTIONARY, "--tables", TABLES)
    run(capsys, "propose", folder)
    change = tmp_path / "change.json"
    change.write_text(json.dumps(CORRECTION), encoding="utf-8")
    code, out, _ = run(capsys, "correction-preview", folder, change)
    assert code == 0 and "THEATRE_CASE" in out and "SELECT" in out
    before = versions(folder)
    code, out, _ = run(capsys, "correction-check", folder, change)
    assert code == 0 and out.strip() and versions(folder) == before
    code, out, _ = run(capsys, "correction-discard", folder, change)
    assert code == 0 and "has not kept the change to role_anaesthetic.patient_key" in out and versions(folder) == before
    code, out, _ = run(capsys, "correction-keep", folder, change, "--actor", "Dr B")
    assert code == 0 and "has kept the change to role_anaesthetic.patient_key" in out and len(versions(folder)) == 3
    sitting = describe.Describe()
    sitting.restore(describe._read_saved(describe_main.latest(folder)))
    kept = sitting.log.latest("correction kept")
    assert kept["actor"] == "Dr B" and sitting.log.latest("test run") is not None
    (tmp_path / "bad.json").write_text("[]", encoding="utf-8")
    assert run(capsys, "correction-keep", folder, tmp_path / "bad.json")[0] == 2


def test_show_gives_the_readiness_the_stale_evidence_and_the_journal(tmp_path, capsys):
    folder = tmp_path / "hospital"
    run(capsys, "start", folder, DICTIONARY, "--tables", TABLES)
    run(capsys, "propose", folder)
    run(capsys, "answer", folder, "role_patient.birth_date", "yes")
    run(capsys, "save", folder)
    code, out, _ = run(capsys, "show", folder)
    assert code == 0 and "## How far the hospital schema has been checked" in out
    assert describe.WORDING["cli_none_stale"] in out and "The journal holds" in out and ", answer, by not recorded" in out
    code, out, _ = run(capsys, "show", folder, "--json")
    held = json.loads(out)
    assert {"view", "readiness", "stale", "journal", "files"} <= set(held) and "map/map.json" in held["files"]
    code, out, _ = run(capsys, "lookup", folder, "columns", "PERSON_MASTER")
    assert code == 0 and json.loads(out)["table"] == "PERSON_MASTER"
    code, out, _ = run(capsys, "scoreboard", folder)
    assert code == 0 and not (folder / "scoreboard-summary.md").exists() and out.strip()
    assert run(capsys, "dictionary-query")[1].startswith("-- Written by Schemalyser test")
    assert run(capsys, "show", tmp_path / "nothing-here")[0] == 2


WORDING_OWED = describe.WORDING["cli_owed"]
