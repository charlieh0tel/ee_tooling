"""Add ground stitching vias to a KiCad 10 board (docs/stitch.md).

stitch BOARD [-o OUT] [options]: through vias on the ground net, first at
each --at, then along every Edge.Cuts outline (stitch.ring_marks), then
in any gap over MAX_GAP still left along one (stitch.gap_marks, with twice
the reach), then on a grid over the board (stitch.grid_marks), each at
the clear spot nearest its mark.  A mark with a ground barrel already
near it needs no via.  Then KiCad refills the zones and runs DRC with the
board's own rules (kicad-cli pcb drc --refill-zones --save-board); each
new via DRC reports (a custom rule's clearance, a via no zone reaches) is
taken out again, and the zones refilled.  The board is written to BOARD
itself unless -o; the project file must be beside the board written.

It reads and writes the board with KiCad's pcbnew, so it runs in a venv
made with `uv venv --system-site-packages --python /usr/bin/python3`;
close the board in KiCad first.

A spot is clear when the via's copper keeps every other net's pad, track
and via at the larger of the two items' own clearances; its hole keeps
every hole at the board's hole-to-hole minimum; it is on the board, its
copper the board's copper-to-edge clearance in from every outline; it is
outside every rule area that forbids vias (the board's or a footprint's)
and every mounting hole's courtyard.
"""

import argparse
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pcbnew

from ee_tooling.kicad_style_board import Point, Segment, edge, on_board, where
from ee_tooling.kicad_style_finishing import (
    MAX_GAP,
    MOUNTING_HOLE,
    barrels,
    plane_net,
    stitching,
)
from ee_tooling.sexpr import Expression, parse
from ee_tooling.stitch import Settings, clearance, gap_marks, grid_marks, ring_marks, spots

# A ring mark with a ground barrel this close needs no via: a fraction of
# the pitch, so the ring stays no further apart than it.
RING_NEAR = 0.5
# Bounding boxes this much past a via's reach are searched for obstacles, mm.
MARGIN = 1.0
# A DRC item this close to a placed via is that via, mm.
SAME = 0.005
# How many times DRC may take vias out before stitch gives up.
DRC_ROUNDS = 3


@dataclass(frozen=True)
class Via:
    diameter: float  # mm
    drill: float  # mm


@dataclass
class Board:
    """A pcbnew board's obstacles to a stitching via on one net, mm."""

    kicad: ModuleType  # the pcbnew module
    board: Any  # its BOARD
    net: str
    via: Via
    outline: list[Segment]
    holes: list[tuple[Point, float]] = field(default_factory=list)  # center, radius
    grounds: list[Point] = field(default_factory=list)  # the net's barrels

    def mm(self, v: "pcbnew.VECTOR2I") -> Point:
        return float(self.kicad.ToMM(v.x)), float(self.kicad.ToMM(v.y))

    def iu(self, p: Point) -> "pcbnew.VECTOR2I":
        return self.kicad.VECTOR2I(self.kicad.FromMM(p[0]), self.kicad.FromMM(p[1]))

    def copper(self) -> list[Any]:
        """Every pad, track and via on another net."""
        items = [t for t in self.board.GetTracks() if t.GetNetname() != self.net]
        items += [
            p
            for f in self.board.GetFootprints()
            for p in f.Pads()
            if p.GetNetname() != self.net or not p.GetNetname()
        ]
        return items

    def keepouts(self) -> list[Any]:
        """Shapes no via may touch: rule areas forbidding vias, and
        mounting holes' courtyards."""
        areas = [z for z in self.board.Zones() if z.GetIsRuleArea()]
        areas += [z for f in self.board.GetFootprints() for z in f.Zones() if z.GetIsRuleArea()]
        shapes = [z.Outline() for z in areas if z.GetDoNotAllowVias()]
        for f in self.board.GetFootprints():
            if str(f.GetFPIDAsString()).rpartition(":")[2].startswith(MOUNTING_HOLE):
                for layer in (self.kicad.F_CrtYd, self.kicad.B_CrtYd):
                    yard = f.GetCourtyard(layer)
                    if yard.OutlineCount():
                        shapes.append(yard)
        return shapes


def load_holes(b: Board) -> None:
    pcbnew = b.kicad
    for t in b.board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            b.holes.append((b.mm(t.GetPosition()), pcbnew.ToMM(t.GetDrillValue()) / 2))
    for f in b.board.GetFootprints():
        for p in f.Pads():
            if p.HasHole():
                size = p.GetDrillSize()
                b.holes.append((b.mm(p.GetPosition()), pcbnew.ToMM(min(size.x, size.y)) / 2))


class Placer:
    """Places stitching vias on a Board, keeping each clear."""

    def __init__(self, b: Board) -> None:
        self.b = b
        pcbnew = b.kicad
        settings = b.board.GetDesignSettings()
        self.hole_to_hole = pcbnew.ToMM(settings.m_HoleToHoleMin)
        self.edge_clearance = pcbnew.ToMM(settings.m_CopperEdgeClearance)
        self.layers = list(b.board.GetEnabledLayers().CuStack())
        self.items = [
            (item, box(pcbnew, item), [x for x in self.layers if item.IsOnLayer(x)])
            for item in b.copper()
        ]
        self.keepouts = b.keepouts()
        probe = pcbnew.PCB_VIA(b.board)
        probe.SetNet(b.board.FindNet(b.net))
        self.own = pcbnew.ToMM(probe.GetOwnClearance(pcbnew.F_Cu))

    def clear(self, p: Point, nearby: list[tuple[Any, list[Any]]]) -> bool:
        pcbnew = self.b.kicad
        radius = self.b.via.diameter / 2
        if not on_board(p, self.b.outline):
            return False
        if clearance(p, self.b.outline) < self.edge_clearance + radius:
            return False
        drill = self.b.via.drill / 2
        if any(math.dist(p, c) - r - drill < self.hole_to_hole for c, r in self.b.holes):
            return False
        point = self.b.iu(p)
        for item, layers in nearby:
            for layer in layers:
                gap = max(self.own, pcbnew.ToMM(item.GetOwnClearance(layer)))
                if item.GetEffectiveShape(layer).Collide(point, pcbnew.FromMM(radius + gap)):
                    return False
        return not any(k.Collide(point, pcbnew.FromMM(radius)) for k in self.keepouts)

    def place(self, mark: Point, settings: Settings) -> Point | None:
        """A via at the clear spot nearest mark, within reach; None if none."""
        reach = settings.reach + self.b.via.diameter / 2 + MARGIN
        nearby = [
            (item, layers)
            for item, (left, top, right, bottom), layers in self.items
            if left - reach <= mark[0] <= right + reach and top - reach <= mark[1] <= bottom + reach
        ]
        for p in spots(mark, settings):
            if self.clear(p, nearby):
                self.add(p)
                return p
        return None

    def add(self, p: Point) -> None:
        pcbnew = self.b.kicad
        v = pcbnew.PCB_VIA(self.b.board)
        v.SetPosition(self.b.iu(p))
        v.SetWidth(pcbnew.FromMM(self.b.via.diameter))
        v.SetDrill(pcbnew.FromMM(self.b.via.drill))
        v.SetNet(self.b.board.FindNet(self.b.net))
        self.b.board.Add(v)
        self.b.holes.append((p, self.b.via.drill / 2))
        self.b.grounds.append(p)


def box(pcbnew: ModuleType, item: "pcbnew.BOARD_ITEM") -> tuple[float, float, float, float]:
    bb = item.GetBoundingBox()
    return (
        pcbnew.ToMM(bb.GetLeft()),
        pcbnew.ToMM(bb.GetTop()),
        pcbnew.ToMM(bb.GetRight()),
        pcbnew.ToMM(bb.GetBottom()),
    )


def near(p: Point, grounds: list[Point], distance: float) -> bool:
    return any(math.dist(p, g) < distance for g in grounds)


def stitch(
    expression: Expression, placer: Placer, settings: Settings, extra: list[Point]
) -> tuple[list[Point], list[Point]]:
    """The vias placed, and the marks that found no clear spot."""
    added: list[Point] = []
    missed: list[Point] = []
    grounds = placer.b.grounds

    def run(marks: list[Point], needed: float, at: Settings) -> None:
        for mark in marks:
            if needed and near(mark, grounds, needed):
                continue
            p = placer.place(mark, at)
            if p is None:
                missed.append(mark)
            else:
                added.append(p)

    run(extra, 0.0, settings)
    run(ring_marks(expression, settings), settings.pitch * RING_NEAR, settings)
    wider = replace(settings, reach=2 * settings.reach)
    run(gap_marks(expression, grounds, settings), settings.pitch * RING_NEAR / 2, wider)
    run(grid_marks(expression, settings), settings.near, settings)
    return added, missed


def drc(board: Path) -> list[Point]:
    """Refill the board's zones and run KiCad's DRC with its own rules,
    saving the board; where each via a violation names is."""
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "drc.json"
        subprocess.run(
            [
                "kicad-cli",
                "pcb",
                "drc",
                "--refill-zones",
                "--save-board",
                "--format",
                "json",
                "--severity-error",
                "--severity-warning",
                "-o",
                str(report),
                str(board),
            ],
            check=True,
            capture_output=True,
        )
        found = json.loads(report.read_text())
    items = [
        i
        for v in [*found.get("violations", []), *found.get("unconnected_items", [])]
        for i in v.get("items", [])
    ]
    return [
        (float(i["pos"]["x"]), float(i["pos"]["y"]))
        for i in items
        if str(i.get("description", "")).startswith("Via [")
    ]


def remove(pcbnew: ModuleType, board_file: Path, vias: list[Point]) -> None:
    """Take vias out of a saved board, at those points."""
    board = pcbnew.LoadBoard(str(board_file))
    for t in list(board.GetTracks()):
        if isinstance(t, pcbnew.PCB_VIA):
            p = (pcbnew.ToMM(t.GetPosition().x), pcbnew.ToMM(t.GetPosition().y))
            if any(math.dist(p, v) <= SAME for v in vias):
                board.Remove(t)
    board.Save(str(board_file))


def point(text: str) -> Point:
    x, _, y = text.partition(",")
    return float(x), float(y)


def via_size(text: str) -> Via:
    diameter, _, drill = text.partition("/")
    return Via(float(diameter), float(drill))


def main() -> None:
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="stitch", description=__doc__.split("\n\n")[0])
    parser.add_argument("board", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="write here, not to BOARD")
    parser.add_argument("--net", help="the ground net (default: kicad-style-check's)")
    parser.add_argument("--via", type=via_size, help="DIAMETER/DRILL, mm (default: the board's)")
    parser.add_argument(
        "--at", type=point, action="append", default=[], help="X,Y: a via here first, mm"
    )
    for name, help_text in (
        ("pitch", "ring marks this far apart along the outline"),
        ("inset", "ring marks this far in from the outline"),
        ("grid", "the grid's pitch; 0 for none"),
        ("near", "a grid mark with a ground barrel this close needs none"),
        ("reach", "a via this far from its mark at most"),
    ):
        parser.add_argument(
            f"--{name}", type=float, default=getattr(defaults, name), help=f"{help_text}, mm"
        )
    args = parser.parse_args()
    try:
        # Imported here, to say what to do when the venv lacks it.
        import pcbnew
    except ImportError:
        sys.exit(
            "pcbnew is not importable: make the venv with"
            " uv venv --system-site-packages --python /usr/bin/python3"
        )
    expression = parse(args.board.read_text())
    net = args.net or plane_net(expression)
    if net is None:
        sys.exit(f"{args.board}: no copper zone with a net; name the ground net with --net")
    board = pcbnew.LoadBoard(str(args.board))
    if board.FindNet(net) is None:
        sys.exit(f"{args.board}: no net {net}")
    design = board.GetDesignSettings()
    via = args.via or Via(
        pcbnew.ToMM(design.GetCurrentViaSize()), pcbnew.ToMM(design.GetCurrentViaDrill())
    )
    b = Board(pcbnew, board, net, via, edge(expression))
    load_holes(b)
    b.grounds = [p for p, n in barrels(expression) if n == net]
    settings = Settings(
        pitch=args.pitch, inset=args.inset, grid=args.grid, near=args.near, reach=args.reach
    )
    out = args.output or args.board
    if not out.with_suffix(".kicad_pro").exists():
        sys.exit(f"{out}: no {out.with_suffix('.kicad_pro').name} beside it for DRC's rules")
    added, missed = stitch(expression, Placer(b), settings, args.at)
    for m in missed:
        print(f"{out}: no clear spot within reach of {where(m)}", file=sys.stderr)
    board.Save(str(out))
    rejected: list[Point] = []
    for _ in range(DRC_ROUNDS):
        flagged = list(
            dict.fromkeys(p for p in drc(out) if any(math.dist(p, a) <= SAME for a in added))
        )
        if not flagged:
            break
        for p in flagged:
            print(f"{out}: via at {where(p)} fails DRC, taken out", file=sys.stderr)
        rejected += flagged
        added = [a for a in added if all(math.dist(a, p) > SAME for p in flagged)]
        remove(pcbnew, out, flagged)
    else:
        sys.exit(f"{out}: DRC still fails new vias after {DRC_ROUNDS} rounds")
    print(
        f"{out}: {len(added)} {net} vias added; {len(missed)} marks without room;"
        f" {len(rejected)} taken out by DRC"
    )
    gaps = [g for g in stitching(parse(out.read_text()), net) if g.length > MAX_GAP]
    for g in gaps:
        print(
            f"{out}: {net} stitching gap {g.length:.1f} mm, over {MAX_GAP:g} mm,"
            f" from {where(g.start)} to {where(g.end)}"
        )
    sys.exit(1 if gaps else 0)
