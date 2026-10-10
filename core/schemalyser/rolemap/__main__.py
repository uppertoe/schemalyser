"""The command line of the roles and the maps, as python -m schemalyser.rolemap runs it. rolemap/__init__.py describes
each command; this module, a surface of layer 5, reads the files and calls the layers that do the work: the map's own
checks (rolemap), the compilation of a query through a map (compiler.py), the role shadow (roleshadow.py), and the
proposer (propose.py).

The commands propose and confirm are for development only, and they produce no schema of record: they write a map
folder directly, which no journal records. The hospital schema of record is made and answered with python -m
schemalyser.describe, whose every command is journalled.
"""
import argparse
import json
import sys
from pathlib import Path

from .. import compiler, datadict, harness, propose, rolemap, roleshadow, summaries
from ..catalogue import Catalogue
from ..extract import decode


def _table(columns, rows):
    lines = ["\t".join(columns)]
    lines += ["\t".join("" if v is None else str(rolemap._number(v) if isinstance(v, (int, float)) else v) for v in row) for row in rows]
    return "\n".join(lines)


def _show(found):
    print(_table(found["columns"], found["rows"]))
    for band, note in found["notes"].items():
        print(f"{band}: {note}")
    print("")
    print("Coverage by year of the anaesthetic's start:")
    columns = ["start_year", "anaesthetics", *rolemap.FIGURES]
    print(_table(columns, [[r[c] for c in columns] for r in found["coverage"]]))
    print("")
    for finding in found["findings"]:
        print(finding)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="schemalyser.rolemap", description=rolemap.__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check a map against a world's catalogue")
    check.add_argument("map", type=Path)
    check.add_argument("--catalogue", type=Path, required=True)
    build = commands.add_parser("compile", help="compile an audit, or the standard counts, with a map into T-SQL")
    build.add_argument("map", type=Path)
    build.add_argument("--audit", type=Path, default=rolemap.AUDIT)
    build.add_argument("--counts", action="store_true", help="compile the standard counts instead of the audit")
    build.add_argument("--exact", action="store_true", help="leave out the blanking of counts from 1 to 4")
    rehearse = commands.add_parser("rehearse", help="run an audit on the role-level shadow, with no map")
    rehearse.add_argument("--audit", type=Path, default=rolemap.AUDIT)
    rehearse.add_argument("--seed", type=int, default=1)
    rehearse.add_argument("--anaesthetics", type=int, default=400)
    rehearse.add_argument("--no-planted", action="store_true")
    shadow = commands.add_parser("shadow", help="run an audit and the counts through a map on a world's hospital-shaped shadow")
    shadow.add_argument("world", type=Path)
    shadow.add_argument("conversion", type=Path)
    shadow.add_argument("map", type=Path)
    shadow.add_argument("--audit", type=Path, default=rolemap.AUDIT)
    shadow.add_argument("--rows", type=int, default=500)
    listing = commands.add_parser("open", help="list a map's open items")
    listing.add_argument("map", type=Path)
    scoring = commands.add_parser("scoreboard", help="say how the proposals of a saved hospital schema fared, as counts only")
    scoring.add_argument("file", type=Path, help="the saved hospital schema, its folder, or its map.json")
    proposing = commands.add_parser(
        "propose", help="for development only: propose a draft map folder, which is no schema of record",
        description="This command is for development only, and it produces no schema of record: it writes a map folder "
                    "directly, which no journal records. The hospital schema of record is made with python -m "
                    "schemalyser.describe start and propose. Schemalyser reads the data dictionary, keeps only the tables and columns that the catalogue holds, and "
                    "proposes for each role the table and column that play it. It writes the draft map to the folder that "
                    "you name, where every binding awaits a person's confirmation. The draft quotes the dictionary, so "
                    "Schemalyser writes it only to a private folder, and it prints names and counts only.")
    proposing.add_argument("dictionary", type=Path, help="the data dictionary, as a CSV or tab-separated file with headings")
    proposing.add_argument("--catalogue", type=Path, required=True, help="the catalogue of the hospital's database")
    proposing.add_argument("--out", type=Path, required=True, help="the private folder for the draft map")
    proposing.add_argument("--tables", type=Path, help="a second file that gives each table's description and primary key")
    proposing.add_argument("--model", type=Path, help="a role model other than the one in rolemodel/contract.json")
    proposing.add_argument("--heading", action="append", default=[], metavar="FIELD=HEADING",
                           help="the dictionary's own heading for a field: table, column, description, data_type or key")
    proposing.add_argument("--base", action="append", default=[], metavar="VIEW=TABLE",
                           help="the table whose rows a person has chosen for a view")
    proposing.add_argument("--world", default="the hospital", help="the name of the hospital or world, for map.json")
    proposing.add_argument("--reference", type=Path, help="a reference conversion's lineage, as python -m schemalyser.compare "
                                                         "reference writes it, whose routes become candidates beside the dictionary's")
    proposing.add_argument("--invented", action="store_true",
                           help="say that the dictionary is invented, so that its draft may be written into a published folder")
    confirming = commands.add_parser(
        "confirm", help="for development only: apply a person's answers to a draft map folder, which is no schema of record",
        description="This command is for development only, and it produces no schema of record: it rewrites a map folder "
                    "directly, which no journal records. A person's answers to the hospital schema of record are given "
                    "with python -m schemalyser.describe answer. Each row of the file of confirmations names a binding, such as role_patient.birth_date, role_patient "
                    "rows or kind map_cuff, and gives the answer yes, no or not sure. With no, the row may give the "
                    "replacement as TABLE.COLUMN, with its link as via TABLE.COLUMN = TABLE.COLUMN where the view does not "
                    "already reach that table, or the local codes of a kind. Schemalyser records each answer with its "
                    "date and writes the views that changed again.")
    confirming.add_argument("map", type=Path)
    confirming.add_argument("confirmations", type=Path, help="a CSV or tab-separated file with the headings attribute and "
                                                             "answer, and optionally replacement, by, date and note")
    confirming.add_argument("--catalogue", type=Path, help="the catalogue, against which the map is checked again")
    confirming.add_argument("--dictionary", type=Path, help="the data dictionary, to find the link to a replacement and quote it")
    confirming.add_argument("--tables", type=Path)
    confirming.add_argument("--heading", action="append", default=[], metavar="FIELD=HEADING")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            found = rolemap.read_map(args.map, decode(args.catalogue.read_bytes()))
            for view, tables in found["tables"].items():
                print(f"{view}: one SELECT over {', '.join(tables)}, with the contract's columns, each in the catalogue.")
            print(f"The map has {len(rolemap.open_items(found))} open items.")
        elif args.command == "compile":
            found = rolemap.read_map(args.map)
            if args.counts:
                for name, item in rolemap.count_queries().items():
                    print(f"-- {name}: {item['says']}")
                    print(compiler.compile_query(item["sql"], found, nolock=True).rstrip() + ";\n")
            else:
                print(compiler.compile_query(decode(args.audit.read_bytes()), found, blank=not args.exact), end="")
        elif args.command == "rehearse":
            con = roleshadow.role_shadow(args.seed, args.anaesthetics, not args.no_planted)
            _show(roleshadow.result(roleshadow.duckdb_runner(con), decode(args.audit.read_bytes())))
        elif args.command == "shadow":
            found = rolemap.read_map(args.map)
            done = roleshadow.hospital_run(harness.World.from_folder(args.world), args.conversion, found, decode(args.audit.read_bytes()),
                                args.rows)
            _show(done["result"])
        elif args.command == "scoreboard":
            board = rolemap.scoreboard(rolemap.read_saved_map(args.file))
            print(board["text"], end="")
            beside = args.file if args.file.is_dir() else args.file.parent
            written = summaries.write_scoreboard(board, beside)
            print(f"Schemalyser has written the counts alone to {written[1].name}, beside the saved schema. Once you have "
                  "read that file, you may show it to the developer's model.")
        elif args.command == "open":
            for item in rolemap.open_items(rolemap.read_map(args.map)):
                print(f"{item['about']} ({item['status']}): {item['question']}")
        elif args.command in ("propose", "confirm"):
            _propose_or_confirm(args)
    except rolemap.MapError as error:
        raise SystemExit(f"schemalyser.rolemap: {error}")
    return 0


def _pairs(items, what):
    pairs = {}
    for item in items:
        name, _, value = item.partition("=")
        if not name or not value:
            raise SystemExit(f"schemalyser.rolemap: {what} is written as NAME=VALUE, and not {item}.")
        pairs[name.strip()] = value.strip()
    return pairs


def _propose_or_confirm(args):
    try:
        headings = _pairs(args.heading, "--heading")
        catalogue = Catalogue.from_csv(decode(args.catalogue.read_bytes())) if args.catalogue else None
        if args.command == "propose":
            model = json.loads(decode(args.model.read_bytes())) if args.model else None
            dictionary = datadict.load(args.dictionary, args.tables, headings)
            reference = propose.read_reference(args.reference.read_bytes()) if args.reference else None
            proposal, found, missing = propose.propose_map(dictionary, catalogue, args.out, model, world=args.world,
                                                           bases=_pairs(args.base, "--base"), invented=args.invented,
                                                           reference=reference)
            if missing:
                print(propose.WORDING["missing"].format(count=missing))
            for view, item in proposal.items():
                if item is None:
                    print(propose.WORDING["summary_none"].format(view=view))
                    continue
                levels = [c["confidence"] for c in item["columns"].values()]
                shown = ", ".join(f"{levels.count(level)} {level}" for level in ("high", "medium", "low") if levels.count(level))
                none = levels.count("none")
                shown += (", and " if shown else "") + f"{none} with nothing that fits" if none else ""
                print(propose.WORDING["summary"].format(view=view, table=item["rows"]["table"], bound=len(levels) - none,
                                                        total=len(levels), confidence=shown or "none"))
            print(propose.WORDING["written"].format(folder=args.out, items=len(rolemap.open_items(found))))
        else:
            dictionary = datadict.load(args.dictionary, args.tables, headings) if args.dictionary else None
            counts, found = propose.confirm(args.map, args.confirmations, catalogue, dictionary)
            detail = ", ".join(f"{counts[a]} {a}" for a in ("yes", "no", "not sure") if counts[a]) or "none"
            print(propose.WORDING["answers"].format(count=sum(counts.values()), detail=detail, items=len(rolemap.open_items(found))))
    except (datadict.DictionaryError, propose.ProposeError) as error:
        raise SystemExit(f"schemalyser.rolemap: {error}")


if __name__ == "__main__":
    sys.exit(main())
