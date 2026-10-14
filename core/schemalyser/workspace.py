"""The public workspace for a coding agent, and the one-way import of a question from it into a hospital's project.

    python -m schemalyser.workspace export --profile public --out FOLDER [--include PATH ...]
    python -m schemalyser.workspace check WORKSPACE/queries/NAME.question.json
    python -m schemalyser.workspace import WORKSPACE/queries/NAME.question.json --hospital PROJECT [--schema NAME]

export writes a workspace from an explicit allowlist of public sources in this repository, and never by copying a
project and taking things out of it. Every file comes from an entry of ALLOWLIST, or is written here from public
sources: README.md, QUERIES.md, an invented saved hospital schema made as the tests make one, and the example
question. A path outside the allowlist, a path under DENIED whatever the allowlist says, a symbolic link, a file of a
kind that its folder is not allowed to hold, and text that names an absolute home folder each stop the export with
the offending path. manifest.json records every file with its source in the repository, its checksum and the profile.

check applies the import's rules to a question package in the workspace without any hospital, so that an agent can
see whether its question would be accepted.

import treats the package as untrusted. A question arrives as one file, NAME.question.json, in the shape of
schemalyser-question/1, which question.py reads and validates; the import refuses a link, a folder, a file over
SIZE_LIMIT, an executable file and a file that is not UTF-8 text, then applies the package's rules, refuses a package
that waits for the contract, and applies the role-level policy of rolepolicy.py to its SQL. A question that passes is
copied into the project's questions/, the feasibility report is made against the saved hospital schema, and the
audit's package is built into the project's audits/ with the package's own decisions, beside a private validation
report. The only thing written back into the workspace is NAME.import-result.json beside the package, which holds a
fixed status from RESULTS with the names of any rules broken, and nothing that the hospital supports. The status is
decided by check_question from the package and the public contract alone, before the private build runs, so the
workspace's own check gives the same status, and the same package gives the same status whichever hospital the
project holds. The export denies fixtures/held-out/ and the planted answers and values whatever the allowlist says.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import audit, feasibility, question, rolemap, rolepolicy

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("public",)
# Folders that no workspace may draw on, whatever an allowlist says. fixtures/held-out holds the planted scenarios and
# their expected rows, which judge an agent's work and so are never shown to it.
DENIED = ("reference", "etl", "notes", ".git", "fixtures/held-out")
# The private examiner root, where a free question's planted cases and their answers are kept, is denied as well,
# wherever SCHEMALYSER_EXAMINATION puts it inside the repository; its default is under reference/, denied already.
_examination_root = os.environ.get("SCHEMALYSER_EXAMINATION")
if _examination_root:
    try:
        DENIED = DENIED + (Path(_examination_root).resolve().relative_to(ROOT).as_posix(),)
    except ValueError:
        pass
# Files that no workspace may hold, whatever an allowlist says: the planted neonates' expected answers, which the
# export replaces with the planted rows alone, and the planted values that a run's output is searched for.
DENIED_FILES = ("core/schemalyser/rolemodel/planted_neonates.json", "fixtures/planted-values.txt")
PLANTED_NEONATES = "core/schemalyser/rolemodel/planted_neonates.json"
# Names that are never part of a source tree and are passed over without being copied.
SKIPPED = ("__pycache__", ".DS_Store", ".pytest_cache")
# Text that would name a folder on someone's own computer.
HOME_PATH = re.compile(rb"(/Users/[A-Za-z0-9._-]+|/home/[a-z][a-z0-9._-]*/|[A-Za-z]:\\\\?Users\\\\?)")

# The modules of the core that the feasibility report, the audit's package and the testbed import, with the role
# policy and this module, so that an agent can check a question before handing it back.
CORE_MODULES = (
    "__init__", "audit", "blanking", "capability", "catalogue", "compiler", "contract", "contractcheck", "conversion", "convert", "corrections",
    "datadict", "evidence", "extract", "feasibility", "first_ask", "harness", "hospital", "manifests", "mapping", "memo", "normalise",
    "plan", "policy", "project", "propose", "question", "realistic", "signing", "equivalence", "release", "rolepolicy", "roles", "roleshadow", "routes",
    "rules", "sample_vocabulary", "sandbox", "selects", "specification", "statements", "summaries", "testbed",
    "translate", "tuning", "vocabulary", "workspace",
)
# The modules of the core that are packages, each a folder with its module in __init__.py and its command line in
# __main__.py, which the same commands import.
CORE_PACKAGES = ("describe", "examination", "rolemap")

# Each entry is a file, or a folder whose files must all have one of its suffixes.
ALLOWLIST = tuple(
    [{"path": f"core/schemalyser/{m}.py", "why": "a public module of the core"} for m in CORE_MODULES] +
    [{"path": f"core/schemalyser/{p}", "tree": True, "suffixes": (".py",), "why": "a public package of the core"}
     for p in CORE_PACKAGES] + [
        {"path": "core/pyproject.toml", "why": "the core's version and its one dependency"},
        {"path": "core/schemalyser/rolemodel", "tree": True, "suffixes": (".json", ".md", ".sql", ".pub"),
         "why": "the role contract, its description, the compiled neonatal audit and the examiner's public key"},
        {"path": "core/schemalyser/omop/cdm54_fields.csv", "why": "the published field list of OMOP CDM 5.4"},
        {"path": "core/schemalyser/omop/SOURCE.md", "why": "where the field list comes from"},
        {"path": "core/schemalyser/realism", "tree": True, "suffixes": (".csv", ".md", ".py"),
         "why": "the public reference data behind the invented world's realistic values"},
        {"path": "tools/sqlserver/harness.py", "why": "the SQL Server harness, which the testbed runs for its SQL Server stage"},
        {"path": "fixtures/invented-catalogue.csv", "why": "the invented world's tables and columns"},
        {"path": "fixtures/invented-checks.csv", "why": "the invented world's check results"},
        {"path": "fixtures/invented-design.sql", "why": "the invented world's design"},
        {"path": "fixtures/invented-site-rules.json", "why": "the invented world's site rules"},
        {"path": "fixtures/packages/neonatal-low-mean-pressure.question.json",
         "why": "the neonatal audit written as a question package, the example of what a question hands back"},
        {"path": "fixtures/testbed.json", "why": "what the testbed expects of the invented conversion"},
        {"path": "fixtures/dqd-expectations.json", "why": "the dashboard findings that the invented world permits"},
        {"path": "fixtures/make_hospital.py", "why": "writes the invented hospital's tables"},
        {"path": "fixtures/dictionary", "tree": True, "suffixes": (".csv", ".tsv"), "why": "the invented data dictionary"},
        {"path": "fixtures/hospital", "tree": True, "suffixes": (".csv", ".json"), "why": "the invented hospital's tables"},
        {"path": "fixtures/conversion", "tree": True, "suffixes": (".sql", ".json", ".csv"),
         "why": "the invented world's conversion to OMOP, with its gates, counts and planted scenarios"},
        {"path": "fixtures/map", "tree": True, "suffixes": (".json", ".sql"), "why": "the invented world's map"},
        {"path": "fixtures/requests", "tree": True, "suffixes": (".sql",), "why": "the invented world's requests"},
        {"path": "fixtures/targets", "tree": True, "suffixes": (".sql",), "why": "the invented target queries"},
    ])

EXAMPLE = "neonatal_low_mean_pressure"
EXAMPLE_SOURCE = "core/schemalyser/rolemodel/neonatal_low_mean_pressure.sql"
EXAMPLE_PACKAGE = "fixtures/packages/neonatal-low-mean-pressure.question.json"
SCHEMA_NAME = "invented-hospital-schema.schemalyser.zip"
# The confirmations and codes with which the tests make a saved schema from the invented dictionary.
CONFIRMED = ("role_patient rows", "role_patient.patient_key", "role_patient.birth_date", "role_patient.is_test",
             "role_anaesthetic rows", "role_anaesthetic.anaesthetic_key", "role_anaesthetic.patient_key",
             "role_anaesthetic.start_time", "role_anaesthetic.stop_time", "role_reading rows", "role_reading.anaesthetic_key",
             "role_reading.kind", "role_reading.reading_time", "role_reading.value")
CODES = {"52": "map_arterial", "51": "map_cuff"}

# A question is one file, NAME.question.json, and the import answers beside it with NAME.import-result.json.
PACKAGE_SUFFIX = question.PACKAGE_SUFFIX
RESULT_SUFFIX = ".import-result.json"
SIZE_LIMIT = 256 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
FILE_RULES = {
    "file": "The question is one plain file named NAME.question.json, with a plain name, and not a folder.",
    "links": "The file is not a link, symbolic or hard, and nothing beside it where the result is written is a link.",
    "executable": "The file is not executable.",
    "size": f"The file is at most {SIZE_LIMIT // 1024} KB.",
    "text": "The file is UTF-8 text.",
}
# The rule the import adds to the package's own: a question that waits for the contract has nothing to build yet.
WAITS_RULE = {"waits": "The package waits for nothing: it holds no proposal and reads no part the contract refuses for a question."}
# What returns to the workspace: a fixed status from RESULTS and nothing else. A verdict, a class or a count of
# requirements in each state would tell the agent what the hospital supports, so none of them returns.
RESULT_FIELDS = ("query", "result", "rules_failed", "says")
RESULTS = ("accepted", "malformed", "requires_private_review")
WORDING = {
    "accepted": "The question was accepted into the hospital's private project for review.",
    "malformed": "The question was refused as malformed, because it breaks the rules named here.",
    "requires_private_review": "The question was accepted into the hospital's private project, where it needs a review before it goes further.",
    "printed_accepted": "Schemalyser has imported the question into the hospital's project, made its feasibility report "
                        "and built its package there, beside a private validation report.",
    "printed_refused": "Schemalyser has not imported the question, and the hospital's project is unchanged.",
    "printed_nothing": "Schemalyser has written only the import result beside the question, and nothing else returns to the workspace.",
}


class WorkspaceError(ValueError):
    """An export or an import that cannot proceed; the message names the path or the reason."""


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


# The export.

def _denied(relative):
    parts = Path(relative).parts
    text = Path(relative).as_posix()
    return bool(parts) and (".." in parts or Path(relative).is_absolute() or text in DENIED_FILES
                            or any(text == d or text.startswith(d + "/") for d in DENIED))


def _covered(relative, allowlist):
    for entry in allowlist:
        base = entry["path"].rstrip("/")
        if relative == base or (entry.get("tree") and relative.startswith(base + "/")):
            return entry
    return None


def tracked_files(repo):
    """The files that git tracks in repo, as relative posix paths, or None where repo is not a git checkout. Raises
    WorkspaceError where git cannot list them."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        return None
    try:
        done = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise WorkspaceError("git could not list the repository's tracked files, which the export reads so that it "
                             "copies nothing that git does not track.") from None
    return {name for name in done.stdout.decode("utf-8").split("\0") if name}


def sources(repo=ROOT, allowlist=ALLOWLIST, include=(), untracked=False):
    """The repository's files that a workspace holds, as sorted relative paths. Raises WorkspaceError, naming the path,
    for anything outside the allowlist or under DENIED, a link, a missing source, a file of a kind its folder may
    not hold, or a file that git does not track: a file written into an allowlisted folder by a command run over a
    hospital's material is never copied because it happens to sit there. untracked lets a copy of the repository that
    is not a git checkout, as the tests make, be exported."""
    repo = Path(repo).resolve()
    found = set()
    tracked = None if untracked else tracked_files(repo)
    if tracked is None and not untracked:
        raise WorkspaceError("The repository is not a git checkout, so the export cannot tell which files git tracks; "
                             "export from a checkout.")
    for entry in allowlist:
        relative = entry["path"].rstrip("/")
        if _denied(relative):
            raise WorkspaceError(f"{relative} lies in a folder that no workspace may draw on.")
        source = repo / relative
        if source.is_symlink():
            raise WorkspaceError(f"{relative} is a symbolic link, which no workspace may hold.")
        if not entry.get("tree"):
            if not source.is_file():
                raise WorkspaceError(f"{relative} is on the allowlist but is not a file in the repository.")
            found.add(relative)
            continue
        if not source.is_dir():
            raise WorkspaceError(f"{relative} is on the allowlist as a folder but is not one in the repository.")
        for path in sorted(source.rglob("*")):
            inner = path.relative_to(repo).as_posix()
            if any(part in SKIPPED for part in Path(inner).parts) or path.suffix == ".pyc":
                continue
            if _denied(inner):
                # A held-out file or folder inside an allowlisted folder is passed over and never copied.
                continue
            if path.is_symlink():
                raise WorkspaceError(f"{inner} is a symbolic link, which no workspace may hold.")
            if path.is_dir():
                continue
            if path.suffix.lower() not in entry["suffixes"]:
                raise WorkspaceError(f"{inner} is not a kind of file that the allowlist lets {relative} hold.")
            found.add(inner)
    for extra in include:
        relative = Path(extra).as_posix().strip("/")
        if _denied(relative) or _covered(relative, allowlist) is None:
            raise WorkspaceError(f"{relative} is not on the allowlist, so the workspace cannot include it.")
    for relative in found:
        resolved = (repo / relative).resolve()
        if repo not in resolved.parents:
            raise WorkspaceError(f"{relative} resolves outside the repository.")
        if tracked is not None and relative not in tracked:
            raise WorkspaceError(f"{relative} is not tracked by git, so the workspace cannot hold it; commit it if it "
                                 "is public, or remove it.")
    return sorted(found)


def _tables_result(catalogue):
    """The tables and columns query's result for the invented world, as SQL Server Management Studio copies it."""
    import csv
    head = ["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE", "CHARACTER_MAXIMUM_LENGTH",
            "NUMERIC_PRECISION", "NUMERIC_SCALE", "IS_NULLABLE"]
    lines = ["\t".join(head + ["TABLE_ROWS"])]
    with open(catalogue, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            lines.append("\t".join([row[k] or "NULL" for k in head] + ["1000"]))
    return "\n".join(lines) + "\n"


def invented_schema(repo, date):
    """The bytes of a saved hospital schema made from the invented dictionary, as the page and the tests make one."""
    from . import describe
    fixtures = Path(repo) / "fixtures"
    sitting = describe.Describe()
    sitting.version = audit.tool_version()
    sitting.load_dictionary((fixtures / "dictionary" / "invented-dictionary.csv").read_bytes(),
                            (fixtures / "dictionary" / "invented-tables.csv").read_bytes(), {},
                            "invented-dictionary.csv", "invented-tables.csv")
    sitting.propose(date=date)
    sitting.set_settings("production", 2024, "Australia/Sydney", True)
    sitting.tables_query()
    sitting.read_tables(_tables_result(fixtures / "invented-catalogue.csv"))
    for about in CONFIRMED:
        sitting.confirm(about, "yes", date=date)
    sitting.choose_codes("role_reading.kind", dict(CODES), date)
    # The role shadow runs the test on made-up rows that the save owes, as the page's bridge does.
    from . import roleshadow
    return roleshadow.save_zip(sitting, date)


def _contract_table():
    model = rolemap.contract()
    lines = ["| View | Column | Type |", "| --- | --- | --- |"]
    for view in model["views"]:
        if view.get("status") != "contract":
            continue
        for column in view["columns"]:
            lines.append(f"| `{view['name']}` | `{column['name']}` | {column['type']} |")
    words = [f"`{k['kind']}`" for k in model["kinds"]]
    kinds = ", ".join(words[:-1]) + " and " + words[-1]
    return "\n".join(lines), kinds, model.get("version")


FUNCTION_NAMES = ("COUNT", "SUM", "MIN", "MAX", "AVG", "CAST", "TRY_CAST", "COALESCE", "ISNULL", "NULLIF", "ROUND",
                  "FLOOR", "CEILING", "ABS", "CASE", "CONCAT", "DATEDIFF", "DATEADD", "YEAR", "MONTH", "DAY")

README = """# The public workspace

This folder is a workspace in which a person or a coding agent can write a clinical question over the parts of the anaesthetic record and test it on invented data. Schemalyser wrote it on {date} from an explicit list of public sources in its repository, with the profile `{profile}`. Nothing here was copied from a hospital's project and then trimmed, and `manifest.json` records every file with its source and its checksum.

## What the workspace holds

- `core/schemalyser/rolemodel/` holds the role contract, `contract.json`, with its description in words, `roles.md`, and the neonatal audit as the example of a question over the role views.
- `fixtures/` holds the invented world: an invented catalogue, data dictionary, hospital tables, conversion to OMOP, and map.
- `core/schemalyser/rolemodel/planted_neonates.json` holds the planted neonates as rows of the role views, without the answers that the neonatal audit must give for them. Those answers, and the expected rows of the planted scenarios, are held out of the workspace, so that a question is judged against answers that its author has not seen.
- `core/schemalyser/omop/cdm54_fields.csv` is the published field list of OMOP CDM 5.4, `core/schemalyser/sample_vocabulary.py` writes the sample vocabulary when the testbed runs, and `tools/sqlserver/harness.py` is the SQL Server harness that the testbed runs for its SQL Server stage.
- `core/` holds a copy of the public modules of Schemalyser that the feasibility report, the audit's package and the testbed need.
- `schemas/{schema}` is a saved hospital schema made from the invented dictionary, as the page makes one, so that the commands have a schema to read.
- `queries/` holds the questions, one file each, beginning with the example, `queries/{example}.question.json`.
- `QUERIES.md` says how to write a question, which functions it may use, and what each command does.

## What it does not hold

The workspace holds no hospital's schema, data dictionary, local codes, plans, counts or results, and no material from the vendor of any hospital's record system. The only hospital schema here is the invented one, and every table it names is invented.

## The invariant

An agent working here can write or change a clinical question over the public contracts but cannot see any hospital's mapping from those contracts to its database.

## A public interface is not the same as shareable content

The role views are a public interface, so anyone may read a question written over them. That does not make every question shareable. A question that singles out an identifiable patient, or that concerns an event the hospital has not disclosed, carries clinical content that does not belong in a public folder, even though it is written over the roles. If a question is of that kind, write it on the hospital's side instead.

## Handing a question back

Each finished question is one file, `queries/NAME.question.json`, a question package in the shape of `core/schemalyser/rolemodel/question.schema.json`: its title, the statement of its cohort, outcome and result in plain words, its decisions, its SQL over the role views, the parts the SQL reads at their versions in the contract, and nothing else. `QUERIES.md` says how to write one, and the example is one to copy.

Before you hand a package back, you can check it with `PYTHONPATH=core python -m schemalyser.workspace check queries/NAME.question.json`, which applies the same rules as the import. The person who keeps the hospital's project then imports the file on the hospital's side. The import refuses a link, a folder, a large file and a package that breaks a rule of its shape, and it writes back beside the package only `NAME.import-result.json`. That file holds one status and nothing more: `accepted` or `requires_private_review` once the question is in the hospital's private project, or `malformed` with the names of the rules it broke. It says nothing of what the hospital's schema supports, and nothing else returns from the hospital.
"""

QUERIES = """# Writing a question

A question is one SELECT over the role views, written in SQL Server's dialect of SQL (T-SQL), as sqlglot 30.21.0 reads it. Schemalyser later compiles it through a hospital schema into a two-part script over that hospital's own tables, so the question itself never names a table of any database.

## The role views

A question may read the three views of version {version} of the contract, the mapping views, and its own common table expressions over them. The fifteen further views in `contract.json` are drafts, and no question may read them until they join the contract.

{table}

A column of the type `kind` holds a word of the contract's vocabulary rather than a hospital's code. The kinds of `role_reading.kind` are {kinds}. A literal compared with a column of a kind must be one of these words, and the hospital schema translates each into that hospital's codes.

A mapping view, such as `map_drug_concept`, translates a hospital's local codes of an open domain into standard concepts. It has four columns, `local_key`, `concept_id`, `status` and `provenance`, and a part's column of the type `local_key` joins it on `local_key`. A question never sees a hospital's code: it names drugs, procedures, diagnoses, tests and units by their standard concepts, and it handles a key whose status is `unmapped` or `ambiguous`, or that the view does not list, as an ordinary outcome.

A question may name the capabilities of the catalogue in `contract.json` that it computes, each on a line of its leading comment written as `-- capability: NAME`. The feasibility report then resolves the question's requirements through each capability's own, and a name that the catalogue does not hold breaks the rule on capabilities.

`core/schemalyser/rolemodel/roles.md` sets out the rules of the record. The most consequential is that a reading belongs to an anaesthetic by its link to that anaesthetic, `role_reading.anaesthetic_key`, and never because its time falls within the anaesthetic.

## What a question may use

- The statement is one SELECT, which may begin with WITH and may use UNION ALL. It fills no table, declares no variable and names no temporary table.
- A role view is named alone, with no database or schema before it.
- The functions are {functions}, with ROW_NUMBER, LEAD and LAG used only with OVER.
- Predicates such as IN, BETWEEN, IS NULL, EXISTS and LIKE are allowed, as is arithmetic.
- EXEC, dynamic SQL, procedures, OPENQUERY and OPENROWSET are refused wherever they appear.

`core/schemalyser/rolepolicy.py` applies these rules, and the import applies them again on the hospital's side.

## The question package

A question is handed back as one file, `queries/NAME.question.json`, in the shape of `core/schemalyser/rolemodel/question.schema.json`: the format `schemalyser-question/1`, a title on one line, the contract version, a `statement` of the cohort, the outcome and the result in plain words, the `decisions` behind it, each with what it is about, the choice, a value and unit where one exists, the sentence, who made it and when, the SQL in `question_sql`, the `parts` the SQL reads, each with its version in the contract and the contract's admission for a question, an empty list of `capabilities` and of `proposals` unless the question composes a capability or needs what the contract lacks, a short `note`, and `produced`, which says who wrote it and when. `python -m schemalyser.question check queries/NAME.question.json` names every rule a package breaks, and the import applies the same rules. A package carries no status: what it may claim is decided by the evidence recorded about it.

## How the example is built

`queries/{example}.question.json` is the neonatal audit as a package, and its SQL is `core/schemalyser/rolemodel/{example}.sql`. The SQL's leading comment states the question and every decision behind it, and the first sentence of that comment, up to the first question mark or full stop, is what the reports show as the question. The query then works in steps. `neonatal` joins each anaesthetic to its patient and keeps the neonates. `reading` joins the mean pressures to those anaesthetics by key, with the kinds named as `'map_arterial'` and `'map_cuff'`. `stood` and `low` work out how long each reading stands, and `banded` puts each anaesthetic in a band of minutes below 40. The final SELECT returns counts alone, one row for each band.

A new question is best written in the same way: a leading comment that states the question and its decisions, steps that each do one thing, and a final SELECT that returns counts rather than rows of patients, with each decision of the comment written as a decision of the package.

## The commands

Run each command from this folder with `core` on the Python path, using Python 3.13 with sqlglot 30.21.0 and duckdb 1.5.1.

```sh
PYTHONPATH=core python -m schemalyser.workspace check queries/NAME.question.json
PYTHONPATH=core python -m schemalyser.feasibility report schemas/{schema} queries/NAME.question.json
PYTHONPATH=core python -m schemalyser.audit build schemas/{schema} queries/NAME.question.json --out audits/NAME
PYTHONPATH=core python -m schemalyser.testbed run --world fixtures --out runs/first
```

- `check` applies the import's rules to the package and lists each rule with any fragment that broke it.
- `feasibility report` reads the question against the invented hospital schema and says, for each requirement, how far that schema supplies it, with the investigation that would move each one that falls short.
- `audit build` compiles the question through the invented hospital schema into the package that a database analyst would run, with the answer on made-up rows, and the safety report with its execution class.
- `testbed run` builds the invented world, runs its conversion to OMOP, checks the planted scenarios and reconciles source with target, all on invented data.

Every table that these reports name is a table of the invented world. On the hospital's side the same commands name the hospital's tables, which is why their reports stay there.
"""

def planted_inputs(repo=ROOT):
    """The planted neonates as rows of the role views, which are development fixtures, without the expected answers,
    which are held out."""
    held = json.loads((Path(repo) / PLANTED_NEONATES).read_text(encoding="utf-8"))
    kept = {key: value for key, value in held.items() if key != "expectations"}
    kept["description"] = ("The planted neonates, written once as rows of the three role views. The answers that the "
                           "neonatal audit must give for them are held out of the public workspace.")
    return (json.dumps(kept, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def export(out, profile="public", repo=ROOT, allowlist=ALLOWLIST, include=(), date=None, untracked=False):
    """Writes the workspace to out, which must be empty or not yet exist. Returns the manifest. Every source is a file
    that git tracks, unless untracked is given for a copy of the repository that is not a checkout."""
    if profile not in PROFILES:
        raise WorkspaceError(f"{profile} is not a profile of the export; the only profile is public.")
    repo, out = Path(repo).resolve(), Path(out).resolve()
    date = date or dt.date.today().isoformat()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise WorkspaceError(f"{out.name} already holds files, and the export writes only into an empty folder.")
    if out == repo or repo in out.parents and _denied(out.relative_to(repo).as_posix()):
        raise WorkspaceError(f"{out.name} lies where no workspace may be written.")
    files = sources(repo, allowlist, include, untracked)
    written = {}
    for relative in files:
        written[relative] = ((repo / relative).read_bytes(), relative)
    table, kinds, version = _contract_table()
    words = {"date": date, "profile": profile, "schema": SCHEMA_NAME, "example": EXAMPLE, "version": version,
             "table": table, "kinds": kinds, "functions": ", ".join(FUNCTION_NAMES)}
    generated = "generated by schemalyser.workspace"
    written["README.md"] = (README.format(**words).encode("utf-8"), None)
    written["QUERIES.md"] = (QUERIES.format(**words).encode("utf-8"), None)
    written[f"schemas/{SCHEMA_NAME}"] = (invented_schema(repo, date), None)
    written[PLANTED_NEONATES] = (planted_inputs(repo), None)
    written[f"queries/{EXAMPLE}{PACKAGE_SUFFIX}"] = ((repo / EXAMPLE_PACKAGE).read_bytes(), EXAMPLE_PACKAGE)
    for relative, (data, _) in written.items():
        if HOME_PATH.search(data):
            raise WorkspaceError(f"{relative} names a folder on someone's own computer, so the workspace cannot hold it.")
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for relative, (data, source) in sorted(written.items()):
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries.append({"path": relative, "source": source or generated, "sha256": sha256(data), "bytes": len(data)})
    manifest = {"tool": audit.TOOL, "tool_version": audit.tool_version(), "profile": profile, "date": date,
                "role_model_version": version, "files": entries}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


# A question's package file.

def _rules(failures, names):
    return [{"id": rule, "rule": names[rule], "passed": not failures.get(rule), "fragments": failures.get(rule, [])}
            for rule in names]


def query_name(path):
    """The name of a question from its file's name, NAME.question.json with a plain NAME, or None."""
    name = Path(path).name
    if not name.endswith(PACKAGE_SUFFIX):
        return None
    stem = name[:-len(PACKAGE_SUFFIX)]
    return stem if SAFE_NAME.match(stem) else None


def read_file(path):
    """Applies the file's rules. Returns (rules, text), where text is None where a rule stopped the read."""
    path = Path(path)
    failures = {}

    def fail(rule, fragment):
        failures.setdefault(rule, []).append(fragment)

    if path.is_symlink():
        fail("links", f"{path.name} is a symbolic link")
        return _rules(failures, FILE_RULES), None
    if path.is_dir():
        fail("file", f"{path.name} is a folder")
        return _rules(failures, FILE_RULES), None
    if not path.exists():
        fail("file", f"{path.name} does not exist")
        return _rules(failures, FILE_RULES), None
    if query_name(path) is None:
        fail("file", f"{path.name} is not named NAME.question.json with a name of letters, digits, full stops, hyphens and underscores")
    held = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(held.st_mode):
        # A pipe would hold the read for ever, and a device or socket is not a file of a question.
        fail("file", f"{path.name} is not a plain file")
        return _rules(failures, FILE_RULES), None
    if held.st_nlink > 1:
        # A hard link to a file elsewhere would make the rules read that file.
        fail("links", f"{path.name} is linked to another file")
        return _rules(failures, FILE_RULES), None
    if result_path(path).is_symlink():
        # A link planted where the result is written would be replaced, never followed; the package is refused all the
        # same, because nothing beside it is a link by accident.
        fail("links", f"{result_path(path).name} beside the package is a link")
        return _rules(failures, FILE_RULES), None
    if held.st_mode & 0o111:
        fail("executable", f"{path.name} is executable")
    if held.st_size > SIZE_LIMIT:
        fail("size", f"{path.name} holds {held.st_size:,} bytes")
        return _rules(failures, FILE_RULES), None
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("text", f"{path.name} is not UTF-8 text")
        return _rules(failures, FILE_RULES), None
    if "\0" in text:
        fail("text", f"{path.name} holds a NUL character")
        return _rules(failures, FILE_RULES), None
    return _rules(failures, FILE_RULES), (text if not failures else None)


def check_question(path, admit=None):
    """The file's rules, the package's rules, the rule that it waits for nothing and, when they pass, the role-level
    policy on its SQL, with the status that the import returns, as {"rules", "failed", "status", "form", "package",
    "sql"}. admit="draft", which only the examiner gives, lets the policy read a draft part.

    The status is a function of the package and the public contract alone: malformed where a rule fails,
    requires_private_review where the question passes every rule but lacks the form of the two-part script (a step
    that chooses the anaesthetics, every part reached from it by key, and counts in the result; audit.public_form),
    and accepted otherwise. No hospital schema is read, so the workspace's own check and the import give the same
    status for the same package, whichever hospital the project holds."""
    rules, text = read_file(path)
    package, sql, form = None, None, []
    if text is not None:
        found = question.check(text)
        rules = rules + found["rules"]
        package, sql = (found["package"], found["sql"]) if not found["failed"] else (None, None)
    if package is not None:
        waiting = [f"the package holds {len(package['proposals'])} proposal(s)"] if package["proposals"] else []
        waiting += [f"{p['name']} is refused for a question" for p in package["parts"] if p["admission"] == question.REFUSED and admit != "draft"]
        rules = rules + _rules({"waits": waiting} if waiting else {}, WAITS_RULE)
        if not waiting:
            # The policy reads the SQL with its capabilities composed, as the hospital compiles it.
            rules = rules + rolepolicy.check(sql, [c["name"] for c in package["capabilities"]], admit=admit)["rules"]
    failed = [r["id"] for r in rules if not r["passed"]]
    if failed:
        status = "malformed"
    else:
        form = audit.public_form(sql, admit=admit)
        status = "requires_private_review" if form else "accepted"
    return {"rules": rules, "failed": failed, "status": status, "form": form, "package": package, "sql": sql}


def _schema_path(project, name):
    if name:
        return project.schema_path(name)
    held = project.schemas()
    if len(held) != 1:
        raise WorkspaceError("The project holds no saved hospital schema; add one to schemas/ first." if not held else
                             "The project holds more than one saved hospital schema, so name one with --schema.")
    return project.schema_path(held[0])


def result_path(path):
    """Where the import's result is written: NAME.import-result.json beside the package."""
    path = Path(path)
    name = path.name[:-len(PACKAGE_SUFFIX)] if path.name.endswith(PACKAGE_SUFFIX) else path.name
    return path.parent / f"{name}{RESULT_SUFFIX}"


def _write_result(path, result):
    """Writes the result beside the question, whole or not at all: it is written to a file of its own in the folder
    and then put in place, so that nothing can swap a link in between."""
    target = result_path(path)
    handle, temporary = tempfile.mkstemp(prefix=".import-result-", suffix=".json", dir=str(target.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, indent=2) + "\n")
    os.replace(temporary, target)


# Stage 5, at the hospital: whether this hospital's mapping supports the question. Recorded privately in the project
# and never returned to the workspace.

MAPPING_SUPPORTED = "mapping supported"
MAPPING_NOT_SUPPORTED = "mapping not yet supported"
LEVEL_WORDING = {
    "no_report": "the feasibility report was not made",
    "short": "{count} of the question's requirements {verb} not described or not mapped in this hospital schema: {ids}",
    "not_built": "the package did not build: {says}",
    "class_d": "the compiled script's class is D, which the policy does not run",
    "not_run": "the question was not run on the shadow of the hospital schema: {says}",
    "no_dictionary": "the saved hospital schema holds no copy of the dictionary, so the shadow of the map cannot be built from it",
}


def mapping_supported(schema, assessment, manifest, sql):
    """Whether this hospital's mapping supports the question, derived from three records and never from a field: the
    feasibility report with every requirement mapped in the hospital schema, the compiled script's class, and the
    question compiled through the map giving the same answer on the map's shadow as the question on the role rows
    directly. A requirement that a person has not yet confirmed is mapped all the same, and is named under
    awaiting_confirmation, since confirmation and reconciliation are what the levels above this one add. Returns
    {"level", "reasons", "requirements": {"total", "short", "awaiting_confirmation"}, "execution_class", "shadow"}."""
    # The shadow brings DuckDB with it, which the import loads only here and never at import.
    from . import roleshadow
    reasons = []
    rows = (assessment or {}).get("states") or []
    short = [r["id"] for r in rows if feasibility.RANK[r["state"]] < feasibility.RANK[feasibility.PROPOSED]]
    awaiting = [r["id"] for r in rows if feasibility.RANK[feasibility.PROPOSED] <= feasibility.RANK[r["state"]] < feasibility.RANK[feasibility.CONFIRMED]]
    if assessment is None:
        reasons.append(LEVEL_WORDING["no_report"])
    elif short:
        reasons.append(LEVEL_WORDING["short"].format(count=len(short), verb="is" if len(short) == 1 else "are", ids=", ".join(short)))
    execution_class = manifest["execution_class"] if manifest else None
    if manifest is None:
        reasons.append(LEVEL_WORDING["not_built"].format(says="see build_failure"))
    elif execution_class == "D":
        reasons.append(LEVEL_WORDING["class_d"])
    shadow = None
    if not schema.writes:
        reasons.append(LEVEL_WORDING["no_dictionary"])
    else:
        try:
            shadow = roleshadow.run_question(schema.sitting, sql)
        except Exception as error:  # noqa: BLE001 - the shadow's own failure is recorded, never raised past the import
            shadow = {"same": False, "problem": f"{type(error).__name__}: {str(error).splitlines()[0][:200]}"}
        if not shadow["same"]:
            reasons.append(LEVEL_WORDING["not_run"].format(says=shadow.get("problem") or "the answers differ"))
    return {"level": MAPPING_SUPPORTED if not reasons else MAPPING_NOT_SUPPORTED, "reasons": reasons,
            "requirements": {"total": len(rows), "short": short, "awaiting_confirmation": awaiting},
            "execution_class": execution_class, "shadow": shadow}


def import_question(path, hospital, schema=None, date=None):
    """Imports one question package into a hospital's project. Returns the counts-only result, which is also written
    beside the package as NAME.import-result.json."""
    from .project import Project, ProjectError
    path = Path(path)
    date = date or dt.date.today().isoformat()
    name = query_name(path)
    checked = check_question(path)
    result = {"query": name, "result": "malformed", "rules_failed": checked["failed"], "says": WORDING["malformed"]}
    if checked["failed"]:
        if path.parent.is_dir():
            _write_result(path, result)
        return result, checked, None
    package = checked["package"]
    # The status is decided here, from the package and the public contract alone, before the hospital's project or
    # schema is read for anything, and nothing that follows changes it. It is written beside the question now, so that
    # whether a result comes back never depends on the project either.
    status = checked["status"]
    result.update(result=status, rules_failed=[], says=WORDING[status])
    _write_result(path, result)
    try:
        project = Project(hospital)
        schema_path = _schema_path(project, schema)
        feasibility.Schema.load(schema_path)
    except (ProjectError, feasibility.FeasibilityError) as error:
        raise WorkspaceError(str(error)) from None
    title = " ".join(package["title"].split())
    sql = checked["sql"]
    held = project.add_question(title, sql)
    question_path = project.question_path(held)
    audit_name = project.unique("audits", f"{Path(held).stem}_{date}")
    record = project.folder("audits") / audit_name
    record.mkdir()
    # The package itself is kept with the record, and the build takes its SQL and its decisions from it.
    kept = record / f"{name}{PACKAGE_SUFFIX}"
    kept.write_bytes(path.read_bytes())
    # The private build runs after the status is decided. What it finds, and any error it meets, goes to the owner's
    # feasibility report and validation report in the project, never to the status.
    found, manifest, failure = None, None, None
    try:
        found = feasibility.assess(feasibility.Schema.load(schema_path), sql, held)
        manifest = audit.build(schema_path, kept, record / "package", date=date)
    except Exception as error:  # noqa: BLE001 - nothing the build meets may reach the status or the workspace
        failure = {"error": type(error).__name__, "says": str(error)}
    built = manifest is not None
    # Stage 5, at this hospital, from the three records above; it goes to the validation report alone.
    try:
        level = mapping_supported(feasibility.Schema.load(schema_path), found, manifest, sql)
    except Exception as error:  # noqa: BLE001
        level = {"level": MAPPING_NOT_SUPPORTED, "reasons": [f"{type(error).__name__}: {str(error).splitlines()[0][:200]}"],
                 "requirements": None, "execution_class": None, "shadow": None}
    states = {}
    for row in (found or {}).get("states", []):
        states[row["state"]] = states.get(row["state"], 0) + 1
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    decisions = question.audit_decisions(package)
    (record / "request.json").write_text(json.dumps(
        {"schema": schema_path.name, "question": held, "period": None, "decisions": decisions["decisions"],
         "exact_small_numbers": False, "imported_from": f"queries/{name}{PACKAGE_SUFFIX}", "exit_code": 0 if built else 1,
         "finished": stamp}, indent=2) + "\n", encoding="utf-8")
    report = {"imported": date, "from": f"queries/{name}{PACKAGE_SUFFIX}", "package_sha256": sha256(path.read_bytes()),
              "title": title, "question": held, "schema": schema_path.name, "rules": checked["rules"],
              "public_form": checked["form"], "parts": package["parts"], "contract_version": package["contract_version"],
              "feasibility": {"verdict": found["verdict"], "says": found["verdict_text"], "states": states} if found else None,
              "package": "package" if built else None, "build_failure": failure,
              "execution_class": manifest["execution_class"] if manifest else None,
              "hospital_level": level,
              "returned_to_workspace": result}
    (record / "import-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_result(path, result)
    return result, checked, record


# A question opened at the hospital, by file with the examiner's signed report: what the page Describe the record and
# python -m schemalyser.describe question show after the nine steps.

def open_question(sitting, package, report):
    """Opens a question package and its report, given as bytes, against the hospital schema that sitting holds: the
    package is checked as the import checks it, the report is verified as the examiner's over these bytes at logic
    examined or above, and the question is compiled through the schema into the two-part script. Returns what the
    page shows: the question in words, its level, each part it reads with what the schema holds for it, the script
    where it compiles, and the reason where it does not. Raises DescribeError, with "question:" or "report:" first,
    where the package or the report cannot be used."""
    import datetime as dt
    import hashlib
    from .describe import DescribeError
    from .examination import status
    found = question.check(package.decode("utf-8", errors="replace"))
    if found["failed"]:
        raise DescribeError("question: " + " ".join(question.sentences(found)))
    held = found["package"]
    try:
        signed = json.loads(report.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise DescribeError("report: the report is not a file the examiner writes.") from None
    if (signed.get("package") or {}).get("sha256") != hashlib.sha256(package).hexdigest():
        raise DescribeError("report: the report is not over this package as it is now.")
    verified = status.verify(signed)
    if not verified["verified"]:
        raise DescribeError("report: " + verified["says"])
    if verified["level"] not in status.LEVELS[2:]:
        raise DescribeError(f"report: the examination grants the level {verified['level']}, and the page compiles a question "
                            f"only at logic examined or above. {verified['says']}")
    policy = rolepolicy.check(found["sql"], [c["name"] for c in held["capabilities"]])
    readiness = (sitting.readiness() or {}).get("parts") or {}
    parts = []
    for part in held["parts"]:
        role = (sitting.data or {}).get("roles", {}).get(part["name"]) or {}
        binding = (role.get("rows") or {}).get("binding")
        state = readiness.get(part["name"]) or {}
        parts.append({"name": part["name"], "title": rolemap.view_title(part["name"], False), "admission": part["admission"],
                      "bound": bool(binding), "table": (binding or {}).get("table"), "readiness": state.get("reached")})
    year = sitting.settings.get("year") or dt.date.today().year
    start, end = dt.date(int(year), 1, 1), dt.date(int(year), 12, 31)
    script, problem = None, None
    try:
        schema = feasibility.Schema(sitting.folder_files())
        compiled = audit.compile_audit(schema, found["sql"], start, end, blank=True)
        meta = {"date": dt.date.today().isoformat(), "query": held["title"], "schema": sitting.file_name(), "question": held["title"],
                "start": start.isoformat(), "end": end.isoformat(), "large": audit.policy.LARGE}
        script = audit.script(compiled, meta, True)
    except (audit.AuditError, feasibility.FeasibilityError, rolemap.MapError, DescribeError) as error:
        problem = str(error)
    return {"question": {"title": held["title"], "statement": held["statement"], "decisions": [x["decision"] for x in held["decisions"]],
                         "examples": [e.get("says") for e in signed.get("examples") or []], "approval": held.get("approval"),
                         "level": verified["level"], "level_says": verified["says"], "examined_at": signed.get("examined_at")},
            "policy": {"outcome": policy["outcome"], "failed": policy["failed"], "admission": policy["admission"]},
            "parts": parts, "script": script, "problem": problem}


# The command line.

def _print_rules(rules):
    for rule in rules:
        mark = "passed" if rule["passed"] else "failed"
        print(f"  {rule['id']}: {mark}. {rule['rule']}")
        for fragment in rule["fragments"]:
            print(f"      {fragment}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.workspace",
                                     description="The public workspace for a coding agent, and the one-way import of a question from it.")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("export", help="Write a workspace from the allowlist of public sources.")
    one.add_argument("--profile", required=True, choices=PROFILES)
    one.add_argument("--out", required=True)
    one.add_argument("--include", action="append", default=[], help="a further path, which must be on the allowlist")
    two = commands.add_parser("check", help="Apply the import's rules to a question package.")
    two.add_argument("package")
    three = commands.add_parser("import", help="Import a question package into a hospital's project.")
    three.add_argument("package")
    three.add_argument("--hospital", required=True)
    three.add_argument("--schema")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            manifest = export(args.out, args.profile, include=args.include)
            size = sum(f["bytes"] for f in manifest["files"])
            print(f"Schemalyser has written a workspace of {len(manifest['files']) + 1} files ({size / 1e6:.1f} MB) "
                  f"to {Path(args.out).name}, with manifest.json listing each one.")
            return 0
        if args.command == "check":
            found = check_question(args.package)
            _print_rules(found["rules"])
            print("The package passes every rule of the import." if not found["failed"] else
                  f"The package fails {len(found['failed'])} of the import's rules.")
            print(f"The import would return the status {found['status']}.")
            return 0 if not found["failed"] else 1
        result, checked, _ = import_question(args.package, args.hospital, args.schema)
        _print_rules(checked["rules"])
        print(WORDING["printed_refused"] if result["result"] == "malformed" else WORDING["printed_accepted"])
        print(json.dumps(result, indent=2))
        print(WORDING["printed_nothing"])
        return 0 if result["result"] != "malformed" else 1
    except (WorkspaceError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
