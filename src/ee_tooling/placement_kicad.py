"""Score a KiCad board's placement, and draw it.

placement-score BOARD [--rules FILE] [--render PNG]: the ratsnest's
crossings and length, decoupling distances, courtyard overlaps and
decoupling order (placement.py), from BOARD's placement.toml unless
--rules names another.  Exits non-zero on an overlap or a broken rule.
It reads the board with KiCad's pcbnew, so it runs in a venv made with
`uv venv --system-site-packages --python /usr/bin/python3`.
"""

import argparse
import sys
import tomllib
from pathlib import Path

from ee_tooling.placement import Box, Footprint, Layout, Pad, Point, Rules, report, score
from ee_tooling.placement_render import render

RULES = "placement.toml"
SCALE = 16.0  # px per mm


def load(board_file: Path) -> Layout:
    """A board's footprints, pads and outline, through pcbnew."""
    try:
        # Imported here, to say what to do when the venv lacks it.
        import pcbnew
    except ImportError:
        sys.exit(
            "pcbnew is not importable: make the venv with"
            " uv venv --system-site-packages --python /usr/bin/python3"
        )

    def point(v: "pcbnew.VECTOR2I") -> Point:
        return float(pcbnew.ToMM(v.x)), float(pcbnew.ToMM(v.y))

    def box(b: "pcbnew.BOX2I") -> Box:
        return Box(
            float(pcbnew.ToMM(b.GetLeft())),
            float(pcbnew.ToMM(b.GetTop())),
            float(pcbnew.ToMM(b.GetRight())),
            float(pcbnew.ToMM(b.GetBottom())),
        )

    board = pcbnew.LoadBoard(str(board_file))
    footprints = []
    for f in board.GetFootprints():
        yard = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
        pads = tuple(
            Pad(
                str(p.GetNumber()),
                str(p.GetNetname()),
                point(p.GetPosition()),
                box(p.GetBoundingBox()),
            )
            for p in f.Pads()
        )
        footprints.append(
            Footprint(
                reference=str(f.GetReference()),
                value=str(f.GetValue()),
                position=point(f.GetPosition()),
                pads=pads,
                courtyard=None if yard.IsEmpty() else box(yard.BBox()),
            )
        )
    return Layout(tuple(footprints), box(board.GetBoardEdgesBoundingBox()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("board", type=Path)
    ap.add_argument("--rules", type=Path, help=f"default: {RULES} beside the board")
    ap.add_argument("--render", type=Path, metavar="PNG", help="draw the placement to PNG")
    ap.add_argument("--scale", type=float, default=SCALE, help="px per mm in the PNG")
    a = ap.parse_args()

    rules_file = a.rules or a.board.parent / RULES
    if rules_file.exists():
        try:
            rules = Rules.load(rules_file.read_text())
        except (tomllib.TOMLDecodeError, KeyError) as e:
            sys.exit(f"{rules_file}: {e!r}")
    elif a.rules:
        sys.exit(f"{rules_file} is not there")
    else:
        print(f"{rules_file} is not there: no poured nets, no decoupling", file=sys.stderr)
        rules = Rules(frozenset(), ())
    if not a.board.exists():
        sys.exit(f"{a.board} is not there")
    layout = load(a.board)
    s = score(layout, rules)
    print("\n".join(report(s)))
    if a.render:
        render(layout, rules, a.scale).save(a.render)
    sys.exit(1 if s.overlaps or s.problems else 0)
