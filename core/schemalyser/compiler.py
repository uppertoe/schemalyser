"""Compilation through a hospital schema: a query over the roles compiled with a map into one statement over the hospital's
tables, and an audit compiled into the two-part script of its execution package, with the specification that describes
that script in the source database's terms.

This is layer 3's work, as docs/contract.md places it. A query or an audit is one T-SQL SELECT that reads only the role
views and the mapping views (check_audit). compile_query places each role view of the map, and each mapping view that the
query reads, as a common table expression ahead of the query, so that the analytics team runs one statement. compile_audit
writes the audit as two parts arranged as the series that the policy requires: part 1 puts the cohort of one period
into #cohort, and part 2 reaches every larger table from #cohort by key. script gives the whole text of query.sql, and
specification the specification.md that describes it. The package that carries them is assembled by audit.py, and the
test of the audit on made-up rows is the role shadow's (roleshadow.py), whose report the package reads.
"""
import datetime as dt
import re
import textwrap

import sqlglot
from sqlglot import exp

from . import describe, policy, rolemap
from .blanking import blanking, count_columns

CAP = policy.CAP
# The views of the record from which part 1 may choose the cohort: the small tables of patients and anaesthetics.
COHORT_PARTS = ("role_anaesthetic", "role_patient")
# How each part of the record is reached from #cohort in part 2: (the column that links it, the column of #cohort).
REACHED = {"role_reading": ("anaesthetic_key", "anaesthetic_key"), "role_anaesthetic": ("anaesthetic_key", "anaesthetic_key"),
           "role_patient": ("patient_key", "patient_key")}
SESSION = ("SET NOCOUNT ON;", "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;", "SET LOCK_TIMEOUT 10000;",
           "SET DEADLOCK_PRIORITY LOW;")
DROP = "IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;"
# The comment line that marks each step of the series, exactly as the policy reads it, just before its statement.
SERIES_MARKERS = ("-- series: count", "-- series: coverage", "-- series: rows")

# The wording of query.sql and of the compilation's refusals, in one place.
WORDING = {
    # The comments of query.sql.
    "written": "Schemalyser wrote this script on {date} from the audit {query} and the hospital schema {schema}. The script names the tables and local codes of the hospital's database, so it is for use inside the hospital only.",
    "timeout": "Before running anything, the database analyst sets a time limit: in the Query menu, Query Options, then Execution, the analyst enters a number of seconds in Execution time-out, because SQL Server Management Studio sets none otherwise.",
    "order": "The database analyst runs part 1 on its own first. The analyst then obtains the estimated plan of part 2, as README.md describes, and runs part 2 only once the plan review in the package has passed. A change to any line of this script voids the plan review.",
    "session": "The session settings below read without waiting for locks, give way to any other statement after ten seconds of waiting, and make this script the one that SQL Server ends if two ever block each other. Every table is also read WITH (NOLOCK), which still holds a schema lock, so the script should not run during the nightly load.",
    "series": "The script runs as a series of three steps of increasing size, and the database analyst can stop after any of them: a count of the anaesthetics in #cohort over the period, then how many of them each link reaches, then the rows. Each step follows a line that names it.",
    "count": "The first step counts the anaesthetics in #cohort that started from {start} to {end}.",
    "coverage": "The second step counts how many of the anaesthetics in #cohort have a matching row in {tables}, through which part 2 reaches the larger tables. It reads no large table.",
    "coverage_none": "The second step counts the anaesthetics in #cohort, because part 2 reaches no further table through a link.",
    "unmeasured": "The link of {parts} begins at a large table, so the second step cannot measure it without reading that table, and its coverage is first seen in the rows.",
    "part1": "Part 1 puts into #cohort at most {cap} anaesthetics of the audit's cohort that started from {start} to {end}, the earliest first, from {tables}. #cohort is a temporary table that exists only in the database analyst's SQL window and disappears when that window closes. Nothing else is made or changed.",
    "limit": "If cohort_reached_the_limit reads 1, #cohort holds {cap} anaesthetics and the period may hold more. In that case the database analyst stops here, and the clinician chooses a shorter period and builds the package again.",
    "part2": "Part 2 answers the audit for the anaesthetics in #cohort only. It reaches {tables} from #cohort by their keys, so that it reads only the rows of those anaesthetics.",
    "part2_small": "Part 2 answers the audit for the anaesthetics in #cohort only.",
    "blank": "The result leaves blank any count from 1 to 4, so that no small number can point to a child.",
    "exact": "The audit's approval allows exact small numbers, so the result keeps every count as it is.",
    "end": "The script removes #cohort once part 2 has run.",
    # Messages.
    "no_cohort": "Schemalyser cannot write this audit as a two-part script, because none of its steps chooses its anaesthetics from the patients and the anaesthetics alone.",
    "no_link": "Schemalyser cannot write this audit as a two-part script, because part 2 reads {part}, and #cohort does not carry {column}, by which it would be reached.",
    "unreached": "Schemalyser cannot write this audit as a two-part script, because the hospital schema does not say how {part} is linked to an anaesthetic.",
}


class AuditError(ValueError):
    """The package cannot be written; the message says why in one sentence."""


def _wrapped(sentence):
    return "\n".join(f"-- {line}" for line in textwrap.wrap(sentence, 110))


def _and(items):
    return describe._and(list(items))


# Compiling a query over the roles through a map, as one statement.

def check_audit(sql, where="audit"):
    """Raises MapError unless sql is one SELECT that reads only the role views and its own common table expressions."""
    tree = rolemap._single_select(sql, where)
    named = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog or table.name.lower() not in set(rolemap.public_views()) | named:
            raise rolemap.MapError(rolemap.WORDING["audit_reads"].format(where=where, table=table.sql(dialect="tsql")))
    return tree


def _with_token(sql):
    """The text of a query split at its first WITH, as (what comes before, what follows WITH), or None without one."""
    for token in sqlglot.tokenize(sql, dialect="tsql"):
        if token.text.upper() == "WITH" and token.token_type.name == "WITH":
            return sql[:token.start], sql[token.end + 1:]
        if token.token_type.name == "SELECT":
            return None
    return None


def _header(sql):
    """The comment lines at the head of a query, and the rest."""
    lines = sql.splitlines()
    count = 0
    while count < len(lines) and (lines[count].strip().startswith("--") or not lines[count].strip()):
        count += 1
    return "\n".join(lines[:count]).rstrip(), "\n".join(lines[count:])


def _final_select_at(sql):
    """Where the final SELECT of a query that begins with common table expressions starts in its text, or None."""
    depth, seen_with = 0, False
    for token in sqlglot.tokenize(sql, dialect="tsql"):
        name = token.token_type.name
        if name == "WITH" and depth == 0:
            seen_with = True
        elif name == "L_PAREN":
            depth += 1
        elif name == "R_PAREN":
            depth -= 1
        elif name == "SELECT" and depth == 0 and seen_with:
            return token.start
    return None


def _blanked_query(body, tree):
    """The audit with its final SELECT read as result by an outer SELECT that leaves blank any count from 1 to 4, using
    the project's own rule for which columns are counts. The audit's common table expressions keep their own text."""
    counts = count_columns(tree)
    at = _final_select_at(body)
    if not counts or at is None:
        return body
    final = tree.copy()
    final.set("with_" if "with_" in final.arg_types else "with", None)
    inner, outer = blanking(final, counts)
    indented = "\n".join("    " + line for line in inner.splitlines())
    return body[:at].rstrip() + ",\nresult AS (\n" + indented + "\n)\n" + rolemap.WORDING["blank"] + "\n" + outer


def compile_query(sql, roles_map, blank=False, nolock=True):
    """One T-SQL statement: the role views of a map as common table expressions, then a query over the role views.

    sql is an audit or a count, one SELECT that reads only the role views. With blank, the final SELECT leaves blank
    any count from 1 to 4, as the project's generated queries do. The audit's own header comment is kept at the top.
    """
    tree = check_audit(sql)
    header, body = _header(sql)
    if blank:
        body = _blanked_query(body, tree)
    data = roles_map["data"]
    # The three views that every map supplies come always, and a further view only where the query reads it.
    read = {table.name.lower() for table in tree.find_all(exp.Table)}
    chosen = [view for view in rolemap.all_views() if view in rolemap.views() or view in read]
    missing = [view for view in chosen if view not in roles_map["views"]]
    if missing:
        raise rolemap.MapError(rolemap.WORDING["map_shape"].format(where="the query", problem=f"it reads {', '.join(missing)}, which the map does not supply"))
    parts = []
    for view in chosen:
        text = rolemap.view_sql(roles_map["views"][view], nolock)
        indented = "\n".join("  " + line for line in text.splitlines())
        parts.append((rolemap.WORDING["view"].format(view=view, says=data["roles"][view]["rows"]["says"]), f"{view} AS (\n{indented}\n)"))
    for view in [name for name in rolemap.mapping_views() if name in read]:
        text = roles_map["views"].get(view) or rolemap.mapping_sql(view, rolemap.mapping_rows(data, view))
        indented = "\n".join("  " + line for line in text.splitlines())
        parts.append((rolemap.WORDING["mapping"].format(view=view), f"{view} AS (\n{indented}\n)"))
    ctes = parts[0][0] + "\nWITH " + parts[0][1] + "".join(f",\n{comment}\n{text}" for comment, text in parts[1:])
    split = _with_token(body)
    head = [line for line in (header, rolemap.WORDING["compiled"].format(world=data["world"]), rolemap.WORDING["names"]) if line]
    if split is None:
        return "\n".join(head) + "\n" + ctes + "\n" + body.strip() + "\n"
    before, after = split
    return "\n".join(head) + "\n" + before.strip() + ("\n" if before.strip() else "") + ctes + ",\n" + after.strip() + "\n"


# The schema's tables.

def schema_tables(schema):
    """{part of the record: [tables its SQL reads]} for every part that the hospital schema supplies, from its files."""
    found = {}
    for path, data in sorted(schema.files.items()):
        match = re.fullmatch(r"map/(role_\w+)\.sql", path)
        if not match:
            continue
        try:
            tree = sqlglot.parse_one(describe._text(data), dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        found[match.group(1)] = list(dict.fromkeys(t.name for t in tree.find_all(exp.Table)))
    return found


def _view_sql(schema, view):
    return rolemap.view_sql(schema.sitting.view_sql(view))


def _columns_from(schema, view):
    """{column of the part: the expression over the hospital's tables that gives it}, from the part's SQL."""
    tree = sqlglot.parse_one(schema.sitting.view_sql(view), dialect="tsql")
    aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    out = {}
    for projection in tree.expressions:
        value = projection.unalias().copy()
        for column in value.find_all(exp.Column):
            if column.table in aliases:
                column.set("table", exp.to_identifier(aliases[column.table]))
        out[projection.alias_or_name] = value.sql(dialect="tsql")
    return out


# Compiling the audit into two parts.

def _reads(node, names):
    return {t.name.lower() for t in node.find_all(exp.Table) if not t.db and t.name.lower() in names}


def _cohort_cte(tree):
    for cte in tree.find_all(exp.CTE):
        reads = {t.name.lower() for t in cte.this.find_all(exp.Table)}
        if "role_anaesthetic" in reads and reads <= set(COHORT_PARTS) and \
                "anaesthetic_key" in {n.lower() for n in cte.this.named_selects}:
            return cte
    raise AuditError(WORDING["no_cohort"])


def _period(start, end):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first > last:
        raise ValueError
    return first, last


def _reached_cte(schema, view, cohort_columns):
    link, cohort_column = REACHED[view]
    if cohort_column not in cohort_columns:
        raise AuditError(WORDING["no_link"].format(part=rolemap.view_title(view, False), column=cohort_column))
    role = schema.sitting.data["roles"].get(view) or {}
    if not ((role.get("columns") or {}).get(link) or {}).get("binding"):
        raise AuditError(WORDING["unreached"].format(part=rolemap.view_title(view, False)))
    columns = [c for c in rolemap.views()[view] if c != link]
    lines, expressions, order, where = schema.sitting._reached(view, link, cohort_column, columns)
    text = ("SELECT " + ",\n       ".join([f"c.{cohort_column} AS {link}"] + [f"{expressions[c]} AS {c}" for c in columns])
            + "\n" + "\n".join(lines + describe._where(where)))
    return text, order


def _large(schema, large):
    sizes = schema.sitting.sizes

    def is_large(table):
        rows = sizes.get(table.upper())
        return rows is None or rows >= large
    return is_large


def _coverage(schema, views, cohort_columns, is_large):
    """The second step of the series: for each part that part 2 reaches from #cohort, how many of the cohort's
    anaesthetics have a matching row through the part's link, following the link only as far as its last table that is
    not large, so that the step reads no large table. Returns (statement, tables read, [parts whose link begins at a
    large table and so cannot be measured before the rows are read])."""
    ctes, joins, counts, tables, unmeasured = [], [], ["COUNT(*) AS cohort_anaesthetics"], [], []
    for n, view in enumerate(views, 1):
        link, cohort_column = REACHED[view]
        if cohort_column not in cohort_columns:
            continue
        lines, _, order, _ = schema.sitting._reached(view, link, cohort_column, [])
        small = 0
        while small < len(order) and not is_large(order[small]):
            small += 1
        if small == 0:
            unmeasured.append(view)
            continue
        tables += order[:small]
        name = f"reach_{view[len('role_'):]}"
        body = "\n".join(lines[:small + 1])
        ctes.append(f"{name} AS (\n  SELECT DISTINCT c.{cohort_column} AS {cohort_column}\n"
                    + "\n".join("  " + line for line in body.splitlines()) + "\n)")
        joins.append(f"LEFT JOIN {name} AS r{n} ON r{n}.{cohort_column} = c.{cohort_column}")
        counts.append(f"COUNT(r{n}.{cohort_column}) AS cohort_reaching_{view[len('role_'):]}")
    text = (("WITH " + ",\n".join(ctes) + "\n") if ctes else "") + "SELECT " + ",\n       ".join(counts) + \
        "\nFROM   #cohort AS c" + "".join("\n" + j for j in joins) + ";"
    return text, list(dict.fromkeys(tables)), unmeasured


def compile_audit(schema, audit_sql, start, end, blank=True, final=None, large=policy.LARGE):
    """The audit as a two-part script over the hospital's tables, arranged as the series that the policy requires: a
    count of the cohort over the period, the coverage of each link from #cohort, then the rows. final, when given,
    replaces the audit's final SELECT (for a rehearsal that reads one of its steps). Returns {"count", "part1",
    "coverage", "part2", "cohort_tables", "coverage_tables", "unmeasured", "part2_tables", "cohort_columns",
    "cohort_conditions", "counts", "columns"}."""
    tree = check_audit(audit_sql).copy()
    # The counts are read from the audit as written, because part 2 reads the cohort's columns from #cohort, through
    # which the rule for what is a count cannot see.
    counts = count_columns(tree) if final is None else []
    cohort = _cohort_cte(tree)
    name = cohort.alias
    body = cohort.this.copy()
    alias = next(t.alias_or_name for t in body.find_all(exp.Table) if t.name.lower() == "role_anaesthetic")
    after = end + dt.timedelta(days=1)
    body.where(f"{alias}.start_time >= CAST('{start.isoformat()}' AS datetime) AND "
               f"{alias}.start_time < CAST('{after.isoformat()}' AS datetime)", dialect="tsql", copy=False)
    if "start_time" not in [c.lower() for c in body.named_selects]:
        # The count of the series bounds #cohort by the period, so #cohort carries the start of each anaesthetic.
        body.select(f"{alias}.start_time", dialect="tsql", copy=False)
    columns = list(body.named_selects)
    views_one = [v for v in COHORT_PARTS[::-1] if v in _reads(body, set(COHORT_PARTS))]
    tables = schema_tables(schema)
    cohort_tables = list(dict.fromkeys(t for v in views_one for t in tables.get(v, [])))
    ctes = ",\n".join(f"{v} AS (\n" + "\n".join("  " + line for line in _view_sql(schema, v).splitlines()) + "\n)"
                      for v in views_one)
    picked = ",\n       ".join([f"ISNULL(k.anaesthetic_key, 0) AS anaesthetic_key"]
                               + [f"k.{c}" for c in columns if c.lower() != "anaesthetic_key"])
    ordered = "k.start_time, k.anaesthetic_key" if "start_time" in [c.lower() for c in columns] else "k.anaesthetic_key"
    inner = "\n".join("  " + line for line in body.sql(dialect="tsql", pretty=True).splitlines())
    # The first step of the series: the anaesthetics of #cohort counted over the period, before any link is followed.
    count = (f"SELECT COUNT(*) AS anaesthetics_in_period\nFROM   #cohort AS c\n"
             f"WHERE  c.start_time >= CAST('{start.isoformat()}' AS datetime)\n"
             f"  AND  c.start_time < CAST('{after.isoformat()}' AS datetime);")
    part1 = (f"WITH {ctes}\nSELECT TOP ({CAP}) {picked}\nINTO   #cohort\nFROM   (\n{inner}\n) AS k\n"
             f"WHERE  k.anaesthetic_key IS NOT NULL\nORDER  BY {ordered};\n"
             "ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);\n"
             f"SELECT CASE WHEN COUNT(*) >= {CAP} THEN 1 ELSE 0 END AS cohort_reached_the_limit FROM #cohort;")
    # Part 2: the cohort's step reads #cohort, and every part still read is reached from #cohort by key.
    cohort.set("this", sqlglot.parse_one("SELECT " + ", ".join(f"c.{c}" for c in columns) + " FROM #cohort AS c",
                                         dialect="tsql"))
    if final is not None:
        replacement = sqlglot.parse_one(final, dialect="tsql")
        replacement.set("with_" if "with_" in replacement.arg_types else "with", tree.args.get("with_") or tree.args.get("with"))
        tree = replacement
    still = [v for v in rolemap.all_views() if v in _reads(tree, set(rolemap.all_views()))]
    added, part2_tables = [], []
    for view in still:
        if view not in REACHED:
            raise AuditError(WORDING["unreached"].format(part=rolemap.view_title(view, False)))
        text, order = _reached_cte(schema, view, [c.lower() for c in columns])
        added.append(exp.CTE(this=sqlglot.parse_one(text, dialect="tsql"), alias=exp.TableAlias(this=exp.to_identifier(view))))
        part2_tables += order
    with_ = tree.args.get("with_") or tree.args.get("with")
    with_.set("expressions", added + list(with_.expressions))
    text = tree.sql(dialect="tsql", pretty=True)
    if blank and counts:
        text = _blanked_part2(tree, counts)
    part2 = text.replace("   WITH (NOLOCK)", " WITH (NOLOCK)").rstrip().rstrip(";") + ";"
    coverage, coverage_tables, unmeasured = _coverage(schema, [v for v in still if v in REACHED],
                                                      [c.lower() for c in columns], _large(schema, large))
    return {"count": count, "part1": part1, "coverage": coverage, "coverage_tables": coverage_tables,
            "unmeasured": unmeasured, "part2": part2, "cohort_name": name, "cohort_tables": cohort_tables,
            "part2_tables": list(dict.fromkeys(part2_tables)), "cohort_columns": columns,
            "cohort_conditions": [c.sql(dialect="tsql") for c in policy._conjuncts(body.args["where"].this)],
            "cohort_views": views_one, "part2_views": still, "counts": counts,
            "columns": list(tree.named_selects)}


def _blanked_part2(tree, counts):
    """The query with its final SELECT read as result by an outer SELECT that leaves blank any count from 1 to 4."""
    key = "with_" if "with_" in tree.arg_types else "with"
    final = tree.copy()
    final.set(key, None)
    inner, outer = blanking(final, counts)
    parts = [cte.sql(dialect="tsql", pretty=True) for cte in tree.args[key].expressions]
    parts.append("result AS (\n" + "\n".join("  " + line for line in inner.splitlines()) + "\n)")
    return "WITH " + ",\n".join(parts) + "\n" + outer


def script(compiled, meta, blank=True):
    """The whole text of query.sql."""
    lines = []
    if meta.get("question"):
        lines.append(_wrapped(meta["question"]))
    lines += [_wrapped(WORDING["written"].format(**meta)), _wrapped(WORDING["timeout"]), _wrapped(WORDING["order"]),
              _wrapped(WORDING["session"])]
    lines += list(SESSION)
    lines += ["", _wrapped(WORDING["series"]), "",
              _wrapped(WORDING["part1"].format(cap=f"{CAP:,}", start=meta["start"], end=meta["end"],
                                               tables=_and(compiled["cohort_tables"]))),
              _wrapped(WORDING["limit"].format(cap=f"{CAP:,}")), DROP, compiled["part1"], "",
              _wrapped(WORDING["count"].format(start=meta["start"], end=meta["end"])), SERIES_MARKERS[0],
              compiled["count"], ""]
    if compiled["coverage_tables"]:
        lines.append(_wrapped(WORDING["coverage"].format(tables=_and(compiled["coverage_tables"]))))
    else:
        lines.append(_wrapped(WORDING["coverage_none"]))
    if compiled["unmeasured"]:
        lines.append(_wrapped(WORDING["unmeasured"].format(parts=_and(rolemap.view_title(v, False) for v in compiled["unmeasured"]))))
    lines += [SERIES_MARKERS[1], compiled["coverage"], ""]
    lines.append(_wrapped(WORDING["part2"].format(tables=_and(compiled["part2_tables"])) if compiled["part2_tables"]
                          else WORDING["part2_small"]))
    lines.append(_wrapped(WORDING["blank"] if blank else WORDING["exact"]))
    lines += [SERIES_MARKERS[2], compiled["part2"], "", _wrapped(WORDING["end"]), "DROP TABLE #cohort;"]
    return "\n".join(lines) + "\n"


# The specification.

SPEC_WORDING = {
    "title": "# Specification of the audit script",
    "inside": "This specification names the tables and local codes of the hospital's database, so it is for use inside the hospital only.",
    "question": "The audit asks: {question}",
    "h_reads": "## What the script reads",
    "reads1": "Part 1 reads {tables}.",
    "reads2": "Part 2 reaches {tables} from #cohort by their keys.",
    "sizes": "The tables and columns query gave these sizes, and the policy treats a table of {large} rows or more, or one whose size is not known, as large:",
    "size": "{table} holds {rows}.",
    "size_unknown": "{table} has no recorded size, so the policy treats it as large.",
    "h_parts": "## Where each part of the record comes from",
    "part": "The part of the record for {title} comes from {tables}, where its columns are as follows.",
    "column": "{column} is {expression}.",
    "h_cohort": "## The cohort",
    "cohort": "Part 1 keeps the anaesthetics that the audit's step {name} chooses, and that started within the period. It keeps at most {cap}, the earliest first. The step's conditions, over the parts of the record listed above, are these:",
    "h_period": "## The period",
    "period": "The period runs from {start} to {end}, both days included, and an anaesthetic belongs to it when it starts within it. The script compares the start with {start} and with the day after {end}.",
    "h_decisions": "## The decisions",
    "decision": "{about}: {decision}",
    "decision_by": "{about}: {decision} ({by}, {date})",
    "no_decisions": "No decision was given with the audit.",
    "h_counted": "## What is counted, and how it is rounded",
    "columns": "The result has the columns {columns}, in that order.",
    "counts": "The columns {columns} are counts.",
    "blank": "The result leaves blank any count from 1 to 4, so that no small number can point to a child. The counts are otherwise exact, and the audit's approval would have to allow exact small numbers for them to be kept.",
    "exact": "The audit's approval allows exact small numbers, so the result keeps every count as it is.",
    "limit": "Part 1 also returns cohort_reached_the_limit, which is 1 when #cohort holds {cap} anaesthetics and 0 otherwise, and no other figure.",
}


def specification(schema, compiled, meta, decisions, blank):
    w = SPEC_WORDING
    lines = [w["title"], "", w["inside"], ""]
    if meta.get("question"):
        lines += [w["question"].format(question=meta["question"]), ""]
    sizes = schema.sitting.sizes
    lines += [w["h_reads"], "", w["reads1"].format(tables=_and(compiled["cohort_tables"]))]
    if compiled["part2_tables"]:
        lines.append(w["reads2"].format(tables=_and(compiled["part2_tables"])))
    lines += ["", w["sizes"].format(large=f"{meta['large']:,}"), ""]
    for table in dict.fromkeys(compiled["cohort_tables"] + compiled["part2_tables"]):
        rows = sizes.get(table.upper())
        lines.append("- " + (w["size"].format(table=table, rows=f"{rows:,} rows") if rows is not None
                             else w["size_unknown"].format(table=table)))
    lines += ["", w["h_parts"], ""]
    tables = schema_tables(schema)
    for view in dict.fromkeys(compiled["cohort_views"] + compiled["part2_views"]):
        lines += [w["part"].format(title=rolemap.view_title(view, False), tables=_and(tables.get(view, []))), ""]
        for column, expression in _columns_from(schema, view).items():
            lines.append(f"- {w['column'].format(column=column, expression='`' + expression + '`')}")
        lines.append("")
    lines += [w["h_cohort"], "", w["cohort"].format(name=compiled["cohort_name"], cap=f"{CAP:,}"), ""]
    lines += [f"- `{c}`" for c in compiled["cohort_conditions"]]
    lines += ["", w["h_period"], "", w["period"].format(start=meta["start"], end=meta["end"]), "", w["h_decisions"], ""]
    if decisions.get("decisions"):
        for item in decisions["decisions"]:
            key = "decision_by" if item.get("by") and item.get("date") else "decision"
            lines.append("- " + w[key].format(about=item.get("about", ""), decision=item.get("decision", ""),
                                              by=item.get("by", ""), date=item.get("date", "")))
    else:
        lines.append(w["no_decisions"])
    lines += ["", w["h_counted"], "", w["columns"].format(columns=_and(compiled["columns"]))]
    if compiled["counts"]:
        lines.append(w["counts"].format(columns=_and(compiled["counts"])))
    lines += [w["blank"] if blank else w["exact"], w["limit"].format(cap=f"{CAP:,}")]
    return "\n".join(lines) + "\n"
