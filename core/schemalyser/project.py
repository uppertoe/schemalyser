"""The project folder: what it holds, and where each command writes.

    FOLDER/
      workbench.json        the folder's name and the tool's version, and nothing else
      schemas/              saved hospital schemas (hospital-schema.schemalyser.zip and others)
      questions/            questions over the parts of the record, one .sql file each, with a leading comment as title
      audits/NAME/          one audit: request.json, log.txt, decisions.json, plans/ and package/, which the audit
                            command wrote
      runs/NAME/            one test on made-up rows: run.json, log.txt and the testbed's own output
      exports/NAME/         one export of the anaesthesia record: request.json, episodes.csv (the private episode
                            list), specification.json, log.txt, package/ (which the audit's export command wrote),
                            returned/ (the files that the database analyst returned) and results/ (the results
                            packages written from them)
      specifications/       the specifications saved for reuse, which hold no hospital material
      .tmp/                 temporary files of the core, kept inside the project so that nothing leaves it
"""
import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import audit, feasibility, specification
from .vocabulary import EXPORT_SCREEN as EXPORT_WORDING  # noqa: F401 - the export screen reads its wording from here

SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
FOLDERS = ("schemas", "questions", "audits", "runs", "exports", "specifications")
EXPORT_RECORD = "request.json"
EPISODES = "episodes.csv"
SPECIFICATION = "specification.json"
PACKAGE = "package"
SUPPORT = "support.json"
RETURNED = ("outcome", "plan", "result")
# The record of a results package, as results.RESULTS names it.
RESULTS = "results.json"


class ProjectError(ValueError):
    """A request that the project folder cannot satisfy; the message says why in one sentence."""


def safe_name(name, suffix=""):
    """A file or folder name that stays inside its folder, or ProjectError."""
    name = (name or "").strip()
    if not SAFE.match(name) or ".." in name or (suffix and not name.lower().endswith(suffix)):
        raise ProjectError(f"{name or 'The name'} is not a name that the workbench can use: it uses letters, digits, "
                           f"full stops, hyphens and underscores, and ends in {suffix}." if suffix else
                           f"{name or 'The name'} is not a name that the workbench can use: it uses letters, digits, "
                           f"full stops, hyphens and underscores.")
    return name


def slug(text, fallback="item"):
    words = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return words[:60].rstrip("_") or fallback


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in FOLDERS:
            (self.root / name).mkdir(exist_ok=True)
        self.tmp = self.root / ".tmp"
        self.tmp.mkdir(exist_ok=True)
        marker = self.root / "workbench.json"
        held = _read_json(marker) or {}
        wanted = {"name": self.root.name, "tool_version": audit.tool_version()}
        if held != wanted:
            marker.write_text(json.dumps(wanted, indent=2) + "\n", encoding="utf-8")

    @property
    def name(self):
        return self.root.name

    def folder(self, kind):
        return self.root / kind

    def unique(self, kind, base, suffix=""):
        """A new name in a folder of the project, from base, that nothing there holds yet."""
        base = slug(base)
        name, number = base, 2
        while (self.root / kind / (name + suffix)).exists():
            name, number = f"{base}_{number}", number + 1
        return name + suffix

    # Saved hospital schemas.

    def schemas(self):
        return sorted(p.name for p in self.folder("schemas").glob("*.zip") if p.is_file())

    def schema_path(self, name):
        path = self.folder("schemas") / safe_name(name, ".zip")
        if not path.is_file():
            raise ProjectError(f"The project holds no saved hospital schema named {name}.")
        return path

    def add_schema(self, filename, data):
        name = safe_name(Path(filename or "").name, ".zip")
        target = self.folder("schemas") / name
        temporary = self.tmp / f"upload-{name}"
        temporary.write_bytes(data)
        try:
            schema = feasibility.Schema.load(temporary)
            # Each save of a hospital schema is a version of its own, so a file of the same name that holds a different
            # version is never overwritten.
            if target.exists() and feasibility.Schema.load(target).schema_id != schema.schema_id:
                raise ProjectError(f"The project already holds a different version of the hospital schema named {name}, "
                                   "so the workbench has not replaced it. Save the new version under the name the page "
                                   "gives it, which carries its version, then add it again.")
        except feasibility.FeasibilityError as error:
            temporary.unlink(missing_ok=True)
            raise ProjectError(str(error)) from None
        except ProjectError:
            temporary.unlink(missing_ok=True)
            raise
        shutil.move(str(temporary), target)
        return name

    # Questions.

    def questions(self):
        found = []
        for path in sorted(self.folder("questions").glob("*.sql")):
            text = path.read_text(encoding="utf-8", errors="replace")
            found.append({"name": path.name, "title": feasibility.question_text(text) or path.stem.replace("_", " ")})
        return found

    def question_path(self, name):
        path = self.folder("questions") / safe_name(name, ".sql")
        if not path.is_file():
            raise ProjectError(f"The project holds no question named {name}.")
        return path

    def add_question(self, title, sql):
        title = " ".join((title or "").split())
        sql = (sql or "").strip()
        if not title or not sql:
            raise ProjectError("A question needs a title and its SQL.")
        name = self.unique("questions", title, ".sql")
        lines = [f"-- {title}" + ("" if title.endswith(("?", ".")) else "."), sql, ""]
        (self.folder("questions") / name).write_text("\n".join(lines), encoding="utf-8")
        return name

    # Audits and runs, each a folder.

    def audits(self):
        return self._records("audits", "request.json")

    def runs(self):
        return self._records("runs", "run.json")

    def _records(self, kind, record):
        found = []
        for folder in sorted(self.folder(kind).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            held = _read_json(folder / record) if folder.is_dir() else None
            if held is not None:
                found.append(dict(held, name=folder.name))
        return found

    def record_folder(self, kind, name):
        folder = self.folder(kind) / safe_name(name)
        if not folder.is_dir():
            raise ProjectError(f"The project holds nothing named {name} in {kind}.")
        return folder

    # Exports of the anaesthesia record, each a folder, and the specifications saved for reuse.

    def exports(self):
        return self._records("exports", EXPORT_RECORD)

    def new_export(self, schema_name):
        """A new export from a saved hospital schema of the project. Returns its name."""
        self.schema_path(schema_name)
        name = self.unique("exports", f"export_{dt.date.today().isoformat()}")
        folder = self.folder("exports") / name
        folder.mkdir()
        _write_json(folder / EXPORT_RECORD, {"schema": schema_name, "created": dt.date.today().isoformat()})
        return name

    def export(self, name):
        """(folder, record) of an export."""
        folder = self.record_folder("exports", name)
        record = _read_json(folder / EXPORT_RECORD)
        if record is None:
            raise ProjectError(f"The project holds no export named {name}.")
        return folder, record

    def _update(self, name, **changes):
        folder, record = self.export(name)
        record.update(changes)
        _write_json(folder / EXPORT_RECORD, record)
        return record

    def add_episodes(self, name, data, form, window_hours="", several=""):
        """Keeps the private episode list of an export, once specification.read_episodes() has read it, as
        exports/NAME/episodes.csv, and records its form, count and hash with the rule for patient and date pairs. The
        specification of the export, if it has one, takes the same form and rule."""
        folder, _ = self.export(name)
        if form not in specification.FORMS:
            raise ProjectError("Choose whether the list holds anaesthetic keys or patient and date pairs.")
        rule = {"form": form}
        if form == "patient_dates":
            hours = (window_hours or "").strip()
            rule.update(window_hours=int(hours) if hours.isdigit() else hours,
                        several=several if several in specification.SEVERAL else specification.SEVERAL[0])
        temporary = folder / f".{EPISODES}.upload"
        temporary.write_bytes(data)
        try:
            held = specification.read_episodes(temporary, form)
        except specification.SpecificationError as error:
            temporary.unlink(missing_ok=True)
            raise ProjectError(str(error).replace(temporary.name, "The episode list")) from None
        shutil.move(str(temporary), folder / EPISODES)
        self._update(name, episodes={"form": held["form"], "count": held["count"], "sha256": held["sha256"]},
                     episode_rule=rule)
        spec = _read_json(folder / SPECIFICATION)
        if isinstance(spec, dict):
            spec["episodes"] = rule
            _write_json(folder / SPECIFICATION, spec)
        return held

    def write_specification(self, name, fields):
        """Writes the specification of an export from the screen's choices (specification.from_form), with the form
        and rule of its episode list, and returns (spec, the rules it breaks). A specification that breaks a rule is
        kept too, so that the screen shows the choices as made, and the package is not compiled from it."""
        folder, record = self.export(name)
        spec = specification.from_form(fields)
        if record.get("episode_rule"):
            spec["episodes"] = record["episode_rule"]
        _write_json(folder / SPECIFICATION, spec)
        return spec, specification.validate(spec)

    def specification_of(self, name):
        """The specification of an export as the screen shows it: {"spec", "reasons", "refusal", "sha256",
        "chosen", "form_differs"}, where chosen is specification.chosen() of it, or of nothing."""
        folder, record = self.export(name)
        path = folder / SPECIFICATION
        spec = _read_json(path)
        if not isinstance(spec, dict):
            return {"spec": None, "reasons": [], "refusal": None, "sha256": None, "chosen": specification.chosen(None),
                    "form_differs": None, "rules": specification.RULES}
        reasons = specification.validate(spec)
        held = (record.get("episodes") or {}).get("form")
        wanted = (spec.get("episodes") or {}).get("form")
        return {"spec": spec, "reasons": reasons, "refusal": specification.refusal(reasons) if reasons else None,
                "sha256": specification.sha256(path.read_bytes()), "chosen": specification.chosen(spec),
                "form_differs": {"held": held, "wanted": wanted} if held and wanted and held != wanted else None,
                "rules": specification.RULES}

    def saved_specifications(self):
        found = []
        for path in sorted(self.folder("specifications").glob("*.json")):
            spec = _read_json(path)
            found.append({"name": path.name, "title": (spec or {}).get("title") if isinstance(spec, dict) else None})
        return found

    def save_specification(self, name):
        """Saves the specification of an export for reuse in specifications/, under a name from its title. It holds no
        hospital material, so it may be shared. Returns the name."""
        folder, _ = self.export(name)
        state = self.specification_of(name)
        if state["spec"] is None or state["reasons"]:
            raise ProjectError("Only a specification that keeps every rule is saved for reuse.")
        target = self.unique("specifications", state["spec"].get("title") or "specification", ".json")
        shutil.copyfile(folder / SPECIFICATION, self.folder("specifications") / target)
        return target

    def load_specification(self, name, saved=None, data=None):
        """Replaces the specification of an export with a saved one, by its name in specifications/, or with the bytes
        of a specification file, once it keeps every rule. The export's rule for its episodes follows it."""
        folder, record = self.export(name)
        if saved:
            path = self.folder("specifications") / safe_name(saved, ".json")
            if not path.is_file():
                raise ProjectError(f"The project holds no saved specification named {saved}.")
            data = path.read_bytes()
        if not data:
            raise ProjectError("Choose a saved specification or a specification file to load.")
        temporary = folder / f".{SPECIFICATION}.upload"
        temporary.write_bytes(data)
        try:
            spec, _ = specification.load(temporary)
        except specification.SpecificationError as error:
            raise ProjectError(str(error)) from None
        finally:
            temporary.unlink(missing_ok=True)
        _write_json(folder / SPECIFICATION, spec)
        changes = {"episode_rule": spec["episodes"]}
        self._update(name, **changes)
        return spec

    def can_compile(self, name):
        folder, record = self.export(name)
        state = self.specification_of(name)
        return bool((folder / EPISODES).is_file() and state["spec"] is not None and not state["reasons"]
                    and not state["form_differs"])

    def export_command(self, name):
        """The arguments of the command that compiles an export's package: python -m schemalyser.audit export."""
        folder, record = self.export(name)
        return ["export", self.schema_path(record["schema"]), folder / SPECIFICATION, "--episodes", folder / EPISODES,
                "--out", folder / PACKAGE]

    def section_support(self, name):
        """feasibility.sections() of the export's saved hospital schema, kept beside the export with the hash of the
        schema it was read from, so that it is read again only when the schema or the tool changes."""
        folder, record = self.export(name)
        path = self.schema_path(record["schema"])
        key = {"schema_sha256": audit.sha256(path.read_bytes()), "tool_version": audit.tool_version()}
        held = _read_json(folder / SUPPORT)
        if isinstance(held, dict) and held.get("key") == key:
            return held["support"]
        found = feasibility.sections(feasibility.Schema.load(path))
        _write_json(folder / SUPPORT, {"key": key, "support": found})
        return json.loads(json.dumps(found, default=str))

    def export_status(self, name):
        folder, record = self.export(name)
        return audit.export_status(folder / PACKAGE, self.schema_path(record["schema"]), folder / SPECIFICATION,
                                   folder / EPISODES)

    def section_package(self, name, section):
        folder, _ = self.export(name)
        path = folder / PACKAGE / "sections" / safe_name(section)
        if not (path / audit.MANIFEST).is_file():
            raise ProjectError(f"The export holds no package for a section named {section}.")
        return path

    def keep_returned(self, name, section, kind, filename, data):
        """Keeps a file that the database analyst returned for a section, as returned/SECTION/TIME-KIND.EXT, and
        returns its path."""
        folder, _ = self.export(name)
        self.section_package(name, section)
        if kind not in RETURNED:
            raise ProjectError("The workbench keeps an outcome, a plan or a result.")
        suffix = Path(filename or "").suffix.lower()
        suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".txt"
        target = folder / "returned" / safe_name(section) / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{kind}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def import_command(self, name, section, form, path, by):
        """The arguments of the evidence import of a returned plan or outcome into the export's hospital schema:
        python -m schemalyser.describe import-evidence, with the request that the section's package carries. The
        import reads the latest version that an import of this export saved, or the schema that the export was
        compiled from, and writes the new version into a folder of its own in the export, from which kept_import()
        moves it into schemas/. Returns (the arguments, that folder)."""
        folder, record = self.export(name)
        package = self.section_package(name, section)
        requests = (_read_json(package / audit.REQUESTS) or {}).get("requests") or []
        request = next((r for r in requests if r.get("form") == form), None)
        if request is None:
            raise ProjectError(f"The package of {section} carries no request for its {form}.")
        out = folder / ".import" / f"{dt.datetime.now():%Y%m%d-%H%M%S-%f}"
        command = ["import-evidence", self.schema_path(record.get("evidence_schema") or record["schema"]),
                   package / audit.REQUESTS, path, "--id", request["request_id"], "--out", out]
        return command + (["--actor", by] if by else []), out

    def kept_import(self, name, out):
        """Moves the version of the hospital schema that an evidence import wrote to out into schemas/, records it as
        the export's latest version for the next import, and returns its name. The schema that the export's package
        was compiled from stays as it is, so that the package still binds to it."""
        found = sorted(Path(out).glob("*.zip"))
        if len(found) != 1:
            raise ProjectError("The evidence import saved no new version of the hospital schema.")
        target = self.folder("schemas") / found[0].name
        if target.exists() and target.read_bytes() != found[0].read_bytes():
            raise ProjectError(f"The project already holds a different file named {target.name}, so the workbench has not replaced it.")
        shutil.move(str(found[0]), target)
        shutil.rmtree(out, ignore_errors=True)
        self._update(name, evidence_schema=target.name)
        return target.name

    def results_command(self, name, section, path, by):
        """The arguments of the command that writes the results package of a returned result, python -m
        schemalyser.results write, into results/SECTION-TIME/ of the export."""
        folder, _ = self.export(name)
        package = self.section_package(name, section)
        out = folder / "results" / f"{safe_name(section)}-{dt.datetime.now():%Y%m%d-%H%M%S}"
        return ["write", package, path, "--out", out] + (["--by", by] if by else [])

    def results_of(self, name):
        """The latest results package of each section of an export, as {section: its path}. The workbench reads each
        with python -m schemalyser.results show, because the results package is the hospital's and its module is not
        one that the project folder's module imports."""
        folder, _ = self.export(name)
        found = {}
        held = folder / "results"
        if held.is_dir():
            for path in sorted(held.iterdir()):
                if (path / RESULTS).is_file():
                    found[path.name.rsplit("-", 2)[0]] = path
        return found


def read_json(path):
    return _read_json(path)


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def export_choices():
    """What the export screen offers, from the role contract and the catalogue alone (specification.choices)."""
    return specification.choices()
