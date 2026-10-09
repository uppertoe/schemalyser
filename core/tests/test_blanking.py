"""The blanking of small counts in a result (blanking.py), which the compiled audit and the query compiled through a map
both use. Carried from the earlier design's tests of the target query (B8)."""
import sqlglot

from schemalyser import rolemap
from schemalyser.blanking import blanking, count_columns

AUDIT = rolemap.AUDIT.read_text(encoding="utf-8")


def test_the_counts_of_a_result_are_found_from_the_query_and_small_ones_are_blanked():
    tree = sqlglot.parse_one(AUDIT, dialect="tsql")
    assert count_columns(tree) == ["anaesthetics", "died_within_90_days", "children", "children_died_within_90_days"]
    assert count_columns(sqlglot.parse_one("SELECT a.x, SUM(a.minutes) AS total FROM t a GROUP BY a.x", dialect="tsql")) == []
    final = tree.copy()
    final.set("with_" if "with_" in final.arg_types else "with", None)
    inner, outer = blanking(final)
    assert "CASE WHEN r.children BETWEEN 1 AND 4 THEN NULL ELSE r.children END AS children" in outer
    assert "r.minutes_below_40," in outer and outer.endswith("ORDER BY\n  r.result_order")
    assert "ROW_NUMBER() OVER (ORDER BY" in inner and "AS result_order" in inner
    # A query with no count is left as it is.
    plain = sqlglot.parse_one("SELECT a.x FROM t a", dialect="tsql")
    assert blanking(plain) == ("SELECT\n  a.x\nFROM t AS a", "")
