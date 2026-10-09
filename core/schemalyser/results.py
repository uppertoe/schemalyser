"""The results package: a production run's output, kept inside the hospital beside what it is the result of.

An execution package says what may run; a results package says what a run returned and what that result can and cannot
be relied on for. It is separate from the execution package, it is patient-derived material, and it stays inside the
hospital. Whether any of it leaves is a separate approval under the hospital's own rules, and nothing in it returns to
the public workspace, aggregated or not, so write() refuses to place one inside a workspace.

    FOLDER/
      results.json          the format, the approved query's version and hashes, the cohort's definition, the coverage
                            limitations that the hospital schema records for the period, the disclosure-control state,
                            the output's file and hash, and the reconciliation evidence
      output/               the output as the database analyst saved it, unchanged
      reconciliation/       the reconciliation of a sample against the clinical record, once there is one
      README.md             what the package is and what it does not protect

    python -m schemalyser.results write PACKAGE OUTPUT --out FOLDER [--reconciliation FILE] [--by NAME] [--date YYYY-MM-DD]

The execution package must still stand as it was built and carry an approval; otherwise no results package is
written, because there would be no approved query for the output to be the result of.
"""
import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

from . import audit, workspace

FORMAT = "schemalyser-results/1"
RESULTS = "results.json"

WORDING = {
    "voided": "The execution package has changed since it was built, so Schemalyser cannot say what query this output is the result of, and has written no results package.",
    "not_approved": "The execution package has not been approved, so the output is not the result of an approved query, and Schemalyser has written no results package.",
    "workspace": "{folder} lies inside a public workspace, where no result may go, so Schemalyser has written no results package there.",
    "occupied": "{folder} already holds files, and a results package is written only into an empty folder.",
    "no_output": "{name} is not a file that Schemalyser can keep as the output.",
    "written": "Schemalyser wrote the results package to {folder}. It holds patient-derived material and stays inside the hospital.",
    "stays": "This results package holds what a production run returned, which is patient-derived material. It stays inside the hospital, and whether any of it leaves is a separate approval under the hospital's own rules. Nothing in it returns to the public workspace.",
    "no_reconciliation": "No reconciliation of a sample against the clinical record has been recorded for this output yet.",
    "blanked": "Every count from 1 to 4 in the result is left blank.",
    "exact": "The approval allowed exact small numbers, so no count was left blank.",
    "rounded": "No count was rounded.",
    "rows": "The result holds rows of patients' records rather than counts alone.",
    "keys_pseudonymised": "The anaesthetics are numbered in the order of their starts in place of their keys, and the link from each number to its anaesthetic is a separate section that never leaves.",
    "keys_recorded": "The anaesthetics carry their keys as the hospital records them.",
    "protects_blank": "A count from 1 to 4 cannot be read from the result itself, so a small group is not singled out by its count alone.",
    "protects_numbers": "A number in place of a key cannot be turned back into the anaesthetic without the separate link.",
    "not_blank": "A blank count can sometimes be worked out from a total, or from another result about the same cohort, so leaving it blank does not prevent differencing.",
    "not_rows": "Rows of a patient's record can identify the patient from their dates and values, whatever is done to the keys, so their release needs the hospital's own approval.",
    "not_exact": "Exact small numbers can point to a patient, which the approval accepted when it allowed them.",
    "not_linkage": "Nothing here prevents the result from being linked with other data that holds the same patients.",
}


class ResultsError(ValueError):
    """A results package that cannot be written; the message says why in one sentence."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n"


def _inside_workspace(folder):
    """Whether folder lies inside a public workspace, which carries a manifest.json naming its profile."""
    for place in [folder, *folder.parents]:
        manifest = place / "manifest.json"
        if manifest.is_file():
            try:
                held = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(held, dict) and held.get("profile") in workspace.PROFILES:
                return True
    return False


def disclosure(manifest):
    """What was rounded or suppressed in the result, and what that does and does not protect."""
    decisions = manifest.get("decisions") or {}
    exact = bool(decisions.get("exact_small_numbers"))
    expected = manifest.get("expected_output") or {}
    counts = expected.get("counts") or []
    rows = expected.get("counts_only") is False
    keys = ((manifest.get("specification") or {}).get("output") or {}).get("keys")
    done = [WORDING["exact"] if exact else WORDING["blanked"], WORDING["rounded"]] if counts else [WORDING["rounded"]]
    protects, not_protected = [], []
    if counts and not exact:
        protects.append(WORDING["protects_blank"])
        not_protected.append(WORDING["not_blank"])
    if counts and exact:
        not_protected.append(WORDING["not_exact"])
    if rows:
        done.append(WORDING["rows"])
        if keys == "pseudonymised":
            done.append(WORDING["keys_pseudonymised"])
            protects.append(WORDING["protects_numbers"])
        elif keys:
            done.append(WORDING["keys_recorded"])
        not_protected.append(WORDING["not_rows"])
    not_protected.append(WORDING["not_linkage"])
    return {"counts": counts, "small_counts_blanked": bool(counts) and not exact, "rounded": False,
            "row_level": rows, "keys": keys, "what_was_done": done, "protects": protects,
            "does_not_protect": not_protected}


def write(package, output, out, reconciliation=None, ran_by=None, date=None):
    """Writes the results package for the output of one run of an approved execution package. Returns its record.
    Raises ResultsError where the package has changed or is not approved, or where out lies in a public workspace."""
    package, output, out = Path(package), Path(output), Path(out).resolve()
    date = date or dt.date.today().isoformat()
    if _inside_workspace(out):
        raise ResultsError(WORDING["workspace"].format(folder=out.name))
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ResultsError(WORDING["occupied"].format(folder=out.name))
    if not output.is_file() or output.is_symlink():
        raise ResultsError(WORDING["no_output"].format(name=output.name))
    standing = audit.status(package)
    if standing["voided"]:
        raise ResultsError(WORDING["voided"])
    manifest = json.loads((package / audit.MANIFEST).read_text(encoding="utf-8"))
    approval = manifest.get("approval") or {}
    if approval.get("state") != "approved":
        raise ResultsError(WORDING["not_approved"])
    found = json.loads((package / "feasibility.json").read_text(encoding="utf-8"))
    (out / "output").mkdir(parents=True)
    kept = out / "output" / output.name
    shutil.copyfile(output, kept)
    evidence = {"state": "not recorded", "says": WORDING["no_reconciliation"]}
    if reconciliation is not None:
        reconciliation = Path(reconciliation)
        (out / "reconciliation").mkdir()
        shutil.copyfile(reconciliation, out / "reconciliation" / reconciliation.name)
        evidence = {"state": "recorded", "file": f"reconciliation/{reconciliation.name}",
                    "sha256": sha256(reconciliation.read_bytes())}
    schema = {k: v for k, v in (manifest.get("schema_file") or {}).items() if k != "readiness"}
    record = {
        "format": FORMAT, "date": date, "ran_by": ran_by or audit.NOT_RECORDED, "says": WORDING["stays"],
        "approved_query": {"package": package.name, "query": manifest.get("query"), "sql_sha256": manifest["sql_sha256"],
                           "inputs_sha256": approval.get("inputs_sha256"), "schema": schema,
                           "contract": manifest.get("contract"), "policy_version": manifest.get("policy_version"),
                           "execution_class": manifest.get("execution_class"), "tool_version": manifest.get("tool_version"),
                           "approval": {"by": approval.get("by"), "date": approval.get("date")}},
        "cohort": {"definition": manifest.get("cohort"), "period": manifest.get("period"),
                   "decisions": manifest.get("decisions"), "specification": manifest.get("specification"),
                   "episodes": manifest.get("episodes")},
        "coverage": {"period": (found.get("coverage") or {}).get("period"), "limitations": found.get("coverage"),
                     "claims": found.get("claims")},
        "disclosure": disclosure(manifest),
        "output": {"file": f"output/{output.name}", "sha256": sha256(kept.read_bytes()), "bytes": kept.stat().st_size,
                   "expected_columns": (manifest.get("expected_output") or {}).get("columns")},
        "reconciliation": evidence,
    }
    (out / RESULTS).write_text(_json(record), encoding="utf-8")
    lines = ["# The results package", "", WORDING["stays"], "", "## What was done to protect the result", ""]
    lines += [f"- {line}" for line in record["disclosure"]["what_was_done"]]
    lines += ["", "## What that protects", ""] + [f"- {line}" for line in record["disclosure"]["protects"] or ["Nothing beyond the approval itself."]]
    lines += ["", "## What it does not protect", ""] + [f"- {line}" for line in record["disclosure"]["does_not_protect"]]
    lines += ["", "## The coverage of the record for the period", "", (found.get("coverage") or {}).get("says") or
              "The feasibility report records nothing about the coverage of the recording pathways.", ""]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.results",
                                     description="Write the results package of a production run, inside the hospital.")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("write", help="Write the results package for one run's output.")
    one.add_argument("package")
    one.add_argument("output")
    one.add_argument("--out", required=True)
    one.add_argument("--reconciliation")
    one.add_argument("--by")
    one.add_argument("--date")
    args = parser.parse_args(argv)
    try:
        write(args.package, args.output, args.out, args.reconciliation, args.by, args.date)
    except (ResultsError, audit.AuditError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(WORDING["written"].format(folder=Path(args.out).name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
