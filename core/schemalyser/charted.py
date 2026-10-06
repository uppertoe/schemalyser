"""The list of what is charted on the audit's cohort: how the codes of a column of readings are found at a real hospital.

A name search on a definitions table returns every name that holds a word, and at a real hospital that is thousands of
rows. What a colleague who writes SQL would run instead is a list of the codes charted on the cohort's anaesthetics in
one year, with their counts and their names. This module composes that list as one query over the source tables.

The cohort comes from target.source_query, restructured to start from the anaesthetics, exactly as the count by year
does. The readings are reached from the step of the conversion that writes them, turned round: the joins that tie a
reading to its anaesthetic are made inner joins in the order that starts from the cohort and reaches the table of
readings last, by key, so that of that table the query reads only the readings on the cohort's own records. The step's
conditions on a reading's value are left out, so that a reading charted as text is listed as well, and its lookups of
mapping rows are left out, so that a code with no mapping row is listed as well.

The query returns, for each code, the readings and the anaesthetics, each rounded down to the nearest ten and blank under
ten, and the names that the site rules' definition table gives the code, most charted first. The names are the
hospital's own, so they are shown on the page and written into no file.
"""
import datetime
import re
from collections import deque
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import checks as checking
from . import convert
from . import scripts
from .translate import OMOP_SCHEMA

WORDING = {
    "header": "This query lists every code of {column} that was charted on the audit's anaesthetics that started in {year}, "
              "with the number of readings and of anaesthetics for each, each rounded down to the nearest ten and left blank "
              "under ten, and the names that {definition} gives each code, most charted first. To find those anaesthetics "
              "it reads {cohort}. It then reaches {path} only through the records of those anaesthetics, by key, so that of "
              "{readings} it reads only the readings on the cohort's own records in that year.",
    "empty": "The list of what is charted on the audit's anaesthetics in {year} came back empty. Either no anaesthetic of "
             "the cohort started in {year}, or the readings are not reaching their anaesthetic, and the audit cannot proceed "
             "until you find which. Check first the match that links a reading to its anaesthetic, {link}: the query with "
             "this point counts it. If the count by year shows no anaesthetic of the cohort in {year}, choose another year.",
    "zero": "None of the chosen codes was charted on an anaesthetic of the audit's cohort from {start} to {end}. Either the "
            "codes are wrong or the readings are not reaching their anaesthetic, and the audit cannot proceed until you find "
            "which. Check first the match that links a reading to its anaesthetic, {link}: the query with this point counts it.",
    "no_cohort": "The count by year found no anaesthetic of the audit's cohort in any year. Either the cohort's conditions "
                 "find no one here, or the anaesthetics are not reaching their patients, and the audit cannot proceed until "
                 "you find which.",
    "sized": "{table} (about {rows:,} rows)",
}

# The anaesthetic's own columns, which stand for the columns of the procedure row that the reading step reads.
FROM_ANAESTHETIC = {("procedure_occurrence", "procedure_occurrence_id"): "anaesthetic_id",
                    ("procedure_occurrence", "procedure_datetime"): "start_datetime",
                    ("procedure_occurrence", "procedure_end_datetime"): "end_datetime",
                    ("visit_detail", "visit_detail_source_value"): "visit_detail_source_value"}


class Unavailable(Exception):
    """The list cannot be composed on this route."""


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def default_year(years, today=None):
    """The latest complete year in the count by year: the latest year before this one, or the latest year where none is."""
    today = today or datetime.date.today()
    listed = sorted({int(y[0]) for y in years or []})
    earlier = [y for y in listed if y < today.year]
    return (earlier or listed or [today.year - 1])[-1]


def _omop(node):
    return isinstance(node, exp.Table) and (node.db or "").upper() == OMOP_SCHEMA.upper()


def _refs(node):
    return {c.table.upper() for c in node.find_all(exp.Column) if c.table}


def _column_of(node):
    while isinstance(node, (exp.Cast, exp.TryCast, exp.Paren)):
        node = node.this
    return node if isinstance(node, exp.Column) else None


def _reading_step(folder, table, column):
    """(step file, parsed SELECT) of the step that writes measurement rows whose source value is the column, and whose
    event field is the anaesthetic's procedure row."""
    from . import questions
    for step, sql in questions._steps(folder):
        if step.get("table") != "measurement":
            continue
        try:
            tree = sqlglot.parse_one(sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        if not isinstance(tree, exp.Select):
            continue
        aliases = {t.alias_or_name.upper(): t for t in tree.find_all(exp.Table) if t.find_ancestor(exp.Select) is tree}
        outputs = {e.alias_or_name.lower(): e.this if isinstance(e, exp.Alias) else e for e in tree.expressions}
        source = _column_of(outputs.get("measurement_source_value"))
        event = _column_of(outputs.get("measurement_event_id"))
        if source is None or event is None or source.name.upper() != column.upper():
            continue
        owner = aliases.get(source.table.upper())
        anaesthetic = aliases.get(event.table.upper())
        if owner is None or owner.name.upper() != table.upper() or not _omop(anaesthetic) \
                or anaesthetic.name.lower() != "procedure_occurrence":
            continue
        return step["file"], tree, source.table.upper()
    raise Unavailable("no step writes the readings of this column with their anaesthetic")


def _mapping_value(folder, conjuncts):
    """The target concept of the one mapping row that a lookup's fixed conditions name, or None."""
    fixed = {}
    for c in conjuncts:
        if isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) and isinstance(c.expression, exp.Literal):
            fixed[c.this.name.lower()] = c.expression.this
    if "source_vocabulary_id" not in fixed or "source_code" not in fixed:
        return None
    for row in convert.mapping_dicts(folder):
        if str(row.get("source_vocabulary_id")) == fixed["source_vocabulary_id"] and str(row.get("source_code")) == fixed["source_code"]:
            value = str(row.get("target_concept_id") or "").strip()
            return value if re.fullmatch(r"-?\d+", value) else None
    return None


def _cohort_sql(conversion, target_sql, catalogue, year, kinds, name, window=None, person=False, outer=False):
    """The restructured cohort, as (the text up to and including its CTEs, the CTE's name, whether it carries the raw key).

    The period is the year, or window, a list of conditions on the start with {a} for the anaesthetic's alias. person adds
    the anaesthetic's patient, and outer keeps an anaesthetic whose record the visit detail table does not hold."""
    from . import target
    alias, conditions = target._cohort_conditions(target_sql)
    if alias is None:
        raise Unavailable("the target query reads no anaesthetic")
    period = [w.format(a=alias) for w in window] if window is not None else \
        [f"{alias}.{target.START_FIELD} >= CAST('{year}-01-01' AS date)",
         f"{alias}.{target.START_FIELD} < CAST('{year + 1}-01-01' AS date)"]
    where = list(conditions) + period
    if kinds:
        where.append(f"{alias}.{target.KIND_FIELD} IN ({', '.join(str(int(k)) for k in kinds)})")
    query = (f"WITH cohort AS (\n    SELECT {alias}.anaesthetic_id, " + (f"{alias}.person_id, " if person else "")
             + f"{alias}.start_datetime, {alias}.end_datetime, vd.visit_detail_source_value\n"
             f"    FROM   omop.anaesthetic {alias}\n           {'LEFT JOIN' if outer else 'JOIN'} omop.visit_detail vd "
             f"ON vd.visit_detail_id = {alias}.visit_detail_id\n"
             f"    WHERE  " + "\n      AND  ".join(where) + "\n)\nSELECT c.anaesthetic_id FROM cohort c")
    found = target.source_query(conversion, query, catalogue, target_name=name, blank=False)
    if not found["restructured"]:
        raise Unavailable(found["reason"] or "the cohort cannot be restructured")
    text = found["sql"]
    head, _, _ = text.partition("/* The question itself")
    named = re.search(r"^(q\d+_cohort) AS \($", head, re.M)
    if not named or "\nWITH\n" not in head:
        raise Unavailable("the cohort has no common table expression of its own")
    keyed = "visit_detail_source_value__key" in head
    if keyed:
        head = re.sub(r"(^q\d+_cohort AS \(\n  SELECT\n(?:.*\n)*?    vd\.visit_detail_source_value)\n",
                      r"\1,\n    vd.visit_detail_source_value__key\n", head, count=1, flags=re.M)
    return head.rstrip().rstrip(","), named.group(1), keyed


def route(folder, catalogue, file, tree, reading, keyed, with_values=False):
    """How the step that writes the readings ties a reading to its anaesthetic, turned round to start from the cohort, as
    {"nodes", "path", "placements", "rest", "link"}: placements is [(table node, [conditions])] from the cohort to the
    table of readings, each with the conditions that can be tested once it is reached, in the cohort's columns (c.*),
    and rest the conditions that are tested after the last join. Raises Unavailable where the readings cannot be
    reached from the cohort by key. with_values keeps the step's conditions on a reading's value, which the list leaves out."""
    from . import target
    # The tables of the step, and the conditions of its joins and of its WHERE, each with the aliases it reads.
    nodes = {t.alias_or_name.upper(): t for t in tree.find_all(exp.Table) if t.find_ancestor(exp.Select) is tree}
    conjuncts = []
    for join in tree.args.get("joins") or []:
        conjuncts += [(c, join.this.alias_or_name.upper()) for c in target._conjuncts(join.args.get("on"))]
    where = tree.args.get("where")
    conjuncts += [(c, None) for c in target._conjuncts(where.this if where is not None else None)]
    omop = {a: n.name.lower() for a, n in nodes.items() if _omop(n)}
    mappings = {a for a, t in omop.items() if t == "source_to_concept_map"}
    anchors = {a for a, t in omop.items() if t in ("procedure_occurrence", "visit_detail")}

    # The path from the reading table to the anaesthetic, through equalities between columns of two source tables.
    edges = {}
    for c, _ in conjuncts:
        if isinstance(c, exp.EQ) and _column_of(c.this) is not None and _column_of(c.expression) is not None:
            a, b = _column_of(c.this).table.upper(), _column_of(c.expression).table.upper()
            if a != b and a in nodes and b in nodes and not {a, b} & mappings:
                edges.setdefault(a, set()).add(b)
                edges.setdefault(b, set()).add(a)
    before, queue = {reading: None}, deque([reading])
    end = None
    while queue:
        alias = queue.popleft()
        if alias in anchors and omop[alias] == "visit_detail":
            end = alias
            break
        for other in sorted(edges.get(alias, ())):
            if other not in before and (other not in omop or other in anchors):
                before[other] = alias
                queue.append(other)
    if end is None:
        raise Unavailable("the step does not tie a reading to its anaesthetic through its record")
    path, alias = [], before[end]
    while alias is not None:
        path.append(alias)
        alias = before[alias]
    # path runs from the table next to the anaesthetic to the reading table.

    # Each condition is kept, rewritten in the cohort's columns, or left out.
    values = {m: _mapping_value(folder, [c for c, owner in conjuncts if owner == m]) for m in mappings}
    kept = []
    for c, owner in conjuncts:
        refs = _refs(c)
        if owner in mappings or any(values.get(m) is None for m in refs & mappings):
            continue    # a lookup of mapping rows, or a condition on one whose value is not fixed
        if not refs - anchors:
            continue    # true of every anaesthetic by construction: the anaesthetic is its own procedure row
        if refs - set(path) - anchors - mappings:
            continue    # a join that the list does not need, such as the patient or the visit
        if reading in refs and c.find(exp.TryCast) is not None and not with_values:
            continue    # a condition on the reading's value, which would leave out a reading charted as text
        c = c.copy()
        for column_node in list(c.find_all(exp.Column)):
            owner_alias = column_node.table.upper()
            if owner_alias in mappings:
                column_node.replace(exp.Literal.number(values[owner_alias]))
            elif owner_alias in anchors:
                mapped = FROM_ANAESTHETIC.get((omop[owner_alias], column_node.name.lower()))
                if mapped is None:
                    raise Unavailable("a condition reads a column of the anaesthetic that the cohort does not carry")
                column_node.replace(exp.column(mapped, table="c"))
        # A join on a key cast to text becomes a join on the key, where the cohort carries the key.
        if keyed and isinstance(c, exp.EQ):
            for one, other in ((c.this, c.expression), (c.expression, c.this)):
                if isinstance(other, exp.Column) and other.table == "c" and other.name == "visit_detail_source_value" \
                        and isinstance(one, exp.Cast) and isinstance(one.this, exp.Column):
                    c = exp.EQ(this=one.this.copy(), expression=exp.column("visit_detail_source_value__key", table="c"))
                    break
        kept.append(exp.Paren(this=c) if isinstance(c, exp.Or) else c)

    # The joins, from the cohort to the reading table, each with the conditions that it can test once it is reached.
    placed, placed_on, used, link = {"C"}, [], set(), None
    for alias in path:
        node = nodes[alias]
        on = [c for i, c in enumerate(kept) if i not in used and alias in _refs(c) and _refs(c) <= placed | {alias}]
        if not any(isinstance(c, exp.EQ) for c in on):
            raise Unavailable("a table on the way to the readings cannot be reached by key")
        used |= {i for i, c in enumerate(kept) if any(c is o for o in on)}
        if link is None and len(placed) > 1:
            link = next((c for c in on if isinstance(c, exp.EQ) and len(_refs(c) - {"C"}) == 2), None)
        placed_on.append(on)
        placed.add(alias)
    rest = [c for i, c in enumerate(kept) if i not in used]
    return {"nodes": nodes, "path": path, "placements": [(nodes[a], on) for a, on in zip(path, placed_on)], "rest": rest,
            "link": link}


def listed_query(conversion, target_sql, catalogue, rules, column, year, kinds=(), checks=None, name="the audit",
                 count=None):
    """The list of what is charted on the cohort in one year, as {"sql", "year", "column", "link", "matched", "worst",
    "withheld"}.

    The list is a script (see scripts.py): the cohort into #cohort, then the readings reached from it by key. sql is ""
    where the script is not offered, and withheld then says why in one sentence; worst is the most anaesthetics that it
    reads, from the count by year (count, held.count()). link names the match that ties a reading to its anaesthetic,
    which is the first thing to check when the list or a count of the chosen codes comes back empty, and matched is the
    plain query that measures it, as {"id", "sql"}. Raises Unavailable where the list cannot be composed on this route.
    """
    from . import target
    folder = Path(conversion)
    table, _, col = column.partition(".")
    definition = target._definition(rules, catalogue, table, col)
    if definition is None:
        raise Unavailable("the site rules give no definition table for this column")
    file, tree, reading = _reading_step(folder, table, col)
    head, cohort, keyed = _cohort_sql(conversion, target_sql, catalogue, year, kinds, name)
    found_route = route(folder, catalogue, file, tree, reading, keyed)
    nodes, path, rest, link = found_route["nodes"], found_route["path"], found_route["rest"], found_route["link"]
    lines = [f"JOIN {checking._name(catalogue, node.name)} AS {node.alias_or_name} WITH (NOLOCK)\n  ON "
             + "\n  AND ".join(c.sql(dialect="tsql") for c in on) for node, on in found_route["placements"]]
    def_table, code, label, shown = definition
    r = nodes[reading].alias_or_name
    names = [f"CAST(d.{checking._bracket(n)} AS nvarchar(200)) AS {checking._bracket(n)}" for n in shown]
    least = checking.MINIMUM_COUNT
    select = [f"SELECT CAST({r}.{checking._bracket(col)} AS nvarchar(50)) AS code,",
              f"       CASE WHEN COUNT(*) >= {least} THEN (COUNT(*) / 10) * 10 END AS readings,",
              f"       CASE WHEN COUNT(DISTINCT c.anaesthetic_id) >= {least} THEN (COUNT(DISTINCT c.anaesthetic_id) / 10) * 10 END AS anaesthetics"
              + ("," if names else "")]
    select += [f"       {n}" + ("," if i < len(names) - 1 else "") for i, n in enumerate(names)]
    body = select + ["FROM #cohort AS c"] + lines
    body.append(f"LEFT JOIN {checking._name(catalogue, def_table)} AS d WITH (NOLOCK)\n  ON d.{checking._bracket(code)} = {r}.{checking._bracket(col)}")
    if rest:
        body.append("WHERE " + "\n  AND ".join(c.sql(dialect="tsql") for c in rest))
    body.append(f"GROUP BY {r}.{checking._bracket(col)}" + "".join(f", d.{checking._bracket(n)}" for n in shown))
    body.append("ORDER BY COUNT(*) DESC;")

    # What it reads, by name and with the size where the check results give it.
    def sized(name):
        rows = checking.size_of(name, checks) if checks is not None else None
        return WORDING["sized"].format(table=name, rows=rows) if rows else name
    cte_names = set(re.findall(r"^([A-Za-z0-9_]+) AS \($", head, re.M))
    cohort_tables = sorted({t for t in re.findall(r"\b(?:FROM|JOIN)\s+\[?(?:dbo\]?\.\[?)?([A-Za-z_][A-Za-z0-9_]*)\]?", head)
                            if t not in cte_names and t.lower() not in ("values",)})
    path_tables = [nodes[a].name for a in path]
    # The largest table that part 1, like the count by year, reads to find the anaesthetics, with its size.
    sizes = sorted(((checking.size_of(t, checks) or 0, t) for t in cohort_tables), reverse=True) if checks is not None else []
    largest = [sizes[0][1], sizes[0][0]] if sizes and sizes[0][0] else None
    sentence = WORDING["header"].format(column=column, year=year, definition=def_table, cohort=_join(sized(t) for t in cohort_tables),
                                        path=_join(sized(t) for t in path_tables), readings=nodes[reading].name)
    header = "\n".join(f"-- {line}" for line in target.textwrap_lines(sentence))
    _, _, ctes = head.partition("\nWITH\n")
    # The same list as one query, as it was written before it became a script, which the tests compare it with.
    single = header + "\nWITH\n" + ctes + "\n" + "\n".join(body).replace("FROM #cohort AS c", f"FROM {cohort} AS c", 1) + "\n"
    found = {"sql": "", "single": single, "year": year, "column": column, "step": file, "link": "", "matched": None,
             "worst": None, "withheld": "", "largest": largest}
    worst, reason = scripts.worst_case(count, f"{year}-01-01", f"{year}-12-31")
    try:
        if not keyed or re.search(r"\bc\.visit_detail_source_value\b(?!__key)", "\n".join(body)):
            raise scripts.Unsafe("the cohort's records are matched to the readings on a key cast to text")
        scripts.check_cohort(head)
    except scripts.Unsafe as error:
        reason = scripts.WORDING["unsafe"].format(reason=str(error))
    if reason:
        found["withheld"] = reason
    else:
        columns = ["anaesthetic_id", "start_datetime", "end_datetime", "visit_detail_source_value__key"]
        found["sql"] = scripts.assemble(worst, header, scripts.cohort_statement(head, cohort, columns), "\n".join(body))
        found["worst"] = worst
    if link is not None:
        (lt, lc), (rt, rc) = [(_column_of(s).table, _column_of(s).name) for s in (link.this, link.expression)]
        lt, rt = nodes[lt.upper()].name, nodes[rt.upper()].name
        found["link"] = f"{lt}.{lc} = {rt}.{rc}"
        if target._own_key(catalogue, lt, lc) and not target._own_key(catalogue, rt, rc):
            (lt, lc), (rt, rc) = (rt, rc), (lt, lc)
        check = checking.Check("matched", lt, lc, parent=(rt, rc))
        found["matched"] = {"id": check.key(), "sql": check.matched_plain(catalogue)}
    return found


def as_script(conversion, catalogue, text, target_sql, window, kinds, name, worst, person=False):
    """A composed query that reads the readings (target.source_query's restructured text), written as a script: its cohort
    into #cohort, built apart by _cohort_sql for the same period and kinds, and each step that reads a table of readings
    rewritten to reach it from #cohort by key, every other common table expression kept. Raises scripts.Unsafe with the
    reason where that cannot be shown to keep the answer."""
    from . import questions
    folder = Path(conversion)
    blocks = scripts._blocks(text)
    original = next((n for n in blocks if re.fullmatch(r"q\d+_\w+", n) and "FROM omop_anaesthetic AS" in text[slice(*blocks[n])]),
                    None)
    if original is None:
        raise scripts.Unsafe("the query has no cohort of its own that Schemalyser can set apart")
    columns, conditions = scripts.cohort_columns(text, original)
    try:
        head, cohort, keyed = _cohort_sql(conversion, target_sql, catalogue, None, kinds, name, window=window, person=person,
                                          outer=True)
    except Unavailable as error:
        raise scripts.Unsafe(str(error)) from error
    if not keyed:
        raise scripts.Unsafe("the cohort's records are matched to the readings on a key cast to text")
    scripts.check_cohort(head)
    # The cohort set apart must be the query's own cohort: the same conditions on the anaesthetic table.
    start, end = scripts._blocks(head)[cohort]
    if scripts._conditions(sqlglot.parse_one(head[start:end], dialect="tsql")) != conditions:
        raise scripts.Unsafe("its cohort cannot be shown to be the same as the cohort that Schemalyser sets apart")
    held = ["anaesthetic_id", "start_datetime", "end_datetime"] + (["person_id"] if person else []) \
        + ["visit_detail_source_value__key"]
    if set(columns) - set(held):
        raise scripts.Unsafe("its cohort carries a column that the temporary table does not hold")
    # Each step that writes readings and is read here: its table of readings, reached from #cohort.
    steps = []
    for step, sql in questions._steps(folder):
        named = f"step_\\d+_{re.escape(Path(step.get('file', '')).stem)}"
        found = next((n for n in blocks if re.fullmatch(named, n)), None)
        if step.get("table") != "measurement" or found is None:
            continue
        try:
            tree = sqlglot.parse_one(sql, dialect="tsql")
            source = tree.args.get("from_") or tree.args.get("from")
            reading = source.this
            if not isinstance(reading, exp.Table) or _omop(reading):
                raise scripts.Unsafe("a step that writes readings does not start from a table of readings")
            placements = route(folder, catalogue, step["file"], tree, reading.alias_or_name.upper(), keyed)["placements"]
        except (sqlglot.errors.SqlglotError, Unavailable, AttributeError) as error:
            raise scripts.Unsafe("a step that writes readings cannot be reached from the cohort by key") from error
        steps.append((found, reading.name, scripts.reached(placements, catalogue, reading.alias_or_name, reading)))
    if not steps:
        raise scripts.Unsafe("no step that reads the readings was found in it")
    rewritten = scripts.rewrite(text, original, columns, steps)
    comments, body = scripts.split_comments(rewritten)
    return scripts.assemble(worst, comments, scripts.cohort_statement(head, cohort, held), body)


def reference_script(conversion, catalogue, draft, target_sql, settings, held, name="the audit"):
    """The reference query (the restructured source draft) as a script for the study period, as {"sql", "worst",
    "withheld"}: sql is "" where it is not offered, and withheld then says why in one sentence."""
    settings = settings or {}
    count = held.count() if held is not None else None
    years = [int(y[0]) for y in count["years"]] if count else []
    start = settings.get("from") or (f"{min(years)}-01-01" if years else "")
    end = settings.get("to") or (f"{max(years)}-12-31" if years else "")
    worst, withheld = scripts.worst_case(count, start, end) if start and end else (None, scripts.WORDING["unseen"])
    if withheld:
        return {"sql": "", "worst": "", "withheld": withheld}
    try:
        sql = as_script(conversion, catalogue, draft, target_sql, _period(settings), settings.get("kinds") or (), name, worst,
                        person=True)
    except scripts.Unsafe as error:
        return {"sql": "", "worst": "", "withheld": scripts.WORDING["unsafe"].format(reason=str(error))}
    return {"sql": sql, "worst": scripts.page(worst), "withheld": ""}


def counted_script(conversion, catalogue, target_sql, settings, codes, concepts, text, name, worst):
    """The count of how often each chosen code is charted (target.charted_count), written as a script like the list: the
    cohort of the period into #cohort, then the readings reached from it by key alone, with the step's own conditions on a
    reading and its value, for the chosen codes whose mapping row gives a concept that the audit reads. text is the count
    as one query, which names the step that it reads. Raises scripts.Unsafe with the reason where it cannot be written."""
    from . import questions, target
    folder = Path(conversion)
    blocks = scripts._blocks(text)
    found = []
    for step, sql in questions._steps(folder):
        if step.get("table") != "measurement" or not any(
                re.fullmatch(f"step_\\d+_{re.escape(Path(step.get('file', '')).stem)}", n) for n in blocks):
            continue
        try:
            tree = sqlglot.parse_one(sql, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        outputs = {e.alias_or_name.lower(): e.this if isinstance(e, exp.Alias) else e for e in tree.expressions}
        source = _column_of(outputs.get("measurement_source_value"))
        concept = outputs.get("measurement_concept_id")
        found.append((step["file"], tree, source, concept))
    if len(found) != 1 or found[0][2] is None:
        raise scripts.Unsafe("the count does not read the readings through one step that Schemalyser can turn round")
    file, tree, source, concept = found[0]
    aliases = {t.alias_or_name.upper(): t for t in tree.find_all(exp.Table) if t.find_ancestor(exp.Select) is tree}
    reading = source.table.upper()
    lookup = next((c.table.upper() for c in (concept.find_all(exp.Column) if concept is not None else [])), None)
    if reading not in aliases or lookup not in aliases or aliases[lookup].name.lower() != "source_to_concept_map":
        raise scripts.Unsafe("the count's concept is not looked up from a mapping row of the reading's code")
    join = next(j for j in tree.args.get("joins") or [] if j.this.alias_or_name.upper() == lookup)
    vocabulary = next((c.expression.this for c in target._conjuncts(join.args.get("on")) if isinstance(c, exp.EQ)
                       and isinstance(c.this, exp.Column) and c.this.name.lower() == "source_vocabulary_id"
                       and isinstance(c.expression, exp.Literal)), None)
    mapped = {str(r.get("source_code")) for r in convert.mapping_dicts(folder)
              if str(r.get("source_vocabulary_id")) == vocabulary and str(r.get("target_concept_id") or "").strip() in
              {str(c) for c in concepts}}
    chosen = sorted(set(codes) & mapped)
    if not chosen:
        raise scripts.Unsafe("none of the chosen codes has a mapping row for a concept that the audit reads")
    window = _period(settings)
    head, cohort, keyed = _cohort_sql(conversion, target_sql, catalogue, None, settings.get("kinds") or (), name,
                                      window=window)
    if not keyed:
        raise scripts.Unsafe("the cohort's records are matched to the readings on a key cast to text")
    scripts.check_cohort(head)
    try:
        found_route = route(folder, catalogue, file, tree, reading, keyed, with_values=True)
    except Unavailable as error:
        raise scripts.Unsafe("the readings cannot be reached from the cohort by key") from error
    r = aliases[reading].alias_or_name
    code = f"CAST({r}.{checking._bracket(source.name)} AS varchar(50))"
    lines = [f"JOIN {checking._name(catalogue, node.name)} AS {node.alias_or_name} WITH (NOLOCK)\n  ON "
             + "\n  AND ".join(c.sql(dialect="tsql") for c in on) for node, on in found_route["placements"]]
    where = [c.sql(dialect="tsql") for c in found_route["rest"]] + \
        [f"{code} IN ({', '.join(target._quote(c) for c in chosen)})"]
    body = "\n".join(lines)
    if re.search(r"\bc\.visit_detail_source_value\b(?!__key)", body + " ".join(where)):
        raise scripts.Unsafe("the cohort's records are matched to the readings on a key cast to text")
    least = checking.MINIMUM_COUNT
    statement = (f"SELECT {code} AS code,\n"
                 f"       CASE WHEN COUNT(*) >= {least} THEN (COUNT(*) / 10) * 10 END AS readings,\n"
                 f"       CASE WHEN COUNT(DISTINCT c.anaesthetic_id) >= {least} THEN (COUNT(DISTINCT c.anaesthetic_id) / 10) * 10 END AS anaesthetics\n"
                 f"FROM #cohort AS c\n{body}\nWHERE " + "\n  AND ".join(where) + f"\nGROUP BY {code}\nORDER BY code;")
    comments, _ = scripts.split_comments(text)
    columns = ["anaesthetic_id", "start_datetime", "end_datetime", "visit_detail_source_value__key"]
    return scripts.assemble(worst, comments, scripts.cohort_statement(head, cohort, columns), statement)


def _period(settings):
    """The study period's conditions on the anaesthetic's start, as target.with_settings writes them, with {a} for the alias,
    for the last year of the period where settings is the pair that target._last_year gives."""
    from . import target
    start, end = settings["window"] if "window" in settings else (settings.get("from"), settings.get("to"))
    out = []
    if start:
        out.append(f"{{a}}.{target.START_FIELD} >= CAST('{start}' AS date)")
    if end:
        out.append(f"{{a}}.{target.START_FIELD} < DATEADD(day, 1, CAST('{end}' AS date))")
    return out


def _column_sought(rows, target_sql, conversion, catalogue, rules):
    """The column of readings whose codes the audit needs, from its codes items, where the site rules name its lookup table."""
    from . import target
    read = target.read_target(target_sql, target.custom_fields(conversion))
    concepts = {str(c) for (table, _), values in read["concepts"].items() if table == "measurement" for c in values}
    for row in rows:
        if row["kind"] != "codes" or not row.get("_columns"):
            continue
        if not str(row["question_id"]).rsplit("-", 1)[-1] in concepts:
            continue
        table, column = row["_columns"][0]
        if target._definition(rules, catalogue, table, column) is not None:
            return f"{catalogue.table(table).name}.{catalogue.table(table).column(column).name}"
    return None


def _open_point(rows, base, row_id, sentence, query):
    """An open point of the first phase, built on the count by year's row, at the head of the list."""
    row = dict(base, question_id=row_id, question=sentence, status="open", blocking="yes", evidence_in_hand="",
               phase="source", query_state="ready" if query else "", query_reason="", query=query["sql"] if query else "",
               _queries=[query["id"]] if query else [], _count=None, _top=True, intent="", route="")
    rows.insert(0, row)


LISTED_INSTEAD = ("Schemalyser needs the codes of {column} that mean {meaning}. Schemalyser finds these codes in the list of "
                  "what is charted on the audit's anaesthetics, which it offers once you have seen the count by year. Expect "
                  "several codes for one meaning.")


def _search_replaced(rows, traced, column):
    """Where the codes of a column are chosen from the list of what is charted, its codes questions no longer speak of a
    name search that the page does not show, in the item or in the list of questions to send."""
    for row in rows:
        ask = row.get("_ask")
        if not ask or ask.get("kind") != "codes" or not ask.get("search") or str(ask.get("column", "")).upper() != column.upper():
            continue
        text = LISTED_INSTEAD.format(column=ask["column"], meaning=ask.get("meaning") or "this meaning")
        if traced.get("questions"):
            traced["questions"] = traced["questions"].replace(ask["text"], text)
        ask["text"] = text


def attach(rows, traced, conversion, target_sql, catalogue, rules, held, settings, checks, name):
    """The list of what is charted on the cohort, for the page, as {"sql", "year", "column", "years", "rows", "link"} or None;
    and the open points that an empty cohort, an empty list or a count of none for the chosen codes raise, at the head of
    the rows, each with the query that measures the match that ties a reading to its anaesthetic."""
    count = held.count()
    base = next((r for r in rows if r["question_id"] == "count-by-year"), None)
    if base is None or not traced.get("steps"):
        return None
    if count is None:
        # Before the count by year has been seen, the list is not yet offered; where it can be had on this route, the page
        # says that the codes will be chosen from it, and offers no name search.
        column = _column_sought(rows, target_sql, conversion, catalogue, rules)
        if column is None:
            return None
        try:
            found = listed_query(conversion, target_sql, catalogue, rules, column, default_year([]), (), checks, name)
        except Unavailable:
            return None
        _search_replaced(rows, traced, column)
        return {"sql": "", "year": None, "column": column, "years": [], "rows": None, "link": "", "waiting": True,
                "largest": found["largest"]}
    if not any(c for _, _, c, *_ in count["years"]):
        base["question"] = WORDING["no_cohort"]
        base["_top"] = True
        return None
    column = _column_sought(rows, target_sql, conversion, catalogue, rules)
    if column is None:
        return None
    listed = held.listed()
    year = listed["year"] if listed and listed.get("column", "").upper() == column.upper() else default_year(count["years"])
    try:
        found = listed_query(conversion, target_sql, catalogue, rules, column, year, (settings or {}).get("kinds") or (),
                             checks, name, count)
    except Unavailable:
        return None
    _search_replaced(rows, traced, column)
    # The match query is offered until its result is in the check results; after that, the point stays and says what to do.
    if found["matched"] and checks is not None and checks.matched:
        key = found["matched"]["id"].split(":", 1)[-1].replace("-", ".").upper()
        if any(f"{a}.{b}.{c}.{d}".upper() == key for a, b, c, d in checks.matched):
            found["matched"] = None
    offered = traced.setdefault("queries", {"sizes": None, "queries": []})
    if found["matched"] and not any(q["id"] == found["matched"]["id"] for q in offered["queries"]):
        offered["queries"].append({"id": found["matched"]["id"], "sql": found["matched"]["sql"], "state": "exact", "table": ""})
    ran = listed["rows"] if listed and listed["year"] == year and listed.get("column", "").upper() == column.upper() else None
    if ran == 0:
        _open_point(rows, base, "charted-empty", WORDING["empty"].format(year=year, link=found["link"] or "the match of a reading to its record"),
                    found["matched"])
    chosen = held.charted()
    # A year in which the count by year shows fewer than ten anaesthetics of the cohort says little about the codes.
    cohort_in = {int(y[0]): y[2] for y in count["years"]}
    if chosen is not None and not chosen["counts"] and cohort_in.get(int(chosen["to"][:4])):
        _open_point(rows, base, "charted-zero", WORDING["zero"].format(start=chosen["from"], end=chosen["to"],
                                                                         link=found["link"] or "the match of a reading to its record"),
                    found["matched"])
    years = sorted({int(y[0]) for y in count["years"]})
    return {"sql": found["sql"], "year": year, "column": column, "years": years, "rows": ran, "link": found["link"],
            "worst": scripts.page(found["worst"]) if found["worst"] else "", "withheld": found["withheld"],
            "largest": found["largest"]}


# The decisions that the reference query and the specification do not yet apply, each an open point with the one action
# that would make it real.
DECISION_POINTS = {
    "bypass": ("Time on cardiopulmonary bypass is to be left out, and neither the reference query nor the specification "
               "applies that yet. Run the name search with this point on {table}, which defines the events of the "
               "anaesthetic record, to find the events that mark the start and the end of bypass.",
               ["BYPASS", "CPB", "ON PUMP", "OFF PUMP"]),
    "ecmo": ("Time on ECMO is to be left out, and neither the reference query nor the specification applies that yet. Run the "
             "name search with this point on {table}, which defines the events of the anaesthetic record, to find the events "
             "that mark the start and the end of ECMO.", ["ECMO", "ECLS", "EXTRACORPOREAL"]),
    "isolated": ("A single isolated low reading is to be ignored, and the reference query does not apply that yet. Schemalyser "
                 "takes an isolated low reading to be a single reading below 40 with the readings either side of it at 40 or "
                 "above. The clinician adds that rule to the reference query after the meeting.", None),
    "age": ("A limit on postmenstrual age is chosen, and the reference query does not apply it yet. It needs the column that "
            "holds the gestational age at birth{gestation}, and the clinician adds the limit to the reference query after "
            "the meeting.", None),
}
NO_EVENTS = ("The site rules name no table that defines the events of the anaesthetic record, so name it in the meeting and "
             "add it to the site rules; Schemalyser then offers the search.")


def _events_definition(catalogue, rules):
    """(definition table, key column, name column, shown) of the events of the anaesthetic record, from the site rules: the
    first definition key whose column or table names events, or None."""
    from . import target
    for key in getattr(rules, "definition_keys", None) or []:
        column, table = str(key.get("column", "")), str(key.get("table", ""))
        if "EVENT" not in (column + table).upper():
            continue
        for entry in ([catalogue.table(table)] if table else catalogue.tables()):
            if entry is not None and entry.column(column) is not None:
                found = target._definition(rules, catalogue, entry.name, column)
                if found is not None:
                    return found
    return None


def decision_points(rows, traced, settings, catalogue, rules):
    """Adds to rows an open point for each decision that departs from the rule in force and that the reference query does
    not yet apply, with the name search that finds the events of bypass or ECMO where the site rules allow one."""
    from . import target
    settings = settings or {}
    base = next((r for r in rows if r["question_id"] == "count-by-year"), None)
    if base is None:
        return
    offered = traced.setdefault("queries", {"sizes": None, "queries": []})
    for key, (sentence, words) in DECISION_POINTS.items():
        value = settings.get(key)
        options = target.DECISIONS[key][0]
        if value in (None, options[0]):
            continue
        query = None
        if words is not None:
            definition = _events_definition(catalogue, rules)
            if definition is None:
                sentence = sentence.split(" Run the name search")[0] + " " + NO_EVENTS
            else:
                sentence = sentence.format(table=definition[0])
                query = {"id": f"search:events-{key}", "sql": target.code_search(catalogue, definition, words)}
                if not any(q["id"] == query["id"] for q in offered["queries"]):
                    offered["queries"].append({"id": query["id"], "sql": query["sql"], "state": "ready", "table": definition[0]})
        elif "{gestation}" in sentence:
            columns = sorted(f"{e.name}.{c.name}" for e in catalogue.tables() for c in e.columns.values() if "GEST" in c.name.upper())
            sentence = sentence.format(gestation=f", which may be {target._join(columns)}" if columns else "")
        row = dict(base, question_id=f"decision-{key}", question=sentence, status="open", blocking="yes", evidence_in_hand="",
                   phase="source", query_state="ready" if query else "", query_reason="", query=query["sql"] if query else "",
                   _queries=[query["id"]] if query else [], _count=None, _top=bool(query), intent="", route="", _fact="",
                   _ask=None)
        rows.append(row)
