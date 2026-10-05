"""Puts everything the page needs at run time into public/, so that the site serves it all itself.

    public/pyodide/   the Pyodide runtime, copied from node_modules
    public/py/        the sqlglot wheel and the schemalyser core as a zip
    public/example/   the invented example, taken from fixtures/, with manifest.json listing its files
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

with zipfile.ZipFile(py_out / "schemalyser.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    # The whole package, as the boundary's container copies it: the core, the OMOP field list that the
    # checklists read, and the public reference data that the sandbox uses for realistic values. Taking
    # the same files means that the page's provenance.json names the same fingerprint of the code as
    # the container's (boundary.tool_digest).
    for path in sorted(CORE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo") \
                or path.name.startswith("."):
            continue
        info = zipfile.ZipInfo(f"schemalyser/{path.relative_to(CORE).as_posix()}", date_time=(1980, 1, 1, 0, 0, 0))
        archive.writestr(info, path.read_bytes())

print("assets prepared in", SITE / "public")

# The invented example: the invented world's state and requests, exactly as the tests use them, so that a person can
# see the whole checklist working without bringing any file. Only the files that the page reads are copied.
FIXTURES = SITE.parent / "fixtures"
example_out = SITE / "public" / "example"
shutil.rmtree(example_out, ignore_errors=True)
copies = {"state/catalogue.csv": FIXTURES / "invented-catalogue.csv",
          "state/site-rules.json": FIXTURES / "invented-site-rules.json",
          "state/checks.csv": FIXTURES / "invented-checks.csv",
          "state/core-profile.csv": FIXTURES / "profile" / "invented-core-profile.csv"}
for folder, prefix in ((FIXTURES / "conversion", "state/conversion"), (FIXTURES / "targets", "state/targets"),
                       (FIXTURES / "requests", "requests")):
    for path in sorted(folder.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.startswith("."):
            copies[f"{prefix}/{path.relative_to(folder).as_posix()}"] = path
for published, source in copies.items():
    (example_out / published).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, example_out / published)
manifest = {"state": sorted(p for p in copies if p.startswith("state/")),
            "requests": sorted(p for p in copies if p.startswith("requests/"))}
(example_out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
