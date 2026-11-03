"""design-review: the evidence and the lints of an adversarial design
review (skills/design-review/SKILL.md, docs/design-review.md).

design-review evidence DIR NAME OUT   kicad-cli exports and summaries into OUT
design-review netlist NETLIST OUT     nets.txt and components.txt into OUT
design-review board PCB               board summary, stdout
design-review erc ERC_JSON            ERC summary, stdout
design-review pages PDF               schematic PDF page to sheet map, stdout
design-review pairs PCB NET_P,NET_N...  pair routing gaps, stdout
design-review findings FILE.md        lint its findings tables; exit 1 on problems
design-review punch-list REPORT PUNCH check a punch list against its report; exit 1 on problems
"""

import argparse
import sys
from pathlib import Path

from ee_tooling import (
    review_board,
    review_erc,
    review_findings,
    review_netlist,
    review_pages,
    review_pairs,
    review_punch,
)
from ee_tooling.review_evidence import Project, export


def pair(arg: str) -> tuple[str, str]:
    first, sep, second = arg.partition(",")
    if not sep or not first or not second or "," in second:
        raise argparse.ArgumentTypeError(f"a pair is NET_P,NET_N: {arg!r}")
    return first, second


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("evidence", help="export the review evidence")
    s.add_argument("directory", type=Path, help="holds NAME.kicad_sch and NAME.kicad_pcb")
    s.add_argument("name")
    s.add_argument("out", type=Path)
    s.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    s = sub.add_parser("netlist", help="condense a netlist")
    s.add_argument("netlist", type=Path)
    s.add_argument("out", type=Path)
    s = sub.add_parser("board", help="summarize a board")
    s.add_argument("pcb", type=Path)
    s = sub.add_parser("erc", help="summarize an ERC report")
    s.add_argument("erc_json", type=Path)
    s = sub.add_parser("pages", help="map schematic PDF pages to sheets")
    s.add_argument("pdf", type=Path)
    s = sub.add_parser("pairs", help="pair routing gaps")
    s.add_argument("pcb", type=Path)
    s.add_argument("pairs", type=pair, nargs="+", metavar="NET_P,NET_N")
    s = sub.add_parser("findings", help="lint the findings tables")
    s.add_argument("file", type=Path)
    s = sub.add_parser("punch-list", help="check a punch list against its report")
    s.add_argument("report", type=Path)
    s.add_argument("punch", type=Path)
    a = ap.parse_args()
    match a.command:
        case "evidence":
            failed = export(Project(a.directory, a.name), a.out, a.dry_run)
            sys.exit(1 if failed else 0)
        case "netlist":
            a.out.mkdir(parents=True, exist_ok=True)
            parts, nets = review_netlist.condense(a.netlist, a.out)
            print(f"{parts} components, {nets} nets -> {a.out}")
        case "board":
            sys.stdout.write(review_board.summary(a.pcb))
        case "erc":
            sys.stdout.write(review_erc.summary(a.erc_json))
        case "pages":
            print("\n".join(review_pages.page_map(a.pdf)))
        case "pairs":
            sys.stdout.write(review_pairs.report(a.pcb, a.pairs))
        case "findings":
            found = review_findings.lint(a.file.read_text(encoding="utf-8"))
            for p in found.problems:
                print(f"{a.file}:{p.line}: {p.message}", file=sys.stderr)
            print(f"{found.tables} tables, {found.rows} rows, {len(found.problems)} problems")
            sys.exit(0 if found.ok else 1)
        case "punch-list":
            in_punch, in_report = review_punch.check(
                a.report.read_text(encoding="utf-8"), a.punch.read_text(encoding="utf-8")
            )
            for path, problems in ((a.punch, in_punch), (a.report, in_report)):
                for p in problems:
                    print(f"{path}:{p.line}: {p.message}", file=sys.stderr)
            print(f"{len(in_punch) + len(in_report)} problems")
            sys.exit(1 if in_punch or in_report else 0)
