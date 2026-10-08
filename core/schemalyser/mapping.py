"""Proposes mapping rows by exact name, from the labels of local codes to standard concepts.

A site's local codes, such as its medication identifiers, each have a label. This module looks
each label up in an Athena vocabulary download and proposes the standard concept of that name.
A label matches when it is the name or a synonym of a concept in the wanted domain, and that
concept either is a standard concept of the wanted class or maps to exactly one.

The proposals are a first pass for a person to review. A label with no match, or with more than
one, is listed and left unmapped.

    python -m schemalyser.mapping LABELS.csv --vocabulary ATHENA --source-vocabulary NAME --out OUT.csv

LABELS.csv has the columns source_code and label. OUT.csv is in the layout of SOURCE_TO_CONCEPT_MAP.
"""
import argparse
import csv
import io
from pathlib import Path

import duckdb

LAYOUT = ("source_code", "source_concept_id", "source_vocabulary_id", "source_code_description", "target_concept_id",
          "target_vocabulary_id", "valid_start_date", "valid_end_date", "invalid_reason")


def _table(path):
    return f"read_csv('{str(path).replace(chr(39), chr(39) * 2)}', delim='\\t', quote='', header=true, all_varchar=true)"


def propose(labels, vocabulary, domain="Drug", concept_class="Ingredient", coded_in=None):
    """Looks up each (code, label). Returns ({code: (concept_id, name, vocabulary_id)}, [(code, label, reason)]).

    With coded_in, the label is taken to be a code of that vocabulary, such as an ICD-10 code,
    and not a name. The concept class is then not asked for, since the domain is enough.
    """
    vocabulary = Path(vocabulary)
    labels = [(str(code), str(label).strip()) for code, label in labels if label is not None and str(label).strip()]
    con = duckdb.connect()
    con.execute("CREATE TABLE labels (code VARCHAR, label VARCHAR)")
    if labels:
        con.executemany("INSERT INTO labels VALUES (?, ?)", labels)
    # A working copy of a whole Athena download, as the testbed builds it, holds the same tables as text in vocabulary.duckdb.
    copy = vocabulary / "vocabulary.duckdb"
    if copy.is_file():
        con.execute(f"ATTACH '{str(copy).replace(chr(39), chr(39) * 2)}' AS athena (READ_ONLY)")

    def table(name):
        return f"athena.{name.lower()}" if copy.is_file() else _table(vocabulary / f"{name}.csv")

    def held(name):
        return copy.is_file() or (vocabulary / f"{name}.csv").exists()

    con.execute(f"CREATE VIEW concept AS SELECT * FROM {table('CONCEPT')}")
    named = "SELECT l.code, c.concept_id FROM labels l JOIN concept c ON lower(c.concept_name) = lower(l.label) WHERE c.domain_id = ?"
    parameters = [domain]
    if coded_in:
        named = "SELECT l.code, c.concept_id FROM labels l JOIN concept c ON upper(c.concept_code) = upper(l.label) WHERE c.vocabulary_id = ?"
        parameters = [coded_in]
    elif held("CONCEPT_SYNONYM"):
        con.execute(f"CREATE VIEW synonym AS SELECT * FROM {table('CONCEPT_SYNONYM')}")
        named += (" UNION SELECT l.code, c.concept_id FROM labels l JOIN synonym s ON lower(s.concept_synonym_name) = lower(l.label) "
                  "JOIN concept c ON c.concept_id = s.concept_id WHERE c.domain_id = ?")
        parameters.append(domain)
    con.execute(f"CREATE TABLE named AS {named}", parameters)
    wanted, wanted_value = ("t.standard_concept = 'S' AND t.domain_id = ?", domain) if coded_in else (
        "t.standard_concept = 'S' AND t.concept_class_id = ?", concept_class)
    targets = f"SELECT n.code, t.concept_id, t.concept_name, t.vocabulary_id FROM named n JOIN concept t ON t.concept_id = n.concept_id WHERE {wanted}"
    parameters = [wanted_value]
    if held("CONCEPT_RELATIONSHIP"):
        con.execute(f"CREATE VIEW relationship AS SELECT * FROM {table('CONCEPT_RELATIONSHIP')}")
        targets += (" UNION SELECT n.code, t.concept_id, t.concept_name, t.vocabulary_id FROM named n "
                    "JOIN relationship r ON r.concept_id_1 = n.concept_id AND r.relationship_id = 'Maps to' "
                    "AND (r.invalid_reason IS NULL OR r.invalid_reason = '') "
                    f"JOIN concept t ON t.concept_id = r.concept_id_2 WHERE {wanted}")
        parameters.append(wanted_value)
    found = {}
    for code, concept, name, source in con.execute(targets, parameters).fetchall():
        found.setdefault(code, set()).add((int(concept), name, source))
    con.close()
    matched, unmatched = {}, []
    for code, label in labels:
        targets_for_code = found.get(code, set())
        if len(targets_for_code) == 1:
            matched[code] = next(iter(targets_for_code))
        else:
            what = "code" if coded_in else "name"
            unmatched.append((code, label, f"more than one concept has this {what}" if targets_for_code else f"no concept has this {what}"))
    return matched, unmatched


def rows(labels, matched, source_vocabulary):
    """The matched labels as rows of SOURCE_TO_CONCEPT_MAP. A row that is valid has no invalid_reason, which is None and not empty text."""
    return [[code, 0, source_vocabulary, label[:255], matched[code][0], matched[code][2], "1970-01-01", "2099-12-31", None]
            for code, label in labels if code in matched]


def main():
    parser = argparse.ArgumentParser(prog="schemalyser.mapping")
    parser.add_argument("labels", type=Path)
    parser.add_argument("--vocabulary", type=Path, required=True)
    parser.add_argument("--source-vocabulary", required=True)
    parser.add_argument("--domain", default="Drug")
    parser.add_argument("--concept-class", default="Ingredient")
    parser.add_argument("--coded-in", help="the vocabulary whose codes the labels are, such as ICD10, where they are codes and not names")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with open(args.labels, newline="", encoding="utf-8-sig") as f:
        labels = [(row["source_code"], row["label"]) for row in csv.DictReader(f)]
    matched, unmatched = propose(labels, args.vocabulary, args.domain, args.concept_class, args.coded_in)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(LAYOUT)
    writer.writerows(rows(labels, matched, args.source_vocabulary))
    args.out.write_text(out.getvalue(), encoding="utf-8")
    print(f"{len(matched)} labels matched one concept, and {len(unmatched)} did not")
    for code, label, reason in unmatched:
        print(f"  {code}: {label}: {reason}")


if __name__ == "__main__":
    main()
