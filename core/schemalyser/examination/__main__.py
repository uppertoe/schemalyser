"""The command line of the examination: python -m schemalyser.examination run PACKAGE.json [--admit draft] [--json]."""
import argparse
import sys
from pathlib import Path

from . import metamorphic, mutants, oracle, planted, shadow, static, status


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.examination", description="The examination of a question package.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the examination's stages over a package and print the report.")
    run.add_argument("package")
    run.add_argument("--admit", choices=("draft",), help="read a draft part, which only an examination does")
    run.add_argument("--json", action="store_true", help="print the report as JSON")
    run.add_argument("--case", action="append", default=[], help="a registered planted case to run the package against")
    run.add_argument("--seed", type=int, default=1, help="the seed of the transform applied to each case")
    run.add_argument("--mutants", action="store_true", help="also run the fixed mutations of the question through the stages")
    run.add_argument("--key", help="the examiner's private key file, hex, with which the report is signed")
    run.add_argument("--out", help="write the finished report, signed where a key is given, to this file")
    check = commands.add_parser("verify", help="Verify a report's signature and say the level it grants now.")
    check.add_argument("report")
    check.add_argument("--public-key", help="the examiner's public key file, hex; the repository's when left out")
    enrol = commands.add_parser("register", help="Write or renew the register's entry for a planted case as its files are now.")
    enrol.add_argument("name")
    enrol.add_argument("--kind", choices=planted.KINDS, default="question")
    args = parser.parse_args(argv)
    if args.command == "verify":
        import json
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        public = status.read_key(args.public_key) if args.public_key else None
        found = status.verify(report, public)
        print(found["says"])
        print(f"The report grants the level {found['level']}.")
        return 0 if found["verified"] else 1
    if args.command == "register":
        entry = planted.register(args.name, args.kind)
        print(f"The case {args.name} is registered as a {entry['kind']} case, written on {entry['date']}, with {len(entry['files'])} files hashed.")
        return 0
    report = static.run(args.package, admit=args.admit)
    if report["level"] == "well formed":
        report["shadow"] = shadow.run(args.package, admit=args.admit)
        if report["shadow"]["outcome"] == "passed":
            report["metamorphic"] = metamorphic.run(args.package, seed=args.seed)
            report["oracle"] = oracle.run(args.package)
            if args.mutants:
                from .. import question
                sql, _, package = question.sql_and_decisions(args.package)
                report["mutants"] = mutants.run(sql, package.get("examinable"), args.case, [c["name"] for c in package["capabilities"]], seed=args.seed)
        if args.case:
            from .. import question
            sql = question.sql_and_decisions(args.package)[0]
            report["planted"] = [planted.run_case(sql, name, args.seed) for name in args.case]
    if args.json:
        print(static.to_json(report), end="")
    else:
        for line in static.sentences(report):
            print(line)
        for stage in ("shadow", "metamorphic", "oracle", "mutants"):
            if stage in report:
                print(report[stage]["says"])
        for case in report.get("planted", []):
            print(case["says"])
    report = status.finish(report, status.read_key(args.key) if args.key else None)
    if args.out:
        Path(args.out).write_text(static.to_json(report), encoding="utf-8")
    if not args.json:
        print(report["status"]["says"])
    passed = (report["level"] == "well formed" and report.get("shadow", {}).get("outcome") == "passed"
              and report.get("metamorphic", {}).get("outcome") == "passed"
              and report.get("oracle", {}).get("outcome") in ("passed", "not applied")
              and all(c["outcome"] == "passed" for c in report.get("planted", [])))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
