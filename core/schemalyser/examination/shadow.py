"""Stage 2 of the examination, synthetic execution: the package run on the role-level shadow with every part filled
from the contract, at more than one seed and in more than one insertion order.

The stage refuses a package whose question does not run, returns columns other than those it declares, gives a
different answer when the same rows are inserted in another order, which is what a window without a total order or a
choice among ties does, or returns nothing where the generator guarantees anaesthetics that qualify.
"""
from .. import equivalence, roleshadow

STAGE = "shadow"
SEEDS = (1, 2)
ORDERS = ("given", "shuffled")
ANAESTHETICS = 120


def run_sql(sql, declared=None, seeds=SEEDS, anaesthetics=ANAESTHETICS):
    """The SQL run on the shadow at each seed in each insertion order. Returns the stage's report: each run's seed,
    order, columns and row count, the outcome and a sentence."""
    runs, problems = [], []
    for seed in seeds:
        answers = {}
        for order in ORDERS:
            con = roleshadow.role_shadow(seed=seed, anaesthetics=anaesthetics, with_planted=True, further=True,
                                         shuffle=None if order == "given" else seed * 31 + 7)
            try:
                columns, rows = roleshadow.duckdb_runner(con)(sql)
            except Exception as error:  # noqa: BLE001 - a question that does not run on the shadow fails the stage
                problems.append(f"the question did not run at seed {seed} in the {order} order: {type(error).__name__}")
                runs.append({"seed": seed, "order": order, "ran": False})
                continue
            finally:
                con.close()
            answers[order] = (list(columns), rows)
            runs.append({"seed": seed, "order": order, "ran": True, "columns": list(columns), "rows": len(rows)})
            if declared and [c.lower() for c in columns] != [c.lower() for c in declared]:
                problems.append(f"at seed {seed} the question returns the columns {', '.join(columns)}, and the package declares {', '.join(declared)}")
            if not rows or all(v is None or v == 0 for row in rows for v in row):
                problems.append(f"at seed {seed} in the {order} order the question returns nothing, no row or only empty or zero values, "
                                "although the generator writes anaesthetics of every kind the contract describes")
        if len(answers) == len(ORDERS):
            found = equivalence.compare_answers(*(_normal(*answers[o]) for o in ORDERS))
            if not found["same"]:
                problems.append(f"at seed {seed} the answer differs between insertion orders ({found['rows_shared']} of {found['rows_a']} rows the same), "
                                "so the question depends on the order of the rows, as a window without a total order does")
    problems = list(dict.fromkeys(problems))
    outcome = "passed" if not problems else "failed"
    says = (f"The question runs on the shadow at {len(seeds)} seeds in {len(ORDERS)} insertion orders and gives one answer at each seed."
            if not problems else "The question fails synthetic execution: " + "; ".join(problems) + ".")
    return {"stage": STAGE, "runs": runs, "problems": problems, "outcome": outcome, "says": says}


def run(path, admit=None, seeds=SEEDS):
    """The stage over a package file, whose SQL is read with its capabilities composed and whose declared result
    columns, where the examinable gives them, are the columns expected."""
    from .. import question
    sql, _, package = question.sql_and_decisions(path)
    declared = ((package.get("examinable") or {}).get("result") or {}).get("columns")
    return run_sql(sql, declared, seeds)


def _normal(columns, rows):
    from .planted import comparable
    return columns, [[comparable(v) for v in row] for row in rows]
