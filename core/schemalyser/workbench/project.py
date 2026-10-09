"""The project folder: what it holds, and where each command writes.

    FOLDER/
      workbench.json        the folder's name and the tool's version, and nothing else
      schemas/              saved hospital schemas (hospital-schema.schemalyser.zip and others)
      questions/            questions over the parts of the record, one .sql file each, with a leading comment as title
      audits/NAME/          one audit: request.json, log.txt, decisions.json, plans/ and package/, which the audit
                            command wrote
      runs/NAME/            one test on made-up rows: run.json, log.txt and the testbed's own output
      .tmp/                 temporary files of the core, kept inside the project so that nothing leaves it
"""
import json
import re
import shutil
from pathlib import Path

from .. import audit, feasibility

SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
FOLDERS = ("schemas", "questions", "audits", "runs")


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
            feasibility.Schema.load(temporary)
        except feasibility.FeasibilityError as error:
            temporary.unlink(missing_ok=True)
            raise ProjectError(str(error)) from None
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


def read_json(path):
    return _read_json(path)
