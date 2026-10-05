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
      {"kind": "codes", "vocabulary": "SITE_X", "concept": 123, "codes": ["14", "15"], "date": "..."},
      {"kind": "join", "left": "T.A", "right": "U.B", "answer": "unsure", "date": "..."},
      {"kind": "codes", "vocabulary": "SITE_X", "concept": 123, "codes": [], "answer": "unsure", "date": "..."},
      {"kind": "charted", "from": "2025-01-01", "to": "2025-12-31", "codes": ["14", "15"], "counts": [["14", 1200, 80]], "date": "..."}
    ]}

A charted fact records what the optional count of the chosen codes gave: for each code, the readings charted on the
cohort's anaesthetics in the last year of the study period and the number of those anaesthetics, each rounded down to
the nearest ten, or empty under ten. A code that the count does not list was not charted on them.

An answer of "unsure" records that a person was asked and could not say, with the date, so that the question is
not asked again and a query can settle it instead.

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
KINDS = ("join", "filter", "codes", "route", "count", "textbp", "charted")
COUNT_ANSWERS = ("right", "few", "many", "unsure")
ROUTE_ANSWERS = ("absent", "hidden", "unsure")
STEP_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.sql")
ANSWERS = ("yes", "no", "unsure")
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
    if fact["kind"] == "count":
        if fact.get("answer") not in COUNT_ANSWERS or not isinstance(fact.get("years"), list) or len(fact["years"]) > 200:
            raise FactsError("a count fact gives the answer and the counts by year")
        years = []
        for item in fact["years"]:
            if not (isinstance(item, list) and len(item) == 3 and isinstance(item[0], int) and 1900 <= item[0] <= 2200
                    and all(v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0) for v in item[1:])):
                raise FactsError("each year of a count fact is a year and two counts")
            years.append(list(item))
        found.update(answer=fact["answer"], years=years)
        return found
    if fact["kind"] == "charted":
        codes, counts = fact.get("codes"), fact.get("counts")
        if not all(DATE.fullmatch(str(fact.get(k) or "")) for k in ("from", "to")) or not isinstance(codes, list) \
                or not codes or len(codes) > 200 or not isinstance(counts, list) or len(counts) > 200:
            raise FactsError("a charted fact gives the period, the codes counted and the counts")
        if not all(_acceptable(str(c), MAXIMUM_TEXT_LENGTH) and "," not in str(c) for c in codes):
            raise FactsError("the codes of a charted fact are plain codes")
        kept = []
        for item in counts:
            if not (isinstance(item, list) and len(item) == 3 and str(item[0]) in {str(c) for c in codes}
                    and all(v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0) for v in item[1:])):
                raise FactsError("each count of a charted fact is a code counted and two counts")
            kept.append([str(item[0]), item[1], item[2]])
        found.update({"from": fact["from"], "to": fact["to"], "codes": sorted({str(c) for c in codes}), "counts": kept})
        return found
    if fact["kind"] == "textbp":
        found["column"] = _name(catalogue, fact.get("column"))
        codes = fact.get("codes")
        if not isinstance(codes, list) or not codes or not all(_acceptable(str(c), MAXIMUM_TEXT_LENGTH) and "," not in str(c) for c in codes):
            raise FactsError("a textbp fact gives the codes charted as text")
        found["codes"] = sorted({str(c) for c in codes})
        return found
    if fact["kind"] == "route":
        if fact.get("answer") not in ROUTE_ANSWERS or not STEP_FILE.fullmatch(str(fact.get("step") or "")):
            raise FactsError("a route fact names a step of the conversion and says absent, hidden or unsure")
        found.update(step=fact["step"], answer=fact["answer"])
        return found
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
        unsure = fact.get("answer") == "unsure"
        if fact.get("answer") not in (None, "yes", "unsure"):
            raise FactsError("a codes fact's answer is yes or unsure")
        if not isinstance(codes, list) or (not codes and not unsure) or len(codes) > 200:
            raise FactsError("a codes fact gives at least one code")
        uncertain = fact.get("uncertain") or []
        if not isinstance(uncertain, list) or not all(_acceptable(str(c), MAXIMUM_TEXT_LENGTH) and "," not in str(c) for c in uncertain):
            raise FactsError("the codes that a person was not sure of are plain codes")
        if uncertain:
            found["uncertain"] = sorted({str(c) for c in uncertain})
        clean = []
        for code in codes:
            text = str(code).strip()
            if not _acceptable(text, MAXIMUM_TEXT_LENGTH) or "," in text:
                raise FactsError("a code is not acceptable")
            clean.append(text)
        found.update(vocabulary=vocabulary, concept=int(concept), codes=sorted(dict.fromkeys(clean)))
        if unsure:
            found["answer"] = "unsure"
        if fact.get("column"):
            found["column"] = _name(catalogue, fact["column"])
    return found


def _subject(fact):
    if fact["kind"] == "join":
        return ("join", frozenset({fact["left"].upper(), fact["right"].upper()}))
    if fact["kind"] == "filter":
        return ("filter", fact["column"].upper())
    if fact["kind"] == "route":
        return ("route", fact["step"].upper())
    if fact["kind"] in ("count", "textbp", "charted"):
        return (fact["kind"], fact.get("column", "").upper())
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

    def instead_of(self, left, right):
        """The fact whose answer names these two columns as the ones that join in place of another pair, or None."""
        wanted = frozenset({left.upper(), right.upper()})
        return next((f for f in self.items if f["kind"] == "join" and f.get("instead")
                     and frozenset(n.upper() for n in f["instead"]) == wanted), None)

    def route(self, step):
        return next((f for f in self.items if _subject(f) == ("route", step.upper())), None)

    def count(self):
        return next((f for f in self.items if f["kind"] == "count"), None)

    def charted(self):
        return next((f for f in self.items if f["kind"] == "charted"), None)

    def text_codes(self):
        return [f for f in self.items if f["kind"] == "textbp"]

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
            if fact["kind"] != "codes" or fact.get("answer") == "unsure":
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
