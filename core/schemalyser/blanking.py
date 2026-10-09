"""The blanking of small counts in a result, so that a result is safe by default.

count_columns finds the output columns of a SELECT that are counts, and blanking reads the SELECT as result inside an
outer SELECT that leaves blank any count from 1 to 4. The compiled audit (audit.py) and the compiled query through a
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


def blanking(select, counts=None):
    """A SELECT made safe by default, as (the inner SELECT, the outer SELECT), both as T-SQL text.

    The inner SELECT is the query itself without its ORDER BY, with a column that keeps its order. The outer
    SELECT reads it as result and leaves blank any count from 1 to 4. Where the query has no count, the outer
    SELECT is "" and nothing changes.
    """
    counts = count_columns(select) if counts is None else counts
    if not counts:
        return select.sql(dialect="tsql", pretty=True), ""
    tree = select.copy()
    order = tree.args.get("order")
    tree.set("order", None)
    if order is not None:
        window = exp.Window(this=exp.RowNumber(), order=order.copy())
        tree.expressions.append(exp.alias_(window, "result_order"))
    names = [p.alias_or_name for p in select.expressions]
    shown = [(f"CASE WHEN r.{name} BETWEEN 1 AND 4 THEN NULL ELSE r.{name} END AS {name}" if name in counts else f"r.{name}")
             for name in names]
    outer = "SELECT\n  " + ",\n  ".join(shown) + "\nFROM result AS r" + ("\nORDER BY\n  r.result_order" if order is not None else "")
    return tree.sql(dialect="tsql", pretty=True), outer
