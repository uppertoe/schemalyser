"""Stage 4 of the examination, the planted cases: a question run on rows written by a person, with the answer written
by hand and held out, under a fresh transform of every key and date, so that an answer written into the question
cannot pass.

A case is rows and an expected answer. The catalogue's capabilities and the neonatal audit keep their cases in the
repository, rows public and answers held out of the workspace; a free question keeps both in the private examiner
root, the folder that SCHEMALYSER_EXAMINATION names or reference/examination/questions, which the workspace export
denies. The repository commits only the register, fixtures/examination-register.json, with each case's name, the hash
of each of its files and the date it was written, and a case whose files no longer match the register is refused, so
that no case changes after a question was examined against it without the register showing it.

Before a case is run, every value of a column of the type key is replaced through a random bijection, and every date
and time is shifted by one random whole number of days, with the expected answer transformed alike; counts and
durations are unchanged. A question that names a key or a date of the planted range therefore fails.
"""
import datetime as dt
import hashlib
import json
import os
import random
import re
from pathlib import Path

from .. import equivalence, rolemap, roleshadow

REPO = Path(__file__).resolve().parents[3]
REGISTER = REPO / "fixtures" / "examination-register.json"
PUBLIC_ROWS = REPO / "fixtures" / "questions"
HELD_OUT = REPO / "fixtures" / "held-out"
STAGE = "planted"
KINDS = ("question", "capability", "audit")


def private_root():
    """The private examiner root: the folder SCHEMALYSER_EXAMINATION names, or reference/examination/questions."""
    named = os.environ.get("SCHEMALYSER_EXAMINATION")
    return Path(named) if named else REPO / "reference" / "examination" / "questions"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def case_files(name, kind="question"):
    """The files of a case, as {label: path}: the rows and the expected answer of a question, from the repository's
    public rows and held-out answers or from the private root; a capability's planted rows and held-out answer; or
    the neonatal audit's planted file, which holds its rows and its expectations together."""
    if kind == "audit":
        return {"planted": rolemap.PLANTED}
    if kind == "capability":
        return {"rows": rolemap.MODEL / "capabilities" / "planted.json", "expected": HELD_OUT / "capabilities" / name / "expected.json"}
    public = PUBLIC_ROWS / name / "rows.json"
    if public.is_file():
        return {"rows": public, "expected": HELD_OUT / "questions" / name / "expected.json"}
    return {"rows": private_root() / name / "rows.json", "expected": private_root() / name / "expected.json"}


def read_register(path=None):
    path = Path(path) if path else REGISTER
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"cases": {}}


def register_entry(name, kind="question", date=None):
    """The register's entry for a case as its files are now: the kind, the hash of each file and the date."""
    files = case_files(name, kind)
    missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        raise CaseError(f"The case {name} has no file at {', '.join(missing)}.")
    return {"kind": kind, "files": {label: sha256(p) for label, p in files.items()},
            "date": date or dt.date.today().isoformat()}


def register(name, kind="question", path=None, date=None):
    """Writes or renews the register's entry for a case. Returns the entry."""
    path = Path(path) if path else REGISTER
    held = read_register(path)
    held["cases"][name] = register_entry(name, kind, date)
    held["cases"] = dict(sorted(held["cases"].items()))
    path.write_text(json.dumps(held, indent=1) + "\n", encoding="utf-8")
    return held["cases"][name]


class CaseError(ValueError):
    """A case that cannot be run: unregistered, changed since it was registered, or missing a file."""


def load_case(name, register_path=None):
    """The case's rows and expected answer, checked against the register. Returns (kind, rows, expected), where rows
    is {view: [row, ...]} in the contract's column order and expected is the held-out file's content."""
    held = read_register(register_path)["cases"].get(name)
    if held is None:
        raise CaseError(f"The case {name} is not in the register, so the examiner does not run it: register it with python -m schemalyser.examination register {name}.")
    kind = held["kind"]
    files = case_files(name, kind)
    for label, path in files.items():
        if not path.is_file():
            raise CaseError(f"The case {name} has no {label} file at {path}.")
        if sha256(path) != held["files"].get(label):
            raise CaseError(f"The case {name} has changed since it was registered on {held['date']}: its {label} file no longer matches the register, so the examiner does not run it.")
    if kind == "audit":
        data = json.loads(files["planted"].read_text(encoding="utf-8"))
        rows = {view: data[view]["rows"] for view in ("role_patient", "role_anaesthetic", "role_reading")}
        return kind, rows, {"expectations": data["expectations"]}
    if kind == "capability":
        data = json.loads(files["rows"].read_text(encoding="utf-8"))[name]
        return kind, data["rows"], dict(json.loads(files["expected"].read_text(encoding="utf-8")), parameters=data["parameters"])
    data = json.loads(files["rows"].read_text(encoding="utf-8"))
    return kind, data["roles"], json.loads(files["expected"].read_text(encoding="utf-8"))


# The transform.

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")


class Transform:
    """A fresh bijection of keys and one shift of dates, from a seed."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.days = self.rng.randrange(400, 3000) * self.rng.choice((-1, 1))
        self.keys = {}

    def key(self, value):
        if value is None:
            return None
        if value not in self.keys:
            self.keys[value] = f"K{len(self.keys) + 1:06d}{self.rng.randrange(1000):03d}"
        return self.keys[value]

    def when(self, value):
        if not isinstance(value, str):
            return value
        if DATE.match(value):
            return (dt.date.fromisoformat(value) + dt.timedelta(days=self.days)).isoformat()
        if DATETIME.match(value):
            sep = "T" if "T" in value else " "
            moment = dt.datetime.fromisoformat(value.replace("T", " ")) + dt.timedelta(days=self.days)
            return moment.isoformat(sep=sep, timespec="seconds" if len(value) >= 19 else "minutes")
        return value

    def rows(self, rows, model=None):
        """The rows of every view transformed: key columns through the bijection, date columns shifted."""
        model = model or rolemap.contract()
        types = {v["name"]: [c["type"] for c in v["columns"]] for v in model["views"]}
        found = {}
        for view, held in rows.items():
            kinds = types.get(view, [])
            out = []
            for row in held:
                out.append([self.key(v) if k == "key" else self.when(v) if k in ("date", "datetime") else v
                            for v, k in zip(row, kinds + ["text"] * (len(row) - len(kinds)))])
            found[view] = out
        return found

    def answer(self, columns, rows):
        """An expected answer transformed: a column named as a key is mapped, a date is shifted, the rest stand."""
        found = []
        for row in rows:
            found.append([self.key(v) if str(c).endswith("_key") and v is not None else self.when(v) for c, v in zip(columns, row)])
        return found


# The comparison.

def comparable(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return str(value)


def _normal(columns, rows):
    return columns, [[comparable(v) for v in row] for row in rows]


def run_case(sql, name, seed=1, register_path=None, final=None, per_grain=None):
    """A question's SQL run on a case under a fresh transform, compared with the held-out answer. Returns the case's
    report: its name, kind and seed, the outcome (passed, failed or refused), the comparison's counts and a sentence.
    For a question, the final answer is compared with expected["answer"], and, where the expected file names the step
    that gives one row per grain under per_<grain> with "step", those rows as well; for a capability, its rows per
    anaesthetic are compared on the columns the expected file names; for the audit, each planted neonate's minutes
    and death are compared through its banded step."""
    try:
        kind, rows, expected = load_case(name, register_path)
    except CaseError as error:
        return {"name": name, "seed": seed, "outcome": "refused", "says": str(error)}
    transform = Transform(seed)
    moved = transform.rows(rows)
    con = roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=moved)
    run = roleshadow.duckdb_runner(con)
    try:
        if kind == "audit":
            return _audit_case(run, sql, expected, transform, name, seed)
        if kind == "capability":
            return _capability_case(run, sql, expected, transform, name, seed)
        return _question_case(run, sql, expected, transform, name, seed)
    except Exception as error:  # noqa: BLE001 - a question that does not run on the case fails it
        return {"name": name, "seed": seed, "outcome": "failed", "says": f"The question did not run on the case {name}: {type(error).__name__}."}
    finally:
        con.close()


def _verdict(name, seed, comparisons):
    same = all(c["same"] for c in comparisons.values())
    words = "; ".join(f"{label}: {c['rows_shared']} of {c['rows_b']} expected rows matched by {c['rows_a']} returned" for label, c in comparisons.items())
    return {"name": name, "seed": seed, "outcome": "passed" if same else "failed", "compared": comparisons,
            "says": (f"The question gives the held-out answer of the case {name} under a fresh transform ({words})." if same else
                     f"The question does not give the held-out answer of the case {name} under a fresh transform ({words}).")}


def _question_case(run, sql, expected, transform, name, seed):
    comparisons = {}
    answer = expected.get("answer") or {}
    if answer.get("columns"):
        got = run(sql)
        want = (answer["columns"], transform.answer(answer["columns"], answer["rows"]))
        comparisons["answer"] = equivalence.compare_answers(_normal(*got), _normal(*want))
    for label, held in expected.items():
        if label.startswith("per_") and isinstance(held, dict) and held.get("step") and held.get("columns"):
            got = run(_from_step(sql, held["step"], held["columns"]))
            want = (held["columns"], transform.answer(held["columns"], held["rows"]))
            comparisons[label] = equivalence.compare_answers(_normal(*got), _normal(*want))
    if not comparisons:
        return {"name": name, "seed": seed, "outcome": "refused", "says": f"The case {name} names no answer to compare: its expected file gives no answer columns and no step."}
    return _verdict(name, seed, comparisons)


def _from_step(sql, step, columns):
    import sqlglot
    from .. import compiler
    tree = compiler.check_audit(sql, admit="draft")
    final = sqlglot.parse_one("SELECT " + ", ".join(f"x.{c}" for c in columns) + f" FROM {step} x", dialect="tsql")
    final.set("with_", (tree.args.get("with_")).copy())
    return final.sql(dialect="tsql")


def _capability_case(run, sql, expected, transform, name, seed):
    columns, rows = run(sql)
    at = [columns.index(c) for c in expected["columns"] if c in columns]
    if len(at) != len(expected["columns"]):
        return {"name": name, "seed": seed, "outcome": "failed", "says": f"The capability does not return the columns the case {name} expects: {', '.join(expected['columns'])}."}
    got = (expected["columns"], [[row[i] for i in at] for row in rows])
    want = (expected["columns"], transform.answer(expected["columns"], expected["rows"]))
    return _verdict(name, seed, {"per_anaesthetic": equivalence.compare_answers(_normal(*got), _normal(*want))})


def _audit_case(run, sql, expected, transform, name, seed):
    found = roleshadow.per_anaesthetic(run, sql)
    got = ("anaesthetic_key", "minutes_below_40", "died_within_90_days"), [[k, None if m is None else float(m), int(d)] for k, (m, d) in found.items()]
    counted = [e for e in expected["expectations"] if e.get("counted")]
    want_rows = [[transform.key(e["anaesthetic_key"]), None if e["minutes_below_40"] is None else float(e["minutes_below_40"]), int(e["died_within_90_days"])]
                 for e in counted]
    comparisons = {"per_anaesthetic": equivalence.compare_answers(_normal(*got), _normal(*(got[0], want_rows)))}
    # The band each neonate falls in, read from the audit's own banded step and named as its final SELECT names it,
    # against the band the case expects.
    words = _band_words(sql)
    if words and all("band" in e for e in counted):
        columns, rows = run(_from_step(sql, "banded", ["anaesthetic_key", "band"]))
        bands = (columns, [[k, words.get(int(b), b)] for k, b in rows])
        want = (columns, [[transform.key(e["anaesthetic_key"]), e["band"]] for e in counted])
        comparisons["band"] = equivalence.compare_answers(_normal(*bands), _normal(*want))
    return _verdict(name, seed, comparisons)


def _band_words(sql):
    """{band number: its words} from a CASE of the question that names each band, or {} where it has none."""
    import sqlglot
    from sqlglot import exp
    tree = sqlglot.parse_one(sql, dialect="tsql")
    for case in tree.find_all(exp.Case):
        found = {}
        for branch in case.args.get("ifs") or []:
            test, words = branch.this, branch.args.get("true")
            if isinstance(test, exp.EQ) and isinstance(test.expression, exp.Literal) and isinstance(words, exp.Literal) and words.is_string \
                    and isinstance(test.this, exp.Column) and test.this.name.lower() == "band":
                found[int(test.expression.this)] = words.this
        if found:
            return found
    return {}
