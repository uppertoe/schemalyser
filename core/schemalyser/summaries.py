"""The named summaries of confidential material: the only part of it that the owner may show the developer's model.

Section 4 of docs/contract.md names three: the proposal scoreboard, the summary at the top of a comparison report, and
a reference adapter's run counts, of what it read, could not read and could not parse. Each exporter here takes the
output of the module that holds the material (rolemap.scoreboard, compare.compare, compare.lineage and
transplant.transplant), keeps only the counter fields that its allowlist below names, and writes the summary alone to
a file of its own, as JSON with a format name and a version and as a short Markdown rendering of the same counts.

An exporter refuses to write, and writes nothing, if a field outside its allowlist is present among the counters or if
any value is something other than a number, a boolean, a word of a fixed vocabulary or a date. The refusal never
quotes the field or the value that it refused, since either may name the hospital's tables. The Markdown is built from
fixed sentences and the checked counters alone.

A summary is safe to show because the owner has read the exact text written and decided to show it, and not because
it holds only numbers. No summary enters the public workspace. docs/summaries.md lists the allowlists in words.

This module sits in the shared tier, though by its purpose it would be orchestration: the reference adapters that
write two of the three summaries may import only layers 1 and 3 and the shared tier, and the scoreboard's module is
layer 2, so the exporters import nothing from the package and take each output as data.
"""
import datetime as dt
import json
import math
import re
from functools import lru_cache
from pathlib import Path

VERSION = 1
CONTRACT = Path(__file__).resolve().parent / "rolemodel" / "contract.json"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NOTE = ("This summary holds counts alone and names no table, column, code or file. Read the exact text before you show "
        "it to the developer's model, and keep it out of the public workspace.")


class SummaryRefused(ValueError):
    """Counters that an exporter will not write, because a field or a value lies outside its allowlist."""


# The kinds of value that an allowlist may name.

COUNT, NUMBER, BOOLEAN, DATE_VALUE = "count", "number", "boolean", "date"


class Words:
    """A field whose value is one word of a fixed vocabulary."""

    def __init__(self, *words):
        self.words = frozenset(words)


class Each:
    """A field that holds, under keys drawn from a fixed vocabulary, one group of counters for each key present."""

    def __init__(self, keys, fields):
        self.keys, self.fields = keys, fields


@lru_cache(maxsize=1)
def _views():
    """The role views of the contract, which are public words and the only names that the scoreboard's parts take."""
    return tuple(view["name"] for view in json.loads(CONTRACT.read_text(encoding="utf-8"))["views"])


# The allowlists. Each names every field that its summary holds, and nothing else may be written.

FARED = {"proposals": COUNT, "as_proposed": COUNT, "listed": COUNT, "unlisted": COUNT, "not_sure": COUNT,
         "unanswered": COUNT}
SCOREBOARD = {
    "overall": FARED,
    "parts": Each(_views, FARED),
    "categories": Each(lambda: ("keys", "links", "timestamps", "codes", "descriptive"), FARED),
    "reference": FARED,
    "levels": Each(lambda: ("high", "medium", "low"), {"answered": COUNT, "corrected": COUNT}),
    "nothing": {"count": COUNT, "chosen": COUNT},
}
COMPARISON = {
    "counts": {"targets_compared": COUNT, "agreeing": COUNT, "differing": COUNT, "only_ours": COUNT,
               "only_theirs": COUNT, "routes_we_lack": COUNT, "uncertainty_items": COUNT},
}
PARSE_ERRORS = ("ParseError", "TokenError", "NoStatement")
REFERENCE_RUN = {
    "kind": Words("conversion", "dbt", "plain"),
    "counts": {"files_read": COUNT, "files_parsed": COUNT, "files_not_read": COUNT, "files_not_parsed": COUNT,
               "files_not_followed": COUNT, "files_not_recognised": COUNT, "statements_ignored": COUNT,
               "omop_tables": COUNT, "source_tables": COUNT, "source_columns": COUNT},
    "errors": Each(lambda: (*PARSE_ERRORS, "other"), {"files": COUNT}),
}
TRANSPLANT_RUN = {
    "date": DATE_VALUE,
    "counts": {"written": COUNT, "written_in_full": COUNT, "incomplete": COUNT, "not_written": COUNT, "skipped": COUNT,
               "placeholders": COUNT, "fields_mapped": COUNT, "fields_left_empty": COUNT,
               "fields_awaiting_a_decision": COUNT, "filters_held_back": COUNT, "not_in_dictionary": COUNT},
}

FORMATS = {"scoreboard": "schemalyser-scoreboard-summary", "comparison": "schemalyser-comparison-summary",
           "reference": "schemalyser-reference-run-summary", "transplant": "schemalyser-transplant-run-summary"}
ALLOWLISTS = {"scoreboard": SCOREBOARD, "comparison": COMPARISON, "reference": REFERENCE_RUN,
              "transplant": TRANSPLANT_RUN}
FILES = {"scoreboard": "scoreboard-summary", "comparison": "comparison-summary", "reference": "reference-run-summary",
         "transplant": "transplant-run-summary"}


def _checked(value, spec, where):
    """value, checked against spec. Raises SummaryRefused, naming only the allowlisted place, never the value."""
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            raise SummaryRefused(f"The summary will not be written, because {where} is not a group of counters.")
        if set(value) - set(spec):
            raise SummaryRefused(f"The summary will not be written, because {where} holds a field that its allowlist "
                                 "does not name.")
        missing = [name for name in spec if name not in value]
        if missing:
            raise SummaryRefused(f"The summary will not be written, because {where} lacks {missing[0]}.")
        return {name: _checked(value[name], spec[name], f"{where}.{name}") for name in spec}
    if isinstance(spec, Each):
        keys = spec.keys()
        if not isinstance(value, dict):
            raise SummaryRefused(f"The summary will not be written, because {where} is not a group of counters.")
        if any(key not in keys for key in value):
            raise SummaryRefused(f"The summary will not be written, because {where} holds a key outside its fixed "
                                 "vocabulary.")
        return {key: _checked(value[key], spec.fields, f"{where}.{key}") for key in keys if key in value}
    if isinstance(spec, Words):
        if not isinstance(value, str) or value not in spec.words:
            raise SummaryRefused(f"The summary will not be written, because {where} is not a word of its vocabulary.")
        return value
    if spec == BOOLEAN:
        if not isinstance(value, bool):
            raise SummaryRefused(f"The summary will not be written, because {where} is not true or false.")
        return value
    if spec == DATE_VALUE:
        try:
            if not isinstance(value, str) or not DATE.fullmatch(value):
                raise ValueError
            dt.date.fromisoformat(value)
        except ValueError:
            raise SummaryRefused(f"The summary will not be written, because {where} is not a date.") from None
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            (isinstance(value, float) and not math.isfinite(value)):
        raise SummaryRefused(f"The summary will not be written, because {where} is not a number.")
    if spec == COUNT and (not isinstance(value, int) or value < 0):
        raise SummaryRefused(f"The summary will not be written, because {where} is not a count.")
    return value


def checked(name, counters):
    """The counters of the named summary, checked against its allowlist and holding nothing else."""
    if not isinstance(counters, dict):
        raise SummaryRefused("The summary will not be written, because its counters are not a group of counters.")
    return _checked(counters, ALLOWLISTS[name], name)


def document(name, counters):
    """The summary as data, ready for its JSON file: the format, the version, a fixed note, and the checked counters."""
    return {"format": FORMATS[name], "version": VERSION, "note": NOTE, **checked(name, counters)}


def write(name, counters, folder, stem=None):
    """Writes the named summary alone to STEM.json and STEM.md in folder, where the stem is FILES[name] unless one is
    given, and returns the two paths. Nothing is written if the counters are refused."""
    data = document(name, counters)
    text = "\n".join(RENDER[name](data)) + "\n"
    folder, stem = Path(folder), stem or FILES[name]
    folder.mkdir(parents=True, exist_ok=True)
    paths = folder / f"{stem}.json", folder / f"{stem}.md"
    paths[0].write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    paths[1].write_text(text, encoding="utf-8")
    return paths


# The counters of each summary, taken from the output of the module that holds the material.

def scoreboard_counters(board):
    """The scoreboard's counters, from what rolemap.scoreboard returns. Its lines and the parts' titles are left out."""
    return {"overall": board["overall"],
            "parts": {part["view"]: {key: part[key] for key in FARED} for part in board["parts"]},
            "categories": board["categories"], "reference": board["reference"], "levels": board["levels"],
            "nothing": board["nothing"]}


def comparison_counters(report):
    """The comparison's counters, from the report that compare.compare returns or report.json holds."""
    return {"counts": report["summary"]["counts"]}


def reference_counters(lineage):
    """The reference run's counters, from the lineage that compare.lineage returns or lineage.json holds. A file not
    read is one whose Jinja the reader does not render; a file not parsed is one whose SQL does not parse; and a file
    not followed parsed, but a query in it could not be traced to its columns."""
    files = lineage["files"]
    unparsed = files["unparsed"]
    not_read = {u["file"] for u in unparsed if u["error"] == "Jinja"}
    not_parsed = max(files["read"] - files["parsed"] - len(not_read), 0)
    errors = {}
    for item in unparsed:
        if item["error"] != "Jinja":
            word = item["error"] if item["error"] in PARSE_ERRORS else "other"
            errors.setdefault(word, set()).add(item["file"])
    sources = lineage["sources"]
    return {"kind": lineage["kind"],
            "counts": {"files_read": files["read"], "files_parsed": files["parsed"], "files_not_read": len(not_read),
                       "files_not_parsed": not_parsed,
                       "files_not_followed": max(len({u["file"] for u in unparsed}) - len(not_read) - not_parsed, 0),
                       "files_not_recognised": len(files["not_recognised"]),
                       "statements_ignored": files["statements_ignored"], "omop_tables": len(lineage["targets"]),
                       "source_tables": len(sources), "source_columns": sum(len(c) for c in sources.values())},
            "errors": {word: {"files": len(held)} for word, held in errors.items()}}


def transplant_counters(report):
    """The transplant run's counters, from the report that transplant.transplant returns."""
    return {"date": report["date"], "counts": report["counts"]}


def write_scoreboard(board, folder):
    return write("scoreboard", scoreboard_counters(board), folder)


def write_comparison(report, folder):
    return write("comparison", comparison_counters(report), folder)


def write_reference(lineage, lineage_file):
    """Writes the reference run's summary beside the lineage file, as NAME-run-summary.json and .md for NAME.json, so
    that the runs on our conversion and on the reference keep a summary each."""
    lineage_file = Path(lineage_file)
    return write("reference", reference_counters(lineage), lineage_file.parent, f"{lineage_file.stem}-run-summary")


def write_transplant(report, folder):
    return write("transplant", transplant_counters(report), folder)


# The Markdown renderings, in fixed sentences.

def _n(count, one, many):
    return f"{count:,} {one if count == 1 else many}"


def _were(count):
    return f"{count:,} {'was' if count == 1 else 'were'}"


def _fared(counts):
    return (f"Of these, {_were(counts['as_proposed'])} confirmed as proposed, {_were(counts['listed'])} corrected to an "
            f"alternative that the page had listed, {_were(counts['unlisted'])} corrected to one that it had not "
            f"listed, {_were(counts['not_sure'])} marked not sure, and {counts['unanswered']:,} "
            f"{'has' if counts['unanswered'] == 1 else 'have'} no answer yet.")


CATEGORY_WORDS = {"keys": "the keys", "links": "the links", "timestamps": "the dates and times",
                  "codes": "the codes and flags", "descriptive": "the descriptive columns"}


def _scoreboard_lines(data):
    out = ["# How the proposals fared", "", NOTE, ""]
    overall = data["overall"]
    if not overall["proposals"]:
        return out + ["The page made no proposal from the dictionary."]
    out += [f"Across the hospital schema, the page made {_n(overall['proposals'], 'proposal', 'proposals')}. "
            f"{_fared(overall)}", ""]
    out += [f"- In `{view}`, the page made {_n(counts['proposals'], 'proposal', 'proposals')}. {_fared(counts)}"
            for view, counts in data["parts"].items()]
    out += [""]
    out += [f"- Among {CATEGORY_WORDS[name]}, the page made {_n(counts['proposals'], 'proposal', 'proposals')}. "
            f"{_fared(counts)}" if counts["proposals"] else f"- Among {CATEGORY_WORDS[name]}, the page made no proposal."
            for name, counts in data["categories"].items()]
    if data["reference"]["proposals"]:
        out += [f"- Among the proposals that rested on a reference conversion, the page made "
                f"{_n(data['reference']['proposals'], 'proposal', 'proposals')}. {_fared(data['reference'])}"]
    out += [""]
    for level, held in data["levels"].items():
        if held["answered"]:
            out.append(f"- Of the {_n(held['answered'], 'proposal', 'proposals')} made with {level} confidence that "
                       f"{'has' if held['answered'] == 1 else 'have'} been answered, {_were(held['corrected'])} "
                       f"corrected, which is {round(100 * held['corrected'] / held['answered'])} per cent.")
        else:
            out.append(f"- No proposal made with {level} confidence has yet been answered.")
    if data["nothing"]["count"]:
        out.append(f"- The page proposed nothing for {_n(data['nothing']['count'], 'column', 'columns')}, and a person "
                   f"has since chosen one for {data['nothing']['chosen']:,} of them.")
    return out


def comparison_lines(counts):
    """The comparison's counts in sentences that name nothing, which the compare command also prints."""
    c = counts
    return [
        f"The comparison covered {_n(c['targets_compared'], 'OMOP table', 'OMOP tables')}.",
        f"The two conversions agree on {c['agreeing']:,} of them and differ on {c['differing']:,}.",
        f"Only our conversion writes {_n(c['only_ours'], 'table', 'tables')}, and only the reference writes "
        f"{c['only_theirs']:,}.",
        f"The reference reads {_n(c['routes_we_lack'], 'route', 'routes')} that our conversion does not, counting each "
        f"source table and each pair of joined tables once for each OMOP table.",
        f"The comparison raised {_n(c['uncertainty_items'], 'uncertainty item', 'uncertainty items')} for the "
        f"anaesthesia tables.",
    ]


def _comparison_lines(data):
    return ["# Summary of a mapping comparison", "", NOTE, ""] + [f"- {line}" for line in comparison_lines(data["counts"])]


KIND_WORDS = {"conversion": "a conversion folder", "dbt": "a dbt-style project", "plain": "a plain folder of SQL"}
ERROR_WORDS = {"ParseError": "a parse error", "TokenError": "an error in reading its tokens",
               "NoStatement": "no statement that could be read", "other": "another error"}


def reference_lines(data):
    """The reference run's counts in sentences that name nothing, which the reference command also prints."""
    c = data["counts"]
    out = [f"Schemalyser read {KIND_WORDS[data['kind']]} of {_n(c['files_read'], 'file', 'files')}, and parsed "
           f"{c['files_parsed']:,} of them.",
           f"It could not read {_n(c['files_not_read'], 'file', 'files')}, could not parse {c['files_not_parsed']:,}, "
           f"and could not follow a query in {c['files_not_followed']:,}.",
           f"It did not recognise the OMOP table of {_n(c['files_not_recognised'], 'file', 'files')}, and it passed "
           f"over {_n(c['statements_ignored'], 'statement', 'statements')} that write nothing.",
           f"The lineage covers {_n(c['omop_tables'], 'OMOP table', 'OMOP tables')}, read from "
           f"{_n(c['source_tables'], 'source table', 'source tables')} and "
           f"{_n(c['source_columns'], 'source column', 'source columns')}."]
    out += [f"Of the files that could not be parsed or followed, {_n(held['files'], 'file', 'files')} met "
            f"{ERROR_WORDS[word]}." for word, held in data["errors"].items()]
    return out


def _reference_lines(data):
    return ["# Summary of a reference run", "", NOTE, ""] + [f"- {line}" for line in reference_lines(data)]


def _are(count):
    return "none is" if count == 0 else f"{count:,} is" if count == 1 else f"{count:,} are"


def transplant_lines(data):
    """The transplant run's counts in sentences that name nothing, which the transplant command also prints."""
    c = data["counts"]
    return [f"On {data['date']}, Schemalyser wrote {_n(c['written'], 'step', 'steps')}, of which {_are(c['incomplete'])} "
            f"incomplete. It could not write {_n(c['not_written'], 'step', 'steps')}, and it skipped "
            f"{_n(c['skipped'], 'table', 'tables')}.",
            f"The steps map {_n(c['fields_mapped'], 'field', 'fields')} from the lineage, leave "
            f"{c['fields_left_empty']:,} empty or at the concept 0 because the lineage cannot reproduce them, leave "
            f"{c['fields_awaiting_a_decision']:,} waiting on a decision, and hold "
            f"{_n(c['placeholders'], 'placeholder', 'placeholders')}.",
            f"The steps hold back {_n(c['filters_held_back'], 'filter', 'filters')} until their values are known, and the "
            f"dictionary lacks {_n(c['not_in_dictionary'], 'table or column', 'tables and columns')} that the "
            f"reference reads."]


def _transplant_lines(data):
    return ["# Summary of a transplant run", "", NOTE, ""] + [f"- {line}" for line in transplant_lines(data)]


RENDER = {"scoreboard": _scoreboard_lines, "comparison": _comparison_lines, "reference": _reference_lines,
          "transplant": _transplant_lines}
