"""Runs the extraction over a set of requests and writes the inventory pack."""
import csv
import io
from collections import Counter, defaultdict

from . import vocabulary as v
from . import checks as checking
from . import roles as meanings
from . import tuning as tunable
from .catalogue import Catalogue
from .extract import analyse_request
from .rules import SiteRules


def _csv(header, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


class Analysis:
    """Holds the catalogue and the site rules, takes requests one at a time, and writes the pack."""

    def __init__(self, catalogue_csv, rules_json=None, dialect="tsql", checks_csv=None):
        self.dialect = dialect
        self.rules = SiteRules.from_json(rules_json)
        self.catalogue, self.held_back = Catalogue.from_csv(catalogue_csv).without(self.rules.local_table_patterns)
        self._requests = {}
        self.summary = {}
        # What the site rules say each column means, for the sandbox to use.
        self.roles = meanings.from_rules(self.rules, self.catalogue)
        # The overrides of the tunable parameters that the site rules give, and the date that means "still in place".
        self.tuning = tunable.accept(self.rules.tuning)
        self.still_in_place = tunable.sentinel(self.rules)
        self.checks = checking.Checks.from_csv(checks_csv, self.catalogue, self.rules) if checks_csv else None
        self._confirmed = self.checks.confirmed(self.catalogue) if self.checks else {}

    def add_request(self, name, sql):
        self._requests[name] = analyse_request(sql, self.catalogue, self.held_back, self.dialect, self._confirmed)

    def check_script(self, include_spans=False, include_fanout=False):
        """The T-SQL script that asks the database what the findings leave open.

        include_spans adds the checks that count the time between two date columns of one table, and
        include_fanout the checks that count how many rows hold each key value of a column that refers
        to another table. Both are off by default, because the script's wording was approved before
        those checks existed.
        """
        planned = self.planned_checks(include_spans=include_spans, include_fanout=include_fanout)
        return checking.script(planned, self.catalogue, v.CHECK_SCRIPT)

    def planned_checks(self, include_years=True, include_spans=False, include_fanout=False):
        findings = set().union(*(r.findings for r in self._requests.values())) if self._requests else set()
        unique = {key: stats["unique"] for key, stats in self.checks.columns.items()} if self.checks else {}
        return checking.plan(self.catalogue, self.rules, findings, include_years, include_spans, include_fanout, unique)

    def checked_tables(self):
        """The definition tables that a planned check reads for labels, which the sandbox must also build.

        Every name comes from the catalogue, through the site rules' definition keys.
        """
        return sorted({c.definition[0] for c in self.planned_checks() if c.kind == "values" and c.definition})

    def request_index(self):
        """Which file each request number refers to. Shown on the page; never part of the pack."""
        return list(enumerate(sorted(self._requests), start=1))

    def pack(self):
        using = defaultdict(set)          # finding -> request numbers
        per_request = []
        totals = Counter()
        for number, name in self.request_index():
            result = self._requests[name]
            for finding in result.findings:
                using[finding].add(number)
            elements = sorted({f"{f[1]}.{f[2]}" for f in result.findings if f[0] == "column"})
            per_request.append([number, result.statements, sum(result.unresolved.values()), " ".join(elements)])
            totals["files"] += 1
            totals["files_not_fully_read"] += v.not_fully_read(result.unresolved)
            # The files that each reason applies to, so that the coverage says the reason that it knows.
            for kind in v.HIDES_SQL:
                totals[f"files_{kind}"] += bool(result.unresolved.get(kind))
            totals["statements"] += result.statements
            totals.update(result.unresolved)

        roles = defaultdict(lambda: defaultdict(set))   # (table, column) -> role -> request numbers
        for finding, numbers in using.items():
            if finding[0] == "column":
                roles[finding[1:3]][finding[3]] |= numbers
        elements = []
        for (table, column), by_role in sorted(roles.items()):
            data_type = self.catalogue.table(table).column(column).data_type
            everyone = set().union(*by_role.values())
            elements.append([table, column, data_type, len(everyone)] + [len(by_role[r]) for r in v.ROLES])

        def rows(kind):
            return sorted(list(f[1:]) + [len(n)] for f, n in using.items() if f[0] == kind)

        self.summary = {
            "files": totals["files"],
            "notFullyRead": totals["files_not_fully_read"],
            "tables": len({f[1] for f in using if f[0] == "table"}),
            "columns": len(elements),
            "joins": sum(1 for f in using if f[0] == "join"),
            "filters": sum(1 for f in using if f[0] == "filter"),
            "derivations": sum(1 for f in using if f[0] == "derivation"),
            "unread": [[v.UNRESOLVED_LABELS[c], totals[c]] for c in v.UNRESOLVED if totals[c]],
        }
        self.summary["sentences"] = [
            v.files_sentence(self.summary["files"], self.summary["notFullyRead"],
                             {kind: totals[f"files_{kind}"] for kind in v.HIDES_SQL}),
            v.found_sentence(*(self.summary[k] for k in ("tables", "columns", "joins", "filters", "derivations"))),
        ]
        coverage = list(self.summary["sentences"])
        coverage += [f"{label} {count}" for label, count in self.summary["unread"]] or [v.NOTHING_UNREAD]

        return {
            "elements.csv": _csv(["table", "column", "data_type", "requests", *v.ROLES], elements),
            "joins.csv": _csv(["left_table", "left_column", "right_table", "right_column", "join_kind", "requests"],
                              rows("join")),
            "filters.csv": _csv(["table", "column", "operator", "value_kind", "value", "requests"], rows("filter")),
            "derivations.csv": _csv(["expression", "columns", "requests"], rows("derivation")),
            "comparisons.csv": _csv(["left_table", "left_column", "operator", "right_table", "right_column", "requests"],
                                    rows("comparison")),
            "requests.csv": _csv(["request", "statements", "unresolved", "elements"], per_request),
            "coverage.txt": "\n".join(coverage) + "\n",
            **({"checks.csv": self.checks.to_csv()} if self.checks else {}),
            # The definition tables that the check script reads, so that the sandbox builds them as well.
            **({"checked.csv": _csv(["table"], [[t] for t in checked])} if (checked := self.checked_tables()) else {}),
            **({"roles.csv": meanings.to_csv(self.roles)} if self.roles else {}),
            **({"tuning.csv": tunable.to_csv(self.tuning, self.still_in_place)}
               if self.tuning or self.still_in_place else {}),
        }
