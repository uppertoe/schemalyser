"""Collects the wording that awaits approval into one document for review.

Each module keeps its wording in one dictionary at its top. This script reads those dictionaries
and writes every string under a heading that says where the string appears, so that the wording
can be reviewed in one sitting. Wording that has already been approved is left out.

    cd core && uv run --with sqlglot==30.21.0 --with duckdb==1.5.1 python ../tools/wording_report.py OUT.md
"""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The first fourteen strings of the release script were approved on 4 October 2026.
APPROVED_RELEASE = 14


def strings(value, path=""):
    """Every string inside a value, with the path of keys that leads to it."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from strings(item, f"{path}[{index}]")


def section(title, where, pairs):
    lines = [f"## {title}", "", where, ""]
    for key, text in pairs:
        if text.strip():
            lines.append(f"- `{key}`: {text}")
    return lines + [""]


def load(name):
    try:
        return importlib.import_module(f"schemalyser.{name}")
    except Exception:    # a module that is being edited is reported and passed over
        return None


def main():
    out = Path(sys.argv[1])
    lines = ["# Wording awaiting approval", "",
             "This document is generated from the code. Each string is shown with the key it has in the code, "
             "under a heading that says where a reader meets it. Text in braces is filled in when the string is used.", ""]
    missing = []

    def module(name):
        found = load(name)
        if found is None:
            missing.append(name)
        return found

    vocabulary = module("vocabulary")
    if vocabulary:
        script = [(k, v) for k, v in strings(vocabulary.CHECK_SCRIPT) if "span" in k.lower() or "fanout" in k.lower()]
        lines += section("The check script: two new kinds of check",
                         "An analyst reads these as comments in the check script. Both kinds are switched off until this wording is approved.", script)
    checks = module("checks")
    if checks:
        bands = [(name, text) for name, label in (("BAND_LABELS", "spans"), ("FANOUT_LABELS", "fanout")) if hasattr(checks, name)
                 for text in getattr(checks, name)]
        lines += section("The check results: band labels", "These appear as values in the results file that the analyst returns.", bands)
    tuning = module("tuning")
    if tuning and hasattr(tuning, "PARAMETERS"):
        described = []
        for parameter in tuning.PARAMETERS if isinstance(tuning.PARAMETERS, (list, tuple)) else tuning.PARAMETERS.values():
            key = getattr(parameter, "key", None) or (parameter.get("key") if isinstance(parameter, dict) else "")
            text = getattr(parameter, "description", None) or (parameter.get("description") if isinstance(parameter, dict) else "")
            if text:
                described.append((key, text))
        lines += section("The tunable parameters of the shadow database",
                         "The clinical lead reads these when overriding a parameter in the site rules, and they appear in the register of open questions.", described)
    release = module("release")
    if release:
        lines += section("The release script: new comments and messages",
                         "An operator reads these in the release script or when it stops. The first fourteen strings were approved earlier and are not shown.",
                         list(strings(release.WORDING))[APPROVED_RELEASE:])
    convert = module("convert")
    if convert and hasattr(convert, "WORDING"):
        lines += section("The conversion runner's report",
                         "The person running a conversion over the shadow database reads these.", strings(convert.WORDING))
    profile = module("profile")
    if profile:
        lines += section("The core profile script, its summary and its findings",
                         "The central OMOP team reads the script's comments. The clinical lead reads the summary and the findings.", strings(profile.WORDING))
    questions = module("questions")
    if questions:
        pairs = list(strings(questions.WORDING)) + list(strings(questions.IN_HAND)) + list(strings(questions.SUMMARY))
        lines += section("The register of open questions (questions.csv)",
                         "The clinical lead and hospital staff read these. Each question is followed by what it decides and the evidence that would settle it.", pairs)
    target = module("target")
    if target:
        lines += section("The checklist and readiness statement for a target query",
                         "The clinical lead and hospital staff read these.", strings(target.WORDING))
        for name, title, where in (("DRAFT_WORDING", "The generated draft query for the analytics team", "An analyst reads these as comments in the draft query."),
                                   ("REVIEW_WORDING", "The static review of a target query", "The author of a target query reads these findings.")):
            if hasattr(target, name):
                lines += section(title, where, strings(getattr(target, name)))
    dictionary = module("dictionary")
    if dictionary and hasattr(dictionary, "WORDING"):
        lines += section("The data dictionary", "A person or an LLM writing a target query reads these.", strings(dictionary.WORDING))
    boundary = module("boundary")
    if boundary:
        lines += section("The boundary run: summary.md and the command's messages",
                         "The person who runs the boundary command, or who reads its output in the state repository, reads these.", strings(boundary.WORDING))
    tables = ROOT / "fixtures" / "conversion" / "tables.json"
    if tables.exists():
        described = []
        for table in json.loads(tables.read_text()):
            described.append((table["name"], table.get("description", "")))
            described += [(f"{table['name']}.{field['name']}", field.get("description", "")) for field in table.get("fields", [])]
        lines += section("The custom tables", "A query author reads these descriptions, each of which states the rule by which a field is derived.", described)
    if missing:
        lines += ["## Not included", "", "These modules could not be read when the document was generated: " + ", ".join(missing) + ".", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    total = sum(1 for line in lines if line.startswith("- `"))
    print(f"{total} strings written to {out.name}")


if __name__ == "__main__":
    main()
