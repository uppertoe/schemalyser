"""Screen 1, describing the record: the hospital folder, made once for each hospital in one sitting.

A clinician and a colleague who can run SQL against the reporting database load the vendor's data dictionary, let the
proposer draft a map from it and the role model, check with one query which of the proposed tables and columns exist
and how large they are, confirm each binding, settle the local codes of each vocabulary, and run a few counts. What
they settle is written to the hospital folder:

    map/map.json                    every binding, its evidence quoting the dictionary, its confirmation and its date
    map/role_*.sql                  one SQL view for each role, written from the bindings and the codes
    map/tables-and-columns.tsv      the result of the tables and columns query, which says what exists and how large
    codes/VIEW.COLUMN.json          the codes chosen for each kind of a vocabulary, with the list they were chosen from
    counts/NAME.tsv                 the result of each count, as pasted
    counts/judgements.json          whether each count looked right, with a note and its date
    dictionary/                     the dictionary's own files, only where a person ticked the box to keep them
    settings.json                   the tool's version, the dates, the database and the year of the lists

The dictionary is licensed. It is read here, in the browser's worker or on the hospital's own machine, and nothing
from it leaves except into the hospital folder: map.json quotes it as evidence, and the dictionary's own files are
copied only where a person asks. No SQL that this module writes quotes a description, and no message holds one.

Every query that this module writes for production reads the small tables only, or is a two-part script: part 1 puts
at most COHORT_LIMIT anaesthetics of one year into #cohort from the anaesthetic's own tables, and part 2 reaches the
larger table from #cohort by its keys alone, joining the table of readings last, so that SQL Server cannot read the
readings before the cohort has been narrowed (see scripts.py for why).
"""
import csv
import datetime as dt
import io
import json
import hashlib
import re
import tempfile
import zipfile
from pathlib import Path

from . import datadict, first_ask, propose, rolemap
from .catalogue import NAME, QUERY_ORDER, Catalogue, CatalogueError

# A table of at least this many rows is marked as large, and no count on this screen reads it in full.
LARGE = 10_000_000
# The most anaesthetics that part 1 of a script puts into #cohort.
COHORT_LIMIT = 5000
# The fewest rows that a count shows; a smaller count is left blank, and every count is rounded down to tens.
LEAST = 10
FOLDER_FORMAT = 1
DICTIONARY_FOLDER = "dictionary"
SAFE_COUNTS = ("coverage_by_year", "repeated_keys")
DROP = "IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;"
# A label column of a lookup table, by the words of its name.
LABEL_WORDS = {"name", "label", "title", "display", "disp", "description"}

WORDING = {
    "headings": "Schemalyser could not find a heading for the {fields} in the dictionary's first row. Please name the "
                "heading under Name the headings yourself, then load the file again.",
    "cohort_comment": "Part 1 puts into #cohort at most {limit} anaesthetics that started in {year}, the earliest first, "
                      "from {tables}, which hold one row for each anaesthetic or fewer. #cohort is a temporary table that "
                      "exists only in your own SQL window and disappears when you close it. Nothing else is made or changed.",
    "timeout": "Before you run this script, set a time limit: open the Query menu, choose Query Options, then Execution, "
               "and enter a number of seconds in Execution time-out. Run the whole script; part 1 finishes first.",
    "charted_comment": "Part 2 lists every code of {column} charted on the anaesthetics in #cohort, with the number of "
                       "rows and of anaesthetics for each, rounded down to ten and left blank under ten, and the name "
                       "that {lookup} gives each code, most charted first. It reaches {path} from #cohort by their keys, "
                       "so that it reads only the rows of those anaesthetics.",
    "charted_comment_bare": "Part 2 lists every code of {column} charted on the anaesthetics in #cohort, with the number "
                            "of rows and of anaesthetics for each, rounded down to ten and left blank under ten, most "
                            "charted first. It reaches {path} from #cohort by their keys, so that it reads only the rows "
                            "of those anaesthetics.",
    "readings_comment": "Part 2 counts the readings of the anaesthetics in #cohort by their kind, as the map translates "
                        "them, with how many were accepted and hold a number, rounded down to ten and left blank under "
                        "ten. It reaches {path} from #cohort by their keys, so that it reads only those readings.",
    "safe_comment": "This count reads {tables} and no table of readings. Each count is rounded down to ten, and a year "
                    "or a group with fewer than ten is left out.",
    "names": "The map names the tables and local codes of the hospital's database, so this query is for use inside the "
             "hospital only.",
    "codes_says": "A person chose {count} local {codes} for this kind from the list of what is charted on {date}.",
    "codes_none": "No local code has been chosen for this kind yet.",
    "codes_question": "Please choose the local codes of this kind from the list of what is charted.",
    "not_found": "The dictionary holds no column {name}.",
    "not_found_catalogue": "The result of the tables and columns query holds no column {name}.",
    "not_a_name": "Please write the replacement as TABLE.COLUMN, such as the name of a table, a full stop and the name of one of its columns.",
    "no_dictionary": "Please load the dictionary in step 2 first, because Schemalyser needs it to find how the view reaches that table.",
    "no_table": "The dictionary holds no table {name}.",
    "unreachable": "The view's table does not reach {table} by any link that the dictionary shows, so Schemalyser cannot use that column here.",
    "unknown_count": "Schemalyser does not know a count named {name}.",
    "grid_columns": "The pasted text does not have the columns that the query returns ({wanted}). Please copy the whole results grid with Copy with Headers, and paste it again.",
    "grid_empty": "The pasted text holds no rows. If the query returned no rows, the grid is empty; otherwise please copy the whole results grid with Copy with Headers, and paste it again.",
    "folder_unreadable": "Schemalyser could not read map.json in the chosen folder as a map, so it has started a new hospital folder instead.",
    "cliff": "In {year}, {count} of {total} anaesthetics {what}, against {best_count} of {best_total} in {best_year}. A fall as sharp as this usually means that the data is held differently in that year.",
    "repeated": "{view} holds {count} keys that more than one row holds.",
    "stamp_query": "Written by Schemalyser {version} on {date}.",
    "stamp_file": "Written by Schemalyser {version} on {date}.",
    "stamp_result": "Pasted into Schemalyser {version} on {date}. The lines below are the result exactly as it was pasted.",
    "check_confirmation": "Schemalyser could not apply the recorded answer for {about} again: {problem}",
    "check_codes": "Schemalyser could not apply the recorded codes of {key} again.",
    "differs": "{about} differs: the folder holds {before}, and the rebuilt map holds {after}.",
    "only_folder": "The folder's map holds {about}, and the rebuilt map does not.",
    "only_rebuilt": "The rebuilt map holds {about}, and the folder's map does not.",
    "table_gone": "The table {table} was in the earlier result and is not in the new one.",
    "column_gone": "The column {column} was in the earlier result and is not in the new one.",
    "table_new": "The table {table} is in the new result and was not in the earlier one.",
    "size_changed": "{table} held about {before} rows and now holds about {after}.",
    "row_gone": "The row {key} was in the earlier result and is not in the new one.",
    "row_new": "The row {key} is in the new result and was not in the earlier one.",
    "count_changed": "{column} of the row {key} was {before} and is now {after}, a change of more than a tenth.",
}
FIGURES = {"with_patient": "have a patient whom the map finds", "with_birth_date": "have a patient with a date of birth",
           "with_stop": "have a recorded stop"}
COUNT_COLUMNS = {
    "coverage_by_year": ("start_year", "anaesthetics", "with_patient", "with_birth_date", "with_death_date", "test_patients",
                         "with_stop", "stop_before_start"),
    "repeated_keys": ("role_view", "keys_repeated", "rows_held"),
    "readings_by_kind": ("kind", "readings", "accepted", "with_value", "anaesthetics"),
}
CHARTED_COLUMNS = ("code", "charted", "anaesthetics", "name")


class DescribeError(ValueError):
    """Something a person gave cannot be used. The message is meant for the page and never holds a description."""


def _today():
    return dt.date.today().isoformat()


def _text(data):
    if data is None:
        return None
    if isinstance(data, str):
        return data
    from .extract import decode
    return decode(bytes(data))


def _name(name):
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else f"[{name}]"


def _lit(text):
    return "'" + str(text).replace("'", "''") + "'"


def _and(items):
    items = list(items)
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _now():
    return dt.datetime.now().isoformat(timespec="minutes")


def _wrap(sentence):
    import textwrap
    return "\n".join(f"-- {line}" for line in textwrap.wrap(sentence, 110))


# Pasted grids.

def read_grid(text, wanted=None):
    """A grid copied from SQL Server Management Studio with its headers, or a CSV, as (columns, rows). wanted, when
    given, is the columns that the query returns, all of which the heading must hold. Raises DescribeError."""
    text = (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if "\t" in text:
        rows = [[cell.strip() for cell in line.split("\t")] for line in text.split("\n")]
    else:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]
    rows = [r for r in rows if r and any(r) and not (len(r) == 1 and re.fullmatch(r"\(\d+ rows? affected\)", r[0]))
            and not all(re.fullmatch(r"-*", c) for c in r)]
    if not rows:
        raise DescribeError(WORDING["grid_empty"])
    columns = [c.lower() for c in rows[0]]
    if wanted is not None and not set(wanted) <= set(columns):
        raise DescribeError(WORDING["grid_columns"].format(wanted=", ".join(wanted)))
    body = [["" if c == "NULL" else c for c in (r + [""] * len(columns))[:len(columns)]] for r in rows[1:]]
    if wanted is not None:
        at = [columns.index(c) for c in wanted]
        columns, body = list(wanted), [[r[i] for i in at] for r in body]
    return columns, body


def _tsv(columns, rows):
    return "\n".join("\t".join(str(c) for c in row) for row in [columns, *rows]) + "\n"


def _number(text):
    text = str(text or "").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value == int(value) else value


# The binding of a view, as data.

def _parse_from(text):
    """A candidate's from text ("T.C" or "T.C, by A.B = C.D, then ...") as a replacement for confirm ("T.C via ...")."""
    head, _, joins = text.partition(", by ")
    return head + (" via " + joins if joins else "")


def _binding_tables(binding):
    if not binding:
        return []
    tables = [binding["table"]] if "table" in binding else []
    for step in binding.get("path") or []:
        tables += [step[0], step[2]]
    return tables


def _candidate_tables(item):
    found = []
    for candidate in item.get("candidates") or []:
        head = candidate["from"].split(",")[0]
        found.append(head.split(".")[0])
        for step in re.findall(r"([A-Za-z0-9_]+)\.[A-Za-z0-9_]+ = ([A-Za-z0-9_]+)\.", candidate["from"]):
            found += list(step)
    return found


class Describe:
    """What one sitting has settled. One instance lives in the worker, and the page asks it for its model."""

    def __init__(self):
        self.model = rolemap.contract()
        self.views = {view["name"]: view for view in self.model["views"]}
        self.dictionary = None
        self.dictionary_files = None
        self.proposer = None
        self.data = None
        self.catalogue = None
        self.catalogue_text = ""
        self.sizes = {}
        self.codes = {}
        self.counts = {}
        self.settings = {"format": FOLDER_FORMAT, "made": None, "updated": None, "database": None, "year": None}
        self.restored = None
        self._scratch = None
        self._lookups = {}
        self.version = ""
        self.journal = {}
        self.queries = {}
        self.results = {}
        self.confirmations = []
        self.dictionary_entry = None
        self.folder = None

    # The dictionary.

    def load_dictionary(self, data, tables=None, headings=None, name="dictionary.csv", tables_name="tables.csv", step=""):
        own = {k: v for k, v in (headings or {}).items() if v}
        try:
            dictionary = datadict.load(bytes(data), bytes(tables) if tables is not None else None, own)
        except datadict.DictionaryError as error:
            message = str(error)
            first = _text(bytes(data)[:20000]).split("\n", 1)[0]
            delimiter = max(("\t", ",", ";", "|"), key=first.count)
            found = [h.strip() for h in next(csv.reader(io.StringIO(first), delimiter=delimiter), [])][:60]
            found = [h for h in found if len(h) <= 64 and re.fullmatch(r"[\w .()/-]+", h)]
            lacking = re.match(r"Schemalyser could not find a heading for the (.+?) in", message)
            if lacking:
                message = WORDING["headings"].format(fields=lacking.group(1))
            elif message.startswith("The dictionary has no heading"):
                message = message + " Please check the heading's spelling, then load the file again."
            raise DescribeError(message) from None
        self.dictionary = dictionary
        self.proposer = None
        self._lookups = {}
        self.dictionary_files = {"name": _safe_file(name, "dictionary.csv"), "data": bytes(data),
                                 "tables_name": _safe_file(tables_name, "tables.csv") if tables is not None else None,
                                 "tables_data": bytes(tables) if tables is not None else None, "headings": own}
        receipt = self.dictionary_receipt()
        self.dictionary_entry = {"name": "dictionary", "step": step, "file": self.dictionary_files["name"],
                                 "bytes": len(self.dictionary_files["data"]),
                                 "sha256": hashlib.sha256(self.dictionary_files["data"]).hexdigest(),
                                 "tables_file": self.dictionary_files["tables_name"],
                                 "tables_bytes": len(tables) if tables is not None else None,
                                 "tables_sha256": hashlib.sha256(bytes(tables)).hexdigest() if tables is not None else None,
                                 "tables": receipt["tables"], "columns": receipt["columns"], "loaded": _now(),
                                 "version": self.version}
        return receipt

    def dictionary_receipt(self):
        if self.dictionary is None:
            return None
        tables = list(self.dictionary.tables())
        described = sum(1 for t in tables for c in t.columns.values() if self.dictionary.description(t.name, c.name))
        keyed = sum(1 for t in tables if t.primary_key())
        return {"tables": len([t for t in tables if t.columns]), "columns": self.dictionary.column_count(),
                "described": described, "keyed": keyed, "skipped": self.dictionary.skipped,
                "file": self.dictionary_files["name"] if self.dictionary_files else "",
                "tablesFile": (self.dictionary_files or {}).get("tables_name")}

    # The proposal.

    def propose(self, progress=None, date=None):
        if self.dictionary is None:
            raise DescribeError(WORDING["no_dictionary"])
        date = date or _today()
        total = len(self.views)
        if progress:
            progress(0, total)
        if self.proposer is None:
            self.proposer = propose.Proposer(self.dictionary, self.model)
        proposer = self.proposer
        done = [0]
        plain_base = propose.Proposer.base

        def counted(view, bound):
            found = plain_base(proposer, view, bound)
            done[0] += 1
            if progress:
                progress(done[0], total)
            return found
        proposer.base = counted
        try:
            proposal = proposer.propose()
        finally:
            del proposer.base
        data = propose.draft(proposal, self.dictionary, self.model, date, "the hospital")
        self.data = data
        self.settings["made"] = self.settings["made"] or date
        self.settings["updated"] = date
        return self.view()

    def repropose(self, view_name, table, date=None):
        """Proposes one view again from a table that a person chose for its rows, keeping every other view."""
        if self.dictionary is None:
            raise DescribeError(WORDING["no_dictionary"])
        if not NAME.match(table or "") or self.dictionary.table(table) is None:
            raise DescribeError(WORDING["no_table"].format(name=table))
        if self.proposer is None:
            self.proposer = propose.Proposer(self.dictionary, self.model)
        bases = {name: role["rows"]["binding"]["table"] for name, role in self.data["roles"].items()
                 if role["rows"].get("binding")}
        bases[view_name] = self.dictionary.table(table).name
        proposal = self.proposer.propose(bases)
        drafted = propose.draft(proposal, self.dictionary, self.model, date or _today(), "the hospital")
        role = drafted["roles"][view_name]
        role["rows"]["status"] = "person"
        role["rows"].pop("question", None)
        role["rows"]["confirmation"] = {"answer": "no", "date": date or _today(), "replacement": bases[view_name]}
        self.data["roles"][view_name] = role
        self.codes = {k: v for k, v in self.codes.items() if not k.startswith(view_name + ".")}

    # The tables and columns query.

    def tables_named(self):
        names = set()
        for name, role in (self.data or {}).get("roles", {}).items():
            names.add(role["rows"]["binding"]["table"] if role["rows"].get("binding") else role["rows"]["from"])
            for item in [role["rows"], *role["columns"].values()]:
                names.update(_binding_tables(item.get("binding")))
                names.update(_candidate_tables(item))
        for entry in self.vocabularies():
            if entry.get("lookup"):
                names.add(entry["lookup"][0])
        return sorted({n for n in names if n and NAME.match(n)}, key=str.upper)

    def tables_query(self, step=""):
        names = self.tables_named()
        sql = first_ask.query(names)
        return {"sql": self.offer("tables-and-columns", step, sql) if sql else "", "tables": len(names)}

    def read_tables(self, text, record=True):
        try:
            rows = first_ask._rows(text)
        except first_ask.FirstAskError:
            raise DescribeError("unreadable") from None
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(QUERY_ORDER)
        writer.writerows(row[:len(QUERY_ORDER)] for row in rows)
        try:
            catalogue = Catalogue.from_csv(out.getvalue())
        except CatalogueError:
            raise DescribeError("unreadable") from None
        if not list(catalogue.tables()):
            raise DescribeError("unreadable")
        sizes = {}
        for row in rows:
            if catalogue.table(row[1]) is not None and row[-1].isdecimal():
                sizes[catalogue.table(row[1]).name.upper()] = int(row[-1])
        self.catalogue, self.sizes = catalogue, sizes
        self.catalogue_text = _tsv(list(first_ask.LAYOUT), rows)
        if record:
            self.pasted("tables-and-columns", text)
        asked = set(n.upper() for n in self.tables_named())
        held = {t.name.upper() for t in catalogue.tables()}
        return {"tables": len(held), "columns": sum(len(t.columns) for t in catalogue.tables()), "sized": len(sizes),
                "asked": len(asked), "absent": len(asked - held), "doubt": first_ask.doubt(asked, held)}

    def presence(self, binding):
        """Whether a binding's tables and columns exist, and how large its tables are, once the query has been read."""
        if self.catalogue is None or not binding:
            return None
        missing, large = [], []
        table = binding.get("table")
        columns = [(table, binding["column"])] if binding.get("column") else []
        for step in binding.get("path") or []:
            columns += [(step[0], step[1]), (step[2], step[3])]
        tables = [table] + [s[2] for s in binding.get("path") or []]
        for name in dict.fromkeys(t for t in tables if t):
            if self.catalogue.table(name) is None:
                missing.append(name)
            elif self.sizes.get(name.upper(), 0) >= LARGE:
                large.append([self.catalogue.table(name).name, self.sizes[name.upper()]])
        for name, column in dict.fromkeys(columns):
            held = self.catalogue.table(name)
            if held is not None and held.column(column) is None:
                missing.append(f"{name}.{column}")
        rows = self.sizes.get((table or "").upper())
        state = "missing" if missing else "large" if large else "present"
        return {"state": state, "missing": missing, "large": large, "rows": rows}

    # Confirmations.

    def confirm(self, about, answer, replacement="", note="", date=None):
        """Records a person's answer for one binding: yes, no with a replacement, or not sure."""
        date = date or _today()
        replacement = (replacement or "").strip()
        rows_of = re.fullmatch(r"(role_\w+) rows", about)
        if answer == "no" and rows_of:
            self.repropose(rows_of.group(1), replacement.split(".")[0], date)
            self.settings["updated"] = date
            self._log_confirmation(about, answer, self.data["roles"][rows_of.group(1)]["rows"]["binding"]["table"], note, date)
            return
        if answer == "no":
            replacement = self._checked_replacement(about, replacement)
        folder = self._work_folder()
        line = io.StringIO()
        writer = csv.writer(line, lineterminator="\n")
        writer.writerow(["attribute", "answer", "replacement", "date", "note"])
        writer.writerow([about, answer, replacement, date, note])
        try:
            propose.confirm(folder, line.getvalue(), None, self._restricted() if answer == "no" else None, date, self.model)
        except propose.ProposeError as error:
            message = str(error).split(": ", 1)[-1]
            raise DescribeError(message[0].upper() + message[1:]) from None
        self.data = rolemap.read_map_json(folder)
        self.settings["updated"] = date
        self._log_confirmation(about, answer, replacement, note, date)

    def _restricted(self):
        if self.dictionary is None:
            return None
        return self.dictionary

    def _checked_replacement(self, about, replacement):
        match = re.fullmatch(r"([^.\s]+)\.([^.\s]+)(\s+via\s+.+)?", replacement)
        if not match:
            raise DescribeError(WORDING["not_a_name"])
        table, column, via = match.groups()
        if not (NAME.match(table) and NAME.match(column)):
            raise DescribeError(WORDING["not_a_name"])
        if self.dictionary is not None:
            held = self.dictionary.table(table)
            if held is None or held.column(column) is None:
                raise DescribeError(WORDING["not_found"].format(name=f"{table}.{column}"))
            table, column = held.name, held.column(column).name
            if not via:
                view = about.split(".")[0]
                base = self.data["roles"][view]["rows"]["binding"]["table"]
                known = propose._known_paths(self.data["roles"][view])
                if table.upper() not in known:
                    graph = self.proposer.graph if self.proposer is not None else propose._Graph(self.dictionary)
                    if table.upper() not in graph.reach(base):
                        raise DescribeError(WORDING["unreachable"].format(table=table))
        elif self.catalogue is not None:
            held = self.catalogue.table(table)
            if held is None or held.column(column) is None:
                raise DescribeError(WORDING["not_found_catalogue"].format(name=f"{table}.{column}"))
        else:
            raise DescribeError(WORDING["no_dictionary"])
        return f"{table}.{column}" + (via or "")

    def tally(self):
        counts = {"confirmed": 0, "corrected": 0, "not_sure": 0, "remaining": 0, "total": 0}
        for _, item in self._items():
            counts["total"] += 1
            answer = (item.get("confirmation") or {}).get("answer")
            if answer == "yes":
                counts["confirmed"] += 1
            elif answer == "no" and item["status"] == "person":
                counts["corrected"] += 1
            elif answer == "not sure":
                counts["not_sure"] += 1
            else:
                counts["remaining"] += 1
        return counts

    def _items(self):
        for name, role in (self.data or {}).get("roles", {}).items():
            yield f"{name} rows", role["rows"]
            for column, item in role["columns"].items():
                yield f"{name}.{column}", item

    def questions(self):
        return [{"about": about, "question": item.get("question") or ""} for about, item in self._items()
                if (item.get("confirmation") or {}).get("answer") == "not sure"]

    # The codes.

    def _lookup(self, table, column):
        """The lookup table of a code column and its label column, from the dictionary: a table whose one-column
        primary key is the code's name, and a column of it whose name or description says that it is a name."""
        if self.dictionary is None:
            return None
        if (table.upper(), column.upper()) in self._lookups:
            return self._lookups[(table.upper(), column.upper())]
        self._lookups[(table.upper(), column.upper())] = None
        found = []
        for other in self.dictionary.tables():
            key = other.primary_key()
            if len(key) != 1 or key[0].upper() != column.upper() or other.name.upper() == table.upper():
                continue
            for entry in other.columns.values():
                if entry.name.upper() == column.upper():
                    continue
                words = set(propose.name_words(entry.name))
                if words & LABEL_WORDS:
                    found.append((0 if "name" in words or "label" in words else 1, len(other.columns), other.name, entry.name))
                    break
        if not found:
            return None
        found.sort()
        self._lookups[(table.upper(), column.upper())] = [found[0][2], found[0][3]]
        return self._lookups[(table.upper(), column.upper())]

    def vocabularies(self):
        """Each column of a kind that the map binds, with what the page needs to settle its codes."""
        found = []
        for view_name, role in (self.data or {}).get("roles", {}).items():
            view = self.views[view_name]
            for column in view["columns"]:
                if column["type"] != "kind":
                    continue
                item = role["columns"][column["name"]]
                binding = item.get("binding")
                key = f"{view_name}.{column['name']}"
                if view_name == "role_reading":
                    kinds = [(k["kind"], k["meaning"]) for k in self.model["kinds"]]
                    vocabulary = "readings"
                else:
                    vocabulary = column.get("vocabulary")
                    kinds = [(k["kind"], k["meaning"]) for k in self.model["vocabularies"][vocabulary]]
                links = {link["column"]: link["to"] for link in view.get("links", [])}
                link = next((c for c in ("anaesthetic_key", "patient_key") if c in links), None)
                reason = ""
                if not binding:
                    reason = "unbound"
                elif link is None:
                    reason = "unlinked"
                elif not role["columns"][link].get("binding"):
                    reason = "unbound_link"
                held = self.codes.get(key, {})
                lookup = held.get("lookup") or (self._lookup(binding["table"], binding["column"]) if binding else None)
                found.append({"key": key, "view": view_name, "column": column["name"], "vocabulary": vocabulary,
                              "required": bool(view.get("required")),
                              "kinds": [k for k, _ in kinds], "meanings": dict(kinds),
                              "bound": f"{binding['table']}.{binding['column']}" if binding else "",
                              "lookup": lookup, "reason": reason, "link": link,
                              "rows": held.get("rows") or [], "chosen": held.get("chosen") or {},
                              "names": held.get("names") or {}, "year": held.get("year"), "date": held.get("date")})
        return found

    def _vocabulary(self, key):
        entry = next((v for v in self.vocabularies() if v["key"] == key), None)
        if entry is None:
            raise DescribeError(f"The map binds no column {key}.")
        return entry

    def _reached(self, view_name, link, cohort_column, columns, raw=()):
        """The joins of a view turned round so that they start from #cohort and reach the view's own table last, by
        keys alone, then the lookups of its other columns. Returns (lines, {column: expression}, tables in order)."""
        role = self.data["roles"][view_name]
        base = role["rows"]["binding"]["table"]
        link_binding = role["columns"][link]["binding"]
        path = [list(step) for step in link_binding["path"]]
        aliases = {(): "t0"}
        tables = {(): base}
        for i in range(len(path)):
            prefix = tuple(tuple(s) for s in path[:i + 1])
            aliases[prefix] = f"t{len(aliases)}"
            tables[prefix] = path[i][2]
        last = tuple(tuple(s) for s in path)
        lines = ["FROM   #cohort AS c",
                 f"JOIN   {_name(tables[last])} AS {aliases[last]} WITH (NOLOCK) ON {aliases[last]}.{_name(link_binding['column'])} = c.{cohort_column}"]
        order = [tables[last]]
        for i in range(len(path) - 1, -1, -1):
            before, after = tuple(tuple(s) for s in path[:i]), tuple(tuple(s) for s in path[:i + 1])
            step = path[i]
            lines.append(f"JOIN   {_name(tables[before])} AS {aliases[before]} WITH (NOLOCK) ON "
                         f"{aliases[before]}.{_name(step[1])} = {aliases[after]}.{_name(step[3])}")
            order.append(tables[before])
        expressions = {}
        specs = {c["name"]: c for c in self.views[view_name]["columns"]}
        for name in columns:
            binding = role["columns"][name].get("binding")
            if not binding:
                expressions[name] = propose._empty(specs[name])
                continue
            prefix = ()
            for step in binding["path"]:
                following = prefix + (tuple(step),)
                if following not in aliases:
                    aliases[following] = f"t{len(aliases)}"
                    tables[following] = step[2]
                    lines.append(f"LEFT JOIN {_name(step[2])} AS {aliases[following]} WITH (NOLOCK) ON "
                                 f"{aliases[following]}.{_name(step[3])} = {aliases[prefix]}.{_name(step[1])}")
                prefix = following
            ref = f"{aliases[prefix]}.{_name(binding['column'])}"
            if name in raw:
                expressions[name] = ref
                continue
            codes = None
            if specs[name]["type"] == "kind":
                if view_name == "role_reading" and name == "kind":
                    codes = {k: item.get("codes", []) for k, item in self.data["kinds"].items()}
                else:
                    codes = self._vocabulary_codes(view_name).get(name, {})
            expressions[name] = propose._expression(specs[name], ref, binding.get("data_type", ""), codes)
        return lines, expressions, order

    def _cohort(self, year):
        """Part 1 of a script: at most COHORT_LIMIT anaesthetics of one year into #cohort, from role_anaesthetic."""
        sql = self.view_sql("role_anaesthetic")
        view = rolemap.view_sql(sql)
        tables = sorted(set(self._tables_of("role_anaesthetic")), key=str.upper)
        comment = _wrap(WORDING["cohort_comment"].format(limit=f"{COHORT_LIMIT:,}", year=year, tables=_and(tables)))
        indented = "\n".join("  " + line for line in view.splitlines())
        statement = (f"WITH role_anaesthetic AS (\n{indented}\n)\n"
                     f"SELECT TOP ({COHORT_LIMIT}) ISNULL(a.anaesthetic_key, 0) AS anaesthetic_key,\n"
                     f"       MIN(a.patient_key) AS patient_key\n"
                     f"INTO   #cohort\n"
                     f"FROM   role_anaesthetic AS a\n"
                     f"WHERE  a.start_time >= CAST('{year}-01-01' AS datetime)\n"
                     f"  AND  a.start_time < CAST('{int(year) + 1}-01-01' AS datetime)\n"
                     f"  AND  a.anaesthetic_key IS NOT NULL\n"
                     f"GROUP  BY a.anaesthetic_key\n"
                     f"ORDER  BY MIN(a.start_time), a.anaesthetic_key;\n"
                     f"ALTER TABLE #cohort ADD PRIMARY KEY (anaesthetic_key);")
        return comment + "\n" + DROP + "\n" + statement

    def _script(self, year, comment, second):
        head = "\n".join([_wrap(WORDING["timeout"]), _wrap(WORDING["names"])])
        return head + "\nSET NOCOUNT ON;\n\n" + self._cohort(year) + "\n\n" + comment + "\n" + second.strip() + "\n"

    def _tables_of(self, view_name):
        role = self.data["roles"][view_name]
        found = [role["rows"]["binding"]["table"]]
        for item in role["columns"].values():
            found += _binding_tables(item.get("binding"))
        return list(dict.fromkeys(found))

    def charted_query(self, key, year, step=""):
        """The list of what is charted for one column of a kind, as a two-part script for one year."""
        entry = self._vocabulary(key)
        if entry["reason"]:
            raise DescribeError(entry["reason"])
        year = int(year)
        view_name, column = entry["view"], entry["column"]
        cohort_column = "anaesthetic_key" if entry["link"] == "anaesthetic_key" else "patient_key"
        lines, expressions, order = self._reached(view_name, entry["link"], cohort_column, [column], raw={column})
        ref = expressions[column]
        lookup = entry["lookup"]
        select = [f"SELECT CAST({ref} AS nvarchar(100)) AS code,",
                  f"       CASE WHEN COUNT(*) >= {LEAST} THEN (COUNT(*) / 10) * 10 END AS charted,",
                  f"       CASE WHEN COUNT(DISTINCT c.anaesthetic_key) >= {LEAST} THEN (COUNT(DISTINCT c.anaesthetic_key) / 10) * 10 END AS anaesthetics,"]
        group = f"GROUP  BY {ref}"
        if lookup:
            select.append(f"       CAST(d.{_name(lookup[1])} AS nvarchar(200)) AS name")
            lines = lines + [f"LEFT JOIN {_name(lookup[0])} AS d WITH (NOLOCK) ON d.{_name(entry['bound'].split('.')[1])} = {ref}"]
            group += f", d.{_name(lookup[1])}"
            comment = WORDING["charted_comment"].format(column=entry["bound"], lookup=lookup[0], path=_and(order))
        else:
            select.append("       CAST(NULL AS nvarchar(200)) AS name")
            comment = WORDING["charted_comment_bare"].format(column=entry["bound"], path=_and(order))
        second = "\n".join(select + lines + [group, "ORDER  BY COUNT(*) DESC;"])
        sql = self.offer(f"charted-{key.replace('.', '-')}", step, self._script(year, _wrap(comment), second), key=key, year=year)
        return {"sql": sql, "year": year, "tables": self._sized(order)}

    def read_charted(self, key, text, year, record=True):
        entry = self._vocabulary(key)
        columns, rows = read_grid(text, CHARTED_COLUMNS)
        if record:
            self.pasted(f"charted-{key.replace('.', '-')}", text)
        parsed = [{"code": r[0], "charted": _number(r[1]), "anaesthetics": _number(r[2]), "name": r[3]} for r in rows if r[0]]
        held = self.codes.setdefault(key, {})
        held.update({"rows": parsed, "year": int(year), "lookup": entry["lookup"]})
        return {"rows": len(parsed)}

    def choose_codes(self, key, chosen, date=None):
        """Records the codes that a person chose for each kind of a vocabulary: chosen is {code: kind}."""
        entry = self._vocabulary(key)
        date = date or _today()
        chosen = {str(code): kind for code, kind in chosen.items() if kind in entry["kinds"] and kind != "other"}
        held = self.codes.setdefault(key, {})
        names = {r["code"]: r["name"] for r in held.get("rows", [])}
        held.update({"chosen": chosen, "names": {c: names.get(c, "") for c in chosen}, "date": date,
                     "lookup": entry["lookup"]})
        if entry["view"] == "role_reading":
            kinds = self.data["kinds"]
            source = entry["bound"]
            for kind in [k for k in entry["kinds"] if k != "other"]:
                codes = sorted((c for c, k in chosen.items() if k == kind), key=str)
                if codes:
                    kinds[kind] = {"codes": codes, "status": "person", "from": source,
                                   "says": WORDING["codes_says"].format(count=len(codes), codes="code" if len(codes) == 1 else "codes", date=date),
                                   "confirmation": {"answer": "yes", "date": date}}
                elif kind in rolemap.MEAN_KINDS:
                    kinds[kind] = {"codes": [], "status": "proposed", "from": source, "says": WORDING["codes_none"],
                                   "question": WORDING["codes_question"]}
                else:
                    kinds.pop(kind, None)
        self.settings["updated"] = date

    def _vocabulary_codes(self, view_name):
        found = {}
        for key, held in self.codes.items():
            view, _, column = key.partition(".")
            if view != view_name or view_name == "role_reading" or not held.get("chosen"):
                continue
            by_kind = {}
            for code, kind in held["chosen"].items():
                by_kind.setdefault(kind, []).append(code)
            found[column] = {k: sorted(v, key=str) for k, v in by_kind.items()}
        return found

    # The views and the counts.

    def view_sql(self, view_name):
        role = dict(self.data["roles"][view_name])
        role["_date"] = propose._proposed_on(self.data)
        return propose.view_sql(view_name, role, self.data["kinds"], self.model, self._vocabulary_codes(view_name))

    def _compiled(self, sql, views):
        parts = []
        for view in views:
            text = rolemap.view_sql(self.view_sql(view))
            parts.append(f"{view} AS (\n" + "\n".join("  " + line for line in text.splitlines()) + "\n)")
        return "WITH " + ",\n".join(parts) + "\n" + sql.strip() + "\n"

    def _sized(self, tables):
        return [[t, self.sizes.get(t.upper())] for t in dict.fromkeys(tables)]

    def count_queries(self, year=None, step=""):
        """The counts of this screen: two that read the small tables only, and the readings of one year's cohort by kind
        as a two-part script. Each is {"name", "safe", "sql", "tables"}."""
        year = int(year or self.settings.get("year") or dt.date.today().year - 1)
        step = 10
        rounded = lambda n: f"g.{n} - g.{n} % {step} AS {n}"  # noqa: E731
        coverage = ("with_patient", "with_birth_date", "with_death_date", "test_patients", "with_stop", "stop_before_start")
        found = []
        small = self._tables_of("role_patient") + self._tables_of("role_anaesthetic")
        coverage_sql = f"""SELECT g.start_year,
       {rounded('anaesthetics')},
       {(',' + chr(10) + '       ').join(rounded(n) for n in coverage)}
FROM   (SELECT YEAR(a.start_time) AS start_year,
               COUNT(*) AS anaesthetics,
               SUM(CASE WHEN p.patient_key IS NOT NULL THEN 1 ELSE 0 END) AS with_patient,
               SUM(CASE WHEN p.birth_date IS NOT NULL THEN 1 ELSE 0 END) AS with_birth_date,
               SUM(CASE WHEN p.death_date IS NOT NULL THEN 1 ELSE 0 END) AS with_death_date,
               SUM(CASE WHEN p.is_test = 1 THEN 1 ELSE 0 END) AS test_patients,
               SUM(CASE WHEN a.stop_time IS NOT NULL THEN 1 ELSE 0 END) AS with_stop,
               SUM(CASE WHEN a.stop_time < a.start_time THEN 1 ELSE 0 END) AS stop_before_start
        FROM   role_anaesthetic a
               LEFT JOIN (SELECT pp.patient_key, MAX(pp.birth_date) AS birth_date, MAX(pp.death_date) AS death_date,
                                 MAX(pp.is_test) AS is_test
                          FROM   role_patient pp
                          GROUP  BY pp.patient_key) p ON p.patient_key = a.patient_key
        GROUP  BY YEAR(a.start_time)) g
WHERE  g.anaesthetics >= {LEAST}
ORDER  BY g.start_year;"""
        head = lambda tables: "\n".join([_wrap(WORDING["safe_comment"].format(tables=_and(dict.fromkeys(tables)))), _wrap(WORDING["names"])])  # noqa: E731
        found.append({"name": "coverage_by_year", "safe": True,
                      "sql": head(small) + "\n" + self._compiled(coverage_sql, ["role_patient", "role_anaesthetic"]),
                      "tables": self._sized(small)})
        repeated_sql = """SELECT g.role_view, g.keys_repeated, g.rows_held
FROM   (SELECT 'role_patient' AS role_view, COUNT(*) AS keys_repeated, COALESCE(SUM(k.n), 0) AS rows_held
        FROM   (SELECT pp.patient_key, COUNT(*) AS n FROM role_patient pp GROUP BY pp.patient_key HAVING COUNT(*) > 1) k
        UNION ALL
        SELECT 'role_anaesthetic', COUNT(*), COALESCE(SUM(k.n), 0)
        FROM   (SELECT aa.anaesthetic_key, COUNT(*) AS n FROM role_anaesthetic aa GROUP BY aa.anaesthetic_key HAVING COUNT(*) > 1) k) g
ORDER  BY g.role_view;"""
        found.append({"name": "repeated_keys", "safe": True,
                      "sql": head(small) + "\n" + self._compiled(repeated_sql, ["role_patient", "role_anaesthetic"]),
                      "tables": self._sized(small)})
        reading = self.data["roles"].get("role_reading")
        if reading and reading["columns"]["anaesthetic_key"].get("binding"):
            columns = ["kind", "accepted", "value"]
            lines, expressions, order = self._reached("role_reading", "anaesthetic_key", "anaesthetic_key", columns)
            inner = ("SELECT " + ",\n       ".join(f"{expressions[c]} AS {c}" for c in columns)
                     + ",\n       c.anaesthetic_key\n" + "\n".join(lines))
            indented = "\n".join("        " + line for line in inner.splitlines())
            second = f"""SELECT g.kind,
       CASE WHEN g.readings >= {LEAST} THEN g.readings - g.readings % 10 END AS readings,
       CASE WHEN g.accepted >= {LEAST} THEN g.accepted - g.accepted % 10 END AS accepted,
       CASE WHEN g.with_value >= {LEAST} THEN g.with_value - g.with_value % 10 END AS with_value,
       CASE WHEN g.anaesthetics >= {LEAST} THEN g.anaesthetics - g.anaesthetics % 10 END AS anaesthetics
FROM   (SELECT r.kind,
               COUNT(*) AS readings,
               SUM(CASE WHEN r.accepted = 1 THEN 1 ELSE 0 END) AS accepted,
               SUM(CASE WHEN r.value IS NOT NULL THEN 1 ELSE 0 END) AS with_value,
               COUNT(DISTINCT r.anaesthetic_key) AS anaesthetics
        FROM   (
{indented}
               ) r
        GROUP  BY r.kind) g
ORDER  BY g.kind;"""
            found.append({"name": "readings_by_kind", "safe": False, "year": year,
                          "sql": self._script(year, _wrap(WORDING["readings_comment"].format(path=_and(order))), second),
                          "tables": self._sized(self._tables_of("role_anaesthetic") + order)})
        for item in found:
            item["sql"] = self.offer(f"count-{item['name']}", step, item["sql"], year=year)
        return found

    def read_count(self, name, text, date=None, record=True):
        if name not in COUNT_COLUMNS:
            raise DescribeError(WORDING["unknown_count"].format(name=name))
        columns, rows = read_grid(text, COUNT_COLUMNS[name])
        if record:
            self.pasted(f"count-{name}", text)
        held = self.counts.setdefault(name, {})
        held.update({"columns": columns, "rows": rows, "date": date or _today()})
        return {"rows": len(rows), "findings": self.findings(name)}

    def judge_count(self, name, looks_right, note="", date=None):
        if name not in COUNT_COLUMNS:
            raise DescribeError(WORDING["unknown_count"].format(name=name))
        held = self.counts.setdefault(name, {})
        held.update({"looks_right": looks_right, "note": " ".join((note or "").split())[:400], "judged": date or _today()})

    def findings(self, name):
        held = self.counts.get(name) or {}
        if not held.get("rows"):
            return []
        records = [dict(zip(held["columns"], [_number(v) if _number(v) is not None else v for v in row])) for row in held["rows"]]
        found = []
        if name == "coverage_by_year":
            records = [r for r in records if isinstance(r.get("anaesthetics"), (int, float)) and r["anaesthetics"]]
            for figure, what in FIGURES.items():
                shares = [((r.get(figure) or 0) / r["anaesthetics"], r) for r in records]
                if not shares:
                    continue
                best_share, best = max(shares, key=lambda s: (s[0], s[1]["anaesthetics"]))
                for share, r in shares:
                    if r is not best and best_share > 0 and share <= best_share / 2 and (best_share - share) * r["anaesthetics"] >= 10:
                        found.append(WORDING["cliff"].format(year=r["start_year"], count=r.get(figure) or 0, total=r["anaesthetics"],
                                                             what=what, best_count=best.get(figure) or 0,
                                                             best_total=best["anaesthetics"], best_year=best["start_year"]))
        elif name == "repeated_keys":
            for r in records:
                if (r.get("keys_repeated") or 0) > 0:
                    found.append(WORDING["repeated"].format(view=r["role_view"], count=r["keys_repeated"]))
        return found

    # The record of how the folder was made: every query offered, every result pasted, every answer.

    def offer(self, name, step, sql, **extra):
        """Records a query as the page offers it, numbered in the order of first offer, and returns its text with a
        first line that names the tool's version and the date, which is the text that the colleague copies."""
        entry = self.journal.get(name)
        if entry is None:
            entry = self.journal[name] = {"number": len(self.journal) + 1, "name": name}
        stamp = f"-- {WORDING['stamp_query'].format(version=self.version or 'unknown', date=_today())}"
        text = stamp + "\n" + sql
        entry.update({"step": step, "offered": _now(), "version": self.version, **extra})
        self.queries[name] = text
        return text

    def pasted(self, name, text):
        entry = self.journal.get(name)
        if entry is None:
            return
        self.results[name] = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        entry.update({"pasted": _now(), "database": self.settings.get("database"), "version": self.version})

    def _file(self, name, folder, suffix):
        return f"{folder}/{self.journal[name]['number']:02d}-{name}.{suffix}"

    def _log_confirmation(self, about, answer, replacement, note, date):
        self.confirmations.append({"attribute": about, "answer": answer, "replacement": replacement, "date": date,
                                   "note": " ".join((note or "").split())[:400], "version": self.version})

    # The hospital folder.

    def _work_folder(self, name="map"):
        """A folder in the worker's own memory, or in a temporary folder on the command line, that holds the map so
        that propose.confirm can work on it as it works on a map folder. It is never one of the hospital's folders."""
        if self._scratch is None:
            self._scratch = Path(tempfile.mkdtemp(prefix="schemalyser-describe-"))
        folder = self._scratch / name
        folder.mkdir(parents=True, exist_ok=True)
        if name != "map":
            return folder
        for old in folder.glob("*"):
            old.unlink()
        for name, text in self._map_files().items():
            (folder / name).write_text(text, encoding="utf-8")
        return folder

    def _map_files(self, stamp=""):
        files = {rolemap.MAP_FILE: json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"}
        for name in self.data["roles"]:
            files[f"{name}.sql"] = (stamp + "\n" if stamp else "") + self.view_sql(name)
        return files

    def _json(self, value, date):
        return (json.dumps({"tool": "Schemalyser", "version": self.version, "written": date, **value}, indent=2,
                           ensure_ascii=False) + "\n").encode("utf-8")

    def folder_files(self, keep_dictionary=False, date=None):
        """The hospital folder, as {path: bytes}. Every file names the tool's version and the date it was written,
        except map.json, whose format the map checker fixes and whose description gives the date of the proposal,
        the pasted results, whose first line does, and the dictionary's own files, which are kept exactly as given."""
        date = date or _today()
        files = {}
        stamp = f"-- {WORDING['stamp_file'].format(version=self.version or 'unknown', date=date)}"
        if self.data is not None:
            for name, text in self._map_files(stamp).items():
                files[f"map/{name}"] = text.encode("utf-8")
        for name, text in self.queries.items():
            files[self._file(name, "queries", "sql")] = text.encode("utf-8")
        for name, text in self.results.items():
            entry = self.journal[name]
            first = "# " + WORDING["stamp_result"].format(version=entry.get("version") or self.version or "unknown",
                                                          date=(entry.get("pasted") or date)[:10])
            files[self._file(name, "results", "tsv")] = (first + "\n" + text.rstrip("\n") + "\n").encode("utf-8")
        for key, held in sorted(self.codes.items()):
            files[f"codes/{key}.json"] = self._json({"view": key.split(".")[0], "column": key.split(".")[1], **held}, date)
        judgements = {name: {k: held.get(k) for k in ("date", "looks_right", "note", "judged") if held.get(k) is not None}
                      for name, held in sorted(self.counts.items())}
        if judgements:
            files["counts/judgements.json"] = self._json({"counts": judgements}, date)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["attribute", "answer", "replacement", "date", "note", "version"])
        writer.writerows([c[k] for k in ("attribute", "answer", "replacement", "date", "note", "version")] for c in self.confirmations)
        files["confirmations.csv"] = out.getvalue().encode("utf-8")
        kept = bool(keep_dictionary and self.dictionary_files)
        if kept:
            meta = self.dictionary_files
            files[f"{DICTIONARY_FOLDER}/{meta['name']}"] = meta["data"]
            if meta.get("tables_data") is not None:
                files[f"{DICTIONARY_FOLDER}/{meta['tables_name']}"] = meta["tables_data"]
            files[f"{DICTIONARY_FOLDER}/dictionary.json"] = self._json(
                {"file": meta["name"], "tables": meta.get("tables_name"), "headings": meta.get("headings") or {}}, date)
        entries = sorted(self.journal.values(), key=lambda e: e["number"])
        journal = []
        if self.dictionary_entry:
            journal.append(self.dictionary_entry)
        for entry in entries:
            item = {k: v for k, v in entry.items() if k != "number"}
            item["query"] = self._file(entry["name"], "queries", "sql")
            item["result"] = self._file(entry["name"], "results", "tsv") if entry["name"] in self.results else None
            journal.append({"number": entry["number"], **item})
        files["journal.json"] = self._json({"entries": journal}, date)
        settings = dict(self.settings)
        settings.update({"dictionary": {"kept": kept, **({k: v for k, v in (self.dictionary_receipt() or {}).items()
                                                          if k in ("tables", "columns", "file", "tablesFile")})}})
        files["settings.json"] = self._json(settings, date)
        files["README.md"] = readme(sorted(files), self.version, date, kept).encode("utf-8")
        return files

    def folder_zip(self, keep_dictionary=False, date=None):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(self.folder_files(keep_dictionary, date).items()):
                archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data)
        return out.getvalue()

    def restore(self, files):
        """Reads a hospital folder that this screen wrote, as {path: bytes} relative to the folder, and returns what
        it held. A folder without map/map.json starts a new hospital folder."""
        files = dict(files)
        self.folder = files
        found = {"map": False, "tables": False, "codes": 0, "counts": 0, "dictionary": False, "confirmations": 0,
                 "queries": 0, "problem": ""}
        held = _json_of(files.get("settings.json"))
        for key in ("made", "updated", "database", "year"):
            if key in held:
                self.settings[key] = held[key]
        info = _json_of(files.get(f"{DICTIONARY_FOLDER}/dictionary.json"))
        if info.get("file") and f"{DICTIONARY_FOLDER}/{info['file']}" in files:
            tables = files.get(f"{DICTIONARY_FOLDER}/{info['tables']}") if info.get("tables") else None
            try:
                self.load_dictionary(files[f"{DICTIONARY_FOLDER}/{info['file']}"], tables, info.get("headings") or {},
                                     info["file"], info.get("tables") or "tables.csv")
                found["dictionary"] = True
            except DescribeError:
                pass
        if "map/map.json" in files:
            folder = self._work_folder("restore")
            (folder / rolemap.MAP_FILE).write_bytes(bytes(files["map/map.json"]))
            try:
                self.data = rolemap.read_map_json(folder)
                found["map"] = True
            except rolemap.MapError:
                found["problem"] = WORDING["folder_unreadable"]
        if "confirmations.csv" in files:
            for row in csv.DictReader(io.StringIO(_text(files["confirmations.csv"]))):
                if row.get("attribute") and row.get("answer"):
                    self.confirmations.append({k: row.get(k) or "" for k in ("attribute", "answer", "replacement", "date", "note", "version")})
            found["confirmations"] = len(self.confirmations)
        for path, data in sorted(files.items()):
            match = re.fullmatch(r"codes/(role_\w+\.\w+)\.json", path)
            if match:
                held = _json_of(data)
                if held:
                    self.codes[match.group(1)] = {k: v for k, v in held.items() if k not in ("view", "column", "tool", "version", "written")}
                    found["codes"] += 1
        judgements = _json_of(files.get("counts/judgements.json")).get("counts") or {}
        for name, held in judgements.items():
            if name in COUNT_COLUMNS:
                self.counts[name] = dict(held)
        for entry in (_json_of(files.get("journal.json")).get("entries") or []):
            if entry.get("name") == "dictionary" or "number" not in entry:
                continue
            name = entry["name"]
            self.journal[name] = {k: v for k, v in entry.items() if k not in ("query", "result")}
            query = files.get(entry.get("query") or "")
            if query is not None:
                self.queries[name] = _text(query)
                found["queries"] += 1
            result = files.get(entry.get("result") or "")
            if result is None:
                continue
            text = _strip_stamp(_text(result))
            self.results[name] = text
            try:
                if name == "tables-and-columns":
                    self.read_tables(text)
                    found["tables"] = True
                elif name.startswith("charted-") and entry.get("key"):
                    self.read_charted(entry["key"], text, entry.get("year") or self.settings.get("year") or 2000, record=False)
                elif name.startswith("count-") and name[6:] in COUNT_COLUMNS:
                    self.read_count(name[6:], text, (entry.get("pasted") or "")[:10] or None, record=False)
                    self.counts[name[6:]].update(judgements.get(name[6:]) or {})
                    found["counts"] += 1
            except DescribeError:
                pass
        self.restored = found
        return found

    # Checking that a folder is still right.

    def check(self, date=None):
        """Rebuilds the map from the dictionary and the recorded confirmations, where the dictionary is loaded, and
        says whether it is the same as the folder's map; and lists every query of the folder with its earlier result."""
        rebuilt = {"rebuilt": False, "same": None, "differences": []}
        if self.dictionary is not None and self.data is not None:
            fresh = Describe()
            fresh.version = self.version
            fresh.dictionary, fresh.dictionary_files = self.dictionary, self.dictionary_files
            fresh.proposer = self.proposer
            fresh.propose(date=propose._proposed_on(self.data) or self.settings.get("made"))
            problems = []
            for row in self.confirmations:
                try:
                    fresh.confirm(row["attribute"], row["answer"], row.get("replacement") or "", row.get("note") or "", row.get("date") or None)
                except (DescribeError, KeyError) as error:
                    problems.append(WORDING["check_confirmation"].format(about=row["attribute"], problem=str(error)))
            for key, held in self.codes.items():
                if held.get("chosen"):
                    fresh.codes.setdefault(key, {}).update({k: v for k, v in held.items() if k != "chosen"})
                    try:
                        fresh.choose_codes(key, held["chosen"], held.get("date"))
                    except DescribeError:
                        problems.append(WORDING["check_codes"].format(key=key))
            self.proposer = fresh.proposer
            rebuilt = {"rebuilt": True, "differences": problems + _differences(self.data, fresh.data)}
            rebuilt["same"] = not rebuilt["differences"]
        queries = []
        for entry in sorted(self.journal.values(), key=lambda e: e["number"]):
            name = entry["name"]
            previous = self.results.get(name)
            columns, rows = [], []
            if previous:
                try:
                    columns, rows = read_grid(previous)
                except DescribeError:
                    pass
            queries.append({"name": name, "number": entry["number"], "step": entry.get("step"), "sql": self.queries.get(name, ""),
                            "file": self._file(name, "queries", "sql"), "pasted": entry.get("pasted"),
                            "database": entry.get("database"), "columns": columns, "rows": rows[:200], "more": max(0, len(rows) - 200)})
        return {**rebuilt, "queries": queries}

    def compare(self, name, text):
        """The differences between a query's earlier result and a new one, as sentences: a table or column that has
        gone, a row that has gone or come, and a count that has changed by more than a tenth."""
        previous = self.results.get(name)
        if previous is None:
            return {"differences": [], "previous": False}
        if name == "tables-and-columns":
            return {"differences": _tables_differences(previous, text), "previous": True}
        before_columns, before = read_grid(previous)
        after_columns, after = read_grid(text, before_columns)
        return {"differences": _grid_differences(before_columns, before, after), "previous": True}

    def set_settings(self, database=None, year=None):
        if database in ("production", "training", "unsure"):
            self.settings["database"] = database
        if year is not None and re.fullmatch(r"(19|20)\d\d", str(year)):
            self.settings["year"] = int(year)

    # The model that the page shows.

    def view(self):
        roles = []
        for view in self.model["views"]:
            role = (self.data or {}).get("roles", {}).get(view["name"])
            entry = {"name": view["name"], "description": view["description"], "required": bool(view.get("required")),
                     "drafted": role is not None, "items": []}
            if role is not None:
                rows = role["rows"]
                table = rows["binding"]["table"] if rows.get("binding") else rows["from"]
                entry["items"].append(self._item(f"{view['name']} rows", "rows", rows, view["description"], table, None))
                for column in view["columns"]:
                    item = role["columns"][column["name"]]
                    binding = item.get("binding")
                    entry["items"].append(self._item(f"{view['name']}.{column['name']}", column["name"], item,
                                                     column["meaning"], binding["table"] if binding else None,
                                                     binding["column"] if binding else None, column["type"]))
            roles.append(entry)
        tally = self.tally()
        return {"dictionary": self.dictionary_receipt(), "proposed": self.data is not None, "roles": roles,
                "tally": tally, "questions": self.questions(), "catalogue": self.catalogue is not None,
                "vocabularies": self.vocabularies(), "counts": {k: {kk: v.get(kk) for kk in ("columns", "rows", "looks_right", "note", "date")}
                                                                | {"findings": self.findings(k)} for k, v in self.counts.items()},
                "settings": {k: self.settings.get(k) for k in ("made", "updated", "database", "year")},
                "restored": self.restored}

    def _item(self, about, attribute, item, meaning, table, column, role_type=None):
        binding = item.get("binding")
        definition = None
        if self.dictionary is not None and table:
            definition = self.dictionary.description(table, column) if column else self.dictionary.description(table)
        candidates = []
        for candidate in item.get("candidates") or []:
            head = candidate["from"].split(",")[0]
            words = None
            if self.dictionary is not None and "." in head:
                words = self.dictionary.description(*head.split(".", 1)) or None
            elif self.dictionary is not None:
                words = self.dictionary.description(head) or None
            candidates.append({"from": candidate["from"], "replacement": _parse_from(candidate["from"]) if "." in head else head,
                               "definition": words or candidate.get("words")})
        confirmation = item.get("confirmation") or {}
        return {"about": about, "attribute": attribute, "meaning": meaning, "type": role_type, "from": item["from"],
                "table": table, "column": column, "bound": bool(binding) if attribute != "rows" else bool(table),
                "definition": definition, "says": item["says"], "confidence": item.get("confidence") or "",
                "candidates": candidates, "status": item["status"], "question": item.get("question") or "",
                "answer": confirmation.get("answer"), "date": confirmation.get("date"),
                "replacement": confirmation.get("replacement"), "note": confirmation.get("note"),
                "presence": self.presence(binding if attribute != "rows" else ({"table": table} if table else None))}


def _safe_file(name, fallback):
    name = Path(str(name or "")).name
    return name if re.fullmatch(r"[\w .()-]{1,100}\.(csv|tsv|txt)", name, re.I) else fallback


def _json_of(data):
    if data is None:
        return {}
    try:
        value = json.loads(_text(data))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _strip_stamp(text):
    """A pasted result as it was pasted, without the first line that the folder adds to name the tool and the date."""
    first, _, rest = (text or "").partition("\n")
    return rest if first.startswith("# Pasted into Schemalyser") else text


def _shown(item):
    if item is None:
        return "nothing"
    binding = item.get("binding")
    if binding and binding.get("column"):
        return f"{binding['table']}.{binding['column']} ({item['status']})"
    if binding:
        return f"{binding['table']} ({item['status']})"
    return f"no binding ({item['status']})"


def _differences(before, after):
    """The bindings and codes in which two maps differ, as sentences."""
    found = []
    roles = list(dict.fromkeys([*before["roles"], *after["roles"]]))
    for name in roles:
        one, two = before["roles"].get(name), after["roles"].get(name)
        if one is None or two is None:
            found.append(WORDING["only_folder" if two is None else "only_rebuilt"].format(about=name))
            continue
        pairs = [(f"{name} rows", one["rows"], two["rows"])]
        pairs += [(f"{name}.{c}", one["columns"].get(c), two["columns"].get(c)) for c in dict.fromkeys([*one["columns"], *two["columns"]])]
        for about, x, y in pairs:
            same = x is not None and y is not None and x.get("binding") == y.get("binding") and x["status"] == y["status"] \
                and (x.get("confirmation") or {}).get("answer") == (y.get("confirmation") or {}).get("answer")
            if not same:
                found.append(WORDING["differs"].format(about=about, before=_shown(x), after=_shown(y)))
    for kind in dict.fromkeys([*before["kinds"], *after["kinds"]]):
        x, y = before["kinds"].get(kind, {}).get("codes"), after["kinds"].get(kind, {}).get("codes")
        if sorted(x or []) != sorted(y or []):
            found.append(WORDING["differs"].format(about=f"The codes of {kind}", before=", ".join(x or []) or "none",
                                                   after=", ".join(y or []) or "none"))
    return found


def _tables_differences(previous, text):
    try:
        before, after = first_ask._rows(_strip_stamp(previous)), first_ask._rows(text)
    except first_ask.FirstAskError:
        raise DescribeError("unreadable") from None
    columns = lambda rows: {(r[1].upper(), r[2].upper()): r for r in rows}  # noqa: E731
    sizes = lambda rows: {r[1].upper(): (r[1], _number(r[-1])) for r in rows}  # noqa: E731
    one, two = columns(before), columns(after)
    tables_one, tables_two = sizes(before), sizes(after)
    found = [WORDING["table_gone"].format(table=tables_one[t][0]) for t in sorted(set(tables_one) - set(tables_two))]
    found += [WORDING["column_gone"].format(column=f"{one[c][1]}.{one[c][2]}") for c in sorted(set(one) - set(two))
              if c[0] in tables_two]
    found += [WORDING["table_new"].format(table=tables_two[t][0]) for t in sorted(set(tables_two) - set(tables_one))]
    for table in sorted(set(tables_one) & set(tables_two)):
        a, b = tables_one[table][1], tables_two[table][1]
        if a is not None and b is not None and abs(b - a) > 0.1 * max(a, 1):
            found.append(WORDING["size_changed"].format(table=tables_one[table][0], before=f"{a:,}", after=f"{b:,}"))
    return found


def _grid_differences(columns, before, after):
    """Rows matched by their first column, and each count compared: a change of more than a tenth is reported."""
    key = lambda row: row[0]  # noqa: E731
    one, two = {key(r): r for r in before}, {key(r): r for r in after}
    found = [WORDING["row_gone"].format(key=k or "with an empty first value") for k in one if k not in two]
    found += [WORDING["row_new"].format(key=k or "with an empty first value") for k in two if k not in one]
    for k in [k for k in one if k in two]:
        for at, column in enumerate(columns[1:], 1):
            a, b = _number(one[k][at]), _number(two[k][at])
            if a is None and b is None:
                continue
            if a is None or b is None or abs(b - a) > 0.1 * max(abs(a), 1):
                found.append(WORDING["count_changed"].format(column=column, key=k, before=one[k][at] or "blank",
                                                             after=two[k][at] or "blank"))
    return found


README = {
    "title": "# The hospital folder",
    "stamp": "Schemalyser {version} wrote this folder on {date}.",
    "intro": "This folder describes how the hospital's reporting database holds the anaesthetic record. A clinician and "
             "a colleague who runs SQL against that database made it together with Schemalyser's page Describe the "
             "record. The page proposed which tables and columns hold each role of the record, the colleague confirmed "
             "or corrected each one, and the queries that the colleague ran settled the local codes and the counts. "
             "The folder names the hospital's own tables and codes, so it stays on the hospital's own storage.",
    "files": "## What each file holds",
    "remake": "## How to check or remake the folder",
    "remake_text": [
        "Open the page Describe the record, take it offline, load the data dictionary in step 2 and choose this folder "
        "in step 3. The page then restores everything below, and you can carry on from where the folder was left.",
        "To check that the folder is still right, for example after a change to the database or a new release of the "
        "vendor's system, choose Check that this folder is still right. Schemalyser proposes the map again from the "
        "dictionary, applies the answers in confirmations.csv in their order, and says whether the result is the same "
        "as map/map.json. It then lists every query in queries/ with its earlier result from results/. The colleague "
        "runs each query again and pastes the new result, and the page lists what has changed: a table or column that "
        "has gone, a row that has gone or come, or a count that has changed by more than a tenth.",
        "Without the page, the folder can still be checked by hand. Each file in queries/ is the exact text that the "
        "colleague ran, and the file of the same number in results/ is what came back. journal.json says which step "
        "offered each query, which database it was run on and when the result was pasted.",
    ],
    "licence": "The data dictionary is licensed. The folder holds a copy of it in dictionary/ only because a person "
               "ticked the box to keep it, and map/map.json quotes it as the evidence for each binding.",
    "no_licence": "The data dictionary is licensed, so the folder holds no copy of it. map/map.json quotes it only as the "
                  "evidence for each binding, and the dictionary must be loaded again to check the folder.",
}
README_FILES = [
    ("settings.json", "The tool's version, the dates on which the folder was made and last changed, the database that "
                      "the queries were run on, production or training, and the year of the lists."),
    ("journal.json", "One entry for each step that took something in: the step's heading, the query file, the result "
                     "file, the database, when the result was pasted and the tool's version. For the dictionary, it "
                     "gives the file's name, its size, its numbers of tables and columns and a fingerprint of its "
                     "contents (a SHA-256 hash), and never its contents."),
    ("confirmations.csv", "Every answer that the colleague gave, in order: the role and attribute, the answer (yes, no "
                          "or not sure), the replacement where the answer was no, the date and any note."),
    ("map/map.json", "Every binding of every role: the table and column that hold it, the dictionary's description "
                     "that supports it, the answer and its date. Its description gives the date of the proposal."),
    ("map/role_*.sql", "One SQL view for each role, written from the bindings and the chosen codes. An audit reads "
                       "these views and nothing else."),
    ("queries/", "The exact text of every query that the page offered, numbered in the order offered."),
    ("results/", "Each result that the colleague pasted, exactly as pasted, with the same number and name as its query. "
                 "The first line names the tool's version and the date."),
    ("codes/", "For each vocabulary that the hospital holds as local codes, the list of what is charted and the codes "
               "chosen for each kind."),
    ("counts/judgements.json", "For each count, whether it looked right to the two of you, and any note."),
    ("dictionary/", "The dictionary's own files, exactly as they were loaded."),
]


def readme(paths, version, date, kept):
    """README.md of the hospital folder, which says what each file is, how it was made and how to remake it."""
    lines = [README["title"], "", README["stamp"].format(version=version or "unknown", date=date), "", README["intro"], "",
             README["files"], ""]
    for name, what in README_FILES:
        present = any(p == name or (name.endswith("/") and p.startswith(name))
                      or (name == "map/role_*.sql" and p.startswith("map/role_")) for p in paths)
        if present:
            lines.append(f"- `{name}`: {what}")
    lines += ["", README["licence"] if kept else README["no_licence"], "", README["remake"], ""]
    for paragraph in README["remake_text"]:
        lines += [paragraph, ""]
    return "\n".join(lines)
