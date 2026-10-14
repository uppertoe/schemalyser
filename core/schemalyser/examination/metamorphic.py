"""Stage 3 of the examination, the three metamorphic checks: changes to the shadow's rows whose effect on a correct
answer is known without knowing the answer.

An unrelated patient added changes nothing: a whole patient, with an anaesthetic and every row linked to it, is copied
with fresh keys and made unrelated in a way every question excludes, as a test patient or as an anaesthetic with no
start, with its times moved fifty years on; the answer must be identical. A duplicate is handled as the examinable
says: a sampled row of an outcome part is written again with a fresh key, and, where the part has amends_key and the
examinable says amended rows are replaced, a correction that amends the sampled row; under collapse the answer must be
identical, under keep it must change. A row moved to another encounter leaves the cohort: a sampled outcome row is
relinked to a new anaesthetic of the same patient thirty years on, with its own time left inside the first
anaesthetic's, and the answer must equal the answer with the row deleted, which catches a join by time; a row of a
part that carries only the stay is moved to a new stay of the same patient in the same way.

Each mutation runs inside a transaction that is rolled back, and the checks are applied to a sample of rows chosen by
seed, so that no single row decides.
"""
from .. import equivalence, question, rolemap, roleshadow

STAGE = "metamorphic"
SAMPLE = 8
SUFFIXES = {"unrelated": "~u", "duplicate": "~d", "amends": "~c", "moved": "~b", "stay": "~s"}
COHORT_PARTS = ("role_patient", "role_anaesthetic")


def run(path, seed=1, anaesthetics=120, sample=SAMPLE):
    """The three checks over a package file. Returns the stage's report."""
    sql, _, package = question.sql_and_decisions(path)
    return run_sql(sql, package.get("examinable") or {}, seed, anaesthetics, sample)


def run_sql(sql, examinable=None, seed=1, anaesthetics=120, sample=SAMPLE):
    """The three checks over a question's SQL, with the examinable's rules on duplicates and amendments where it has
    them. Returns {"stage", "checks": {name: {"applied", "samples", "failures", "outcome"}}, "outcome", "says"}."""
    examinable = examinable or {}
    model = rolemap.contract()
    shape = {v["name"]: v for v in model["views"]}
    outcome_parts = [p for p in question.parts_read(sql)[0] if p in shape and p not in COHORT_PARTS]
    con = roleshadow.role_shadow(seed=seed, anaesthetics=anaesthetics, with_planted=True, further=True)
    run = roleshadow.duckdb_runner(con)
    try:
        baseline = _normal(*run(sql))
        checks = {"unrelated": _unrelated(con, run, sql, baseline, shape, sample),
                  "duplicates": _duplicates(con, run, sql, baseline, shape, outcome_parts, examinable, sample),
                  "moved": _moved(con, run, sql, baseline, shape, outcome_parts, sample, examinable)}
    finally:
        con.close()
    failed = [name for name, check in checks.items() if check["outcome"] == "failed"]
    outcome = "failed" if failed else "passed"
    applied = [name for name, check in checks.items() if check["applied"]]
    if failed:
        says = "The question fails the metamorphic " + ("check " if len(failed) == 1 else "checks ") + ", ".join(failed) + ": " + \
            "; ".join(checks[name]["failures"][0] for name in failed) + "."
    else:
        says = f"The question passes the metamorphic checks applied ({', '.join(applied) or 'none'}) on a sample of {sample} rows at seed {seed}."
    return {"stage": STAGE, "seed": seed, "checks": checks, "outcome": outcome, "says": says}


def _normal(columns, rows):
    from .planted import comparable
    return list(columns), [[comparable(v) for v in row] for row in rows]


def _same(a, b):
    return equivalence.compare_answers(a, b)["same"]


def _types(view):
    return {c["name"]: c["type"] for c in view["columns"]}


def _copy_select(view, suffix, shift_years=0, link=None, value=None, overrides=None):
    """An INSERT ... SELECT that copies the rows of a view whose link column holds a value, every key column given a
    suffix, every date and time moved on by shift_years, with any override by column."""
    overrides = overrides or {}
    parts = []
    for name, kind in _types(view).items():
        if name in overrides:
            parts.append(f"{overrides[name]} AS {name}")
        elif kind == "key":
            parts.append(f"CASE WHEN {name} IS NULL THEN NULL ELSE {name} || '{suffix}' END AS {name}")
        elif kind in ("date", "datetime") and shift_years:
            parts.append(f"{name} + INTERVAL {shift_years} YEAR AS {name}")
        else:
            parts.append(name)
    where = f" WHERE {link} = ?" if link else ""
    return f"INSERT INTO {view['name']} SELECT {', '.join(parts)} FROM {view['name']}{where}", ([value] if link else [])


def _sample_anaesthetics(con, sample, offset=0):
    """A sample of anaesthetics, half of them planted neonates, which a neonatal question counts, and half of them
    generated with rows of every further part, which a question over any part reads, so that a copy of one bears on
    the answer of either kind of question."""
    base = ("SELECT a.anaesthetic_key FROM role_anaesthetic a JOIN role_patient p ON p.patient_key = a.patient_key "
            "WHERE p.is_test = 0 AND a.start_time IS NOT NULL AND a.stop_time > a.start_time ")
    half = max(1, sample // 2)
    planted = [k for (k,) in con.execute(base + "AND NOT EXISTS (SELECT 1 FROM role_anaesthetic_detail d WHERE d.anaesthetic_key = a.anaesthetic_key) "
                                         f"ORDER BY a.anaesthetic_key LIMIT {half} OFFSET {offset}").fetchall()]
    generated = [k for (k,) in con.execute(base + "AND EXISTS (SELECT 1 FROM role_anaesthetic_detail d WHERE d.anaesthetic_key = a.anaesthetic_key) "
                                           f"ORDER BY a.anaesthetic_key LIMIT {half} OFFSET {offset}").fetchall()]
    return planted + generated


def _unrelated(con, run, sql, baseline, shape, sample):
    failures = []
    for form in ("test patient", "no start"):
        for key in _sample_anaesthetics(con, sample, offset=3):
            (patient,) = con.execute("SELECT patient_key FROM role_anaesthetic WHERE anaesthetic_key = ?", [key]).fetchone()
            stays = [s for (s,) in con.execute("SELECT stay_key FROM role_anaesthetic_detail WHERE anaesthetic_key = ?", [key]).fetchall()]
            con.execute("BEGIN")
            try:
                suffix = SUFFIXES["unrelated"]
                overrides = {"is_test": "1"} if form == "test patient" else {}
                con.execute(*_copy_select(shape["role_patient"], suffix, 50, "patient_key", patient, overrides))
                overrides = {"start_time": "NULL"} if form == "no start" else {}
                con.execute(*_copy_select(shape["role_anaesthetic"], suffix, 50, "anaesthetic_key", key, overrides))
                for name, view in shape.items():
                    columns = _types(view)
                    if name in COHORT_PARTS:
                        continue
                    if "anaesthetic_key" in columns:
                        con.execute(*_copy_select(view, suffix, 50, "anaesthetic_key", key))
                    elif "stay_key" in columns and stays:
                        for stay in stays:
                            con.execute(*_copy_select(view, suffix, 50, "stay_key", stay))
                    elif "patient_key" in columns:
                        con.execute(*_copy_select(view, suffix, 50, "patient_key", patient))
                after = _normal(*run(sql))
            finally:
                con.execute("ROLLBACK")
            if not _same(baseline, after):
                failures.append(f"an unrelated patient added as a {form}, a copy of the patient of anaesthetic {key} with every row linked to it, changes the answer")
                break
    return {"applied": True, "samples": sample, "failures": failures, "outcome": "failed" if failures else "passed"}


def _sample_keys(con, view, outcome, sample, offset):
    """The keys of a sample of a part's rows: the rows the examinable names, of its kinds and forms, linked to an
    anaesthetic where the part links to one, and originals rather than corrections, so that a change to them bears on
    the answer."""
    own = view["key"][0]
    columns = _types(view)
    conditions, values = [], []
    if "anaesthetic_key" in columns:
        conditions.append("anaesthetic_key IS NOT NULL")
    if "kind" in columns and outcome.get("kinds"):
        conditions.append("kind IN (" + ", ".join("?" for _ in outcome["kinds"]) + ")")
        values += list(outcome["kinds"])
    if "volume_form" in columns and outcome.get("forms"):
        conditions.append("volume_form IN (" + ", ".join("?" for _ in outcome["forms"]) + ")")
        values += list(outcome["forms"])
    if "amends_key" in columns:
        conditions.append("amends_key IS NULL")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return [k for (k,) in con.execute(f"SELECT {own} FROM {view['name']}{where} ORDER BY {own} LIMIT {sample} OFFSET {offset}", values).fetchall()]


def _duplicates(con, run, sql, baseline, shape, outcome_parts, examinable, sample):
    outcome = examinable.get("outcome") or {}
    rule = outcome.get("duplicates")
    amended = outcome.get("amended")
    parts = [p for p in outcome_parts if p == outcome.get("part")] if outcome.get("part") else []
    if not parts or rule is None:
        return {"applied": False, "samples": 0, "failures": [], "outcome": "not applied",
                "says": "the examinable names no outcome part with a rule for duplicates"}
    failures = []
    for name in parts:
        view = shape[name]
        own = view["key"][0]
        columns = _types(view)
        keys = _sample_keys(con, view, outcome, sample, 1)
        for key in keys:
            con.execute("BEGIN")
            try:
                overrides = {}
                if "documented_time" in columns:
                    overrides["documented_time"] = "documented_time + INTERVAL 1 HOUR"
                statement, values = _copy_select(view, SUFFIXES["duplicate"], 0, own, key, overrides)
                # The copy keeps every link, so only the row's own key takes the suffix.
                for link in ("anaesthetic_key", "patient_key", "stay_key", "amends_key", "order_key", "note_key", "person_key"):
                    if link in columns and link != own:
                        statement = statement.replace(f"CASE WHEN {link} IS NULL THEN NULL ELSE {link} || '{SUFFIXES['duplicate']}' END AS {link}", link)
                con.execute(statement, values)
                after = _normal(*run(sql))
            finally:
                con.execute("ROLLBACK")
            same = _same(baseline, after)
            if rule == "keep" and same:
                failures.append(f"a duplicate of a row of {name} leaves the answer unchanged, and the examinable says each row counts")
                break
            if rule != "keep" and not same:
                failures.append(f"a duplicate of a row of {name} changes the answer, and the examinable says duplicates collapse")
                break
        if "amends_key" in columns and amended == "replaced" and not failures:
            for key in keys:
                con.execute("BEGIN")
                try:
                    overrides = {"amends_key": f"'{key}'", "source_kind": "'correction'"}
                    if "documented_time" in columns:
                        overrides["documented_time"] = "documented_time + INTERVAL 1 DAY"
                    statement, values = _copy_select(view, SUFFIXES["amends"], 0, own, key, overrides)
                    for link in ("anaesthetic_key", "patient_key", "stay_key", "order_key", "note_key", "person_key"):
                        if link in columns and link != own:
                            statement = statement.replace(f"CASE WHEN {link} IS NULL THEN NULL ELSE {link} || '{SUFFIXES['amends']}' END AS {link}", link)
                    con.execute(statement, values)
                    after = _normal(*run(sql))
                finally:
                    con.execute("ROLLBACK")
                if not _same(baseline, after):
                    failures.append(f"a correction that amends a row of {name} with the same values changes the answer, and the examinable says an amended row is replaced")
                    break
    return {"applied": True, "samples": sample, "failures": failures, "outcome": "failed" if failures else "passed"}


def _moved(con, run, sql, baseline, shape, outcome_parts, sample, examinable=None):
    outcome = (examinable or {}).get("outcome") or {}
    if not outcome_parts:
        return {"applied": False, "samples": 0, "failures": [], "outcome": "not applied", "says": "the question reads no part beyond the patients and the anaesthetics"}
    failures = []
    for name in outcome_parts:
        view = shape[name]
        own = view["key"][0]
        columns = _types(view)
        if "anaesthetic_key" in columns:
            keys = _sample_keys(con, view, outcome if outcome.get("part") == name else {}, sample, 2)
            rows = [(k, a) for k, a in con.execute(f"SELECT {own}, anaesthetic_key FROM {name} WHERE anaesthetic_key IS NOT NULL").fetchall() if k in keys]
            for key, anaesthetic in rows:
                deleted = _world(con, run, sql, [(f"DELETE FROM {name} WHERE {own} = ?", [key])])
                moved = _world(con, run, sql, [
                    _copy_select(shape["role_anaesthetic"], SUFFIXES["moved"], 30, "anaesthetic_key", anaesthetic),
                    (f"UPDATE {name} SET anaesthetic_key = ? WHERE {own} = ?", [f"{anaesthetic}{SUFFIXES['moved']}", key])])
                if not _same(deleted, moved):
                    failures.append(f"a row of {name} moved to another anaesthetic of the same patient, thirty years on, with its time left inside the first, "
                                    f"gives a different answer from the row deleted, so the question attributes it by time rather than by key")
                    break
        elif "stay_key" in columns and "patient_key" in columns:
            rows = con.execute(f"SELECT {own}, stay_key FROM {name} WHERE stay_key IS NOT NULL ORDER BY {own} LIMIT {sample} OFFSET 5").fetchall()
            for key, stay in rows:
                deleted = _world(con, run, sql, [(f"DELETE FROM {name} WHERE {own} = ?", [key])])
                moved = _world(con, run, sql, [
                    _copy_select(shape["role_stay"], SUFFIXES["stay"], 30, "stay_key", stay),
                    (f"UPDATE {name} SET stay_key = ? WHERE {own} = ?", [f"{stay}{SUFFIXES['stay']}", key])])
                if not _same(deleted, moved):
                    failures.append(f"a row of {name} moved to another stay of the same patient, thirty years on, gives a different answer from the row deleted")
                    break
    return {"applied": True, "samples": sample, "failures": failures, "outcome": "failed" if failures else "passed"}


def _world(con, run, sql, mutations):
    con.execute("BEGIN")
    try:
        for statement, values in mutations:
            con.execute(statement, values)
        return _normal(*run(sql))
    finally:
        con.execute("ROLLBACK")
