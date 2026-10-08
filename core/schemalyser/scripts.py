"""The queries that reach the table of readings, written as short scripts that are safe by construction.

SQL Server does not keep a common table expression once it has worked it out: it writes each one into the query
wherever it is read, and then orders every join of the whole query as it sees fit. A single long query that holds both
the steps that find the cohort and the step that reads the readings therefore leaves SQL Server free to read the
readings before the cohort has been narrowed, and a join on an expression, such as an inner join on a COALESCE of two
outer joined columns, can become a join with no condition at all, a cross product held in tempdb, with the condition
tested only afterwards. That is how an early form of the list of what is charted ran for more than fifteen minutes and
filled tempdb to about 96 GB in a rehearsal: a nested loop with no predicate replayed a spool of the cohort's readings
once for every visit, because the visit was joined on a COALESCE of two outer-joined visits.

Each script here therefore works in two steps that SQL Server cannot merge:

1. The cohort's anaesthetics for the stated period, with the keys of their own records, are selected from the small
   tables into a temporary table, #cohort, which is given a primary key. The steps that find the cohort may join only on
   keys or on lookups of mapping rows; where they join on an expression that hides a key, no script is offered.
2. The readings are reached from #cohort by equality on keys alone: the tables that tie a reading to its anaesthetic are
   joined from the cohort, and the table of readings is joined last, on its own record key.

A script is offered only once the count by year has been seen, and only where the count shows at most LIMIT anaesthetics
of the cohort in the period, so that its first lines can state the most that it reads.
"""
import re

import sqlglot
from sqlglot import exp

from . import checks as checking

# The most anaesthetics of the cohort that a script reaching the readings may start from.
LIMIT = 5000

WORDING = {
    "timeout": "The database analyst sets a time limit before running this script, because SQL Server Management Studio sets "
               "none otherwise: in the Query menu, Query Options, then Execution, the analyst enters a number of seconds in "
               "Execution time-out. The analyst runs part 1 on its own, then select part 2 and press Ctrl+L to see its estimated plan without "
               "running it. If a box that names the table of readings reads Scan, or a box that reads Hash Match has an "
               "arrow coming in from it, the analyst does not run part 2 and shows the plan to the clinician. If the "
               "table of readings appears only in boxes that read Seek, the analyst can run part 2.",
    "temporary": "This script makes one temporary table, #cohort, which is a small table that exists only in the database "
                 "analyst's SQL window and disappears when that window closes. #cohort holds only the identifying numbers of the "
                 "audit's anaesthetics and of their records, with their start and end times. Nothing else is made or changed.",
    "worst": "The count by year shows {worst}, and part 2 asks only for the readings of those anaesthetics.",
    "part1": "Part 1 finds the audit's anaesthetics in the smaller tables and puts them into #cohort, one row for each anaesthetic.",
    "part2": "Part 2 fetches the readings of only the anaesthetics in #cohort.",
    "unseen": "Schemalyser offers this query once the count by year has been seen, because the count shows how many "
              "anaesthetics it would read.",
    "too_many": "The count by year shows that the cohort may hold as many as {n} anaesthetics from {start} to {end}. Schemalyser offers a "
                "script that reads the readings only where the period holds at most {limit} anaesthetics of the cohort, so "
                "please choose a shorter period.",
    "unsafe": "Schemalyser does not offer this query to be run, because {reason}.",
}


class Unsafe(Exception):
    """The query cannot be written as a script that is safe by construction; the message says why, as a clause."""


def _period(start, end):
    """The period in words: "in 2025" for a whole year, otherwise "from START to END"."""
    start, end = str(start), str(end)
    if start[:4] == end[:4] and start[4:] in ("", "-01-01") and end[4:] in ("", "-12-31"):
        return f"in {start[:4]}"
    return f"from {start} to {end}"


def worst_case(count, start, end):
    """(the anaesthetics of the cohort that the count by year shows for the period from start to end, in words, such as
    "about 200 anaesthetics of the cohort in 2025", or None, and the reason in one sentence where no script is offered).
    start and end are ISO dates or years. count is held.count(), whose years are [year, anaesthetics, in_the_cohort, ...]
    rounded down to tens, None meaning under ten. The limit is tested on the most that the rounded counts allow, each
    year's count with ten added."""
    if count is None:
        return None, WORDING["unseen"]
    first, last = int(str(start)[:4]), int(str(end)[:4])
    shown, most = 0, 0
    for item in count["years"]:
        if first <= int(item[0]) <= last:
            shown += int(item[2] or 0)
            most += int(item[2] or 0) + 10    # rounded down to tens, so each year may hold up to ten more
    if most > LIMIT:
        return None, WORDING["too_many"].format(n=f"{most:,}", start=start, end=end, limit=f"{LIMIT:,}")
    number = f"about {shown:,}" if shown >= 10 else "fewer than ten"
    return f"{number} anaesthetics of the cohort {_period(start, end)}", None


def header(worst):
    """The comment lines with which every script begins."""
    from .target import textwrap_lines
    lines = []
    for sentence in (WORDING["timeout"], WORDING["temporary"], WORDING["worst"].format(worst=worst)):
        lines += [f"-- {line}" for line in textwrap_lines(sentence)]
    return "\n".join(lines)


def page(worst):
    """The worst case as the page states it."""
    return worst


# The steps that find the cohort.

def _bare(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def check_cohort(head):
    """Raises Unsafe where the steps that find the cohort join on an expression that hides a key: an expression that reads
    both the table joined and the tables already joined, or, in an inner join, a COALESCE, ISNULL or CASE of the tables
    already joined or a key cast to text. The early list that ran away joined a visit on COALESCE(billed, own) of two outer
    joined visits: SQL Server could test that only after both outer joins, so it joined the visits to the readings with no
    condition at all and held the product in tempdb. A lookup of mapping rows, which is a short list of values, may join on
    a code cast to text, and an outer join to a lookup may compute what it looks up from the tables already joined,
    because it cannot remove a row and SQL Server must join it after them."""
    _, _, ctes = head.partition("\nWITH\n")
    try:
        tree = sqlglot.parse_one("WITH\n" + (ctes or head) + "\nSELECT 1 AS x", dialect="tsql")
    except sqlglot.errors.SqlglotError as error:
        raise Unsafe("Schemalyser could not read the steps that find the cohort") from error
    for join in tree.find_all(exp.Join):
        if join.this.name.lower() == "mapping_rows":
            continue
        inner = (join.side or "").upper() not in ("LEFT", "RIGHT", "FULL")
        joined = join.this.alias_or_name
        on = join.args.get("on")
        for c in on.find_all(exp.EQ) if on is not None else []:
            sides = [_bare(c.this), _bare(c.expression)]
            if not any(joined in {col.table for col in s.find_all(exp.Column)} for s in sides):
                continue    # a condition on the tables already joined, such as a fixed value
            for side in sides:
                tables = {col.table for col in side.find_all(exp.Column)}
                if joined in tables and len(tables) > 1:
                    raise Unsafe("one of the steps that find the cohort joins on an expression that reads both sides of "
                                 f"the join, {c.sql(dialect='tsql')}, which SQL Server cannot use as a key")
                if joined in tables or not tables:
                    continue
                # The side of the tables already joined. An inner join must reach its table by a key; an outer join to a
                # lookup may compute the value that it looks up from the tables already joined.
                if inner and side.find(exp.Coalesce, exp.Case, exp.If) is not None:
                    raise Unsafe("one of the steps that find the cohort joins on an expression of the tables already "
                                 f"joined, {c.sql(dialect='tsql')}, which SQL Server cannot use as a key")
                if inner and isinstance(side, (exp.Cast, exp.TryCast)) and isinstance(_bare(side.this), exp.Column):
                    raise Unsafe("one of the steps that find the cohort joins on a key cast to text, "
                                 f"{c.sql(dialect='tsql')}, which SQL Server cannot use as a key")
    return True


def cohort_statement(head, cohort, columns):
    """The first statement: the cohort, from the steps that find it, into #cohort with a primary key."""
    _, _, ctes = head.partition("\nWITH\n")
    picked = ",\n       ".join(["ISNULL(c.anaesthetic_id, 0) AS anaesthetic_id"]
                               + [f"c.{name}" for name in columns if name != "anaesthetic_id"])
    return ("WITH\n" + ctes.rstrip().rstrip(",") + f"\nSELECT {picked}\nINTO #cohort\nFROM {cohort} AS c;\n"
            "ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_id);")


def split_comments(text):
    """(the comment lines at the head of a composed query, each ending in a new line, the rest)."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].startswith("--") or not lines[i].strip()):
        i += 1
    return "".join(lines[:i]), "".join(lines[i:])


DROP = "IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;"


def assemble(worst, description, first, second):
    """The whole script: the comment lines that advise a time-out and say what it creates and reads, the query's own
    description (comment lines), SET NOCOUNT ON, then part 1, which puts the cohort into #cohort (after removing a #cohort
    left in the same window by an earlier run), and part 2, which reads the readings from it."""
    description = description.strip()
    return (header(worst) + "\n" + (description + "\n" if description else "") + "SET NOCOUNT ON;\n\n"
            + f"-- {WORDING['part1']}\n{DROP}\n" + first.strip() + "\n\n" + f"-- {WORDING['part2']}\n" + second.strip() + "\n")


# The readings, reached from #cohort by key.

def reached(placements, catalogue, reading, node):
    """The table of readings as a derived table that holds only the readings on the records of the anaesthetics in
    #cohort: the tables on the way are joined from #cohort as the step joins them, and the table of readings is joined
    last on the record key by which the step reaches it. placements is [(alias, [conditions])] from the cohort to the
    table of readings, as charted.route gives it."""
    *before, (last, on) = placements
    lines = [f"JOIN {checking._name(catalogue, n.name)} AS {n.alias_or_name}\n    ON "
             + "\n    AND ".join(c.sql(dialect="tsql") for c in conditions) for n, conditions in before]
    keys = []
    for c in on:
        if not isinstance(c, exp.EQ):
            continue
        one, other = _bare(c.this), _bare(c.expression)
        if isinstance(other, exp.Column) and other.table.upper() == reading.upper():
            one, other = other, one
        if isinstance(one, exp.Column) and one.table.upper() == reading.upper() and isinstance(other, exp.Column) \
                and other.table.upper() != reading.upper():
            keys.append((one.name, other))
    if not keys:
        raise Unsafe("the table of readings is not reached by a key of its own")
    picked = ", ".join(f"{other.sql(dialect='tsql')} AS k{i}" for i, (_, other) in enumerate(keys, 1))
    joined = " AND ".join(f"{node.alias_or_name}.{name} = own.k{i}" for i, (name, _) in enumerate(keys, 1))
    inner = f"SELECT DISTINCT {picked}\n    FROM #cohort AS c" + "".join("\n    " + line for line in lines)
    return (f"(\n  SELECT {node.alias_or_name}.*\n  FROM (\n    {inner}\n  ) AS own\n  JOIN {checking._name(catalogue, node.name)} "
            f"AS {node.alias_or_name} WITH (NOLOCK)\n    ON {joined}\n) AS {node.alias_or_name}")


def _blocks(text):
    """The common table expressions of a composed query, as {name: (start, end)} offsets of their bodies in text."""
    found = {}
    for match in re.finditer(r"^(?:WITH )?([A-Za-z0-9_]+) AS \(\n", text, re.M):
        depth, i = 1, match.end()
        while depth and i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        found[match.group(1)] = (match.end(), i - 1)
    return found


def _conditions(select):
    where = select.args.get("where")
    out = set()

    def walk(node):
        node = _bare(node)
        if isinstance(node, exp.And):
            walk(node.this)
            walk(node.expression)
        elif node is not None:
            out.add(node.sql(dialect="tsql", comments=False))
    walk(where.this if where is not None else None)
    return out


def cohort_columns(text, cohort):
    """The output columns of the cohort's common table expression in a composed query, after checking that it reads the
    anaesthetic table alone, as (columns, conditions)."""
    blocks = _blocks(text)
    if cohort not in blocks:
        raise Unsafe("the query has no common table expression for its cohort")
    start, end = blocks[cohort]
    select = sqlglot.parse_one(text[start:end], dialect="tsql")
    source = select.args.get("from_") or select.args.get("from")
    if select.args.get("joins") or source is None or source.this.name.lower() != "omop_anaesthetic":
        raise Unsafe("its cohort is not read from the anaesthetic table alone")
    return [e.alias_or_name for e in select.expressions], _conditions(select)


def rewrite(text, cohort, columns, steps):
    """The composed query with its cohort read from #cohort and the table of readings in each step that reads it replaced
    by the readings reached from #cohort. steps is [(name of the step's common table expression, table name, reached
    derived table)]. Raises Unsafe where another part of the query reads the table of readings."""
    blocks = _blocks(text)
    start, end = blocks[cohort]
    body = "  SELECT\n    " + ",\n    ".join(columns) + "\n  FROM #cohort\n"
    out = text[:start] + body + text[end:]
    # Each table of readings may be read only by the steps that are rewritten, once in each.
    for table in {t for _, t, _ in steps}:
        reads = re.findall(r"\b(?:FROM|JOIN)\s+(?:\[?dbo\]?\.)?\[?" + re.escape(table) + r"\b", out, re.I)
        if len(reads) != sum(1 for _, t, _ in steps if t == table):
            raise Unsafe(f"another part of the query reads {table} without starting from the cohort")
    for name, table, derived in steps:
        blocks = _blocks(out)
        if name not in blocks:
            raise Unsafe("the step that reads the readings is not where Schemalyser expects it")
        start, end = blocks[name]
        part = out[start:end]
        pattern = re.compile(r"FROM\s+(?:\[?dbo\]?\.)?\[?" + re.escape(table) + r"\]?\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)"
                             r"(?:\s+WITH\s*\(NOLOCK\))?", re.I)
        found = pattern.search(part)
        if found is None:
            raise Unsafe("the step that reads the readings does not start from the table of readings")
        part = part[:found.start()] + "FROM " + derived.replace("\n", "\n    ") + part[found.end():]
        out = out[:start] + part + out[end:]
    return out
