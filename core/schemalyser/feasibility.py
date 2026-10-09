"""Whether a question can be answered: what a query over the role views needs, and how far the hospital schema supplies it.

A question is one T-SQL SELECT over the role views, as the neonatal audit in rolemodel/ is. This module reads it with
sqlglot and states its requirements: the parts (role views) and columns it reads, the links between parts on which it
joins, the conditions it tests, the kinds of a vocabulary that it names in literals (as kind IN ('map_arterial',
'map_cuff')), the time arithmetic it does, and the columns behind its result. A column, a part, a kind or a link that
the role model does not describe is a requirement the model cannot meet, and is reported as such.

It then reads a saved hospital schema, the one file that screen 1 saves, and records for each requirement what that
file says, and nothing it does not say. The dimensions are kept apart in the JSON: whether a column is mapped, whether
a person confirmed it and on what date, whether the tables and columns query found it, whether its codes are
translated, and the readiness of its part (runs, checked against the database with the measured coverage, and
clinically validated, which the file never records); for a link, the confirmation, the test on made-up rows and any
test query run on the database with what it showed; for a kind, the codes chosen. The report sums each requirement up
in one of five states, and for each gap writes the smallest investigation that screen 1 already offers, as a request
that a named person can act on, with the query text where the saved schema can write it.

A missing mapping says only that the hospital schema does not yet say where the record is held. It is never evidence
that the hospital's database lacks it, and a route that has been checked is not evidence that it reaches every record.

    python -m schemalyser.feasibility report SCHEMA.zip QUERY.sql [--out report.md|report.json]
    python -m schemalyser.feasibility programme SCHEMA.zip FOLDER/ [--out programme.md|programme.json]

The report names the hospital's tables where it quotes a proposal or a query, so it stays on the hospital's own storage
with the hospital schema.
"""
import argparse
import datetime as dt
import json
import re
import sys
import zipfile
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from . import describe, evidence, normalise, rolemap

# The summary states, in order. The first is a requirement that the role model itself cannot meet.
NOT_DESCRIBED = "not described by the role model"
NOT_MAPPED = "not currently mapped"
PROPOSED = "proposed, not confirmed"
CONFIRMED = "confirmed, not yet checked against the database"
CHECKED = "checked against the database"
VALIDATED = "clinically validated"
STATES = (NOT_DESCRIBED, NOT_MAPPED, PROPOSED, CONFIRMED, CHECKED, VALIDATED)
RANK = {state: n for n, state in enumerate(STATES)}

VERDICTS = {
    "answerable": "This question can be answered from the hospital schema as it stands.",
    "not_yet": "This question is expressible but cannot yet be answered reliably.",
    "model": "This question needs parts the role model does not yet describe.",
}
CLINICIAN, ANALYST = "clinician", "database analyst"
STEPS = {4: "4. Propose where each part is held", 5: "5. Check which tables exist", 6: "6. Confirm each column",
         7: "7. Choose the hospital's codes", 8: "8. Run the counts", 9: "9. Save the hospital schema"}
# The units of time arithmetic that measure an elapsed time within a day, which a change of daylight saving can upset.
CLOCK_UNITS = {"second", "minute", "hour", "millisecond", "microsecond"}
TIME_NODES = tuple(getattr(exp, name) for name in ("DateDiff", "DateAdd", "DateSub", "DatetimeAdd", "DatetimeDiff",
                                                   "DatetimeSub", "TimestampAdd", "TimestampDiff", "TsOrDsAdd",
                                                   "TsOrDsDiff", "TimeAdd", "TimeDiff") if hasattr(exp, name))

# The wording that a person reads, in one place.
WORDING = {
    "heading_requirements": "What the question needs",
    "heading_tests": "What the question tests",
    "heading_missing": "What is missing",
    "heading_requests": "Evidence requests",
    "heading_principle": "What this report can and cannot say",
    "asks": "The question reads as follows: {question}",
    "read_on": "Schemalyser read this question against the hospital schema last updated on {date}.",
    "names": "The evidence requests below name the tables of the hospital's database, so this report stays on the hospital's own storage with the hospital schema.",
    "table_head": "| What the question needs | State | What the hospital schema records |",
    "nothing_missing": "Every requirement of this question has been checked against the database.",
    "no_requests": "No evidence request is needed before the question is run.",
    "principle": "Where this report says that something is not currently mapped, the hospital schema does not yet say where the hospital's database keeps it; that is not evidence that the database lacks it. Where a part has been checked against the database, the route through which the hospital schema reaches it runs and its counts looked right; that is not evidence that the route captures every record. Only a reconciliation of a sample of anaesthetics against the clinical record can show that, and the hospital schema never records it.",
    # The plain names of requirements.
    "link": "The link from {source} to {target}",
    "kind": "The hospital's codes for {meaning}",
    "gap_part": "A part named {name}",
    "gap_column": "A column named {column} in {part}",
    "gap_kind": "A kind named {kind} in {about}",
    "gap_link": "A link between {source} and {target}",
    "gap_table": "The table {name}",
    # What the hospital schema records, as sentences.
    "part_absent": "The hospital schema has no place for this part yet.",
    "unmapped": "The hospital schema does not yet say which column holds this.",
    "unanswered": "The page proposed a column, and no person has answered for it yet.",
    "not_sure": "The page proposed a column, and a person marked it Not sure on {date}.",
    "part_unanswered": "The page proposed a table for this part, and no person has answered for it yet.",
    "confirmed": "A person confirmed it on {date}.",
    "confirmed_count": "A count confirmed it.",
    "present": "The tables and columns query found it.",
    "absent": "The tables and columns query did not find it.",
    "presence_unknown": "The tables and columns query has not been read.",
    "untranslated": "Its codes are not yet translated.",
    "translated": "Its codes are translated.",
    "runs": "The part runs on made-up rows.",
    "fails": "The part does not yet pass the test on made-up rows.",
    "part_checked": "The part was checked against the database on {date}.",
    "part_unchecked": "The part has not been checked against the database.",
    "validated": "The part was clinically validated on {date}.",
    "measured": "{says}",
    "test_passed": "Its change passed the test on made-up rows on {date}.",
    "test_failed": "Its change was kept on {date} although it failed the test on made-up rows.",
    "probe": "A test query run on the {database} database on {date} found: {finding}",
    "probe_unreal": "A test query was run on {where}, which checks nothing about the real record.",
    "no_probe": "No test query has been run on the database for this link.",
    "codes": "A person chose {count} for it on {date}.",
    "codes_proposed": "The page proposed {count} for it, and no person has chosen them.",
    "codes_none": "No code has been chosen for it.",
    "not_described": "The role model does not describe this.",
    # What is missing, by state.
    "missing_one": "One requirement is {state}: {items}.",
    "missing_many": "{count} requirements are {state}: {items}.",
    "concerns": "This request concerns {items}.",
    "time_arithmetic": "the time arithmetic",
    "derived": "a time that the question derives",
    # The evidence requests.
    "for": "For the {role}.",
    "req_model": "The role model does not describe {title}. The clinician raises this with the project, so that the role model can describe it, or rewrites the question to use a part or column that the role model already holds.",
    "req_answer": "The database analyst answers for {title} at step {step}, with Yes, this is right, Choose another column or Not sure.",
    "req_answer_asked": "The database analyst answers for {title} at step {step}, with Yes, this is right, Choose another column or Not sure. If the database analyst is not sure, the clinician sends the database team this question:",
    "req_answer_part": "The database analyst answers for the table of {title} at step {step}, with Yes, this is right or Choose another table.",
    "req_question": "The clinician sends the database team this question, using Copy the questions at step {step}, and the database analyst records the team's answer there:",
    "req_nothing": "The clinician sends the database team this question, and the database analyst records the answer at step {step} with Choose a column:",
    "req_absent": "The tables and columns query did not find the column that the hospital schema names for {title}, so the database analyst chooses another column at step {step}.",
    "req_values": "The database analyst runs this query of values in the SQL window connected to the hospital's database, then at step {step} says which of its values mean yes for {title} and chooses Check this change.",
    "req_values_unwritten": "The database analyst chooses Write the query of values for {title} at step {step}, runs it, and says which of its values mean yes.",
    "req_list": "The database analyst runs this list of what is charted in the SQL window connected to the hospital's database and pastes it at step {step} with Read the list. The clinician then chooses the codes of {kinds} and chooses Save these codes.",
    "req_list_unwritten": "At step {step}, the database analyst chooses Write the list for {title}, runs it and chooses Read the list, and the clinician then chooses the codes of {kinds} and chooses Save these codes.",
    "req_probe": "The database analyst runs this test query in the SQL window connected to the hospital's database. It counts how many anaesthetics of {year} have at least one row through {title}, and how many have none. Its result enters the hospital schema through the evidence import, with python -m schemalyser.describe import-evidence.",
    "req_counts": "The database analyst runs {counts} on the production database, pastes each result at step {step} and chooses Read the result, and the clinician then chooses Save whether these look right. This checks {parts} against the database.",
    "req_counts_unwritten": "At step {step}, the clinician chooses Write the counts, the database analyst runs {counts} on the production database, and the clinician then judges whether each looks right. This checks {parts} against the database.",
    "req_draft": "No count at step 8 reads {part} yet, so the page cannot yet record it as checked against the database. The database analyst can run this query of values on {title} for evidence that the column holds what the question needs, and the clinician judges whether it looks right.",
    "req_draft_unwritten": "No count at step 8 reads {part} yet, so the page cannot yet record it as checked against the database. The clinician and the database analyst look at the values of {title} with Write the query of values at step 6 for evidence that it holds what the question needs.",
    "req_time_zone": "The question measures elapsed time in {units}, and the hospital schema does not yet record the time zone of the database's clocks. The clinician records it, and whether the clocks change with daylight saving, at step {step}.",
    "req_validate": "Every requirement has been checked against the database, and none is clinically validated. The clinician reconciles a sample of anaesthetics against the clinical record, including some from each group of the question's result, before the answer is relied on.",
    "count_names": {"coverage_by_year": "the coverage by year", "repeated_keys": "the count of repeated identifiers",
                    "readings_by_kind": "the readings by kind"},
    # The time arithmetic and the conditions.
    "time_diff": "The question counts {unit}s between {columns}.",
    "time_add": "The question adds {unit}s to {columns}.",
    "time_zone": "The hospital schema records that the database's clocks follow {zone}{saving}.",
    "time_zone_none": "The hospital schema does not yet record the time zone of the database's clocks.",
    "saving_yes": " and change with daylight saving, so an elapsed time across a change of daylight saving is out by an hour",
    "saving_no": " and do not change with daylight saving",
    "condition": "The question keeps only the rows for which `{text}` holds, which tests {columns}.",
    "outcome": "The question's result rests on {columns}.",
    "outcome_named": "The role model describes this outcome as follows: {meaning}",
    # The programme.
    "programme_heading": "The programme of questions",
    "programme_read": "Schemalyser read {count} against the hospital schema last updated on {date}.",
    "question_coverage": "{answered} of the {total} {questions} {have} every requirement checked against the database.",
    "structural_coverage": "The hospital schema gives a place for {parts} of the {all_parts} parts that the role model describes, and for {columns} of their {all_columns} columns.",
    "programme_blocking": "What holds back the most questions",
    "programme_blocking_head": "| Requirement | State | Questions that need it |",
    "programme_none": "Nothing holds back any question.",
    "programme_questions": "Each question",
    "programme_questions_head": "| Question | Verdict |",
}


class FeasibilityError(ValueError):
    """A question or a saved hospital schema cannot be read."""


# Reading the question.

def _leading_comments(sql):
    found = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            break
        found.append(stripped[2:].strip())
    return found


def question_text(sql):
    """The question in the query's leading comment, up to its first question mark or full stop, or ""."""
    text = " ".join(_leading_comments(sql)).strip()
    if not text:
        return ""
    found = re.match(r"(.+?[?.])(?:\s|$)", text)
    return (found.group(1) if found else text).strip()


def _heading(name):
    words = re.sub(r"[_\-]+", " ", Path(name).stem).strip()
    return words[:1].upper() + words[1:] if words else "The question"


def _contract():
    model = rolemap.contract()
    views = {view["name"]: view for view in model["views"]}
    columns = {(view["name"], column["name"]): column for view in model["views"] for column in view["columns"]}
    return model, views, columns


def _vocabulary(model, view, column):
    """The kinds that a column of a kind may hold, as {kind: meaning}."""
    if view == "role_reading" and column["name"] == "kind":
        return {k["kind"]: k["meaning"] for k in model["kinds"]}
    return {k["kind"]: k["meaning"] for k in model["vocabularies"].get(column.get("vocabulary") or "", [])}


class _Tracer:
    """Follows a column of any scope of a query back to the columns of the role views behind it."""

    def __init__(self, views, columns):
        self.views, self.columns = views, columns
        self.memo = {}
        self.gaps = {}

    def _source_of(self, scope, column):
        """The source that a column of a scope reads, as (alias, Table or Scope), or (None, None)."""
        selected = scope.selected_sources
        alias = column.table
        if alias:
            found = next((v for k, v in selected.items() if k.lower() == alias.lower()), None)
            return (alias, found[1]) if found else (None, None)
        if len(selected) == 1:
            name, (_, source) = next(iter(selected.items()))
            return name, source
        name = column.name.lower()
        holders = []
        for key, (_, source) in selected.items():
            if isinstance(source, exp.Table):
                if (source.name.lower(), name) in self.columns:
                    holders.append((key, source))
            elif name in self._outputs(source):
                holders.append((key, source))
        return holders[0] if len(holders) == 1 else (None, None)

    @staticmethod
    def _selects(scope):
        expression = scope.expression
        if isinstance(expression, exp.SetOperation):
            branches = getattr(scope, "set_operation_scopes", None) or getattr(scope, "union_scopes", None) or []
            return [s for branch in branches for s in _Tracer._selects(branch)]
        return [(scope, expression)] if isinstance(expression, exp.Select) else []

    def _outputs(self, scope):
        return {p.alias_or_name.lower() for _, select in self._selects(scope) for p in select.expressions}

    def base(self, scope, column, values=False):
        """The role columns behind one column of a scope, as a set of (view, column). With values, a column that only
        partitions or orders a window, and so gives no value of its own, is left out."""
        alias, source = self._source_of(scope, column)
        name = column.name.lower()
        if source is None:
            # A column named only by a projection's alias in the same SELECT, as in ORDER BY.
            select = scope.expression if isinstance(scope.expression, exp.Select) else None
            projection = next((p for p in (select.expressions if select else []) if p.alias and p.alias.lower() == name
                               and not isinstance(p.this, exp.Column)), None)
            return self.within(scope, projection.this, values) if projection is not None else set()
        if isinstance(source, exp.Table):
            view = source.name.lower()
            if view not in self.views:
                return set()
            if (view, name) not in self.columns:
                self.gaps[f"column:{view}.{name}"] = {"form": "column", "view": view, "column": name}
                return set()
            return {(view, name)}
        return self.output(source, name, values)

    def output(self, scope, name, values=False):
        key = (id(scope), name, values)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = set()
        found = set()
        for branch, select in self._selects(scope):
            for projection in select.expressions:
                if isinstance(projection, exp.Star):
                    continue
                if projection.alias_or_name.lower() == name:
                    found |= self.within(branch, projection, values)
        self.memo[key] = found
        return found

    def within(self, scope, node, values=False):
        """The role columns behind every column in an expression of a scope."""
        found = set()
        if node is None:
            return found
        nodes = [node] if isinstance(node, exp.Column) else list(node.find_all(exp.Column))
        for column in nodes:
            if values and _frames_window(column, node):
                continue
            found |= self.base(scope, column, values)
        return found


def _frames_window(column, stop):
    """Whether a column only partitions or orders a window function, below stop."""
    child, parent = column, column.parent
    while parent is not None and child is not stop:
        if isinstance(parent, exp.Window) and child is not parent.this:
            return True
        child, parent = parent, parent.parent
    return False


def _conjuncts(node):
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    return [node] if node is not None else []


def _literals(node):
    return [lit.this for lit in node.find_all(exp.Literal) if lit.is_string]


def requirements(sql, name="question.sql"):
    """What a question over the role views needs, as a plain dictionary. Raises FeasibilityError when the text is not
    one SELECT. Each requirement carries an id: a part as role_x, a column as role_x.column, a link as
    "role_x.column -> role_y.column", a kind as "role_x.column = kind", and a gap of the role model as "gap:..."."""
    model, views, columns = _contract()
    try:
        trees = [t for t in sqlglot.parse(sql, read="tsql") if t is not None]
    except sqlglot.errors.SqlglotError:
        raise FeasibilityError(f"{name}: Schemalyser could not read this as SQL.") from None
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
        raise FeasibilityError(f"{name}: a question is one SELECT over the parts of the record.")
    tree = trees[0]
    tracer = _Tracer(views, columns)
    used, parts, links, kinds, conditions, times = set(), set(), {}, {}, [], []
    gaps = {}
    named_ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        title = table.name.lower()
        qualified = bool(table.db or table.catalog)
        if not qualified and title in named_ctes:
            continue
        if not qualified and title in views:
            parts.add(title)
        elif not qualified and title.startswith("role_"):
            gaps[f"gap:part:{title}"] = {"form": "part", "name": title}
        else:
            gaps[f"gap:table:{table.sql(dialect='tsql')}"] = {"form": "table", "name": table.sql(dialect="tsql")}
    scopes = list(traverse_scope(tree))
    for scope in scopes:
        for column in scope.columns:
            alias, source = tracer._source_of(scope, column)
            if isinstance(source, exp.Table) and source.name.lower() in views:
                tracer.base(scope, column)
                key = (source.name.lower(), column.name.lower())
                if key in columns:
                    used.add(key)
        select = scope.expression if isinstance(scope.expression, exp.Select) else None
        if select is None:
            continue
        tested = []
        if select.args.get("where") is not None:
            tested += _conjuncts(select.args["where"].this)
        for join in select.args.get("joins") or []:
            tested += _conjuncts(join.args.get("on"))
        if select.args.get("having") is not None:
            tested += _conjuncts(select.args["having"].this)
        for condition in tested:
            if isinstance(condition, exp.EQ) and isinstance(condition.left, exp.Column) and isinstance(condition.right, exp.Column):
                left, right = tracer.within(scope, condition.left), tracer.within(scope, condition.right)
                if len(left) == 1 and len(right) == 1 and next(iter(left))[0] != next(iter(right))[0]:
                    a, b = next(iter(left)), next(iter(right))
                    if columns[a]["type"] == "key" and columns[b]["type"] == "key":
                        link = _link(views, a, b)
                        if link:
                            links[link["id"]] = link
                        else:
                            gaps[f"gap:link:{a[0]}.{a[1]}={b[0]}.{b[1]}"] = {"form": "link", "a": a, "b": b}
                        continue
            # A condition is one of the question's own tests where it reads a part directly, rather than joining the
            # question's own steps to one another.
            direct = any(isinstance(tracer._source_of(scope, c)[1], exp.Table) for c in condition.find_all(exp.Column))
            behind = tracer.within(scope, condition, values=True)
            if behind and direct:
                conditions.append({"text": condition.sql(dialect="tsql"), "columns": sorted(f"{v}.{c}" for v, c in behind)})
        # Kinds named in literals, wherever a column of a kind is compared with them.
        for node in select.find_all(exp.EQ, exp.NEQ, exp.In):
            if node.find_ancestor(exp.Select) is not select:
                continue
            sides = [node.this, *node.expressions] if isinstance(node, exp.In) else [node.left, node.right]
            column = next((s for s in sides if isinstance(s, exp.Column)), None)
            if column is None:
                continue
            behind = tracer.within(scope, column)
            if len(behind) != 1:
                continue
            view, name_ = next(iter(behind))
            spec = columns[(view, name_)]
            if spec["type"] != "kind":
                continue
            known = _vocabulary(model, view, spec)
            for side in sides:
                if side is column:
                    continue
                for kind in _literals(side):
                    if kind in known:
                        key = f"{view}.{name_} = {kind}"
                        kinds[key] = {"id": key, "view": view, "column": name_, "kind": kind, "meaning": known[kind]}
                    else:
                        gaps[f"gap:kind:{view}.{name_}={kind}"] = {"form": "kind", "view": view, "column": name_, "kind": kind}
        for node in select.find_all(*TIME_NODES):
            if node.find_ancestor(exp.Select) is not select or node.find_ancestor(*TIME_NODES) is not None:
                continue
            # The columns in the order of the arguments: for DATEDIFF, the earlier time and then the later.
            behind = []
            for argument in (node.args.get("expression"), node.this):
                for found in sorted(tracer.within(scope, argument, values=True)):
                    if found not in behind:
                        behind.append(found)
            unit = node.args.get("unit")
            unit = (unit.name if isinstance(unit, exp.Expression) else str(unit or "day")).lower() or "day"
            form = "diff" if "Diff" in type(node).__name__ else "add"
            times.append({"form": form, "unit": unit, "text": node.sql(dialect="tsql"),
                          "columns": [f"{v}.{c}" for v, c in behind], "clock": unit in CLOCK_UNITS})
    for key, gap in tracer.gaps.items():
        gaps[f"gap:{key}"] = gap
    # The columns behind the result: every column of the final SELECT, followed back to the role views.
    root = scopes[-1]
    outcomes = []
    for _, select in _Tracer._selects(root):
        for projection in select.expressions:
            behind = tracer.within(root, projection, values=True)
            outcomes.append({"name": projection.alias_or_name, "columns": sorted(f"{v}.{c}" for v, c in behind)})
        break
    behind_result = {c for o in outcomes for c in o["columns"]}
    named_outcomes = []
    for outcome in model.get("outcomes", []):
        wanted = outcome.get("from") or []
        if wanted and all(("." in f and f in behind_result) for f in wanted):
            named_outcomes.append({"name": outcome["name"], "meaning": outcome["meaning"]})
    parts |= {v for v, _ in used}
    for link in links.values():
        parts |= {link["source"].split(".")[0], link["target"].split(".")[0]}
    unique_times, seen = [], set()
    for item in times:
        if item["text"] not in seen:
            seen.add(item["text"])
            unique_times.append(item)
    unique_conditions, seen = [], set()
    for item in conditions:
        if item["text"] not in seen:
            seen.add(item["text"])
            unique_conditions.append(item)
    order = list(views)
    return {"name": Path(name).name, "heading": _heading(name), "question": question_text(sql),
            "parts": sorted(parts, key=order.index),
            "columns": sorted((f"{v}.{c}" for v, c in used), key=lambda a: (order.index(a.split(".")[0]), a)),
            "links": sorted(links.values(), key=lambda l: l["id"]),
            "kinds": sorted(kinds.values(), key=lambda k: k["id"]),
            "conditions": unique_conditions, "time": unique_times, "outcomes": outcomes, "named_outcomes": named_outcomes,
            "gaps": [{"id": k, **v} for k, v in sorted(gaps.items())]}


def _link(views, a, b):
    """The link of the role model that joins two role columns, as {"id", "source", "target"}, or None."""
    for one, other in ((a, b), (b, a)):
        for link in views[one[0]].get("links", []):
            if link["column"] == one[1] and link["to"] == f"{other[0]}.{other[1]}":
                source = f"{one[0]}.{one[1]}"
                return {"id": f"{source} -> {link['to']}", "source": source, "target": link["to"]}
    return None


# Reading the saved hospital schema.

class Schema:
    """A saved hospital schema, read as the one file that the page saves or its folder, with a sitting of screen 1
    restored from it so that its own query writers can be used."""

    def __init__(self, files):
        self.files = files
        self.settings = describe._json_of(files.get("settings.json"))
        try:
            self.map = normalise.resolve(json.loads(describe._text(files["map/map.json"])))
        except (KeyError, ValueError):
            raise FeasibilityError("The file holds no hospital schema that Schemalyser can read.") from None
        self.judgements = describe._json_of(files.get("counts/judgements.json")).get("counts") or {}
        self.schema_id = self.settings.get("schema_id")
        sitting = describe.Describe()
        sitting.version = "feasibility"
        found = sitting.restore(files)
        if not found.get("map"):
            # Without the dictionary inside the file, the sitting holds the map and the codes alone, which is enough
            # for what is read here, and too little to write a query.
            sitting = describe.Describe()
            sitting.data = self.map
            for path, data in files.items():
                match = re.fullmatch(r"codes/(role_\w+\.\w+)\.json", path)
                if match:
                    sitting.codes[match.group(1)] = describe._json_of(data)
            held = describe._json_of(files.get("dimensions.json"))
            sitting.dimensions = {kind: held.get(kind) or {} for kind in ("bindings", "links", "translations")}
            self.writes = False
        else:
            self.writes = True
        self.sitting = sitting
        # The readiness of each part is derived from the dimensions that the file records, and is never read as stored.
        self.readiness = sitting.readiness() or {}
        self.year = int(self.settings.get("year") or dt.date.today().year - 1)

    @classmethod
    def load(cls, path):
        path = Path(path)
        files = {}
        if path.is_dir():
            for item in path.rglob("*"):
                if item.is_file():
                    files[item.relative_to(path).as_posix()] = item.read_bytes()
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        else:
            raise FeasibilityError(f"{path.name} is not a saved hospital schema.")
        return cls(files)

    def updated(self):
        return describe._day(self.settings.get("updated") or self.settings.get("made") or "")

    def part(self, view):
        """The readiness of a part as the file records it: {"runs", "checked", "validated", "measured"}."""
        held = (self.readiness.get("parts") or {}).get(view) or {}
        measured = self.readiness.get("measured") or {}
        figures = measured.get("figures") or {}
        mine = {}
        if view in ("role_patient", "role_anaesthetic") and "with_patient" in figures:
            mine["with_patient"] = figures["with_patient"]
        if view in ("role_anaesthetic", "role_reading") and "with_needed_kind" in figures:
            mine["with_needed_kind"] = figures["with_needed_kind"]
            mine["year"] = figures.get("year")
        return {"runs": held.get(describe.RUNS), "checked": held.get(describe.CHECKED),
                "validated": held.get(describe.VALIDATED), "measured": mine,
                "measured_says": measured.get("says") if mine else None}

    def item(self, about):
        view, _, column = about.partition(".")
        role = (self.map.get("roles") or {}).get(view)
        if role is None:
            return None
        return role["rows"] if not column else role["columns"].get(column)

    def probe(self, about):
        """The test query of a link as the journal records it, with its figures and what it found, or None."""
        entry = next((e for e in self.sitting.journal.values() if e.get("about") == about and e.get("probe")), None)
        if entry is None or not entry.get("result"):
            return None
        held = self.sitting.probes.get(about) or {}
        figures = {}
        if held.get("rows"):
            figures = dict(zip(held["columns"], [describe._number(v) for v in held["rows"][0]]))
        findings = self.sitting.probe_findings(about) if held else []
        real = entry.get("database") in describe.REAL_DATABASES and entry.get("from") != describe.INVENTED_HOSPITAL
        return {"database": entry.get("database"), "from": entry.get("from") or "pasted", "date": (entry.get("pasted") or "")[:10],
                "year": entry.get("year"), "figures": figures, "findings": findings, "real": real}

    def correction(self, about):
        found = [e for e in self.sitting.corrections if e.get("about") == about]
        if not found:
            return None
        last = found[-1]
        return {"passed": bool(last.get("passed")), "test": last.get("test"), "date": last.get("date"), "reason": last.get("reason")}


# The evidence for each requirement.

def _mapped(item):
    if item is None:
        return False
    if "binding" in item or "confidence" in item:
        return bool(item.get("binding"))
    return not str(item.get("from", "")).startswith("nothing")


def _source(item):
    binding = (item or {}).get("binding") or {}
    if binding.get("table") and binding.get("column"):
        return f"{binding['table']}.{binding['column']}"
    return binding.get("table")


def _confirmation(item):
    item = item or {}
    held = item.get("confirmation") or {}
    confirmed = item.get("status") in rolemap.CONFIRMED
    return {"status": item.get("status"), "answer": held.get("answer"), "confirmed": confirmed,
            "date": held.get("date") if confirmed else None,
            "by": ("a person" if item.get("status") == "person" else "a count") if confirmed else None,
            "asked": held.get("date") if held.get("answer") == "not sure" else None}


def _part_state(part):
    if part["validated"]:
        return VALIDATED
    if part["checked"]:
        return CHECKED
    return CONFIRMED


def column_evidence(schema, about, columns):
    view, _, name = about.partition(".")
    item = schema.item(about)
    spec = columns[(view, name)]
    part = schema.part(view)
    evidence = {"mapped": _mapped(item), "source": _source(item) if _mapped(item) else None,
                "part_held": item is not None, **{"confirmation": _confirmation(item)}, "present": None, "translation": None,
                "part": part, "status": rolemap.statuses().get(view)}
    if evidence["mapped"]:
        presence = schema.sitting.presence((item or {}).get("binding")) if schema.writes else None
        evidence["present"] = presence["state"] if presence else None
        coding = schema.sitting.coding(view, spec, item)
        if coding is not None:
            evidence["translation"] = {"form": coding["form"], "translated": bool(coding["translated"])}
    if not evidence["mapped"]:
        state = NOT_MAPPED
    elif not evidence["confirmation"]["confirmed"] or evidence["present"] == "missing" or \
            (evidence["translation"] and not evidence["translation"]["translated"]):
        state = PROPOSED
    elif not part["runs"]:
        state = PROPOSED
    else:
        state = _part_state(part)
    return {"id": about, "form": "column", "title": rolemap.plain_about(about, True), "state": state, "evidence": evidence}


def part_evidence(schema, view):
    item = schema.item(view)
    part = schema.part(view)
    evidence = {"mapped": _mapped(item), "confirmation": _confirmation(item), "part": part,
                "status": rolemap.statuses().get(view)}
    if not evidence["mapped"]:
        state = NOT_MAPPED
    elif not evidence["confirmation"]["confirmed"] or not part["runs"]:
        state = PROPOSED
    else:
        state = _part_state(part)
    return {"id": view, "form": "part", "title": rolemap.view_title(view), "state": state, "evidence": evidence}


def link_evidence(schema, link, columns):
    source, target = link["source"], link["target"]
    one, other = schema.item(source), schema.item(target)
    test = schema.correction(source)
    runs = schema.part(source.split(".")[0])["runs"]
    probe = schema.probe(source)
    evidence = {"mapped": _mapped(one) and _mapped(other), "confirmation": _confirmation(one),
                "target_confirmation": _confirmation(other),
                "test": {"correction": test, "part_runs": runs,
                         "passed": (test["passed"] if test else bool(runs))},
                "probe": probe, "part": schema.part(source.split(".")[0])}
    a, b = source.split(".")[0], target.split(".")[0]
    title = WORDING["link"].format(source=rolemap.view_title(a), target=rolemap.view_title(b))
    if not evidence["mapped"]:
        state = NOT_MAPPED
    elif not (evidence["confirmation"]["confirmed"] and evidence["target_confirmation"]["confirmed"]) or not evidence["test"]["passed"]:
        state = PROPOSED
    elif schema.part(a)["validated"] and schema.part(b)["validated"]:
        state = VALIDATED
    elif (probe and probe["real"] and probe["figures"]) or evidence["part"]["checked"]:
        state = CHECKED
    else:
        state = CONFIRMED
    return {"id": link["id"], "form": "link", "title": title, "state": state, "evidence": evidence,
            "source": source, "target": target}


def _meaning_phrase(meaning):
    text = meaning.rstrip(".")
    return text[:1].lower() + text[1:]


def kind_evidence(schema, kind):
    key = f"{kind['view']}.{kind['column']}"
    item = schema.item(key)
    held = schema.sitting.codes.get(key) or {}
    if kind["view"] == "role_reading":
        entry = (schema.map.get("kinds") or {}).get(kind["kind"]) or {}
        codes = list(entry.get("codes") or [])
        chosen = entry.get("status") in rolemap.CONFIRMED and bool(codes)
        date = (entry.get("confirmation") or {}).get("date") or held.get("date")
    else:
        codes = sorted(c for c, k in (held.get("chosen") or {}).items() if k == kind["kind"])
        chosen = bool(codes)
        date = held.get("date")
    part = schema.part(kind["view"])
    evidence = {"column_mapped": _mapped(item), "codes": len(codes), "chosen": chosen, "date": date if chosen else None,
                "by": "a person" if chosen else None, "part": part}
    if not evidence["column_mapped"] or not codes:
        state = NOT_MAPPED
    elif not chosen or not part["runs"]:
        state = PROPOSED
    else:
        state = _part_state(part)
    return {"id": kind["id"], "form": "kind", "title": WORDING["kind"].format(meaning=_meaning_phrase(kind["meaning"])),
            "state": state, "evidence": evidence, "key": key, "kind": kind["kind"], "meaning": _meaning_phrase(kind["meaning"])}


def gap_evidence(gap):
    form = gap["form"]
    if form == "part":
        title = WORDING["gap_part"].format(name=gap["name"])
    elif form == "column":
        title = WORDING["gap_column"].format(column=gap["column"], part=rolemap.view_title(gap["view"]))
    elif form == "kind":
        title = WORDING["gap_kind"].format(kind=gap["kind"], about=rolemap.plain_about(f"{gap['view']}.{gap['column']}"))
    elif form == "link":
        title = WORDING["gap_link"].format(source=rolemap.plain_about(".".join(gap["a"])), target=rolemap.plain_about(".".join(gap["b"])))
    else:
        title = WORDING["gap_table"].format(name=gap["name"])
    return {"id": gap["id"], "form": "gap", "title": title, "state": NOT_DESCRIBED, "evidence": {"gap": gap}}


# What the hospital schema records, in sentences.

def _recorded(row):
    e = row["evidence"]
    said = []
    if row["form"] == "gap":
        return WORDING["not_described"]
    if row["form"] in ("column", "part"):
        if row["form"] == "column" and not e["part_held"]:
            return WORDING["part_absent"]
        if not e["mapped"]:
            return WORDING["part_absent"] if row["form"] == "part" else WORDING["unmapped"]
        c = e["confirmation"]
        if c["confirmed"]:
            said.append(WORDING["confirmed"].format(date=describe._day(c["date"])) if c["by"] == "a person" and c["date"]
                        else WORDING["confirmed_count"] if c["by"] == "a count" else WORDING["confirmed"].format(date="an unrecorded date"))
        elif c["answer"] == "not sure":
            said.append(WORDING["not_sure"].format(date=describe._day(c["asked"])))
        else:
            said.append(WORDING["part_unanswered"] if row["form"] == "part" else WORDING["unanswered"])
        if row["form"] == "column":
            said.append({"present": WORDING["present"], "large": WORDING["present"], "missing": WORDING["absent"]}.get(
                e["present"], WORDING["presence_unknown"]))
            if e["translation"]:
                said.append(WORDING["translated"] if e["translation"]["translated"] else WORDING["untranslated"])
    elif row["form"] == "link":
        c = e["confirmation"]
        if not e["mapped"]:
            return WORDING["unmapped"]
        said.append(WORDING["confirmed"].format(date=describe._day(c["date"])) if c["confirmed"] and c["date"]
                    else WORDING["not_sure"].format(date=describe._day(c["asked"])) if c["answer"] == "not sure"
                    else WORDING["unanswered"] if not c["confirmed"] else WORDING["confirmed_count"])
        test = e["test"]["correction"]
        if test:
            said.append((WORDING["test_passed"] if test["passed"] else WORDING["test_failed"]).format(date=describe._day(test["date"])))
        probe = e["probe"]
        if probe and probe["real"] and probe["findings"]:
            said.append(WORDING["probe"].format(database=probe["database"], date=describe._day(probe["date"]),
                                                finding=probe["findings"][0]))
        elif probe:
            said.append(WORDING["probe_unreal"].format(where="the invented hospital" if probe["from"] == describe.INVENTED_HOSPITAL
                                                       else f"a {probe['database'] or 'unnamed'} database"))
        else:
            said.append(WORDING["no_probe"])
    elif row["form"] == "kind":
        count = f"{e['codes']} {'code' if e['codes'] == 1 else 'codes'}"
        said.append(WORDING["codes"].format(count=count, date=describe._day(e["date"])) if e["chosen"]
                    else WORDING["codes_proposed"].format(count=count) if e["codes"] else WORDING["codes_none"])
    part = e.get("part")
    if part is not None:
        said.append(WORDING["runs"] if part["runs"] else WORDING["fails"])
        if part["validated"]:
            said.append(WORDING["validated"].format(date=describe._day(part["validated"])))
        elif part["checked"]:
            said.append(WORDING["part_checked"].format(date=describe._day(part["checked"])))
        else:
            said.append(WORDING["part_unchecked"])
    return " ".join(said)


# The evidence requests.

def _sql(write):
    try:
        return write()
    except (describe.DescribeError, KeyError, ValueError, TypeError, AttributeError, IndexError):
        return None


def _requests(schema, rows):
    """The smallest investigation that would move each requirement below checked, merged where one request moves
    several, in the order of the requirements."""
    found = {}
    sitting = schema.sitting
    model, views, columns = _contract()
    meanings = {}
    for row in rows:
        if row["form"] == "kind":
            meanings.setdefault(row["key"], []).append(row["meaning"])

    def add(key, role, step, says, moves, question=None, sql=None, form=""):
        held = found.get(key)
        if held is None:
            held = found[key] = {"id": key, "form": form, "role": role, "step": STEPS.get(step) if step else None,
                                 "says": says, "question": question, "sql": sql, "moves": []}
        if moves not in held["moves"]:
            held["moves"].append(moves)

    for row in rows:
        state, e = row["state"], row["evidence"]
        if RANK[state] >= RANK[CHECKED]:
            continue
        if state == NOT_DESCRIBED:
            add(row["id"], CLINICIAN, None, WORDING["req_model"].format(title=row["title"][:1].lower() + row["title"][1:]),
                row["id"], form="model")
            continue
        if row["form"] == "column":
            view, _, name = row["id"].partition(".")
            title = rolemap.plain_about(row["id"])
            item = schema.item(row["id"])
            if state == NOT_MAPPED:
                question = describe.WORDING["question_nothing"].format(title=title)
                add(f"question:{row['id']}", CLINICIAN, 6, WORDING["req_nothing"].format(step=6), row["id"], question, form="question")
                continue
            if state == PROPOSED:
                c = e["confirmation"]
                if not c["confirmed"]:
                    question = describe.WORDING["question_column"].format(source=e["source"], title=title)
                    if c["answer"] == "not sure":
                        add(f"question:{row['id']}", CLINICIAN, 6, WORDING["req_question"].format(step=6), row["id"], question, form="question")
                    else:
                        add(f"answer:{row['id']}", ANALYST, 6, WORDING["req_answer_asked"].format(title=title, step=6), row["id"],
                            question, form="answer")
                elif e["present"] == "missing":
                    add(f"absent:{row['id']}", ANALYST, 6, WORDING["req_absent"].format(title=title, step=6), row["id"], form="answer")
                elif e["translation"] and not e["translation"]["translated"]:
                    if e["translation"]["form"] == "kind":
                        _list_request(schema, add, row["id"], row["id"], meanings.get(row["id"]))
                    else:
                        binding = item.get("binding") or {}
                        sql = _sql(lambda: sitting.values_query(row["id"], binding["table"], binding["column"], schema.year)["sql"]) \
                            if schema.writes else None
                        says = (WORDING["req_values"] if sql else WORDING["req_values_unwritten"]).format(title=title, step=6)
                        add(f"values:{row['id']}", ANALYST, 6, says, row["id"], sql=sql, form="values")
                else:
                    add(f"answer:{row['id']}", ANALYST, 6, WORDING["req_answer"].format(title=title, step=6), row["id"], form="answer")
                continue
            _counts_request(schema, add, view, row["id"])
        elif row["form"] == "part":
            title = rolemap.view_title(row["id"], False)
            if state == NOT_MAPPED:
                what = views[row["id"]]["one_row_per"]
                question = describe.WORDING["question_nothing"].format(title=f"the table that holds one row for each {what}")
                add(f"question:{row['id']}", CLINICIAN, 6, WORDING["req_nothing"].format(step=6), row["id"], question, form="question")
            elif state == PROPOSED:
                add(f"answer:{row['id']}", ANALYST, 6, WORDING["req_answer_part"].format(title=title, step=6), row["id"], form="answer")
            else:
                _counts_request(schema, add, row["id"], row["id"])
        elif row["form"] == "link":
            source = row["source"]
            if state in (NOT_MAPPED, PROPOSED):
                # The link rests on its column, whose own request is the smaller step.
                item = schema.item(source)
                title = rolemap.plain_about(source)
                if not _mapped(item):
                    question = describe.WORDING["question_nothing"].format(title=title)
                    add(f"question:{source}", CLINICIAN, 6, WORDING["req_nothing"].format(step=6), row["id"], question, form="question")
                elif (item.get("confirmation") or {}).get("answer") == "not sure":
                    question = describe.WORDING["question_column"].format(source=_source(item), title=title)
                    add(f"question:{source}", CLINICIAN, 6, WORDING["req_question"].format(step=6), row["id"], question, form="question")
                else:
                    add(f"answer:{source}", ANALYST, 6, WORDING["req_answer"].format(title=title, step=6), row["id"], form="answer")
                continue
            kind = sitting.probe_kind(source) if schema.writes else None
            probe = _sql(lambda: sitting.probe_query(source, schema.year)["sql"]) if kind == "link" else None
            if probe:
                add(f"probe:{source}", ANALYST, None, WORDING["req_probe"].format(year=schema.year, title=row["title"][:1].lower() + row["title"][1:]),
                    row["id"], sql=probe, form="test query")
            else:
                _counts_request(schema, add, source.split(".")[0], row["id"])
        elif row["form"] == "kind":
            if state in (NOT_MAPPED, PROPOSED):
                _list_request(schema, add, row["key"], row["id"], meanings.get(row["key"]))
            else:
                _counts_request(schema, add, row["key"].split(".")[0], row["id"])
    return list(found.values())


def _versioned(schema, request):
    """Adds to a request what the evidence import needs to take its result back: the request's format, the schema
    version it was made from, a stable request id, and, for a request that a result answers, each of its queries with
    the columns and types of the result it expects, or, for a reconciliation, what the reconciliation covers."""
    sitting = schema.sitting
    names = []
    form = request["form"]
    key = request["id"].split(":", 1)[-1]
    if form == "counts":
        names = [f"count-{n}" for n in key.split(",")]
    elif form == "test query":
        names = ["probe-" + re.sub(r"[^\w]+", "-", key).strip("-")]
    elif form == "code list":
        names = [f"charted-{key.replace('.', '-')}"]
    elif form in ("values", "counting query") and request.get("sql"):
        names = [n for n, state in sitting.journal.items() if n.startswith("values-") and state.get("about") == key][-1:]
    queries = [q for q in (sitting.request_query(n) for n in names if request.get("sql")) if q]
    request.update({"format": describe.REQUEST_FORMAT, "schema_id": schema.schema_id,
                    "request_id": "q" + evidence.digest([schema.schema_id, request["id"], request.get("sql") or ""], 16),
                    "queries": queries,
                    "expects": [{"query": q["name"], "columns": q["expects"]} for q in queries] or None})
    if form == "reconciliation":
        parts = sorted({m.split(" ")[0].split(".")[0] for m in request["moves"] if m.startswith("role_")})
        request["covers"] = sitting.covered(parts) if sitting.data is not None else {}
        request["expects"] = [{"query": "clinical reconciliation",
                               "columns": [{"name": n, "type": t} for n, t in describe.RECONCILIATION_COLUMNS]}]


def _list_request(schema, add, key, moves, meanings):
    sitting = schema.sitting
    sql = _sql(lambda: sitting.charted_query(key, schema.year)["sql"]) if schema.writes else None
    names = describe._and(meanings) if meanings else "each kind that the question needs"
    says = (WORDING["req_list"] if sql else WORDING["req_list_unwritten"]).format(step=7, title=rolemap.plain_about(key), kinds=names)
    add(f"list:{key}", ANALYST, 7, says, moves, sql=sql, form="code list")


def _counts_request(schema, add, view, moves):
    """The counts at step 8 that read a part and have not yet been judged on a real database, or a query of values
    for a draft part that no count reads."""
    names = [n for n, parts in describe.COUNT_PARTS.items() if view in parts]
    if not names:
        part = rolemap.view_title(view, False)
        if "." in moves and " = " not in moves and "->" not in moves:
            item = schema.item(moves) or {}
            binding = item.get("binding") or {}
            title = rolemap.plain_about(moves)
            sql = _sql(lambda: schema.sitting.values_query(moves, binding["table"], binding["column"], schema.year)["sql"]) \
                if schema.writes and binding.get("column") else None
            add(f"draft:{moves}", ANALYST, 6, (WORDING["req_draft"] if sql else WORDING["req_draft_unwritten"]).format(part=part, title=title),
                moves, sql=sql, form="counting query")
        else:
            add(f"draft:{view}", ANALYST, 6, WORDING["req_draft_unwritten"].format(part=part, title=rolemap.view_title(view, False)),
                moves, form="counting query")
        return
    wanted = []
    for name in names:
        held = schema.judgements.get(name) or {}
        if not (held.get("looks_right") == "yes" and held.get("database") in describe.REAL_DATABASES):
            wanted.append(name)
    wanted = wanted or names
    parts = sorted({p for n in wanted for p in describe.COUNT_PARTS[n]}, key=list(rolemap.all_views()).index)
    counts = describe._and([WORDING["count_names"][n] for n in wanted])
    sql = None
    if schema.writes:
        written = _sql(lambda: schema.sitting.count_queries(schema.year))
        if written:
            sql = "\n\n".join(q["sql"] for q in written if q["name"] in wanted)
    says = (WORDING["req_counts"] if sql else WORDING["req_counts_unwritten"]).format(
        counts=counts, step=8, parts=describe._and([rolemap.view_title(p, False) for p in parts]))
    add("counts:" + ",".join(wanted), ANALYST, 8, says, moves, sql=sql, form="counts")


# The report.

def _verdict(rows):
    if any(r["state"] == NOT_DESCRIBED for r in rows):
        return "model"
    if all(RANK[r["state"]] >= RANK[CHECKED] for r in rows):
        return "answerable"
    return "not_yet"


def assess(schema, sql, name="question.sql"):
    """The whole assessment of one question against a saved hospital schema, as a plain dictionary."""
    needs = requirements(sql, name)
    model, views, columns = _contract()
    rows = [part_evidence(schema, view) for view in needs["parts"]]
    rows += [column_evidence(schema, about, columns) for about in needs["columns"]]
    rows += [link_evidence(schema, link, columns) for link in needs["links"]]
    rows += [kind_evidence(schema, kind) for kind in needs["kinds"]]
    rows += [gap_evidence(gap) for gap in needs["gaps"]]
    for row in rows:
        row["recorded"] = _recorded(row)
    requests = _requests(schema, rows)
    if not requests and rows and all(RANK[r["state"]] >= RANK[CHECKED] for r in rows) \
            and not all(r["state"] == VALIDATED for r in rows):
        requests.append({"id": "reconcile", "form": "reconciliation", "role": CLINICIAN, "step": None,
                         "says": WORDING["req_validate"], "question": None, "sql": None,
                         "moves": [r["id"] for r in rows if r["state"] != VALIDATED]})
    clock = sorted({t["unit"] for t in needs["time"] if t["clock"]})
    if clock and not schema.settings.get("time_zone"):
        requests.append({"id": "time-zone", "form": "time zone", "role": CLINICIAN, "step": STEPS[9],
                         "says": WORDING["req_time_zone"].format(units=describe._and([f"{u}s" for u in clock]), step=9),
                         "question": None, "sql": None, "moves": ["time"]})
    for request in requests:
        _versioned(schema, request)
    # The requests that move the requirements furthest from being checked come first.
    rank = {r["id"]: RANK[r["state"]] for r in rows}
    requests.sort(key=lambda request: min((rank.get(m, RANK[CONFIRMED]) for m in request["moves"]), default=RANK[CONFIRMED]))
    verdict = _verdict(rows)
    return {"name": needs["name"], "heading": needs["heading"], "question": needs["question"],
            "verdict": verdict, "verdict_text": VERDICTS[verdict], "updated": schema.settings.get("updated"),
            "requirements": needs, "states": rows, "requests": requests,
            "time_zone": {"zone": schema.settings.get("time_zone"), "daylight_saving": schema.settings.get("daylight_saving")}}


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _inline(title):
    """A title as it reads within a sentence: "The date of birth in Patients" becomes "the date of birth in Patients",
    and the name of a part, such as Anaesthetics, keeps its capital."""
    return "the " + title[4:] if title.startswith("The ") else title


def _tests(found):
    needs = found["requirements"]
    lines = []
    for item in needs["conditions"]:
        lines.append(WORDING["condition"].format(text=item["text"], columns=describe._and([rolemap.plain_about(c) for c in item["columns"]])))
    for item in needs["time"]:
        if not item["columns"]:
            continue
        named = [rolemap.plain_about(c) for c in item["columns"]]
        if item["form"] == "diff" and len(named) == 1:
            named.append(WORDING["derived"])
        template = WORDING["time_diff"] if item["form"] == "diff" else WORDING["time_add"]
        lines.append(template.format(unit=item["unit"], columns=describe._and(named)))
    if any(t["clock"] for t in needs["time"]):
        zone = found["time_zone"]
        if zone["zone"]:
            saving = WORDING["saving_yes"] if zone["daylight_saving"] else WORDING["saving_no"] if zone["daylight_saving"] is False else ""
            lines.append(WORDING["time_zone"].format(zone=zone["zone"], saving=saving))
        else:
            lines.append(WORDING["time_zone_none"])
    behind = sorted({c for o in needs["outcomes"] for c in o["columns"]})
    if behind:
        lines.append(WORDING["outcome"].format(columns=describe._and([rolemap.plain_about(c) for c in behind])))
    for outcome in needs["named_outcomes"]:
        lines.append(WORDING["outcome_named"].format(meaning=outcome["meaning"]))
    return lines


NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _number(n):
    """A count as a person reads it in a sentence: in words up to ten, and in figures above."""
    return NUMBERS[n] if 0 <= n < len(NUMBERS) else f"{n:,}"


def _capital(text):
    return text[:1].upper() + text[1:]


def markdown(found):
    """The report on one question, in the house register."""
    lines = [f"# {found['heading']}", "", found["verdict_text"], ""]
    if found["question"]:
        lines += [WORDING["asks"].format(question=found["question"]), ""]
    lines += [WORDING["read_on"].format(date=describe._day(found["updated"] or "")), "",
              f"## {WORDING['heading_requirements']}", "", WORDING["table_head"], "| --- | --- | --- |"]
    for row in found["states"]:
        lines.append(f"| {_cell(row['title'])} | {_capital(row['state'])} | {_cell(row['recorded'])} |")
    tests = _tests(found)
    if tests:
        lines += ["", f"## {WORDING['heading_tests']}", ""] + [f"- {t}" for t in tests]
    lines += ["", f"## {WORDING['heading_missing']}", ""]
    missing = [r for r in found["states"] if RANK[r["state"]] < RANK[CHECKED]]
    if missing:
        for state in STATES:
            held = [_inline(r["title"]) for r in missing if r["state"] == state]
            if held:
                lines += [_capital(WORDING["missing_one" if len(held) == 1 else "missing_many"].format(
                    count=_number(len(held)), state=state, items=describe._and(held))), ""]
    else:
        lines += [WORDING["nothing_missing"], ""]
    lines += [f"## {WORDING['heading_requests']}", ""]
    if not found["requests"]:
        lines.append(WORDING["no_requests"])
    else:
        if any(r.get("sql") or r.get("question") for r in found["requests"]):
            lines += [WORDING["names"], ""]
        titles = {r["id"]: r["title"] for r in found["states"]}
        for n, request in enumerate(found["requests"], 1):
            moved = describe._and([_inline(titles[m]) if m in titles else WORDING["time_arithmetic"] for m in request["moves"]])
            lines.append(f"{n}. **{WORDING['for'].format(role=request['role'])}** "
                         f"{WORDING['concerns'].format(items=moved)} {request['says']}")
            if request.get("question"):
                lines += ["", f"   > {request['question']}"]
            if request.get("sql"):
                lines += ["", "   ```sql"] + [f"   {line}" if line else "" for line in request["sql"].splitlines()] + ["   ```"]
            lines.append("")
    lines += ["", f"## {WORDING['heading_principle']}", "", WORDING["principle"], ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def to_json(found):
    return json.dumps(found, indent=2, ensure_ascii=False, default=str) + "\n"


# The programme of questions.

def programme(schema, queries):
    """Over several questions, given as [(name, sql)]: how many questions need each requirement, the unresolved
    requirements ordered by how many questions they hold back, the share of questions whose every requirement has
    been checked against the database, and the share of the role model's parts and columns that the hospital schema
    gives a place for."""
    found, needed = [], {}
    for name, sql in queries:
        one = assess(schema, sql, name)
        found.append(one)
        for row in one["states"]:
            held = needed.setdefault(row["id"], {"id": row["id"], "title": row["title"], "state": row["state"], "questions": []})
            held["questions"].append(one["name"])
    for held in needed.values():
        held["count"] = len(held["questions"])
    blocking = sorted((r for r in needed.values() if RANK[r["state"]] < RANK[CHECKED]),
                      key=lambda r: (-r["count"], RANK[r["state"]], r["id"]))
    answered = sum(1 for one in found if one["verdict"] == "answerable")
    parts = columns = mapped_parts = mapped_columns = 0
    for view, names in rolemap.all_views().items():
        parts += 1
        role = (schema.map.get("roles") or {}).get(view)
        mapped_parts += bool(role and _mapped(role["rows"]))
        for column in names:
            columns += 1
            mapped_columns += bool(role and _mapped(role["columns"].get(column)))
    return {"questions": [{"name": q["name"], "heading": q["heading"], "verdict": q["verdict"], "verdict_text": q["verdict_text"]}
                          for q in found],
            "requirements": sorted(needed.values(), key=lambda r: (-r["count"], r["id"])), "blocking": blocking,
            "question_coverage": {"answered": answered, "total": len(found)},
            "structural_coverage": {"parts": mapped_parts, "all_parts": parts, "columns": mapped_columns, "all_columns": columns},
            "updated": schema.settings.get("updated"), "assessments": found}


def programme_markdown(found):
    total = found["question_coverage"]["total"]
    lines = [f"# {WORDING['programme_heading']}", "",
             WORDING["programme_read"].format(count=f"{_number(total)} {'question' if total == 1 else 'questions'}",
                                              date=describe._day(found["updated"] or "")), "",
             _capital(WORDING["question_coverage"].format(answered=_number(found["question_coverage"]["answered"]), total=_number(total),
                                                          questions="question" if total == 1 else "questions",
                                                          have="has" if found["question_coverage"]["answered"] == 1 else "have")), "",
             WORDING["structural_coverage"].format(**found["structural_coverage"]), "",
             f"## {WORDING['programme_blocking']}", ""]
    if found["blocking"]:
        lines += [WORDING["programme_blocking_head"], "| --- | --- | --- |"]
        lines += [f"| {_cell(r['title'])} | {r['state'][:1].upper() + r['state'][1:]} | {r['count']} |" for r in found["blocking"]]
    else:
        lines.append(WORDING["programme_none"])
    lines += ["", f"## {WORDING['programme_questions']}", "", WORDING["programme_questions_head"], "| --- | --- |"]
    lines += [f"| {_cell(q['heading'])} | {_cell(q['verdict_text'])} |" for q in found["questions"]]
    lines += ["", WORDING["principle"], ""]
    return "\n".join(lines)


def _write(text_md, data, out):
    if out is None:
        sys.stdout.write(text_md)
        return
    out = Path(out)
    out.write_text(to_json(data) if out.suffix.lower() == ".json" else text_md, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.feasibility",
                                     description="Whether a question over the parts of the record can be answered from a saved hospital schema.")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("report", help="The report on one question.")
    one.add_argument("schema")
    one.add_argument("query")
    one.add_argument("--out")
    many = commands.add_parser("programme", help="The programme view over a folder of questions.")
    many.add_argument("schema")
    many.add_argument("folder")
    many.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        schema = Schema.load(args.schema)
        if args.command == "report":
            path = Path(args.query)
            found = assess(schema, path.read_text(encoding="utf-8"), path.name)
            _write(markdown(found), found, args.out)
        else:
            queries = [(p.name, p.read_text(encoding="utf-8")) for p in sorted(Path(args.folder).glob("*.sql"))]
            if not queries:
                raise FeasibilityError(f"{Path(args.folder).name} holds no .sql file.")
            found = programme(schema, queries)
            _write(programme_markdown(found), found, args.out)
    except (FeasibilityError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
