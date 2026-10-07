"""The functions the page's worker calls. They take and return plain values only."""
import io
import json
import re
import os
import shutil
import zipfile

from .analysis import Analysis
from .catalogue import CatalogueError
from .checks import ChecksError
from .extract import decode
from .vocabulary import NOTHING_UNREAD

_analysis = None


def _skipped(checks):
    """How many checks the script left out for want of time, and how many because a table is large."""
    skipped = set(getattr(checks, "skipped", ()))
    return (sum(1 for item in skipped if item[3] == "time"), sum(1 for item in skipped if item[3] == "size"))


def _bytes(data):
    # Under Pyodide a JavaScript Uint8Array arrives as a proxy with to_bytes().
    return data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)


def start(catalogue, rules=None, checks=None):
    """Begins an analysis. Returns "ok", "ok-no-headers", or "catalogue" or "checks" if that file cannot be read."""
    global _analysis
    _analysis = None
    try:
        _analysis = Analysis(decode(_bytes(catalogue)), decode(_bytes(rules)) if rules is not None else None,
                             checks_csv=decode(_bytes(checks)) if checks is not None else None)
    except CatalogueError:
        return "catalogue"
    except ChecksError:
        return "checks"
    return "ok-no-headers" if _analysis.catalogue.assumed_headers else "ok"


def check_script():
    """The check script that the page offers: the boundary's, when it ran, which also plans checks for the
    columns that the conversion maps, and otherwise the analysis's own, with the spans and fanout checks."""
    if _boundary is not None:
        return _boundary["files"]["check_script.sql"]
    return _analysis.check_script(include_spans=True, include_fanout=True)


def add(name, data):
    _analysis.add_request(name, decode(_bytes(data)))


def finish():
    """Returns the pack, the request index and two counts, as JSON."""
    from . import vocabulary as v
    pack = _analysis.pack()
    checks = None
    if _analysis.checks is not None:
        confirmed = {f[1:3] + (f[5],) for r in _analysis._requests.values() for f in r.findings
                     if f[0] == "filter" and f[5]}
        checks = {
            "used": v.checks_used_sentence(len(confirmed), len({c[:2] for c in confirmed})),
            "unanswered": " ".join(part for part in (
                v.checks_unanswered_sentence(_analysis.checks.errors) if _analysis.checks.errors else "",
                v.checks_skipped_sentence(*_skipped(_analysis.checks))) if part),
            "noHeaders": _analysis.checks.assumed_headers,
        }
    return json.dumps({"pack": pack, "index": _analysis.request_index(), "summary": _analysis.summary,
                       "nothingUnread": NOTHING_UNREAD, "checks": checks})


def pack_zip():
    """The pack as one zip file, with fixed timestamps so that two runs give identical bytes.

    When the boundary has run, its outputs follow in the folder boundary/, exactly as the boundary
    command writes them. The sandbox reads only the pack's own files at the top of the zip.
    """
    out = io.BytesIO()
    files = dict(_analysis.pack())
    if _boundary is not None:
        files.update({f"{BOUNDARY_FOLDER}/{name}": text for name, text in sorted(_boundary["files"].items())})
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), text)
    return out.getvalue()


def clear():
    global _analysis, _sandbox, _boundary, _checks, _pasted, _pasted_profile, _profile, _first_names, _facts, _settings
    _analysis = None
    _sandbox = None
    _boundary = None
    _checks = None
    _pasted = None
    _pasted_profile = None
    _profile = None
    _first_names = None
    _facts = None
    _settings = None
    _outcomes.clear()
    shutil.rmtree(BOUNDARY_ROOT, ignore_errors=True)


# The boundary. The page writes the state and the requests into the worker's own file system, in the
# layout of the state folder, and runs boundary.produce over them, so that the page and the boundary
# command can never disagree. Nothing here is written outside the worker's memory.

BOUNDARY_ROOT = "/tmp/schemalyser-boundary"
BOUNDARY_FOLDER = "boundary"
# The limits on a path that boundary_put writes: the characters in one name, the characters in the
# whole path, and the number of folders and file in it. A path beyond any of them is left out.
MAX_NAME_LENGTH = 255
MAX_PATH_LENGTH = 1024
MAX_PATH_DEPTH = 32
_boundary = None
# The check results that the checklists use: those of the state, with every result pasted since added; the
# results pasted since the page was cleared, which a new analysis keeps; and the requests commit of the last
# run, which a run after a paste keeps.
_checks = None
_pasted = None
_requests_commit = None
# The same for the core profile: the rows pasted since the page was cleared, and the profile that the
# checklists use, as the text of core-profile.csv.
_pasted_profile = None
_profile = None
# The facts that a person confirmed on this page since it was cleared, which a new analysis keeps.
_facts = None
# The audit's settings entered on this page since it was cleared, as the text of audit.json, which a new analysis keeps.
_settings = None
# The files that boundary_put could not write, by kind, which the summary counts as left out.
_put_refused = {"state": 0, "requests": 0}
# The kinds of checklist item that a sample query from the data team can settle, and the sentence in
# the evidence in hand that says that no sample query yet does.
_SAMPLE_KINDS = {"table": "used", "column": "used", "relationship": "joined", "filter": "filtered"}


def boundary_begin():
    global _boundary, _checks
    _boundary = None
    _checks = None
    shutil.rmtree(BOUNDARY_ROOT, ignore_errors=True)
    for kind in ("state", "requests"):
        os.makedirs(f"{BOUNDARY_ROOT}/{kind}")
        _put_refused[kind] = 0


def _safe_path(path):
    """A relative path inside the folder, or None. Empty parts, '.' and '..' are refused, as is a path beyond the limits."""
    text = str(path).replace("\\", "/")
    parts = text.split("/")
    if not parts or len(text) > MAX_PATH_LENGTH or len(parts) > MAX_PATH_DEPTH or any(
            part in ("", ".", "..") or "\x00" in part or len(part) > MAX_NAME_LENGTH for part in parts):
        return None
    return "/".join(parts)


def boundary_put(kind, path, data):
    """Writes one file of the state or of the requests. Returns False, and counts the file as left out, when the path cannot be used."""
    if kind not in ("state", "requests"):
        return False
    relative = _safe_path(path)
    target = f"{BOUNDARY_ROOT}/{kind}/{relative}"
    try:
        if relative is None:
            raise ValueError
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(_bytes(data))
    except (OSError, ValueError, RecursionError):
        # A name too long for the file system, or a file where a folder must be, as with a.sql and then a.sql/b.sql.
        _put_refused[kind] += 1
        return False
    return True


def _group(row, total):
    """Where the page lists an item: answered, settled by a sample query, or needing something else."""
    from . import target
    if row["status"] == "answered":
        return "answered"
    prefix = _SAMPLE_KINDS.get(row["kind"])
    if prefix is not None:
        none = target.WORDING["in_hand"][f"{prefix}_none"].format(total=total)
        if none in row["evidence_in_hand"]:
            return "sql"
    return "other"


def _actor(row):
    """Who must act on an item and how, in the readiness statement's own words, or "" where it has none."""
    from . import target
    who, how = target.WORDING["who"].get(row["who"]), target.WORDING["how"].get(row["mechanism"])
    if not who or not how:
        return ""
    item = target._item(row) if target.WORDING["item"].get(row.get("_wording")) or \
        target.WORDING["item"].get(row["kind"]) else ""
    return target.WORDING["readiness"]["act"].format(who=who, item=item or "this point", how=how)


def _reading():
    """The catalogue and the site rules by which check results are read: the analysis's, or else the state's own."""
    if _analysis is not None:
        return _analysis.catalogue, _analysis.rules
    def text(name):
        path = f"{BOUNDARY_ROOT}/state/{name}"
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return decode(f.read())
    from . import boundary
    try:
        reading = Analysis(text(boundary.CATALOGUE) or "", text(boundary.RULES))
    except (CatalogueError, ValueError, TypeError, AttributeError):
        return None, None
    return reading.catalogue, reading.rules


def _remains(row):
    """Whether an item of the first phase remains to be settled: a blocking item that is open, or any item that a person
    was asked about and that nothing has yet settled, such as a doubt, a match given in place of another, a code marked not
    sure or a name whose visibility is not known."""
    if (row.get("phase") or "source") != "source" or row["status"] == "answered":
        return False
    # A point that a training database could not settle is to be asked again on the production copy, and is counted apart.
    if row.get("_again"):
        return False
    if row["blocking"] == "yes" and row["status"] == "open":
        return True
    if row.get("_fact") in ("unsure", "measure", "noted", "no"):
        return True
    # A route rests on a name that is not visible, so it remains until a person has said that the name does not exist here.
    return row["question_id"].startswith("route-")


def _withdrawable(row, rows):
    """The facts that a person gave and that settled or shaped an item, as patterns that fact_withdraw takes, or []."""
    names = row.get("_names") or {}
    if row["question_id"] == "count-by-year":
        return [{"kind": "count"}] if row.get("_count") else []
    if row.get("_fact") == "measure" and row["kind"] == "relationship":
        # A match that a person gave in place of another: withdrawing it withdraws the answer about the match it replaced.
        import re
        found = re.search(r"in place of ([A-Za-z0-9_$#@]+\.[A-Za-z0-9_$#@]+) = ([A-Za-z0-9_$#@]+\.[A-Za-z0-9_$#@]+)", row.get("evidence_in_hand", ""))
        return [{"kind": "join", "left": found.group(1), "right": found.group(2)}] if found else []
    if not row.get("_fact") or row["_fact"] == "measure":
        return []
    if row["question_id"].startswith("route-"):
        # The question about a name that is not visible was answered for every route that rests on it, so all go together.
        return [{"kind": "route", "step": (r.get("_names") or {}).get("step", "")} for r in rows
                if r["question_id"].startswith("route-") and (r.get("_names") or {}).get("missing") == names.get("missing")]
    if row["kind"] == "relationship":
        return [{"kind": "join", "left": names.get("left", ""), "right": names.get("right", "")}]
    if row["kind"] == "filter":
        return [{"kind": "filter", "column": names.get("column", "")}]
    if row["kind"] == "codes":
        if row.get("_wording") == "codes-concept":
            return [{"kind": "codes", "vocabulary": v.strip(), "concept": names.get("concept")}
                    for v in str(names.get("vocabularies", "")).replace(" and ", ",").split(",") if v.strip()]
        if names.get("vocabulary"):
            return [{"kind": "codes", "vocabulary": names["vocabulary"]}]
    return []


def _routes(rows):
    """The routes that the catalogue settled, one sentence for each name that is not visible, however many steps rest on it."""
    from . import target
    template = target.WORDING["in_hand"].get("route_taken", "")
    if "{what} from {tables}" not in template:
        return []   # the checklist's own sentences, one for each step, are shown instead
    by_missing = {}
    for row in rows:
        names = row.get("_names") or {}
        if row["question_id"].startswith("route-") and names.get("missing"):
            by_missing.setdefault(names["missing"], []).append(names)
    said = []
    for missing, group in by_missing.items():
        # Two steps that give the same thing, such as the anaesthetics, are named once, with the tables of both.
        tables = {}
        for n in group:
            for table in re.split(r", | and ", str(n.get("tables", "other tables"))):
                tables.setdefault(n.get("what", "rows"), [])
                if table and table not in tables[n.get("what", "rows")]:
                    tables[n.get("what", "rows")].append(table)
        parts = [f"{what} from {target._join(found)}" for what, found in tables.items()]
        sentence = template.replace("{what} from {tables}", ", and the ".join(parts)).format(missing=missing)
        effects = list(dict.fromkeys(n["effect"] for n in group if n.get("effect")))
        said.append(" ".join([sentence] + effects))
    return said


def _matches(fact, pattern):
    """Whether a fact is the one that a withdrawal pattern names: the same kind, and the same value for each key it gives."""
    if not isinstance(pattern, dict) or fact.get("kind") != pattern.get("kind"):
        return False
    if fact["kind"] == "join":
        return frozenset({str(fact["left"]).upper(), str(fact["right"]).upper()}) == \
            frozenset({str(pattern.get("left", "")).upper(), str(pattern.get("right", "")).upper()})
    return all(str(fact.get(key, "")).upper() == str(value).upper() for key, value in pattern.items() if key != "kind")


def _needs(rows):
    """What a person must still do for the first phase, by kind, and how many points are only less certain."""
    first = [r for r in rows if (r.get("phase") or "source") == "source"]
    # An empty cohort, an empty list of what is charted and a count of none for the chosen codes head the list.
    must = sorted((r for r in first if _remains(r)), key=lambda r: not r.get("_top"))
    asked = [r for r in must if r.get("_ask") and not r.get("_fact")]
    queried = [r for r in must if r not in asked and r.get("_queries")]
    return {"questions": len(asked), "queries": len(queried), "other": len(must) - len(asked) - len(queried),
            "lessCertain": sum(1 for r in first if r["status"] == "partly" and not _remains(r) and not r.get("_again")),
            "remaining": [r["question_id"] for r in must],
            # What a training database could not settle, to be asked again on the production copy.
            "again": [r["question_id"] for r in first if r.get("_again") and r["status"] != "answered"],
            "settled": sum(1 for r in first if r["status"] == "answered")}


def boundary_run(state_commit=None, requests_commit=None):
    """Runs the boundary over what boundary_put wrote. Returns JSON for the page.

    {"ok": false, "problem": key} when the state cannot be used, where key names the sentence in
    boundary.WORDING; otherwise the checklist of each target query, with the facts the page shows.
    """
    global _boundary, _checks, _requests_commit, _profile
    from . import boundary
    from . import profile as core_profile
    from .checks import Checks
    _boundary = None
    _requests_commit = requests_commit
    path = f"{BOUNDARY_ROOT}/state/{boundary.CHECKS}"
    _checks, unreadable = None, False
    catalogue, rules = _reading()
    if catalogue is None:
        unreadable = True       # the boundary refuses the catalogue in its own words
    elif os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                _checks = Checks.from_csv(decode(f.read()), catalogue, rules)
        except ChecksError:
            unreadable = True   # the boundary refuses the file in its own words
    if _pasted is not None and not unreadable:
        # The results pasted since the page was cleared are added to those of the state, which then differs
        # from any commit.
        _checks = _pasted if _checks is None else _checks.merged(_pasted)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(_checks.to_csv())
        state_commit = None
    profile_path, conversion = f"{BOUNDARY_ROOT}/state/{boundary.PROFILE}", f"{BOUNDARY_ROOT}/state/{boundary.CONVERSION}"
    _profile = None
    if os.path.isfile(profile_path):
        with open(profile_path, "rb") as f:
            _profile = decode(f.read())
    if _pasted_profile and os.path.isdir(conversion):
        # The rows pasted into the core profile since the page was cleared are added to the state's profile.
        try:
            _profile = core_profile.merged(_profile, _pasted_profile, conversion)
            with open(profile_path, "w", encoding="utf-8", newline="") as f:
                f.write(_profile)
            state_commit = None
        except (core_profile.ProfileError, ValueError, KeyError, IndexError, OSError):
            pass    # the boundary refuses the state's profile in its own words
    _write_facts(catalogue)
    if _settings is not None:
        # The settings entered on this page since it was cleared take the place of the state's own.
        with open(f"{BOUNDARY_ROOT}/state/{boundary.SETTINGS}", "w", encoding="utf-8") as f:
            f.write(_settings)
        state_commit = None
    if _facts:
        state_commit = None
    try:
        # The reference query is composed only when the page will show it: once nothing remains and a study period is set.
        outputs, facts = boundary.produce(f"{BOUNDARY_ROOT}/state", f"{BOUNDARY_ROOT}/requests",
                                          state_commit or None, requests_commit or None, refused=dict(_put_refused),
                                          draft_when=lambda rows, settings: not _needs(rows)["remaining"] and bool(settings.get("from")))
    except boundary.BoundaryError as error:
        problem = next((key for key, text in boundary.WORDING.items() if text == str(error)), "other")
        return json.dumps({"ok": False, "problem": problem})
    files = boundary.everything(outputs, facts)
    _boundary = {"files": files}
    total = facts["summary"]["files"]
    targets = []
    for t in facts["targets"]:
        rows = [{"id": row["question_id"], "kind": row["kind"], "status": row["status"],
                 "blocking": row["blocking"] == "yes", "question": row["question"],
                 "needed": row["evidence_needed"], "inHand": row["evidence_in_hand"],
                 "actor": _actor(row), "group": _group(row, total),
                 # Optional columns that the checklist may carry: what the step is trying to do, and how
                 # the sample queries get between the same tables instead. Read by name, empty when absent.
                 "intent": row.get("intent") or "", "route": row.get("route") or "",
                 # The plain queries that would answer an open item, by identifier, with why and whether they can run.
                 "queryState": row.get("query_state") or "", "queryReason": row.get("query_reason") or "",
                 "queryIds": list(row.get("_queries") or []), "stage": row.get("phase") or "source",
                 # The question that a colleague can answer from knowledge, and the answer a person gave, if any.
                 "ask": row.get("_ask"), "fact": row.get("_fact") or "", "note": row.get("_note") or "",
                 # The facts that a person gave about the item, which the page can withdraw, and the counts by year once seen.
                 "withdraw": _withdrawable(row, t["rows"]),
                 # Whether the item heads what remains, and, for a route, the name that is not visible, which the page
                 # uses to name each such table once.
                 "top": bool(row.get("_top")), "again": bool(row.get("_again")) and row["status"] != "answered",
                 "missing": (row.get("_names") or {}).get("missing", "")
                 if row["question_id"].startswith("route-") else "",
                 **({"years": row["_count"]["years"]} if row.get("_count") else {})} for row in t["rows"]]
        offered = t.get("queries") or {"sizes": None, "queries": []}
        # The source draft, which the page offers as the audit query once the first stage is ready. It stays
        # in this page, which runs inside the hospital, and is written to no file unless the user saves it.
        # A draft not yet composed is marked as waiting, so that the page can say what it waits for.
        draft = {"sql": "", "countsOnly": False, "restructured": False, "tables": [], "waiting": True} if t.get("draft_pending") else None
        if t.get("draft"):
            from . import target as target_module
            facts_of = target_module.draft_facts(t["draft"], _reading()[0])
            sizes = _checks.rows if _checks is not None else {}
            draft = {"sql": t["draft"], "countsOnly": facts_of["counts_only"], "restructured": bool(t.get("draft_restructured")),
                     # The reference query as a script that is safe to run, or the reason that it is not offered to be run.
                     "script": t.get("draft_script"),
                     "tables": [{"name": n, "rows": next((v for k, v in sizes.items() if k.upper() == n.upper()), None)}
                                for n in facts_of["tables"]]}
        targets.append({"name": t["name"], "counts": t["counts"], "steps": t["steps"],
                        "verdict": boundary.verdict(t), "readiness": t["readiness"], "rows": rows,
                        "sizes": offered["sizes"],
                        "queries": offered["queries"] + ([{"id": "yearcount", "sql": t["year_count"], "state": "ready", "table": ""}]
                                                         if t.get("year_count") else []),
                        "profile": offered.get("profile") or [], "draft": draft, "stages": t.get("stages"),
                        "questions": t.get("questions") or "", "specification": t.get("specification") or "",
                        # The routes that the catalogue settled, one sentence each, shown at the head of the checklist.
                        "routes": _routes(t["rows"]) or t.get("routes") or [],
                        "settings": t.get("settings") or {}, "kinds": [[k, n] for k, n in t.get("kinds") or []],
                        # What choosing the kinds of anaesthetic costs, in one sentence, shown above the ticks.
                        "kinds_cost": t.get("kinds_cost") or "",
                        # The optional count of how often each chosen code is charted, with what an earlier count gave.
                        "charted": {k: v for k, v in t["charted"].items() if k != "single"} if t.get("charted") else None,
                        # The list of what is charted on the cohort in one year, from which the codes are chosen.
                        "listed": t.get("listed"),
                        "needs": _needs(t["rows"]),
                        "stageVerdicts": [line for line in t["readiness"].splitlines()
                                          if line.startswith(("The question is ready", "The question is not yet ready"))]})
    w = boundary.WORDING
    notes = [w["target_refused"].format(name=name) for name in facts["refused"]]
    if facts["named_out"]:
        notes.append(w["target_names_refused"].format(count=boundary._n(facts["named_out"], "target query")))
    return json.dumps({
        "ok": True, "conversion": facts["present"]["conversion"], "targetFiles": facts["target_files"],
        "targets": targets, "notes": notes, "summary": files["summary.md"], "files": sorted(files),
        "requests": total, "checks": _checks.to_csv() if _checks is not None else "", "profile": _profile or "",
    })


def checks_paste(text):
    """Reads results pasted from a results grid or a CSV file, adds them to the check results, and works out the checklists again.

    The pasted rows are read by Checks.from_pasted under the same rules as a check results file, and a
    later result for a check replaces the earlier one. The merged results take the place of checks.csv in
    the state, and the boundary runs again over the same state and requests, so that the page and the
    boundary command agree. Returns JSON: {"ok": false} when no row could be read; otherwise the counts
    of rows read and kept under "pasted", and under "boundary" what boundary_run returns, or null when no
    row was kept and nothing changed.
    """
    global _pasted
    from .checks import Checks
    catalogue, rules = _reading()
    try:
        if catalogue is None:
            raise ChecksError
        pasted = Checks.from_pasted(str(text), catalogue, rules)
    except ChecksError:
        return json.dumps({"ok": False})
    counts = {"read": pasted.read, "accepted": pasted.accepted}
    if not pasted.accepted:
        return json.dumps({"ok": True, "pasted": counts, "boundary": None})
    _pasted = pasted if _pasted is None else _pasted.merged(pasted)
    result = json.loads(boundary_run(None, _requests_commit))
    return json.dumps({"ok": True, "pasted": counts, "boundary": result})


# The sandbox. DuckDB is imported only here, so that the analysis page does not need it.

_sandbox = None
_outcomes = {}


def sandbox_start(catalogue, inventory):
    """Returns "ok", "ok-no-headers", "catalogue" or "inventory"."""
    global _sandbox
    from .catalogue import Catalogue
    from .sandbox import InventoryError, Sandbox
    _sandbox = None
    try:
        entries = Catalogue.from_csv(decode(_bytes(catalogue)))
    except CatalogueError:
        return "catalogue"
    try:
        _sandbox = Sandbox(entries, _bytes(inventory))
    except InventoryError:
        return "inventory"
    return "ok-no-headers" if entries.assumed_headers else "ok"


def sandbox_from_analysis():
    """Makes the sandbox from the analysis just finished, so that no file has to be chosen again."""
    global _sandbox
    from .sandbox import Sandbox
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in _analysis.pack().items():
            archive.writestr(name, text)
    _sandbox = Sandbox(_analysis.catalogue, out.getvalue())


def sandbox_build(rows):
    return json.dumps(_sandbox.build(rows))


def sandbox_run(sql):
    return json.dumps(_sandbox.run(sql))


def sandbox_requests_begin():
    _outcomes.clear()


def sandbox_request(name, data):
    _outcomes[name] = _sandbox.outcome(decode(_bytes(data)))


def sandbox_requests_finish():
    from . import vocabulary as v
    names = sorted(_outcomes)
    counts = [sum(1 for n in names if _outcomes[n] == kind) for kind in (v.OUTCOME_ROWS, v.OUTCOME_NO_ROWS, v.OUTCOME_NOT_RUN)]
    return json.dumps({
        "sentence": v.requests_sentence(len(names), *counts),
        "outcomes": [[number, _outcomes[name]] for number, name in enumerate(names, start=1)],
        "index": [[number, name] for number, name in enumerate(names, start=1)],
    })


def profile_paste(text):
    """Reads core profile results pasted from a results grid or a CSV file, adds them to the core profile, and works out the checklists again.

    Each pasted row is read by profile.read on its own, with the state's conversion, and a row that it
    refuses is left out. The kept rows are merged into the state's core profile by profile.merged, which
    reads the whole profile again, and the boundary runs again. Returns JSON as checks_paste does.
    """
    global _pasted_profile
    from . import boundary
    from . import profile as core_profile
    conversion = f"{BOUNDARY_ROOT}/state/{boundary.CONVERSION}"
    try:
        if not os.path.isdir(conversion):
            raise core_profile.ProfileError("no conversion")
        rows = core_profile.pasted_rows(str(text))
        if not rows:
            raise core_profile.ProfileError("no rows")
        kept = core_profile.accepted_rows(rows, conversion)
    except (core_profile.ProfileError, ValueError, KeyError, IndexError, OSError):
        return json.dumps({"ok": False})
    counts = {"read": len(rows), "accepted": len(kept)}
    if not kept:
        return json.dumps({"ok": True, "pasted": counts, "boundary": None})
    _pasted_profile = (_pasted_profile or []) + kept
    result = json.loads(boundary_run(None, _requests_commit))
    return json.dumps({"ok": True, "pasted": counts, "boundary": result})


# The first ask, for a project that starts without a catalogue. The names that it lists come from the
# requests, so the query exists only here and on the page, and nothing here writes it anywhere.

_first_names = None


def first_ask_begin():
    global _first_names
    from .first_ask import Names
    _first_names = Names()


def first_ask_add(data):
    """Adds the table names that one request, or one step of the conversion, reads."""
    _first_names.add(decode(_bytes(data)))


def first_ask_query():
    """The first query, as JSON: {"sql", "names", "leftOut"}. "sql" is empty when no plain name was found."""
    from . import first_ask
    names, left_out = _first_names.chosen()
    return json.dumps({"sql": first_ask.query(names), "names": len(names), "leftOut": left_out})


def first_ask_read(text, rules=None, checks=None):
    """Reads the pasted result of the first query as a catalogue and as check results.

    Returns JSON: {"ok": false} when the text is not the result; otherwise the catalogue and the check
    results as the text of their files, and counts of what was read.
    """
    from . import first_ask
    from .rules import SiteRules
    try:
        rules_text = decode(_bytes(rules)) if rules is not None else None
        site_rules = SiteRules.from_json(rules_text)
        catalogue, checks_text, facts = first_ask.read(str(text), site_rules,
                                                       decode(_bytes(checks)) if checks is not None else None)
    except (first_ask.FirstAskError, ValueError, TypeError, AttributeError):
        return json.dumps({"ok": False})
    # The tables that the first query asked about and that did not come back, which are not visible to this login. Their
    # names come from the requests and the conversion, so they are shown only on the page. Where most of them, or every
    # lookup table that the site rules name, did not come back, "doubt" says so, because the SQL window may be connected
    # to the wrong database or schema, or with a login whose rights are narrow.
    missing, doubt = [], ""
    if _first_names is not None:
        from .catalogue import Catalogue
        held = {t.name.upper() for t in Catalogue.from_csv(catalogue).tables()}
        asked = _first_names.chosen()[0]
        missing = sorted(n for n in asked if n.upper() not in held)
        doubt = first_ask.doubt(asked, held, site_rules)
    return json.dumps({"ok": True, "catalogue": catalogue, "checks": checks_text, "facts": facts, "missing": missing,
                       "doubt": doubt})


def state_zip():
    """The state that a second project starts from, as a zip: the catalogue, the check results, the core profile,
    the facts and the site's mapping rows, and what the team's SQL showed."""
    from . import boundary
    out = io.BytesIO()
    files = {}
    path = f"{BOUNDARY_ROOT}/state/{boundary.CATALOGUE}"
    if os.path.isfile(path):
        with open(path, "rb") as f:
            files[boundary.CATALOGUE] = decode(f.read())
    if _checks is not None:
        files[boundary.CHECKS] = _checks.to_csv()
    if _profile:
        files[boundary.PROFILE] = _profile
    # What the team's SQL showed, as the last run gathered it, so that a later run can count it without the request files.
    if _boundary is not None and boundary.EVIDENCE in _boundary["files"]:
        files[boundary.EVIDENCE] = _boundary["files"][boundary.EVIDENCE]
    for name in (boundary.FACTS, boundary.SETTINGS, f"{boundary.CONVERSION}/site_mappings.csv"):
        path = f"{BOUNDARY_ROOT}/state/{name}"
        if os.path.isfile(path):
            with open(path, "rb") as f:
                files[name] = decode(f.read())
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return out.getvalue()


# Facts that a person confirmed on this page.

def _write_facts(catalogue):
    """Adds the facts confirmed on this page to the state's facts.json, and writes the codes as the site's mapping rows."""
    from . import boundary
    from . import facts as facts_module
    if not _facts or catalogue is None:
        return
    path = f"{BOUNDARY_ROOT}/state/{boundary.FACTS}"
    held = facts_module.Facts()
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                held = facts_module.Facts.from_json(decode(f.read()), catalogue)
        except facts_module.FactsError:
            return      # the boundary refuses the state's file in its own words
    for fact in _facts:
        held = held.with_fact(fact)
    with open(path, "w", encoding="utf-8") as f:
        f.write(held.to_json())
    conversion = f"{BOUNDARY_ROOT}/state/{boundary.CONVERSION}"
    if os.path.isdir(conversion):
        with open(f"{conversion}/{facts_module.SITE_MAPPINGS}", "w", encoding="utf-8", newline="") as f:
            f.write(held.site_mappings())


def facts_add(text):
    """Adds several facts at once, given as a JSON list, and works out the checklists once. Returns JSON as fact_add does."""
    global _facts
    from . import facts as facts_module
    catalogue, _ = _reading()
    try:
        if catalogue is None:
            raise facts_module.FactsError("no catalogue")
        given = json.loads(str(text))
        if not isinstance(given, list) or not given or len(given) > 50:
            raise facts_module.FactsError("a list of facts")
        checked = [facts_module.check(fact, catalogue) for fact in given]
    except (facts_module.FactsError, ValueError, TypeError):
        return json.dumps({"ok": False})
    _facts = (_facts or []) + checked
    return json.dumps({"ok": True, "boundary": json.loads(boundary_run(None, _requests_commit))})


def codes_search_sql(text):
    """The name search again, with the words that the two people chose on the page: {"ok", "sql"}."""
    from . import target as target_module
    catalogue, rules = _reading()
    try:
        given = json.loads(str(text))
        table, code, label = given["definition"]
        definition = target_module._definition(rules, catalogue, given["table"], given["column"])
        if catalogue is None or definition is None or definition[:3] != (table, code, label):
            raise ValueError
        words = [w for w in given["words"] if isinstance(w, str) and target_module.PLAIN_WORD.fullmatch(w.strip())]
        if not words:
            raise ValueError
        return json.dumps({"ok": True, "sql": target_module.code_search(catalogue, definition, [w.strip() for w in words])})
    except (ValueError, KeyError, TypeError):
        return json.dumps({"ok": False})


def codes_search_read(text):
    """Reads the pasted result of a name search: {"ok", "columns", "candidates": [{"code", "values"}]}.

    The first column is the code and the rest are its names and kinds, which are the hospital's own build. They are
    returned to the page to show, and written into no file.
    """
    from .checks import MAXIMUM_TEXT_LENGTH, _acceptable
    lines = [l for l in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]
    rows = [[c.strip() for c in (l.split("\t") if "\t" in l else l.split(","))] for l in lines]
    rows = [r for r in rows if not all(set(c) <= {"-"} for c in r) and not (len(r) == 1 and "rows affected" in r[0])]
    columns = rows[0][1:] if rows and rows[0][0].lower() == "code" else []
    found, seen = [], set()
    for row in rows[1 if columns else 0:]:
        code, values = row[0], [v if v != "NULL" else "" for v in row[1:]]
        if not _acceptable(code, MAXIMUM_TEXT_LENGTH) or "," in code or code in seen or any(len(v) > 200 for v in values):
            continue
        seen.add(code)
        found.append({"code": code, "values": values})
    return json.dumps({"ok": bool(found), "columns": columns, "candidates": found})


def listed_read(text):
    """Reads the pasted list of what is charted on the cohort: {"ok", "columns", "rows": [{"code", "readings", "anaesthetics",
    "names"}]}. The names are the hospital's own; they are returned to the page to show and written into no file."""
    lines = [l for l in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]
    cells = [[c.strip() for c in (l.split("\t") if "\t" in l else l.split(","))] for l in lines]
    cells = [r for r in cells if not all(set(c) <= {"-"} for c in r) and not (len(r) == 1 and "rows affected" in r[0])]
    headed = bool(cells) and cells[0][0].lower() == "code"
    columns = cells[0][3:] if headed else []
    found, seen = [], set()
    for row in cells[1 if headed else 0:]:
        if len(row) < 3 or not row[0] or row[0] in seen or len(row[0]) > 50 or "," in row[0]:
            continue
        number = lambda v: int(v) if v.isdecimal() else None  # noqa: E731
        seen.add(row[0])
        found.append({"code": row[0], "readings": number(row[1]), "anaesthetics": number(row[2]),
                      "names": [v if v != "NULL" else "" for v in row[3:]][:6]})
    return json.dumps({"ok": bool(found) or headed, "columns": columns, "rows": found[:5000]})


def charted_read(text):
    """Reads the pasted result of the count of the chosen codes: {"ok", "rows": [[code, readings, anaesthetics]]}.

    A count left blank, as NULL or empty, is under ten and is read as None. The header row is passed over."""
    found = []
    for line in str(text or "").replace("\r", "").split("\n"):
        cells = [c.strip() for c in (line.split("\t") if "\t" in line else line.split(","))]
        if len(cells) != 3 or cells[0].lower() == "code" or not cells[0] or "," in cells[0] or len(cells[0]) > 50:
            continue
        if not all(c.isdecimal() or c in ("", "NULL") for c in cells[1:]):
            continue
        found.append([cells[0]] + [int(c) if c.isdecimal() else None for c in cells[1:]])
    headed = "readings" in str(text or "").lower()
    return json.dumps({"ok": bool(found) or headed, "rows": found[:200]})


def year_count_read(text):
    """Reads the pasted result of the count by year: {"ok", "years": [[year, anaesthetics, in the cohort, with no kind
    recorded]]}. A result of the earlier count, without the last column, gives each year three values."""
    found = []
    for line in str(text or "").replace("\r", "").split("\n"):
        cells = [c.strip() for c in (line.split("\t") if "\t" in line else line.split(","))]
        if len(cells) not in (3, 4) or not cells[0].isdecimal() or not 1900 <= int(cells[0]) <= 2200:
            continue
        values = [int(c) if c.isdecimal() else None for c in cells[1:]]
        found.append([int(cells[0])] + values)
    headed = "start_year" in str(text or "").lower()
    return json.dumps({"ok": bool(found) or headed, "years": sorted(found)[:200]})


def settings_set(text):
    """Writes the audit's settings, the study period and the kinds of anaesthetic, into the state, and works out the
    checklists again. Returns JSON: {"ok": false} when they cannot be read; otherwise what boundary_run returns."""
    from . import boundary
    from . import target as target_module
    try:
        settings = target_module.read_settings(str(text))
    except target_module.TargetError:
        return json.dumps({"ok": False})
    global _settings
    _settings = json.dumps({k: v for k, v in settings.items() if v}) + "\n"
    return json.dumps({"ok": True, "boundary": json.loads(boundary_run(None, _requests_commit))})


def fact_withdraw(text):
    """Withdraws the facts that a person gave about one item, so that the question is asked again.

    The text is JSON: one pattern, or a list of them, as the page's item gives under "withdraw". Each fact that a pattern
    names is removed for its subject from the facts confirmed on this page and from the state's facts.json, the site's
    mapping rows are written again, and the checklists are worked out again. Returns JSON as fact_add does.
    """
    global _facts
    from . import boundary
    from . import facts as facts_module
    catalogue, _ = _reading()
    try:
        if catalogue is None:
            raise facts_module.FactsError("no catalogue")
        given = json.loads(str(text))
        patterns = given if isinstance(given, list) else [given]
        if not patterns or len(patterns) > 50 or not all(isinstance(p, dict) and p.get("kind") in facts_module.KINDS for p in patterns):
            raise facts_module.FactsError("a list of facts to withdraw")
        path = f"{BOUNDARY_ROOT}/state/{boundary.FACTS}"
        held = facts_module.Facts()
        if os.path.isfile(path):
            with open(path, "rb") as f:
                held = facts_module.Facts.from_json(decode(f.read()), catalogue)
    except (facts_module.FactsError, ValueError, TypeError):
        return json.dumps({"ok": False})
    subjects = {facts_module._subject(f) for f in held.items + list(_facts or []) if any(_matches(f, p) for p in patterns)}
    for subject in subjects:
        held = held.without(subject)
    _facts = [f for f in _facts or [] if facts_module._subject(f) not in subjects]
    if subjects:
        with open(path, "w", encoding="utf-8") as f:
            f.write(held.to_json())
        conversion = f"{BOUNDARY_ROOT}/state/{boundary.CONVERSION}"
        if os.path.isdir(conversion):
            with open(f"{conversion}/{facts_module.SITE_MAPPINGS}", "w", encoding="utf-8", newline="") as f:
                f.write(held.site_mappings())
    return json.dumps({"ok": True, "boundary": json.loads(boundary_run(None, _requests_commit))})


def fact_add(text):
    """Adds one fact that a person confirmed, given as JSON, and works out the checklists again.

    Returns JSON: {"ok": false} when the fact does not have the expected form or names something that the
    catalogue does not hold; otherwise {"ok": true, "boundary": what boundary_run returns}.
    """
    global _facts
    from . import facts as facts_module
    catalogue, _ = _reading()
    try:
        if catalogue is None:
            raise facts_module.FactsError("no catalogue")
        fact = facts_module.check(json.loads(str(text)), catalogue)
    except (facts_module.FactsError, ValueError, TypeError):
        return json.dumps({"ok": False})
    _facts = (_facts or []) + [fact]
    return json.dumps({"ok": True, "boundary": json.loads(boundary_run(None, _requests_commit))})
