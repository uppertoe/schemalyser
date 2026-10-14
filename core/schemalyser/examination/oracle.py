"""The oracle: the expected answer of a question worked out in Python from its examinable object over rows, with no
SQL, so that a package's answer can be compared with an answer the model never wrote.

The oracle filters the cohort by the examinable's grain, age bounds, period, exclusions and requirements; keeps each
outcome row by its link to the anaesthetic or the patient, its kinds, forms, flags and window; applies the rules on
amended rows and duplicates; computes the measure for each unit of the grain, exists, count, sum, min, max,
first_time or a composed capability of the catalogue run on a shadow of the rows alone; and takes the statistic, count,
proportion, sum, median or rows, in bands where the result names them. A question whose examinable says the oracle is
none is reported as needing cases written by hand.

The differential run reads the rows of the role-level shadow at several seeds, evaluates the examinable over them, and
compares the result with the package's own answer on the same rows.
"""
import datetime as dt
import statistics

from .. import capability, equivalence, rolemap, roleshadow

STAGE = "oracle"
MEASURES = ("exists", "count", "sum", "min", "max", "first_time")
STATISTICS = ("count", "proportion", "sum", "median", "rows")
TIME_COLUMNS = ("reading_time", "given_time", "event_time", "recorded_time", "placed_time", "taken_time", "transfer_time",
                "written_time", "found_time", "present_from")


class OracleError(ValueError):
    """An examinable the oracle cannot evaluate."""


def _when(value):
    if value is None or isinstance(value, (dt.datetime, dt.date)):
        return value
    text = str(value).replace("T", " ")
    try:
        return dt.datetime.fromisoformat(text) if len(text) > 10 else dt.date.fromisoformat(text)
    except ValueError:
        return value


def _date(value):
    value = _when(value)
    return value.date() if isinstance(value, dt.datetime) else value


def rows_as_dicts(rows, model=None):
    """{view: [{column: value}]} from rows in the contract's column order, with dates and times read."""
    model = model or rolemap.contract()
    shape = {v["name"]: v["columns"] for v in model["views"]}
    found = {}
    for view, held in rows.items():
        columns = shape.get(view, [])
        out = []
        for row in held:
            item = {}
            for column, value in zip(columns, row):
                item[column["name"]] = _when(value) if column["type"] in ("date", "datetime") else value
            out.append(item)
        found[view] = out
    return found


def cohort(examinable, data):
    """The anaesthetics of the cohort, as [{anaesthetic row with the patient's birth_date, is_test and age_days}]."""
    spec = examinable.get("cohort") or {}
    patients = {p["patient_key"]: p for p in data.get("role_patient", [])}
    exclude = set(spec.get("exclude") or [])
    lower, upper = spec.get("age_days") or (None, None)
    period = spec.get("period") or (None, None)
    found = []
    for a in data.get("role_anaesthetic", []):
        p = patients.get(a["patient_key"])
        if p is None:
            continue
        start, stop = a.get("start_time"), a.get("stop_time")
        if "test_patient" in exclude and p.get("is_test") == 1:
            continue
        if "no_start" in exclude and start is None:
            continue
        if "stop_before_start" in exclude and start is not None and stop is not None and stop < start:
            continue
        age = (_date(start) - _date(p["birth_date"])).days if start is not None and p.get("birth_date") is not None else None
        if (lower is not None or upper is not None) and (age is None or (lower is not None and age < lower) or (upper is not None and age > upper)):
            continue
        if period[0] and (start is None or _date(start) < dt.date.fromisoformat(period[0])):
            continue
        if period[1] and (start is None or _date(start) > dt.date.fromisoformat(period[1])):
            continue
        if not _requires(spec.get("requires") or [], a, data):
            continue
        found.append(dict(a, birth_date=p.get("birth_date"), is_test=p.get("is_test"), age_days=age))
    return found


def _requires(requirements, anaesthetic, data):
    for need in requirements:
        rows = [r for r in data.get(need["part"], []) if r.get("anaesthetic_key") == anaesthetic["anaesthetic_key"]]
        if need.get("kinds"):
            rows = [r for r in rows if r.get("kind") in need["kinds"]]
        for flag, value in (need.get("flags") or {}).items():
            rows = [r for r in rows if r.get(flag) == value]
        if not rows:
            return False
    return True


def _time_column(view):
    return next((c for c in TIME_COLUMNS if c in view), None)


def _edge(anaesthetic, at, minutes):
    base = anaesthetic.get("start_time") if at == "start" else anaesthetic.get("stop_time")
    if base is None:
        return None
    return base + dt.timedelta(minutes=minutes or 0)


def outcome_rows(examinable, anaesthetic, data, model=None):
    """The rows of the outcome part that belong to one anaesthetic under the examinable's rules."""
    spec = examinable.get("outcome") or {}
    part = spec.get("part")
    rows = data.get(part, [])
    if rows and "anaesthetic_key" in rows[0]:
        rows = [r for r in rows if r.get("anaesthetic_key") == anaesthetic["anaesthetic_key"]]
    else:
        rows = [r for r in rows if r.get("patient_key") == anaesthetic["patient_key"]]
    if spec.get("kinds"):
        rows = [r for r in rows if r.get("kind") in spec["kinds"]]
    if spec.get("forms"):
        rows = [r for r in rows if r.get("volume_form") in spec["forms"]]
    for flag, value in (spec.get("flags") or {}).items():
        rows = [r for r in rows if r.get(flag) == value]
    window = spec.get("window")
    if window and rows:
        # An edge the record does not give, such as a missing stop, closes the window, as the specification compiles
        # it: a question that keeps the window open after a missing stop says so in its own words, not through the oracle.
        column = _time_column(rows[0])
        low = _edge(anaesthetic, window.get("from", "start"), window.get("from_minutes"))
        high = _edge(anaesthetic, window.get("to", "stop"), window.get("to_minutes"))
        if low is None or high is None:
            return []
        rows = [r for r in rows if r.get(column) is not None and low <= r[column] <= high]
    if spec.get("amended", "replaced") == "replaced" and rows and "amends_key" in rows[0]:
        amended = {r["amends_key"] for r in data.get(part, []) if r.get("amends_key")}
        own = next(v["key"][0] for v in (model or rolemap.contract())["views"] if v["name"] == part)
        rows = [r for r in rows if r.get(own) not in amended]
    duplicates = spec.get("duplicates", "keep")
    if duplicates != "keep" and rows:
        column = _time_column(rows[0])
        kept = {}
        for r in rows:
            at = (r.get("kind"), r.get(column))
            held = kept.get(at)
            if held is None:
                kept[at] = r
            elif duplicates in ("collapse", "lowest") and (r.get("value") is not None and (held.get("value") is None or r["value"] < held["value"])):
                kept[at] = r
            elif duplicates == "highest" and (r.get("value") is not None and (held.get("value") is None or r["value"] > held["value"])):
                kept[at] = r
            elif duplicates == "last" and (r.get("documented_time") or r.get(column) or dt.datetime.min) > (held.get("documented_time") or held.get(column) or dt.datetime.min):
                kept[at] = r
        rows = list(kept.values())
    return rows


def measure_of(examinable, anaesthetic, rows, data, capability_rows=None):
    """One anaesthetic's value under the examinable's measure, or None where it has none."""
    spec = examinable.get("outcome") or {}
    measure = spec.get("measure")
    absent = spec.get("absent", "null")
    of = spec.get("of")
    if isinstance(measure, dict):
        held = (capability_rows or {}).get(str(anaesthetic["anaesthetic_key"]))
        value = None if held is None else held.get(of or list(held)[-1])
    elif measure == "exists":
        value = 1 if rows else 0
    elif measure == "count":
        value = len(rows)
    elif measure in ("sum", "min", "max"):
        values = [r.get(of) for r in rows if r.get(of) is not None]
        value = None if not values else {"sum": sum, "min": min, "max": max}[measure](values)
    elif measure == "first_time":
        column = _time_column(rows[0]) if rows else None
        times = [r[column] for r in rows if column and r.get(column) is not None]
        value = min(times) if times else None
    else:
        raise OracleError(f"the measure {measure!r} is not one the oracle knows")
    if value is None and measure not in ("exists",) and not isinstance(measure, dict):
        value = 0 if absent == "zero" and measure in ("count", "sum") else None
    per = spec.get("per")
    if per and value is not None:
        detail = next((d for d in data.get("role_anaesthetic_detail", []) if d.get("anaesthetic_key") == anaesthetic["anaesthetic_key"]), None)
        divisor = detail.get(per) if detail else None
        value = None if not divisor else value / divisor
    return value


def _capability_rows(spec, rows):
    """A composed capability's row for each anaesthetic, run on a shadow that holds the rows alone."""
    held = spec["measure"]
    con = roleshadow.role_shadow(seed=1, anaesthetics=0, with_planted=False, extra=rows)
    try:
        columns, found = roleshadow.duckdb_runner(con)(capability.fill(held["name"], held["parameters"]))
    finally:
        con.close()
    return {str(row[columns.index("anaesthetic_key")]): dict(zip(columns, row)) for row in found}


def evaluate(examinable, rows, model=None):
    """The examinable evaluated over rows ({view: [row lists in the contract's order]}). Returns {"units": [(key,
    value)], "answer": {"columns", "rows"}}, or {"oracle": "none", "because"} where the examinable says so."""
    if examinable.get("oracle") == "none":
        return {"oracle": "none", "because": examinable.get("because")}
    model = model or rolemap.contract()
    data = rows_as_dicts(rows, model)
    spec = examinable.get("outcome") or {}
    chosen = cohort(examinable, data)
    capability_rows = _capability_rows(spec, rows) if isinstance(spec.get("measure"), dict) else None
    units = []
    for a in chosen:
        value = measure_of(examinable, a, outcome_rows(examinable, a, data, model), data, capability_rows)
        if value is None and spec.get("absent") == "excluded":
            continue
        units.append((a, value))
    grain = (examinable.get("cohort") or {}).get("grain", "anaesthetic")
    if grain == "patient":
        by_patient = {}
        for a, value in units:
            by_patient.setdefault(a["patient_key"], []).append(value)
        units = [({"anaesthetic_key": None, "patient_key": p}, _combine(values, spec)) for p, values in by_patient.items()]
    return {"units": [(a["anaesthetic_key"] if grain == "anaesthetic" else a["patient_key"], v) for a, v in units],
            "answer": statistic(examinable, units)}


def _combine(values, spec):
    measure = spec.get("measure")
    values = [v for v in values if v is not None]
    if not values:
        return None
    if measure in ("exists", "max") or isinstance(measure, dict):
        return max(values)
    if measure == "min" or measure == "first_time":
        return min(values)
    return sum(values)


def _in_band(band, value):
    if band.get("absent"):
        return value is None
    if value is None:
        return False
    low, high = band.get("from"), band.get("to")
    if low is not None and high is not None and low == high:
        return value == low
    return (low is None or value >= low) and (high is None or value < high)


def statistic(examinable, units):
    """The result's statistic over the units, as {"columns", "rows"} in the result's declared columns."""
    result = examinable.get("result") or {}
    kind = result.get("statistic", "count")
    columns = list(result.get("columns") or [])
    bands = result.get("by") or []
    values = [v for _, v in units]
    if kind == "rows":
        return {"columns": columns, "rows": [[a["anaesthetic_key"] or a["patient_key"], v] for a, v in units]}
    if bands:
        # Each unit falls in the first band that holds it, so that a band from zero does not count the zeros again.
        placed = {}
        for a, v in units:
            for n, band in enumerate(bands):
                if _in_band(band, v):
                    placed.setdefault(n, []).append((a, v))
                    break
        rows = []
        for n, band in enumerate(bands):
            inside = placed.get(n, [])
            row = [band.get("band"), len(inside)]
            if len(columns) >= 3:
                row.append(len({a["patient_key"] for a, _ in inside}))
            rows.append(row[:len(columns)] if columns else row)
        return {"columns": columns, "rows": rows}
    if kind == "count":
        return {"columns": columns, "rows": [[len(units)]]}
    if kind == "proportion":
        with_value = sum(1 for v in values if v)
        return {"columns": columns, "rows": [[with_value, len(units), (with_value / len(units)) if units else None][:len(columns) or 3]]}
    if kind == "sum":
        held = [v for v in values if v is not None]
        return {"columns": columns, "rows": [[sum(held) if held else None]]}
    if kind == "median":
        held = sorted(v for v in values if v is not None)
        return {"columns": columns, "rows": [[statistics.median(held) if held else None]]}
    raise OracleError(f"the statistic {kind!r} is not one the oracle knows")


# The differential run.

def differential(sql, examinable, seeds=(1, 2, 3), anaesthetics=120):
    """The package's answer compared with the oracle's on the role-level shadow at each seed. Returns the stage's
    report: each seed's comparison, the outcome and a sentence; a package whose examinable says the oracle is none is
    reported as needing cases written by hand."""
    if not examinable or examinable.get("oracle") == "none":
        because = (examinable or {}).get("because") or "the package gives no examinable object"
        return {"stage": STAGE, "outcome": "not applied", "runs": [],
                "says": f"The oracle does not cover the question ({because.rstrip('.')}), so its logic rests on cases written by hand."}
    model = rolemap.contract()
    runs, problems = [], []
    for seed in seeds:
        con = roleshadow.role_shadow(seed=seed, anaesthetics=anaesthetics, with_planted=True, further=True)
        try:
            rows = {v["name"]: [list(r) for r in con.execute(f"SELECT * FROM {v['name']}").fetchall()] for v in model["views"]}
            columns, answer = roleshadow.duckdb_runner(con)(sql)
        finally:
            con.close()
        expected = evaluate(examinable, rows, model)["answer"]
        from .planted import comparable
        got = (list(columns), [[comparable(v) for v in row] for row in answer])
        want = (expected["columns"] or list(columns), [[comparable(v) for v in row] for row in expected["rows"]])
        found = equivalence.compare_answers(got, want)
        runs.append({"seed": seed, "same": found["same"], "returned": found["rows_a"], "expected": found["rows_b"], "shared": found["rows_shared"]})
        if not found["same"]:
            problems.append(f"at seed {seed} the package's answer differs from the oracle's ({found['rows_shared']} of {found['rows_b']} rows the same)")
    outcome = "passed" if not problems else "failed"
    says = (f"The package's answer agrees with the oracle's on the shadow at {len(seeds)} seeds." if not problems else
            "The package's answer disagrees with the oracle's: " + "; ".join(problems) + ".")
    return {"stage": STAGE, "outcome": outcome, "runs": runs, "says": says}


def run(path, seeds=(1, 2, 3)):
    from .. import question
    sql, _, package = question.sql_and_decisions(path)
    return differential(sql, package.get("examinable"), seeds)
