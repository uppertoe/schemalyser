"""The public workspace for a coding agent, and the one-way import of a question from it into a hospital's project.

    python -m schemalyser.workspace export --profile public --out FOLDER [--include PATH ...]
    python -m schemalyser.workspace check WORKSPACE/queries/NAME
    python -m schemalyser.workspace import WORKSPACE/queries/NAME --hospital PROJECT [--schema NAME]

export writes a workspace from an explicit allowlist of public sources in this repository, and never by copying a
project and taking things out of it. Every file comes from an entry of ALLOWLIST, or is written here from public
sources: README.md, QUERIES.md, an invented saved hospital schema made as the tests make one, and the example
question. A path outside the allowlist, a path under DENIED whatever the allowlist says, a symbolic link, a file of a
kind that its folder is not allowed to hold, and text that names an absolute home folder each stop the export with
the offending path. manifest.json records every file with its source in the repository, its checksum and the profile.

check applies the import's rules to a folder of the workspace without any hospital, so that an agent can see whether
its question would be accepted.

import treats the folder as untrusted. It accepts question.sql, title.txt and note.md and nothing else, refuses a
symbolic link, a subfolder, a file over SIZE_LIMIT, an executable file and any declaration of dependencies, and applies the role-level
policy of rolepolicy.py to question.sql. A question that passes is copied into the project's questions/, the
feasibility report is made against the saved hospital schema, and the audit's package is built into the project's
audits/, beside a private validation report. The only thing written back into the workspace is import-result.json,
which holds a fixed status from RESULTS with the names of any rules broken, and nothing that the hospital supports. The
status is decided by check_folder from the package and the public contract alone, before the private build runs, so
the workspace's own check gives the same status, and the same package gives the same status whichever hospital the
project holds. The export denies fixtures/held-out/ and the planted answers and values whatever the allowlist says.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import audit, feasibility, rolemap, rolepolicy

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("public",)
# Folders that no workspace may draw on, whatever an allowlist says. fixtures/held-out holds the planted scenarios and
# their expected rows, which judge an agent's work and so are never shown to it.
DENIED = ("reference", "etl", "notes", ".git", "fixtures/held-out")
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
    "__init__", "audit", "blanking", "capability", "catalogue", "convert", "corrections", "datadict", "describe",
    "evidence", "extract", "feasibility", "first_ask", "harness", "hospital", "mapping", "memo", "normalise", "plan",
    "policy", "project", "propose", "realistic", "release", "rolemap", "rolepolicy", "roles", "routes", "rules",
    "sample_vocabulary", "sandbox", "specification", "statements", "summaries", "testbed", "translate", "tuning",
    "vocabulary", "workspace",
)

# Each entry is a file, or a folder whose files must all have one of its suffixes.
ALLOWLIST = tuple(
    [{"path": f"core/schemalyser/{m}.py", "why": "a public module of the core"} for m in CORE_MODULES] + [
        {"path": "core/pyproject.toml", "why": "the core's version and its one dependency"},
        {"path": "core/schemalyser/rolemodel", "tree": True, "suffixes": (".json", ".md", ".sql"),
         "why": "the role contract, its description and the compiled neonatal audit"},
        {"path": "core/schemalyser/omop/cdm54_fields.csv", "why": "the published field list of OMOP CDM 5.4"},
        {"path": "core/schemalyser/omop/SOURCE.md", "why": "where the field list comes from"},
        {"path": "core/schemalyser/realism", "tree": True, "suffixes": (".csv", ".md", ".py"),
         "why": "the public reference data behind the invented world's realistic values"},
        {"path": "tools/sqlserver/harness.py", "why": "the SQL Server harness, which the testbed runs for its SQL Server stage"},
        {"path": "fixtures/invented-catalogue.csv", "why": "the invented world's tables and columns"},
        {"path": "fixtures/invented-checks.csv", "why": "the invented world's check results"},
        {"path": "fixtures/invented-design.sql", "why": "the invented world's design"},
        {"path": "fixtures/invented-site-rules.json", "why": "the invented world's site rules"},
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
SCHEMA_NAME = "invented-hospital-schema.schemalyser.zip"
# The confirmations and codes with which the tests make a saved schema from the invented dictionary.
CONFIRMED = ("role_patient rows", "role_patient.patient_key", "role_patient.birth_date", "role_patient.is_test",
             "role_anaesthetic rows", "role_anaesthetic.anaesthetic_key", "role_anaesthetic.patient_key",
             "role_anaesthetic.start_time", "role_anaesthetic.stop_time", "role_reading rows", "role_reading.anaesthetic_key",
             "role_reading.kind", "role_reading.reading_time", "role_reading.value")
CODES = {"52": "map_arterial", "51": "map_cuff"}

# The folder of a question.
QUESTION, TITLE, NOTE, RESULT = "question.sql", "title.txt", "note.md", "import-result.json"
ACCEPTED_FILES = (QUESTION, TITLE, NOTE)
SIZE_LIMIT = 64 * 1024
TITLE_LIMIT = 120
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DEPENDENCY_NAMES = re.compile(r"^(requirements.*\.(txt|in)|pyproject\.toml|setup\.(py|cfg)|pipfile(\.lock)?|poetry\.lock|"
                              r"uv\.lock|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|environment\.ya?ml|conda\.ya?ml|"
                              r"gemfile(\.lock)?|go\.(mod|sum)|cargo\.(toml|lock)|.*\.(whl|egg|tar\.gz))$", re.I)
FOLDER_RULES = {
    "folder": "The question's folder is a folder with a plain name, not a link.",
    "links": "The folder holds no symbolic link.",
    "dependencies": "The folder declares no dependency.",
    "files": "The folder holds question.sql, title.txt and note.md, and nothing else.",
    "size": f"Each file is at most {SIZE_LIMIT // 1024} KB.",
    "text": "Each file is UTF-8 text.",
    "title": f"The title is one line of at most {TITLE_LIMIT} characters.",
    "executable": "No file is executable.",
}
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
    "printed_nothing": "Schemalyser has written only import-result.json into the workspace, and nothing else returns to it.",
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


def sources(repo=ROOT, allowlist=ALLOWLIST, include=()):
    """The repository's files that a workspace holds, as sorted relative paths. Raises WorkspaceError, naming the path,
    for anything outside the allowlist or under DENIED, a link, a missing source, or a file of a kind its folder may
    not hold."""
    repo = Path(repo).resolve()
    found = set()
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
    return sitting.save_zip(date)


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
- `queries/` holds the questions, one folder each, beginning with the example, `queries/{example}/`.
- `QUERIES.md` says how to write a question, which functions it may use, and what each command does.

## What it does not hold

The workspace holds no hospital's schema, data dictionary, local codes, plans, counts or results, and no material from the vendor of any hospital's record system. The only hospital schema here is the invented one, and every table it names is invented.

## The invariant

An agent working here can write or change a clinical question over the public contracts but cannot see any hospital's mapping from those contracts to its database.

## A public interface is not the same as shareable content

The role views are a public interface, so anyone may read a question written over them. That does not make every question shareable. A question that singles out an identifiable patient, or that concerns an event the hospital has not disclosed, carries clinical content that does not belong in a public folder, even though it is written over the roles. If a question is of that kind, write it on the hospital's side instead.

## Handing a question back

Each finished question goes in a folder of its own, `queries/NAME/`, which holds three files and nothing else:

- `question.sql`, the question as one SELECT over the role views;
- `title.txt`, its title on one line;
- `note.md`, a short note of what the question measures.

Before you hand a folder back, you can check it with `PYTHONPATH=core python -m schemalyser.workspace check queries/NAME`, which applies the same rules as the import. The person who keeps the hospital's project then imports the folder on the hospital's side. The import refuses any other file, any link and any large file, and it writes back into the folder only `import-result.json`. That file holds one status and nothing more: `accepted` or `requires_private_review` once the question is in the hospital's private project, or `malformed` with the names of the rules it broke. It says nothing of what the hospital's schema supports, and nothing else returns from the hospital.
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

## How the example is built

`queries/{example}/question.sql` is the neonatal audit. Its leading comment states the question and every decision behind it, and the first sentence of that comment, up to the first question mark or full stop, is what the reports show as the question. The query then works in steps. `neonatal` joins each anaesthetic to its patient and keeps the neonates. `reading` joins the mean pressures to those anaesthetics by key, with the kinds named as `'map_arterial'` and `'map_cuff'`. `stood` and `low` work out how long each reading stands, and `banded` puts each anaesthetic in a band of minutes below 40. The final SELECT returns counts alone, one row for each band.

A new question is best written in the same way: a leading comment that states the question and its decisions, steps that each do one thing, and a final SELECT that returns counts rather than rows of patients.

## The commands

Run each command from this folder with `core` on the Python path, using Python 3.13 with sqlglot 30.21.0 and duckdb 1.5.1.

```sh
PYTHONPATH=core python -m schemalyser.workspace check queries/NAME
PYTHONPATH=core python -m schemalyser.feasibility report schemas/{schema} queries/NAME/question.sql
PYTHONPATH=core python -m schemalyser.audit build schemas/{schema} queries/NAME/question.sql --out audits/NAME
PYTHONPATH=core python -m schemalyser.testbed run --world fixtures --out runs/first
```

- `check` applies the import's rules to the folder and lists each rule with any fragment that broke it.
- `feasibility report` reads the question against the invented hospital schema and says, for each requirement, how far that schema supplies it, with the investigation that would move each one that falls short.
- `audit build` compiles the question through the invented hospital schema into the package that a database analyst would run, with the answer on made-up rows, and the safety report with its execution class.
- `testbed run` builds the invented world, runs its conversion to OMOP, checks the planted scenarios and reconciles source with target, all on invented data.

Every table that these reports name is a table of the invented world. On the hospital's side the same commands name the hospital's tables, which is why their reports stay there.
"""

NOTE_TEXT = """The neonatal low mean pressure audit measures, for each neonatal anaesthetic, how many minutes the mean arterial pressure spent below 40, and how many of the children died within 90 days. It reports counts for each band of minutes and no row of any patient.
"""


def planted_inputs(repo=ROOT):
    """The planted neonates as rows of the role views, which are development fixtures, without the expected answers,
    which are held out."""
    held = json.loads((Path(repo) / PLANTED_NEONATES).read_text(encoding="utf-8"))
    kept = {key: value for key, value in held.items() if key != "expectations"}
    kept["description"] = ("The planted neonates, written once as rows of the three role views. The answers that the "
                           "neonatal audit must give for them are held out of the public workspace.")
    return (json.dumps(kept, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def export(out, profile="public", repo=ROOT, allowlist=ALLOWLIST, include=(), date=None):
    """Writes the workspace to out, which must be empty or not yet exist. Returns the manifest."""
    if profile not in PROFILES:
        raise WorkspaceError(f"{profile} is not a profile of the export; the only profile is public.")
    repo, out = Path(repo).resolve(), Path(out).resolve()
    date = date or dt.date.today().isoformat()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise WorkspaceError(f"{out.name} already holds files, and the export writes only into an empty folder.")
    if out == repo or repo in out.parents and _denied(out.relative_to(repo).as_posix()):
        raise WorkspaceError(f"{out.name} lies where no workspace may be written.")
    files = sources(repo, allowlist, include)
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
    written[f"queries/{EXAMPLE}/{QUESTION}"] = ((repo / EXAMPLE_SOURCE).read_bytes(), EXAMPLE_SOURCE)
    written[f"queries/{EXAMPLE}/{TITLE}"] = (b"Neonatal low mean pressure\n", None)
    written[f"queries/{EXAMPLE}/{NOTE}"] = (NOTE_TEXT.encode("utf-8"), None)
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


# A question's folder.

def _rules(failures, names):
    return [{"id": rule, "rule": names[rule], "passed": not failures.get(rule), "fragments": failures.get(rule, [])}
            for rule in names]


def read_folder(folder):
    """Applies the folder's rules. Returns (rules, {name: text}) with the texts of the accepted files that were read."""
    folder = Path(folder)
    failures, texts = {}, {}

    def fail(rule, fragment):
        failures.setdefault(rule, []).append(fragment)

    if folder.is_symlink() or not folder.is_dir():
        fail("folder", f"{folder.name} is not a folder")
        return _rules(failures, FOLDER_RULES), texts
    if not SAFE_NAME.match(folder.name):
        fail("folder", "the folder's name uses characters other than letters, digits, full stops, hyphens and underscores")
    present = set()
    for entry in sorted(os.scandir(folder), key=lambda e: e.name):
        name = entry.name
        if entry.is_symlink():
            fail("links", f"{name} is a symbolic link")
            continue
        if entry.is_dir(follow_symlinks=False):
            fail("files", f"{name} is a folder")
            continue
        if name == RESULT:
            # The import's own result from an earlier import, which is never read.
            continue
        if DEPENDENCY_NAMES.match(name):
            fail("dependencies", f"{name} declares dependencies")
            continue
        if name not in ACCEPTED_FILES:
            fail("files", f"{name} is not one of question.sql, title.txt and note.md")
            continue
        present.add(name)
        if entry.stat(follow_symlinks=False).st_mode & 0o111:
            fail("executable", f"{name} is executable")
            continue
        size = entry.stat(follow_symlinks=False).st_size
        if size > SIZE_LIMIT:
            fail("size", f"{name} holds {size:,} bytes")
            continue
        data = Path(entry.path).read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            fail("text", f"{name} is not UTF-8 text")
            continue
        if "\0" in text:
            fail("text", f"{name} holds a NUL character")
            continue
        texts[name] = text
    for name in ACCEPTED_FILES:
        if name not in present:
            fail("files", f"{name} is missing")
    title = texts.get(TITLE)
    if title is not None:
        lines = [line for line in title.splitlines() if line.strip()]
        if len(lines) != 1 or len(lines[0].strip()) > TITLE_LIMIT or any(ord(c) < 32 for c in lines[0]):
            fail("title", "the title is not one line of plain text" if len(lines) != 1 else f"the title holds {len(lines[0].strip())} characters")
    return _rules(failures, FOLDER_RULES), texts


def check_folder(folder):
    """The folder's rules and, when they pass, the role-level policy on question.sql, with the status that the import
    returns, as {"rules", "failed", "status", "form", "texts"}.

    The status is a function of the package and the public contract alone: malformed where a rule fails,
    requires_private_review where the question passes every rule but lacks the form of the two-part script (a step
    that chooses the anaesthetics, every part reached from it by key, and counts in the result; audit.public_form),
    and accepted otherwise. No hospital schema is read, so the workspace's own check and the import give the same
    status for the same package, whichever hospital the project holds."""
    rules, texts = read_folder(folder)
    form = []
    if all(r["passed"] for r in rules):
        rules = rules + rolepolicy.check(texts[QUESTION])["rules"]
    failed = [r["id"] for r in rules if not r["passed"]]
    if failed:
        status = "malformed"
    else:
        form = audit.public_form(texts[QUESTION])
        status = "requires_private_review" if form else "accepted"
    return {"rules": rules, "failed": failed, "status": status, "form": form, "texts": texts}


def _schema_path(project, name):
    if name:
        return project.schema_path(name)
    held = project.schemas()
    if len(held) != 1:
        raise WorkspaceError("The project holds no saved hospital schema; add one to schemas/ first." if not held else
                             "The project holds more than one saved hospital schema, so name one with --schema.")
    return project.schema_path(held[0])


def _write_result(folder, result):
    target = Path(folder) / RESULT
    if target.is_symlink() or target.exists():
        target.unlink()
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def import_question(folder, hospital, schema=None, date=None):
    """Imports one question's folder into a hospital's project. Returns the counts-only result, which is also written
    into the folder as import-result.json."""
    from .project import Project, ProjectError
    folder = Path(folder)
    date = date or dt.date.today().isoformat()
    name = folder.name if SAFE_NAME.match(folder.name) else None
    checked = check_folder(folder)
    result = {"query": name, "result": "malformed", "rules_failed": checked["failed"], "says": WORDING["malformed"]}
    if checked["failed"]:
        if not folder.is_symlink() and folder.is_dir():
            _write_result(folder, result)
        return result, checked, None
    texts = checked["texts"]
    try:
        project = Project(hospital)
        schema_path = _schema_path(project, schema)
        feasibility.Schema.load(schema_path)
    except (ProjectError, feasibility.FeasibilityError) as error:
        raise WorkspaceError(str(error)) from None
    # The status is decided here, from the package and the public contract alone, before the hospital schema is read
    # for anything, and nothing that follows changes it.
    status = checked["status"]
    result.update(result=status, rules_failed=[], says=WORDING[status])
    title = " ".join(texts[TITLE].split())
    question = project.add_question(title, texts[QUESTION])
    question_path = project.question_path(question)
    audit_name = project.unique("audits", f"{Path(question).stem}_{date}")
    record = project.folder("audits") / audit_name
    record.mkdir()
    # The private build runs after the status is decided. What it finds, and any error it meets, goes to the owner's
    # feasibility report and validation report in the project, never to the status.
    found, manifest, failure = None, None, None
    try:
        found = feasibility.assess(feasibility.Schema.load(schema_path), question_path.read_text(encoding="utf-8"), question)
        manifest = audit.build(schema_path, question_path, record / "package", date=date)
    except Exception as error:  # noqa: BLE001 - nothing the build meets may reach the status or the workspace
        failure = {"error": type(error).__name__, "says": str(error)}
    built = manifest is not None
    states = {}
    for row in (found or {}).get("states", []):
        states[row["state"]] = states.get(row["state"], 0) + 1
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    (record / "request.json").write_text(json.dumps(
        {"schema": schema_path.name, "question": question, "period": None, "decisions": [], "exact_small_numbers": False,
         "imported_from": f"queries/{name}", "exit_code": 0 if built else 1, "finished": stamp}, indent=2) + "\n",
        encoding="utf-8")
    report = {"imported": date, "from": f"queries/{name}", "files": {k: sha256(v) for k, v in sorted(texts.items())},
              "title": title, "question": question, "schema": schema_path.name, "rules": checked["rules"],
              "public_form": checked["form"],
              "feasibility": {"verdict": found["verdict"], "says": found["verdict_text"], "states": states} if found else None,
              "package": "package" if built else None, "build_failure": failure,
              "execution_class": manifest["execution_class"] if manifest else None,
              "returned_to_workspace": result}
    (record / "import-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_result(folder, result)
    return result, checked, record


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
    two = commands.add_parser("check", help="Apply the import's rules to a question's folder.")
    two.add_argument("folder")
    three = commands.add_parser("import", help="Import a question's folder into a hospital's project.")
    three.add_argument("folder")
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
            found = check_folder(args.folder)
            _print_rules(found["rules"])
            print("The folder passes every rule of the import." if not found["failed"] else
                  f"The folder fails {len(found['failed'])} of the import's rules.")
            print(f"The import would return the status {found['status']}.")
            return 0 if not found["failed"] else 1
        result, checked, _ = import_question(args.folder, args.hospital, args.schema)
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
