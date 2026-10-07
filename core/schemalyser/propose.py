"""The proposer: a draft map from the role model and a hospital's data dictionary, by plain matching and with no model.

For each role view the proposer chooses the table whose rows are the view's rows, and for each column of the view the
table and column that play it. It matches the words of the role model (each role's and each attribute's look_for
phrases and name, in rolemodel/contract.json) with the words of the dictionary's descriptions and names, with a fixed
ranking that gives the same answer every time:

    1. Words. Every description and name is cut into words, with British and American spellings made one, a short
       list of common abbreviations of names spelt out (PAT for patient, DTTM for date and time), and plurals and the
       endings -ed and -ing taken off. A column's words are its description's words and, counted twice, its name's.
    2. Score. A candidate's score is the BM25 score of the role's words against the column's words, which favours
       rare words over common ones, plus a bonus for each look_for phrase that the description holds in order.
    3. Type. The score is raised where the column's data type suits the role (a date and time for a time, a Y or N
       column for a flag) and cut where it does not.
    4. Reach. A column counts only in the view's own table or in a table that the view's table reaches by a key: a
       column whose name is the primary key of another table, or ends with that key's name, as a column named
       ..._ENC_ID reaches the table whose key is ENC_ID. Each step costs a little of the score, and a view never
       reaches a table by a key that is not that table's whole primary key, so that no join repeats the view's rows.
    5. Links. A column that links to another role, such as role_anaesthetic.patient_key, is the column in reach that
       has the name of that role's key, or ends with it.
    6. The view's table is the one with the best sum: its own description's score, the best score of each of the
       view's columns within its reach, and whether it has a key of one column for a view that needs one.

Each proposal gives the top few candidates, the dictionary's words that matched, and a confidence (high, medium or
low). Where no candidate has at least two of the role's words or one of its phrases, of a type that suits, the
proposal says that nothing fits and the view gives the column empty.

The draft map is a map folder in the usual format: one SQL view for each role and map.json, in which every binding is
proposed, carries a question, and quotes the dictionary's description as its evidence. Because it quotes the licensed
dictionary, the draft map is the hospital's own and is written only to a private folder. The command line prints
names and counts only, never a description.

confirm applies a person's answers to a draft map: yes, no with the replacement, or not sure, each recorded with its
date, and writes the affected views again from their bindings.
"""
import csv
import datetime as dt
import io
import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from . import rolemap
from .catalogue import NAME
from .extract import decode

ROOT = Path(__file__).resolve().parents[2]
# The folders of the repository that are published. A draft map from a real dictionary quotes it, so it may not be
# written into any of them; an invented dictionary's draft may, when the caller says that the dictionary is invented.
PUBLIC = ("core", "docs", "fixtures", "site", "tools")

SPELLING = (("anaesth", "anesth"), ("haem", "hem"), ("paed", "ped"), ("oesoph", "esoph"), ("oedem", "edem"),
            ("theatre", "theater"), ("colour", "color"), ("centre", "center"), ("isation", "ization"),
            ("artefact", "artifact"), ("programme", "program"))
# Common abbreviations in the names of columns and tables, spelt out. Each is a general abbreviation of health
# records, and none is a name of any vendor's table or column.
ABBREVIATIONS = {
    "pat": "patient", "pt": "patient", "enc": "encounter", "hsp": "hospital", "hosp": "hospital", "adm": "admission",
    "admsn": "admission", "admit": "admission", "disch": "discharge", "dt": "date", "dte": "date", "tm": "time",
    "dttm": "date time", "ts": "date time", "tstamp": "date time", "an": "anesthesia", "anes": "anesthesia",
    "anesth": "anesthesia", "anaes": "anesthesia", "meas": "measurement", "obs": "observation", "yn": "flag",
    "flg": "flag", "ind": "flag", "id": "identifier", "key": "identifier", "num": "number", "proc": "procedure",
    "emerg": "emergency", "dx": "diagnosis", "diag": "diagnosis", "med": "medication", "meds": "medication",
    "prov": "provider", "dept": "department", "loc": "location", "surg": "surgery", "wt": "weight", "ht": "height",
    "gest": "gestation", "temp": "temperature", "bp": "blood pressure", "stat": "status", "typ": "type",
    "desc": "description", "cmt": "comment", "cat": "category", "amt": "amount", "qty": "quantity", "seq": "sequence",
    "dob": "date birth", "dod": "date death", "icu": "intensive care", "los": "length stay", "rec": "record",
    "evt": "event", "dur": "duration", "inst": "time", "instant": "time", "read": "reading", "accept": "accepted",
}
STOP = set("the a an of for this that these those to in on at is are be been by with and or which it its as from was "
           "were has have if into per each any all such can may will who whom what when where there their than then "
           "also only other used use uses using stores store stored contains contain contained holds hold table "
           "column row rows information data item given".split())
# Words that mean the same in a dictionary, after their endings are taken off.
SYNONYMS = {"died": "death", "dead": "death", "decease": "death", "born": "birth", "instant": "time", "began": "start",
            "begin": "start", "commence": "start", "end": "stop", "ended": "stop", "finish": "stop", "id": "identifier",
            "key": "identifier"}

BM25_K1, BM25_B = 1.2, 0.5
HOP = 0.85                      # what each step of a join costs
TOP = 400                       # how many of the best columns of the whole dictionary each column of a view keeps
TABLES = 30                     # how many of the best-described tables each view considers for its rows
SHOWN = 3                       # how many other candidates a proposal lists
QUOTE = 160                     # how many characters of a description a sentence quotes
# The words, after their endings are taken off, of a table that keeps old or deleted copies of rows rather than the
# record itself, such as a table of edited values or an audit trail.
COPIES = {"edit", "audit", "original", "delet"}

WORDING = {
    "description": "Schemalyser proposed this draft map from a data dictionary on {date}, and no person has yet confirmed any of its bindings.",
    "rows_says": "The dictionary describes the table {table} as \"{quote}\", and {words} match one row for each {what}.",
    "rows_says_bare": "The dictionary gives no description of the table {table}, and its name and columns match one row for each {what}.",
    "rows_question": "Please confirm whether one row of {table} is one {what}, with no row repeated for the same {what}.",
    "column_says": "The dictionary describes {table}.{column} as \"{quote}\", and {words} match the role.",
    "column_says_name": "The dictionary describes {table}.{column} as \"{quote}\", and its name matches the role.",
    "column_says_bare": "The dictionary gives no description of {table}.{column}, and its name matches the role.",
    "key_says": "{table}.{column} is the column that identifies a row of the table that holds this part, which the dictionary describes as \"{quote}\".",
    "key_says_bare": "{table}.{column} is the column that identifies a row of the table that holds this part, and the dictionary gives no description of it.",
    "link_says": "{table}.{column} has the same name as {target}, the column that identifies a row of {role}, and the page reaches it {how}.",
    "link_says_quote": "{table}.{column} has the same name as {target}, the column that identifies a row of {role}, and the page reaches it {how}; the dictionary describes {first} as \"{quote}\".",
    "reverse_says": "{table}.{column} is the column that identifies a row of {role}, and the page reaches it {how}. That last link runs from the other side, so a row is repeated wherever one row of {other} has more than one row of {table}.",
    "column_question": "Please confirm whether {table}.{column} holds {about}, and name the column that holds it if it does not.",
    "nothing_says": "The dictionary holds no column within reach of {table} that fits {about}, so the page leaves it empty.",
    "nothing_question": "Please name the table and column that hold {about}, or say that the hospital does not record it.",
    "no_rows_says": "The dictionary holds no table that fits {view}, so the proposer has not drafted it.",
    "kind_says": "The dictionary cannot give the local codes of {kind}, because each hospital builds its own list of the things that can be charted.",
    "kind_question": "Please list the local codes of {source} that hold {meaning}",
    "header": "-- {view}: a draft that Schemalyser proposed from the data dictionary on {date}.",
    "header_none": "-- No person has confirmed any of its bindings yet, and map.json gives each one with its evidence and its question.",
    "header_nothing": "-- The dictionary holds no column that fits {columns}, so the view gives {them} empty.",
    "header_vocabulary": "-- The local values of {columns} have not yet been translated to the role's kinds or to 1 and 0, so the view gives a kind as other and a flag as empty until a person writes the translation.",
    "header_codes": "-- The local codes of the mean pressures are not yet known, so every reading is of the kind other until a person supplies them in map.json.",
    "header_person": "-- A person has answered for some bindings of this view, and map.json records each answer with its date.",
    "via_one": "by matching {path}",
    "in_table": "in the table that holds this part",
    "replaced_says": "A person replaced the proposal with {source} on {date}.",
    "replaced_says_quote": "A person replaced the proposal with {source} on {date}, which the dictionary describes as \"{quote}\".",
    "no_says": "A person said on {date} that the proposal does not hold this role, and named no replacement.",
    "codes_says": "A person gave the local codes {codes} on {date}.",
    # The command line.
    "public": "{folder} lies inside a published folder of the repository, and a draft map quotes the data dictionary, so Schemalyser writes it only to a private folder. If the dictionary is invented, add --invented.",
    "summary": "{view}: {table}, with {bound} of {total} columns proposed ({confidence}).",
    "summary_none": "{view}: the dictionary holds no table that fits.",
    "missing": "The catalogue does not hold {count} of the dictionary's columns, so the proposer has left them out.",
    "written": "Schemalyser wrote the draft map to {folder}. It has {items} open items, and every binding awaits a person's confirmation.",
    "answers": "Schemalyser recorded {count} answers ({detail}). The map now has {items} open items.",
    "answer_unknown": "{where}: the answer is yes, no or not sure, and not {answer}.",
    "attribute_unknown": "{where}: the map holds no binding named {about}.",
    "replacement": "{where}: a replacement is a table and a column, written as TABLE.COLUMN, with an optional link written as via TABLE.COLUMN = TABLE.COLUMN.",
    "replacement_rows": "{where}: a different table for the rows changes every binding of the view, so please run propose again with --base {view}=TABLE.",
    "unreachable": "{where}: the table that holds this part does not reach {table} by any link the map knows, so please write the link as via TABLE.COLUMN = TABLE.COLUMN.",
    "hand_written": "{where}: this binding was written by hand and carries no binding data, so confirm can record yes or not sure for it but cannot change its SQL.",
    "confirmations_headings": "The file of confirmations needs the headings attribute and answer, and may add replacement, by, date and note.",
}


class ProposeError(ValueError):
    """The proposer or a confirmation cannot go on. The message never holds a description from the dictionary."""


# Words.

def stem(word):
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    if word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "aeiouls":
        word = word[:-1]
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return SYNONYMS.get(word, word)


def _spelt(text):
    text = text.lower()
    for british, american in SPELLING:
        text = text.replace(british, american)
    return text


def words(text):
    """The words of a description or a phrase, in order, as stems."""
    return [stem(w) for w in re.findall(r"[a-z]+|[0-9]+", _spelt(text or "")) if w not in STOP and not w.isdigit()]


@lru_cache(maxsize=None)
def name_words(name):
    """The words of a table's or a column's name, with the common abbreviations spelt out, as a tuple."""
    found = []
    for part in re.split(r"[^A-Za-z0-9]+|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", name or ""):
        part = _spelt(part)
        if not part or part.isdigit():
            continue
        if part in ABBREVIATIONS:
            found.extend(stem(w) for w in ABBREVIATIONS[part].split())
        else:
            found.append(stem(part))
            # A flag is often named IS... or HAS... in one word, as ISVALID.
            for prefix in ("is", "has"):
                if part.startswith(prefix) and len(part) > len(prefix) + 3:
                    found.append(stem(part[len(prefix):]))
    return tuple(found)


def _in_order(phrase, sequence, gap=2):
    """Whether the words of a phrase appear in the sequence in order, with at most gap words between neighbours."""
    if not phrase:
        return False
    for start, word in enumerate(sequence):
        if word != phrase[0]:
            continue
        at, ok = start, True
        for wanted in phrase[1:]:
            window = sequence[at + 1:at + 2 + gap]
            if wanted not in window:
                ok = False
                break
            at = at + 1 + window.index(wanted)
        if ok:
            return True
    return False


class _Index:
    """The words of every column and table of a dictionary, for BM25 scoring."""

    def __init__(self, dictionary):
        self.dictionary = dictionary
        self.docs, self.sequence, self.names = [], [], []
        self.postings = defaultdict(list)
        self.tables, self.table_sequence, self.table_postings = [], [], defaultdict(list)
        for table in dictionary.tables():
            for entry in table.columns.values():
                described = words(dictionary.description(table.name, entry.name))
                named = name_words(entry.name)
                counts = Counter(described) + Counter(named) + Counter(named)
                at = len(self.docs)
                self.docs.append((table.name, entry.name, counts, sum(counts.values())))
                self.sequence.append(described)
                self.names.append(named)
                for word in counts:
                    self.postings[word].append(at)
            described = words(dictionary.description(table.name))
            named = name_words(table.name)
            counts = Counter(described) + Counter(named) + Counter(named)
            at = len(self.tables)
            self.tables.append((table.name, counts, sum(counts.values())))
            self.table_sequence.append(described)
            for word in counts:
                self.table_postings[word].append(at)
        self.by_name = {(t.upper(), c.upper()): i for i, (t, c, _, _) in enumerate(self.docs)}
        self.table_at = {name.upper(): i for i, (name, _, _) in enumerate(self.tables)}
        self.average = sum(d[3] for d in self.docs) / max(len(self.docs), 1)
        self.table_average = sum(t[2] for t in self.tables) / max(len(self.tables), 1)

    def idf(self, word, tables=False):
        postings = self.table_postings if tables else self.postings
        n = len(self.tables if tables else self.docs)
        df = len(postings.get(word, ()))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, query, counts, length, average, tables=False):
        score = 0.0
        for word in query:
            f = counts.get(word, 0)
            if f:
                score += self.idf(word, tables) * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * length / average))
        return score

    def score(self, at, query, phrases):
        """(score, matched words, phrase matched) of one column for a role's words and phrases."""
        _, _, counts, length = self.docs[at]
        score = self._bm25(query, counts, length, self.average)
        sequence, named = self.sequence[at], self.names[at]
        phrase = False
        for words_ in phrases:
            if len(words_) >= 2 and (_in_order(words_, sequence) or _in_order(words_, named, gap=0)):
                score += sum(self.idf(w) for w in words_)
                phrase = True
        matched = {w for w in query if counts.get(w)}
        return score, matched, phrase

    def search(self, query, phrases, limit=TOP):
        """The best columns of the whole dictionary for a role's words, as [(score, at)]."""
        seen = set()
        for word in query:
            seen.update(self.postings.get(word, ()))
        scored = [(self.score(at, query, phrases)[0], at) for at in seen]
        scored.sort(key=lambda item: (-item[0], self.docs[item[1]][0], self.docs[item[1]][1]))
        return scored[:limit]

    def table_search(self, query, phrases, limit=TABLES):
        seen = set()
        for word in query:
            seen.update(self.table_postings.get(word, ()))
        scored = []
        for at in seen:
            name, counts, length = self.tables[at]
            score = self._bm25(query, counts, length, self.table_average, tables=True)
            phrase = False
            for words_ in phrases:
                if len(words_) >= 2 and _in_order(words_, self.table_sequence[at]):
                    score += sum(self.idf(w, tables=True) for w in words_)
                    phrase = True
            scored.append((score, name, phrase))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[:limit]


# Types.

def _type_of(data_type):
    text = (data_type or "").upper()
    if not text:
        return "unknown"
    if any(w in text for w in ("DATE", "TIME")):
        return "date" if "TIME" not in text else "datetime"
    if any(w in text for w in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "REAL", "MONEY", "NUMBER", "BIT", "DOUBLE")):
        return "number"
    return "text"


def type_factor(role_type, data_type, name):
    found = _type_of(data_type)
    flagged = bool(re.search(r"(_YN|_FLAG|_FLG|_IND|_YN_C)$", name.upper()))
    if role_type in ("date", "datetime"):
        if found == "unknown":
            return 1.0
        if found in ("date", "datetime"):
            return 1.3 if found == role_type or role_type == "date" else 1.1
        return 0.25
    if found in ("date", "datetime"):
        return 0.2
    if role_type in ("flag", "flag_or_empty"):
        return 1.3 if flagged else 1.0
    if flagged:
        return 0.6
    return 1.0


# Links between tables.

def _core(parts):
    """The words of a key's name without a last word that only says it is an identifier."""
    return parts[:-1] if len(parts) > 1 and parts[-1] in ("ID", "KEY", "NUM", "NO") else parts


class _Graph:
    """The many-to-one links of a dictionary: a column of one table that names the whole primary key of another."""

    def __init__(self, dictionary):
        self.dictionary = dictionary
        self.by_key = defaultdict(list)
        for table in dictionary.tables():
            key = table.primary_key()
            if len(key) == 1:
                self.by_key[key[0].upper()].append(table.name)
        self.tails = defaultdict(list)
        for key in self.by_key:
            core = _core(key.split("_"))
            if len(core) >= 2:
                self.tails[tuple(core[-2:])].append(key)
        self._matches, self._reach = {}, {}

    def matching_keys(self, name):
        """The single-column primary keys that a column's name gives, as [(key, strength)]."""
        name = name.upper()
        if name in self._matches:
            return self._matches[name]
        found = []
        if name in self.by_key:
            found.append((name, 1.0))
        parts = _core(name.split("_"))
        if len(parts) >= 2:
            for key in self.tails.get(tuple(parts[-2:]), ()):
                if key == name:
                    continue
                theirs = _core(key.split("_"))
                shared = 0
                while shared < min(len(parts), len(theirs)) and parts[-1 - shared] == theirs[-1 - shared]:
                    shared += 1
                if shared >= 2 and shared >= len(theirs) - 1:
                    # A name that only ends with a key's name is weaker than the key's own name.
                    found.append((key, 0.5 + 0.35 * shared / len(theirs)))
        self._matches[name] = found
        return found

    def home(self, table, key):
        """How well a table is the home of a key: 1 where its name's words are all in the key's name."""
        mine = set(name_words(table)) - {"identifier"}
        return 1.0 if mine and mine <= set(name_words(key)) else 0.8

    def hops(self, table_name):
        table = self.dictionary.table(table_name)
        own = {k.upper() for k in table.primary_key()}
        for entry in table.columns.values():
            for key, strength in self.matching_keys(entry.name):
                for target in self.by_key[key]:
                    if target.upper() == table.name.upper():
                        continue
                    target_entry = self.dictionary.table(target).column(key)
                    a, b = _type_of(entry.data_type), _type_of(target_entry.data_type if target_entry else "")
                    if "unknown" not in (a, b) and a != b:
                        continue
                    # A table's own whole key leads only to a table of the same thing, one row to one row.
                    weight = strength * self.home(target, key) * (0.95 if entry.name.upper() in own else 1.0)
                    yield entry.name, target, target_entry.name, weight

    def reach(self, base, depth=2, widest=40, avoid=frozenset()):
        """The tables that base reaches by many-to-one links, as {table: (factor, path)}, the base itself included
        with factor 1 and no path. A path is [[from_table, from_column, to_table, to_column], ...]. avoid is a set of
        key names whose tables the path may not pass through."""
        if (base, depth, avoid) in self._reach:
            return self._reach[(base, depth, avoid)]
        found = {base.upper(): (1.0, [], base)}
        frontier = [(1.0, [], base)]
        for level in range(depth):
            following = []
            for factor, path, table in frontier:
                for column, target, key, weight in self.hops(table):
                    if key.upper() in avoid:
                        continue
                    value = factor * weight * HOP
                    step = path + [[table, column, target, key]]
                    held = found.get(target.upper())
                    if held is None or value > held[0] + 1e-9:
                        found[target.upper()] = (value, step, target)
                        following.append((value, step, target))
            following.sort(key=lambda item: (-item[0], item[2]))
            frontier = following[:widest]
        reach = {table: (factor, path) for factor, path, table in found.values()}
        self._reach[(base, depth, avoid)] = reach
        return reach


# The proposal.

@lru_cache(maxsize=None)
def _query_of(name, look_for):
    return _query_uncached({"name": name, "look_for": list(look_for)})


def _query(item):
    """The words of a role and its look_for phrases, as (words, [phrase words]), worked out once for each role."""
    return _query_of(item["name"], tuple(item.get("look_for", [])))


def _query_uncached(item):
    phrases = [words(p) for p in item.get("look_for", [])]
    query = []
    for word in [w for p in phrases for w in p] + list(name_words(item["name"].replace("role_", ""))):
        if word not in query:
            query.append(word)
    return query, phrases


def _fits(candidate, own):
    """Whether a candidate fits a role at all: it holds one of the role's phrases, or two of its words of which one is
    a word of the role's own name, or every word of the role's name in its own name, and its type suits the role."""
    if candidate["factor"] < 1.0:
        return False
    if candidate["phrase"]:
        return True
    if len(candidate["matched"]) >= 2 and candidate["matched"] & own:
        return True
    return bool(own) and own <= set(candidate.get("named", ())) and bool(candidate["matched"])


def _confidence(score, runner_up, phrase, factor, matched):
    margin = score / runner_up if runner_up else float("inf")
    if phrase and factor >= 1.0 and margin >= 1.25:
        return "high"
    if phrase or (len(matched) >= 2 and margin >= 1.1):
        return "medium"
    return "low"


class Proposer:
    """Proposes a draft map for a role model from a dictionary that is already restricted to the catalogue."""

    def __init__(self, dictionary, model=None):
        self.dictionary = dictionary
        self.model = model or rolemap.contract()
        self.index = _Index(dictionary)
        self.graph = _Graph(dictionary)
        self._scores, self._pools = {}, {}

    # One column of a view, in a given base table's reach.

    def _scored(self, view, column, at):
        """A column's own score for a role's column, with no regard to where it lies, worked out once."""
        key = (view["name"], column["name"], at)
        if key not in self._scores:
            query, phrases = _query(column)
            name, col, _, _ = self.index.docs[at]
            score, matched, phrase = self.index.score(at, query, phrases)
            entry = self.dictionary.table(name).column(col)
            factor = type_factor(column["type"], entry.data_type, col)
            avoided = set(words(" ".join(column.get("avoid", [])))) & (set(self.index.sequence[at]) | set(self.index.names[at]))
            factor *= 0.2 ** len(avoided)
            self._scores[key] = (name, col, entry.data_type, score * factor, matched, phrase, factor)
        return self._scores[key]

    def _candidates(self, base, view, column):
        reach = self.graph.reach(base)
        found = {}
        pool_key = (view["name"], column["name"])
        if pool_key not in self._pools:
            query, phrases = _query(column)
            self._pools[pool_key] = [at for _, at in self.index.search(query, phrases)]
        table = self.dictionary.table(base)
        pool = self._pools[pool_key] + [self.index.by_name[(table.name.upper(), c.upper())] for c in table.columns]
        for at in pool:
            name = self.index.docs[at][0]
            held = reach.get(name.upper())
            if held is None or at in found:
                continue
            name, col, data_type, score, matched, phrase, factor = self._scored(view, column, at)
            found[at] = {"table": name, "column": col, "path": held[1], "data_type": data_type,
                         "score": score * held[0], "matched": matched, "phrase": phrase,
                         "factor": factor, "named": self.index.names[at]}
        ranked = sorted(found.values(), key=lambda c: (-c["score"], len(c["path"]), c["table"], c["column"]))
        return ranked

    def _link_candidates(self, base, target, avoid=frozenset()):
        """Columns in base's reach that carry the key of another role, bound to target (table, column). avoid holds
        the keys of the roles that the other role itself links to, such as the patient's key for an anaesthetic: a
        path through the patient reaches every anaesthetic of that patient, and so is no link to one of them."""
        target_table, target_column = target
        reach = self.graph.reach(base, avoid=avoid)
        found = []
        for table_upper, (factor, path) in reach.items():
            table = self.dictionary.table(table_upper)
            for entry in table.columns.values():
                if path and path[-1][3].upper() == entry.name.upper():
                    continue    # the column by which the last join arrived, whose value the table before it holds
                if table.name.upper() == target_table.upper() and entry.name.upper() == target_column.upper():
                    strength = 1.0
                elif entry.name.upper() == target_column.upper():
                    strength = 0.95 if not path else 0.8
                else:
                    strength = max((s for k, s in self.graph.matching_keys(entry.name) if k == target_column.upper()), default=0)
                    strength *= 0.9
                if strength:
                    found.append({"table": table.name, "column": entry.name, "path": path, "data_type": entry.data_type,
                                  "score": strength * factor, "exact": entry.name.upper() == target_column.upper(),
                                  "matched": set(), "phrase": False, "factor": 1.0})
        if not found:
            # The other role's table may point at a table in reach, as an anaesthetic record points at its theatre case.
            # Such a join is one to many in principle, so it is offered last and with low confidence.
            home = self.dictionary.table(target_table)
            for entry in home.columns.values():
                for key, strength in self.graph.matching_keys(entry.name):
                    for reached in self.graph.by_key[key]:
                        held = reach.get(reached.upper())
                        if held is None or reached.upper() == home.name.upper() or len(held[1]) > 1:
                            continue
                        key_name = self.dictionary.table(reached).column(key).name
                        found.append({"table": home.name, "column": self.dictionary.table(home.name).column(target_column).name,
                                      "path": held[1] + [[reached, key_name, home.name, entry.name]],
                                      "data_type": "", "score": 0.5 * strength * held[0], "exact": False, "reverse": True,
                                      "matched": set(), "phrase": False, "factor": 1.0})
        found.sort(key=lambda c: (-c["score"], len(c["path"]), c["table"], c["column"]))
        return found

    def _own_key(self, base, view, column):
        key = self.dictionary.table(base).primary_key()
        if len(key) == 1 and view["key"] == [column["name"]] and column["name"] not in self._links(view):
            entry = self.dictionary.table(base).column(key[0])
            return {"table": self.dictionary.table(base).name, "column": entry.name, "path": [], "data_type": entry.data_type,
                    "score": 1.0, "own_key": True, "matched": set(), "phrase": False, "factor": 1.0}
        return None

    def _links(self, view):
        return {link["column"]: link["to"] for link in view.get("links", [])}

    def _avoid(self, role, bound):
        """The key names of the roles that a role links to, which a path to that role may not pass through."""
        view = next((v for v in self.model["views"] if v["name"] == role), None)
        if view is None:
            return frozenset()
        avoid = set()
        for to in self._links(view).values():
            other, key = to.split(".")
            held = bound.get(other, {}).get(key)
            if held is not None:
                avoid.add(held[1].upper())
        return frozenset(avoid)

    def column(self, base, view, column, bound):
        """The proposal for one column of a view whose rows come from base: {"best", "candidates", "confidence"}."""
        links = self._links(view)
        own = self._own_key(base, view, column)
        if own is not None:
            return {"best": own, "candidates": [], "confidence": "high"}
        if column["name"] in links:
            role, key = links[column["name"]].split(".")
            target = bound.get(role, {}).get(key)
            if target is not None:
                found = self._link_candidates(base, target, self._avoid(role, bound))
                if found:
                    best = found[0]
                    confidence = "low" if best.get("reverse") else "high" if best["exact"] and len(best["path"]) <= 1 \
                        else "medium" if best["exact"] else "low"
                    return {"best": best, "candidates": found[1:1 + SHOWN], "confidence": confidence, "link": (role, key, target)}
            ranked = self._candidates(base, view, column)
            return {"best": None, "candidates": ranked[:SHOWN], "confidence": "none", "link": (role, key, target)}
        ranked = self._candidates(base, view, column)
        own = set(name_words(column["name"]))
        fitting = [c for c in ranked if _fits(c, own)]
        if not fitting:
            return {"best": None, "candidates": ranked[:SHOWN], "confidence": "none"}
        best = fitting[0]
        others = [c for c in fitting[1:] if c["column"].upper() != best["column"].upper()]
        runner_up = others[0]["score"] if others else 0
        return {"best": best, "candidates": [c for c in fitting[1:]][:SHOWN],
                "confidence": _confidence(best["score"], runner_up, best["phrase"], best["factor"], best["matched"])}

    # The table of a view's rows.

    def base(self, view, bound):
        """The proposal for a view's rows: {"table", "score", "phrase", "candidates": [table, ...]}, or None."""
        query, phrases = _query({"name": view["one_row_per"], "look_for": view.get("look_for", []) + [view["one_row_per"]]})
        tables = self.index.table_search(query, phrases)
        best_table = tables[0][0] if tables else 0
        described = {name.upper(): (score, phrase) for score, name, phrase in tables}
        links = self._links(view)
        plain = [c for c in view["columns"] if c["name"] not in links and view["key"] != [c["name"]]]
        candidates = {name.upper() for _, name, _ in tables}
        tops = {}
        for column in plain:
            cq, cp = _query(column)
            hits = self.index.search(cq, cp, limit=TOP)
            tops[column["name"]] = hits[0][0] if hits else 0
            for _, at in hits[:5]:
                candidates.add(self.index.docs[at][0].upper())
        keyed = set()
        if len(view["key"]) == 1 and view["key"][0] in links:
            # A view of one row for each thing of another role, such as a patient's details, is drawn from a table
            # whose key is that role's key, wherever the candidates hold one.
            role, other = links[view["key"][0]].split(".")
            target = bound.get(role, {}).get(other)
            if target is not None:
                keyed = {name for name in candidates if self.dictionary.table(name).primary_key() == (target[1],)}
                candidates = keyed or candidates
        scored = []
        for table_upper in sorted(candidates):
            table = self.dictionary.table(table_upper)
            score, phrase = described.get(table_upper, (0.0, False))
            total = 2.0 * math.sqrt(score / best_table) if best_table else 0.0
            if self._table_words(table.name) & COPIES:
                total -= 1.0
            covered = 0
            for column in plain:
                found = self._candidates(table.name, view, column)
                fitting = [c for c in found if _fits(c, set(name_words(column["name"])))]
                covered += bool(fitting)
                if fitting and tops[column["name"]]:
                    # A column in the table itself counts in full, and one that a join reaches counts for less, so
                    # that a table that reaches the whole patient record does not win by that alone.
                    total += max(min(1.0, c["score"] / tops[column["name"]]) * 0.5 ** len(c["path"]) for c in fitting[:10])
            if len(view["key"]) == 1:
                # A view of one row for each thing needs a table with a key of one column, and where that thing is
                # another role's, such as a patient's details, the key should be that role's key.
                key = table.primary_key()
                total += 0.5 if len(key) == 1 else -1.0
                if view["key"][0] in links and len(key) == 1:
                    role, other = links[view["key"][0]].split(".")
                    target = bound.get(role, {}).get(other)
                    total += 0.5 if target is not None and key[0].upper() == target[1].upper() else -0.5
            for name, to in links.items():
                role, key = to.split(".")
                target = bound.get(role, {}).get(key)
                if target is not None:
                    found = self._link_candidates(table.name, target, self._avoid(role, bound))
                    total += found[0]["score"] if found else 0.0
            scored.append((total, table.name, score, phrase, covered))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not view.get("required"):
            # A further view is drafted only where the table's own description fits it and at least half of the
            # view's other columns fit within its reach.
            own = set(query)
            fitting = {name.upper() for _, name, phrase in tables if phrase or len(self._table_words(name) & own) >= 2}
            fitting |= keyed
            scored = [item for item in scored if item[1].upper() in fitting and 2 * item[4] >= len(plain)]
        if not scored or scored[0][0] <= 0:
            return None
        total, name, score, phrase, _ = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0
        confidence = "high" if phrase and total >= 1.15 * runner_up else "medium" if phrase or total >= 1.15 * runner_up else "low"
        return {"table": name, "phrase": phrase, "confidence": confidence, "candidates": [s[1] for s in scored[1:1 + SHOWN]]}

    def _table_words(self, name):
        return set(self.index.tables[self.index.table_at[name.upper()]][1])

    def propose(self, bases=None):
        """The proposal for every view of the role model, in an order that proposes a role before the roles that link
        to it. bases, when given, is {view: table} for any view whose table a person has already chosen."""
        bases = {k: v for k, v in (bases or {}).items()}
        views = {view["name"]: view for view in self.model["views"]}
        order = []
        while len(order) < len(views):
            ready = [name for name, view in views.items() if name not in order
                     and {to.split(".")[0] for to in self._links(view).values()} - {name} <= set(order)]
            # A cycle of links, which the contract does not have, is broken in the contract's order.
            order += ready or [next(name for name in views if name not in order)]
        bound, found = {}, {}
        for name in order:
            view = views[name]
            if name in bases:
                table = self.dictionary.table(bases[name])
                if table is None:
                    raise ProposeError(f"The dictionary, as the catalogue restricts it, holds no table {bases[name]} for {name}.")
                rows = {"table": table.name, "phrase": False, "confidence": "high", "candidates": [], "chosen": True}
            else:
                rows = self.base(view, bound)
            if rows is None:
                found[name] = None
                continue
            columns = {}
            for column in view["columns"]:
                columns[column["name"]] = self.column(rows["table"], view, column, bound)
            bound[name] = {c: (p["best"]["table"], p["best"]["column"]) for c, p in columns.items() if p["best"] is not None}
            found[name] = {"rows": rows, "columns": columns}
        return {name: found[name] for name in views}


# Wording of the evidence.

def _quote(text, limit=QUOTE):
    text = " ".join((text or "").split()).replace('"', "'").replace("?", ".").replace("!", ".").replace("$(", "$ (")
    text = text.rstrip(" .")
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.")
        text = cut + " ..."
    return text


def _matched_words(description, matched):
    shown = []
    for word in re.findall(r"[A-Za-z]+", description or ""):
        if stem(_spelt(word)) in matched and word.lower() not in STOP and word.lower() not in shown:
            shown.append(word.lower())
    return shown[:4]


def _listed(items):
    items = [f"'{item}'" for item in items]
    if len(items) == 1:
        return f"the word {items[0]}"
    return "the words " + ", ".join(items[:-1]) + " and " + items[-1]


def _fit(sentence, rebuild):
    """A sentence of at most 400 characters, made by shortening its quote where it is too long."""
    limit = QUOTE
    while len(sentence) > 400 and limit > 20:
        limit -= 30
        sentence = rebuild(limit)
    return sentence


def _path_text(path):
    if not path:
        return WORDING["in_table"]
    if len(path) > 2:
        return WORDING["via_one"].format(path=f"{_match_text(path[0])} and {len(path) - 1} further links")
    return WORDING["via_one"].format(path=", then ".join(_match_text(step) for step in path))


def _step_text(step):
    return " and ".join(f"{step[0]}.{a} = {step[2]}.{b}" for a, b in step_pairs(step))


def _match_text(step):
    """A link in words: "A.a to B.b", which a sentence introduces as matching."""
    return " and ".join(f"{step[0]}.{a} to {step[2]}.{b}" for a, b in step_pairs(step))


def _from_text(candidate):
    """Where a binding comes from, on one line: the column, and the joins that reach it."""
    source = f"{candidate['table']}.{candidate['column']}"
    if not candidate["path"]:
        return source
    return source + ", by " + ", then ".join(_step_text(step) for step in candidate["path"])


def _column_says(dictionary, candidate, view_name, column_name, link=None):
    table, column = candidate["table"], candidate["column"]
    description = dictionary.description(table, column)
    if candidate.get("own_key"):
        if not description:
            return WORDING["key_says_bare"].format(table=table, column=column)
        return _fit(WORDING["key_says"].format(table=table, column=column, quote=_quote(description)),
                    lambda n: WORDING["key_says"].format(table=table, column=column, quote=_quote(description, n)))
    if link is not None and candidate.get("reverse"):
        role = rolemap.view_title(link[0], False)
        sentence = WORDING["reverse_says"].format(table=table, column=column, role=role, how=_path_text(candidate["path"]),
                                                  other=candidate["path"][-1][0])
        if len(sentence) > 400:
            sentence = WORDING["reverse_says"].format(table=table, column=column, role=role, how="by its links",
                                                      other=candidate["path"][-1][0])
        return sentence
    if link is not None:
        role, _, (target_table, target_column) = link
        role = rolemap.view_title(role, False)
        how = _path_text(candidate["path"])
        first = f"{candidate['path'][0][0]}.{candidate['path'][0][1]}" if candidate["path"] else f"{table}.{column}"
        first_description = dictionary.description(*first.split(".", 1))
        target = f"{target_table}.{target_column}"
        if not first_description:
            return WORDING["link_says"].format(table=table, column=column, target=target, role=role, how=how)
        build = lambda n: WORDING["link_says_quote"].format(table=table, column=column, target=target, role=role,  # noqa: E731
                                                            how=how, first=first, quote=_quote(first_description, n))
        sentence = _fit(build(QUOTE), build)
        return sentence if len(sentence) <= 400 else WORDING["link_says"].format(table=table, column=column, target=target, role=role, how="by its links")
    if not description:
        return WORDING["column_says_bare"].format(table=table, column=column)
    shown = _matched_words(description, candidate["matched"])
    if not shown:
        return _fit(WORDING["column_says_name"].format(table=table, column=column, quote=_quote(description)),
                    lambda n: WORDING["column_says_name"].format(table=table, column=column, quote=_quote(description, n)))
    return _fit(WORDING["column_says"].format(table=table, column=column, quote=_quote(description), words=_listed(shown)),
                lambda n: WORDING["column_says"].format(table=table, column=column, quote=_quote(description, n), words=_listed(shown)))


def _candidate_entry(dictionary, candidate):
    description = dictionary.description(candidate["table"], candidate["column"])
    entry = {"from": _from_text(candidate)}
    if description:
        entry["words"] = _quote(description) + "."
    return entry


def _binding(candidate):
    return {"table": candidate["table"], "column": candidate["column"], "path": candidate["path"],
            "data_type": candidate.get("data_type", "")}


def draft(proposal, dictionary, model=None, date=None, world="the hospital"):
    """The draft map.json, as data, from a proposal. Views for which no table fits are left out, and the three views
    that every map supplies must each have a table."""
    model = model or rolemap.contract()
    date = (date or dt.date.today()).isoformat() if not isinstance(date, str) else date
    views = {view["name"]: view for view in model["views"]}
    roles = {}
    for name, found in proposal.items():
        view = views[name]
        if found is None:
            if view.get("required"):
                raise ProposeError(WORDING["no_rows_says"].format(view=name))
            continue
        rows = found["rows"]
        table = rows["table"]
        description = dictionary.description(table)
        query, _ = _query({"name": view["one_row_per"], "look_for": view.get("look_for", []) + [view["one_row_per"]]})
        shown = _matched_words(description, set(query))
        what = view["one_row_per"]
        if description and shown:
            says = _fit(WORDING["rows_says"].format(table=table, quote=_quote(description), words=_listed(shown), what=what),
                        lambda n: WORDING["rows_says"].format(table=table, quote=_quote(description, n), words=_listed(shown), what=what))
        else:
            says = WORDING["rows_says_bare"].format(table=table, what=what)
        evidence = {"status": "proposed", "from": table, "says": says,
                    "question": WORDING["rows_question"].format(table=table, what=what),
                    "binding": {"table": table}, "confidence": rows["confidence"],
                    "candidates": [{"from": t} for t in rows["candidates"]]}
        columns = {}
        for column in view["columns"]:
            item = found["columns"][column["name"]]
            best = item["best"]
            candidates = [_candidate_entry(dictionary, c) for c in item["candidates"]]
            if best is None:
                columns[column["name"]] = {
                    "status": "proposed", "from": "nothing in the dictionary fits",
                    "says": WORDING["nothing_says"].format(table=table, about=rolemap.plain_about(f"{name}.{column['name']}")),
                    "question": WORDING["nothing_question"].format(about=rolemap.plain_about(f"{name}.{column['name']}")),
                    "binding": None, "confidence": "none", "candidates": candidates}
                continue
            columns[column["name"]] = {
                "status": "proposed", "from": _from_text(best),
                "says": _column_says(dictionary, best, name, column["name"], item.get("link")),
                "question": WORDING["column_question"].format(table=best["table"], column=best["column"],
                                                              about=rolemap.plain_about(f"{name}.{column['name']}")),
                "binding": _binding(best), "confidence": item["confidence"], "candidates": candidates}
        roles[name] = {"file": f"{name}.sql", "rows": evidence, "columns": columns}
    kind_source = roles["role_reading"]["columns"]["kind"]["from"] if "role_reading" in roles else "the readings"
    kind_source = kind_source.split(",")[0]
    meanings = {item["kind"]: item["meaning"] for item in model["kinds"]}
    kinds = {}
    for kind in rolemap.MEAN_KINDS:
        meaning = meanings[kind][0].lower() + meanings[kind][1:]
        kinds[kind] = {"codes": [], "status": "proposed", "from": kind_source,
                       "says": WORDING["kind_says"].format(kind=kind),
                       "question": WORDING["kind_question"].format(source=kind_source, meaning=meaning)}
    return {"world": world, "description": WORDING["description"].format(date=date), "roles": roles, "kinds": kinds}


# The SQL of a view, from its bindings.

def _name(name):
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else f"[{name}]"


def _literal(text):
    return "'" + str(text).replace("'", "''") + "'"


def plan(column, binding, codes=None):
    """What a view does with the column that a binding names, as a small description that both the SQL writer
    (render) and the check's model of the binding (corrections.evaluate) read, so that the two cannot drift apart.

    It is a tuple whose first item names the operation: raw, date, float, int, const, flag_in, kind, derive_flag,
    scale or trim, with the operation's own settings after it."""
    kind = column["type"]
    data_type = binding.get("data_type", "") if binding else ""
    found = _type_of(data_type)
    derive = (binding or {}).get("derive")
    if derive:
        form = derive["form"]
        if form == "flag":
            return ("derive_flag", tuple(str(v) for v in derive["values"]), kind == "flag_or_empty")
        if form == "scale":
            return ("scale", float(derive.get("factor", 1)), float(derive.get("offset", 0)), kind == "whole")
        if form == "date":
            return ("date",)
        if form == "trim":
            return ("trim",)
    if kind == "date":
        return ("date",)
    if kind == "number":
        return ("float",) if found in ("text", "unknown") else ("raw",)
    if kind == "whole":
        return ("int",) if found in ("text", "unknown") else ("raw",)
    if kind in ("flag", "flag_or_empty") and _category(binding["column"]):
        empty = _empty(column)
        return ("const", None if empty == "NULL" else int(empty))
    if kind in ("flag", "flag_or_empty"):
        numeric = found == "number"
        yes, no = ((1,), (0,)) if numeric else (("Y", "Yes", "1"), ("N", "No", "0"))
        mode = "plain" if kind == "flag" and column.get("if_empty", 0) != 1 else "if_empty" if kind == "flag" else "or_empty"
        return ("flag_in", yes, no, mode)
    if kind == "kind":
        if codes is not None:
            given = tuple((k, tuple(str(x) for x in c)) for k, c in codes.items() if c)
            return ("kind", given, True)
        return ("kind", (), False)
    return ("raw",)


def _list(values):
    return ", ".join(str(v) if isinstance(v, (int, float)) else _literal(v) for v in values)


def _number_text(value):
    value = float(value)
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)


def render(step, ref):
    """The SQL of one planned operation on the column reference ref."""
    op = step[0]
    if op == "raw":
        return ref
    if op == "date":
        return f"CAST({ref} AS date)"
    if op == "float":
        return f"TRY_CAST({ref} AS float)"
    if op == "int":
        return f"TRY_CAST({ref} AS int)"
    if op == "const":
        value = "NULL" if step[1] is None else str(step[1])
        return f"CASE WHEN {ref} IS NULL THEN {value} ELSE {value} END"
    if op == "flag_in":
        _, yes, no, mode = step
        if mode == "plain":
            return f"CASE WHEN {ref} IN ({_list(yes)}) THEN 1 ELSE 0 END"
        if mode == "if_empty":
            return f"CASE WHEN {ref} IN ({_list(no)}) THEN 0 ELSE 1 END"
        return f"CASE WHEN {ref} IN ({_list(yes)}) THEN 1 WHEN {ref} IN ({_list(no)}) THEN 0 END"
    if op == "kind":
        _, given, translated = step
        if not translated:
            return f"CASE WHEN {ref} IS NOT NULL THEN 'other' END"
        if not given:
            return f"CASE WHEN {ref} IS NOT NULL THEN 'other' ELSE 'other' END"
        whens = " ".join(f"WHEN CAST({ref} AS varchar(254)) IN ({', '.join(_literal(x) for x in c)}) THEN {_literal(k)}" for k, c in given)
        return f"CASE {whens} ELSE 'other' END"
    if op == "derive_flag":
        _, values, or_empty = step
        test = f"CAST({ref} AS varchar(254)) IN ({', '.join(_literal(v) for v in values)})"
        if or_empty:
            return f"CASE WHEN {ref} IS NULL THEN NULL WHEN {test} THEN 1 ELSE 0 END"
        return f"CASE WHEN {test} THEN 1 ELSE 0 END"
    if op == "scale":
        _, factor, offset, whole = step
        text = f"TRY_CAST({ref} AS float) * {_number_text(factor)}"
        if offset:
            text += f" + {_number_text(offset)}" if offset > 0 else f" - {_number_text(-offset)}"
        return f"CAST(ROUND({text}, 0) AS int)" if whole else text
    if op == "trim":
        return f"LTRIM(RTRIM(CAST({ref} AS nvarchar(4000))))"
    raise ValueError(op)


def _expression(column, ref, data_type, codes=None, binding=None):
    binding = dict(binding or {})
    binding.setdefault("data_type", data_type)
    binding.setdefault("column", ref.rsplit(".", 1)[-1].strip("[]"))
    return render(plan(column, binding, codes), ref)


def step_pairs(step):
    """The pairs of columns that one step of a path joins, as [(from_column, to_column), ...]: the step's own pair and,
    where the step joins on more than one column, the further pairs it gives as its fifth item."""
    return [(step[1], step[3])] + [tuple(pair) for pair in (step[4] if len(step) > 4 else [])]


def step_key(step):
    return (step[0], step[1], step[2], step[3], tuple(tuple(p) for p in (step[4] if len(step) > 4 else [])))


def walk(path, aliases, joins, prefix=(), outer="LEFT JOIN"):
    """Adds the joins of a path from the alias of prefix, reusing any join that an earlier binding of the view made
    over the same steps, and returns the alias at its end and the prefix that names it."""
    for step in path:
        following = prefix + (step_key(step),)
        if following not in aliases:
            alias = f"t{len(aliases)}"
            aliases[following] = alias
            on = " AND ".join(f"{alias}.{_name(to)} = {aliases[prefix]}.{_name(fr)}" for fr, to in step_pairs(step))
            joins.append(f"{outer} {_name(step[2])} {alias} ON {on}")
        prefix = following
    return aliases[prefix], prefix


def window_on(alias, window, time_ref):
    """The condition by which a row joins the anaesthetic that it shares a key with, within the anaesthetic's window."""
    before, after = int(window.get("before", 0)), int(window.get("after", 0))
    start, stop = f"{alias}.{_name(window['start'])}", f"{alias}.{_name(window['stop'])}"
    low = f"DATEADD(minute, -{before}, {start})" if before else start
    high = f"DATEADD(minute, {after}, {stop})" if after else stop
    return f"{time_ref} >= {low} AND ({stop} IS NULL OR {time_ref} <= {high})"


def joined_sql(joined, on_ref, alias):
    """The subquery that joins several rows back into one text, in order."""
    return (f"(SELECT STRING_AGG(CAST({alias}.{_name(joined['text'])} AS nvarchar(4000)), {_literal(joined.get('separator', ' '))}) "
            f"WITHIN GROUP (ORDER BY {alias}.{_name(joined['order'])}) FROM {_name(joined['table'])} {alias} "
            f"WHERE {alias}.{_name(joined['link'])} = {on_ref})")


def filter_sql(item, ref):
    return f"CAST({ref} AS varchar(254)) IN ({', '.join(_literal(v) for v in item['values'])})"


def _category(ref):
    """Whether a column holds a code of a category list, whose values a person must translate, by its name."""
    return bool(re.search(r"(_CAT|_C|_C_NAME|_CD|_CODE|_TYPE|_KIND)\]?$", ref.upper()))


def _empty(column):
    if column["type"] == "flag":
        return str(column.get("if_empty", 0))
    return "NULL"


def view_sql(name, role, kinds=None, model=None, vocabularies=None):
    """The SQL of one view of a draft map, written from the bindings in its evidence. vocabularies, when given, is
    {column: {kind: [code, ...]}} for any column of a kind other than the readings' whose local codes a person has
    chosen, and the view then translates those codes as it translates the readings' kinds."""
    model = model or rolemap.contract()
    view = next(v for v in model["views"] if v["name"] == name)
    base = role["rows"]["binding"]["table"]
    aliases, joins = {(): "t0"}, []
    lines, nothing, vocabulary = [], [], []
    links = {link["column"] for link in view.get("links", [])}
    anchor = None
    refs, windows = {}, []
    for column in view["columns"]:
        evidence = role["columns"][column["name"]]
        binding = evidence.get("binding")
        if not binding:
            nothing.append(column["name"])
            lines.append(f"{_empty(column)} AS {column['name']}")
            continue
        alias, _ = walk(binding["path"], aliases, joins)
        ref = f"{alias}.{_name(binding['column'])}"
        refs[column["name"]] = ref
        if binding.get("window"):
            # A link by a shared key and a time window waits until every other column is placed, because its join
            # reads the time of the row.
            windows.append((len(lines), column, binding, ref))
            lines.append(None)
            continue
        if binding.get("joined"):
            lines.append(f"{joined_sql(binding['joined'], ref, f'j{len(aliases)}')} AS {column['name']}")
            continue
        codes = None
        if name == "role_reading" and column["name"] == "kind":
            codes = {k: item.get("codes", []) for k, item in (kinds or {}).items()}
        elif column["type"] == "kind" and (vocabularies or {}).get(column["name"]) is not None:
            codes = vocabularies[column["name"]]
        elif not binding.get("derive") and (column["type"] == "kind" or (column["type"] in ("flag", "flag_or_empty") and _category(binding["column"]))):
            vocabulary.append(column["name"])
        lines.append(f"{render(plan(column, binding, codes), ref)} AS {column['name']}")
        if column["name"] in links and column["name"] in view["key"] and anchor is None:
            anchor = ref
    for at, column, binding, shared_ref in windows:
        window = binding["window"]
        alias = f"w{len(aliases)}"
        aliases[("window", column["name"])] = alias
        time_ref = refs.get(window["time"], "NULL")
        joins.append(f"LEFT JOIN {_name(window['table'])} {alias} ON {alias}.{_name(window['key'])} = {shared_ref} AND "
                     f"{window_on(alias, window, time_ref)}")
        ref = f"{alias}.{_name(window['output'])}"
        lines[at] = f"{ref} AS {column['name']}"
        if column["name"] in links and column["name"] in view["key"] and anchor is None:
            anchor = ref
    conditions = [f"{anchor} IS NOT NULL"] if anchor is not None else []
    for item in (role["rows"].get("binding") or {}).get("filter") or []:
        alias, _ = walk(item["path"], aliases, joins)
        conditions.append(filter_sql(item, f"{alias}.{_name(item['column'])}"))
    header = [WORDING["header"].format(view=name, date=role.get("_date", ""))]
    if any(e.get("confirmation") for e in [role["rows"], *role["columns"].values()]):
        header.append(WORDING["header_person"])
    else:
        header.append(WORDING["header_none"])
    if nothing:
        header.append(WORDING["header_nothing"].format(columns=_and(nothing), them="it" if len(nothing) == 1 else "them"))
    if vocabulary:
        header.append(WORDING["header_vocabulary"].format(columns=_and(vocabulary)))
    if name == "role_reading" and not any((kinds or {}).get(k, {}).get("codes") for k in rolemap.MEAN_KINDS):
        header.append(WORDING["header_codes"])
    sql = "\n".join(header) + "\nSELECT " + ",\n       ".join(lines) + f"\nFROM   {_name(base)} t0"
    if joins:
        sql += "\n       " + "\n       ".join(joins)
    if conditions:
        sql += "\nWHERE  " + "\n  AND  ".join(conditions)
    return sql + "\n"


def _and(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# Writing a draft map, and reading back.

def _public(folder):
    try:
        relative = Path(folder).resolve().relative_to(ROOT)
    except ValueError:
        return False
    return bool(relative.parts) and relative.parts[0] in PUBLIC


def write(data, folder, date, invented=False, model=None):
    """Writes map.json and one SQL file for each role of the draft. Refuses a published folder unless the dictionary
    is invented."""
    folder = Path(folder)
    if _public(folder) and not invented:
        raise ProposeError(WORDING["public"].format(folder=folder))
    folder.mkdir(parents=True, exist_ok=True)
    for name in rolemap.all_views():
        stale = folder / f"{name}.sql"
        if name not in data["roles"] and stale.exists():
            stale.unlink()
    for name, role in data["roles"].items():
        role["_date"] = date
        (folder / f"{name}.sql").write_text(view_sql(name, role, data["kinds"], model), encoding="utf-8")
        role.pop("_date")
    (folder / rolemap.MAP_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def propose_map(dictionary, catalogue, out, model=None, date=None, world="the hospital", bases=None, invented=False):
    """The whole proposal: restricts the dictionary to the catalogue, proposes, writes the draft map to out and reads
    it back with the map checker. Returns (proposal, checked map, number of dictionary columns that the catalogue
    lacks)."""
    restricted = dictionary.restricted_to(catalogue)
    proposer = Proposer(restricted, model)
    proposal = proposer.propose(bases)
    date = date or dt.date.today().isoformat()
    data = draft(proposal, restricted, model, date, world)
    write(data, out, date, invented, model)
    return proposal, rolemap.read_map(out, catalogue), restricted.missing


# Confirmations.

ANSWERS = {"yes": "yes", "y": "yes", "no": "no", "n": "no", "not sure": "not sure", "unsure": "not sure", "not_sure": "not sure"}


def read_confirmations(source):
    """The rows of a file of confirmations, CSV or tab-separated, with the headings attribute and answer, and
    optionally replacement, by, date and note."""
    text = decode(Path(source).read_bytes()) if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source) else source
    first = text.split("\n", 1)[0]
    delimiter = "\t" if first.count("\t") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames or []}
    if not {"attribute", "answer"} <= set(fields):
        raise ProposeError(WORDING["confirmations_headings"])
    rows = []
    for number, row in enumerate(reader, 2):
        cell = lambda key: (row.get(fields.get(key, "")) or "").strip()  # noqa: E731
        if not cell("attribute"):
            continue
        rows.append({"line": number, "attribute": cell("attribute"), "answer": cell("answer"),
                     "replacement": cell("replacement"), "by": cell("by"), "date": cell("date"), "note": cell("note")})
    return rows


def _parse_replacement(text, where):
    match = re.fullmatch(r"\s*([^.\s]+)\.([^.\s]+)\s*(?:via\s+(.+))?", text, flags=re.IGNORECASE)
    if not match:
        raise ProposeError(WORDING["replacement"].format(where=where))
    table, column, via = match.groups()
    path = []
    for part in re.split(r"\s*,\s*|\s+then\s+", via or ""):
        if not part:
            continue
        step = re.fullmatch(r"([^.\s]+)\.([^.\s=]+)\s*=\s*([^.\s]+)\.([^.\s]+)", part)
        if not step:
            raise ProposeError(WORDING["replacement"].format(where=where))
        path.append(list(step.groups()))
    for name in (table, column, *[n for s in path for n in s]):
        if not NAME.match(name):
            raise ProposeError(WORDING["replacement"].format(where=where))
    return table, column, path, via is not None


def _known_paths(role):
    """The joins that a view already makes, as {table: path}, its own table included."""
    paths = {role["rows"]["binding"]["table"].upper(): []}
    for evidence in role["columns"].values():
        binding = evidence.get("binding")
        if binding:
            for at in range(len(binding["path"]) + 1):
                path = binding["path"][:at]
                table = path[-1][2] if path else role["rows"]["binding"]["table"]
                paths.setdefault(table.upper(), path)
    return paths


def confirm(folder, confirmations, catalogue=None, dictionary=None, today=None, model=None):
    """Applies a file of confirmations to a map folder, rewrites map.json and the SQL of every view that changed, and
    checks the result. Returns {"yes", "no", "not sure"} counts and the checked map."""
    folder = Path(folder)
    data = rolemap.read_map_json(folder)
    today = today or dt.date.today().isoformat()
    counts = Counter()
    changed = set()
    restricted = dictionary.restricted_to(catalogue) if dictionary is not None and catalogue is not None else dictionary
    graph = _Graph(restricted) if restricted is not None else None
    for row in read_confirmations(confirmations):
        where = f"line {row['line']}"
        answer = ANSWERS.get(row["answer"].lower())
        if answer is None:
            raise ProposeError(WORDING["answer_unknown"].format(where=where, answer=row["answer"] or "an empty answer"))
        about = row["attribute"]
        date = row["date"] or today
        record = {"answer": answer, "date": date}
        if row["by"]:
            record["by"] = row["by"][:100]
        if row["note"]:
            record["note"] = " ".join(row["note"].split())[:400]
        kind = re.fullmatch(r"kind\s+(\S+)", about)
        rows_of = re.fullmatch(r"(role_\w+)(?:\s+rows)?", about)
        column_of = re.fullmatch(r"(role_\w+)\.(\w+)", about)
        if kind:
            item = data["kinds"].get(kind.group(1))
            if item is None:
                raise ProposeError(WORDING["attribute_unknown"].format(where=where, about=about))
            if answer == "no" and row["replacement"]:
                item["codes"] = [c for c in re.split(r"[\s,;]+", row["replacement"]) if c]
                item["says"] = WORDING["codes_says"].format(codes=", ".join(item["codes"]), date=date)
                record["replacement"] = ", ".join(item["codes"])
            _settle(item, answer, row["replacement"])
            item["confirmation"] = record
            changed.add("role_reading")
            counts[answer] += 1
            continue
        if column_of:
            view, name = column_of.groups()
            role = data["roles"].get(view)
            item = role["columns"].get(name) if role else None
        elif rows_of:
            view, name = rows_of.group(1), None
            role = data["roles"].get(view)
            item = role["rows"] if role else None
        else:
            role = item = None
        if item is None:
            raise ProposeError(WORDING["attribute_unknown"].format(where=where, about=about))
        if answer == "no" and name is None and row["replacement"]:
            raise ProposeError(WORDING["replacement_rows"].format(where=where, view=view))
        if answer == "no" and name is not None:
            if "binding" not in item:
                raise ProposeError(WORDING["hand_written"].format(where=where))
            if row["replacement"]:
                table, column, path, given = _parse_replacement(row["replacement"], where)
                if not given:
                    known = _known_paths(role)
                    if table.upper() in known:
                        path = known[table.upper()]
                    elif graph is not None and restricted.table(role["rows"]["binding"]["table"]) is not None:
                        reach = graph.reach(role["rows"]["binding"]["table"])
                        if table.upper() not in reach:
                            raise ProposeError(WORDING["unreachable"].format(where=where, table=table))
                        path = reach[table.upper()][1]
                    else:
                        raise ProposeError(WORDING["unreachable"].format(where=where, table=table))
                data_type = ""
                if restricted is not None and restricted.table(table) is not None and restricted.table(table).column(column):
                    data_type = restricted.table(table).column(column).data_type
                elif catalogue is not None and catalogue.table(table) is not None and catalogue.table(table).column(column):
                    data_type = catalogue.table(table).column(column).data_type
                candidate = {"table": table, "column": column, "path": path, "data_type": data_type}
                item["binding"] = _binding(candidate)
                item["from"] = _from_text(candidate)
                description = restricted.description(table, column) if restricted is not None else ""
                source = f"{table}.{column}"
                item["says"] = _fit(WORDING["replaced_says_quote"].format(source=source, date=date, quote=_quote(description)),
                                    lambda n: WORDING["replaced_says_quote"].format(source=source, date=date, quote=_quote(description, n))) \
                    if description else WORDING["replaced_says"].format(source=source, date=date)
                record["replacement"] = row["replacement"]
            else:
                item["binding"] = None
                item["from"] = "a person found that the proposal does not hold it"
                item["says"] = WORDING["no_says"].format(date=date)
            changed.add(view)
        _settle(item, answer, row["replacement"])
        item["confirmation"] = record
        counts[answer] += 1
    for name, role in data["roles"].items():
        if name in changed:
            if "binding" not in role["rows"]:
                continue
            role["_date"] = _proposed_on(data)
            (folder / role["file"]).write_text(view_sql(name, role, data["kinds"], model), encoding="utf-8")
            role.pop("_date")
    (folder / rolemap.MAP_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return counts, rolemap.read_map(folder, catalogue)


def _proposed_on(data):
    found = re.search(r"\d{4}-\d{2}-\d{2}", data.get("description", ""))
    return found.group(0) if found else ""


def _settle(item, answer, replacement):
    if answer == "yes" or (answer == "no" and replacement):
        item["status"] = "person"
        item.pop("question", None)
    elif answer == "no":
        item["status"] = "proposed"
        item["question"] = "Please name the table and column that hold this role, or say that the hospital does not record it."
