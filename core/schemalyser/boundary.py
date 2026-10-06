"""Does everything that reads confidential input, in one run, from two folders into one output folder.

Whatever reads the data team's requests or the hospital's catalogue runs at the boundary: the
analyser, the check script, the register of open questions and the checklist for each target query.
This module runs all four from a state folder and a requests folder, and writes one output folder
that a person reads before committing any of it.

    python -m schemalyser.boundary --state STATE --requests REQUESTS --out OUT
                                   [--state-commit SHA] [--requests-commit SHA]

The state folder holds

    catalogue.csv        required: the catalogue export, in the layout of the catalogue query
    site-rules.json      optional: the site rules
    checks.csv           optional: the results of the check script
    conversion/          optional: a conversion folder, as convert.py reads it
    targets/*.sql        optional: the target queries, one SELECT against the OMOP tables in each
    core-profile.csv     optional: the saved result of the core profile script, or of the general profile
    facts.json           optional: facts that a person confirmed, which settle items as SQL does (facts.py)
    sql_evidence.json    optional: what the team's SQL showed in earlier runs, by catalogue names, counts and
                         dates only, which settles items as the request files did (sql_evidence.py)
    boundary.json        optional: {"includeSpans": true, "includeFanout": true, "writeSourceDraft": false}

and the requests folder is any tree of .sql files, such as the analytics team's repository checked
out. A request file that is a link, or larger than MAX_REQUEST_BYTES, is left out and counted. A
file or folder of either folder that a link leads outside that folder is left out and counted, and
the run reads a copy of the state folder that holds only what lies inside it.

The output folder must be empty or new, and receives

    summary.md           what was read, what could not be read, the open questions and the target queries
    provenance.json      the commits that were read, the options, and a checksum of every other output
    inventory/           the inventory pack, built from the requests alone and without the check results
    check_script.sql     the check script, which also plans checks for the columns that the conversion maps
    sql_evidence.json    what the request files showed, with what earlier runs saw and these do not, to keep in the state
    questions.csv        the register of open questions, with questions-summary.txt (needs a conversion)
    targets/NAME/        checklist.csv, readiness.txt and queries.sql for each target query (needs a conversion),
                         and source_draft.sql where boundary.json asks for it, for use inside the hospital only

Every name in these files comes from the catalogue, the site rules, the OMOP field list, the
conversion's own files and vocabularies, the names of the target files, or the fixed wording below.
No file holds text from a request or a value from the check results. The command never builds the
sandbox, never runs a conversion and never opens a network connection.

The exit code is 0 when every output was produced, 1 when a target query could not be read (the
other outputs are still written), and 2 when the state folder, the requests folder, the output
folder or a commit cannot be used, in which case nothing is written. Any other error ends the run
with 3 and one fixed sentence, because the text of an error could quote a request. A target query that is not yet
ready is not an error.

This module imports only the analyser's own modules at the top, so that it can be imported without
duckdb. The register and the checklists are imported when they are needed, and they bring in duckdb,
because harness.py and convert.py import it at the top. The boundary never calls duckdb.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from .catalogue import Catalogue, CatalogueError
from .extract import decode
from .rules import SiteRules

# The layout of the state folder.
CATALOGUE = "catalogue.csv"
RULES = "site-rules.json"
CHECKS = "checks.csv"
CONVERSION = "conversion"
TARGETS = "targets"
PROFILE = "core-profile.csv"
FACTS = "facts.json"
SETTINGS = "audit.json"
EVIDENCE = "sql_evidence.json"
OPTIONS = "boundary.json"
# The options that boundary.json may set, with their defaults. The spans and fanout checks are on by
# default; boundary.json can turn either of them off.
# writeSourceDraft writes each target query's source draft, which names the source tables and holds local
# codes, so it is off unless the state asks for it.
DEFAULT_OPTIONS = {"includeSpans": True, "includeFanout": True, "writeSourceDraft": False, "writeSpecification": False}
# A request file larger than this is left out, as the page leaves out a file fetched from GitHub.
MAX_REQUEST_BYTES = 2 * 1024 * 1024
# A target query's file name, which also names its output folder.
TARGET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.sql")
COMMIT = re.compile(r"[0-9a-fA-F]{7,64}")

# All of the wording that a person reads, in one place. It is a draft until the clinical lead approves it.
WORDING = {
    # summary.md.
    "title": "Summary of the boundary run",
    "intro": "Schemalyser read the state folder and the requests folder inside the boundary and wrote the files in this folder. "
             "Every name in them comes from the catalogue, the site rules, the OMOP field list, the conversion's own files, "
             "the names of the target queries or Schemalyser's fixed wording. None of them holds text from a request or a value "
             "from the check results.",
    "review": "The person who commits any of these files to the state repository should read it first.",
    "state_commit": "Schemalyser read the state repository at commit {commit}.",
    "state_commit_none": "No commit was given for the state repository, so these files cannot be traced to a particular version of it.",
    "requests_commit": "Schemalyser read the requests repository at commit {commit}.",
    "requests_commit_none": "No commit was given for the requests repository, so these files cannot be traced to a particular version of it.",
    "tool": "The Schemalyser code that wrote them has the fingerprint {digest}.",

    "h_read": "What was read",
    "read_header": ("Input", "Count"),
    "read_rows": {
        "files": "Request files read",
        "not_fully": "Request files that could not be read in full, because each hides some of its SQL from Schemalyser",
        "left_out": "Request files left out, because each was a link or larger than 2 MB",
        "tables": "Tables accepted from the catalogue",
        "columns": "Columns accepted from the catalogue",
        "held_back": "Tables that the site rules hold back as built locally",
        "steps": "Steps of the conversion",
        "targets": "Target queries",
    },
    "rules_present": "The state folder holds site rules, and Schemalyser used them throughout.",
    "rules_absent": "The state folder holds no site rules, so Schemalyser used none.",
    "checks_present": "The state folder holds check results. Schemalyser used them to work out how far each question is answered, "
                      "and left them out of the inventory pack.",
    "checks_absent": "The state folder holds no check results, so every question that they would answer remains open.",
    "profile_present": "The state folder holds a core profile, and Schemalyser used it for the questions about the core.",
    "profile_absent": "The state folder holds no core profile, so the questions about the core remain open.",
    "conversion_absent": "The state folder holds no conversion, so Schemalyser has not written the register of open questions "
                         "or a checklist for any target query.",
    "options_spans": "The check script includes the spans checks.",
    "options_fanout": "The check script includes the fanout checks.",
    "options_spans_off": "The check script leaves out the spans checks, because boundary.json turns them off.",
    "options_fanout_off": "The check script leaves out the fanout checks, because boundary.json turns them off.",
    "options_draft": "Each target query's folder holds source_draft.sql, the question as one query over the source tables, "
                     "because boundary.json asks for it. It names the source tables and holds local codes, so it is for use "
                     "inside the hospital only.",

    "h_unread": "What could not be read",
    "unread_header": ("Part of the requests", "Count"),
    "unread_none": "Schemalyser was able to read every part of every request file.",
    "left_out": "Schemalyser left out {count}, because each was a link or larger than 2 MB.",
    "unusable_paths": "Schemalyser left out {count}, because the path of each was too long, was nested too deeply or "
                      "clashed with the path of another file.",
    "state_left_out": "Schemalyser left out {count} in the state folder, because each was a link that led outside that folder.",
    "conversion_unread": "Schemalyser could not read {count} of the conversion as one SELECT, so the check script does not plan "
                         "checks for the columns that those steps map.",

    "h_questions": "The open questions",
    "questions_total": "The register in questions.csv holds {total} questions. Of those, {answered} are answered, {partly} are "
                       "partly answered and {open} are open.",
    "status_header": ("Status", "Questions"),
    "status_names": {"answered": "Answered", "partly": "Partly answered", "open": "Open"},
    "who_header": ("Who must act", "Questions not yet answered"),
    "who_names": {"analytics team": "The analytics team", "central OMOP team": "The central OMOP team",
                  "clinical lead": "The clinical lead"},
    "who_none": "Every question in the register is answered, so nobody needs to act on it.",
    "questions_none": "Schemalyser has not written the register of open questions, because the state folder holds no conversion.",

    "h_targets": "The target queries",
    "targets_none": "The state folder holds no target queries.",
    "targets_no_conversion": "Schemalyser has not written a checklist for any target query, because the state folder holds no conversion.",
    "targets_header": ("Target query", "Blocking items", "Open", "Ready"),
    "ready_yes": "yes",
    "ready_no": "no",
    "target_ready": "The shadow database is ready for {name}, because none of its {total} blocking items is open.",
    "target_not_ready_one": "The shadow database is not yet ready for {name}, because 1 of its {total} blocking items remains open.",
    "target_not_ready": "The shadow database is not yet ready for {name}, because {open} of its {total} blocking items remain open.",
    "target_no_steps": "No step of the conversion writes rows that {name} keeps, so the shadow database cannot simulate it.",
    "target_open_items": "The open blocking items are these, with who must act on each:",
    "items_header": ("Item", "Who must act", "Through"),
    "target_details": "The readiness statement in full",
    "target_refused": "Schemalyser could not read the target query {name}, so it has written no checklist for it. "
                      "If the query is meant to be used, make sure that it is a single SELECT that reads only the OMOP tables.",
    "target_names_refused": "Schemalyser left out {count}, because each file name held characters other than letters, digits, "
                            "full stops, hyphens and underscores.",

    "h_files": "The files in this folder",
    "file_inventory": "inventory/ holds the inventory pack, which describes how the requests use the database. "
                      "Schemalyser built it from the requests alone, without the conversion or the check results.",
    "file_check_script": "check_script.sql is the whole check script, for an analytics team that prefers to run every check "
                         "in one go. It also plans checks for the columns that the conversion maps.",
    "file_questions": "questions.csv is the register of open questions, and questions-summary.txt gives its counts.",
    "file_targets": "targets/ holds a folder for each target query, with its checklist, its readiness statement and "
                    "queries.sql, which holds the plain queries that its checklist offers now, one for each check that "
                    "its open items need.",
    "file_evidence": "sql_evidence.json records which tables, columns, joins and filters the request files showed, by name, "
                     "with the number of files that showed each and the date, and keeps what earlier runs saw. "
                     "If you place it in the state folder, a later run counts that evidence even without the request files.",
    "file_provenance": "provenance.json names the commits that were read and gives a checksum of every other file in this folder.",

    # Messages that the command prints.
    "done": "Schemalyser has written the boundary outputs. Please read summary.md first.",
    "done_with_refusals": "Schemalyser has written the boundary outputs, but it could not read {count}. summary.md names each one.",
    "no_state": "Schemalyser could not find the state folder, so it has not written anything.",
    "no_requests": "Schemalyser could not find the requests folder, so it has not written anything.",
    "out_not_empty": "The output folder already holds files, so Schemalyser has not written anything. "
                     "If you run the command again, give it an empty or new folder, so that every file in it comes from one run.",
    "out_not_folder": "The output path is not a folder, so Schemalyser has not written anything.",
    "bad_commit": "The {which} commit must be a hexadecimal commit identifier of 7 to 64 characters, "
                  "so Schemalyser has not written anything.",
    "no_catalogue": "The state folder does not hold catalogue.csv, so Schemalyser has not written anything.",
    "empty_catalogue": "Schemalyser could not accept any table from catalogue.csv, so it has not written anything. "
                       "If the file is the catalogue export, check that it has the layout of the catalogue query.",
    "bad_rules": "Schemalyser could not read site-rules.json, so it has not written anything. "
                 "If the file is meant to hold the site rules, check that it is valid JSON and uses only the known keys.",
    "bad_options": "Schemalyser could not read boundary.json, so it has not written anything. "
                   "The file may set only includeSpans, includeFanout, writeSourceDraft and writeSpecification, each to true or false.",
    "bad_checks": "Schemalyser could not read checks.csv, because it does not have the layout of the check script's results, "
                  "so it has not written anything.",
    "bad_conversion": "Schemalyser could not read the conversion folder, so it has not written anything. "
                      "If the folder is meant to hold a conversion, check that conversion.json lists each step and that each "
                      "step's file is present.",
    "bad_profile": "Schemalyser could not read core-profile.csv as the result of a profile script, so it has not written anything.",
    "bad_facts": "Schemalyser could not read facts.json, because a fact in it names something that the catalogue does not hold "
                 "or does not have the expected form, so it has not written anything.",
    "bad_evidence": "Schemalyser could not read sql_evidence.json, because a finding in it names something that the catalogue "
                    "does not hold or does not have the expected form, so it has not written anything.",
    "bad_settings": "Schemalyser could not read audit.json, because a date in it is not written as yyyy-mm-dd, the period ends "
                    "before it starts, or a kind is not a concept number, so it has not written anything.",
    "unexpected": "Schemalyser met an unexpected error and has stopped, so the output folder may be incomplete. "
                  "Schemalyser does not show the error itself, because its text could quote an input.",

    # Counted nouns: the singular, the plural, and the words for none.
    "nouns": {"request file": ("request file", "request files", "no request files"),
              "step": ("step", "steps", "no steps"),
              "target query": ("target query", "target queries", "no target queries"),
              "file": ("file or folder", "files or folders", "no files or folders"),
              "chosen file": ("file", "files", "no files")},
}


class BoundaryError(ValueError):
    """The state folder, the requests folder, the output folder or a commit cannot be used."""


def _n(count, noun):
    one, many, none = WORDING["nouns"][noun]
    return none if count == 0 else f"{count:,} {one if count == 1 else many}"


# Reading the inputs.

def _commit(value, which):
    if value is None or value == "":
        return None
    if not COMMIT.fullmatch(value):
        raise BoundaryError(WORDING["bad_commit"].format(which=which))
    return value.lower()


def _outside(path, root):
    """Whether a path's resolved location falls outside a folder, as it can through a link."""
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return True
    return False


def request_files(folder):
    """The .sql files under a folder, as (kept, left out). A link, or a file over MAX_REQUEST_BYTES, is left out.

    The walk does not follow links to folders, and it passes over the .git folder of a checkout. A
    link to a folder outside the requests folder is counted as left out.
    """
    folder = Path(folder)
    kept, left_out = [], 0
    for root, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        left_out += sum(1 for d in dirs if (Path(root) / d).is_symlink() and _outside(Path(root) / d, folder))
        for name in names:
            if not name.lower().endswith(".sql"):
                continue
            path = Path(root) / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REQUEST_BYTES:
                left_out += 1
                continue
            kept.append(path)
    return sorted(kept, key=lambda p: p.relative_to(folder).as_posix()), left_out


def state_copy(state, into):
    """Copies the state folder into another folder, leaving out every file or folder whose resolved location is outside it.

    The rest of the run reads only the copy, so that no link in the state folder can lead it to a file
    elsewhere. Returns the number of files and folders left out.
    """
    state, into = Path(state), Path(into)
    left_out = 0
    for root, dirs, names in os.walk(state, followlinks=False):
        here = Path(root)
        kept = []
        for name in sorted(dirs):
            if (here / name).is_symlink():
                # A link to a folder is never followed. One that leads outside the state folder is counted.
                left_out += _outside(here / name, state)
            else:
                kept.append(name)
        dirs[:] = kept
        target = into / here.relative_to(state)
        target.mkdir(parents=True, exist_ok=True)
        for name in sorted(names):
            path = here / name
            if _outside(path, state) or not path.is_file():
                left_out += 1
                continue
            shutil.copyfile(path, target / name)
    return left_out


def _options(path):
    if not path.exists():
        return dict(DEFAULT_OPTIONS)
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (ValueError, OSError):
        raise BoundaryError(WORDING["bad_options"]) from None
    if not isinstance(data, dict) or set(data) - set(DEFAULT_OPTIONS) \
            or not all(isinstance(value, bool) for value in data.values()):
        raise BoundaryError(WORDING["bad_options"])
    return {**DEFAULT_OPTIONS, **data}


def _text(path):
    return decode(path.read_bytes()) if path.exists() else None


def _today():
    """The date that sql_evidence.json gives to what this run's request files showed."""
    return datetime.date.today().isoformat()


def _modules():
    """The modules that bring in duckdb, imported only when the register and the checklists are needed."""
    from . import harness, questions, target
    from . import profile as core_profile
    return harness, questions, target, core_profile


def _world(harness, catalogue_path, requests_path, rules_path, files):
    """A harness world over the state's catalogue and rules and the requests that were kept."""
    class Folders(harness.World):
        def request_files(self):
            return list(files)

    return Folders(catalogue_path, requests_path, rules_path)


def tool_digest():
    """A checksum of the schemalyser package as it ran, so that an output names the code that wrote it."""
    package = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                       and p.suffix not in (".pyc", ".pyo") and not p.name.startswith(".")):
        digest.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


# The run.

def produce(state, requests, state_commit=None, requests_commit=None, refused=None, draft_when=None):
    """Everything that the boundary writes, as ({relative path: text}, facts for the summary).

    Raises BoundaryError, before anything is written, when an input cannot be used. The run reads a
    copy of the state folder that leaves out every link that leads outside it. refused, which the page
    gives, counts the files of each kind, "state" and "requests", that it could not write, so that the
    summary can count them as left out. draft_when, which the page gives, decides from a target's checklist rows and
    settings whether its source draft is composed now; the page shows the draft only once nothing remains and a study
    period is set, so it is not composed before then. Without it, every draft is composed.
    """
    state, requests = Path(state), Path(requests)
    commits = {"state": _commit(state_commit, "state"), "requests": _commit(requests_commit, "requests")}
    if not state.is_dir():
        raise BoundaryError(WORDING["no_state"])
    if not requests.is_dir():
        raise BoundaryError(WORDING["no_requests"])
    with tempfile.TemporaryDirectory(prefix="schemalyser-state-") as copy:
        left_out = state_copy(state, Path(copy) / "state")
        outputs, facts = _produce(Path(copy) / "state", requests, commits, left_out, draft_when)
    unusable = sum((refused or {}).values())
    if unusable:
        facts["unusable_paths"] = unusable
        outputs["summary.md"] = summary(facts)
    return outputs, facts


def _produce(state, requests, commits, state_left_out, draft_when=None):
    if not (state / CATALOGUE).is_file():
        raise BoundaryError(WORDING["no_catalogue"])
    options = _options(state / OPTIONS)

    rules_path = state / RULES if (state / RULES).is_file() else None
    rules_text = rules_path.read_text(encoding="utf-8") if rules_path else None
    if rules_text is not None:
        try:
            if not isinstance(json.loads(rules_text), dict):
                raise ValueError
            SiteRules.from_json(rules_text)
        except (ValueError, TypeError, AttributeError):
            raise BoundaryError(WORDING["bad_rules"]) from None
    checks_text = _text(state / CHECKS)
    profile_text = _text(state / PROFILE)
    facts_text = _text(state / FACTS)
    settings_text = _text(state / SETTINGS)
    evidence_text = _text(state / EVIDENCE)
    conversion = state / CONVERSION if (state / CONVERSION).is_dir() else None

    harness, questions, target, core_profile = _modules()
    from . import checks as checking
    from . import convert

    files, left_out = request_files(requests)
    world = _world(harness, state / CATALOGUE, requests, rules_path, files)
    try:
        if not list(Catalogue.from_csv(world.catalogue_text()).tables()):
            raise CatalogueError
    except CatalogueError:
        raise BoundaryError(WORDING["empty_catalogue"]) from None
    from . import sql_evidence
    try:
        saved = sql_evidence.Saved.from_json(evidence_text, Catalogue.from_csv(world.catalogue_text())) \
            if evidence_text is not None else None
    except sql_evidence.EvidenceError:
        raise BoundaryError(WORDING["bad_evidence"]) from None

    # The conversion is read before the profile, because the profile's reader checks its joins against the conversion.
    # A step that reads what the catalogue does not hold gives way to an alternative that reads only what it does. The
    # run reads a copy of the state, so the choice is written into the copy and every later reader follows it.
    steps, unread = [], []
    if conversion is not None:
        from . import routes
        try:
            choices = routes.choose(conversion, Catalogue.from_csv(world.catalogue_text()))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            choices = []
        if choices:
            routes.apply(conversion, choices)
        # A match that a person said is wrong, naming the columns that do match, is changed in the copy of the steps.
        if facts_text:
            from . import facts as facts_module
            try:
                held = facts_module.Facts.from_json(facts_text, Catalogue.from_csv(world.catalogue_text()))
            except facts_module.FactsError:
                raise BoundaryError(WORDING["bad_facts"]) from None
            routes.rejoin(conversion, held)
        try:
            steps = questions._steps(conversion)
            if not steps or not all(isinstance(step.get("table"), str) for step, _ in steps):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise BoundaryError(WORDING["bad_conversion"]) from None
    first = world.analysis()
    try:
        planner = world.analysis(checks_text) if checks_text is not None else world.analysis()
    except checking.ChecksError:
        raise BoundaryError(WORDING["bad_checks"]) from None
    if profile_text is not None:
        try:
            core_profile.read(profile_text, conversion)
        except (core_profile.ProfileError, ValueError, KeyError, IndexError, OSError):
            raise BoundaryError(WORDING["bad_profile"]) from None

    # The check script, planned from the requests and the conversion together, as convert.run and the
    # register plan it, so that each source column that a step maps has its values listed.
    if steps:
        planner.add_request("conversion", convert.as_request(
            [(step["table"], sql) for step, sql in steps], questions._definitions(rules_text), unread))
    script = planner.check_script(include_spans=options["includeSpans"], include_fanout=options["includeFanout"])

    # The inventory pack, from the requests alone and without the check results, so that it holds no checked value.
    pack = first.pack()
    outputs = {f"inventory/{name}": text for name, text in pack.items()}
    outputs["check_script.sql"] = script
    # What the request files showed, with what earlier runs saw and these do not, for the state folder.
    outputs[EVIDENCE] = target._Evidence(world, first.catalogue, first.held_back, saved).gathered(
        first.catalogue, _today()).to_json()

    register, targets, refused, named_out = None, [], [], 0
    if conversion is not None:
        try:
            register = questions.questions(world, conversion, checks_text, profile_text)
        except questions.QuestionsError as error:
            raise BoundaryError(WORDING["bad_checks"] if "check results" in str(error) else WORDING["bad_profile"]) from None
        outputs["questions.csv"] = questions.to_csv(register)
        outputs["questions-summary.txt"] = questions.summary(register)

    target_files = sorted((state / TARGETS).glob("*.sql")) if (state / TARGETS).is_dir() else []
    for path in target_files:
        if not TARGET_NAME.fullmatch(path.name):
            named_out += 1
            continue
        if conversion is None:
            continue
        name = path.name[:-len(".sql")]
        try:
            settings = target.read_settings(settings_text)
        except target.TargetError:
            raise BoundaryError(WORDING["bad_settings"]) from None
        try:
            target_text = target.with_settings(decode(path.read_bytes()), settings, conversion)
            rows, traced = target.checklist(world, conversion, target_text, checks_text, profile_text,
                                            facts_text, name, evidence_text,
                                            draft=draft_when is None or options["writeSourceDraft"])
            if traced.get("draft_later") and draft_when(rows, settings):
                traced["draft"], traced["draft_restructured"], traced["draft_reason"] = traced["draft_later"]()
        except target.TargetError as error:
            if "facts.json" in str(error):
                raise BoundaryError(WORDING["bad_facts"]) from None
            if "sql_evidence.json" in str(error):
                raise BoundaryError(WORDING["bad_evidence"]) from None
            refused.append(name)
            continue
        outputs[f"targets/{name}/checklist.csv"] = target.to_csv(rows)
        outputs[f"targets/{name}/readiness.txt"] = target.readiness(rows, traced)
        outputs[f"targets/{name}/queries.sql"] = target.queries_file(name, traced["queries"])
        if options["writeSourceDraft"] and traced.get("draft"):
            outputs[f"targets/{name}/source_draft.sql"] = traced["draft"]
        try:
            spec = target.specification(conversion, target_text, rows, traced, first.catalogue, name, settings) \
                if traced["steps"] else ""
        except Exception:   # noqa: BLE001 - a specification that cannot be written is left out, and the checklist stands
            spec = ""
        if options["writeSpecification"] and spec:
            outputs[f"targets/{name}/specification.txt"] = spec
        # The optional count of how often each chosen code is charted, once codes are chosen and the study period has an end.
        charted = None
        if traced["steps"] and settings.get("to"):
            from . import facts as facts_module
            try:
                held = facts_module.Facts.from_json(facts_text, first.catalogue) if facts_text else facts_module.Facts()
                charted = target.charted_count(conversion, decode(path.read_bytes()), first.catalogue, settings, held, name)
                kept = held.charted()
                if charted is not None:
                    charted["kept"] = bool(kept and (kept["from"], kept["to"], kept["codes"]) ==
                                           (charted["from"], charted["to"], sorted(charted["codes"])))
            except (facts_module.FactsError, target.TargetError):
                charted = None
        # The list of what is charted on the cohort in one year, once the count by year has been seen, and the open points that
        # an empty cohort, an empty list or a count of none for the chosen codes raise.
        listed = None
        if traced["steps"]:
            from . import charted as charting
            from . import facts as facts_module
            try:
                held = facts_module.Facts.from_json(facts_text, first.catalogue) if facts_text else facts_module.Facts()
                listed = charting.attach(rows, traced, conversion, target_text, first.catalogue, first.rules, held, settings,
                                         planner.checks, name)
            except (facts_module.FactsError, target.TargetError):
                listed = None
        # The reference query reaches the readings, so the page offers it to be run only as a script that is safe by
        # construction; where it cannot be one, the page shows it as a reference only and says why.
        draft_script = None
        if traced.get("draft"):
            from . import charted as charting
            from . import facts as facts_module
            try:
                held = facts_module.Facts.from_json(facts_text, first.catalogue) if facts_text else facts_module.Facts()
                draft_script = charting.reference_script(conversion, first.catalogue, traced["draft"], target_text, settings,
                                                         held, name) if traced.get("draft_restructured") else \
                    {"sql": "", "worst": "", "withheld": charting.scripts.WORDING["unsafe"].format(
                        reason="it could not be written to start from the cohort"
                        + (f": {traced['draft_reason']}" if traced.get("draft_reason") else ""))}
            except (facts_module.FactsError, target.TargetError):
                draft_script = None
        targets.append({"name": name, "rows": rows, "counts": target.counts(rows), "steps": bool(traced["steps"]),
                        "listed": listed,
                        "charted": charted,
                        "readiness": outputs[f"targets/{name}/readiness.txt"], "queries": traced["queries"],
                        "draft": traced.get("draft"), "draft_restructured": traced.get("draft_restructured", False),
                        "draft_script": draft_script,
                        "draft_pending": bool(traced.get("draft_later")) and not traced.get("draft"),
                        "questions": traced.get("questions") or "", "specification": spec,
                        "routes": traced.get("routes") or [], "settings": settings, "year_count": traced.get("year_count"),
                        "kinds": target.kinds_offered(conversion),
                        "kinds_cost": target.kinds_cost(traced, first.catalogue),
                        "stages": {"source": target.stage_counts(rows, "source"), "release": target.stage_counts(rows)}})

    facts = {
        "commits": commits, "options": options, "summary": first.summary,
        "left_out": left_out, "state_left_out": state_left_out, "unread_steps": len(dict.fromkeys(unread)), "steps": len(steps),
        "tables": len(list(first.catalogue.tables())),
        "columns": sum(len(t.columns) for t in first.catalogue.tables()),
        "held_back": len(first.held_back),
        "present": {"rules": rules_path is not None, "checks": checks_text is not None,
                    "conversion": conversion is not None, "profile": profile_text is not None,
                    "evidence": evidence_text is not None},
        "register": register, "targets": targets, "refused": refused, "named_out": named_out,
        "target_files": len(target_files), "who": questions.WHO, "statuses": questions.STATUSES,
        "tool": tool_digest(),
    }
    outputs["summary.md"] = summary(facts)
    return outputs, facts


# summary.md.

def _cell(text):
    """Text made safe for a cell or a line of Markdown: the characters that Markdown or HTML would read are escaped.

    An underscore inside a word, as in a table name, is left alone, because GitHub does not read it as emphasis.
    """
    text = re.sub(r"([\\`*{}\[\]<>|#])", r"\\\1", str(text)).replace("\n", " ")
    return re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", text)


def _table(header, rows):
    lines = ["| " + " | ".join(_cell(h) for h in header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def verdict(t):
    """One sentence on whether the shadow database is ready for a target query, from its entry in facts["targets"]."""
    w, c, name = WORDING, t["counts"], t["name"]
    if not t["steps"]:
        return w["target_no_steps"].format(name=name)
    if c["open"] == 0:
        return w["target_ready"].format(name=name, total=c["total"])
    if c["open"] == 1:
        return w["target_not_ready_one"].format(name=name, total=c["total"])
    return w["target_not_ready"].format(name=name, total=c["total"], open=c["open"])


def summary(facts):
    """The page that a person reads first, in Markdown."""
    w = WORDING
    lines = [f"# {w['title']}", "", _cell(w["intro"]), "", _cell(w["review"]), ""]
    for which in ("state", "requests"):
        commit = facts["commits"][which]
        lines.append(_cell(w[f"{which}_commit"].format(commit=commit) if commit else w[f"{which}_commit_none"]))
    lines.append(_cell(w["tool"].format(digest=facts["tool"])))
    lines.append("")

    s = facts["summary"]
    counts = {"files": s["files"], "not_fully": s["notFullyRead"], "left_out": facts["left_out"],
              "tables": facts["tables"], "columns": facts["columns"], "held_back": facts["held_back"],
              "steps": facts["steps"], "targets": facts["target_files"]}
    lines += [f"## {w['h_read']}", ""]
    lines += _table(w["read_header"], [(w["read_rows"][k], f"{v:,}") for k, v in counts.items()])
    lines.append("")
    present = facts["present"]
    options = facts["options"]
    for key in ("rules", "checks", "profile"):
        lines += [_cell(w[f"{key}_present" if present[key] else f"{key}_absent"]), ""]
    if not present["conversion"]:
        lines += [_cell(w["conversion_absent"]), ""]
    lines += [_cell(w["options_spans" if options["includeSpans"] else "options_spans_off"]), ""]
    lines += [_cell(w["options_fanout" if options["includeFanout"] else "options_fanout_off"]), ""]
    if options.get("writeSourceDraft"):
        lines += [_cell(w["options_draft"]), ""]

    lines += [f"## {w['h_unread']}", ""]
    unread = [(label.rstrip(":"), f"{count:,}") for label, count in s["unread"]]
    if unread:
        lines += _table(w["unread_header"], unread) + [""]
    else:
        lines += [_cell(w["unread_none"]), ""]
    if facts["left_out"]:
        lines += [_cell(w["left_out"].format(count=_n(facts["left_out"], "request file"))), ""]
    if facts.get("unusable_paths"):
        lines += [_cell(w["unusable_paths"].format(count=_n(facts["unusable_paths"], "chosen file"))), ""]
    if facts.get("state_left_out"):
        lines += [_cell(w["state_left_out"].format(count=_n(facts["state_left_out"], "file"))), ""]
    if facts["unread_steps"]:
        lines += [_cell(w["conversion_unread"].format(count=_n(facts["unread_steps"], "step"))), ""]

    lines += [f"## {w['h_questions']}", ""]
    register = facts["register"]
    if register is None:
        lines += [_cell(w["questions_none"]), ""]
    else:
        status = Counter(row["status"] for row in register)
        lines += [_cell(w["questions_total"].format(total=len(register), **{k: status[k] for k in facts["statuses"]})), ""]
        lines += _table(w["status_header"], [(w["status_names"][k], status[k]) for k in facts["statuses"]]) + [""]
        waiting = Counter(row["who"] for row in register if row["status"] != "answered")
        if waiting:
            lines += _table(w["who_header"], [(w["who_names"].get(who, who), waiting[who])
                                              for who in facts["who"] if waiting[who]]) + [""]
        else:
            lines += [_cell(w["who_none"]), ""]

    lines += [f"## {w['h_targets']}", ""]
    if not facts["target_files"]:
        lines += [_cell(w["targets_none"]), ""]
    elif not present["conversion"]:
        lines += [_cell(w["targets_no_conversion"]), ""]
    if facts["targets"]:
        lines += _table(w["targets_header"], [
            (t["name"], t["counts"]["total"], t["counts"]["open"],
             w["ready_yes"] if t["steps"] and not t["counts"]["open"] else w["ready_no"]) for t in facts["targets"]]) + [""]
    for name in facts["refused"]:
        lines += [_cell(w["target_refused"].format(name=name)), ""]
    if facts["named_out"]:
        lines += [_cell(w["target_names_refused"].format(count=_n(facts["named_out"], "target query"))), ""]
    for t in facts["targets"]:
        lines += [f"### {_cell(t['name'])}", ""]
        lines += [_cell(verdict(t)), ""]
        open_items = [row for row in t["rows"] if row["blocking"] == "yes" and row["status"] == "open"]
        if open_items:
            lines += [_cell(w["target_open_items"]), ""]
            lines += _table(w["items_header"], [(row["question"], w["who_names"].get(row["who"], row["who"]), row["mechanism"])
                                                for row in open_items]) + [""]
        paragraphs = [line for line in t["readiness"].splitlines()[1:] if line.strip()]
        lines += ["<details>", f"<summary>{_cell(w['target_details'])}</summary>", ""]
        for paragraph in paragraphs:
            lines += [_cell(paragraph), ""]
        lines += ["</details>", ""]

    lines += [f"## {w['h_files']}", ""]
    lines += [f"- {_cell(w['file_inventory'])}", f"- {_cell(w['file_check_script'])}", f"- {_cell(w['file_evidence'])}"]
    if register is not None:
        lines.append(f"- {_cell(w['file_questions'])}")
    if facts["targets"]:
        lines.append(f"- {_cell(w['file_targets'])}")
    lines.append(f"- {_cell(w['file_provenance'])}")
    return "\n".join(lines) + "\n"


def provenance(outputs, facts):
    """provenance.json: the commits, the options, which state files were present, and a checksum of every other output."""
    data = {
        "stateCommit": facts["commits"]["state"],
        "requestsCommit": facts["commits"]["requests"],
        "tool": facts["tool"],
        "options": facts["options"],
        "state": {CATALOGUE: True, RULES: facts["present"]["rules"], CHECKS: facts["present"]["checks"],
                  CONVERSION: facts["present"]["conversion"], PROFILE: facts["present"]["profile"],
                  EVIDENCE: facts["present"].get("evidence", False), TARGETS: facts["target_files"]},
        "outputs": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sorted(outputs.items())},
    }
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def everything(outputs, facts):
    """Every file that the boundary writes, with provenance.json, as {relative path: text}. The page uses it too."""
    return {**outputs, "provenance.json": provenance(outputs, facts)}


def write(out, outputs, facts):
    out = Path(out)
    if out.exists() and not out.is_dir():
        raise BoundaryError(WORDING["out_not_folder"])
    if out.exists() and any(out.iterdir()):
        raise BoundaryError(WORDING["out_not_empty"])
    files = everything(outputs, facts)
    for name, text in sorted(files.items()):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(prog="schemalyser.boundary",
                                     description="Runs everything that reads confidential input, from two folders into one.")
    parser.add_argument("--state", type=Path, required=True, help="the state folder")
    parser.add_argument("--requests", type=Path, required=True, help="the folder of requests, searched for .sql files")
    parser.add_argument("--out", type=Path, required=True, help="an empty or new folder for the outputs")
    parser.add_argument("--state-commit", help="the commit of the state repository that was read")
    parser.add_argument("--requests-commit", help="the commit of the requests repository that was read")
    args = parser.parse_args(argv)
    try:
        out = Path(args.out)
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise BoundaryError(WORDING["out_not_folder"] if not out.is_dir() else WORDING["out_not_empty"])
        outputs, facts = produce(args.state, args.requests, args.state_commit, args.requests_commit)
        write(out, outputs, facts)
    except BoundaryError as error:
        print(f"schemalyser.boundary: {error}", file=sys.stderr)
        return 2
    except Exception:   # noqa: BLE001 - the text of any other error could quote a request, so none is printed
        print(f"schemalyser.boundary: {WORDING['unexpected']}", file=sys.stderr)
        return 3
    if facts["refused"]:
        print(WORDING["done_with_refusals"].format(count=_n(len(facts["refused"]), "target query")))
        return 1
    print(WORDING["done"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
