"""What the team's SQL showed, kept in the state as sql_evidence.json, so that it settles items after the requests are gone.

A request file settles a checklist item by using a table or a column, by joining two columns or by
comparing a column with fixed values. Once a run has read the request files, this file keeps each such
finding as the catalogue names it, with the number of request files that showed it and the date of the
run. It keeps no text of any request and no file name. A later run that has no request files, or other
ones, reads it back, and each finding settles its item as the SQL did, with a sentence that says how
many of the team's queries showed it and on which date. Where the request files to hand show the same
finding, the item cites them instead, and the file takes their count and the new date.

    {"evidence": [
      {"kind": "table", "names": ["T"], "files": 3, "date": "2026-10-05"},
      {"kind": "column", "names": ["T", "A"], "files": 2, "date": "2026-10-05"},
      {"kind": "join", "names": ["T", "A", "U", "B"], "files": 1, "date": "2026-10-05"},
      {"kind": "filter", "names": ["T", "A"], "files": 1, "date": "2026-10-05"}
    ]}

Every name must resolve in the catalogue, the count must be a whole number of at least one, and the
date must be yyyy-mm-dd; a file that breaks any of these is refused as a whole, as facts.json is.
"""
import json
import re

FILE = "sql_evidence.json"
KINDS = {"table": 1, "column": 2, "join": 4, "filter": 2}     # each kind, with the number of names it gives
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAXIMUM_ENTRIES = 200_000
MAXIMUM_FILES = 10_000_000


class EvidenceError(ValueError):
    """sql_evidence.json does not have the expected form."""


def _key(kind, names):
    """The subject of a finding, in capitals, so that either spelling and either direction of a join is the same."""
    upper = tuple(n.upper() for n in names)
    if kind == "join":
        return (kind, frozenset({upper[:2], upper[2:]}))
    return (kind,) + upper


def _resolved(kind, names, catalogue):
    """The names in the catalogue's own spelling, or None where one of them does not resolve."""
    spelled = []
    for i in range(0, len(names), 2):
        entry = catalogue.table(names[i])
        if entry is None:
            return None
        spelled.append(entry.name)
        if i + 1 < len(names):
            field = entry.column(names[i + 1])
            if field is None:
                return None
            spelled.append(field.name)
    if kind == "join" and spelled[:2] == spelled[2:]:
        return None
    if kind == "join" and (spelled[0].upper(), spelled[1].upper()) > (spelled[2].upper(), spelled[3].upper()):
        spelled = spelled[2:] + spelled[:2]
    return spelled


class Saved:
    """The findings of the team's SQL, at most one for each subject, each with its count of request files and its date."""

    def __init__(self):
        self.items = {}       # key -> {"kind", "names", "files", "date"}

    @classmethod
    def from_json(cls, text, catalogue):
        """Reads sql_evidence.json, refusing the whole file if any entry does not have the expected form."""
        try:
            data = json.loads(text) if text and text.strip() else {"evidence": []}
        except ValueError as error:
            raise EvidenceError("sql_evidence.json is not valid JSON") from error
        if not isinstance(data, dict) or set(data) != {"evidence"} or not isinstance(data["evidence"], list) \
                or len(data["evidence"]) > MAXIMUM_ENTRIES:
            raise EvidenceError("sql_evidence.json holds a list of findings")
        found = cls()
        for entry in data["evidence"]:
            if not isinstance(entry, dict) or set(entry) != {"kind", "names", "files", "date"}:
                raise EvidenceError("a finding has a kind, its names, a count of files and a date")
            kind, names, files, date = entry["kind"], entry["names"], entry["files"], entry["date"]
            if kind not in KINDS or not isinstance(names, list) or len(names) != KINDS[kind] \
                    or not all(isinstance(n, str) for n in names):
                raise EvidenceError("a finding has a kind that is not known, or the wrong number of names")
            if isinstance(files, bool) or not isinstance(files, int) or not 1 <= files <= MAXIMUM_FILES:
                raise EvidenceError("a finding's count of files is a whole number of at least one")
            if not isinstance(date, str) or not DATE.fullmatch(date):
                raise EvidenceError("a finding needs its date as yyyy-mm-dd")
            spelled = _resolved(kind, names, catalogue)
            if spelled is None:
                raise EvidenceError("a finding names something that the catalogue does not hold")
            found.items[_key(kind, spelled)] = {"kind": kind, "names": spelled, "files": files, "date": date}
        return found

    @classmethod
    def gathered(cls, requests, catalogue, earlier=None, date=None):
        """The findings of the request files to hand, as a Saved, with any earlier finding that they do not show kept as it was.

        requests holds, for each request file, the set of the analyser's findings; date is that of this run.
        """
        found = cls()
        counted = {}
        for findings in requests:
            seen = {}
            for f in findings:
                if f[0] in ("table",):
                    names = [f[1]]
                elif f[0] in ("column", "filter"):
                    names = [f[1], f[2]]
                elif f[0] == "join":
                    names = [f[1], f[2], f[3], f[4]]
                else:
                    continue
                spelled = _resolved(f[0], names, catalogue)
                if spelled is not None:
                    seen.setdefault(_key(f[0], spelled), (f[0], spelled))
            for key, (kind, spelled) in seen.items():
                if key in counted:
                    counted[key]["files"] += 1
                else:
                    counted[key] = {"kind": kind, "names": spelled, "files": 1, "date": date}
        if earlier is not None:
            found.items.update({key: dict(item) for key, item in earlier.items.items() if key not in counted})
        found.items.update(counted)
        return found

    def get(self, kind, *names):
        """(files, date) for a finding, or None. A join is given as four names, in either direction."""
        item = self.items.get(_key(kind, names))
        return (item["files"], item["date"]) if item else None

    def joins(self):
        """Every join kept, as ((table, column), (table, column), files)."""
        return [((i["names"][0], i["names"][1]), (i["names"][2], i["names"][3]), i["files"])
                for i in self.items.values() if i["kind"] == "join"]

    def to_json(self):
        order = list(KINDS)
        entries = sorted(self.items.values(), key=lambda i: (order.index(i["kind"]), [n.upper() for n in i["names"]]))
        if not entries:
            return '{"evidence": []}\n'
        lines = ",\n".join("  " + json.dumps(entry) for entry in entries)
        return '{"evidence": [\n' + lines + "\n]}\n"
