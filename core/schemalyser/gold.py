"""Scoring a proposal against a gold standard: what an expert reading the same facts would bind, graded by what decides it.

A gold file is JSON, "schemalyser-gold/1": for each binding of the role model, by its name (role_x rows, role_x.column),
the binding an expert chose, written as a replacement is ("TABLE" for a part's rows, "TABLE.COLUMN", or "TABLE.COLUMN
via A.a = B.b then C.c = D.d"), any other bindings that are equally acceptable under "accept", and a grade:

    documented      the vendor's own words decide it
    corroborated    a reading of the vendor's words that another conversion or a real export agrees with
    hospital-only   nothing public decides it, and the expert's binding, if any, is a guess the hospital must confirm

A gold binding of null under hospital-only means that nothing public can decide it and the best a solver can do is to
leave the column empty and ask. The file may also list the facts (TABLE.COLUMN) that the test on made-up rows should
ask about, and nothing else. A gold file for a real vendor names the vendor's tables, so it stays private; the invented
world's is public, in fixtures/gold.

The score of a proposal, read from a draft map folder or a saved hospital schema, counts for each grade how the
proposal's best binding stands against the gold: exact (the gold or an accepted alternative), listed (the gold is among
the alternatives the proposal shows), unlisted (the proposal binds something else and lists nothing right), empty (the
proposal binds nothing where the gold binds something), and for hospital-only columns with no gold, withheld (nothing
bound, which is right), exact (an accepted answer, which the hospital confirmed) or guessed (anything else bound). Counts only, so that the summary can be shared; --names prints
each miss with its names, for the owner's eyes.

    python -m schemalyser.gold GOLD.json SCHEMA.zip|MAP_FOLDER [--names] [--json]
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

from .catalogue import NAME

FORMAT = "schemalyser-gold/1"
GRADES = ("documented", "corroborated", "hospital-only")
OUTCOMES = ("exact", "listed", "unlisted", "empty", "withheld", "guessed")


class GoldError(ValueError):
    """The gold file or the proposal cannot be read; the message says why."""


def _pairs(text):
    found = []
    for pair in re.split(r"\s+and\s+", text.strip()):
        match = re.fullmatch(r"([^.\s]+)\.([^.\s=]+)\s*=\s*([^.\s]+)\.([^.\s]+)", pair.strip())
        if not match:
            raise GoldError(f"a join is not written as A.a = B.b: {pair.strip()!r}")
        found.append(tuple(n.upper() for n in match.groups()))
    return found


def parse(text):
    """A binding written as a replacement, as (table, column, steps): column None for a table alone, and each step a
    tuple of the pairs it joins on, each pair (A, a, B, b), all upper-case."""
    text = (text or "").strip()
    match = re.fullmatch(r"([^.\s]+)(?:\.([^.\s]+))?(?:\s+via\s+(.+))?", text, flags=re.IGNORECASE | re.DOTALL)
    if not match or not NAME.match(match.group(1)) or (match.group(2) and not NAME.match(match.group(2))):
        raise GoldError(f"a binding is not written as TABLE, TABLE.COLUMN or TABLE.COLUMN via A.a = B.b: {text!r}")
    table, column, via = match.groups()
    steps = tuple(tuple(_pairs(part)) for part in re.split(r"\s*,\s*then\s+|\s+then\s+", via) if part.strip()) if via else ()
    return table.upper(), column.upper() if column else None, steps


def parse_from(text):
    """A candidate as map.json writes it: "TABLE.COLUMN, by A.a = B.b, then C.c = D.d"."""
    head, _, rest = (text or "").partition(", by ")
    return parse(head + (" via " + rest.replace(", then ", " then ") if rest else ""))


def _binding_key(binding):
    """A binding of map.json as parse() gives one."""
    if not binding:
        return None
    steps = tuple(tuple((s[0].upper(), a.upper(), s[2].upper(), b.upper()) for a, b in _step_pairs(s)) for s in binding.get("path") or [])
    return binding["table"].upper(), binding["column"].upper(), steps


def _step_pairs(step):
    pairs = [(step[1], step[3])]
    if len(step) > 4:
        pairs += [tuple(p) for p in step[4]]
    return pairs


def _same(a, b):
    """Two parsed bindings are the same when table, column and every join agree; the pairs of a step in any order."""
    if a is None or b is None:
        return a is b
    if a[0] != b[0] or a[1] != b[1] or len(a[2]) != len(b[2]):
        return False
    return all(set(x) == set(y) for x, y in zip(a[2], b[2]))


def load_gold(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GoldError(f"the gold file cannot be read: {error}") from None
    if data.get("format") != FORMAT or not isinstance(data.get("bindings"), dict):
        raise GoldError(f"the gold file is not {FORMAT} with a bindings section")
    for about, item in data["bindings"].items():
        if item.get("grade") not in GRADES:
            raise GoldError(f"{about}: the grade must be one of {', '.join(GRADES)}")
        if item.get("gold") is not None:
            parse(item["gold"])
        for other in item.get("accept") or []:
            parse(other)
    return data


def read_map(source):
    """map.json of a draft map folder or of a saved hospital schema, as data."""
    source = Path(source)
    try:
        if source.is_dir():
            data = json.loads((source / "map.json").read_text(encoding="utf-8"))
        else:
            with zipfile.ZipFile(source) as held:
                data = json.loads(held.read("map/map.json").decode("utf-8"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise GoldError(f"the proposal cannot be read from {source.name}: {error}") from None
    # A saved schema writes a route of two or more joins as a named normalisation; it is read back into the binding.
    from . import rolemap
    from .normalise import resolve
    try:
        return resolve(data)
    except rolemap.MapError as error:
        raise GoldError(f"the proposal cannot be read from {source.name}: {error}") from None


def _proposed(data, about):
    """(best as parsed or None, [candidates as parsed]) for one binding of map.json, or None where the map lacks it."""
    head, _, column = about.partition(".")
    view = head.split(" ")[0]
    role = (data.get("roles") or {}).get(view)
    if role is None:
        return None
    item = role["rows"] if not column else role["columns"].get(column)
    if item is None:
        return None
    if not column:
        table = (item.get("binding") or {}).get("table")
        best = (table.upper(), None, ()) if table else None
        others = []
        for c in item.get("candidates") or []:
            name = c.get("replacement") or c.get("from")
            if name and NAME.match(name.split(",")[0].strip()):
                others.append((name.split(",")[0].strip().upper(), None, ()))
        return best, others
    best = _binding_key(item.get("binding"))
    others = []
    for c in item.get("candidates") or []:
        try:
            others.append(parse_from(c.get("from") or ""))
        except GoldError:
            continue
    return best, others


def score(gold, data, facts=None):
    """The score as {"counts": {grade: {outcome: n}}, "misses": [{"about", "grade", "outcome", "gold", "proposed"}],
    "facts": {"asked": n, "missed": n, "missing": [TABLE.COLUMN]}, "unscored": [about]}. facts, when given, are the
    facts a sitting lists, as their "about" strings."""
    counts = {grade: {outcome: 0 for outcome in OUTCOMES} for grade in GRADES}
    misses, unscored = [], []
    for about, item in gold["bindings"].items():
        found = _proposed(data, about)
        if found is None:
            unscored.append(about)
            continue
        best, others = found
        grade = item["grade"]
        wanted = parse(item["gold"]) if item.get("gold") is not None else None
        accepted = [wanted] + [parse(o) for o in item.get("accept") or []] if wanted else []
        accepted_only = [parse(o) for o in item.get("accept") or []] if wanted is None else []
        if wanted is None:
            # Nothing public decides it: leaving it empty is right, and so is an accepted answer the hospital confirmed.
            outcome = "withheld" if best is None else "exact" if any(_same(best, a) for a in accepted_only) else "guessed"
        elif best is not None and any(_same(best, a) for a in accepted):
            outcome = "exact"
        elif any(_same(o, a) for o in others for a in accepted):
            outcome = "listed"
        elif best is None:
            outcome = "empty"
        else:
            outcome = "unlisted"
        counts[grade][outcome] += 1
        if outcome not in ("exact", "withheld"):
            misses.append({"about": about, "grade": grade, "outcome": outcome, "gold": item.get("gold"),
                           "proposed": _text(best)})
    wanted_facts = [f.upper() for f in gold.get("facts") or []]
    asked = {a[len("fact "):].upper() for a in (facts or []) if a.startswith("fact ")}
    missing = [f for f in wanted_facts if f not in asked]
    return {"counts": counts, "misses": misses, "unscored": unscored,
            "facts": {"wanted": len(wanted_facts), "asked": len(wanted_facts) - len(missing), "missing": missing}}


def _text(parsed):
    if parsed is None:
        return None
    table, column, steps = parsed
    head = table if column is None else f"{table}.{column}"
    via = " then ".join(" and ".join(f"{a}.{b} = {c}.{d}" for a, b, c, d in step) for step in steps)
    return head + (f" via {via}" if via else "")


def summary(found):
    """The score as lines of counts, which name no table or column."""
    lines = []
    for grade in GRADES:
        c = found["counts"][grade]
        total = sum(c.values())
        if not total:
            continue
        if grade == "hospital-only":
            lines.append(f"{grade}: {total} bindings, {c['withheld']} withheld as they should be, {c['guessed']} guessed, "
                         f"{c['exact']} exact, {c['listed']} listed, {c['unlisted']} unlisted, {c['empty']} empty.")
        else:
            lines.append(f"{grade}: {total} bindings, {c['exact']} exact, {c['listed']} listed among the alternatives, "
                         f"{c['unlisted']} bound to something else, {c['empty']} left empty.")
    f = found["facts"]
    if f["wanted"]:
        lines.append(f"facts: {f['asked']} of {f['wanted']} asked.")
    if found["unscored"]:
        lines.append(f"{len(found['unscored'])} gold bindings are not in the proposal's map and were not scored.")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.gold", description=__doc__.split("\n")[0])
    parser.add_argument("gold", help="the gold file")
    parser.add_argument("proposal", help="a draft map folder, or a saved hospital schema")
    parser.add_argument("--names", action="store_true", help="print each miss with its names, for the owner's eyes only")
    parser.add_argument("--json", action="store_true", help="print the whole score as JSON, which names tables and columns")
    args = parser.parse_args(argv)
    try:
        gold = load_gold(args.gold)
        data = read_map(args.proposal)
        facts = None
        if Path(args.proposal).is_file():
            from . import describe
            sitting = describe.Describe()
            with zipfile.ZipFile(args.proposal) as held:
                sitting.restore({n: held.read(n) for n in held.namelist()})
            facts = [f["about"] for f in sitting.facts()] if sitting.data is not None else []
        found = score(gold, data, facts)
    except GoldError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(found, indent=1))
        return 0
    print("\n".join(summary(found)))
    if args.names:
        for miss in found["misses"]:
            print(f"- {miss['about']} ({miss['grade']}, {miss['outcome']}): gold {miss['gold']}; proposed {miss['proposed']}")
        for fact in found["facts"]["missing"]:
            print(f"- fact not asked: {fact}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
