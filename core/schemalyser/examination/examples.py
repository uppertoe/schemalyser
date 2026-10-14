"""Worked examples for the asker: small cases at each boundary of the examinable, valued by the oracle and written as
sentences in clinical words, so that the asker can confirm the meaning of the question without reading SQL.

From the examinable's cohort come a child one day either side of each age bound, an anaesthetic on each side of the
period, a test patient, an anaesthetic with no start and one with a stop before its start; from its outcome, an
anaesthetic in which nothing was given, one with no weight where the measure is per kilogram, two rows at one
instant, a row a later correction amends, and a row just outside the window. Each example is a set of rows, the
oracle's value for it, and one sentence; the asker confirms each or says which is wrong, and a wrong one returns to
the model as a change of decision.
"""
import datetime as dt

from . import oracle
from .. import rolemap

START = dt.datetime(2024, 6, 12, 9, 0)
STOP = START + dt.timedelta(minutes=90)


def _kind_words(kind):
    return (kind or "the outcome").replace("_", " ")


def _unit(column):
    if not column:
        return ""
    for suffix, words in (("_ml", "ml"), ("_kg", "kg"), ("_grams", "g"), ("_cm", "cm"), ("_minutes", "minutes"), ("_hours", "hours")):
        if column.endswith(suffix):
            return words
    return ""


class Builder:
    """Rows of a small world, in the contract's column order."""

    def __init__(self, model=None):
        self.model = model or rolemap.contract()
        self.shape = {v["name"]: [c["name"] for c in v["columns"]] for v in self.model["views"]}
        self.rows = {}

    def add(self, view, **values):
        self.rows.setdefault(view, []).append([values.get(c) for c in self.shape[view]])
        return self

    def patient(self, key="P1", age_days=10, is_test=0, birth=None):
        birth = birth or (START.date() - dt.timedelta(days=age_days))
        return self.add("role_patient", patient_key=key, birth_date=birth.isoformat(), death_date=None, is_test=is_test)

    def anaesthetic(self, key="A1", patient="P1", start=START, stop=STOP):
        return self.add("role_anaesthetic", anaesthetic_key=key, patient_key=patient,
                        start_time=None if start is None else start.isoformat(sep=" "), stop_time=None if stop is None else stop.isoformat(sep=" "))

    def detail(self, key="A1", **values):
        return self.add("role_anaesthetic_detail", anaesthetic_key=key, stay_key=f"S{key}", **values)

    def outcome(self, examinable, key="A1", own="O1", at=None, value=10.0, **more):
        spec = examinable.get("outcome") or {}
        part = spec["part"]
        columns = self.shape[part]
        values = {"anaesthetic_key": key, "patient_key": "P1", "stay_key": f"S{key}"}
        for column in columns:
            if column.endswith("_key") and column not in values and column != "amends_key":
                values[column] = own
        if "kind" in columns and spec.get("kinds"):
            values["kind"] = spec["kinds"][0]
        if "volume_form" in columns:
            values["volume_form"] = (spec.get("forms") or ["amount"])[0]
        for flag, flagged in (spec.get("flags") or {}).items():
            values[flag] = flagged
        if spec.get("of") and spec["of"] in columns:
            values[spec["of"]] = value
        elif "value" in columns:
            values["value"] = value
        time_column = oracle._time_column({c: None for c in columns})
        if time_column:
            values[time_column] = (at or START + dt.timedelta(minutes=20)).isoformat(sep=" ")
        if "source_kind" in columns:
            values["source_kind"] = "charted_value"
        values.update(more)
        return self.add(part, **{k: v for k, v in values.items() if k in columns})


def _value_words(examinable, value):
    spec = examinable.get("outcome") or {}
    measure = spec.get("measure")
    if value is None:
        return "has no value"
    if measure == "exists":
        return "counts as having it" if value else "counts as not having it"
    if measure == "count":
        return f"counts {int(value)}"
    unit = _unit(spec.get("of"))
    per = spec.get("per")
    if per:
        return f"gives {round(value, 3)} {unit} per {_unit(per) or per}".replace("  ", " ").strip()
    return f"gives {round(value, 3)} {unit}".strip()


def _example(examinable, builder, example_id, about, situation, key="A1"):
    found = oracle.evaluate(examinable, builder.rows)
    units = {str(k): v for k, v in found["units"]}
    if key in units:
        says = f"{situation} is counted, and {_value_words(examinable, units[key])}."
        counted, value = True, units[key]
    else:
        says = f"{situation} is left out."
        counted, value = False, None
    return {"id": example_id, "about": about, "says": says, "counted": counted, "value": value, "rows": builder.rows}


def generate(examinable, model=None):
    """The worked examples of an examinable, as [{"id", "about", "says", "counted", "value", "rows"}], or [] where
    the oracle does not cover the question."""
    if not examinable or examinable.get("oracle") == "none" or not examinable.get("outcome"):
        return []
    model = model or rolemap.contract()
    cohort = examinable.get("cohort") or {}
    spec = examinable["outcome"]
    kind = _kind_words((spec.get("kinds") or [None])[0])
    unit = _unit(spec.get("of"))
    given = f"given 10 {unit} of {kind}".strip() if unit else f"with a {kind} recorded"
    examples = []
    lower, upper = cohort.get("age_days") or (None, None)
    for bound, name in ((lower, "lower"), (upper, "upper")):
        if bound is None:
            continue
        for age in (bound - 1, bound, bound + 1):
            if age < 0:
                continue
            b = Builder(model).patient(age_days=age).anaesthetic().detail(weight_kg=3.0).outcome(examinable)
            examples.append(_example(examinable, b, f"age-{age}", "the age bound", f"A child aged {age} days on the day of the anaesthetic, {given},"))
    period = cohort.get("period") or (None, None)
    for edge, name in ((period[0], "first"), (period[1], "last")):
        if not edge:
            continue
        day = dt.date.fromisoformat(edge)
        for shift in (-1, 0, 1):
            when = dt.datetime.combine(day + dt.timedelta(days=shift), dt.time(9))
            age = (when.date() - (START.date() - dt.timedelta(days=10))).days
            b = Builder(model).patient(birth=when.date() - dt.timedelta(days=10)).anaesthetic(start=when, stop=when + dt.timedelta(minutes=90)).detail(weight_kg=3.0)
            b.outcome(examinable, at=when + dt.timedelta(minutes=20))
            examples.append(_example(examinable, b, f"period-{name}{shift:+d}", "the period",
                                     f"An anaesthetic started on {when.date().strftime('%-d %B %Y')}, {given},"))
    exclusions = set(cohort.get("exclude") or [])
    if "test_patient" in exclusions:
        b = Builder(model).patient(is_test=1).anaesthetic().detail(weight_kg=3.0).outcome(examinable)
        examples.append(_example(examinable, b, "test-patient", "test patients", f"A test patient's anaesthetic, {given},"))
    if "no_start" in exclusions:
        b = Builder(model).patient().anaesthetic(start=None).detail(weight_kg=3.0).outcome(examinable)
        examples.append(_example(examinable, b, "no-start", "an anaesthetic with no start", f"An anaesthetic with no recorded start, {given},"))
    if "stop_before_start" in exclusions:
        b = Builder(model).patient().anaesthetic(stop=START - dt.timedelta(minutes=30)).detail(weight_kg=3.0).outcome(examinable)
        examples.append(_example(examinable, b, "stop-before-start", "an anaesthetic whose stop is before its start",
                                 f"An anaesthetic whose recorded stop is before its start, {given},"))
    b = Builder(model).patient().anaesthetic().detail(weight_kg=3.0)
    examples.append(_example(examinable, b, "nothing-given", "an anaesthetic with none of the outcome",
                             f"An anaesthetic in which no {kind} was recorded"))
    if spec.get("per"):
        b = Builder(model).patient().anaesthetic().detail().outcome(examinable)
        examples.append(_example(examinable, b, "no-weight", f"a missing {spec['per'].replace('_', ' ')}",
                                 f"An anaesthetic with no {_unit(spec['per']) and 'weight' or spec['per']} recorded, {given},"))
    if spec.get("duplicates"):
        b = Builder(model).patient().anaesthetic().detail(weight_kg=3.0).outcome(examinable, own="O1").outcome(examinable, own="O2")
        examples.append(_example(examinable, b, "duplicate", "two rows at one instant",
                                 f"An anaesthetic with two rows of 10 {unit} of {kind} at the same instant".strip()))
    part_columns = Builder(model).shape[spec["part"]]
    if spec.get("amended") and "amends_key" in part_columns:
        b = Builder(model).patient().anaesthetic().detail(weight_kg=3.0).outcome(examinable, own="O1", value=10.0)
        b.outcome(examinable, own="O2", value=15.0, amends_key="O1", source_kind="correction")
        examples.append(_example(examinable, b, "amended", "a corrected row",
                                 f"An anaesthetic whose row of 10 {unit} of {kind} was later corrected to 15 {unit}".replace("  ", " ")))
    window = spec.get("window")
    if window:
        b = Builder(model).patient().anaesthetic().detail(weight_kg=3.0)
        before = oracle._edge({"start_time": START, "stop_time": STOP}, window.get("from", "start"), window.get("from_minutes")) - dt.timedelta(minutes=1)
        b.outcome(examinable, at=before)
        examples.append(_example(examinable, b, "before-window", "the window",
                                 f"An anaesthetic whose only {kind} row is one minute before the window opens"))
    return examples


def public(examples):
    """The examples as the report carries them: the id, what each is about and its sentence, without the rows."""
    return [{"id": e["id"], "about": e["about"], "says": e["says"]} for e in examples]
