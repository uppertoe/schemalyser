"""Realistic values for columns that the site rules give a role to.

The sandbox first fills every table with structurally sound filler. This module then writes over
the columns that have a role, using public reference data kept in the realism folder: growth
charts, vital sign ranges by age, anaesthetic durations and medication names. SOURCES.md in that
folder says where each file came from. Every timing and distribution constant comes from the table
of tunable parameters in tuning.py, which the site rules and the check results may adjust.

Rows line up across tables: row i of any table belongs to subject i modulo the number of subjects,
where the subjects are the rows of the table that holds the date of birth. A child's sex, age and
place on the growth chart are therefore the same wherever that child appears. In the same way, row
i of a table of timed values belongs to anaesthetic i modulo the number of anaesthetics, so that a
child with two anaesthetics has the values of each placed around that anaesthetic.

A role that cannot be applied leaves the filler in place, and the others go on. Each such role is
recorded in failures, by its table, column and role, with a reason from REASONS.
"""
import csv
from pathlib import Path

import duckdb

from .checks import BANDS
from .roles import ADDED_ROWS
from .tuning import PUBLIC, Tuning

DATA = Path(__file__).parent / "realism"
OUNCES_PER_KG, POUNDS_PER_KG, CM_PER_INCH = 35.274, 2.20462, 2.54
# Why a role was not applied: a statement failed in the database, something the role depends on is
# missing (such as the start of the anaesthetic for a timed value), or the reference data is missing.
REASONS = ("database", "prerequisite", "reference data")
# The longest span drawn from the open-ended band of a spans result, in minutes: two weeks.
LONGEST_SPAN = 20160
# Roles that are placed around the anaesthetic, rather than written from reference data.
TIMED_ROLES = ("event_time", "administration_time", "time_during_anaesthetic", "placement_time", "removal_time",
               "admission_time", "discharge_time")


def _load(name):
    path = DATA / name
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _text(value):
    return "'" + value.replace("'", "''") + "'"


def _n(value):
    """A number from the table of parameters, written as SQL."""
    return repr(value) if isinstance(value, float) else str(int(value))


def _spread(seed_expression, salt):
    """A number from about -3 to 3, bunched around 0, that is fixed for a given seed."""
    parts = " + ".join(f"hash({seed_expression} + {salt + k}) % 1000" for k in range(3))
    return f"((({parts}) / 1000.0 - 1.5) * 2.0)"


def _chance(seed_expression, share):
    """A condition that holds for about the given share of seeds."""
    return f"hash({seed_expression}) % 10000 < {round(share * 10000)}"


def _drawn(bands, seed_expression, salt):
    """Minutes drawn from spans bands in proportion to their counts, evenly within each band.

    Only bands of no less than zero minutes are drawn from, because a duration cannot be negative.
    """
    usable = []
    for label, count in bands:
        low, high = next((lo, hi) for name, lo, hi in BANDS if name == label)
        if low is not None and count > 0:
            usable.append((low, high if high is not None else LONGEST_SPAN, count))
    if not usable:
        return None
    total, running, branches = sum(c for _, _, c in usable), 0, []
    for low, high, count in usable:
        running += count
        branches.append(f"WHEN hash({seed_expression} + {salt}) % {total} < {running} "
                        f"THEN {low} + hash({seed_expression} + {salt + 1}) % {max(1, high - low)}")
    return f"CAST(CASE {' '.join(branches)} END AS BIGINT)"


class Realism:
    def __init__(self, con, catalogue, roles, sizes, kinds, tuning=None, spans=None, lineage=None, listed=None):
        self.con, self.catalogue, self.sizes, self.kinds = con, catalogue, sizes, kinds
        # The roles that add rows of their own write them only where the check results list their code, because only
        # then does the source hold such readings. listed holds those roles.
        self.listed = set(listed or ())
        self.added = 0          # the rows that those roles have added
        # Where fanout results skew the keys, the rows' subjects and anaesthetics follow the keys (see
        # sandbox.Lineage) rather than i modulo the number of subjects or anaesthetics.
        self.lineage = lineage
        self.tuning = tuning or Tuning()
        self.spans = spans or {}   # (table, first column, second column) -> [(band, count)] from the check results
        self.roles = [r for r in roles if r.table in sizes]
        self.by_role = {}
        for role in self.roles:
            self.by_role.setdefault(role.role, []).append(role)
        self.subjects = None    # the number of subjects, once the subject table is known
        self.done = set()       # the roles that have been applied
        self.failed = set()     # the roles that could not be applied
        self.failures = []      # the same, as plain dictionaries of catalogue names, role names and reasons

    def first(self, name):
        return (self.by_role.get(name) or [None])[0]

    def run(self, statement):
        self.con.execute(statement)

    def table(self, name, rows, columns):
        self.run(f"CREATE OR REPLACE TEMP TABLE {name} ({columns})")
        if rows:
            self.con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(rows[0]))})", rows)

    def cast(self, role, expression):
        kind = self.kinds[(role.table, role.column)]
        return f"CAST({expression} AS {kind})"

    def where(self, role, alias="r"):
        if not role.when_column:
            return ""
        return f" WHERE CAST({alias}.{_quoted(role.when_column)} AS VARCHAR) = {_text(role.when_value)}"

    # Keeping account of what was applied.

    def fail(self, role, reason, column=None):
        self.failed.add(role)
        entry = {"table": role.table, "column": column or role.column, "role": role.role, "reason": reason}
        if entry not in self.failures:
            self.failures.append(entry)

    def attempt(self, roles, action, *arguments):
        """Runs one step for the given roles, and records them as applied, or as failed in the database."""
        try:
            action(*arguments)
        except duckdb.Error:
            for role in roles:
                self.fail(role, "database")
            return False
        self.done.update(roles)
        return True

    def apply(self):
        """Writes every role it can. Returns the roles that could not be applied."""
        if not self.roles:
            return self.failures
        try:
            self.reference_data()
        except duckdb.Error:
            for role in self.roles:
                self.fail(role, "reference data")
            return self.failures
        self.sexes()
        birth = self.first("birth_date")
        if self.lineage is not None:
            try:
                self.owners()
            except duckdb.Error:
                for role in self.roles:
                    self.fail(role, "database")
                return self.failures
        if birth is not None:
            self.subjects = self.sizes[birth.table]
            if not self.attempt([birth], self.births, birth) or not self.attempt([], self.subject_table):
                self.subjects = None
        # The end of each anaesthetic is written first, because the timed values are placed around it.
        for role in self.by_role.get("anaesthetic_stop", []):
            self.write(role)
        self.timings()
        for role in self.roles:
            if role.role not in ("sex", "birth_date", "anaesthetic_stop", *TIMED_ROLES, *ADDED_ROWS):
                self.write(role)
        # The roles that add rows come last, because a cuff mean is drawn from the blood pressure beside it and an
        # arterial mean is placed through its anaesthetic.
        for name in ADDED_ROWS:
            for role in self.by_role.get(name, []):
                self.write(role)
        # The start of an anaesthetic is read rather than written, so it counts as applied once the stop is.
        for role in self.by_role.get("anaesthetic_start", []):
            if any(stop in self.done and stop.table == role.table for stop in self.by_role.get("anaesthetic_stop", [])):
                self.done.add(role)
        for role in self.roles:
            if role not in self.done and role not in self.failed:
                self.fail(role, "prerequisite")
        for name in ("realism_weight", "realism_stature", "realism_vitals", "realism_pressure",
                     "realism_subject", "realism_reference", "realism_anaesthetic", "realism_owner",
                     "realism_owner_anaesthetic", "realism_row_anaesthetic"):
            try:
                self.run(f"DROP TABLE IF EXISTS {name}")
            except duckdb.Error:
                pass    # a temporary table that cannot be dropped goes when the connection closes
        return self.failures

    def write(self, role):
        writer = getattr(self, "write_" + role.role, None)
        if writer is None:
            return
        try:
            applied = writer(role)
        except duckdb.Error:
            self.fail(role, "database")
            return
        if applied is False:
            self.fail(role, "reference data")
        elif applied is None:
            self.done.add(role)
        # A writer that returns "prerequisite" has found something missing that the role depends on.
        elif applied == "prerequisite":
            self.fail(role, "prerequisite")

    def reference_data(self):
        growth = lambda name: [(int(r["sex"]), int(r["age_months"]), float(r["l"]), float(r["m"]), float(r["s"]))  # noqa: E731
                               for r in _load(name)]
        self.table("realism_weight", growth("growth_weight.csv"), "sex INTEGER, age_months INTEGER, l DOUBLE, m DOUBLE, s DOUBLE")
        self.table("realism_stature", growth("growth_stature.csv"), "sex INTEGER, age_months INTEGER, l DOUBLE, m DOUBLE, s DOUBLE")
        vitals = [(r["measure"], int(r["age_from_months"]), int(r["age_to_months"]), float(r["p10"]), float(r["p50"]), float(r["p90"]))
                  for r in _load("vitals.csv")]
        self.table("realism_vitals", vitals, "measure VARCHAR, age_from INTEGER, age_to INTEGER, p10 DOUBLE, p50 DOUBLE, p90 DOUBLE")
        self.have = {"realism_weight": bool(_load("growth_weight.csv")), "realism_stature": bool(_load("growth_stature.csv")),
                     "realism_vitals": bool(vitals)}
        number = lambda text: float(text) if text.strip() else None  # noqa: E731
        pressure = [(int(r["age_from_months"]), int(r["age_to_months"]), *(number(r[k]) for k in (
            "systolic_p50", "mean_p50", "diastolic_p50", "systolic_sd", "mean_sd", "diastolic_sd")))
            for r in _load("blood_pressure.csv")]
        self.table("realism_pressure", pressure, "age_from INTEGER, age_to INTEGER, systolic_p50 DOUBLE, mean_p50 DOUBLE, "
                   "diastolic_p50 DOUBLE, systolic_sd DOUBLE, mean_sd DOUBLE, diastolic_sd DOUBLE")
        self.have["realism_pressure"] = bool(pressure)
        # A durations file, where one is present, replaces the invented durations as the default.
        listed = [int(float(r["minutes"])) for r in _load("durations.csv") if r["measure"] == "anaesthesia_duration"]
        if listed:
            self.tuning.defaults.setdefault("anaesthetic_durations_minutes", (tuple(listed), PUBLIC))
        self.medications = [r["name"] for r in _load("medications.csv") if r.get("name")]
        self.procedures = [r["name"] for r in _load("procedures.csv") if r.get("name")]
        self.diagnoses = [r["icd10_code"] for r in _load("diagnoses.csv") if r.get("icd10_code")]

    def sexes(self):
        for role in self.by_role.get("sex", []):
            if not (role.male and role.female):
                self.fail(role, "prerequisite")    # the rules do not say which values mean male and female
                continue
            value = f"CASE WHEN hash(rowid + 11) % 2 = 0 THEN {_text(role.male)} ELSE {_text(role.female)} END"
            self.attempt([role], self.run, f"UPDATE {_quoted(role.table)} SET {_quoted(role.column)} = {self.cast(role, value)}")

    def owners(self):
        """Where the keys follow a lineage: each row's subject, and the anaesthetics that it is placed around.

        A row below the anaesthetic belongs to its own anaesthetic. A row above it, such as a hospital
        visit, is placed around all of its anaesthetics, from the start of the first to the end of the
        last. A row that reaches only its subject is placed around the subject's first anaesthetic. A row
        that has no anaesthetic keeps its time, and its subject is born before that time as well. A row
        of a table that reaches neither belongs to anaesthetic i modulo their number, as before.
        """
        birth, start = self.first("birth_date"), self.first("anaesthetic_start")
        tables = sorted({r.table for r in self.roles})
        by_subject = self.lineage.down(birth.table, start.table) if birth and start else None
        subject_of_anaesthetic = self.lineage.up(start.table, birth.table) if birth and start else None
        rows, anaesthetic_rows = [], []
        for table in tables:
            size = self.sizes[table]
            subjects = self.lineage.up(table, birth.table) if birth else None
            owned = None
            if start is not None:
                above = self.lineage.up(table, start.table)
                if above is not None:
                    owned = [[k] if k is not None else [] for k in above]
                else:
                    owned = self.lineage.down(table, start.table)
                if owned is None and subjects is not None and by_subject is not None:
                    owned = [by_subject[s][:1] if s is not None else [] for s in subjects]
                if owned is None:
                    owned = [[i % self.sizes[start.table]] for i in range(size)]
            if subjects is None and birth is not None:
                subjects = [i % self.sizes[birth.table] for i in range(size)]
                if owned is not None and subject_of_anaesthetic is not None:
                    subjects = [subject_of_anaesthetic[ks[0]] if ks else s for ks, s in zip(owned, subjects)]
            for i in range(size):
                rows.append((table, i, subjects[i] if subjects else None, bool(owned and owned[i])))
                anaesthetic_rows.extend((table, i, k) for k in (owned[i] if owned else ()))
        self.columns("realism_owner", rows, "t VARCHAR, rn BIGINT, s BIGINT, placed BOOLEAN")
        self.columns("realism_owner_anaesthetic", anaesthetic_rows, "t VARCHAR, rn BIGINT, k BIGINT")

    def columns(self, name, rows, columns):
        """A temporary table of whole numbers beside a table name, filled a column at a time.

        The numbers go to the database as text, which is much quicker than a row or a value at a time.
        """
        self.run(f"CREATE OR REPLACE TEMP TABLE {name} ({columns})")
        if not rows:
            return
        lists = list(zip(*rows))
        names = ", ".join(f"string_split(${k + 1}, ',') AS c{k}" for k in range(len(lists)))
        each = ", ".join(f"CAST(NULLIF(c{k}[n], '') AS {part.split()[1]})" if k else f"c{k}[n]"
                         for k, part in enumerate(columns.split(", ")))
        text = [",".join("" if v is None else str(int(v)) if not isinstance(v, str) else v for v in values)
                for values in lists]
        self.con.execute(f"INSERT INTO {name} SELECT {each} FROM (SELECT {names}) AS x, "
                         f"range(1, len(x.c0) + 1) AS r(n)", text)

    def subject_of(self, table, alias="r"):
        """The subject that a row belongs to, as SQL."""
        if self.lineage is None:
            return f"{alias}.rowid % {self.subjects}"
        return f"(SELECT o.s FROM realism_owner o WHERE o.t = {_text(table)} AND o.rn = {alias}.rowid)"

    def births(self, birth):
        """Gives each subject a date of birth that falls before every one of that subject's events."""
        t = self.tuning
        # The reference time for each subject is the earliest of their events and anaesthetics.
        events = self.by_role.get("event_time", []) + self.by_role.get("anaesthetic_start", [])
        if events and self.lineage is not None:
            # A timed value that will not be placed around an anaesthetic keeps its time, so it counts as an event.
            start = self.first("anaesthetic_start")
            kept = sorted({(r.table, r.at_column or r.column) for r in self.roles
                           if (r.role in TIMED_ROLES or r.at_column) and (start is None or r.table != start.table)})
            each = " UNION ALL ".join(
                [f"SELECT o.s AS s, CAST(x.{_quoted(e.column)} AS TIMESTAMP) AS t FROM {_quoted(e.table)} x "
                 f"JOIN realism_owner o ON o.t = {_text(e.table)} AND o.rn = x.rowid WHERE o.s IS NOT NULL" for e in events]
                + [f"SELECT o.s AS s, CAST(x.{_quoted(column)} AS TIMESTAMP) AS t FROM {_quoted(table)} x "
                   f"JOIN realism_owner o ON o.t = {_text(table)} AND o.rn = x.rowid WHERE o.s IS NOT NULL AND NOT o.placed"
                   for table, column in kept])
            self.run(f"CREATE OR REPLACE TEMP TABLE realism_reference AS SELECT s, MIN(t) AS t, MAX(t) AS latest FROM ({each}) GROUP BY s")
        elif events:
            each = " UNION ALL ".join(
                f"SELECT rowid % {self.subjects} AS s, CAST({_quoted(e.column)} AS TIMESTAMP) AS t FROM {_quoted(e.table)}"
                for e in events)
            self.run(f"CREATE OR REPLACE TEMP TABLE realism_reference AS SELECT s, MIN(t) AS t, MAX(t) AS latest FROM ({each}) GROUP BY s")
        else:
            self.run(f"CREATE OR REPLACE TEMP TABLE realism_reference AS SELECT i AS s, TIMESTAMP '2024-06-30' AS t, "
                     f"TIMESTAMP '2024-06-30' AS latest FROM range({self.subjects}) AS r(i)")
        # Ages lean young, as a children's hospital's do. Nobody is younger than the youngest age, so
        # that an admission the day before the anaesthetic still follows the birth.
        youngest, oldest = t["youngest_age_days"], t["oldest_age_days"]
        age_days = (f"CAST({_n(youngest)} + floor(pow((hash(p.rowid + 23) % 100000) / 100000.0, {_n(t['age_curve_exponent'])}) "
                    f"* {_n(oldest - youngest)}) AS INTEGER)")
        value = (f"(SELECT COALESCE(MIN(f.t), TIMESTAMP '2024-06-30') FROM realism_reference f WHERE f.s = p.rowid) "
                 f"- to_days({age_days})")
        self.run(f"UPDATE {_quoted(birth.table)} AS p SET {_quoted(birth.column)} = {self.cast(birth, value)}")

    def timings(self):
        """Places each timed value around the anaesthetic that its row belongs to.

        Row i of a table of timed values belongs to anaesthetic i modulo the number of anaesthetics,
        as a key that refers to the table of anaesthetics would. A weight or a height is placed
        before the anaesthetic starts, and every other value in a table of timed values is placed
        while it runs. Without this a child's heart rates would fall years away from the anaesthetic
        they belong to.
        """
        t = self.tuning
        start, stop = self.first("anaesthetic_start"), self.first("anaesthetic_stop")
        timed_by_column = [role for role in self.roles if role.at_column and (start is None or role.table != start.table)]
        if start is None or stop is None or start.table != stop.table:
            # The timed roles are recorded as not applied at the end. A value with a time column of
            # its own is written all the same, so its time column is recorded here.
            for role in timed_by_column:
                self.fail(role, "prerequisite", role.at_column)
            return
        count = self.sizes[start.table]
        began, ended = (f"CAST({_quoted(r.column)} AS TIMESTAMP)" for r in (start, stop))
        try:
            self.run(f"CREATE OR REPLACE TEMP TABLE realism_anaesthetic AS SELECT rowid AS k, {began} AS began, "
                     f"{ended} AS ended FROM {_quoted(start.table)} WHERE {began} IS NOT NULL AND {ended} >= {began}")
        except duckdb.Error:
            for role in timed_by_column:
                self.fail(role, "database", role.at_column)
            for role in self.roles:
                if role.role in TIMED_ROLES and role.table != start.table:
                    self.fail(role, "database")
            return
        if self.lineage is not None:
            try:
                # Each row's own anaesthetic, or the span from the first to the last of its anaesthetics.
                self.run("CREATE OR REPLACE TEMP TABLE realism_row_anaesthetic AS SELECT o.t, o.rn, MIN(a.began) AS began, "
                         "MAX(a.ended) AS ended FROM realism_owner_anaesthetic o JOIN realism_anaesthetic a ON a.k = o.k "
                         "GROUP BY o.t, o.rn")
            except duckdb.Error:
                for role in timed_by_column:
                    self.fail(role, "database", role.at_column)
                return
        least, most = t["measurement_before_least_minutes"], t["measurement_before_most_minutes"]
        before = f"a.began - to_minutes(CAST({_n(least)} + hash(r.rowid + 83) % {_n(max(1, most - least))} AS BIGINT))"
        during = ("a.began + to_seconds(CAST(((hash(r.rowid + 89) % 1000) / 1000.0) "
                  "* date_diff('second', a.began, a.ended) AS BIGINT))")

        def early(minutes):
            return f"least(a.ended, a.began + to_seconds(CAST(hash(r.rowid + 97) % {_n(minutes * 60)} AS BIGINT)))"

        late = (f"greatest(a.began, a.ended - to_seconds(CAST(hash(r.rowid + 103) % "
                f"{_n(t['removal_window_minutes'] * 60)} AS BIGINT)))")
        admitted = (f"a.began - to_minutes(CAST({_n(t['admission_before_least_minutes'])} + hash(r.rowid + 137) % "
                    f"{_n(max(1, t['admission_before_most_minutes'] - t['admission_before_least_minutes']))} AS BIGINT))")
        after = t["discharge_after_least_minutes"]
        discharged = (f"a.ended + to_minutes(CAST({_n(after)} + hash(r.rowid + 139) % "
                      f"{_n(max(1, t['discharge_after_most_minutes'] - after))} AS BIGINT))")
        alone = {
            # A share of the doses is given at induction, early in the anaesthetic, and the rest later.
            "administration_time": f"CASE WHEN {_chance('r.rowid + 101', t['induction_dose_share'])} "
                                   f"THEN {early(t['induction_window_minutes'])} ELSE {during} END",
            "time_during_anaesthetic": during,
            # An airway or a line goes in early in the anaesthetic and comes out near its end.
            "placement_time": early(t["placement_window_minutes"]),
            "removal_time": late,
            # The child comes into hospital before the anaesthetic and leaves after it.
            "admission_time": admitted,
            "discharge_time": discharged,
        }
        placed, later, timed = {}, {}, {}
        for role in self.roles:
            if role.table == start.table:
                continue    # the anaesthetic's own table is not placed around itself
            if role.at_column:
                timed.setdefault((role.table, role.at_column), []).append(role)
            elif role.role in alone:
                placed.setdefault((role.table, role.column), (alone[role.role], []))[1].append(role)
            elif role.role == "event_time":
                # The case is on the day of the anaesthetic.
                placed.setdefault((role.table, role.column), ("date_trunc('day', a.began)", []))[1].append(role)
        for key, roles in timed.items():
            beforehand = [f"CAST(r.{_quoted(r_.when_column)} AS VARCHAR) = {_text(r_.when_value)}" if r_.when_column else "TRUE"
                          for r_ in roles if r_.role in ("weight", "height")]
            when = f"CASE WHEN {' OR '.join(beforehand)} THEN {before} ELSE {during} END" if beforehand else during
            placed[key] = (when, roles)
        # Where check results give the spans between a pair of columns in one table, the second column
        # follows the first by a span drawn from those results. It is written after the first.
        for first_role, second_role, key in (("placement_time", "removal_time", "removal_window_minutes"),
                                             ("admission_time", "discharge_time", "discharge_after_most_minutes")):
            for second in self.by_role.get(second_role, []):
                first = next((r for r in self.by_role.get(first_role, [])
                              if r.table == second.table and (r.table, r.column) in placed), None)
                bands = self.spans.get((second.table, first.column, second.column)) if first else None
                drawn = _drawn(bands, "r.rowid", 149) if bands else None
                if drawn is None or (second.table, second.column) not in placed:
                    continue
                value = f"CAST(r.{_quoted(first.column)} AS TIMESTAMP) + to_minutes({drawn})"
                if second_role == "discharge_time":
                    value = f"greatest({value}, a.ended + to_minutes({_n(after)}))"
                later[(second.table, second.column)] = (value, placed.pop((second.table, second.column))[1])
                t.set_from_checks(key, bands)
        if t.still_in_place and t["still_in_place_share"] > 0:
            # A share of airways and lines is recorded as still in place, with the site's sentinel date.
            for role in self.by_role.get("removal_time", []):
                for group in (placed, later):
                    if (role.table, role.column) in group:
                        when, roles = group[(role.table, role.column)]
                        group[(role.table, role.column)] = (
                            f"CASE WHEN {_chance('r.rowid + 107', t['still_in_place_share'])} "
                            f"THEN TIMESTAMP {_text(t.still_in_place)} ELSE {when} END", roles)
        for (table, column), (when, roles) in [*placed.items(), *later.items()]:
            kind = self.kinds[(table, column)]
            source = f"realism_anaesthetic a WHERE a.k = r.rowid % {count}" if self.lineage is None else \
                f"realism_row_anaesthetic a WHERE a.t = {_text(table)} AND a.rn = r.rowid"
            statement = (f"UPDATE {_quoted(table)} AS r SET {_quoted(column)} = COALESCE((SELECT CAST({when} AS {kind}) "
                         f"FROM {source}), r.{_quoted(column)})")
            try:
                self.run(statement)
            except duckdb.Error:
                for role in roles:
                    self.fail(role, "database", column)
                continue
            # A role with a time column is applied once its value is written as well, so only the timed roles count here.
            self.done.update(role for role in roles if not role.at_column)

    def subject_table(self):
        """One row for each subject: date of birth, sex and a reference time."""
        birth, sex = self.first("birth_date"), self.first("sex")
        female = "hash(p.rowid + 11) % 2 = 1"
        if sex is not None and sex.table == birth.table and sex.female:
            female = f"CAST(p.{_quoted(sex.column)} AS VARCHAR) = {_text(sex.female)}"
        self.run(f"CREATE OR REPLACE TEMP TABLE realism_subject AS SELECT p.rowid AS s, "
                 f"CAST(p.{_quoted(birth.column)} AS TIMESTAMP) AS birth, CASE WHEN {female} THEN 2 ELSE 1 END AS sex, "
                 f"COALESCE(f.t, TIMESTAMP '2024-06-30') AS reference "
                 f"FROM {_quoted(birth.table)} p LEFT JOIN realism_reference f ON f.s = p.rowid")

    # Each row's subject, age in months and sex, as SQL that can be used inside an UPDATE of that row.

    def person(self, role):
        if self.subjects is None:
            # Without a date of birth in the rules, each row stands alone with an age of its own.
            t = self.tuning
            age = (f"CAST(floor(pow((hash(r.rowid + 23) % 100000) / 100000.0, {_n(t['age_curve_exponent'])}) "
                   f"* {_n(t['oldest_age_days'])} / 30.44) AS INTEGER)")
            return age, "(1 + hash(r.rowid + 11) % 2)", "r.rowid", ""
        when = f"CAST(r.{_quoted(role.at_column)} AS TIMESTAMP)" if role.at_column else "sub.reference"
        age = f"least(240, greatest(0, date_diff('month', sub.birth, {when})))"
        if self.lineage is not None:
            # The subject is joined beside the row, which the database runs far more quickly than a nested lookup.
            return age, "sub.sex", "sub.s", (f"realism_subject sub, realism_owner o WHERE o.t = {_text(role.table)} "
                                             f"AND o.rn = r.rowid AND sub.s = o.s")
        return age, "sub.sex", "sub.s", f"realism_subject sub WHERE sub.s = {self.subject_of(role.table)}"

    def update(self, role, value, source=""):
        target = f"{_quoted(role.table)} AS r"
        if source:
            self.run(f"UPDATE {target} SET {_quoted(role.column)} = (SELECT {self.cast(role, value)} FROM {source} LIMIT 1)"
                     f"{self.where(role)}")
        else:
            self.run(f"UPDATE {target} SET {_quoted(role.column)} = {self.cast(role, value)}{self.where(role)}")

    def growth(self, role, table, scale, places):
        if not self.have[table]:
            return False    # without the reference data the filler stays
        age, sex, seed, subject = self.person(role)
        z = _spread(seed, 31)
        size = f"g.m * pow(greatest(0.05, 1 + g.l * g.s * {z}), 1 / g.l)"
        value = f"round(({size}) * {scale}, {places})"
        join = f"{table} g, {subject}" if subject else f"{table} g WHERE TRUE"
        self.update(role, value, f"{join} AND g.sex = {sex} AND g.age_months = {age}")

    def write_weight(self, role):
        scale = {"kg": 1, "g": 1000, "oz": OUNCES_PER_KG, "lb": POUNDS_PER_KG}[role.unit]
        return self.growth(role, "realism_weight", scale, 0 if role.unit == "g" else 1)

    def write_height(self, role):
        scale = {"cm": 1, "in": 1 / CM_PER_INCH, "m": 0.01}[role.unit]
        return self.growth(role, "realism_stature", scale, 2 if role.unit == "m" else 1)

    def vital(self, role, measure):
        if not self.have["realism_vitals"]:
            return False
        age, _, _, subject = self.person(role)
        value = f"CAST(round(v.p50 + {_spread('r.rowid', 41)} * (v.p90 - v.p10) / 2.563) AS BIGINT)"
        join = f"realism_vitals v, {subject}" if subject else "realism_vitals v WHERE TRUE"
        self.update(role, value, f"{join} AND v.measure = {_text(measure)} AND {age} >= v.age_from AND {age} < v.age_to")

    def write_heart_rate(self, role):
        return self.vital(role, "heart_rate")

    def write_respiratory_rate(self, role):
        return self.vital(role, "respiratory_rate")

    def pressure(self, role, measure):
        t = self.tuning
        age, _, _, subject = self.person(role)
        z = _spread("r.rowid", 53)
        if self.have["realism_pressure"]:
            # Reference values recorded in children under anaesthesia, by age.
            fallback = t["diastolic_sd_fallback"] if measure == "diastolic" else t["systolic_sd_fallback"]
            value = f"CAST(round(b.{measure}_p50 + {z} * COALESCE(b.{measure}_sd, {_n(fallback)})) AS BIGINT)"
            join = f"realism_pressure b, {subject}" if subject else "realism_pressure b WHERE TRUE"
            self.update(role, value, f"{join} AND {age} >= b.age_from AND {age} < b.age_to")
            return
        # Approximate medians for age, in mmHg, rising by a fixed amount each year to a ceiling.
        years = f"({age} / 12.0)"
        systolic = f"least({_n(t['systolic_highest'])}, {_n(t['systolic_base'])} + {_n(t['systolic_per_year'])} * {years})"
        diastolic = f"least({_n(t['diastolic_highest'])}, {_n(t['diastolic_base'])} + {_n(t['diastolic_per_year'])} * {years})"
        median = {"systolic": systolic, "diastolic": diastolic, "mean": f"({diastolic} * 2 + {systolic}) / 3"}[measure]
        value = f"CAST(round({median} + {z} * {_n(t['pressure_fallback_spread'])}) AS BIGINT)"
        self.update(role, value, subject.replace(" WHERE ", " WHERE TRUE AND ", 1) if subject else "")

    def write_blood_pressure(self, role):
        """Systolic over diastolic as text, as in 110/70, which is how a charted pressure is often held."""
        t = self.tuning
        age, _, _, subject = self.person(role)
        high, low = _spread("r.rowid", 53), _spread("r.rowid", 59)
        if not self.have["realism_pressure"]:
            return False
        value = (f"CAST(CAST(round(b.systolic_p50 + {high} * COALESCE(b.systolic_sd, {_n(t['systolic_sd_fallback'])})) "
                 f"AS BIGINT) AS VARCHAR) || '/' || "
                 f"CAST(CAST(round(b.diastolic_p50 + {low} * COALESCE(b.diastolic_sd, {_n(t['diastolic_sd_fallback'])})) "
                 f"AS BIGINT) AS VARCHAR)")
        join = f"realism_pressure b, {subject}" if subject else "realism_pressure b WHERE TRUE"
        self.update(role, value, f"{join} AND {age} >= b.age_from AND {age} < b.age_to")

    def write_end_tidal_co2(self, role):
        t = self.tuning
        value = (f"least({_n(t['end_tidal_co2_highest'])}, greatest({_n(t['end_tidal_co2_lowest'])}, "
                 f"{_n(t['end_tidal_co2_mean'])} + {_spread('r.rowid', 61)} * {_n(t['end_tidal_co2_spread'])}))")
        self.update(role, f"CAST(round({value}) AS BIGINT)")

    def write_end_tidal_agent(self, role):
        t = self.tuning
        value = (f"least({_n(t['end_tidal_agent_highest'])}, greatest({_n(t['end_tidal_agent_lowest'])}, "
                 f"{_n(t['end_tidal_agent_mean'])} + {_spread('r.rowid', 67)} * {_n(t['end_tidal_agent_spread'])}))")
        self.update(role, f"round({value}, 1)")

    def write_systolic_pressure(self, role):
        return self.pressure(role, "systolic")

    def write_diastolic_pressure(self, role):
        return self.pressure(role, "diastolic")

    def write_mean_pressure(self, role):
        return self.pressure(role, "mean")

    # The roles that add rows of their own. Each new row copies a row of its table, so that it reaches the same
    # patient and anaesthetic through the same keys, and takes the role's code, a time and a value.

    def insert(self, role, values, source):
        """Adds the rows that source gives, as copies of the row r with the given columns written over."""
        names = [c.name for c in self.catalogue.table(role.table).columns.values()]
        chosen = ", ".join(values.get(name, f"r.{_quoted(name)}") for name in names)
        found = self.con.execute(f"INSERT INTO {_quoted(role.table)} SELECT {chosen} FROM {source}").fetchone()
        self.added += found[0] if found else 0

    def code(self, role):
        return f"CAST({_text(role.when_value)} AS {self.kinds[(role.table, role.when_column)]})"

    def bounded_mean(self, value):
        t = self.tuning
        return f"least({_n(t['mean_pressure_highest'])}, greatest({_n(t['mean_pressure_lowest'])}, round({value})))"

    def write_cuff_mean_pressure(self, role):
        """A mean pressure from the cuff beside each charted blood pressure of the same table, with the same time.

        The mean is the usual estimate from the pressure beside it, the diastolic and a third of the difference to the
        systolic, so that the two agree. A charted pressure that cannot be read as systolic over diastolic gets none.
        """
        if role not in self.listed or not (role.when_column and role.at_column):
            return "prerequisite"
        charted = next((r for r in self.by_role.get("blood_pressure", []) if r in self.done and r.table == role.table
                        and r.column == role.column and r.when_column == role.when_column and r.at_column == role.at_column),
                       None)
        if charted is None:
            return "prerequisite"
        text = f"CAST(r.{_quoted(role.column)} AS VARCHAR)"
        systolic = f"TRY_CAST(split_part({text}, '/', 1) AS DOUBLE)"
        diastolic = f"TRY_CAST(NULLIF(split_part({text}, '/', 2), '') AS DOUBLE)"
        mean = self.bounded_mean(f"{diastolic} + ({systolic} - {diastolic}) / 3.0")
        self.insert(role, {role.column: self.cast(role, f"CAST({mean} AS BIGINT)"), role.when_column: self.code(role)},
                    f"{_quoted(role.table)} AS r WHERE CAST(r.{_quoted(role.when_column)} AS VARCHAR) = "
                    f"{_text(charted.when_value)} AND {systolic} IS NOT NULL AND {diastolic} IS NOT NULL")

    def write_arterial_mean_pressure(self, role):
        """A mean pressure from an arterial line, at a fixed interval from the start to the end of a share of anaesthetics.

        Each reading copies the first row of its table that belongs to the anaesthetic. Its value is drawn from the
        reference mean pressure for the child's age, around a level of the anaesthetic's own that holds throughout,
        so that a child whose pressure runs low stays low for a while.
        """
        t = self.tuning
        start, stop = self.first("anaesthetic_start"), self.first("anaesthetic_stop")
        if role not in self.listed or not (role.when_column and role.at_column) or self.subjects is None \
                or start is None or stop is None or start.table == role.table or not self.con.execute(
                    "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = 'realism_anaesthetic'").fetchone()[0]:
            return "prerequisite"
        if not self.have["realism_pressure"]:
            return False
        if self.lineage is not None:
            first = (f"SELECT k, MIN(rn) AS rn FROM realism_owner_anaesthetic WHERE t = {_text(role.table)} GROUP BY k")
            subject = f"(SELECT o.s FROM realism_owner o WHERE o.t = {_text(role.table)} AND o.rn = f.rn)"
        else:
            # Row i belongs to anaesthetic i modulo the number of anaesthetics, so row k is the first of anaesthetic k.
            first = f"SELECT k, k AS rn FROM realism_anaesthetic WHERE k < {_n(self.sizes[role.table])}"
            subject = f"f.rn % {_n(self.subjects)}"
        every = _n(t["arterial_interval_minutes"])
        chosen = (f"SELECT a.k, a.began, a.ended, f.rn, {subject} AS s FROM realism_anaesthetic a "
                  f"JOIN ({first}) f ON f.k = a.k WHERE {_chance('a.k + 151', t['arterial_line_share'])}")
        times = (f"SELECT x.k, x.rn, x.s, x.began + to_minutes(CAST(x.n * {every} AS BIGINT)) AS taken, x.n FROM "
                 f"(SELECT c.*, unnest(generate_series(0, CAST(date_diff('minute', c.began, c.ended) // {every} AS BIGINT))) "
                 f"AS n FROM ({chosen}) c) x")
        age = "least(240, greatest(0, date_diff('month', sub.birth, p.taken)))"
        level, wander = _spread("p.k", 157), _spread("p.k * 100003 + p.n", 163)
        mean = self.bounded_mean(f"b.mean_p50 + ({level} + 0.5 * {wander}) * "
                                 f"COALESCE(b.mean_sd, {_n(t['pressure_fallback_spread'])})")
        self.insert(role, {role.column: self.cast(role, f"CAST({mean} AS BIGINT)"), role.when_column: self.code(role),
                           role.at_column: f"CAST(p.taken AS {self.kinds[(role.table, role.at_column)]})"},
                    f"({times}) p JOIN {_quoted(role.table)} AS r ON r.rowid = p.rn "
                    f"JOIN realism_subject sub ON sub.s = p.s "
                    f"JOIN realism_pressure b ON {age} >= b.age_from AND {age} < b.age_to")

    def write_oxygen_saturation(self, role):
        low, high = self.tuning["oxygen_saturation_lowest"], self.tuning["oxygen_saturation_highest"]
        self.update(role, f"{_n(low)} + hash(r.rowid + 61) % {_n(high - low + 1)}")

    def write_temperature(self, role):
        low, high = self.tuning["temperature_lowest_c"], self.tuning["temperature_highest_c"]
        celsius = f"{_n(low)} + (hash(r.rowid + 67) % {round((high - low) * 10) + 1}) / 10.0"
        self.update(role, f"round({celsius if role.unit == 'c' else f'({celsius}) * 9 / 5 + 32'}, 1)")

    def write_medication_name(self, role):
        return self.one_of(role, self.medications, 71)

    def one_of(self, role, values, salt):
        if not values:
            return False
        listed = ", ".join(_text(value) for value in values)
        self.update(role, f"([{listed}])[CAST(1 + hash(r.rowid + {salt}) % {len(values)} AS BIGINT)]")

    def write_procedure_name(self, role):
        return self.one_of(role, self.procedures, 109)

    def write_diagnosis_code(self, role):
        return self.one_of(role, self.diagnoses, 113)

    def write_death_date(self, role):
        """Nearly everyone is alive. A small share of subjects has a date of death, after their last event."""
        if self.subjects is None:
            self.run(f"UPDATE {_quoted(role.table)} SET {_quoted(role.column)} = NULL")
            return
        t = self.tuning
        least, most = t["death_after_least_days"], t["death_after_most_days"]
        died = (f"least(f.latest + to_days(CAST({_n(least)} + hash(r.rowid + 131) % {_n(max(1, most - least))} AS BIGINT)), "
                f"greatest(f.latest + to_days({_n(least)}), CAST(current_date AS TIMESTAMP)))")
        if self.lineage is not None:
            value = f"CASE WHEN {_chance('o.s + 127', t['death_share'])} THEN {died} END"
            self.update(role, value, f"realism_owner o LEFT JOIN realism_reference f ON f.s = o.s "
                                     f"WHERE o.t = {_text(role.table)} AND o.rn = r.rowid")
            return
        subject = self.subject_of(role.table)
        value = (f"CASE WHEN {_chance(f'{subject} + 127', t['death_share'])} THEN "
                 f"(SELECT {died} FROM realism_reference f WHERE f.s = {subject}) END")
        self.update(role, value)

    def write_anaesthetic_stop(self, role):
        t = self.tuning
        start = next((r for r in self.by_role.get("anaesthetic_start", []) if r.table == role.table), None)
        if start is None:
            return "prerequisite"
        # Where the check results give the spans from start to stop, the durations follow them.
        bands = self.spans.get((role.table, start.column, role.column))
        minutes = _drawn(bands, "r.rowid", 79) if bands else None
        if minutes is not None:
            t.set_from_checks("anaesthetic_durations_minutes", bands)
        else:
            durations = t["anaesthetic_durations_minutes"]
            minutes = f"([{', '.join(map(_n, durations))}])[CAST(1 + hash(r.rowid + 79) % {len(durations)} AS BIGINT)]"
        value = (f"CAST(r.{_quoted(start.column)} AS TIMESTAMP) + "
                 f"to_minutes(greatest({_n(t['shortest_anaesthetic_minutes'])}, {minutes}))")
        self.update(role, value)
