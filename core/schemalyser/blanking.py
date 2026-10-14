"""The blanking of small counts in a result, so that a result is safe by default.

count_columns finds the output columns of a SELECT that are counts, statistic_columns the medians and other order
statistics over a group, and blanking reads the SELECT as result inside an outer SELECT that leaves blank any count
from 1 to 4 and any order statistic over a group smaller than SMALLEST_GROUP. The compiled audit (audit.py) and the compiled query through a
map (rolemap.py) both use them. Nothing here knows of any layer: it reads and writes T-SQL alone.
"""
from sqlglot import exp


def count_columns(select):
    """The output columns of a SELECT that are counts: a COUNT, or a SUM of an indicator that is only 0 or 1,
    looking through COALESCE and through a column of a common table expression or a derived table to its CASE."""
    root = select if isinstance(select, exp.Select) else select.find(exp.Select)
    derived = {cte.alias.upper(): cte.this for cte in select.find_all(exp.CTE)}
    for sub in select.find_all(exp.Subquery):
        if sub.alias:
            derived[sub.alias.upper()] = sub.this
    tables = {t.alias_or_name.upper(): t.name.upper() for t in select.find_all(exp.Table)}

    def indicator(node, depth=0):
        if depth > 5:
            return False
        if isinstance(node, exp.Literal):
            return node.this in ("0", "1")
        if isinstance(node, exp.Case):
            branches = [i.args.get("true") for i in node.args.get("ifs") or []] + [node.args.get("default")]
            return all(b is not None and indicator(b, depth + 1) for b in branches)
        if isinstance(node, exp.Column):
            name = tables.get(node.table.upper(), node.table.upper())
            source = derived.get(name)
            source = source if isinstance(source, exp.Select) or source is None else source.find(exp.Select)
            projection = next((p for p in (source.expressions if source is not None else [])
                               if p.alias_or_name.upper() == node.name.upper()), None)
            return projection is not None and indicator(projection.unalias(), depth + 1)
        return False

    found = []
    for projection in root.expressions:
        value = projection.unalias()
        while isinstance(value, exp.Coalesce):
            value = value.this
        if isinstance(value, exp.Count) or (isinstance(value, exp.Sum) and indicator(value.this)):
            found.append(projection.alias_or_name)
    return found


# The smallest group a median or any other order statistic may be reported over: below it, the statistic is a small
# cell, as a count from 1 to 4 is, because a median of a handful of patients is as telling as their count.
SMALLEST_GROUP = 5


def statistic_columns(select):
    """The output columns of a SELECT that are order statistics over a group: a PERCENTILE_CONT, the median, with the
    window it is taken over, as {column: the window's PARTITION BY expressions as T-SQL, or ""}."""
    root = select if isinstance(select, exp.Select) else select.find(exp.Select)
    found = {}
    for projection in root.expressions:
        window = next((w for w in projection.find_all(exp.Window) if w.find(exp.PercentileCont) is not None), None)
        if window is not None:
            partition = window.args.get("partition_by") or []
            found[projection.alias_or_name] = ", ".join(e.sql(dialect="tsql") for e in partition)
    return found


def small_cell_columns(select):
    """The output columns that the blanking reads: the counts, and the order statistics over a group."""
    return list(count_columns(select)) + list(statistic_columns(select))


def blanking(select, counts=None):
    """A SELECT made safe by default, as (the inner SELECT, the outer SELECT), both as T-SQL text.

    The inner SELECT is the query itself without its ORDER BY, with a column that keeps its order and, for each order
    statistic over a group, the size of that group. The outer SELECT reads it as result and leaves blank any count
    from 1 to 4 and any order statistic over a group smaller than SMALLEST_GROUP. Where the query has neither, the
    outer SELECT is "" and nothing changes.
    """
    counts = count_columns(select) if counts is None else counts
    statistics = statistic_columns(select)
    if not counts and not statistics:
        return select.sql(dialect="tsql", pretty=True), ""
    tree = select.copy()
    order = tree.args.get("order")
    tree.set("order", None)
    if order is not None:
        window = exp.Window(this=exp.RowNumber(), order=order.copy())
        tree.expressions.append(exp.alias_(window, "result_order"))
    for name, partition in statistics.items():
        tree.expressions.append(exp.alias_(exp.maybe_parse(f"COUNT(*) OVER ({'PARTITION BY ' + partition if partition else ''})", dialect="tsql"),
                                           f"{name}_group"))
    names = [p.alias_or_name for p in select.expressions]
    shown = [(f"CASE WHEN r.{name} BETWEEN 1 AND 4 THEN NULL ELSE r.{name} END AS {name}" if name in counts else
              f"CASE WHEN r.{name}_group < {SMALLEST_GROUP} THEN NULL ELSE r.{name} END AS {name}" if name in statistics else f"r.{name}")
             for name in names]
    outer = "SELECT\n  " + ",\n  ".join(shown) + "\nFROM result AS r" + ("\nORDER BY\n  r.result_order" if order is not None else "")
    return tree.sql(dialect="tsql", pretty=True), outer
