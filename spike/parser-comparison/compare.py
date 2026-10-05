"""Scores each parser's output against fixtures/expected.json."""
import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
expected = json.loads((FIXTURES / "expected.json").read_text())


def score(found, want):
    found, want = set(found), set(want)
    return sorted(want - found), sorted(found - want)


for result_path in sys.argv[1:]:
    result = json.loads(Path(result_path).read_text())
    print(f"\n=== {result['parser']} ===")
    totals = {"files": 0, "outcome_right": 0, "tables_right": 0, "columns_right": 0, "columns_checked": 0}
    for name, want in expected.items():
        got = result["files"].get(name)
        totals["files"] += 1
        if got is None:
            print(f"  {name}: no result")
            continue
        notes = []
        outcome_right = got["ok"] != bool(want.get("expectFailure"))
        totals["outcome_right"] += outcome_right
        if not outcome_right:
            notes.append(f"outcome ok={got['ok']} error={got['error']}")
        missing, extra = score(got["tables"], want["tables"])
        totals["tables_right"] += not missing and not extra
        if missing:
            notes.append(f"tables missing {missing}")
        if extra:
            notes.append(f"tables extra {extra}")
        if "columns" in want:
            totals["columns_checked"] += 1
            missing, extra = score(got["columns"], want["columns"])
            totals["columns_right"] += not missing and not extra
            if missing:
                notes.append(f"columns missing {missing}")
            if extra:
                notes.append(f"columns extra {extra}")
        for key in ("opaque", "analysisErrors"):
            if got.get(key):
                notes.append(f"{key}={got[key]}")
        if got.get("qualify_errors"):
            notes.append(f"qualify_errors={got['qualify_errors']}")
        status = "ok " if not notes else "!! "
        print(f"  {status}{name}  [{got['statements']} stmts, {len(got['columns'])} cols, {got['ms']} ms]")
        for note in notes:
            print(f"       {note}")
    print(f"  totals: {totals}")
