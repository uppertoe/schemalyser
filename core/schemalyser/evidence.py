"""The evidence store of the saved hospital schema: its append-only journal, the five dimensions of evidence that each
binding, link and code translation carries, and the identity of each saved version.

The journal is an ordered list of immutable entries. An entry is never updated, overwritten or deleted: a query offered
again, a result pasted again, a test query run again and a confirmation withdrawn each append a new entry that names
the entry it supersedes. Every entry has

    id            a stable identifier, made from the entry's own content
    sequence      its place in the journal, from 1
    time          when it was written
    kind          what happened (KINDS)
    actor         who did it: the name a person gave, Schemalyser for what the tool did itself, or "not recorded"
    provenance    one of the six sources in PROVENANCES
    scope         the hospital, the schema version it was made from, and, where they apply, the period it covers and
                  the workflow or codes it concerns
    supersedes    the id of the entry it replaces, or None
    payload       what was recorded

A dimensions record keeps the five dimensions of section 3 of docs/contract.md apart. Each dimension is either absent
(None) or a record of its date, the actor or the journal entry that established it, the measured figure where there is
one, and the hashes of what it rested on (rests_on). A change to any of those makes the dimension stale, with the
reason, which stale() gives; nothing here is ever recomputed and written as if it were recorded.
"""
import copy
import datetime as dt
import hashlib
import json

# Where each fact came from: the whole of a table, a sample of it (the anaesthetics of one year in #cohort), the
# database's own records of its tables or the data dictionary, a person's answer or judgement, the page's own proposal,
# or a reference conversion's lineage.
COMPLETE, SAMPLE, METADATA, PERSON, INFERENCE = "complete data", "a sample", "metadata", "a person", "an inference"
REFERENCE = "a reference conversion"
# The hospital's own conversion to OMOP, which may give the concept of a local code.
HOSPITAL_CONVERSION = "the hospital's own conversion"
PROVENANCES = (COMPLETE, SAMPLE, METADATA, PERSON, INFERENCE, REFERENCE, HOSPITAL_CONVERSION)
# The actor recorded where the page collected no name, and the actor of what the tool did itself.
NOT_RECORDED = "not recorded"
TOOL = "Schemalyser"
JOURNAL_FORMAT = 2
KINDS = ("dictionary loaded", "vendor descriptions added", "reference lineage loaded", "query offered", "result returned",
         "answer", "correction kept", "codes chosen", "concepts translated", "judgement", "setting", "test run", "evidence imported")
DIMENSIONS = ("confirmed", "present", "tested", "reconciled", "validated")
# Why a dimension is stale, by the part of what it rested on that changed.
REASONS = {"binding": "the binding changed", "link": "the link changed", "codes": "the codes changed",
           "contract": "the contract changed"}


class EvidenceError(ValueError):
    """An entry or a record that breaks the rules of the evidence store."""


def digest(value, length=16):
    """A short SHA-256 hash of a value written as canonical JSON."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


class Journal:
    """The append-only journal. Entries are held as their JSON text, so that nothing can change one after it is
    written; every read returns a copy."""

    def __init__(self, entries=()):
        self._held = []
        for entry in entries:
            self._check(entry, len(self._held) + 1)
            self._held.append(json.dumps(entry, sort_keys=True, ensure_ascii=False))
        self._ids = {json.loads(text)["id"]: n for n, text in enumerate(self._held)}

    @staticmethod
    def _check(entry, sequence):
        wanted = {"id", "sequence", "time", "kind", "actor", "provenance", "scope", "supersedes", "payload"}
        if not isinstance(entry, dict) or set(entry) != wanted:
            raise EvidenceError("A journal entry holds exactly id, sequence, time, kind, actor, provenance, scope, supersedes and payload.")
        if entry["sequence"] != sequence:
            raise EvidenceError("The journal's entries are numbered in order from 1.")
        if entry["kind"] not in KINDS or entry["provenance"] not in PROVENANCES:
            raise EvidenceError("A journal entry names a kind and a provenance that the journal knows.")
        if not isinstance(entry["actor"], str) or not entry["actor"].strip():
            raise EvidenceError("A journal entry names its actor.")
        if not isinstance(entry["scope"], dict) or not {"hospital", "schema_id"} <= set(entry["scope"]):
            raise EvidenceError("A journal entry's scope names the hospital and the schema version.")

    def __len__(self):
        return len(self._held)

    def __iter__(self):
        return iter(self.entries())

    def entries(self):
        return [json.loads(text) for text in self._held]

    def get(self, entry_id):
        at = self._ids.get(entry_id)
        return json.loads(self._held[at]) if at is not None else None

    def copy(self):
        other = Journal()
        other._held, other._ids = list(self._held), dict(self._ids)
        return other

    def append(self, kind, payload, *, actor, provenance, scope, supersedes=None, time=None):
        """Appends one entry and returns a copy of it."""
        if supersedes is not None and supersedes not in self._ids:
            raise EvidenceError("An entry supersedes only an entry that the journal holds.")
        sequence = len(self._held) + 1
        entry = {"sequence": sequence, "time": time or now(), "kind": kind, "actor": (actor or NOT_RECORDED).strip()[:100] or NOT_RECORDED,
                 "provenance": provenance, "scope": copy.deepcopy(scope), "supersedes": supersedes,
                 "payload": copy.deepcopy(payload)}
        entry["id"] = "j" + digest(entry, 12)
        self._check(entry, sequence)
        self._held.append(json.dumps(entry, sort_keys=True, ensure_ascii=False))
        self._ids[entry["id"]] = sequence - 1
        return json.loads(self._held[-1])

    def superseded(self):
        """The ids of every entry that a later entry supersedes."""
        return {json.loads(text)["supersedes"] for text in self._held} - {None}

    def current(self, kind, key=None):
        """The entries of a kind that no later entry supersedes, in order, optionally only those whose payload holds
        each item of key."""
        gone = self.superseded()
        return [e for e in self.entries() if e["kind"] == kind and e["id"] not in gone
                and all(e["payload"].get(k) == v for k, v in (key or {}).items())]

    def latest(self, kind, **key):
        found = self.current(kind, key)
        return found[-1] if found else None


# The dimensions record.

def empty():
    return {dimension: None for dimension in DIMENSIONS}


def record(date, rests_on, *, by=None, entry=None, figure=None, **more):
    """One dimension's record: its date, the actor or entry that established it, any measured figure, and the hashes
    of what it rested on."""
    if by is None and entry is None:
        raise EvidenceError("A dimension names the actor or the journal entry that established it.")
    found = {"date": str(date)[:10], "by": by, "entry": entry, "figure": figure, "rests_on": dict(rests_on)}
    found.update({k: v for k, v in more.items() if v is not None})
    return found


def stale(held, current):
    """The reasons that a dimension's record no longer holds, as sentences' clauses, from the hashes it rested on and
    the hashes of the same things now; [] where it still holds."""
    if not held:
        return []
    return [REASONS.get(part, f"the {part} changed") for part, value in sorted(held.get("rests_on", {}).items())
            if current.get(part) != value]


def content_id(files):
    """The content hash of a saved version: every file but settings.json, which carries it, and README.md, which is
    written from the rest. Returns the whole SHA-256 in hex."""
    hashed = sorted((path, hashlib.sha256(bytes(data)).hexdigest()) for path, data in files.items()
                    if path not in ("settings.json", "README.md"))
    return hashlib.sha256(json.dumps(hashed).encode("utf-8")).hexdigest()
