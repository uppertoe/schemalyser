"""Facts that a person confirmed, kept in the state as facts.json, which settle checklist items as SQL does.

A colleague who knows the source database can often answer in a minute what no query result can settle:
whether two columns join, whether a step's comparison with fixed values is right, and which local codes mean
a concept. Each answer is a fact with a kind, the catalogue names it is about, the answer and the date, and
optionally who gave it. Every name must resolve in the catalogue, and a code must pass the same rule as a
value in the check results. Who gave a fact is kept only in facts.json and is never written to any other
output; the checklist says only that a person confirmed it, and on which date.

    {"facts": [
      {"kind": "join", "left": "T.A", "right": "U.B", "answer": "yes", "date": "2026-10-05", "who": "..."},
      {"kind": "join", "left": "T.A", "right": "U.B", "answer": "no", "instead": ["T.C", "U.D"], "date": "..."},
      {"kind": "filter", "column": "T.A", "answer": "yes", "date": "..."},
      {"kind": "codes", "vocabulary": "SITE_X", "concept": 123, "codes": ["14", "15"], "date": "..."}
    ]}

The codes of a codes fact become mapping rows in site_mappings.csv, in the conversion folder beside the
conversion's own source_to_concept_map.csv, so that the conversion, the checklist and the release load them
together and nobody edits a mapping file by hand. Where a site mapping row and the conversion's own row give
the same source code under the same vocabulary, the site's row wins, because it records what a person at
the site confirmed.
"""
import csv
import io
import json
import re

from .checks import MAXIMUM_TEXT_LENGTH, _acceptable

FILE = "facts.json"
SITE_MAPPINGS = "site_mappings.csv"
KINDS = ("join", "filter", "codes")
ANSWERS = ("yes", "no")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
VOCABULARY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,49}")
MAXIMUM_WHO = 80
MAXIMUM_FACTS = 2000
MAPPING_FIELDS = ("source_code", "source_concept_id", "source_vocabulary_id", "source_code_description",
                  "target_concept_id", "target_vocabulary_id", "valid_start_date", "valid_end_date", "invalid_reason")


class FactsError(ValueError):
    """facts.json, or a fact given to it, does not have the expected form."""


def _name(catalogue, text):
    """A TABLE.COLUMN that resolves in the catalogue, in the catalogue's own spelling."""
    table, _, column = str(text or "").partition(".")
    entry = catalogue.table(table) if table else None
    field = entry.column(column) if entry is not None and column else None
    if field is None:
        raise FactsError("a fact names a column that the catalogue does not hold")
    return f"{entry.name}.{field.name}"


def _who(value):
    text = str(value or "").strip()
    if len(text) > MAXIMUM_WHO or "\n" in text or "\r" in text:
        raise FactsError("who gave a fact is short plain text")
    return text


def check(fact, catalogue):
    """One fact, checked and written in the catalogue's spelling. Raises FactsError."""
    if not isinstance(fact, dict) or fact.get("kind") not in KINDS:
        raise FactsError("a fact has a kind that is not known")
    date = str(fact.get("date") or "")
    if not DATE.fullmatch(date):
        raise FactsError("a fact needs its date as yyyy-mm-dd")
    found = {"kind": fact["kind"], "date": date}
    if fact.get("who"):
        found["who"] = _who(fact["who"])
    if fact["kind"] in ("join", "filter"):
        if fact.get("answer") not in ANSWERS:
            raise FactsError("a fact's answer is yes or no")
        found["answer"] = fact["answer"]
    if fact["kind"] == "join":
        found["left"], found["right"] = _name(catalogue, fact.get("left")), _name(catalogue, fact.get("right"))
        if fact.get("instead"):
            if found["answer"] != "no" or not isinstance(fact["instead"], list) or len(fact["instead"]) != 2:
                raise FactsError("the columns that do join are given with a no, as two columns")
            found["instead"] = [_name(catalogue, name) for name in fact["instead"]]
            tables = {name.split(".")[0] for name in found["instead"]}
            if tables != {found["left"].split(".")[0], found["right"].split(".")[0]}:
                raise FactsError("the columns that do join belong to the same two tables")
    elif fact["kind"] == "filter":
        found["column"] = _name(catalogue, fact.get("column"))
    else:
        vocabulary = str(fact.get("vocabulary") or "")
        if not VOCABULARY.fullmatch(vocabulary):
            raise FactsError("a codes fact names a mapping vocabulary")
        concept = str(fact.get("concept") or "")
        if not concept.isdecimal() or len(concept) > 12:
            raise FactsError("a codes fact names a concept by its number")
        codes = fact.get("codes")
        if not isinstance(codes, list) or not codes or len(codes) > 200:
            raise FactsError("a codes fact gives at least one code")
        clean = []
        for code in codes:
            text = str(code).strip()
            if not _acceptable(text, MAXIMUM_TEXT_LENGTH) or "," in text:
                raise FactsError("a code is not acceptable")
            clean.append(text)
        found.update(vocabulary=vocabulary, concept=int(concept), codes=sorted(dict.fromkeys(clean)))
        if fact.get("column"):
            found["column"] = _name(catalogue, fact["column"])
    return found


def _subject(fact):
    if fact["kind"] == "join":
        return ("join", frozenset({fact["left"].upper(), fact["right"].upper()}))
    if fact["kind"] == "filter":
        return ("filter", fact["column"].upper())
    return ("codes", fact["vocabulary"].upper(), fact["concept"])


class Facts:
    """The confirmed facts, at most one for each subject: a later fact about the same thing replaces the earlier."""

    def __init__(self, facts=()):
        self.items = list(facts)

    @classmethod
    def from_json(cls, text, catalogue):
        """Reads facts.json, refusing the whole file if any fact does not have the expected form."""
        try:
            data = json.loads(text) if text and text.strip() else {"facts": []}
        except ValueError as error:
            raise FactsError("facts.json is not valid JSON") from error
        if not isinstance(data, dict) or not isinstance(data.get("facts"), list) or len(data["facts"]) > MAXIMUM_FACTS:
            raise FactsError("facts.json holds a list of facts")
        found = cls()
        for fact in data["facts"]:
            found = found.with_fact(check(fact, catalogue))
        return found

    def with_fact(self, fact):
        subject = _subject(fact)
        return Facts([f for f in self.items if _subject(f) != subject] + [fact])

    def to_json(self):
        return json.dumps({"facts": self.items}, indent=2, sort_keys=True) + "\n"

    def join(self, left, right):
        wanted = ("join", frozenset({left.upper(), right.upper()}))
        return next((f for f in self.items if _subject(f) == wanted), None)

    def filter(self, column):
        return next((f for f in self.items if _subject(f) == ("filter", column.upper())), None)

    def codes(self, vocabulary, concept=None):
        return [f for f in self.items if f["kind"] == "codes" and f["vocabulary"].upper() == vocabulary.upper()
                and (concept is None or f["concept"] == int(concept))]

    def site_mappings(self, vocabulary_ids=None):
        """The mapping rows that the codes facts give, as CSV text in the columns of source_to_concept_map."""
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(MAPPING_FIELDS)
        for fact in self.items:
            if fact["kind"] != "codes":
                continue
            for code in fact["codes"]:
                writer.writerow([code, 0, fact["vocabulary"], f"confirmed by a person on {fact['date']}",
                                 fact["concept"], (vocabulary_ids or {}).get(fact["concept"], ""), "1970-01-01",
                                 "2099-12-31", ""])
        return out.getvalue()


def merged_mappings(own_rows, site_rows):
    """The conversion's own mapping rows with the site's added, where the site's row wins for the same code and vocabulary."""
    key = lambda row: (str(row.get("source_vocabulary_id") or "").upper(), str(row.get("source_code") or ""))  # noqa: E731
    site = {key(row) for row in site_rows}
    return [row for row in own_rows if key(row) not in site] + list(site_rows)
