"""Runs the whole pipeline over a "world": a catalogue, some requests and a stand-in database.

A world is a folder holding

    catalogue.csv        the tables and columns, in the layout of the catalogue query
    requests/            the .sql files to analyse
    site-rules.json      optional
    design.sql           optional DuckDB statements that write designed values into the stand-in database
    planted-values.txt   optional strings that must never appear in any output

The harness plays every role in turn. It analyses the requests, plans the checks, builds a
stand-in database and answers the checks from it, analyses again with those answers, builds the
sandbox and runs the requests in it. It prints a scorecard, and writes it to scorecard.txt in the
world, so that a change to the tool can be judged by what it does to the numbers.

    python -m schemalyser.harness WORLD [--rows 600] [--dialect tsql] [--spans] [--fanout]

With --spans the checks include the spans between pairs of date columns, which the check script
leaves out by default. With --fanout they include the fanout checks, which count how many rows hold
each key value of a column that refers to another table, and the stand-in database is given a skewed
design, DESIGNED_FANOUT, for every such join from a column that does not key its own table: most
parents have one child and a few have many. The scorecard then gives, for each join, the
distribution that the check results asked for and the one that the sandbox built.
"""
import argparse
import csv
import io

import duckdb
import zipfile
from collections import Counter
from pathlib import Path

from . import Analysis
from . import vocabulary as v
from .catalogue import Catalogue
from .checks import FANOUT_LABELS, KINDS, LAYOUT, MAXIMUM_DEFINITIONS, MAXIMUM_VALUES, MINIMUM_COUNT, Check
from .extract import decode
from .sandbox import Sandbox
from .translate import Unreadable, Unsupported, to_duckdb

LIMITS = {"@minimum_count": MINIMUM_COUNT, "@maximum_values": MAXIMUM_VALUES,
          "@maximum_definitions": MAXIMUM_DEFINITIONS}
# The skewed design of the stand-in database with --fanout: the share of parents in each band of child rows.
DESIGNED_FANOUT = (("1 row", 70), ("2 rows", 18), ("3 to 5 rows", 9), ("6 to 10 rows", 3))


class World:
    def __init__(self, catalogue, requests, rules=None, design=None, planted=None, dialect="tsql"):
        self.catalogue_path, self.requests_path = Path(catalogue), Path(requests)
        self.rules_path = Path(rules) if rules else None
        self.design_path = Path(design) if design else None
        self.planted_path = Path(planted) if planted else None
        self.dialect = dialect

    @classmethod
    def from_folder(cls, folder, dialect="tsql"):
        folder = Path(folder)
        optional = lambda name: folder / name if (folder / name).exists() else None  # noqa: E731
        return cls(folder / "catalogue.csv", folder / "requests", optional("site-rules.json"),
                   optional("design.sql"), optional("planted-values.txt"), dialect)

    def catalogue_text(self):
        return decode(self.catalogue_path.read_bytes())

    def request_files(self):
        return sorted(self.requests_path.rglob("*.sql"))

    def planted(self):
        if not self.planted_path:
            return []
        return [line for line in self.planted_path.read_text().splitlines() if line.strip()]

    def analysis(self, checks_csv=None):
        result = Analysis(self.catalogue_text(), self.rules_path.read_text() if self.rules_path else None,
                          dialect=self.dialect, checks_csv=checks_csv)
        for path in self.request_files():
            result.add_request(path.relative_to(self.requests_path).as_posix(), decode(path.read_bytes()))
        return result

    def truth(self, analysis, rows=600, fanout=False):
        """The stand-in database: the generator's tables, with the designed values written over them.

        With fanout, every join that a fanout check counts, from a column that does not key its own table,
        is given the skewed design DESIGNED_FANOUT.
        """
        designed = {}
        if fanout:
            catalogue = analysis.catalogue
            for check in analysis.planned_checks(include_fanout=True):
                first = catalogue.table(check.table).first_column() if check.kind == "fanout" else None
                if first is not None and not (first.name == check.column and first.nullable is False):
                    designed[(check.table, check.column, *check.parent)] = list(DESIGNED_FANOUT)
        sandbox = Sandbox(Catalogue.from_csv(self.catalogue_text()), inventory_zip(analysis), designed)
        sandbox.build(rows)
        if self.design_path:
            for statement in self.design_path.read_text().split(";\n"):
                if statement.strip():
                    try:
                        sandbox.con.execute(statement)
                    except duckdb.CatalogException:
                        pass    # the statement designs a table or column that these requests did not call for
        return sandbox.con


def inventory_zip(analysis):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in analysis.pack().items():
            archive.writestr(name, text)
    return out.getvalue()


def run_checks(analysis, con, include_spans=False, include_fanout=False):
    """Answers each planned check as the script would. Returns the rows in the script's layout, and the failures."""
    def run(sql):
        for name, number in LIMITS.items():
            sql = sql.replace(name, str(number))
        for statement in to_duckdb(sql + ";"):
            # SQL Server divides whole numbers into a whole number; DuckDB needs // to do the same.
            cursor = con.execute(statement.replace("/ 10) * 10", "// 10) * 10"))
        return cursor

    rows, failed = [], []
    for check in analysis.planned_checks(include_years=True, include_spans=include_spans, include_fanout=include_fanout):
        try:
            if check.kind == "values" and run(check.guard(analysis.catalogue)).fetchone()[0] > LIMITS[check.limit()]:
                continue
            cursor = run(check.select(analysis.catalogue))
        except Exception as error:  # the harness records the failure and carries on
            failed.append((check.kind, check.table, check.column, type(error).__name__))
            continue
        names = [n.strip() for n in check.columns().strip("()").split(",")]
        for values in cursor.fetchall():
            row = dict.fromkeys(LAYOUT, "")
            row.update({n: "" if value is None else str(value) for n, value in zip(names, values)})
            rows.append([row[n] for n in LAYOUT])
    return sorted(rows), failed


def checks_csv(rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(LAYOUT)
    writer.writerows(rows)
    return out.getvalue()


def fanout_built(con, catalogue, child, column):
    """The bands of a fanout check, as the sandbox holds them: (band, number of key values), unrounded."""
    sql = Check("fanout", child, column, parent=("P", "K")).select(catalogue)
    sql = sql.replace("HAVING COUNT_BIG(*) >= @minimum_count", "").replace("(g.n / 10) * 10", "g.n")
    return {row[3]: row[5] for row in con.execute(to_duckdb(sql + ";")[0]).fetchall()}


def scorecard(world, rows=600, include_spans=False, include_fanout=False):
    lines = []
    say = lines.append

    first = world.analysis()
    pack = first.pack()
    say(f"requests: {len(world.request_files())}")
    say("")
    say("ANALYSIS")
    say(pack["coverage.txt"].rstrip())

    planned = first.planned_checks(include_years=True, include_spans=include_spans, include_fanout=include_fanout)
    kinds = Counter(c.kind for c in planned)
    say("")
    say("CHECKS")
    say("planned: " + ", ".join(f"{kinds[k]} {k}" for k in KINDS if kinds[k]))
    con = world.truth(first, rows, include_fanout)
    answers, failed = run_checks(first, con, include_spans, include_fanout)
    answered = Counter(row[0] for row in answers)
    say("answered rows: " + ", ".join(f"{answered[k]} {k}" for k in KINDS if answered[k]))
    say(f"checks the stand-in database could not answer: {len(failed)}")
    for item in failed:
        say("  " + " ".join(item))

    text = checks_csv(answers)
    second = world.analysis(text)
    second_pack = second.pack()
    confirmed = [r for r in csv.DictReader(io.StringIO(second_pack["filters.csv"])) if r["value"]]
    say(f"filter values confirmed: {len(confirmed)} in {len({(r['table'], r['column']) for r in confirmed})} columns")

    say("")
    say("SANDBOX")
    for label, analysis in (("without check results", first), ("with check results", second)):
        sandbox = Sandbox(Catalogue.from_csv(world.catalogue_text()), inventory_zip(analysis))
        built = sandbox.build(rows)
        outcomes, reasons = Counter(), {}
        for path in world.request_files():
            name = path.relative_to(world.requests_path).as_posix()
            result = sandbox.run(decode(path.read_bytes()))
            if result["status"] != "ok":
                outcomes[v.OUTCOME_NOT_RUN] += 1
                reasons[name] = result["status"] + (": " + result["message"][:110] if result.get("message") else "")
            else:
                outcomes[v.OUTCOME_ROWS if result["anyRows"] else v.OUTCOME_NO_ROWS] += 1
        say(f"{label}: {built['tables']} tables, {built['rows']:,} rows; " + ", ".join(
            f"{outcomes[o]} {o}" for o in (v.OUTCOME_ROWS, v.OUTCOME_NO_ROWS, v.OUTCOME_NOT_RUN)))
        not_applied = built["rolesNotApplied"]
        last = sandbox
    say(f"roles that could not be applied, with check results: {len(not_applied)}")
    for item in not_applied:
        say(f"  {item['table']}.{item['column']}: {item['role']} ({item['reason']})")
    if include_fanout and second.checks:
        say("fanout, asked by the check results and built in the sandbox with them, as shares of key values:")
        provenance = {(f["child_table"], f["child_column"]): f["provenance"] for f in last.fanout}
        for (child, column, parent, key), asked in sorted(second.checks.fanout.items()):
            built_bands = fanout_built(last.con, second.catalogue, child, column)
            shares = []
            for bands in (dict(asked), built_bands):
                total = sum(bands.values()) or 1
                shares.append(" ".join(f"{round(100 * bands.get(label, 0) / total)}" for label in FANOUT_LABELS))
            say(f"  {child}.{column} -> {parent}.{key}: asked {shares[0]}; built {shares[1]} "
                f"({provenance.get((child, column), 'not a parent-child join in the sandbox')})")
    say("requests that could not be run:")
    for name, reason in sorted(reasons.items()):
        say(f"  {name}: {reason}")

    say("")
    say("PLANTED VALUES")
    outputs = "\n".join([*pack.values(), *second_pack.values(), first.check_script(), text]).lower()
    leaked = [value for value in world.planted() if value.lower() in outputs]
    say(f"planted values checked: {len(world.planted())}; found in an output: {len(leaked)}")
    for value in leaked:
        say(f"  LEAKED: {value}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.harness")
    parser.add_argument("world", type=Path)
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--dialect", default="tsql")
    parser.add_argument("--spans", action="store_true", help="include the spans checks")
    parser.add_argument("--fanout", action="store_true", help="include the fanout checks, over a skewed stand-in database")
    args = parser.parse_args()
    card = scorecard(World.from_folder(args.world, args.dialect), args.rows, args.spans, args.fanout)
    (args.world / "scorecard.txt").write_text(card)
    print(card, end="")


if __name__ == "__main__":
    main()
