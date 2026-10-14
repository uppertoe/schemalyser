"""The command line of the examination: python -m schemalyser.examination run PACKAGE.json [--admit draft] [--json]."""
import argparse
import sys

from . import metamorphic, oracle, planted, shadow, static


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.examination", description="The examination of a question package.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the examination's stages over a package and print the report.")
    run.add_argument("package")
    run.add_argument("--admit", choices=("draft",), help="read a draft part, which only an examination does")
    run.add_argument("--json", action="store_true", help="print the report as JSON")
    run.add_argument("--case", action="append", default=[], help="a registered planted case to run the package against")
    run.add_argument("--seed", type=int, default=1, help="the seed of the transform applied to each case")
    enrol = commands.add_parser("register", help="Write or renew the register's entry for a planted case as its files are now.")
    enrol.add_argument("name")
    enrol.add_argument("--kind", choices=planted.KINDS, default="question")
    args = parser.parse_args(argv)
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
        if args.case:
            from .. import question
            sql = question.sql_and_decisions(args.package)[0]
            report["planted"] = [planted.run_case(sql, name, args.seed) for name in args.case]
    if args.json:
        print(static.to_json(report), end="")
    else:
        for line in static.sentences(report):
            print(line)
        for stage in ("shadow", "metamorphic", "oracle"):
            if stage in report:
                print(report[stage]["says"])
        for case in report.get("planted", []):
            print(case["says"])
    passed = (report["level"] == "well formed" and report.get("shadow", {}).get("outcome") == "passed"
              and report.get("metamorphic", {}).get("outcome") == "passed"
              and report.get("oracle", {}).get("outcome") in ("passed", "not applied")
              and all(c["outcome"] == "passed" for c in report.get("planted", [])))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
