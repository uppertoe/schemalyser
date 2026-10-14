"""The examination of the examination: fixed mutations of a correct question, each run through the stages, with a
report of which stage killed each and which survived.

A mutation is a small change a model's question is likely to get wrong: the test-patient filter dropped; a join by
key turned into a join by time; a boundary moved by one, each >= made >, each <= made <, each < made <= and each
> made >=, where a number is compared; the accepted flag dropped; a window's tie-break dropped; a LEFT JOIN made an
inner join; a DISTINCT dropped; a CAST to float dropped before a division. A surviving mutant is either an equivalent
mutation, which a person marks by its name, or a case nobody has written yet.
"""
import sqlglot
from sqlglot import exp

from . import metamorphic, oracle, planted, shadow
from .. import rolepolicy

STAGE = "mutants"
STAGES = ("static", "shadow", "metamorphic", "oracle", "planted")
FLOAT_TYPES = {"FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL"}


def _tree(sql):
    return sqlglot.parse_one(sql, dialect="tsql")


def _text(tree):
    return tree.sql(dialect="tsql", pretty=True)


def _each(tree, kind, condition, change):
    """One mutant for each node of a kind that meets the condition, with the change applied to that node alone."""
    found = []
    nodes = [n for n in tree.find_all(kind) if condition(n)]
    for index in range(len(nodes)):
        copy = tree.copy()
        target = [n for n in copy.find_all(kind) if condition(n)][index]
        change(target)
        found.append(_text(copy))
    return found


def _is_number(node):
    return isinstance(node, exp.Literal) and not node.is_string


def mutations(sql):
    """The mutants of a question's SQL, as [(name, sql)], in a fixed order; a mutation that does not apply yields none."""
    tree = _tree(sql)
    found = []

    def drop_predicate(node):
        node.replace(exp.true())

    found += [("drop_test_filter", m) for m in _each(tree, exp.EQ, lambda n: isinstance(n.this, exp.Column) and n.this.name.lower() == "is_test", drop_predicate)]
    found += [("drop_accepted_flag", m) for m in _each(tree, exp.EQ, lambda n: isinstance(n.this, exp.Column) and n.this.name.lower() == "accepted", drop_predicate)]

    def by_time(node):
        left, right = node.this, node.expression
        other = right.table if isinstance(right, exp.Column) else None
        node.replace(exp.GTE(this=exp.column("reading_time", table=left.table), expression=exp.column("start_time", table=other)))

    def key_join(node):
        return (isinstance(node.this, exp.Column) and isinstance(node.expression, exp.Column)
                and node.this.name.lower() == "anaesthetic_key" and node.expression.name.lower() == "anaesthetic_key"
                and isinstance(node.parent, exp.Join))
    found += [("key_join_to_time", m) for m in _each(tree, exp.EQ, key_join, by_time)]
    for name, kind, replacement in (("gte_to_gt", exp.GTE, exp.GT), ("lte_to_lt", exp.LTE, exp.LT), ("lt_to_lte", exp.LT, exp.LTE), ("gt_to_gte", exp.GT, exp.GTE)):
        found += [(name, m) for m in _each(tree, kind, lambda n: _is_number(n.expression) or _is_number(n.this),
                                           lambda n, r=replacement: n.replace(r(this=n.this.copy(), expression=n.expression.copy())))]

    def drop_tie_break(window):
        order = window.args.get("order")
        order.set("expressions", order.expressions[:-1])
    found += [("drop_tie_break", m) for m in _each(tree, exp.Window, lambda w: isinstance(w.this, exp.RowNumber) and w.args.get("order") is not None
                                                     and len(w.args["order"].expressions) > 1, drop_tie_break)]
    found += [("left_to_inner_join", m) for m in _each(tree, exp.Join, lambda j: (j.side or "").upper() == "LEFT", lambda j: j.set("side", None))]
    found += [("drop_distinct", m) for m in _each(tree, exp.Select, lambda s: s.args.get("distinct") is not None, lambda s: s.set("distinct", None))]

    def float_cast(node):
        return isinstance(node, exp.Cast) and node.to.this.name in FLOAT_TYPES and isinstance(node.parent, exp.Div)
    found += [("drop_float_cast", m) for m in _each(tree, exp.Cast, float_cast, lambda c: c.replace(c.this.copy()))]
    return found


def examine(sql, examinable=None, cases=(), capabilities=(), seed=1, sample=3, case_kind=None):
    """The stages run over one SQL, stopping at the first that fails. Returns (the stage that killed it, or None, the
    sentence)."""
    found = rolepolicy.check(sql, capabilities)
    if found["failed"]:
        return "static", f"the role policy refuses it ({', '.join(found['failed'])})"
    report = shadow.run_sql(sql, seeds=(seed,))
    if report["outcome"] != "passed":
        return "shadow", report["says"]
    report = metamorphic.run_sql(sql, examinable or {}, seed=seed, sample=sample)
    if report["outcome"] != "passed":
        return "metamorphic", report["says"]
    if examinable and examinable.get("oracle") != "none":
        report = oracle.differential(sql, examinable, seeds=(seed,))
        if report["outcome"] == "failed":
            return "oracle", report["says"]
    for case in cases:
        report = planted.run_case(sql, case, seed=seed)
        if report["outcome"] != "passed":
            return "planted", report["says"]
    return None, "every stage passed"


def run(sql, examinable=None, cases=(), capabilities=(), equivalent=None, seed=1, sample=3):
    """The mutation report of a correct question: for each mutant its name, the stage that killed it or that it
    survived, and whether a person marked it equivalent; the counts by stage; and a sentence."""
    equivalent = equivalent or {}
    killed_by, _ = examine(sql, examinable, cases, capabilities, seed, sample)
    if killed_by is not None:
        return {"stage": STAGE, "outcome": "not applied", "mutants": [], "killed": {}, "survivors": [],
                "says": f"The question itself does not pass the stage {killed_by}, so its mutants say nothing."}
    results, killed = [], {stage: 0 for stage in STAGES}
    for n, (name, text) in enumerate(mutations(sql), 1):
        label = f"{name}#{n}"
        stage, says = examine(text, examinable, cases, capabilities, seed, sample)
        if stage is not None:
            killed[stage] += 1
        results.append({"name": label, "mutation": name, "killed_by": stage, "equivalent": equivalent.get(label) or equivalent.get(name), "says": says})
    survivors = [r["name"] for r in results if r["killed_by"] is None and not r["equivalent"]]
    outcome = "passed" if not survivors else "failed"
    says = (f"Every one of {len(results)} mutants that is not marked equivalent was killed, by stage: "
            + ", ".join(f"{stage} {count}" for stage, count in killed.items() if count) + "." if not survivors else
            f"{len(survivors)} of {len(results)} mutants survived every stage and are not marked equivalent: {', '.join(survivors)}; "
            "each is an equivalent mutation a person marks or a case nobody has written yet.")
    return {"stage": STAGE, "outcome": outcome, "mutants": results, "killed": killed, "survivors": survivors, "says": says}
