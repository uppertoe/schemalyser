"""The core's commands, started as subprocesses whose output and record stay in their own folder of the project.

A job writes everything it prints to log.txt in its folder, and its record (request.json or run.json) gains
"exit_code" and "finished" when it ends. A record without them whose process this server did not start is shown as
having stopped before it finished, as happens when the workbench is closed during a run.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
_running = {}
_lock = threading.Lock()


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def environment(project):
    """The environment of a command: the core importable, and every temporary file inside the project folder."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(PACKAGE_PARENT), env.get("PYTHONPATH")) if p)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("TMPDIR", "TEMP", "TMP"):
        env[key] = str(project.tmp)
    return env


def module(name, *args):
    return [sys.executable, "-m", f"schemalyser.{name}", *[str(a) for a in args]]


def run_now(project, command):
    """Runs a short command to its end. Returns (exit code, what it printed)."""
    done = subprocess.run(command, cwd=PACKAGE_PARENT, env=environment(project), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    return done.returncode, (done.stdout + done.stderr).strip()


def start(project, folder, record_name, command):
    """Starts a command in the background. Its output goes to folder/log.txt, and its record to folder/record_name."""
    folder = Path(folder)
    record_path = folder / record_name
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # The record names the files of the command relative to the project folder, and the interpreter by its name.
    shown = ["python"] + [str(c).replace(str(project.root), ".") for c in command[1:]]
    record.update(started=now(), command=shown)
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    log = (folder / "log.txt").open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=PACKAGE_PARENT, env=environment(project), stdout=log,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    with _lock:
        _running[str(folder)] = process

    def wait():
        code = process.wait()
        log.close()
        held = json.loads(record_path.read_text(encoding="utf-8"))
        held.update(exit_code=code, finished=now())
        record_path.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
        with _lock:
            _running.pop(str(folder), None)

    threading.Thread(target=wait, daemon=True).start()
    return process


def state(folder, record):
    """running, finished, failed or interrupted, from the record and the processes that this server started."""
    if record.get("exit_code") is not None:
        return "finished" if record["exit_code"] == 0 else "failed"
    with _lock:
        if str(Path(folder)) in _running:
            return "running"
    return "interrupted" if record.get("started") else "not started"


def log_tail(folder, lines=60):
    path = Path(folder) / "log.txt"
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def wait_all(timeout=None):
    """Waits for every job this server started; the tests use it."""
    with _lock:
        processes = list(_running.values())
    for process in processes:
        process.wait(timeout=timeout)
    # The thread that records the end runs just after the process ends.
    for _ in range(200):
        with _lock:
            if not _running:
                return
        threading.Event().wait(0.05)
