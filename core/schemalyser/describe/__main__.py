"""The command line of the hospital schema, as python -m schemalyser.describe runs it: a surface of layer 5 over the same
files as the page.

    python -m schemalyser.describe walk CALLS.json [--out FOLDER]

walk makes the hospital schema from the calls that the page makes of its bridge, in order, and saves it, so that the
command line does whatever the page does with the same files. The sitting itself (describe/__init__.py) records the test
on made-up rows and never runs it, so the walk runs it through the role shadow (roleshadow.py), as the page's bridge does,
and builds the invented hospital (hospital.py) where the calls name it.

import-evidence is the evidence import, by which the database analyst's result enters the hospital schema without the
page:

    python -m schemalyser.describe import-evidence SCHEMA.zip REQUEST.json RESULT [RESULT ...] [--id REQUEST_ID]
                                   [--actor NAME] [--provenance SOURCE] [--out FOLDER]

REQUEST.json is one request of the feasibility report, or the report itself with --id naming the request. Each RESULT
is the result of one of the request's queries, in the order the request lists them, as the results grid copies it.
The new version is written beside SCHEMA.zip, or into FOLDER, under the name that carries its schema_id.
"""
import json
import zipfile
from pathlib import Path

from .. import evidence, roleshadow
from ..hospital import HospitalError, InventedHospital, files_from
from . import WORDING, Describe, DescribeError, _read_saved, _zipped


def walk(calls, base=".", version="", sitting=None):
    """Makes the hospital schema from the calls that the page makes of its bridge, in order, so that the command line can
    do whatever the page does with the same files. Each call is {"call": the bridge function's name without describe_,
    "request": what the page sends}. A file the page reads is named by "file" (and "tables" beside a dictionary), a
    pasted result by "text" or by "file", and the invented hospital by "folder" and "catalogue", each relative to base.
    A call whose input the core refuses changes nothing and the walk goes on, as the page does when it shows the
    refusal. Returns (the sitting, which the caller saves, and [(the number of each call refused, why)])."""
    base = Path(base)
    s = sitting or Describe()
    s.version = s.version or str(version or "")

    def data(r, key="file"):
        return (base / r[key]).read_bytes() if r.get(key) else None

    def text(r):
        return r["text"] if "text" in r else (base / r["file"]).read_text(encoding="utf-8")

    def name(r, key, fallback):
        return Path(r[key]).name if r.get(key) else fallback

    held = {"hospital": None}

    def hospital(r):
        # The invented hospital from the folder of its tables and the invented catalogue, as the page publishes them
        # beside the invented dictionary.
        try:
            held["hospital"] = InventedHospital(files_from(base / r["folder"], base / r["catalogue"]))
        except (HospitalError, OSError, ValueError, KeyError):
            raise DescribeError(WORDING["invented_failed"]) from None

    actions = {
        "dictionary_upload": lambda r: s.upload(data(r), data(r, "tables"), r.get("headings") or {},
                                                name(r, "file", "dictionary.csv"), name(r, "tables", "tables.csv"),
                                                r.get("step") or "", data(r, "reference"), name(r, "reference", "lineage.json")),
        "dictionary": lambda r: s.load_dictionary(data(r), data(r, "tables"), r.get("headings") or {},
                                                  name(r, "file", "dictionary.csv"), name(r, "tables", "tables.csv"),
                                                  r.get("step") or "", invented=bool(r.get("invented"))),
        "dictionary_database": lambda r: s.load_from_database(data(r) if r.get("file") else r["text"],
                                                              name(r, "file", "data-dictionary.csv"), r.get("step") or ""),
        "dictionary_vendor": lambda r: s.add_descriptions(data(r), data(r, "tables"), r.get("headings") or {},
                                                          name(r, "file", "vendor-dictionary.csv"),
                                                          name(r, "tables", "vendor-tables.csv")),
        "schema_open": lambda r: s.restore(_read_saved(base / r["file"])),
        "hospital_build": hospital,
        "hospital_run": lambda r: s.run_invented(held["hospital"], r["query"], r["read"],
                                                 **{k: r[k] for k in ("key", "year", "name", "about") if k in r}),
        "propose": lambda r: s.propose(),
        "settings": lambda r: s.set_settings(r.get("database"), r.get("year"), r.get("timeZone"), r.get("daylightSaving"),
                                             r.get("timeZoneFrom")),
        "tables_query": lambda r: s.tables_query(r.get("step") or ""),
        "tables_read": lambda r: s.read_tables(text(r)),
        "confirm": lambda r: s.confirm(r["about"], r["answer"], r.get("replacement") or "", r.get("note") or "",
                                       actor=r.get("actor")),
        "correction_preview": lambda r: s.correction_preview(r["correction"]),
        "correction_check": lambda r: roleshadow.correction_check(s, r["correction"]),
        "correction_keep": lambda r: roleshadow.correction_keep(s, r["correction"], bool(r.get("although")),
                                                                r.get("reason") or "", actor=r.get("actor")),
        "model_check": lambda r: roleshadow.check_model(s),
        "charted_query": lambda r: s.charted_query(r["key"], r["year"], r.get("step") or ""),
        "charted_read": lambda r: s.read_charted(r["key"], text(r), r["year"]),
        "codes": lambda r: s.choose_codes(r["key"], r["chosen"], actor=r.get("actor")),
        "counts": lambda r: s.count_queries(r.get("year"), r.get("step") or ""),
        "count_read": lambda r: s.read_count(r["name"], text(r)),
        "count_judge": lambda r: s.judge_count(r["name"], r["looksRight"], r.get("note") or "", actor=r.get("actor")),
        "values_query": lambda r: s.values_query(r["about"], r["table"], r["column"], r.get("year"), r.get("step") or ""),
        "values_read": lambda r: s.read_values(r["name"], text(r)),
        "probe_query": lambda r: s.probe_query(r["about"], r.get("year"), r.get("step") or ""),
        "probe_read": lambda r: s.read_probe(r["about"], text(r)),
    }
    refused = []
    for number, held in enumerate(calls, 1):
        action = actions.get(str(held.get("call", "")).removeprefix("describe_"))
        if action is None:
            raise DescribeError(WORDING["walk_unknown"].format(number=number, call=held.get("call")))
        try:
            action(held.get("request") or {})
        except DescribeError as error:
            refused.append((number, str(error)))
    return s, refused


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="python -m schemalyser.describe")
    commands = parser.add_subparsers(dest="command", required=True)
    walked = commands.add_parser("walk", help="make a hospital schema from the calls that the page makes, and save it")
    walked.add_argument("calls", help="a JSON file of {\"version\", \"calls\": [{\"call\", \"request\"}]}, whose files are "
                                      "named relative to its own folder")
    walked.add_argument("--out", default=None, help="the folder for the saved file (by default, the folder of CALLS)")
    one = commands.add_parser("import-evidence", help="import the result of an evidence request into a saved hospital schema")
    one.add_argument("schema")
    one.add_argument("request")
    one.add_argument("results", nargs="+")
    one.add_argument("--id", default=None, help="the request's request_id or id, where REQUEST.json is a whole report")
    one.add_argument("--actor", default=None, help="the name of the person who ran the query and returned its result")
    one.add_argument("--provenance", default=None, help=f"one of: {', '.join(evidence.PROVENANCES)}")
    one.add_argument("--out", default=None, help="the folder for the new version (by default, the folder of SCHEMA)")
    args = parser.parse_args(argv)
    if args.command == "walk":
        return _main_walk(args)
    try:
        held = json.loads(Path(args.request).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(WORDING["not_a_request"], file=sys.stderr)
        return 2
    if isinstance(held, dict) and isinstance(held.get("requests"), list):
        held = next((r for r in held["requests"] if args.id in (r.get("request_id"), r.get("id"))), None)
    sitting = Describe()
    try:
        from importlib.metadata import version
        sitting.version = version("schemalyser")
    except Exception:  # noqa: BLE001 - the package may run from its folder without being installed
        sitting.version = "unknown"
    try:
        files = _read_saved(args.schema)
    except (OSError, zipfile.BadZipFile):
        print(WORDING["folder_unreadable"], file=sys.stderr)
        return 2
    sitting.restore(files)
    if sitting.data is None:
        print(WORDING["folder_unreadable"], file=sys.stderr)
        return 2
    # The import saves a new version, so the test on made-up rows that the schema owes, if any, is run first.
    roleshadow.run_owed_test(sitting)
    texts = [Path(r).read_text(encoding="utf-8", errors="replace") for r in args.results]
    names = [q.get("name") for q in (held or {}).get("queries") or [] if isinstance(q, dict)]
    result = dict(zip(names, texts)) if names and len(texts) == len(names) else texts[0]
    try:
        found = sitting.import_evidence(held, result, args.actor, args.provenance)
    except DescribeError as error:
        print(str(error), file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else Path(args.schema).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    target = out / found["file"]
    data = _zipped(found["files"])
    if target.exists() and target.read_bytes() != data:
        print(f"{target.name} already holds another version, so Schemalyser has not overwritten it.", file=sys.stderr)
        return 1
    target.write_bytes(data)
    print(WORDING["imported"].format(request=held["request_id"], schema_id=found["schema_id"], file=target.name))
    return 0



def _main_walk(args):
    import sys
    path = Path(args.calls)
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        calls = held["calls"]
    except (OSError, ValueError, KeyError, TypeError):
        print(WORDING["walk_unreadable"], file=sys.stderr)
        return 2
    try:
        sitting, refused = walk(calls, path.resolve().parent, held.get("version") or "")
    except DescribeError as error:
        print(str(error), file=sys.stderr)
        return 1
    for number, why in refused:
        print(WORDING["walk_refused"].format(number=number, why=why), file=sys.stderr)
    files = roleshadow.save(sitting)
    out = Path(args.out) if args.out else path.resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    target = out / sitting.file_name()
    target.write_bytes(_zipped(files))
    print(WORDING["walk_saved"].format(file=target.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
