"""Puts everything the page needs at run time into public/, so that the site serves it all itself.

    public/pyodide/   the Pyodide runtime, copied from node_modules
    public/py/        the sqlglot wheel and the schemalyser core as a zip
    public/example/   the invented dictionary and the invented hospital, taken from fixtures/
"""
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
CORE = SITE.parent / "core" / "schemalyser"
SQLGLOT = "sqlglot==30.21.0"
SQLGLOT_SHA256 = "816d1a4815b7562b3976efcc0b561c928a743f976efd0fb8ad6db7a5b96c069e"
PYODIDE_FILES = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]

pyodide_out = SITE / "public" / "pyodide"
py_out = SITE / "public" / "py"
pyodide_out.mkdir(parents=True, exist_ok=True)
py_out.mkdir(parents=True, exist_ok=True)

for name in PYODIDE_FILES:
    shutil.copyfile(SITE / "node_modules" / "pyodide" / name, pyodide_out / name)

# DuckDB for the sandbox: the wheel that this version of Pyodide names in its lock file, checked
# against the lock file's own SHA-256 and served beside the runtime.
lock = json.loads((SITE / "node_modules" / "pyodide" / "pyodide-lock.json").read_text())
duckdb = lock["packages"]["duckdb"]
duckdb_out = pyodide_out / duckdb["file_name"]
if not duckdb_out.exists():
    cached = SITE / "node_modules" / "pyodide" / duckdb["file_name"]
    if cached.exists():
        data = cached.read_bytes()
    else:
        version = json.loads((SITE / "node_modules" / "pyodide" / "package.json").read_text())["version"]
        with urllib.request.urlopen(f"https://cdn.jsdelivr.net/pyodide/v{version}/full/{duckdb['file_name']}") as response:
            data = response.read()
    if hashlib.sha256(data).hexdigest() != duckdb["sha256"]:
        raise SystemExit("The DuckDB wheel does not match the checksum in Pyodide's lock file.")
    duckdb_out.write_bytes(data)

wheel = py_out / "sqlglot.whl"
if not wheel.exists():
    download = py_out / "download"
    subprocess.run([sys.executable, "-m", "pip", "download", SQLGLOT, "--no-deps", "--quiet",
                    "--disable-pip-version-check", "-d", str(download)], check=True)
    (found,) = download.glob("sqlglot-*-py3-none-any.whl")
    found.rename(wheel)
    shutil.rmtree(download)
# The wheel is checked every time, whether it was just downloaded or was already there.
if hashlib.sha256(wheel.read_bytes()).hexdigest() != SQLGLOT_SHA256:
    raise SystemExit("The sqlglot wheel does not match its pinned checksum.")

REPO = SITE.parent


def tracked(folder):
    """The files under folder that git tracks, as absolute paths. The page is built from those alone, so that a file a
    command wrote into the repository over a hospital's material is never published because it happens to sit there."""
    relative = Path(folder).resolve().relative_to(REPO).as_posix()
    try:
        done = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--", relative],
                              capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("git could not list the tracked files, which the build needs; build from a checkout.")
    found = [REPO / name for name in done.stdout.decode("utf-8").split("\0") if name]
    if not found:
        raise SystemExit(f"git tracks nothing under {relative}, so there is nothing to build from.")
    return found


with zipfile.ZipFile(py_out / "schemalyser.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    # The whole package as git tracks it: the core, the role model, the OMOP field list and the public reference data
    # that the synthetic world uses for realistic values.
    for path in tracked(CORE):
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo") or path.name.startswith("."):
            continue
        info = zipfile.ZipInfo(f"schemalyser/{path.relative_to(CORE).as_posix()}", date_time=(1980, 1, 1, 0, 0, 0))
        archive.writestr(info, path.read_bytes())

print("assets prepared in", SITE / "public")

FIXTURES = SITE.parent / "fixtures"
example_out = SITE / "public" / "example"
shutil.rmtree(example_out, ignore_errors=True)

# The invented data dictionary and its tables file, which the describe page loads at step 2 when a person wants to try
# the page before using a real dictionary.
(example_out / "dictionary").mkdir(parents=True, exist_ok=True)
for name in ("invented-dictionary.csv", "invented-tables.csv"):
    if FIXTURES / "dictionary" / name not in tracked(FIXTURES / "dictionary"):
        raise SystemExit(f"git does not track fixtures/dictionary/{name}, so the build cannot publish it.")
    shutil.copyfile(FIXTURES / "dictionary" / name, example_out / "dictionary" / name)

# The invented hospital, on which the describe page runs its own queries when the invented dictionary is in use: the
# invented world's tables and rows as fixtures/make_hospital.py writes them, and the invented catalogue that gives the
# database's own records of its tables. Every value is invented. The page fetches these files beside the invented
# dictionary while the tab is online, and the worker builds the database from them.
hospital_out = example_out / "hospital"
for path in tracked(FIXTURES / "hospital"):
    target = hospital_out / path.relative_to(FIXTURES / "hospital")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
if FIXTURES / "invented-catalogue.csv" not in tracked(FIXTURES):
    raise SystemExit("git does not track fixtures/invented-catalogue.csv, so the build cannot publish it.")
shutil.copyfile(FIXTURES / "invented-catalogue.csv", hospital_out / "catalogue.csv")
