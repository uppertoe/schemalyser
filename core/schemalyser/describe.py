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
    settings.json                   the tool's version, the dates, the database, the year of the lists, the time zone
                                    of the database's clocks, and the state of readiness that each part has reached

The dictionary is licensed. It is read here, in the browser's worker or on the hospital's own machine, and nothing
from it leaves except into the hospital folder: map.json quotes it as evidence, and the dictionary's own files are
copied only where a person asks. No SQL that this module writes quotes a description, and no message holds one.

Every query that this module writes for production reads the small tables only, or is a two-part script: part 1 puts
at most COHORT_LIMIT anaesthetics of one year into #cohort from the anaesthetic's own tables, and part 2 reaches the
larger table from #cohort by its keys alone, joining the table of readings last, so that SQL Server cannot read the
readings before the cohort has been narrowed (see scripts.py for why).
"""
import copy
import csv
import dataclasses
import datetime as dt
import io
import json
import hashlib
import re
import tempfile
import time
import zipfile
from pathlib import Path

from . import corrections, datadict, first_ask, propose, rolemap
from .catalogue import NAME, QUERY_ORDER, Catalogue, CatalogueError

# A table of at least this many rows is marked as large, and no count on this screen reads it in full.
LARGE = 10_000_000
# The most anaesthetics that part 1 of a script puts into #cohort.
COHORT_LIMIT = 5000
# The fewest rows that a count shows; a smaller group is left out, a smaller figure within a group is left empty, and
# every count is rounded down to tens.
LEAST = 10
FOLDER_FORMAT = 1
DICTIONARY_FOLDER = "dictionary"
SAFE_COUNTS = ("coverage_by_year", "repeated_keys")
CONFIRMATION_FIELDS = ("attribute", "answer", "replacement", "date", "note", "version", "correction", "test", "reason",
                       "provenance")
# Where each fact of the saved hospital schema came from: the whole of a table, a sample of it (the anaesthetics of one
# year in #cohort), the database's own records of its tables or the data dictionary, a person's answer or judgement,
# or the page's own proposal.
COMPLETE, SAMPLE, METADATA, PERSON, INFERENCE = "complete data", "a sample", "metadata", "a person", "an inference"
# The three states of readiness, in order. The page can reach the first two, and never the third.
RUNS, CHECKED, VALIDATED = "runs", "checked against the database", "clinically validated"
READINESS = (RUNS, CHECKED, VALIDATED)
# The parts of the record that each count of step 8 reads.
COUNT_PARTS = {"coverage_by_year": ("role_patient", "role_anaesthetic"), "repeated_keys": ("role_patient", "role_anaesthetic"),
               "readings_by_kind": ("role_anaesthetic", "role_reading")}
# The databases whose figures can check a part: a training database's patients are fictional, and the invented
# hospital is made up.
REAL_DATABASES = ("production", "unsure")
# The most distinct values that the query of values returns.
MOST_VALUES = 50
PROBE_COLUMNS = {"link": ("anaesthetics", "with_rows", "without_rows"), "filter": ("rows_read", "passing"),
                 "flag": ("ones", "zeros", "empty")}
DROP = "IF OBJECT_ID('tempdb..#cohort') IS NOT NULL DROP TABLE #cohort;"
# A label column of a lookup table, by the words of its name.
LABEL_WORDS = {"name", "label", "title", "display", "disp", "description"}
# A column that identifies a person: a name, an address, a telephone number, an email address, a medical record number or
# another number that identifies a person, by the words of its name or of the dictionary's description. A date of birth is
# a column of the record in its own right, and is not among them. Such a column is never offered as the source of a flag,
# a kind, a value or a filter, and its values are never listed.
PERSON_NAME = re.compile(
    r"(^|_)(GIVEN|FAMILY|FIRST|LAST|MIDDLE|MAIDEN|PREFERRED|FULL|PAT|PATIENT|PERSON|STAFF|PROV|PROVIDER|EMP|EMPLOYEE|USER|"
    r"CONTACT|KIN)_?NAME($|_)|(^|_)(SURNAME|FORENAME|ADDR|ADDRESS|ADDRESS_LINE\d?|STREET|SUBURB|POSTCODE|POST_CODE|ZIP|ZIPCODE|"
    r"PHONE|PHONE_NO|TELEPHONE|MOBILE|FAX|EMAIL|E_MAIL|MRN|RECORD_NO|RECORD_NUM|MED_REC_NO|UR_NO|URN|SSN|MEDICARE|MEDICARE_NO|"
    r"NHI|IHI|PASSPORT|LICENCE_NO|LICENSE_NO)($|_)", re.IGNORECASE)
PERSON_WORDS = re.compile(
    r"\b(patient|person|staff member|employee|clinician|provider|user|surgeon|anaesthetist|anesthetist|doctor|nurse|"
    r"guardian|contact|next of kin)(\u2019s|'s)? (given |family |first |last |middle |full |preferred |maiden )?name\b|"
    r"\b(given|family|first|last|middle|maiden) name\b|\bsurname\b|"
    r"\bname of the (patient|person|staff member|employee|clinician|provider|user|surgeon|anaesthetist|anesthetist|doctor|nurse)\b|"
    r"\b(home|street|postal|mailing|residential|email|e-mail) address\b|\baddress of the (patient|person)\b|"
    r"\b(tele)?phone( number)?\b|\bmobile number\b|\be-?mail\b|\bmedical record number\b|\bsocial security\b|"
    r"\bmedicare (card )?number\b|\bnational (health )?identifier\b|\bpassport\b|\bdriver'?s licen[cs]e\b",
    re.IGNORECASE)


def identifies_person(column, description=""):
    """Whether a column identifies a person, by its name or the dictionary's description of it."""
    return bool(PERSON_NAME.search(column or "") or PERSON_WORDS.search(description or ""))

WORDING = {
    "headings": "Schemalyser could not find a heading for the {fields} in the dictionary's first row. Please name the "
                "heading under Name the headings yourself, then load the file again.",
    "cohort_comment": "Part 1 puts into #cohort at most {limit} anaesthetics that started in {year}, the earliest first, "
                      "from {tables}, which hold one row for each anaesthetic or fewer. #cohort is a temporary table that "
                      "exists only in your own SQL window and disappears when you close it. Nothing else is made or changed.",
    "timeout": "Before you run this script, set a time limit: open the Query menu, choose Query Options, then Execution, "
               "and enter a number of seconds in Execution time-out. Run the whole script; part 1 finishes first.",
    "charted_comment": "Part 2 lists every code of {column} charted on the anaesthetics in #cohort, with the number of "
                       "rows and of anaesthetics for each, rounded down to ten and left empty under ten, and the name "
                       "that {lookup} gives each code, most charted first. It reaches {path} from #cohort by their keys, "
                       "so that it reads only the rows of those anaesthetics.",
    "charted_comment_bare": "Part 2 lists every code of {column} charted on the anaesthetics in #cohort, with the number "
                            "of rows and of anaesthetics for each, rounded down to ten and left empty under ten, most "
                            "charted first. It reaches {path} from #cohort by their keys, so that it reads only the rows "
                            "of those anaesthetics.",
    "readings_comment": "Part 2 counts the readings of the anaesthetics in #cohort by their kind, as the hospital schema "
                        "translates them, with how many were accepted and hold a number, rounded down to ten and left empty under "
                        "ten. It reaches {path} from #cohort by their keys, so that it reads only those readings.",
    "safe_comment": "This count reads {tables} and no table of readings. Each count is rounded down to ten. A year or a "
                    "group with fewer than ten is left out, and a figure under ten within it is left empty, which the "
                    "page shows as under 10.",
    "names": "The hospital schema names the tables and local codes of the hospital's database, so this query is for use inside the "
             "hospital only.",
    "codes_says": "A person chose {count} local {codes} for this kind from the list of what is charted on {date}.",
    "codes_none": "No local code has been chosen for this kind yet.",
    "codes_question": "Please choose the local codes of this kind from the list of what is charted.",
    "database_headings": "The page could not find the headings of the data dictionary query in the first row. In SQL "
                         "Server Management Studio, open Tools, then Options, Query Results, SQL Server and Results to "
                         "Grid, tick Include column headers when copying or saving the results, run the query again, "
                         "then paste or save the result again.",
    "database_unreadable": "The page could not read this as the result of the data dictionary query. Make sure that it "
                           "is the result of the query shown here, with its headers, then paste it or choose the file again.",
    "no_database_dictionary": "The page adds the vendor's descriptions to a data dictionary made from the database, and "
                              "none is loaded yet.",
    "not_found": "The dictionary holds no column {name}.",
    "not_found_catalogue": "The result of the tables and columns query holds no column {name}.",
    "not_a_name": "Please write the replacement as TABLE.COLUMN, such as the name of a table, a full stop and the name of one of its columns.",
    "no_dictionary": "Please load the dictionary in step 2 first, because Schemalyser needs it to find how this part reaches that table.",
    "no_table": "The dictionary holds no table {name}.",
    "unreachable": "This part's own table does not reach {table} by any link that the dictionary shows, so Schemalyser cannot use that column here.",
    "unknown_count": "Schemalyser does not know a count named {name}.",
    "grid_columns": "The pasted text does not have the columns that the query returns ({wanted}). Please copy the whole results grid with Copy with Headers, and paste it again.",
    "grid_empty": "The pasted text holds no rows. If the query returned no rows, the grid is empty; otherwise please copy the whole results grid with Copy with Headers, and paste it again.",
    "folder_unreadable": "Schemalyser could not read the hospital schema in this file, so it has started a new hospital schema instead.",
    "cliff": "In {year}, {count} of {total} anaesthetics {what}, against {best_count} of {best_total} in {best_year}. A fall as sharp as this usually means that the data is held differently in that year.",
    "no_patient": "In {years}, most anaesthetics have no patient whom the hospital schema finds, so the link from each anaesthetic to its patient may be wrong. Look again at the patient's identifier in Anaesthetics at step 6.",
    "repeated": "In {view}, {count} values of the column that identifies a row are held by more than one row.",
    "stamp_query": "Written by Schemalyser {version} on {date}.",
    "stamp_file": "Written by Schemalyser {version} on {date}.",
    "stamp_result": "Pasted into Schemalyser {version} on {date}. The lines below are the result exactly as it was pasted.",
    "stamp_invented": "Run on the invented hospital into Schemalyser {version} on {date}. The lines below are the result exactly as the invented hospital gave it.",
    "check_confirmation": "Schemalyser could not apply the recorded answer for {about} again: {problem}",
    "check_codes": "Schemalyser could not apply the recorded codes of {key} again.",
    "differs": "{about} differs: the saved schema holds {before}, and the schema proposed again holds {after}.",
    "only_folder": "The saved schema holds {about}, and the schema proposed again does not.",
    "only_rebuilt": "The schema proposed again holds {about}, and the saved schema does not.",
    "training": "These queries were run on a training database, whose patients are fictional, so their figures say nothing about the real record. Run each of them again on the production database before the figures are used:",
    "table_gone": "The table {table} was in the earlier result and is not in the new one.",
    "column_gone": "The column {column} was in the earlier result and is not in the new one.",
    "table_new": "The table {table} is in the new result and was not in the earlier one.",
    "size_changed": "{table} held about {before} rows and now holds about {after}.",
    "row_gone": "The row {key} was in the earlier result and is not in the new one.",
    "row_new": "The row {key} is in the new result and was not in the earlier one.",
    "count_changed": "{column} of the row {key} was {before} and is now {after}, a change of more than a tenth.",
    "keep_failing": "This change fails the test on made-up rows, so Schemalyser keeps it only if you tick Keep it although the test fails and give the reason.",
    "no_probe": "Schemalyser offers no test query for this kind of change.",
    "values_comment": "Part 2 lists the commonest values of {column} among the rows of the anaesthetics in #cohort, at most {most}, with the number of rows that hold each, rounded down to ten and left empty under ten.",
    "values_safe": "This query lists the commonest values of {column}, at most {most}, with the number of rows that hold each, rounded down to ten and left empty under ten. It returns at most {most} rows and reads no table of readings. It reads each of these tables once: {tables}.",
    "probe_link_comment": "Part 2 counts how many of the anaesthetics in #cohort have at least one row through the link that {about} now makes, and how many have none, rounded down to ten and left empty under ten.",
    "probe_filter_comment": "This test query counts the rows of {view} that are read and how many of them pass the filter on {column}, rounded down to ten and left empty under ten.",
    "probe_flag_comment": "This test query counts the rows in which {about} is 1, 0 and empty, rounded down to ten and left empty under ten.",
    "probe_cohort": "It reads only the rows of the anaesthetics in #cohort, which part 1 makes.",
    "probe_small": "It reads no table of readings, and it reads each of these tables once: {tables}.",
    "probe_flag_two_comment": "This test query counts the rows in which {about} is 1 and 0, rounded down to ten and left empty under ten.",
    "probe_flag_two": "The flag is 1 in {ones} rows and 0 in {zeros}.",
    "sized": "{table}, which holds about {rows} rows",
    "sized_few": "{table}, which holds fewer than ten rows",
    "sized_unknown": "{table}, whose size is not known",
    "question_column": "The page proposes {source} as {title}. Is that right, and if not, which column holds it?",
    "question_nothing": "The page has found no column for {title}. Which column holds it, if the hospital records it?",
    "question_rows": "The page proposes {table} as the table that holds one row for each {what}. Is that right, and if not, which table holds them?",
    "assumed_plain": "The page assumes that {subject} is 1 where {source} holds {yes}, and 0 where it holds anything else or is empty. Confirm or change it in the form that opens after Yes.",
    "assumed_if_empty": "The page assumes that {subject} is 0 where {source} holds {no}, and 1 where it holds anything else or is empty. Confirm or change it in the form that opens after Yes.",
    "assumed_or_empty": "The page assumes that {subject} is 1 where {source} holds {yes}, 0 where it holds {no}, and empty otherwise. Confirm or change it in the form that opens after Yes.",
    "described_none": "Schemalyser proposed this draft hospital schema from a data dictionary on {date}, and no person has yet answered for any of its columns or tables.",
    "described_some": "Schemalyser proposed this draft hospital schema from a data dictionary on {date}. A person has since answered for {answered} of its {total} columns and tables, and {left} still to be answered.",
    "described_all": "Schemalyser proposed this draft hospital schema from a data dictionary on {date}. A person has since answered for every one of its {total} columns and tables.",
    "described_codes": " {count} still {hold} codes that a person has not yet translated.",
    "draft": "draft: {parts}",
    "state_runs": "The part compiles and runs on made-up rows, by the test on made-up rows.",
    "state_checked": "The counts that read the part have been run on the hospital's database and judged to look right.",
    "state_validated": "A sample of anaesthetics has been reconciled against the clinical record. The page cannot do this, so it never records this state; the hospital's own reconciliation does.",
    "sample_stamp": " The figures are from a sample: the anaesthetics of one year in #cohort, at most {limit}.",
    "probe_few": "Fewer than ten anaesthetics came back, so the test query says too little to judge the link.",
    "probe_link": "Of {total} anaesthetics of the year, {linked} have at least one row through this link and {none} have none.",
    "probe_filter": "Of {total} rows read, {passing} pass the filter.",
    "probe_flag": "The flag is 1 in {ones} rows, 0 in {zeros} and empty in {empty}.",
    "identifying": "The page does not offer columns that hold a person's name, address, contact details or medical record number, and {name} is one, so the page cannot use it here.",
    "identifying_values": "The page does not list the values of {name}, because it holds a person's name, address, contact details or medical record number.",
    "kinds_absent": "Codes were chosen at step 7 for {kinds}, but no readings of {those} appear in {year}. Look again at {codes} at step 7.",
    "invented_only": "The invented hospital answers only the queries written for the invented dictionary. With a real dictionary, your colleague runs each query on the hospital's database.",
    "invented_not_offered": "The page has not written this query yet. Write it first, then choose Run on the invented hospital.",
    "invented_failed": "The invented hospital could not run this query. Write it again and run it once more; if it still fails, answer this item by hand.",
    "invented_missing": "The invented hospital holds no table {table}, so it cannot run this query. Choose another table or column at step 6, or answer this item by hand.",
}
# Where a result came from when it was not pasted: the journal records it beside the result.
INVENTED_HOSPITAL = "invented hospital"
FIGURES = {"with_patient": "have a patient whom the hospital schema finds", "with_birth_date": "have a patient with a date of birth",
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


def _count_words(n, one, many):
    return f"{n:,} {one if n == 1 else many}" if n else ""


def _now():
    return dt.datetime.now().isoformat(timespec="minutes")


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")


def _day(iso):
    """A date as a person reads it, as 8 October 2026, from one written as 2026-10-08."""
    found = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{int(found.group(3))} {MONTHS[int(found.group(2)) - 1]} {found.group(1)}" if found else str(iso or "")


def _where(conditions):
    return ["WHERE  " + "\n  AND  ".join(conditions)] if conditions else []


def _rounded(expression, name):
    """A count rounded down to ten and left empty under ten, as every count of this screen is."""
    return f"CASE WHEN {expression} >= {LEAST} THEN ({expression}) - ({expression}) % 10 END AS {name}"


def _shown_count(value):
    return "fewer than ten" if value is None else f"about {value:,}" if isinstance(value, int) else str(value)


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
    return corrections.named_tables(binding)


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
        # Whether the dictionary is the invented one, which a person loads to try the page; the folder then says so.
        self.invented = False
        # Where the dictionary came from: "file" when a person chose it, "invented", or "saved" when a saved hospital
        # schema held it.
        self.dictionary_source = None
        # Whether the dictionary was read again from a saved hospital schema, and the vendor's descriptions added to a
        # dictionary made from the database, as {"file", "matched", "gained"}.
        self.dictionary_saved = False
        self.vendor = None
        # Where the result of the tables and columns came from: "database" when the data dictionary query gave it, and
        # "query" when the tables and columns query of step 5 did.
        self.catalogue_source = None
        self.proposer = None
        self.data = None
        self.catalogue = None
        self.catalogue_text = ""
        self.sizes = {}
        self.codes = {}
        self.counts = {}
        self.settings = {"format": FOLDER_FORMAT, "made": None, "updated": None, "database": None, "year": None,
                         "time_zone": None, "daylight_saving": None}
        # The readiness that the last save recorded, which the page names in its receipt.
        self._readiness = None
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
        # The corrections that a person kept, each with the outcome of its check, and the probes pasted for them.
        self.corrections = []
        self.probes = {}
        self.values = {}
        self._baseline = None
        self._checked = {}
        self._graph = None
        # Where the result being read came from, while the invented hospital answers a query; None for a paste.
        self.origin = None

    # The dictionary.

    def load_dictionary(self, data, tables=None, headings=None, name="dictionary.csv", tables_name="tables.csv", step="",
                        invented=False, source=None):
        own = {k: v for k, v in (headings or {}).items() if v}
        dictionary = _read_dictionary(data, tables, own)
        self.dictionary = dictionary
        self.dictionary_saved = source == "saved"
        self.vendor = None
        if self.catalogue_source == "database":
            self.catalogue, self.sizes, self.catalogue_text, self.catalogue_source = None, {}, "", None
        self.invented = bool(invented)
        self.dictionary_source = source or ("invented" if invented else "file")
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
        if self.invented:
            self.dictionary_entry["invented"] = True
            # With the invented dictionary, the invented hospital is the only database that the queries can run on.
            self.settings["database"] = "invented"
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
                "tablesFile": (self.dictionary_files or {}).get("tables_name"),
                "invented": self.invented, "source": self.dictionary_source, "saved": self.dictionary_saved,
                "vendor": dict(self.vendor) if self.vendor else None}

    # The data dictionary made from the database.

    def dictionary_query(self, step="", record=True):
        """The data dictionary query, offered at step 2. It names nothing of the hospital's, so it is the same for every
        hospital. The page shows it before anything is loaded, unrecorded; it is recorded with the other queries once its
        result is read, so that the saved hospital schema holds what was run."""
        if not record:
            stamp = f"-- {WORDING['stamp_query'].format(version=self.version or 'unknown', date=_day(_today()))}"
            return {"sql": stamp + "\n" + first_ask.database_query()}
        return {"sql": self.offer("data-dictionary", step, first_ask.database_query())}

    def load_from_database(self, data, name="data-dictionary.csv", step="", record=True):
        """Makes the data dictionary from the result of the data dictionary query, pasted or saved as a file, and reads
        the same result as the result of the tables and columns query, so that step 5 is answered at once. The
        dictionary's file is the result in a plain CSV with the query's twelve headings."""
        text = _text(bytes(data) if not isinstance(data, str) else data)
        try:
            rows = first_ask.database_rows(text)
        except first_ask.FirstAskError as error:
            raise DescribeError(WORDING["database_headings" if str(error) == "headings" else "database_unreadable"]) from None
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(first_ask.DATABASE_LAYOUT)
        writer.writerows(rows)
        canonical = out.getvalue().encode("utf-8")
        try:
            self.load_dictionary(canonical, name=_safe_file(name, "data-dictionary.csv"), step=step, source="database")
        except DescribeError:
            raise DescribeError(WORDING["database_unreadable"]) from None
        self.dictionary_saved = not record
        try:
            self.read_tables(_tsv(list(first_ask.LAYOUT), [row[:len(first_ask.LAYOUT)] for row in rows]), record=False)
        except DescribeError:
            raise DescribeError(WORDING["database_unreadable"]) from None
        self.catalogue_source = "database"
        if record:
            if "data-dictionary" not in self.journal:
                self.dictionary_query(step)
            self.journal["data-dictionary"].update({"pasted": _now(), "database": self.settings.get("database"),
                                                   "version": self.version,
                                                   "dictionary": f"{DICTIONARY_FOLDER}/{self.dictionary_files['name']}"})
        return {**self.dictionary_receipt(), "sized": len(self.sizes)}

    def upload(self, data, tables=None, headings=None, name="dictionary.csv", tables_name="tables.csv", step=""):
        """Reads a dictionary file that a person already has, as (kind, receipt): a result of the data dictionary query
        saved earlier makes the dictionary from the database ("database"); a vendor's export adds its descriptions to a
        dictionary made from the database ("vendor"), and is otherwise read as the dictionary itself ("dictionary")."""
        first = _text(bytes(data)[:20000]).lstrip("\ufeff").split("\n", 1)[0].replace("\r", "")
        cells = {c.strip().strip('"').upper() for c in first.split("\t" if "\t" in first else ",")}
        if set(first_ask.DATABASE_LAYOUT) <= cells:
            return "database", self.load_from_database(data, name, step)
        if self.dictionary is not None and self.dictionary_source == "database":
            return "vendor", self.add_descriptions(data, tables, headings, name, tables_name)
        return "dictionary", self.load_dictionary(data, tables, headings, name, tables_name, step)

    def add_descriptions(self, data, tables=None, headings=None, name="vendor-dictionary.csv", tables_name="vendor-tables.csv",
                         record=True):
        """Adds the vendor's descriptions to the data dictionary made from the database: each table and column of the
        vendor's file that the database holds, matched by name without regard to case, gives its description, and its
        primary key where the database declares none. Nothing the database does not hold is added."""
        if self.dictionary is None or self.dictionary_source != "database":
            raise DescribeError(WORDING["no_database_dictionary"])
        own = {k: v for k, v in (headings or {}).items() if v}
        vendor = _read_dictionary(data, tables, own)
        matched = gained = 0
        for table in self.dictionary.tables():
            theirs = vendor.table(table.name)
            if theirs is None:
                continue
            if theirs._description:
                table._description = theirs._description
            for key, entry in list(table.columns.items()):
                other = theirs.column(entry.name)
                if other is None:
                    continue
                matched += 1
                if other._description:
                    gained += not entry._description
                    table.columns[key] = dataclasses.replace(entry, _description=other._description)
            if not table.primary_key() and theirs.primary_key():
                key = tuple(table.column(k).name for k in theirs.primary_key() if table.column(k) is not None)
                if len(key) == len(theirs.primary_key()):
                    table.key = key
        self.proposer = None
        self._lookups = {}
        self.vendor = {"file": _safe_file(name, "vendor-dictionary.csv"), "matched": matched, "gained": gained}
        self.dictionary_files.update({"vendor_name": self.vendor["file"], "vendor_data": bytes(data),
                                      "vendor_tables_name": _safe_file(tables_name, "vendor-tables.csv") if tables is not None else None,
                                      "vendor_tables_data": bytes(tables) if tables is not None else None,
                                      "vendor_headings": own})
        if record and self.dictionary_entry is not None:
            self.dictionary_entry.update({"vendor_file": self.vendor["file"], "vendor_bytes": len(bytes(data)),
                                          "vendor_sha256": hashlib.sha256(bytes(data)).hexdigest(),
                                          "vendor_matched": matched, "vendor_gained": gained})
        return self.dictionary_receipt()

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
        self.catalogue_source = "query"
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
        columns = corrections.named_columns(binding)
        tables = corrections.named_tables(binding)
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
        """The answers so far. The figures count columns only; the table of each part is counted apart, under tables
        and tables_remaining. A column of a flag or a kind that is answered but whose codes nobody has yet translated is
        counted as untranslated, and not as confirmed or corrected."""
        counts = {"confirmed": 0, "corrected": 0, "not_sure": 0, "remaining": 0, "untranslated": 0, "total": 0,
                  "tables": 0, "tables_remaining": 0}
        for about, item in self._items():
            answer = (item.get("confirmation") or {}).get("answer")
            if about.endswith(" rows"):
                counts["tables"] += 1
                if not (answer in ("yes", "not sure") or (answer == "no" and item["status"] == "person")):
                    counts["tables_remaining"] += 1
                continue
            counts["total"] += 1
            if (answer == "yes" or (answer == "no" and item["status"] == "person")) and self._untranslated(about, item):
                counts["untranslated"] += 1
            elif answer == "yes":
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

    def _vocabulary_reason(self, view_name, binding):
        """Why the list of what is charted cannot be written for a column of a kind, or "" where it can."""
        view = self.views[view_name]
        role = self.data["roles"][view_name]
        links = {link["column"] for link in view.get("links", [])}
        link = next((c for c in ("anaesthetic_key", "patient_key") if c in links), None)
        if not binding:
            return "unbound"
        if link is None:
            return "unlinked"
        if not role["columns"][link].get("binding"):
            return "unbound_link"
        return ""

    def coding(self, view_name, column, item):
        """Whether a column of a flag or a kind is bound to a source that holds codes, which a person must translate
        before the view gives anything useful: None where it is not, or {"form": "flag" | "kind", "translated",
        "assumed": the sentence of any translation that the proposer guessed, "values": the values guessed to mean
        yes, "list": whether step 7 can list its codes}. A flag bound to a column of numbers is read as 1 and 0 and
        needs no translation."""
        binding = item.get("binding")
        if not binding or column["type"] not in ("flag", "flag_or_empty", "kind") or binding.get("window") or binding.get("joined"):
            return None
        about = f"{view_name}.{column['name']}"
        if column["type"] == "kind":
            translated = bool((self.codes.get(about) or {}).get("date"))
            if view_name == "role_reading" and column["name"] == "kind":
                translated = translated or any((k or {}).get("status") == "person" and k.get("codes")
                                               for k in self.data["kinds"].values())
            return {"form": "kind", "translated": translated, "assumed": "", "values": [],
                    "list": not self._vocabulary_reason(view_name, binding)}
        derive = binding.get("derive")
        if derive:
            return {"form": "flag", "translated": True, "assumed": "", "values": list(derive.get("values") or [])} \
                if derive.get("form") == "flag" else None
        step = propose.plan(column, binding)
        if step[0] == "const":
            return {"form": "flag", "translated": False, "assumed": "", "values": []}
        if step[0] == "flag_in" and not all(isinstance(v, (int, float)) for v in step[1]):
            _, yes, no, mode = step
            words = {"subject": rolemap.plain_about(about), "source": f"{binding['table']}.{binding['column']}",
                     "yes": _and(yes).replace(" and ", " or "), "no": _and(no).replace(" and ", " or ")}
            return {"form": "flag", "translated": False, "assumed": WORDING[f"assumed_{mode}"].format(**words),
                    "values": [str(v) for v in yes]}
        return None

    def _untranslated(self, about, item):
        view_name, _, name = about.partition(".")
        spec = next((c for c in self.views[view_name]["columns"] if c["name"] == name), None)
        found = self.coding(view_name, spec, item) if spec else None
        return bool(found and not found["translated"])

    def untranslated(self):
        """The columns answered whose codes are not yet translated, each named in plain words with its source."""
        return [{"about": about, "title": rolemap.plain_about(about, True), "from": item.get("from", "").split(",")[0]}
                for about, item in self._items() if not about.endswith(" rows")
                and ((item.get("confirmation") or {}).get("answer") == "yes"
                     or ((item.get("confirmation") or {}).get("answer") == "no" and item["status"] == "person"))
                and self._untranslated(about, item)]

    def unfinished(self):
        """What keeps the folder a draft, as the words of settings.json ("52 columns unanswered and 3 still to
        translate"), or "" where nothing does."""
        t = self.tally() if self.data is not None else None
        if t is None:
            return ""
        unanswered = [p for p in (_count_words(t["remaining"], "column", "columns"), _count_words(t["tables_remaining"], "table", "tables")) if p]
        parts = [f"{_and(unanswered)} unanswered"] if unanswered else []
        if t["untranslated"]:
            n = t["untranslated"]
            parts.append(f"{n:,} {'column' if n == 1 else 'columns'} confirmed whose codes are not yet translated")
        return " and ".join(parts)

    def questions(self):
        """The bindings marked not sure, each named in plain words, with a question that states the proposal and asks
        whether it is right, and the meaning of the column."""
        found = []
        for about, item in self._items():
            if (item.get("confirmation") or {}).get("answer") != "not sure":
                continue
            view, _, column = about.partition(".")
            view_name = view.split(" ")[0]
            spec = next((c for c in self.views[view_name]["columns"] if c["name"] == column), None)
            meaning = rolemap.plain(spec["meaning"], view) if spec else rolemap.plain(self.views[view_name]["description"])
            binding = item.get("binding")
            if about.endswith(" rows"):
                what = self.views[view_name]["one_row_per"]
                question = WORDING["question_rows"].format(table=binding["table"], what=what) if binding else \
                    WORDING["question_nothing"].format(title=f"the table that holds one row for each {what}")
            elif binding:
                question = WORDING["question_column"].format(source=f"{binding['table']}.{binding['column']}", title=rolemap.plain_about(about))
            else:
                question = WORDING["question_nothing"].format(title=rolemap.plain_about(about))
            found.append({"about": about, "title": rolemap.plain_about(about, True), "question": question, "meaning": meaning})
        return found

    # Corrections in plain forms, each checked on invented rows before it is kept.

    def _clone(self):
        """A copy of this sitting on which a correction can be tried without changing the sitting itself."""
        other = copy.copy(self)
        other.data = copy.deepcopy(self.data)
        other.codes = copy.deepcopy(self.codes)
        other.settings = dict(self.settings)
        other._lookups = dict(self._lookups)
        return other

    def _built(self, correction):
        try:
            built = corrections.build(self, correction)
        except corrections.CorrectionError as error:
            raise DescribeError(str(error)) from None
        # A column that identifies a person is never the source of a flag, a kind, a value or a filter, even written by hand.
        if self.offers_identifying(built["about"]):
            return built
        named = []
        if built.get("filter"):
            named.append((built["filter"]["table"], built["filter"]["column"]))
        binding = built.get("binding") or {}
        if binding.get("joined"):
            named.append((binding["joined"]["table"], binding["joined"]["text"]))
        elif binding:
            named.append((binding["table"], binding["column"]))
        for table, column in named:
            if self._identifying(table, column):
                raise DescribeError(WORDING["identifying"].format(name=f"{table}.{column}"))
        return built

    def _identifying(self, table, column):
        """Whether a column of the dictionary identifies a person, by its name or the dictionary's description."""
        if self.dictionary is None:
            return identifies_person(column)
        return identifies_person(column, self.dictionary.description(table, column) or "")

    def offers_identifying(self, about):
        """Whether a column that identifies a person may be chosen for this column of a part: only for a key or a link,
        which may need a person's identifier to join, and for a column that the record itself marks as identifying,
        such as the text of a note. Never for the filter of a part's rows, a flag, a kind or a value."""
        if about.endswith(" rows"):
            return False
        view_name, _, name = about.partition(".")
        view = self.views.get(view_name)
        spec = next((c for c in (view or {}).get("columns", []) if c["name"] == name), None)
        if spec is None:
            return False
        links = {link["column"] for link in view.get("links", [])}
        return spec["type"] == "key" or name in links or bool(spec.get("identifying"))

    def correction_preview(self, correction):
        """What a correction means, as the sentence that the map records and the SQL of the view that it changes."""
        built = self._built(correction)
        trial = self._clone()
        corrections.apply(trial, built, {"answer": "no", "date": _today()})
        return {"sentence": built["sentence"], "sql": trial.view_sql(built["view"]), "view": built["view"]}

    def _baseline_check(self):
        mark = corrections.fingerprint(self)
        if self._baseline is None or self._baseline[0] != mark:
            self._baseline = (mark, corrections.run_check(self))
        return self._baseline[1]

    def check_model(self):
        """The check of the map as it stands, with no change."""
        if self.data is None:
            raise DescribeError(WORDING["no_dictionary"])
        return corrections.report(self._baseline_check(), self._baseline_check(), change=False)

    def correction_check(self, correction):
        """Tests a correction on invented rows: the whole map with the change, against the map as it stands."""
        built = self._built(correction)
        began = time.perf_counter()
        before = self._baseline_check()
        trial = self._clone()
        corrections.apply(trial, built, {"answer": "no", "date": _today()})
        after = corrections.run_check(trial)
        found = corrections.report(before, after)
        found["sentence_of_change"] = built["sentence"]
        found["seconds"] = round(time.perf_counter() - began, 1)
        self._checked[json.dumps(correction, sort_keys=True)] = found
        return found

    def correction_keep(self, correction, although=False, reason="", date=None):
        """Keeps a correction that has been checked. One that fails its check is kept only with although and a reason,
        and both are recorded with it."""
        date = date or _today()
        built = self._built(correction)
        key = json.dumps(correction, sort_keys=True)
        found = self._checked.get(key) or self.correction_check(correction)
        reason = " ".join((reason or "").split())[:400]
        if not found["passed"] and not (although and reason):
            raise DescribeError(WORDING["keep_failing"])
        result = corrections.outcome(found)
        answer = self._answer_of(built)
        record = {"answer": answer, "date": date, "replacement": built["source"], "correction": correction, "check": result}
        if not found["passed"]:
            record["reason"] = reason
        corrections.apply(self, built, record)
        self.settings["updated"] = date
        if answer == "yes":
            mine = [i for i, c in enumerate(self.confirmations) if c["attribute"] == built["about"]]
            if mine and self.confirmations[mine[-1]]["answer"] == "yes" and not self.confirmations[mine[-1]].get("correction"):
                del self.confirmations[mine[-1]]
        self.confirmations.append({"attribute": built["about"], "answer": answer, "replacement": built["source"], "date": date,
                                   "note": "", "version": self.version, "correction": json.dumps(correction, sort_keys=True),
                                   "test": result, "reason": reason if not found["passed"] else ""})
        self.corrections.append({"name": "correction", "about": built["about"], "form": built["form"], "says": built["sentence"],
                                 "test": result, "passed": found["passed"], "reason": reason if not found["passed"] else "",
                                 "date": date, "version": self.version})
        self._checked.pop(key, None)
        return {"kept": built["about"], "probe": self.probe_kind(built["about"])}

    def _answer_of(self, built):
        """The answer that a kept correction records. A form that keeps the column already bound, such as the 1-or-0
        form or a translation of codes on the proposed column, confirms it with a translation, and is recorded as yes;
        any other correction is recorded as no. A column that a person has already corrected stays corrected."""
        if built["form"] not in ("column", "derived", "codes") or not built.get("column"):
            return "no"
        item = self.data["roles"][built["view"]]["columns"][built["column"]]
        current = item.get("binding")
        if (item.get("confirmation") or {}).get("answer") == "no" or not current:
            return "no"
        if built["form"] == "codes":
            return "yes"
        new = built["binding"]
        same = (current["table"].upper(), current["column"].upper()) == (new["table"].upper(), new["column"].upper())
        return "yes" if same else "no"

    def replay_correction(self, correction, date=None, check="", reason=""):
        """Applies a correction recorded in confirmations.csv again, without checking it, as the folder check does."""
        built = self._built(correction)
        record = {"answer": self._answer_of(built), "date": date or _today(), "replacement": built["source"],
                  "correction": correction, "check": check}
        if reason:
            record["reason"] = reason
        corrections.apply(self, built, record)

    def names(self):
        """The tables of the dictionary, by name only, for the page's lists."""
        if self.dictionary is None:
            raise DescribeError(WORDING["no_dictionary"])
        return {"tables": sorted((t.name for t in self.dictionary.tables() if t.columns), key=str.upper)}

    def columns_of(self, table):
        if self.dictionary is None:
            raise DescribeError(WORDING["no_dictionary"])
        held = self.dictionary.table(table) if NAME.match(table or "") else None
        if held is None:
            raise DescribeError(WORDING["no_table"].format(name=table))
        found = []
        for entry in held.columns.values():
            present = None
            if self.catalogue is not None:
                known = self.catalogue.table(held.name)
                present = bool(known is not None and known.column(entry.name) is not None)
            description = self.dictionary.description(held.name, entry.name) or ""
            found.append({"name": entry.name, "type": entry.data_type or "", "key": entry.name in held.primary_key(), "present": present,
                          "description": description, "identifying": identifies_person(entry.name, description)})
        return {"table": held.name, "columns": found}

    def joins_from(self, table):
        """The joins that the dictionary's keys suggest from one table: to a table whose whole key a column names, and
        from a table that names this table's key, which may repeat rows."""
        if self.dictionary is None:
            raise DescribeError(WORDING["no_dictionary"])
        held = self.dictionary.table(table) if NAME.match(table or "") else None
        if held is None:
            raise DescribeError(WORDING["no_table"].format(name=table))
        graph = corrections._graph(self)
        found = [{"from": column, "table": target, "to": key, "repeats": False} for column, target, key, _ in graph.hops(held.name)]
        key = held.primary_key()
        if len(key) == 1:
            for other in self.dictionary.tables():
                if other.name.upper() == held.name.upper():
                    continue
                entry = other.column(key[0])
                if entry is not None and other.primary_key() != (entry.name,):
                    found.append({"from": key[0], "table": other.name, "to": entry.name, "repeats": True})
        seen, unique = set(), []
        for item in found:
            mark = (item["from"].upper(), item["table"].upper(), item["to"].upper())
            if mark not in seen:
                seen.add(mark)
                unique.append(item)
        return {"table": held.name, "joins": unique[:40]}

    # The distinct values of a column, for choosing a filter or the values of a flag.

    def values_query(self, about, table, column, year=None, step=""):
        view_name = about.split(" ")[0].split(".")[0]
        role = (self.data or {}).get("roles", {}).get(view_name)
        if role is None:
            raise DescribeError(corrections.WORDING["not_drafted"].format(view=view_name))
        try:
            table, column, _ = corrections._column(self, table, column)
            path = corrections.path_to(self, role, table)
        except corrections.CorrectionError as error:
            raise DescribeError(str(error)) from None
        if self._identifying(table, column):
            raise DescribeError(WORDING["identifying_values"].format(name=f"{table}.{column}"))
        year = int(year or self.settings.get("year") or dt.date.today().year - 1)
        name = f"values-{view_name}-{table}-{column}"
        script, sql, order = self._counted(view_name, path, table, column, year)
        if script:
            second = "\n".join([f"SELECT TOP ({MOST_VALUES}) CAST({sql} AS nvarchar(254)) AS value,",
                                "       " + _rounded("COUNT(*)", "rows"), *order,
                                f"GROUP  BY CAST({sql} AS nvarchar(254))", "ORDER  BY COUNT(*) DESC;"])
            text = self._script(year, _wrap(WORDING["values_comment"].format(column=f"{table}.{column}", most=MOST_VALUES)), second)
        else:
            body = "\n".join([f"SELECT TOP ({MOST_VALUES}) CAST({sql} AS nvarchar(254)) AS value,",
                              "       " + _rounded("COUNT(*)", "rows"), *order,
                              f"GROUP  BY CAST({sql} AS nvarchar(254))", "ORDER  BY COUNT(*) DESC;"])
            text = "\n".join([_wrap(WORDING["values_safe"].format(column=f"{table}.{column}", most=MOST_VALUES,
                                                                  tables=self._sized_text(self._sized_names(order)))),
                              _wrap(WORDING["names"]), body])
        return {"sql": self.offer(name, step, text, about=about, table=table, column=column, year=year if script else None),
                "name": name, "script": script}

    def read_values(self, name, text):
        if name not in self.journal:
            raise DescribeError(WORDING["unknown_count"].format(name=name))
        columns, rows = read_grid(text, ("value", "rows"))
        self.pasted(name, text)
        parsed = [{"value": r[0], "rows": _number(r[1])} for r in rows]
        self.values[name] = parsed
        return {"values": parsed}

    def _sized_text(self, names):
        """Tables named with their sizes, as the result of the tables and columns query gives them."""
        parts = []
        for name in dict.fromkeys(names):
            size = self.sizes.get(name.upper())
            parts.append(WORDING["sized_unknown"].format(table=name) if size is None
                         else WORDING["sized_few"].format(table=name) if size < LEAST
                         else WORDING["sized"].format(table=name, rows=f"{size:,}"))
        return _and(parts)

    def _sized_names(self, lines):
        return [m for line in lines for m in re.findall(r"(?:FROM|JOIN)\s+\[?([A-Za-z_][\w]*)\]?\s+(?:AS\s+)?t\d", line)]

    def _counted(self, view_name, path, table, column, year, filters=True):
        """How a count of one column of a view reaches it: over the view's own table where that is small, or from
        #cohort in a script of two parts where it may be large. Returns (script, column reference, FROM lines), the
        lines ending with the view's own filters unless filters is false."""
        role = self.data["roles"][view_name]
        base = role["rows"]["binding"]["table"]
        names = [base] + [s[2] for s in path]
        small = view_name in ("role_patient", "role_anaesthetic") or all(
            self.sizes.get(n.upper()) is not None and self.sizes[n.upper()] < LARGE for n in names)
        links = {link["column"]: link["to"] for link in self.views[view_name].get("links", [])}
        link = next((c for c in ("anaesthetic_key", "patient_key") if c in links and role["columns"][c].get("binding")), None)
        if small or link is None:
            aliases, joins = {(): "t0"}, []
            alias, _ = propose.walk(path, aliases, joins)
            conditions = []
            for item in (role["rows"]["binding"].get("filter") or []) if filters else []:
                end, _ = propose.walk(item["path"], aliases, joins)
                conditions.append(propose.filter_sql(item, f"{end}.{_name(item['column'])}"))
            lines = [f"FROM   {_name(base)} AS t0 WITH (NOLOCK)"]
            for join in joins:
                head, _, rest = join.partition(" ON ")
                words = head.split(" ")
                lines.append(" ".join(words[:-1]) + f" AS {words[-1]} WITH (NOLOCK) ON " + rest)
            return False, f"{alias}.{_name(column)}", lines + _where(conditions)
        cohort = "anaesthetic_key" if link == "anaesthetic_key" else "patient_key"
        probe = {"table": path[-1][2] if path else base, "column": column, "path": path, "data_type": ""}
        lines, expressions, _, where = self._reached(view_name, link, cohort, ["__probe"], raw={"__probe"},
                                                     extra={"__probe": probe}, filters=filters)
        return True, expressions["__probe"], lines + _where(where)

    # The probe of a kept correction against the real database.

    def probe_kind(self, about):
        """The kind of probe that suits a kept correction: link, filter or flag, or None."""
        view_name = about.split(" ")[0].split(".")[0]
        role = (self.data or {}).get("roles", {}).get(view_name)
        if role is None:
            return None
        if about.endswith(" rows"):
            return "filter" if role["rows"].get("binding", {}).get("filter") else None
        column = about.split(".", 1)[1]
        spec = next((c for c in self.views[view_name]["columns"] if c["name"] == column), None)
        binding = role["columns"].get(column, {}).get("binding")
        links = {link["column"] for link in self.views[view_name].get("links", [])}
        if not binding or spec is None:
            return None
        if column in links and column in ("anaesthetic_key", "patient_key"):
            if view_name == "role_anaesthetic" and column == "patient_key":
                patient = self.data["roles"]["role_patient"]["columns"]["patient_key"].get("binding")
                return "link" if patient and not patient.get("path") else None
            return "link"
        if spec["type"] in ("flag", "flag_or_empty"):
            return "flag"
        return None

    def probe_query(self, about, year=None, step=""):
        kind = self.probe_kind(about)
        if kind is None:
            raise DescribeError(WORDING["no_probe"])
        year = int(year or self.settings.get("year") or dt.date.today().year - 1)
        view_name = about.split(" ")[0].split(".")[0]
        role = self.data["roles"][view_name]
        name = "probe-" + re.sub(r"[^\w]+", "-", about).strip("-")
        if kind == "link":
            column = about.split(".", 1)[1]
            if view_name == "role_anaesthetic":
                patient = self.data["roles"]["role_patient"]
                p_binding = patient["columns"]["patient_key"]["binding"]
                second = "\n".join([
                    "SELECT " + _rounded("n.total", "anaesthetics") + ",",
                    "       " + _rounded("n.linked", "with_rows") + ",",
                    "       " + _rounded("n.total - n.linked", "without_rows"),
                    "FROM   (SELECT COUNT(*) AS total,",
                    "               SUM(CASE WHEN p.found = 1 THEN 1 ELSE 0 END) AS linked",
                    "        FROM   #cohort AS c",
                    f"               LEFT JOIN (SELECT DISTINCT pp.{_name(p_binding['column'])} AS patient_key, 1 AS found",
                    f"                          FROM   {_name(p_binding['table'])} AS pp WITH (NOLOCK)) AS p ON p.patient_key = c.patient_key) AS n;"])
            else:
                cohort = "anaesthetic_key" if column == "anaesthetic_key" else "patient_key"
                lines, _, _, where = self._reached(view_name, column, cohort, [])
                inner = "\n".join("                       " + line for line in lines + _where(where))
                second = "\n".join([
                    "SELECT " + _rounded("n.total", "anaesthetics") + ",",
                    "       " + _rounded("n.linked", "with_rows") + ",",
                    "       " + _rounded("n.total - n.linked", "without_rows"),
                    "FROM   (SELECT (SELECT COUNT(*) FROM #cohort) AS total,",
                    "               (SELECT COUNT(DISTINCT c.anaesthetic_key)",
                    inner + ") AS linked) AS n;"])
            text = self._script(year, _wrap(WORDING["probe_link_comment"].format(about=about)), second)
            columns = PROBE_COLUMNS["link"]
        else:
            if kind == "filter":
                filters = role["rows"]["binding"]["filter"]
                item = filters[-1]
                script, ref, lines = self._counted(view_name, item["path"], item["table"], item["column"], year, filters=False)
                condition = propose.filter_sql(item, ref)
                select = ["SELECT " + _rounded("COUNT(*)", "rows_read") + ",",
                          "       " + _rounded(f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)", "passing")]
                comment = WORDING["probe_filter_comment"].format(view=view_name, column=f"{item['table']}.{item['column']}")
            else:
                column = about.split(".", 1)[1]
                spec = next(c for c in self.views[view_name]["columns"] if c["name"] == column)
                binding = role["columns"][column]["binding"]
                script, ref, lines = self._counted(view_name, binding["path"], binding["table"], binding["column"], year)
                expression = propose.render(propose.plan(spec, binding), ref)
                two = spec["type"] == "flag"
                select = ["SELECT " + _rounded(f"SUM(CASE WHEN {expression} = 1 THEN 1 ELSE 0 END)", "ones") + ",",
                          "       " + _rounded(f"SUM(CASE WHEN {expression} = 0 THEN 1 ELSE 0 END)", "zeros") + ("" if two else ",")]
                if not two:
                    select.append("       " + _rounded(f"SUM(CASE WHEN {expression} IS NULL THEN 1 ELSE 0 END)", "empty"))
                comment = WORDING["probe_flag_two_comment" if two else "probe_flag_comment"].format(about=rolemap.plain_about(about))
            body = "\n".join(select + lines) + ";"
            if script:
                text = self._script(year, _wrap(comment + " " + WORDING["probe_cohort"]), body)
            else:
                text = "\n".join([_wrap(comment + " " + WORDING["probe_small"].format(tables=self._sized_text(self._sized_names(lines)))),
                                  _wrap(WORDING["names"]), body])
            columns = self._probe_columns(about, kind)
        sql = self.offer(name, step, text, about=about, year=year if kind == "link" or script else None, probe=kind)
        return {"sql": sql, "name": name, "kind": kind, "columns": list(columns)}

    def _probe_columns(self, about, kind):
        """The columns of a probe. A flag that the record never leaves empty is counted as 1 and 0 alone."""
        if kind == "flag":
            view_name, _, column = about.partition(".")
            spec = next((c for c in self.views[view_name]["columns"] if c["name"] == column), {})
            if spec.get("type") == "flag":
                return ("ones", "zeros")
        return PROBE_COLUMNS[kind]

    def read_probe(self, about, text, record=True):
        kind = self.probe_kind(about)
        if kind is None:
            raise DescribeError(WORDING["no_probe"])
        name = "probe-" + re.sub(r"[^\w]+", "-", about).strip("-")
        columns, rows = read_grid(text, self._probe_columns(about, kind))
        if record:
            self.pasted(name, text)
        self.probes[about] = {"kind": kind, "columns": columns, "rows": rows, "date": _today()}
        return {"rows": len(rows), "findings": self.probe_findings(about)}

    def probe_findings(self, about):
        held = self.probes.get(about)
        if not held or not held["rows"]:
            return []
        row = dict(zip(held["columns"], [_number(v) for v in held["rows"][0]]))
        if held["kind"] == "link":
            if row.get("anaesthetics") is None:
                return [WORDING["probe_few"]]
            return [WORDING["probe_link"].format(total=_shown_count(row.get("anaesthetics")), linked=_shown_count(row.get("with_rows")),
                                                 none=_shown_count(row.get("without_rows")))]
        if held["kind"] == "filter":
            return [WORDING["probe_filter"].format(total=_shown_count(row.get("rows_read")), passing=_shown_count(row.get("passing")))]
        if "empty" not in held["columns"]:
            return [WORDING["probe_flag_two"].format(ones=_shown_count(row.get("ones")), zeros=_shown_count(row.get("zeros")))]
        return [WORDING["probe_flag"].format(ones=_shown_count(row.get("ones")), zeros=_shown_count(row.get("zeros")),
                                             empty=_shown_count(row.get("empty")))]

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
                reason = self._vocabulary_reason(view_name, binding)
                held = self.codes.get(key, {})
                lookup = held.get("lookup") or (self._lookup(binding["table"], binding["column"]) if binding else None)
                found.append({"key": key, "title": rolemap.plain_about(key, True), "view": view_name, "column": column["name"], "vocabulary": vocabulary,
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
            raise DescribeError(f"The hospital schema holds no column {key}.")
        return entry

    def _reached(self, view_name, link, cohort_column, columns, raw=(), extra=None, filters=True):
        """The joins of a view turned round so that they start from #cohort and reach the view's own table last, by
        keys alone, then the lookups of its other columns. Returns (lines, {column: expression}, tables in order)."""
        role = self.data["roles"][view_name]
        base = role["rows"]["binding"]["table"]
        link_binding = role["columns"][link]["binding"]
        window = link_binding.get("window")
        path = [list(step) for step in link_binding["path"]]
        aliases = {(): "t0"}
        tables = {(): base}
        for i in range(len(path)):
            prefix = tuple(propose.step_key(s) for s in path[:i + 1])
            aliases[prefix] = f"t{len(aliases)}"
            tables[prefix] = path[i][2]
        last = tuple(propose.step_key(s) for s in path)
        where = []
        if window:
            # A link by a shared key and a time window: from #cohort to the anaesthetic's own table, then to the rows
            # that share its key, whose time is tested against the window below.
            lines = ["FROM   #cohort AS c",
                     f"JOIN   {_name(window['table'])} AS w WITH (NOLOCK) ON w.{_name(window['output'])} = c.{cohort_column}",
                     f"JOIN   {_name(tables[last])} AS {aliases[last]} WITH (NOLOCK) ON {aliases[last]}.{_name(link_binding['column'])} = w.{_name(window['key'])}"]
            order = [window["table"], tables[last]]
        else:
            lines = ["FROM   #cohort AS c",
                     f"JOIN   {_name(tables[last])} AS {aliases[last]} WITH (NOLOCK) ON {aliases[last]}.{_name(link_binding['column'])} = c.{cohort_column}"]
            order = [tables[last]]
        for i in range(len(path) - 1, -1, -1):
            before = tuple(propose.step_key(s) for s in path[:i])
            after = tuple(propose.step_key(s) for s in path[:i + 1])
            step = path[i]
            on = " AND ".join(f"{aliases[before]}.{_name(a)} = {aliases[after]}.{_name(b)}" for a, b in propose.step_pairs(step))
            lines.append(f"JOIN   {_name(tables[before])} AS {aliases[before]} WITH (NOLOCK) ON {on}")
            order.append(tables[before])

        def forward(steps):
            prefix = ()
            for step in steps:
                following = prefix + (propose.step_key(step),)
                if following not in aliases:
                    aliases[following] = f"t{len(aliases)}"
                    tables[following] = step[2]
                    on = " AND ".join(f"{aliases[following]}.{_name(b)} = {aliases[prefix]}.{_name(a)}" for a, b in propose.step_pairs(step))
                    lines.append(f"LEFT JOIN {_name(step[2])} AS {aliases[following]} WITH (NOLOCK) ON {on}")
                prefix = following
            return aliases[prefix]

        expressions = {}
        specs = {c["name"]: c for c in self.views[view_name]["columns"]}
        for name in columns:
            binding = (extra or {}).get(name) or role["columns"][name].get("binding")
            if not binding:
                expressions[name] = propose._empty(specs[name])
                continue
            ref = f"{forward(binding['path'])}.{_name(binding['column'])}"
            if binding.get("window"):
                expressions[name] = f"w.{_name(binding['window']['output'])}" if name == link else "NULL"
                continue
            if binding.get("joined"):
                expressions[name] = propose.joined_sql(binding["joined"], ref, f"j{len(aliases)}")
                continue
            if name in raw:
                expressions[name] = ref
                continue
            codes = None
            if specs[name]["type"] == "kind":
                if view_name == "role_reading" and name == "kind":
                    codes = {k: item.get("codes", []) for k, item in self.data["kinds"].items()}
                else:
                    codes = self._vocabulary_codes(view_name).get(name, {})
            expressions[name] = propose.render(propose.plan(specs[name], binding, codes), ref)
        if window:
            time_binding = role["columns"][window["time"]].get("binding")
            time_ref = f"{forward(time_binding['path'])}.{_name(time_binding['column'])}" if time_binding else "NULL"
            where.append(propose.window_on("w", window, time_ref))
        for item in (role["rows"]["binding"].get("filter") or []) if filters else []:
            where.append(propose.filter_sql(item, f"{forward(item['path'])}.{_name(item['column'])}"))
        return lines, expressions, order, where

    def _direct(self, view_name, link, column):
        """(table, key) where a column of a part sits in a table that the part reaches by the very key that links the
        part to #cohort, such as the sex in the patients' own table: that table is then joined to #cohort directly, and
        the part's own table, which may lack rows for some patients, is not read. None otherwise."""
        role = self.data["roles"][view_name]
        link_binding = role["columns"][link].get("binding") or {}
        binding = role["columns"][column].get("binding") or {}
        if not link_binding or link_binding.get("path") or link_binding.get("window") or role["rows"]["binding"].get("filter"):
            return None
        path = binding.get("path") or []
        if len(path) != 1 or len(path[0]) != 4 or binding.get("window") or binding.get("joined"):
            return None
        start, one, table, other = path[0]
        if start.upper() != link_binding["table"].upper() or one.upper() != link_binding["column"].upper():
            return None
        return table, other

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
        self.set_settings(year=year)
        view_name, column = entry["view"], entry["column"]
        cohort_column = "anaesthetic_key" if entry["link"] == "anaesthetic_key" else "patient_key"
        direct = self._direct(view_name, entry["link"], column)
        if direct:
            table, shared = direct
            lines = ["FROM   #cohort AS c",
                     f"JOIN   {_name(table)} AS t0 WITH (NOLOCK) ON t0.{_name(shared)} = c.{cohort_column}"]
            ref = f"t0.{_name(self.data['roles'][view_name]['columns'][column]['binding']['column'])}"
            order, where = [table], []
        else:
            lines, expressions, order, where = self._reached(view_name, entry["link"], cohort_column, [column], raw={column})
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
        second = "\n".join(select + lines + _where(where) + [group, "ORDER  BY COUNT(*) DESC;"])
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
        # A code chosen as other is kept as chosen, so that the page shows it as chosen, and is translated as any code
        # left unchosen is.
        chosen = {str(code): kind for code, kind in chosen.items() if kind in entry["kinds"]}
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
                                   "says": WORDING["codes_says"].format(count=len(codes), codes="code" if len(codes) == 1 else "codes", date=_day(date)),
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
                if kind != "other":
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
        self.set_settings(year=year)
        rounding = 10
        rounded = lambda n: f"g.{n} - g.{n} % {rounding} AS {n}"  # noqa: E731
        # A figure within a year that is under ten is left empty, never shown as 0.
        within = lambda n: f"CASE WHEN g.{n} >= {LEAST} THEN g.{n} - g.{n} % {rounding} END AS {n}"  # noqa: E731
        coverage = ("with_patient", "with_birth_date", "with_death_date", "test_patients", "with_stop", "stop_before_start")
        found = []
        small = self._tables_of("role_patient") + self._tables_of("role_anaesthetic")
        coverage_sql = f"""SELECT g.start_year,
       {rounded('anaesthetics')},
       {(',' + chr(10) + '       ').join(within(n) for n in coverage)}
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
            lines, expressions, order, where = self._reached("role_reading", "anaesthetic_key", "anaesthetic_key", columns)
            inner = ("SELECT " + ",\n       ".join(f"{expressions[c]} AS {c}" for c in columns)
                     + ",\n       c.anaesthetic_key\n" + "\n".join(lines + _where(where)))
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
WHERE  g.readings >= {LEAST}
ORDER  BY g.kind;"""
            found.append({"name": "readings_by_kind", "safe": False, "year": year,
                          "sql": self._script(year, _wrap(WORDING["readings_comment"].format(path=_and(order))), second),
                          "tables": self._sized(self._tables_of("role_anaesthetic") + order)})
        for item in found:
            item["sql"] = self.offer(f"count-{item['name']}", step, item["sql"], year=item.get("year"))
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
        held.update({"looks_right": looks_right, "note": " ".join((note or "").split())[:400], "judged": date or _today(),
                     "database": self.settings.get("database") or "unsure"})

    def finding_codes(self, name):
        """The list of codes at step 7 that each finding of a count leads to, as {finding: key}."""
        return {f: "role_reading.kind" for f in self.findings(name) if f.endswith("at step 7.") and f.startswith("Codes were chosen")}

    def finding_about(self, name):
        """The column of step 6 that each finding of a count leads to, as {finding: about}."""
        return {f: "role_anaesthetic.patient_key" for f in self.findings(name) if f.endswith("Look again at the patient's identifier in Anaesthetics at step 6.")}

    def findings(self, name):
        held = self.counts.get(name) or {}
        if not held.get("rows"):
            return []
        records = [dict(zip(held["columns"], [_number(v) if _number(v) is not None else v for v in row])) for row in held["rows"]]
        found = []
        if name == "coverage_by_year":
            records = [r for r in records if isinstance(r.get("anaesthetics"), (int, float)) and r["anaesthetics"]]
            # A year in which most anaesthetics have no patient, which a comparison of years misses when every year has it.
            lacking = [str(r["start_year"]) for r in records if r["anaesthetics"] >= 2 * LEAST
                       and (r.get("with_patient") in (None, "") or (isinstance(r.get("with_patient"), (int, float))
                                                                   and r["with_patient"] * 2 < r["anaesthetics"]))]
            if lacking:
                found.append(WORDING["no_patient"].format(years=_and(lacking)))
            for figure, what in FIGURES.items():
                # A figure under ten comes back empty, and says too little to compare.
                shares = [(r[figure] / r["anaesthetics"], r) for r in records if isinstance(r.get(figure), (int, float))]
                if not shares:
                    continue
                best_share, best = max(shares, key=lambda s: (s[0], s[1]["anaesthetics"]))
                for share, r in shares:
                    if r is not best and best_share > 0 and share <= best_share / 2 and (best_share - share) * r["anaesthetics"] >= 10:
                        found.append(WORDING["cliff"].format(year=r["start_year"], count=f"{r.get(figure) or 0:,}", total=f"{r['anaesthetics']:,}",
                                                             what=what, best_count=f"{best.get(figure) or 0:,}",
                                                             best_total=f"{best['anaesthetics']:,}", best_year=best["start_year"]))
        elif name == "readings_by_kind":
            # A kind chosen at step 7 of which the count holds no readings at all, which usually means a wrong code.
            chosen = [k for k in dict.fromkeys(((self.codes.get("role_reading.kind") or {}).get("chosen") or {}).values()) if k != "other"]
            seen = {str(r.get("kind")) for r in records}
            absent = [k for k in chosen if k not in seen]
            if absent:
                meanings = {k["kind"]: k["meaning"] for k in self.model["kinds"]}
                year = (self.journal.get("count-readings_by_kind") or {}).get("year") or self.settings.get("year")
                found.append(WORDING["kinds_absent"].format(
                    kinds=_and(_kind_words(meanings.get(k), k) for k in absent),
                    those="that kind" if len(absent) == 1 else "those kinds", year=year or "the year chosen",
                    codes="that code" if len(absent) == 1 else "those codes"))
        elif name == "repeated_keys":
            for r in records:
                if (r.get("keys_repeated") or 0) > 0:
                    found.append(WORDING["repeated"].format(view=rolemap.view_title(str(r["role_view"]), False), count=f"{r['keys_repeated']:,}"))
        return found

    # The record of how the folder was made: every query offered, every result pasted, every answer.

    def offer(self, name, step, sql, **extra):
        """Records a query as the page offers it, numbered in the order of first offer, and returns its text with a
        first line that names the tool's version and the date, which is the text that the colleague copies."""
        entry = self.journal.get(name)
        if entry is None:
            entry = self.journal[name] = {"number": len(self.journal) + 1, "name": name}
        stamp = f"-- {WORDING['stamp_query'].format(version=self.version or 'unknown', date=_day(_today()))}"
        text = stamp + "\n" + sql
        entry.update({"step": step, "offered": _now(), "version": self.version,
                      **{k: v for k, v in extra.items() if v is not None}})
        if name.startswith("count-") and self.data is not None:
            # The schema as it stood when the count was written, so that a count written before a later change is
            # not taken as having checked the part.
            entry["schema"] = self._schema_mark()
        for key in [k for k, v in extra.items() if v is None]:
            entry.pop(key, None)
        self.queries[name] = text
        return text

    def pasted(self, name, text):
        entry = self.journal.get(name)
        if entry is None:
            return
        self.results[name] = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        entry.update({"pasted": _now(), "database": self.settings.get("database"), "version": self.version})
        if self.origin:
            entry["from"] = self.origin
        else:
            entry.pop("from", None)

    def run_invented(self, hospital, query, read, **given):
        """Runs a query that the page has offered on the invented hospital, and reads its result exactly as a paste of
        it would be read, recording in the journal that the result came from the invented hospital. read names the
        reading: tables, charted (key, year), count (name), values or probe (about). Returns the reading's receipt."""
        from .hospital import HospitalError
        if not self.invented or hospital is None:
            raise DescribeError(WORDING["invented_only"])
        sql = self.queries.get(query)
        if sql is None:
            raise DescribeError(WORDING["invented_not_offered"])
        try:
            text = hospital.grid(sql)
        except HospitalError as error:
            missing = getattr(error, "table", None)
            raise DescribeError(WORDING["invented_missing"].format(table=missing) if missing
                                else WORDING["invented_failed"]) from None
        readers = {"tables": lambda: self.read_tables(text),
                   "charted": lambda: self.read_charted(given["key"], text, given["year"]),
                   "count": lambda: self.read_count(given["name"], text),
                   "values": lambda: self.read_values(query, text),
                   "probe": lambda: self.read_probe(given["about"], text)}
        if read not in readers:
            raise DescribeError(WORDING["invented_failed"])
        self.origin = INVENTED_HOSPITAL
        self.settings["database"] = "invented"
        try:
            return readers[read]()
        finally:
            self.origin = None

    def _file(self, name, folder, suffix):
        return f"{folder}/{self.journal[name]['number']:02d}-{name}.{suffix}"

    def _log_confirmation(self, about, answer, replacement, note, date):
        self.confirmations.append({"attribute": about, "answer": answer, "replacement": replacement, "date": date,
                                   "note": " ".join((note or "").split())[:400], "version": self.version,
                                   "correction": "", "test": "", "reason": ""})

    # Where each fact came from, and how far each part has been checked.

    def _schema_mark(self):
        """A fingerprint of what the views are written from: the bindings, the kinds' codes and the codes chosen."""
        roles = {name: {"rows": role["rows"].get("binding"), "columns": {c: e.get("binding") for c, e in role["columns"].items()}}
                 for name, role in (self.data or {}).get("roles", {}).items()}
        kinds = {k: v.get("codes") for k, v in (self.data or {}).get("kinds", {}).items()}
        chosen = {k: v.get("chosen") for k, v in self.codes.items()}
        return hashlib.sha256(json.dumps([roles, kinds, chosen], sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]

    def provenance(self, name):
        """Where the result of a query came from: the database's own records of its tables (metadata), a sample of
        the anaesthetics of one year in #cohort, or the whole of the tables it reads (complete data)."""
        if name in ("data-dictionary", "tables-and-columns"):
            return METADATA
        return SAMPLE if "#cohort" in (self.queries.get(name) or "") else COMPLETE

    @staticmethod
    def _binding_provenance(item):
        """Where a binding came from: a person's answer, or the page's own proposal."""
        return PERSON if item.get("status") == "person" or (item.get("confirmation") or {}).get("answer") else \
            COMPLETE if item.get("status") == "count" else INFERENCE

    def _checked_parts(self):
        """{part: date} for each part whose every count has been run on a real database, on the schema as it now
        stands, and judged to look right."""
        mark = self._schema_mark() if self.data is not None else None
        found = {}
        for view in {v for parts in COUNT_PARTS.values() for v in parts}:
            dates = []
            for name, parts in COUNT_PARTS.items():
                if view not in parts:
                    continue
                held, entry = self.counts.get(name) or {}, self.journal.get(f"count-{name}") or {}
                if not (held.get("rows") and held.get("looks_right") == "yes" and entry.get("pasted")
                        and entry.get("from") != INVENTED_HOSPITAL and entry.get("database") in REAL_DATABASES
                        and entry.get("schema") == mark):
                    dates = None
                    break
                dates.append(str(held.get("judged") or held.get("date") or "")[:10])
            if dates:
                found[view] = max(dates)
        return found

    def readiness(self, date=None):
        """How far each part of the hospital schema has been checked, in the three states: runs, checked against the
        database, and clinically validated, which the page never records. Each part gives the date on which it reached
        each state, or None. The whole schema has reached the state that every part of the version 1 contract has."""
        date = date or _today()
        if self.data is None:
            return None
        report = self._baseline_check()
        contract = [v for v, status in rolemap.statuses().items() if status == "contract"]
        failing = set()
        for problem in report["problems"]:
            about = (report.get("about") or {}).get(problem)
            # A problem that names no part, such as the test audit's answer, falls on the parts that every audit reads.
            failing |= {about.split(" ")[0].split(".")[0]} if about else set(contract)
        checked = self._checked_parts()
        parts = {}
        for view in [v for v in rolemap.all_views() if v in self.data["roles"]]:
            runs = date if view not in failing else None
            reached_checked = checked.get(view) if runs else None
            parts[view] = {"status": rolemap.statuses()[view], "reached": CHECKED if reached_checked else RUNS if runs else None,
                           RUNS: runs, CHECKED: reached_checked, VALIDATED: None}
        order = {None: 0, RUNS: 1, CHECKED: 2}
        held = [parts[v]["reached"] for v in contract if v in parts]
        reached = min(held, key=lambda state: order[state]) if len(held) == len(contract) else None
        self._readiness = {"reached": reached, "states": {RUNS: WORDING["state_runs"], CHECKED: WORDING["state_checked"],
                                                         VALIDATED: WORDING["state_validated"]},
                           "parts": parts, "date": date}
        return self._readiness

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

    def _described(self):
        """The description of map.json, which follows the record: the date of the proposal, and how many of its
        columns and tables a person has answered for."""
        date = propose._proposed_on(self.data)
        t = self.tally()
        total = t["total"] + t["tables"]
        left = t["remaining"] + t["tables_remaining"]
        answered = total - left
        if not answered:
            text = WORDING["described_none"].format(date=date)
        elif left:
            text = WORDING["described_some"].format(date=date, answered=f"{answered:,}", total=f"{total:,}",
                                                    left=f"{left:,} {'is' if left == 1 else 'are'}")
        else:
            text = WORDING["described_all"].format(date=date, total=f"{total:,}")
        if t["untranslated"]:
            n = t["untranslated"]
            text += WORDING["described_codes"].format(count=f"{n:,} {'column' if n == 1 else 'columns'}", hold="holds" if n == 1 else "hold")
        return text

    def _map_files(self, stamp="", provenance=False):
        self.data["description"] = self._described()
        data = self.data
        if provenance:
            # Each binding and each kind says where it came from: a person's answer, or the page's own proposal.
            data = copy.deepcopy(self.data)
            for role in data["roles"].values():
                for item in [role["rows"], *role["columns"].values()]:
                    item["provenance"] = self._binding_provenance(item)
            for item in data["kinds"].values():
                item["provenance"] = self._binding_provenance(item)
        files = {rolemap.MAP_FILE: json.dumps(data, indent=2, ensure_ascii=False) + "\n"}
        for name in self.data["roles"]:
            files[f"{name}.sql"] = (stamp + "\n" if stamp else "") + self.view_sql(name)
        return files

    def _json(self, value, date):
        return (json.dumps({"tool": "Schemalyser", "version": self.version, "written": date, **value}, indent=2,
                           ensure_ascii=False) + "\n").encode("utf-8")

    def folder_files(self, date=None):
        """The saved hospital schema, as {path: bytes} inside the one file that the page saves. The dictionary is
        always inside it, so that opening the file needs nothing else. Every file names the tool's version and the date it was written,
        except map.json, whose format the map checker fixes and whose description gives the date of the proposal,
        the pasted results, whose first line does, and the dictionary's own files, which are kept exactly as given."""
        date = date or _today()
        files = {}
        stamp = f"-- {WORDING['stamp_file'].format(version=self.version or 'unknown', date=_day(date))}"
        if self.data is not None:
            for name, text in self._map_files(stamp, provenance=True).items():
                files[f"map/{name}"] = text.encode("utf-8")
        for name, text in self.queries.items():
            files[self._file(name, "queries", "sql")] = text.encode("utf-8")
        for name, text in self.results.items():
            entry = self.journal[name]
            stamp = "stamp_invented" if entry.get("from") == INVENTED_HOSPITAL else "stamp_result"
            first = "# " + WORDING[stamp].format(version=entry.get("version") or self.version or "unknown",
                                                 date=_day((entry.get("pasted") or date)[:10]))
            if self.provenance(name) == SAMPLE:
                first += WORDING["sample_stamp"].format(limit=f"{COHORT_LIMIT:,}")
            files[self._file(name, "results", "tsv")] = (first + "\n" + text.rstrip("\n") + "\n").encode("utf-8")
        for key, held in sorted(self.codes.items()):
            files[f"codes/{key}.json"] = self._json({"view": key.split(".")[0], "column": key.split(".")[1], **held,
                                                     "provenance": {"rows": SAMPLE, "chosen": PERSON}}, date)
        judgements = {name: {**{k: held.get(k) for k in ("date", "looks_right", "note", "judged", "database") if held.get(k) is not None},
                             "provenance": {"figures": self.provenance(f"count-{name}"),
                                            **({"judgement": PERSON} if held.get("looks_right") else {})}}
                      for name, held in sorted(self.counts.items())}
        if judgements:
            files["counts/judgements.json"] = self._json({"counts": judgements}, date)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(list(CONFIRMATION_FIELDS))
        writer.writerows([c.get(k) or (PERSON if k == "provenance" else "") for k in CONFIRMATION_FIELDS] for c in self.confirmations)
        files["confirmations.csv"] = out.getvalue().encode("utf-8")
        kept = bool(self.dictionary_files)
        if kept:
            meta = self.dictionary_files
            files[f"{DICTIONARY_FOLDER}/{meta['name']}"] = meta["data"]
            if meta.get("tables_data") is not None:
                files[f"{DICTIONARY_FOLDER}/{meta['tables_name']}"] = meta["tables_data"]
            vendor = {}
            if meta.get("vendor_data") is not None:
                files[f"{DICTIONARY_FOLDER}/{meta['vendor_name']}"] = meta["vendor_data"]
                vendor = {"vendor": meta["vendor_name"], "vendor_headings": meta.get("vendor_headings") or {}}
                if meta.get("vendor_tables_data") is not None:
                    files[f"{DICTIONARY_FOLDER}/{meta['vendor_tables_name']}"] = meta["vendor_tables_data"]
                    vendor["vendor_tables"] = meta["vendor_tables_name"]
            files[f"{DICTIONARY_FOLDER}/dictionary.json"] = self._json(
                {"file": meta["name"], "tables": meta.get("tables_name"), "headings": meta.get("headings") or {},
                 **({"source": "database"} if self.dictionary_source == "database" else {}), **vendor,
                 **({"invented": True} if self.invented else {}), "provenance": METADATA}, date)
        entries = sorted(self.journal.values(), key=lambda e: e["number"])
        journal = []
        if self.dictionary_entry:
            journal.append({**self.dictionary_entry, "provenance": METADATA})
        journal += [{**entry, "provenance": PERSON} for entry in self.corrections]
        for entry in entries:
            item = {k: v for k, v in entry.items() if k != "number"}
            item["query"] = self._file(entry["name"], "queries", "sql")
            item["result"] = self._file(entry["name"], "results", "tsv") if entry["name"] in self.results else None
            item["provenance"] = self.provenance(entry["name"])
            journal.append({"number": entry["number"], **item})
        files["journal.json"] = self._json({"entries": journal}, date)
        settings = dict(self.settings)
        unfinished = self.unfinished()
        # Every column answered is not the same as complete: how far the schema has been checked is its readiness.
        settings["answered"] = self.data is not None and not unfinished
        if unfinished:
            settings["draft"] = WORDING["draft"].format(parts=unfinished)
        readiness = self.readiness(date)
        if readiness is not None:
            settings["readiness"] = readiness
        settings.update({"dictionary": {"kept": kept, **({k: v for k, v in (self.dictionary_receipt() or {}).items()
                                                          if k in ("tables", "columns", "file", "tablesFile")})}})
        if self.invented:
            settings["invented"] = True
            settings["dictionary"]["invented"] = True
            settings["database"] = "invented"
        files["settings.json"] = self._json(settings, date)
        training = [self._file(e["name"], "queries", "sql") for e in entries if e.get("database") == "training"]
        files["README.md"] = readme(sorted(files), self.version, date, kept, training, unfinished,
                                    self.untranslated() if self.data is not None else [], self.invented,
                                    readiness).encode("utf-8")
        return files

    def folder_zip(self, date=None):
        """The saved hospital schema as the bytes of the one file that the page saves."""
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(self.folder_files(date).items()):
                archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data)
        return out.getvalue()

    def restore(self, files):
        """Reads a hospital folder that this screen wrote, as {path: bytes} relative to the folder, and returns what
        it held. A folder without map/map.json starts a new hospital folder. A folder that holds no copy of the
        dictionary is restored only once a dictionary is loaded; until then nothing of it is taken, and the answer says
        that the dictionary is needed."""
        files = dict(files)
        found = {"map": False, "tables": False, "codes": 0, "counts": 0, "dictionary": False, "confirmations": 0,
                 "queries": 0, "problem": "", "needs_dictionary": False}
        info = _json_of(files.get(f"{DICTIONARY_FOLDER}/dictionary.json"))
        has_dictionary = bool(info.get("file") and f"{DICTIONARY_FOLDER}/{info['file']}" in files)
        if files and not has_dictionary and self.dictionary is None:
            found["needs_dictionary"] = True
            return found
        self.folder = files
        # What the sitting held before is replaced by what the file holds, so that nothing is counted twice.
        self.confirmations, self.corrections, self.codes, self.counts = [], [], {}, {}
        self.journal, self.queries, self.results, self.values, self.probes = {}, {}, {}, {}, {}
        self._checked, self._baseline = {}, None
        held = _json_of(files.get("settings.json"))
        for key in ("made", "updated", "database", "year", "time_zone", "daylight_saving"):
            if key in held:
                self.settings[key] = held[key]
        if has_dictionary:
            tables = files.get(f"{DICTIONARY_FOLDER}/{info['tables']}") if info.get("tables") else None
            try:
                if info.get("source") == "database":
                    self.load_from_database(files[f"{DICTIONARY_FOLDER}/{info['file']}"], info["file"], record=False)
                    vendor = files.get(f"{DICTIONARY_FOLDER}/{info.get('vendor')}") if info.get("vendor") else None
                    if vendor is not None:
                        vendor_tables = files.get(f"{DICTIONARY_FOLDER}/{info['vendor_tables']}") if info.get("vendor_tables") else None
                        self.add_descriptions(vendor, vendor_tables, info.get("vendor_headings") or {}, info["vendor"],
                                              info.get("vendor_tables") or "vendor-tables.csv", record=False)
                else:
                    self.load_dictionary(files[f"{DICTIONARY_FOLDER}/{info['file']}"], tables, info.get("headings") or {},
                                         info["file"], info.get("tables") or "tables.csv",
                                         invented=bool(info.get("invented") or held.get("invented")), source="saved")
                found["dictionary"] = True
            except DescribeError:
                pass
        # A folder made with the invented dictionary stays marked as such, whatever dictionary is loaded beside it.
        if held.get("invented"):
            self.invented = True
            if self.dictionary_entry:
                self.dictionary_entry["invented"] = True
        if "map/map.json" in files:
            folder = self._work_folder("restore")
            (folder / rolemap.MAP_FILE).write_bytes(bytes(files["map/map.json"]))
            try:
                self.data = rolemap.read_map_json(folder)
                # Where each binding came from is written at each save, and is not part of the schema itself.
                for item in [i for role in self.data["roles"].values() for i in [role["rows"], *role["columns"].values()]] \
                        + list(self.data["kinds"].values()):
                    item.pop("provenance", None)
                found["map"] = True
            except rolemap.MapError:
                found["problem"] = WORDING["folder_unreadable"]
        if "confirmations.csv" in files:
            for row in csv.DictReader(io.StringIO(_text(files["confirmations.csv"]))):
                if row.get("attribute") and row.get("answer"):
                    self.confirmations.append({k: row.get(k) or "" for k in CONFIRMATION_FIELDS})
            found["confirmations"] = len(self.confirmations)
        for path, data in sorted(files.items()):
            match = re.fullmatch(r"codes/(role_\w+\.\w+)\.json", path)
            if match:
                held = _json_of(data)
                if held:
                    self.codes[match.group(1)] = {k: v for k, v in held.items() if k not in ("view", "column", "tool", "version", "written", "provenance")}
                    found["codes"] += 1
        judgements = _json_of(files.get("counts/judgements.json")).get("counts") or {}
        for name, held in judgements.items():
            if name in COUNT_COLUMNS:
                self.counts[name] = {k: v for k, v in held.items() if k != "provenance"}
        for entry in (_json_of(files.get("journal.json")).get("entries") or []):
            if entry.get("name") == "correction":
                self.corrections.append({k: v for k, v in entry.items() if k != "provenance"})
                continue
            if entry.get("name") == "dictionary" and self.dictionary_entry is not None:
                self.dictionary_entry.update({k: v for k, v in entry.items() if k.startswith("vendor_")})
            if entry.get("name") == "dictionary" or "number" not in entry:
                continue
            name = entry["name"]
            self.journal[name] = {k: v for k, v in entry.items() if k not in ("query", "result", "provenance")}
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
                    self.read_tables(text, record=False)
                    found["tables"] = True
                elif name.startswith("charted-") and entry.get("key"):
                    self.read_charted(entry["key"], text, entry.get("year") or self.settings.get("year") or 2000, record=False)
                elif name.startswith("probe-") and entry.get("about"):
                    self.read_probe(entry["about"], text, record=False)
                elif name.startswith("values-"):
                    self.values[name] = [{"value": r[0], "rows": _number(r[1])} for r in read_grid(text, ("value", "rows"))[1]]
                elif name.startswith("count-") and name[6:] in COUNT_COLUMNS:
                    self.read_count(name[6:], text, (entry.get("pasted") or "")[:10] or None, record=False)
                    self.counts[name[6:]].update({k: v for k, v in (judgements.get(name[6:]) or {}).items() if k != "provenance"})
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
            fresh.catalogue = self.catalogue
            for row in self.confirmations:
                try:
                    if row.get("correction"):
                        fresh.replay_correction(json.loads(row["correction"]), row.get("date") or None, row.get("test") or "",
                                                row.get("reason") or "")
                    else:
                        fresh.confirm(row["attribute"], row["answer"], row.get("replacement") or "", row.get("note") or "", row.get("date") or None)
                except (DescribeError, KeyError, ValueError) as error:
                    problems.append(WORDING["check_confirmation"].format(about=rolemap.plain_about(row["attribute"]), problem=str(error)))
            for key, held in self.codes.items():
                if held.get("chosen"):
                    fresh.codes.setdefault(key, {}).update({k: v for k, v in held.items() if k != "chosen"})
                    try:
                        fresh.choose_codes(key, held["chosen"], held.get("date"))
                    except DescribeError:
                        problems.append(WORDING["check_codes"].format(key=rolemap.plain_about(key)))
            self.proposer = fresh.proposer
            rebuilt = {"rebuilt": True, "differences": problems + _differences(self.data, fresh.data)}
            rebuilt["same"] = not rebuilt["differences"]
        queries = []
        for entry in sorted(self.journal.values(), key=lambda e: e["number"]):
            name = entry["name"]
            previous = self.results.get(name)
            if name == "data-dictionary" and self.catalogue_source == "database":
                previous = self.catalogue_text
            columns, rows = [], []
            if previous:
                try:
                    columns, rows = read_grid(previous)
                except DescribeError:
                    pass
            queries.append({"name": name, "number": entry["number"], "step": entry.get("step"), "sql": self.queries.get(name, ""),
                            "file": self._file(name, "queries", "sql"), "pasted": entry.get("pasted"),
                            "database": entry.get("database"), "columns": columns, "rows": rows[:200], "more": max(0, len(rows) - 200)})
        failing = [{"about": row["attribute"], "check": row.get("test") or "", "reason": row.get("reason") or "", "date": row.get("date") or ""}
                   for row in self.confirmations if (row.get("test") or "").startswith("failed")]
        return {**rebuilt, "queries": queries, "failing": failing}

    def compare(self, name, text):
        """The differences between a query's earlier result and a new one, as sentences: a table or column that has
        gone, a row that has gone or come, and a count that has changed by more than a tenth."""
        previous = self.results.get(name)
        if name == "data-dictionary" and self.catalogue_source == "database":
            try:
                rows = first_ask.database_rows(text)
            except first_ask.FirstAskError:
                raise DescribeError("unreadable") from None
            fresh = _tsv(list(first_ask.LAYOUT), [row[:len(first_ask.LAYOUT)] for row in rows])
            return {"differences": _tables_differences(self.catalogue_text, fresh), "previous": True}
        if previous is None:
            return {"differences": [], "previous": False}
        if name == "tables-and-columns":
            return {"differences": _tables_differences(previous, text), "previous": True}
        before_columns, before = read_grid(previous)
        after_columns, after = read_grid(text, before_columns)
        return {"differences": _grid_differences(before_columns, before, after), "previous": True}

    def set_settings(self, database=None, year=None, time_zone=None, daylight_saving=None):
        if database in ("production", "training", "unsure"):
            self.settings["database"] = database
        if year is not None and re.fullmatch(r"(19|20)\d\d", str(year)):
            self.settings["year"] = int(year)
        # The time zone that the database's clocks follow, as a name such as Australia/Sydney or UTC, which a person
        # gives once; the views give each time as the database holds it, so the saved schema says what that means.
        if time_zone is not None and re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}", str(time_zone).strip()) \
                and len(str(time_zone).strip()) <= 64:
            self.settings["time_zone"] = str(time_zone).strip()
        if daylight_saving is not None:
            self.settings["daylight_saving"] = bool(daylight_saving)

    # The model that the page shows.

    def view(self):
        roles = []
        for view in self.model["views"]:
            role = (self.data or {}).get("roles", {}).get(view["name"])
            entry = {"name": view["name"], "title": rolemap.view_title(view["name"]), "description": rolemap.plain(view["description"]),
                     "required": bool(view.get("required")), "drafted": role is not None, "items": []}
            if role is not None:
                rows = role["rows"]
                table = rows["binding"]["table"] if rows.get("binding") else rows["from"]
                entry["items"].append(self._item(f"{view['name']} rows", "rows", rows, rolemap.plain(view["description"]), table, None))
                links = {link["column"]: link["to"] for link in view.get("links", [])}
                for column in view["columns"]:
                    item = role["columns"][column["name"]]
                    binding = item.get("binding")
                    shown = self._item(f"{view['name']}.{column['name']}", column["name"], item,
                                       rolemap.plain(column["meaning"], view["name"]), binding["table"] if binding else None,
                                       binding["column"] if binding else None, column["type"])
                    shown["link"] = links.get(column["name"])
                    shown["coding"] = self.coding(view["name"], column, item)
                    shown["title"] = rolemap.column_title(view["name"], column["name"])
                    entry["items"].append(shown)
            roles.append(entry)
        tally = self.tally()
        return {"dictionary": self.dictionary_receipt(), "proposed": self.data is not None, "roles": roles,
                "tally": tally, "questions": self.questions(), "catalogue": self.catalogue is not None,
                "catalogue_source": self.catalogue_source if self.catalogue is not None else None,
                "vocabularies": self.vocabularies(), "values": self.values, "counts": {k: {kk: v.get(kk) for kk in ("columns", "rows", "looks_right", "note", "date", "database")}
                                                                | {"findings": self.findings(k), "finding_about": self.finding_about(k),
                                                                   "finding_codes": self.finding_codes(k)} for k, v in self.counts.items()},
                "settings": {k: self.settings.get(k) for k in ("made", "updated", "database", "year", "time_zone", "daylight_saving")},
                "readiness": self._readiness,
                "scoreboard": rolemap.scoreboard(self.data)["lines"] if self.data is not None else [],
                "provenance": {name: self.provenance(name) for name in self.journal},
                "anaesthetic_table": ((self.data or {}).get("roles", {}).get("role_anaesthetic") or {}).get("rows", {}).get("binding", {}).get("table"),
                "bases": {name: role["rows"]["binding"]["table"] for name, role in (self.data or {}).get("roles", {}).items()
                          if role["rows"].get("binding")},
                "restored": self.restored,
                "untranslated": self.untranslated() if self.data is not None else [],
                "unfinished": self.unfinished(),
                "counts_offered": [n[6:] for n in self.journal if n.startswith("count-")]}

    def _item(self, about, attribute, item, meaning, table, column, role_type=None):
        binding = item.get("binding")
        definition = None
        if self.dictionary is not None and table:
            definition = self.dictionary.description(table, column) if column else self.dictionary.description(table)
        candidates = []
        offers = self.offers_identifying(about)
        withheld = 0
        for candidate in item.get("candidates") or []:
            head = candidate["from"].split(",")[0]
            if not offers and "." in head and self._identifying(*head.split(".", 1)):
                withheld += 1
                continue
            words = None
            if self.dictionary is not None and "." in head:
                words = self.dictionary.description(*head.split(".", 1)) or None
            elif self.dictionary is not None:
                words = self.dictionary.description(head) or None
            candidates.append({"from": candidate["from"], "replacement": _parse_from(candidate["from"]) if "." in head else head,
                               "definition": words or candidate.get("words")})
        confirmation = item.get("confirmation") or {}
        correction = confirmation.get("correction") if isinstance(confirmation.get("correction"), dict) else None
        return {"correction": {"form": correction["form"], "says": rolemap.plain(item["says"] if attribute != "rows" or correction["form"] == "rows"
                                                                         else _filter_says(item, about)),
                               "check": confirmation.get("check") or "", "reason": confirmation.get("reason") or "",
                               "probe": self.probe_kind(about), "probed": self.probes.get(about),
                               "findings": self.probe_findings(about)} if correction else None,
                "binding_form": _binding_form(binding), "coding": None,
                "about": about, "attribute": attribute, "meaning": meaning, "type": role_type, "from": item["from"],
                "table": table, "column": column, "bound": bool(binding) if attribute != "rows" else bool(table),
                "definition": definition, "says": re.sub(r"\b(match|matches) the role\.$", r"\1 this column.", rolemap.plain(item["says"])), "title": "", "confidence": item.get("confidence") or "",
                "basis": _basis(item.get("says") or ""),
                "candidates": candidates, "offers_identifying": offers, "withheld": withheld, "status": item["status"], "question": rolemap.plain(item.get("question") or ""),
                "answer": confirmation.get("answer"), "date": confirmation.get("date"),
                "replacement": confirmation.get("replacement"), "note": confirmation.get("note"),
                "presence": self.presence(binding if attribute != "rows" else ({"table": table} if table else None))}


def _kind_words(meaning, kind):
    """A kind of reading named in a sentence, from its meaning: "A mean arterial pressure from a non-invasive cuff, in
    mmHg." becomes "the mean arterial pressure from a non-invasive cuff"."""
    if not meaning:
        return kind
    words = re.sub(r",\s*in [^,]+\.?$", "", meaning.strip()).rstrip(". ")
    return re.sub(r"^(A|An)\s+", "the ", words)


def _basis(says):
    """What a proposal rests on, from the sentence that gives its reason, so that the page's confidence agrees with it:
    "key" for the column that identifies the part's rows, "link" for a link by the same name, "name" where only the
    names match, and "words" where the dictionary's words match."""
    if "is the column that identifies a row of the table that holds this part" in says:
        return "key"
    if "has the same name as" in says or "is the column that identifies a row of" in says:
        return "link"
    if "its name matches" in says or "its name and columns match" in says:
        return "name"
    return "words"


def _read_dictionary(data, tables, own):
    """A dictionary read from the bytes of its file and of its tables' file, with an error a person can act on."""
    try:
        return datadict.load(bytes(data), bytes(tables) if tables is not None else None, own)
    except datadict.DictionaryError as error:
        message = str(error)
        lacking = re.match(r"Schemalyser could not find a heading for the (.+?) in", message)
        if lacking:
            message = WORDING["headings"].format(fields=lacking.group(1))
        elif message.startswith("The dictionary has no heading"):
            message = message + " Please check the heading's spelling, then load the file again."
        raise DescribeError(message) from None


def _binding_form(binding):
    """The form of a binding in words of one item, for the page: column, derived, window, joined or filter."""
    if not binding:
        return None
    for form in ("window", "joined", "derive", "filter"):
        if binding.get(form):
            return "derived" if form == "derive" else form
    if any(len(step) > 4 for step in binding.get("path") or []):
        return "pair"
    return "column"


def _filter_says(item, about=""):
    filters = (item.get("binding") or {}).get("filter") or []
    if not filters:
        return item["says"]
    return " ".join(corrections._fit(corrections.WORDING["say_filter"].format(
        view=rolemap.view_title(about.split(" ")[0], False), source=f"{f['table']}.{f['column']}", values=corrections._shown_values(f["values"]), how=""))
        for f in filters)


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
    return rest if first.startswith(("# Pasted into Schemalyser", "# Run on the invented hospital into Schemalyser")) else text


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
            found.append(WORDING["only_folder" if two is None else "only_rebuilt"].format(about=rolemap.view_title(name, False)))
            continue
        pairs = [(f"{name} rows", one["rows"], two["rows"])]
        pairs += [(f"{name}.{c}", one["columns"].get(c), two["columns"].get(c)) for c in dict.fromkeys([*one["columns"], *two["columns"]])]
        for about, x, y in pairs:
            same = x is not None and y is not None and x.get("binding") == y.get("binding") and x["status"] == y["status"] \
                and (x.get("confirmation") or {}).get("answer") == (y.get("confirmation") or {}).get("answer")
            if not same:
                found.append(WORDING["differs"].format(about=rolemap.plain_about(about, True), before=_shown(x), after=_shown(y)))
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
    "invented": "This file was made with the invented dictionary, for practice, and describes no hospital.",
    "title": "# The saved hospital schema",
    "stamp": "Schemalyser {version} saved this file on {date}.",
    "intro": "This file holds the hospital schema: where the hospital's database keeps each part of the anaesthetic "
             "record. A clinician and a colleague who runs SQL against that database made it together with "
             "Schemalyser's page Describe the record. The page proposed the schema from the data dictionary, the "
             "colleague confirmed or corrected each column, and the queries that the colleague ran settled the "
             "hospital's codes and the counts.",
    "storage": "This file contains the hospital's data dictionary and the hospital schema, which name the hospital's own "
               "tables and codes. It must stay on the hospital's own storage.",
    "files": "## What each part of the file holds",
    "rerun": "## Queries to run again on production",
    "draft": "## This hospital schema is a draft",
    "draft_text": "Some of the hospital schema is not yet answered ({parts}). To finish it, open the page Describe the "
                  "record, open this file at step 3 and carry on from step 6.",
    "readiness": "## How far the hospital schema has been checked",
    "readiness_text": "Schemalyser records how far each part of the hospital schema has been checked, in three states. A "
                      "part runs once it compiles and runs on made-up rows. It is checked against the database once the "
                      "counts that read it have been run on the hospital's database and judged to look right. It is "
                      "clinically validated only once a sample of anaesthetics has been reconciled against the clinical "
                      "record, which the page cannot do, so this file never records that state.",
    "readiness_reached": "The parts that every audit reads have reached the state {state}, on the dates below.",
    "readiness_none": "The parts that every audit reads have not yet reached the first state, because the test on made-up "
                      "rows finds a problem in at least one of them.",
    "readiness_part": "- {title} ({status}): {states}.",
    "readiness_part_none": "- {title} ({status}): no state reached yet.",
    "provenance": "## Where each fact came from",
    "provenance_text": "Every fact in this file says where it came from. In journal.json, map/map.json, codes/, "
                       "counts/judgements.json and confirmations.csv, the field headed provenance gives one of five "
                       "sources: complete data, for a count over the whole of the tables it reads; a sample, for a count "
                       "over the anaesthetics of one year in #cohort, whose figures show what is charted but not how "
                       "much; metadata, for the data dictionary and the database's own records of its tables; a person, "
                       "for an answer, a choice of codes or a judgement; and an inference, for the page's own proposal "
                       "that no person has yet answered. The first line of each result taken from a sample says so.",
    "draft_codes": "These columns are answered, but the codes that they hold are not yet translated, so their views give "
                   "nothing useful until a person translates them:",
    "remake": "## How to check or remake the hospital schema",
    "remake_text": [
        "Open the page Describe the record, take it offline and open this file at step 3. The page then reads the data "
        "dictionary and everything below from the file, and you can carry on from where you left off.",
        "To check that the hospital schema is still right, for example after a change to the database or a new release "
        "of the vendor's system, choose Check against the database under Check a saved schema against the database. "
        "Schemalyser proposes the schema again from the dictionary, applies the answers in confirmations.csv in their "
        "order, and says whether the result is the same as map/map.json. It then lists every query in queries/ with its "
        "earlier result from results/. The colleague runs each query again and pastes the new result, and the page "
        "lists what has changed: a table or column that has gone, a row that has gone or come, or a count that has "
        "changed by more than a tenth.",
        "Without the page, the hospital schema can still be checked by hand. Each file in queries/ is the exact text "
        "that the colleague ran, and the file of the same number in results/ is what came back. journal.json says "
        "which step offered each query, which database it was run on and when the result was pasted.",
    ],
    "licence": "The data dictionary is licensed. This file holds a copy of it in dictionary/, so that the page can open "
               "the hospital schema without anything else, and map/map.json quotes it as the evidence for each column.",
    "no_licence": "The data dictionary is licensed. This file holds no copy of it, so the dictionary must be loaded again "
                  "at step 2 before the file is opened.",
}
README_FILES = [
    ("settings.json", "The tool's version, the dates on which the hospital schema was made and last changed, the database that "
                      "the queries were run on (production, training, or invented where the invented hospital ran them), "
                      "the year of the lists, the time zone that the database's clocks follow and whether they change "
                      "with daylight saving, whether every column has an answer, and the state of readiness that each "
                      "part has reached, with its date."),
    ("journal.json", "One entry for each step that took something in: the step's heading, the query file, the result "
                     "file, the database, when the result was pasted or run, whether it came from the invented hospital "
                     "rather than a paste, and the tool's version. For the dictionary, it "
                     "gives the file's name, its size, its numbers of tables and columns and a fingerprint of its "
                     "contents (a SHA-256 hash), and never its contents. Each correction kept has an entry of its own, "
                     "with the sentence that it means, the outcome of its test on made-up rows and any reason for keeping it."),
    ("confirmations.csv", "Every answer that the colleague gave, in order, one row for each answer: the part and column, "
                          "the answer (yes, no or not sure), the replacement where the answer was no or where a Yes carried "
                          "a translation of the column's codes, the date and any note. For a correction "
                          "made in one of the page's forms, it also gives the correction as data, the outcome of the test on "
                          "made-up rows that Schemalyser ran before it was kept (the column headed test), and, where it "
                          "was kept although the test failed, the reason that was given."),
    ("map/map.json", "Every column of every part of the record: the table and column that hold it, the links "
                     "that reach them, the dictionary's description that supports it, the answer and its date. Its "
                     "description gives the date of the proposal and how many columns a person has answered for."),
    ("map/role_*.sql", "One SQL file for each part of the record, written from its columns, its links and the chosen "
                       "codes. An audit reads these parts of the record and nothing else."),
    ("queries/", "The exact text of every query that the page offered, numbered in the order offered."),
    ("results/", "Each result, exactly as the colleague pasted it or as the invented hospital gave it, with the same number "
                 "and name as its query. journal.json records which of the two each came from, and the first line of each "
                 "file says so, with the tool's version and the date."),
    ("codes/", "For each column that holds the hospital's own codes, the list of what is charted and the codes "
               "chosen for each kind."),
    ("counts/judgements.json", "For each count, whether it looked right to the two of you, any note, and the database "
                               "whose figures were judged: production, training, or invented for the invented hospital."),
    ("dictionary/", "The dictionary's own files. Where the data dictionary was made from the database, it is the "
                    "result of the data dictionary query as a CSV, and any file of the vendor's descriptions is kept "
                    "exactly as it was loaded. Otherwise the files are exactly as they were loaded."),
]


def readme(paths, version, date, kept, training=(), unfinished="", untranslated=(), invented=False, readiness=None):
    """README.md of the hospital folder, which says what each file is, how it was made and how to remake it. training
    lists the queries whose results came from a training database, which are to be run again on production."""
    lines = [README["invented"], ""] if invented else []
    lines += [README["title"], "", README["stamp"].format(version=version or "unknown", date=_day(date)), "", README["intro"], "",
              README["storage"], ""]
    if unfinished:
        lines += [README["draft"], "", README["draft_text"].format(parts=unfinished), ""]
        if untranslated:
            lines += [README["draft_codes"], ""] + [f"- {u['title']} (`{u['from']}`)" for u in untranslated] + [""]
    if readiness is not None:
        lines += [README["readiness"], "", README["readiness_text"], ""]
        lines += [README["readiness_reached"].format(state=readiness["reached"]) if readiness["reached"] else README["readiness_none"], ""]
        for view, part in readiness["parts"].items():
            status = "read by every audit" if part["status"] == "contract" else "not yet read by any audit"
            states = [f"{state} on {_day(part[state])}" for state in READINESS if part.get(state)]
            lines.append(README["readiness_part"].format(title=rolemap.view_title(view), status=status, states=_and(states))
                         if states else README["readiness_part_none"].format(title=rolemap.view_title(view), status=status))
        lines += [""]
    lines += [README["provenance"], "", README["provenance_text"], ""]
    lines += [README["files"], ""]
    for name, what in README_FILES:
        present = any(p == name or (name.endswith("/") and p.startswith(name))
                      or (name == "map/role_*.sql" and p.startswith("map/role_")) for p in paths)
        if present:
            lines.append(f"- `{name}`: {what}")
    if training:
        lines += ["", README["rerun"], "", WORDING["training"], ""] + [f"- `{path}`" for path in training]
    lines += ["", README["licence"] if kept else README["no_licence"], "", README["remake"], ""]
    for paragraph in README["remake_text"]:
        lines += [paragraph, ""]
    return "\n".join(lines)
