"""The names of the concepts that a conversion uses, so that a person is never asked about a bare number.

A conversion folder may hold concept_names.csv, with the columns concept_id, concept_name and vocabulary_id:
the vocabulary's own name for each concept that the steps write as a constant, that the mapping rows lead to,
or that a target query compares with. The names are public. This module writes the file from an Athena
download and reads it, and named() gives the words that a question or a page shows:

    python -m schemalyser.concepts CONVERSION --vocabulary ATHENA [--targets FOLDER]

A concept whose name the file does not give is shown as "the concept N".
"""
import argparse
import csv
import io
import re
from pathlib import Path

import sqlglot
from sqlglot import exp

FILE = "concept_names.csv"
FIELDS = ("concept_id", "concept_name", "vocabulary_id")
NAME = re.compile(r"[^\r\n\t]{1,255}")
_cache = {}


def used(conversion, targets=()):
    """The concept numbers that a conversion's steps, mapping rows and the given target queries use."""
    from . import convert
    from .extract import decode
    import json
    folder = Path(conversion)
    found = set()
    for row in convert.mapping_dicts(folder):
        value = str(row.get("target_concept_id") or "").strip()
        if value.isdecimal() and value != "0":
            found.add(int(value))
    texts = []
    for step in json.loads((folder / "conversion.json").read_text()):
        path = folder / str(step.get("file", ""))
        if path.is_file():
            texts.append(decode(path.read_bytes()))
    texts += [decode(Path(t).read_bytes()) for t in targets]
    for text in texts:
        try:
            tree = sqlglot.parse_one(text, dialect="tsql")
        except sqlglot.errors.SqlglotError:
            continue
        for projection in (tree.expressions if isinstance(tree, exp.Select) else []):
            if projection.alias_or_name.lower().endswith("concept_id"):
                for literal in projection.find_all(exp.Literal):
                    if literal.is_int and int(literal.this) > 0:
                        found.add(int(literal.this))
        for node in tree.find_all(exp.EQ, exp.In):
            columns = [c for c in node.this.find_all(exp.Column)] if node.this is not None else []
            if any(c.name.lower().endswith("concept_id") for c in columns):
                for literal in node.find_all(exp.Literal):
                    if literal.is_int and int(literal.this) > 0:
                        found.add(int(literal.this))
    return found


def write(conversion, vocabulary, targets=()):
    """Writes concept_names.csv from CONCEPT.csv of an Athena download. Returns the number of concepts named."""
    wanted = used(conversion, targets)
    names = {}
    with open(Path(vocabulary) / "CONCEPT.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            identifier = row.get("concept_id", "")
            if identifier.isdecimal() and int(identifier) in wanted:
                names[int(identifier)] = (row["concept_name"].strip(), row["vocabulary_id"].strip())
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FIELDS)
    for identifier in sorted(names):
        writer.writerow([identifier, *names[identifier]])
    (Path(conversion) / FILE).write_text(out.getvalue(), encoding="utf-8")
    return len(names)


def names(conversion):
    """concept id -> name, from the conversion's concept_names.csv, with any line that is not plain left out."""
    path = Path(conversion) / FILE if conversion is not None else None
    if path is None or not path.is_file():
        return {}
    key = (str(path), path.stat().st_mtime)
    if key not in _cache:
        found = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                identifier, name = str(row.get("concept_id") or "").strip(), str(row.get("concept_name") or "").strip()
                if identifier.isdecimal() and NAME.fullmatch(name) and "$(" not in name:
                    found[int(identifier)] = name
        _cache[key] = found
    return _cache[key]


def named(conversion, concept):
    """The words for a concept: its name with its number in brackets, or "the concept N" where it has no name."""
    text = str(concept).strip()
    name = names(conversion).get(int(text)) if text.isdecimal() else None
    return f"{name} (concept {text})" if name else f"the concept {text}"


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.concepts")
    parser.add_argument("conversion", type=Path)
    parser.add_argument("--vocabulary", type=Path, required=True, help="an Athena download that holds CONCEPT.csv")
    parser.add_argument("--targets", type=Path, help="a folder of target queries, whose concepts are named as well")
    args = parser.parse_args()
    targets = sorted(args.targets.glob("*.sql")) if args.targets else []
    count = write(args.conversion, args.vocabulary, targets)
    print(f"concept_names.csv names {count} concepts.")


if __name__ == "__main__":
    main()
