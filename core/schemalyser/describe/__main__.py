"""The command line of the hospital schema, as python -m schemalyser.describe runs it: a surface of layer 5 over the same
files as the page Describe the record.

Every operation that a person performs on the page has a command here, and docs/describe.md lists them with a sentence
each. A command reads the saved hospital schema, which is one file, performs one operation of the sitting
(describe/__init__.py), and saves what the operation changed as a new version beside it, under the name that carries
the version's schema_id. It reads and writes nothing else, so that the saved file is the only state: the next command
reads the version that this one wrote. Where a command is given a folder in place of a file, it reads the latest version
in the folder, which is the one from which no other version there was made.

    start FOLDER DICTIONARY [--tables FILE] [--reference FILE] [--heading FIELD=HEADING] [--invented] [--hospital NAME]
    add-descriptions SCHEMA VENDOR [--tables FILE] [--heading FIELD=HEADING]
    dictionary-query
    propose SCHEMA
    settings SCHEMA [--database NAME] [--year YEAR] [--hospital NAME] [--time-zone ZONE] [--daylight-saving yes|no]
             [--time-zone-from SOURCE] [--actor NAME]
    query SCHEMA tables|codes|counts|values|probe [--key KEY] [--year YEAR] [--about ABOUT] [--table T] [--column C]
    paste SCHEMA tables|codes|count|values|probe RESULT [--key KEY] [--year YEAR] [--name NAME] [--about ABOUT]
    invented-run SCHEMA QUERY tables|codes|count|values|probe [--key KEY] [--year YEAR] [--name NAME] [--about ABOUT]
    answer SCHEMA ABOUT yes|no|not-sure [--replacement TABLE.COLUMN] [--note TEXT] [--actor NAME]
    translate-codes SCHEMA KEY CODE=KIND ... [--actor NAME]
    translate-concepts SCHEMA MAPPING ROWS [--actor NAME]
    add-pathway SCHEMA PART TABLE KIND [--name NAME] [--actor NAME]
    source-kind SCHEMA PART KIND [--actor NAME]
    judge SCHEMA COUNT yes|no [--note TEXT] [--actor NAME]
    correction-preview SCHEMA CORRECTION.json
    correction-check SCHEMA CORRECTION.json
    correction-keep SCHEMA CORRECTION.json [--although --reason TEXT] [--actor NAME]
    correction-discard SCHEMA CORRECTION.json
    test SCHEMA
    save SCHEMA
    show SCHEMA [--json]
    check SCHEMA
    compare SCHEMA NAME RESULT
    lookup SCHEMA tables|columns|joins [TABLE]
    scoreboard SCHEMA [--write]
    import-evidence SCHEMA REQUEST.json RESULT [RESULT ...] [--id REQUEST_ID] [--actor NAME] [--provenance SOURCE]
    walk CALLS.json [--out FOLDER]

The sitting records the test on made-up rows and never runs it, so the commands that need one (test, the check and keep
of a correction, save and the evidence import) hand the sitting to the role shadow (roleshadow.py), as the page's bridge
does. Every other command saves its version without the test, and the version then says that the test is owed, as the
page's sitting does until a person saves it.

walk is a convenience: it takes the calls that the page makes of its bridge, in order, gives each to the command that
carries it, over a folder of its own, and saves the hospital schema that they make. BRIDGE below names the command for
each operation of the bridge, and the test of invariant 9 reads it.
"""
import argparse
import contextlib
import csv
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from .. import evidence, propose, rolemap, roleshadow, summaries
from ..hospital import HospitalError, InventedHospital, files_from
from . import NOT_RECORDED, WORDING, Describe, DescribeError, _read_saved, _zipped

ROOT = Path(__file__).resolve().parents[3]
INVENTED_HOSPITAL = ROOT / "fixtures" / "hospital"
INVENTED_CATALOGUE = ROOT / "fixtures" / "invented-catalogue.csv"
SAVED = "hospital-schema*.schemalyser.zip"

# The command that carries each operation of the page's bridge, the describe_ functions of browser.py.
BRIDGE = {
    "begin": "start", "dictionary": "start", "dictionary_upload": "start", "dictionary_database": "start",
    "dictionary_query": "dictionary-query", "dictionary_vendor": "add-descriptions",
    "schema_open": "show", "model": "show", "schema_files": "show",
    "propose": "propose", "settings": "settings",
    "tables_query": "query", "charted_query": "query", "counts": "query", "values_query": "query", "probe_query": "query",
    "tables_read": "paste", "charted_read": "paste", "count_read": "paste", "values_read": "paste", "probe_read": "paste",
    "hospital_build": "invented-run", "hospital_run": "invented-run",
    "confirm": "answer", "codes": "translate-codes", "count_judge": "judge", "concepts": "translate-concepts",
    "pathway": "add-pathway", "source_kind": "source-kind",
    "correction_preview": "correction-preview", "correction_check": "correction-check", "correction_keep": "correction-keep",
    "model_check": "test", "schema_zip": "save",
    "check": "check", "compare": "compare", "names": "lookup", "columns": "lookup", "joins": "lookup",
    "import_evidence": "import-evidence",
}
READINGS = {"tables": "tables", "codes": "charted", "count": "count", "values": "values", "probe": "probe"}


class Refused(Exception):
    """A command that cannot go on: its sentence, and the exit code, 1 where the core refused the input and 2 where a
    file could not be read."""

    def __init__(self, sentence, code=1):
        super().__init__(sentence)
        self.code = code


def _installed():
    try:
        from importlib.metadata import version
        return version("schemalyser")
    except Exception:  # noqa: BLE001 - the package may run from its folder without being installed
        return "unknown"


def _bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        raise Refused(WORDING["cli_unreadable_file"].format(name=Path(path).name), 2) from None


def _text(path):
    return _bytes(path).decode("utf-8", errors="replace")


def _settings_of(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read("settings.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def latest(folder):
    """The latest version of the hospital schema in a folder: the one from which no other version there was made."""
    folder = Path(folder)
    held = {path: _settings_of(path) for path in sorted(folder.glob(SAVED))}
    held = {path: s for path, s in held.items() if s.get("schema_id")}
    if not held:
        raise Refused(WORDING["cli_no_schema"].format(folder=folder), 2)
    parents = set()
    for settings in held.values():
        parents.update([settings.get("parent_id"), *(settings.get("lineage") or [])])
    found = [path for path, settings in held.items() if settings["schema_id"] not in parents]
    if len(found) != 1:
        raise Refused(WORDING["cli_several"].format(folder=folder, count=len(found)), 2)
    return found[0]


def _open(given, version):
    """The sitting restored from the saved hospital schema, and the path of the file it was read from."""
    path = Path(given)
    if path.is_dir():
        path = latest(path)
    try:
        files = _read_saved(path)
    except (OSError, zipfile.BadZipFile):
        raise Refused(WORDING["cli_unreadable"].format(name=path.name), 2) from None
    sitting = Describe()
    sitting.version = version
    found = sitting.restore(files)
    if found["refused"]:
        raise Refused(WORDING["cli_refused"].format(name=path.name, rule=found["refused"]))
    if found["needs_dictionary"]:
        raise Refused(WORDING["cli_needs_dictionary"].format(name=path.name))
    if sitting.dictionary is None:
        raise Refused(WORDING["cli_unreadable"].format(name=path.name), 2)
    return sitting, path


def _steady(files):
    # What differs between two writes of one version: the time of the save, and the README written from the rest.
    settings = json.loads(files["settings.json"])
    settings.pop("saved", None)
    return {name: data for name, data in files.items() if name not in ("settings.json", "README.md")}, settings


def _write(sitting, folder, tested=False):
    """Saves a new version of the sitting into folder and says so. With tested, the test on made-up rows that the
    hospital schema owes runs first, as the page's save does. Returns the path written."""
    files = roleshadow.save(sitting) if tested else sitting.save()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / sitting.file_name()
    schema_id = sitting.identity["schema_id"]
    if target.exists():
        try:
            held = _read_saved(target)
        except (OSError, zipfile.BadZipFile):
            held = None
        if held is None or _settings_of(target).get("schema_id") != schema_id:
            raise Refused(WORDING["cli_other_version"].format(file=target.name))
        if _steady(held) == _steady(files):
            print(WORDING["cli_unchanged"].format(schema_id=schema_id, file=target.name))
            return target
    # A version is named by the hash of every file but settings.json, so a change to the settings alone is written
    # into the file of the same version.
    target.write_bytes(_zipped(files))
    print(WORDING["cli_saved"].format(schema_id=schema_id, file=target.name))
    if json.loads(files["settings.json"]).get("test_owed"):
        print(WORDING["cli_owed"])
    return target


def _pairs(items, sentence):
    found = {}
    for item in items or []:
        name, _, value = item.partition("=")
        if not name.strip() or not value.strip():
            raise Refused(sentence.format(item=item), 2)
        found[name.strip()] = value.strip()
    return found


def _correction(path):
    try:
        held = json.loads(_text(path))
    except ValueError:
        held = None
    if not isinstance(held, dict):
        raise Refused(WORDING["cli_correction_file"].format(name=Path(path).name), 2)
    return held


def _rows_of(count):
    return f"{count:,} {'row' if count == 1 else 'rows'}"


def _show_report(report):
    print(report["sentence"])
    if report.get("mended"):
        print(report["mended"])
    for problem in report.get("problems") or []:
        print(f"- {problem}")


# The commands. Each takes the parsed arguments and the version of Schemalyser to record, and raises Refused where it
# cannot go on.

def cmd_start(args, version):
    folder = Path(args.folder)
    if folder.is_dir() and any(folder.glob(SAVED)):
        raise Refused(WORDING["cli_started_already"].format(folder=folder))
    sitting = Describe()
    sitting.version = version
    if args.hospital is not None:
        sitting.set_settings(hospital=args.hospital)
    headings = _pairs(args.heading, WORDING["cli_heading"])
    data, tables = _bytes(args.dictionary), _bytes(args.tables) if args.tables else None
    name, tables_name = Path(args.dictionary).name, Path(args.tables).name if args.tables else "tables.csv"
    if args.invented:
        receipt = sitting.load_dictionary(data, tables, headings, name, tables_name, args.step, invented=True)
        if args.reference:
            receipt["reference"] = sitting.load_reference(_bytes(args.reference), Path(args.reference).name)
    else:
        _, receipt = sitting.upload(data, tables, headings, name, tables_name, args.step,
                                    _bytes(args.reference) if args.reference else None,
                                    Path(args.reference).name if args.reference else "lineage.json")
    print(WORDING["cli_dictionary"].format(file=receipt["file"], tables=f"{receipt['tables']:,}",
                                           columns=f"{receipt['columns']:,}", described=f"{receipt['described']:,}"))
    if receipt.get("reference"):
        print(WORDING["cli_reference"].format(**receipt["reference"]))
    _write(sitting, folder)


def cmd_add_descriptions(args, version):
    sitting, path = _open(args.schema, version)
    headings = _pairs(args.heading, WORDING["cli_heading"])
    receipt = sitting.add_descriptions(_bytes(args.vendor), _bytes(args.tables) if args.tables else None, headings,
                                       Path(args.vendor).name, Path(args.tables).name if args.tables else "vendor-tables.csv")
    print(WORDING["cli_vendor"].format(**receipt["vendor"]))
    _write(sitting, args.out or path.parent)


def cmd_dictionary_query(args, version):
    sitting = Describe()
    sitting.version = version
    print(sitting.dictionary_query(args.step, record=False)["sql"])


def cmd_propose(args, version):
    sitting, path = _open(args.schema, version)
    sitting.propose()
    print(sitting._described())
    _write(sitting, args.out or path.parent)


def cmd_settings(args, version):
    sitting, path = _open(args.schema, version)
    daylight = None if args.daylight_saving is None else args.daylight_saving == "yes"
    sitting.set_settings(args.database, args.year, args.time_zone, daylight, args.time_zone_from, hospital=args.hospital,
                         actor=args.actor)
    held = sitting.settings
    print(WORDING["cli_settings"].format(**{k: held.get(k) if held.get(k) is not None else NOT_RECORDED
                                            for k in ("database", "year", "time_zone")}))
    _write(sitting, args.out or path.parent)


def cmd_query(args, version):
    sitting, path = _open(args.schema, version)
    if args.what == "tables":
        found = [{"name": "tables-and-columns", "sql": sitting.tables_query(args.step)["sql"]}]
    elif args.what == "codes":
        found = [{"name": f"charted-{args.key.replace('.', '-')}",
                  "sql": sitting.charted_query(args.key, args.year, args.step)["sql"]}]
    elif args.what == "counts":
        found = [{"name": f"count-{q['name']}", "sql": q["sql"]} for q in sitting.count_queries(args.year, args.step)]
    elif args.what == "values":
        offered = sitting.values_query(args.about, args.table, args.column, args.year, args.step)
        found = [{"name": offered["name"], "sql": offered["sql"]}]
    else:
        found = [{"name": "probe-" + re.sub(r"[^\w]+", "-", args.about or "").strip("-"),
                  "sql": sitting.probe_query(args.about, args.year, args.step)["sql"]}]
    for query in found:
        if args.name in (None, query["name"], query["name"].removeprefix("count-")):
            print(query["sql"].rstrip("\n"))
            print("")
    # The query goes to standard output, for the database analyst to copy, and what Schemalyser did to standard error.
    with contextlib.redirect_stdout(sys.stderr):
        for query in found:
            print(WORDING["cli_offered"].format(name=query["name"]))
        _write(sitting, args.out or path.parent)


def _read(sitting, what, text, args):
    if what == "tables":
        receipt = sitting.read_tables(text)
        return "tables-and-columns", WORDING["cli_read"].format(name="tables-and-columns", rows=f"{receipt['tables']:,} tables "
                                                                f"and {receipt['columns']:,} columns"), []
    if what == "charted":
        receipt = sitting.read_charted(args.key, text, args.year)
        name = f"charted-{args.key.replace('.', '-')}"
        return name, WORDING["cli_read"].format(name=name, rows=_rows_of(receipt["rows"])), []
    if what == "count":
        receipt = sitting.read_count(args.name, text)
        return f"count-{args.name}", WORDING["cli_read"].format(name=f"count-{args.name}", rows=_rows_of(receipt["rows"])), \
            receipt["findings"]
    if what == "values":
        receipt = sitting.read_values(args.name, text)
        return args.name, WORDING["cli_read"].format(name=args.name, rows=_rows_of(len(receipt["values"]))), []
    receipt = sitting.read_probe(args.about, text)
    return "probe", WORDING["cli_read"].format(name=f"the test query of {args.about}", rows=_rows_of(receipt["rows"])), \
        receipt["findings"]


def _findings(found):
    for finding in found or []:
        print(finding if isinstance(finding, str) else finding.get("says") or json.dumps(finding, ensure_ascii=False))


def cmd_paste(args, version):
    sitting, path = _open(args.schema, version)
    _, sentence, findings = _read(sitting, READINGS[args.what], _text(args.result), args)
    print(sentence)
    _findings(findings)
    _write(sitting, args.out or path.parent)


def cmd_invented_run(args, version):
    sitting, path = _open(args.schema, version)
    try:
        hospital = InventedHospital(files_from(args.hospital, args.catalogue))
    except (HospitalError, OSError, ValueError, KeyError):
        raise Refused(WORDING["invented_failed"]) from None
    read = READINGS[args.read]
    given = {k: getattr(args, k) for k in ("key", "year", "name", "about") if getattr(args, k) is not None}
    if "year" in given:
        given["year"] = int(given["year"]) if str(given["year"]).isdecimal() else given["year"]
    receipt = sitting.run_invented(hospital, args.query, read, **given)
    if "tables" in receipt:
        held = f"{receipt['tables']:,} tables and {receipt['columns']:,} columns"
    else:
        held = _rows_of(receipt["rows"] if "rows" in receipt else len(receipt.get("values") or []))
    print(WORDING["cli_read"].format(name=args.query, rows=held))
    _findings(receipt.get("findings"))
    _write(sitting, args.out or path.parent)


def cmd_answer(args, version):
    sitting, path = _open(args.schema, version)
    answer = propose.ANSWERS.get(args.answer.lower().replace("-", " "), args.answer)
    sitting.confirm(args.about, answer, args.replacement or "", args.note or "", actor=args.actor)
    print(WORDING["cli_answered"].format(answer=answer, about=args.about, actor=args.actor or NOT_RECORDED))
    _write(sitting, args.out or path.parent)


def cmd_translate_codes(args, version):
    sitting, path = _open(args.schema, version)
    chosen = _pairs(args.codes, WORDING["cli_pair"])
    sitting.choose_codes(args.key, chosen, actor=args.actor)
    print(WORDING["cli_codes"].format(count=f"{len(chosen):,} {'code' if len(chosen) == 1 else 'codes'}", key=args.key,
                                      actor=args.actor or NOT_RECORDED))
    _write(sitting, args.out or path.parent)


def cmd_translate_concepts(args, version):
    sitting, path = _open(args.schema, version)
    found = sitting.read_concepts(args.mapping, _text(args.rows), actor=args.actor)
    print(WORDING["cli_concepts"].format(mapping=found["mapping"], **{k: f"{v:,}" for k, v in found["statuses"].items()}))
    _write(sitting, args.out or path.parent)


def cmd_add_pathway(args, version):
    sitting, path = _open(args.schema, version)
    added = sitting.add_pathway(args.part, args.table, args.kind, args.name, actor=args.actor)
    print(WORDING["cli_pathway"].format(part=args.part, name=added["name"], table=args.table, kind=args.kind,
                                        actor=args.actor or NOT_RECORDED))
    _write(sitting, args.out or path.parent)


def cmd_source_kind(args, version):
    sitting, path = _open(args.schema, version)
    sitting.choose_source_kind(args.part, args.kind, actor=args.actor)
    print(WORDING["cli_source_kind"].format(part=args.part, kind=args.kind, actor=args.actor or NOT_RECORDED))
    _write(sitting, args.out or path.parent)


def cmd_judge(args, version):
    sitting, path = _open(args.schema, version)
    sitting.judge_count(args.count, args.looks_right, args.note or "", actor=args.actor)
    print(WORDING["cli_judged"].format(name=args.count, looks_right=args.looks_right, actor=args.actor or NOT_RECORDED))
    _write(sitting, args.out or path.parent)


def cmd_correction_preview(args, version):
    sitting, _ = _open(args.schema, version)
    found = sitting.correction_preview(_correction(args.correction))
    print(found["sentence"])
    print("")
    print(found["sql"].rstrip("\n"))


def cmd_correction_check(args, version):
    sitting, _ = _open(args.schema, version)
    _show_report(roleshadow.correction_check(sitting, _correction(args.correction)))


def cmd_correction_keep(args, version):
    sitting, path = _open(args.schema, version)
    correction = _correction(args.correction)
    if not sitting.check_recorded(correction):
        _show_report(roleshadow.correction_check(sitting, correction))
    kept = roleshadow.correction_keep(sitting, correction, args.although, args.reason or "", actor=args.actor)
    print(WORDING["cli_kept"].format(about=kept["kept"]))
    _write(sitting, args.out or path.parent)


def cmd_correction_discard(args, version):
    sitting, _ = _open(args.schema, version)
    correction = _correction(args.correction)
    # The change is read as the trial read it, so that a change that could not be tried is refused with its reason.
    preview = sitting.correction_preview(correction)
    print(WORDING["cli_discarded"].format(about=correction.get("about") or preview["view"]))


def cmd_test(args, version):
    sitting, path = _open(args.schema, version)
    _show_report(roleshadow.check_model(sitting))
    _write(sitting, args.out or path.parent)


def cmd_save(args, version):
    sitting, path = _open(args.schema, version)
    _write(sitting, args.out or path.parent, tested=True)


def cmd_show(args, version):
    sitting, path = _open(args.schema, version)
    entries = sitting.log.entries()
    if args.json:
        print(json.dumps({"file": path.name, "view": sitting.view(), "readiness": sitting.readiness(),
                          "stale": sitting.stale_evidence(), "journal": entries,
                          "files": sorted(_read_saved(path))}, indent=2, ensure_ascii=False, default=str))
        return
    held = _read_saved(path)
    print(held.get("README.md", b"").decode("utf-8", errors="replace").rstrip("\n"))
    print("")
    stale = sitting.stale_evidence()
    print(WORDING["cli_stale"] if stale else WORDING["cli_none_stale"])
    for item in stale:
        print(f"- {item['subject']}, {item['dimension']}: {', '.join(item['reasons'])}")
    print("")
    print(WORDING["cli_journal"].format(count=f"{len(entries):,} {'entry' if len(entries) == 1 else 'entries'}"))
    for entry in reversed(entries):
        print(f"- {entry['sequence']}. {entry['time']}, {entry['kind']}, by {entry['actor']} ({entry['provenance']})")


def cmd_check(args, version):
    sitting, _ = _open(args.schema, version)
    print(json.dumps(sitting.check(), indent=2, ensure_ascii=False, default=str))


def cmd_compare(args, version):
    sitting, _ = _open(args.schema, version)
    found = sitting.compare(args.name, _text(args.result))
    for line in found["differences"]:
        print(line)


def cmd_lookup(args, version):
    sitting, _ = _open(args.schema, version)
    found = sitting.names() if args.what == "tables" else \
        sitting.columns_of(args.table or "") if args.what == "columns" else sitting.joins_from(args.table or "")
    print(json.dumps(found, indent=2, ensure_ascii=False))


def cmd_scoreboard(args, version):
    sitting, path = _open(args.schema, version)
    board = rolemap.scoreboard(sitting.data or {"roles": {}})
    print(board["text"], end="")
    if args.write:
        written = summaries.write_scoreboard(board, path.parent)
        print(WORDING["cli_scoreboard"].format(file=written[1].name))


def cmd_import_evidence(args, version):
    try:
        held = json.loads(Path(args.request).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(WORDING["not_a_request"], 2) from None
    if isinstance(held, dict) and isinstance(held.get("requests"), list):
        held = next((r for r in held["requests"] if args.id in (r.get("request_id"), r.get("id"))), None)
    sitting, path = _open(args.schema, version)
    # The import saves a new version, so the test on made-up rows that the schema owes, if any, is run first.
    roleshadow.run_owed_test(sitting)
    texts = [_text(r) for r in args.results]
    names = [q.get("name") for q in (held or {}).get("queries") or [] if isinstance(q, dict)]
    result = dict(zip(names, texts)) if names and len(texts) == len(names) else texts[0]
    found = sitting.import_evidence(held, result, args.actor, args.provenance)
    out = Path(args.out) if args.out else path.resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    target = out / found["file"]
    data = _zipped(found["files"])
    if target.exists() and target.read_bytes() != data:
        raise Refused(WORDING["cli_other_version"].format(file=target.name))
    target.write_bytes(data)
    print(WORDING["imported"].format(request=held["request_id"], schema_id=found["schema_id"], file=target.name))


# The walk of the page's calls, each given to the command that carries it.

def _argv(call, r, base, work, scratch, held):
    """The arguments of the command that carries one call of the page's bridge, or None for a call that changes nothing
    that the saved hospital schema holds or that the walk takes for itself."""
    number = len(list(scratch.iterdir()))

    def path(key="file"):
        return str(base / r[key])

    def text(name):
        # A pasted result goes into a file of the walk's own, which the command then reads.
        if "text" not in r:
            return path()
        target = scratch / f"{number:04d}" / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(r["text"], encoding="utf-8")
        return str(target)

    def json_file(value, name):
        target = scratch / f"{number:04d}-{name}.json"
        target.write_text(json.dumps(value), encoding="utf-8")
        return str(target)

    def opt(flag, value):
        return [] if value is None or value == "" else [flag, str(value)]

    step = opt("--step", r.get("step"))
    s = str(work)
    if call in ("dictionary_upload", "dictionary", "dictionary_database"):
        source = path() if r.get("file") else text("data-dictionary.csv")
        headings = [f"--heading={k}={v}" for k, v in (r.get("headings") or {}).items() if v]
        return ["start", s, source, *opt("--tables", path("tables") if r.get("tables") else None),
                *opt("--reference", path("reference") if r.get("reference") else None), *headings, *step,
                *(["--invented"] if call == "dictionary" and r.get("invented") else [])]
    if call == "dictionary_vendor":
        headings = [f"--heading={k}={v}" for k, v in (r.get("headings") or {}).items() if v]
        return ["add-descriptions", s, path(), *opt("--tables", path("tables") if r.get("tables") else None), *headings]
    if call == "schema_open":
        for old in work.glob(SAVED):
            old.unlink()
        shutil.copy(path(), work / Path(r["file"]).name)
        return None
    if call == "hospital_build":
        held["hospital"] = (path("folder"), path("catalogue"))
        return None
    if call == "hospital_run":
        folder, catalogue = held.get("hospital") or (str(INVENTED_HOSPITAL), str(INVENTED_CATALOGUE))
        read = {v: k for k, v in READINGS.items()}.get(r["read"], r["read"])
        return ["invented-run", s, r["query"], read, "--hospital", folder, "--catalogue", catalogue,
                *[a for k in ("key", "year", "name", "about") for a in opt(f"--{k}", r.get(k))]]
    if call == "propose":
        return ["propose", s]
    if call == "settings":
        daylight = r.get("daylightSaving")
        return ["settings", s, *opt("--database", r.get("database")), *opt("--year", r.get("year")),
                *(["--hospital", str(r["hospital"])] if r.get("hospital") is not None else []), *opt("--actor", r.get("actor")),
                *(["--time-zone", str(r["timeZone"])] if r.get("timeZone") is not None else []),
                *opt("--daylight-saving", None if daylight is None else "yes" if daylight else "no"),
                *opt("--time-zone-from", r.get("timeZoneFrom"))]
    if call == "tables_query":
        return ["query", s, "tables", *step]
    if call == "tables_read":
        return ["paste", s, "tables", text("tables.tsv")]
    if call == "confirm":
        return ["answer", s, r["about"], r["answer"], *opt("--replacement", r.get("replacement")),
                *opt("--note", r.get("note")), *opt("--actor", r.get("actor"))]
    if call in ("correction_preview", "correction_check"):
        return [call.replace("_", "-"), s, json_file(r["correction"], "correction")]
    if call == "correction_keep":
        return ["correction-keep", s, json_file(r["correction"], "correction"), *(["--although"] if r.get("although") else []),
                *opt("--reason", r.get("reason")), *opt("--actor", r.get("actor"))]
    if call == "model_check":
        return ["test", s]
    if call == "charted_query":
        return ["query", s, "codes", "--key", r["key"], "--year", str(r["year"]), *step]
    if call == "charted_read":
        return ["paste", s, "codes", text("charted.tsv"), "--key", r["key"], "--year", str(r["year"])]
    if call == "codes":
        return ["translate-codes", s, r["key"], *[f"{code}={kind}" for code, kind in (r.get("chosen") or {}).items()],
                *opt("--actor", r.get("actor"))]
    if call == "counts":
        return ["query", s, "counts", *opt("--year", r.get("year")), *step]
    if call == "count_read":
        return ["paste", s, "count", text("count.tsv"), "--name", r["name"]]
    if call == "concepts":
        return ["translate-concepts", s, r.get("mapping") or "", text("concepts.txt"), *opt("--actor", r.get("actor"))]
    if call == "pathway":
        return ["add-pathway", s, r.get("view") or "", r.get("table") or "", r.get("sourceKind") or "",
                *opt("--name", r.get("name")), *opt("--actor", r.get("actor"))]
    if call == "source_kind":
        return ["source-kind", s, r.get("about") or "", r.get("kind") or "", *opt("--actor", r.get("actor"))]
    if call == "count_judge":
        return ["judge", s, r["name"], r["looksRight"], *opt("--note", r.get("note")), *opt("--actor", r.get("actor"))]
    if call == "values_query":
        return ["query", s, "values", "--about", r["about"], "--table", r["table"], "--column", r["column"],
                *opt("--year", r.get("year")), *step]
    if call == "values_read":
        return ["paste", s, "values", text("values.tsv"), "--name", r["name"]]
    if call == "probe_query":
        return ["query", s, "probe", "--about", r["about"], *opt("--year", r.get("year")), *step]
    if call == "probe_read":
        return ["paste", s, "probe", text("probe.tsv"), "--about", r["about"]]
    if call == "import_evidence":
        result = r.get("result") or ""
        results = []
        if isinstance(result, dict):
            for name, value in result.items():
                target = scratch / f"{number:04d}-{len(results)}-result.txt"
                target.write_text(value, encoding="utf-8")
                results.append(str(target))
        else:
            target = scratch / f"{number:04d}-result.txt"
            target.write_text(result, encoding="utf-8")
            results.append(str(target))
        return ["import-evidence", s, json_file(r.get("request"), "request"), *results, *opt("--actor", r.get("actor")),
                *opt("--provenance", r.get("provenance"))]
    if call == "schema_zip":
        return ["save", s]
    return None


def walk(calls, base=".", version="", out=None):
    """Makes the hospital schema from the calls that the page makes of its bridge, in order, by giving each to the command
    that carries it, so that the command line does whatever the page does with the same files. Each call is {"call": the
    bridge function's name, with or without describe_, "request": what the page sends}. A file the page reads is named by
    "file" (and "tables" or "reference" beside a dictionary), a pasted result by "text" or by "file", and the invented
    hospital by "folder" and "catalogue", each relative to base. A call that the command refuses changes nothing and the
    walk goes on, as the page does when it shows the refusal. The hospital schema is saved at the end, as the page saves
    it, into out. Returns (the path of the saved file, [(the number of each call refused, why)])."""
    base = Path(base)
    version = str(version or "")
    refused, held = [], {}
    with tempfile.TemporaryDirectory(prefix="schemalyser-walk-") as temporary:
        work, scratch = Path(temporary) / "schema", Path(temporary) / "calls"
        work.mkdir()
        scratch.mkdir()
        # The page may record the hospital's name before a dictionary is loaded, when no saved schema exists yet for a
        # command to read; such a call waits until the first version is saved. Settings are not journalled, so the order
        # does not change what is saved.
        waiting = []
        queue = [(number, given) for number, given in enumerate(calls, 1)]
        while queue:
            number, given = queue.pop(0)
            call = str(given.get("call", "")).removeprefix("describe_")
            if call not in BRIDGE:
                raise Refused(WORDING["walk_unknown"].format(number=number, call=given.get("call")))
            if call == "settings" and not any(work.glob(SAVED)):
                waiting.append((number, given))
                continue
            if waiting and any(work.glob(SAVED)):
                queue = waiting + [(number, given)] + queue
                waiting = []
                continue
            argv = _argv(call, given.get("request") or {}, base, work, scratch, held)
            if argv is None:
                continue
            named = [w["request"]["hospital"] for _, w in waiting if (w.get("request") or {}).get("hospital") is not None]
            if argv[0] == "start" and named:
                # The hospital named before the dictionary was loaded is in the scope of the first entry, as on the page.
                argv += ["--hospital", str(named[-1])]
            said = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(said):
                try:
                    code = main([*argv, "--tool-version", version])
                except SystemExit as stopped:
                    # The command's own arguments could not be read, which argparse reports before it stops.
                    code = stopped.code or 2
            if code:
                refused.append((number, said.getvalue().strip()))
        refused.sort()
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["save", str(work), "--tool-version", version])
        if code:
            raise Refused(WORDING["walk_unreadable"])
        saved = latest(work)
        target = Path(out) / saved.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(saved, target)
    return target, refused


def cmd_walk(args, version):
    path = Path(args.calls)
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        calls = held["calls"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused(WORDING["walk_unreadable"], 2) from None
    target, refused = walk(calls, path.resolve().parent, held.get("version") or version,
                           Path(args.out) if args.out else path.resolve().parent)
    for number, why in refused:
        print(WORDING["walk_refused"].format(number=number, why=why), file=sys.stderr)
    print(WORDING["walk_saved"].format(file=target.name))


def parser():
    top = argparse.ArgumentParser(prog="python -m schemalyser.describe",
                                  description="The command line of the hospital schema. Each command reads the saved "
                                              "hospital schema and saves what it changed as a new version beside it.")
    commands = top.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tool-version", default=None,
                        help="the version of Schemalyser that the files record; by default, the installed version")
    schema = argparse.ArgumentParser(add_help=False, parents=[common])
    schema.add_argument("schema", help="the saved hospital schema, or the folder that holds its versions, of which the "
                                       "latest is read")
    writes = argparse.ArgumentParser(add_help=False, parents=[schema])
    writes.add_argument("--out", default=None, help="the folder for the new version; by default, the folder of SCHEMA")
    actor = argparse.ArgumentParser(add_help=False)
    actor.add_argument("--actor", default=None, help="the name of the person who did this, which the journal records; "
                                                     "without it, the journal records not recorded")
    step = argparse.ArgumentParser(add_help=False)
    step.add_argument("--step", default="", help="the step of the page that this belongs to, which the journal records")

    def add(name, help, parents=(), **more):
        made = commands.add_parser(name, help=help, description=help[0].upper() + help[1:] + ".", parents=list(parents), **more)
        return made

    one = add("start", "start a hospital schema from a dictionary, and save its first version in a folder", [common, step])
    one.add_argument("folder", help="an empty folder, or a new one, for the versions of the hospital schema")
    one.add_argument("dictionary", help="the dictionary: a vendor's export, or the saved result of the data dictionary query")
    one.add_argument("--tables", help="a second file that gives each table's description and primary key")
    one.add_argument("--reference", help="a reference conversion's lineage, which the proposer reads beside the dictionary")
    one.add_argument("--heading", action="append", default=[], metavar="FIELD=HEADING",
                     help="the dictionary's own heading for a field: table, column, description, data_type or key")
    one.add_argument("--invented", action="store_true", help="say that the dictionary is the invented one")
    one.add_argument("--hospital", help="the name of the hospital that the hospital schema describes")
    one = add("add-descriptions", "add the vendor's descriptions to a dictionary made from the database", [writes])
    one.add_argument("vendor")
    one.add_argument("--tables")
    one.add_argument("--heading", action="append", default=[], metavar="FIELD=HEADING")
    add("dictionary-query", "print the data dictionary query, which is the same for every hospital", [common, step])
    add("propose", "propose where the hospital's database keeps each part of the record", [writes])
    one = add("settings", "record the database, the year of the lists, the hospital's name, or the time zone of the "
                          "database's clocks", [writes, actor])
    one.add_argument("--database", help="production, training or unsure")
    one.add_argument("--year")
    one.add_argument("--time-zone", help="a name such as Australia/Sydney or UTC")
    one.add_argument("--daylight-saving", choices=("yes", "no"))
    one.add_argument("--time-zone-from", help="a person, or proposed from this computer")
    one.add_argument("--hospital", help="the name of the hospital that the hospital schema describes; an empty name takes "
                                        "back one given earlier")
    one = add("query", "write a query for the database analyst to run, and record it in the journal", [writes, step])
    one.add_argument("what", choices=("tables", "codes", "counts", "values", "probe"))
    one.add_argument("--key", help="for codes, the column of a kind, such as role_reading.kind")
    one.add_argument("--year")
    one.add_argument("--about", help="for values and probe, the column or table that the query is written for")
    one.add_argument("--table")
    one.add_argument("--column")
    one.add_argument("--name", help="for counts, print only the count of this name")
    one = add("paste", "read the result of a query, as the results grid copies it, and record it in the journal", [writes])
    one.add_argument("what", choices=tuple(READINGS))
    one.add_argument("result", help="a file holding the result as it was copied, with its headers")
    one.add_argument("--key")
    one.add_argument("--year")
    one.add_argument("--name", help="for a count or values, the name of the query")
    one.add_argument("--about", help="for a probe, the column or table that the query was written for")
    one = add("invented-run", "run a query on the invented hospital, and read its result as a paste would be read", [writes])
    one.add_argument("query", help="the name of the query, as the command query recorded it")
    one.add_argument("read", choices=tuple(READINGS))
    one.add_argument("--hospital", default=str(INVENTED_HOSPITAL), help="the folder of the invented hospital's tables")
    one.add_argument("--catalogue", default=str(INVENTED_CATALOGUE), help="the invented catalogue")
    one.add_argument("--key")
    one.add_argument("--year")
    one.add_argument("--name")
    one.add_argument("--about")
    one = add("answer", "record a person's answer to a proposal: yes, no with a replacement, or not sure", [writes, actor])
    one.add_argument("about", help="the proposal, such as role_patient.birth_date or role_patient rows")
    one.add_argument("answer", help="yes, no, or not-sure")
    one.add_argument("--replacement", help="with no, the column that holds it, as TABLE.COLUMN")
    one.add_argument("--note")
    one = add("translate-codes", "record the local codes that a person chose for each kind of a column", [writes, actor])
    one.add_argument("key", help="the column, such as role_reading.kind")
    one.add_argument("codes", nargs="*", metavar="CODE=KIND")
    one = add("translate-concepts", "record the translation of a mapping view's local codes into standard concepts",
              [writes, actor])
    one.add_argument("mapping", help="the mapping view")
    one.add_argument("rows", help="a CSV or tab-separated file with the headings code, description, concept_id, status "
                                  "and provenance")
    one = add("add-pathway", "add a further pathway to a part that records events: another table with its kind of record",
              [writes, actor])
    one.add_argument("part", help="the part, such as role_drug")
    one.add_argument("table", help="the table whose rows are the further pathway")
    one.add_argument("kind", help="the kind of record, one of the source kinds, such as order or administration")
    one.add_argument("--name", help="the pathway's name, in lower-case letters; by default, its kind of record")
    one = add("source-kind", "record the kind of record of a pathway, named as role_x or role_x@name", [writes, actor])
    one.add_argument("part", help="the pathway, such as role_drug or role_drug@order")
    one.add_argument("kind", help="the kind of record, one of the source kinds")
    one = add("judge", "record the clinician's judgement of a count", [writes, actor])
    one.add_argument("count", help="the count's name, such as coverage_by_year")
    one.add_argument("looks_right", help="yes or no")
    one.add_argument("--note")
    one = add("correction-preview", "say what a change means and show the SQL of the part it changes", [schema])
    one.add_argument("correction", help="a JSON file holding one correction, as the page's form gives it")
    one = add("correction-check", "try a change on made-up rows and report what it breaks or mends; nothing is saved",
              [schema])
    one.add_argument("correction")
    one = add("correction-keep", "keep a change once it has been tried on made-up rows", [writes, actor])
    one.add_argument("correction")
    one.add_argument("--although", action="store_true", help="keep a change that fails its test, with --reason")
    one.add_argument("--reason")
    one = add("correction-discard", "discard a change after its trial, which leaves the hospital schema as it was", [schema])
    one.add_argument("correction")
    add("test", "run the test on made-up rows of the hospital schema as it stands, and record it", [writes])
    add("save", "save the hospital schema, running first the test on made-up rows that it owes", [writes])
    one = add("show", "open a saved hospital schema and show its readiness, its stale evidence and its journal", [schema])
    one.add_argument("--json", action="store_true", help="give the page's whole view, the readiness, the stale evidence, "
                                                         "the journal and the files as JSON")
    add("check", "propose the hospital schema again from its dictionary and its answers, and say whether it is the same",
        [schema])
    one = add("compare", "compare a new result of a query with the result that the hospital schema holds", [schema])
    one.add_argument("name")
    one.add_argument("result")
    one = add("lookup", "list the dictionary's tables, a table's columns, or the joins from a table", [schema])
    one.add_argument("what", choices=("tables", "columns", "joins"))
    one.add_argument("table", nargs="?")
    one = add("scoreboard", "say how the proposals fared, as counts only", [schema])
    one.add_argument("--write", action="store_true", help="write the counts alone beside the saved schema as well")
    one = add("import-evidence", "import the result of an evidence request into a saved hospital schema", [writes])
    one.add_argument("request")
    one.add_argument("results", nargs="+")
    one.add_argument("--id", default=None, help="the request's request_id or id, where REQUEST.json is a whole report")
    one.add_argument("--actor", default=None, help="the name of the person who ran the query and returned its result")
    one.add_argument("--provenance", default=None, help=f"one of: {', '.join(evidence.PROVENANCES)}")
    one = add("walk", "make a hospital schema from the calls that the page makes, through these commands, and save it",
              [common])
    one.add_argument("calls", help="a JSON file of {\"version\", \"calls\": [{\"call\", \"request\"}]}, whose files are "
                                   "named relative to its own folder")
    one.add_argument("--out", default=None, help="the folder for the saved file (by default, the folder of CALLS)")
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    version = args.tool_version if args.tool_version is not None else _installed()
    try:
        globals()["cmd_" + args.command.replace("-", "_")](args, version)
    except Refused as refusal:
        print(str(refusal), file=sys.stderr)
        return refusal.code
    except DescribeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
