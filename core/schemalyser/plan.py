"""The review of an estimated plan of part 2, in SQL Server's SHOWPLAN XML, before part 2 is run on production.

The database analyst obtains the plan in Management Studio without running part 2 (README.md in the package gives the
steps) and saves it as a .sqlplan file, which is SHOWPLAN XML. This module reads it and says, for each large table:

    reject     the plan scans the table, rather than seeking it;
    reject     a join has no join predicate (SQL Server's own NoJoinPredicate warning, or a nested loop with neither a
               predicate nor outer references between two inputs of more than one row);
    reject     the largest estimated intermediate result, rows times executions, is above ROWS;
    escalate   the memory grant is above MEMORY_MB, or a spool or a sort lies over a large input;
    encourage  the plan seeks a large table from #cohort, which is encouraging but not conclusive.

The state is "rejected" where any reject applies, "escalate" where only an escalation does, and "accepted" otherwise.
An estimated plan is an estimate, made from the statistics that SQL Server held when it was obtained, and the plan that
runs may differ. The review also checks that the plan holds the statement of part 2 as the package writes it.
"""
import re
import xml.etree.ElementTree as ET

ROWS = 10_000_000
MEMORY_MB = 1024
LARGE_INPUT = 1_000_000
NS = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"
SCANS = {"Table Scan", "Clustered Index Scan", "Index Scan", "Columnstore Index Scan"}
SEEKS = {"Index Seek", "Clustered Index Seek"}


def _name(text):
    return (text or "").strip("[]").upper()


def _rows(op):
    estimate = float(op.get("EstimateRows") or 0)
    executions = 1 + float(op.get("EstimateRebinds") or 0) + float(op.get("EstimateRewinds") or 0)
    return estimate * executions


def _children(op):
    """The operators that feed this one."""
    out = []

    def walk(node):
        for child in node:
            if child.tag == NS + "RelOp":
                out.append(child)
            else:
                walk(child)
    walk(op)
    return out


def _own_objects(op):
    """The objects that this operator itself reads: those under it but not under any input operator."""
    found = []
    stack = [c for c in op if c.tag != NS + "RelOp"]
    while stack:
        node = stack.pop()
        if node.tag == NS + "RelOp":
            continue
        if node.tag == NS + "Object":
            found.append(node)
        stack.extend(list(node))
    return found


def _subtree_tables(op):
    return {_name(o.get("Table")) for o in op.iter(NS + "Object")}


def _flat(text):
    return " ".join((text or "").split()).rstrip(";").strip().upper()


def review(data, large_tables, part2=""):
    """The review of a plan, from its bytes. large_tables are the tables that the policy treats as large, and part2 is
    the text of part 2 as query.sql writes it. Returns a dictionary for plan-review.json."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {"state": "rejected", "reasons": ["the file is not a plan in SHOWPLAN XML"], "escalations": [],
                "encouraging": [], "largest_rows": None, "memory_grant_kb": None, "matches_sql": False,
                "statements": [], "estimate": True}
    large = {_name(t) for t in large_tables}
    statements = [s.get("StatementText") or "" for s in root.iter(NS + "StmtSimple")]
    want = _flat(part2)
    matches = bool(want) and any(want == _flat(s) or want in _flat(s) for s in statements)
    reasons, escalations, seeks, notes = [], [], set(), []
    largest = 0.0
    for op in root.iter(NS + "RelOp"):
        physical = op.get("PhysicalOp") or ""
        rows = _rows(op)
        largest = max(largest, rows)
        own = {_name(o.get("Table")) for o in _own_objects(op)}
        hit = sorted(t for t in own if t in large)
        if physical in SCANS and hit:
            reasons.append({"rule": "scan", "table": hit[0], "op": physical, "node": op.get("NodeId")})
        if physical in SEEKS and hit:
            seeks.update(hit)
        for warning in op.iter(NS + "Warnings"):
            if (warning.get("NoJoinPredicate") or "").lower() in ("true", "1"):
                reasons.append({"rule": "no_predicate", "op": physical, "node": op.get("NodeId")})
        if physical == "Nested Loops":
            loops = op.find(NS + "NestedLoops")
            inputs = _children(op)
            if loops is not None and loops.find(NS + "Predicate") is None and loops.find(NS + "OuterReferences") is None \
                    and len(inputs) == 2 and all(_rows(i) > 1 for i in inputs):
                reasons.append({"rule": "no_predicate", "op": physical, "node": op.get("NodeId")})
        if "Spool" in physical or physical in ("Sort", "Top N Sort"):
            inputs = _children(op)
            feeding = max((_rows(i) for i in inputs), default=rows)
            if feeding >= LARGE_INPUT or any(_subtree_tables(i) & large for i in inputs):
                escalations.append({"rule": "spool", "op": physical, "rows": round(feeding), "node": op.get("NodeId")})
    if largest > ROWS:
        reasons.append({"rule": "rows", "rows": round(largest)})
    grant = None
    for info in root.iter(NS + "MemoryGrantInfo"):
        value = float(info.get("SerialDesiredMemory") or info.get("SerialRequiredMemory") or 0)
        grant = max(grant or 0, value)
    for qp in root.iter(NS + "QueryPlan"):
        if qp.get("MemoryGrant"):
            grant = max(grant or 0, float(qp.get("MemoryGrant")))
    if grant is not None and grant / 1024 > MEMORY_MB:
        escalations.append({"rule": "memory", "kb": round(grant)})
    # The seeks that #cohort feeds: a seek of a large table inside a nested loop whose outer input reads #cohort.
    from_cohort = set()
    for op in root.iter(NS + "RelOp"):
        if op.get("PhysicalOp") != "Nested Loops":
            continue
        inputs = _children(op)
        if len(inputs) != 2:
            continue
        outer, inner = inputs
        if any(t.startswith("#COHORT") for t in _subtree_tables(outer)):
            for node in inner.iter(NS + "RelOp"):
                if node.get("PhysicalOp") in SEEKS:
                    from_cohort |= {_name(o.get("Table")) for o in _own_objects(node)} & large
    if not matches:
        reasons.append({"rule": "mismatch"})
    state = "rejected" if reasons else ("escalate" if escalations else "accepted")
    return {"state": state, "estimate": True, "reasons": reasons, "escalations": escalations,
            "encouraging": sorted(from_cohort), "seeks_of_large_tables": sorted(seeks),
            "largest_rows": round(largest), "memory_grant_kb": None if grant is None else round(grant),
            "matches_sql": matches, "statements": len(statements),
            "thresholds": {"rows": ROWS, "memory_mb": MEMORY_MB, "large_input_rows": LARGE_INPUT}}


def _reason(item):
    from .audit import PLAN_WORDING as w
    if item["rule"] == "scan":
        return w["scan"].format(table=item["table"])
    if item["rule"] == "no_predicate":
        return w["no_predicate"].format(op=item["op"])
    if item["rule"] == "rows":
        return w["rows"].format(rows=f"{item['rows']:,}", threshold=f"{ROWS:,}")
    if item["rule"] == "memory":
        return w["memory"].format(mb=f"{item['kb'] // 1024:,}", limit=f"{MEMORY_MB:,}")
    if item["rule"] == "spool":
        return w["spool"].format(op=item["op"].lower(), rows=f"{item['rows']:,}")
    return ""


def paragraph(found):
    """The paragraph of README.md that states the review."""
    from .audit import PLAN_WORDING as w
    from .describe import _and, _day
    date = _day(found.get("date") or "")
    parts = []
    if any(r["rule"] == "mismatch" for r in found["reasons"]):
        parts.append(w["mismatch"])
    other = [_reason(r) for r in found["reasons"] if r["rule"] != "mismatch"]
    if found["state"] == "rejected" and other:
        parts.append(w["rejected"].format(date=date, reasons="; ".join(dict.fromkeys(other))))
    elif found["state"] == "escalate":
        parts.append(w["escalate"].format(date=date, reasons="; ".join(dict.fromkeys(_reason(r) for r in found["escalations"]))))
    elif found["state"] == "accepted":
        parts.append(w["accepted"].format(date=date))
    parts.append(w["seeks"].format(tables=_and(found["encouraging"])) if found["encouraging"] else w["no_seeks"])
    if found.get("largest_rows") is not None:
        parts.append(w["largest"].format(rows=f"{found['largest_rows']:,}"))
    parts.append(w["grant"].format(mb=f"{max(1, found['memory_grant_kb'] // 1024):,}") if found.get("memory_grant_kb")
                 else w["no_grant"])
    parts.append(w["estimate"])
    return " ".join(parts)
