"""A KiCad 10 board against the house style's finishing rules
(docs/kicad-style.md, Board layout, Board finishing).

Edge stitching: along every closed Edge.Cuts outline (a board's, a
panel's boards', a slot's or cutout's), barrels on the ground plane's net
within EDGE_BAND of it, on the board, no further apart along it than
MAX_GAP.  A barrel is a through via or a footprint's plated through-hole
pad (a connector's shield leg, a mounting hole's vias).  The ground
plane's net is the net named GROUND with a copper zone, or else the net
whose copper zones cover the most area.  An
outline shorter than MAX_GAP round needs no via.

Mounting holes: a footprint whose name, after its library, starts with
"MountingHole" (KiCad's MountingHole_3.2mm_M3, ..._Pad, ..._Pad_TopBottom,
..._Pad_Via); a connector's pegs, and a footprint named for its mounting
holes (DSUB-9_..._MountingHolesOffset9.12mm), are not.  Its hole is its
pad with the largest drill: plated, with an annular ring of at least
MIN_RING (its widest concentric pad on any copper layer), and on a net:
tied to ground by its own copper, it needs no vias.  An unplated hole, or
one on no net, is reported for the owner to decide.
"""

import itertools
import math
import re
from collections import defaultdict
from dataclasses import dataclass

from ee_tooling.kicad_style_board import (
    COPPER,
    EDGE,
    UNCONNECTED,
    BoardError,
    Point,
    Segment,
    at,
    distance,
    edge,
    layers,
    net_name,
    on_board,
    place,
    polyline,
    where,
)
from ee_tooling.sexpr import Atom, Expression, child, children, number

# A barrel's center this far in from an outline, or nearer, stitches it, mm:
# a via of 0.6-0.8 mm beside the copper's 0.3-0.5 mm edge clearance sits
# 0.6-0.9 mm in; 2 mm allows a second row, and keeps the board's field
# vias out.
EDGE_BAND = 2.0
# The largest gap allowed between neighboring stitching vias along an
# outline, mm: lambda/20 in FR-4 (er 4.5) at 1.5 GHz, the third harmonic
# of USB 2.0's 480 Mb/s and well above a hobby board's clocks.
MAX_GAP = 10.0
# Outline ends this close together join, mm.
JOIN = 0.01
# Via kinds that do not go through the board, so cannot stitch it.
PARTIAL_VIAS = ("blind", "buried", "micro")
# The ground net's name, after any sheet path.
GROUND = re.compile(r"^(?:.*/)?GND$")
# Mounting holes: the footprint's name, after the library, starts so.
MOUNTING_HOLE = "MountingHole"
PLATED = "thru_hole"
# KiCad names a pad's net so when nothing else is on it: no net.
# The narrowest annular ring a mounting hole may have, mm: copper the
# fastener bears on, above any fab's 0.1-0.25 mm minimum for a plated
# hole's ring.
MIN_RING = 0.5
# A pad centered this close to the hole is part of its land, mm.
CONCENTRIC = 0.01


@dataclass(frozen=True)
class Gap:
    """The largest gap between stitching vias along one outline."""

    length: float  # mm, along the outline; its perimeter if no via
    start: Point  # the via that opens it, or a point on the outline
    end: Point  # the via that closes it
    vias: int  # stitching vias along the outline


def net(item: Expression) -> str | None:
    """An item's net, None if it has none or is alone on KiCad's
    unconnected-(...) net."""
    name = net_name(item)
    return None if name is None or name.startswith(UNCONNECTED) else name


def area(points: list[Point]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in polyline(points, closed=True))) / 2


def plane_net(board: Expression) -> str | None:
    """The ground net (GROUND) if it has a copper zone, or else the net
    whose copper zones cover the most area, summed over layers."""
    covered: dict[str, float] = defaultdict(float)
    for zone in children(board, "zone"):
        name = net(zone)
        copper = [x for x in layers(zone) if x.endswith(COPPER)]
        if not name or not copper or child(zone, "keepout"):
            continue
        for polygon in children(zone, "polygon"):
            pts = children(child(polygon, "pts") or [], "xy")
            covered[name] += len(copper) * area([(number(p[1]), number(p[2])) for p in pts])
    named = sorted(k for k in covered if GROUND.match(k))
    if named:
        return named[0]
    return max(covered, key=lambda k: covered[k]) if covered else None


def outlines(segments: list[Segment]) -> tuple[list[list[Point]], list[Point]]:
    """The segments chained into closed outlines, each its points in order;
    and where each chain that does not close ends."""
    remaining = [s for s in segments if math.dist(*s) > 0]
    closed, open_ends = [], []
    while remaining:
        chain = list(remaining.pop(0))
        while math.dist(chain[-1], chain[0]) > JOIN:
            n = next(
                (
                    i
                    for i, s in enumerate(remaining)
                    if min(math.dist(chain[-1], q) for q in s) <= JOIN
                ),
                None,
            )
            if n is None:
                open_ends.append(chain[-1])
                break
            a, b = remaining.pop(n)
            chain.append(b if math.dist(a, chain[-1]) <= JOIN else a)
        else:
            closed.append(chain[:-1])
    return closed, open_ends


def along(p: Point, loop: list[Point]) -> tuple[float, float]:
    """p's nearest point on a closed outline: its distance along the
    outline from the first point, and p's distance from it."""
    best, best_s, s = math.inf, 0.0, 0.0
    for a, b in polyline(loop, closed=True):
        length = math.dist(a, b)
        d = distance(p, (a, b))
        if d < best:
            t = (
                0.0
                if length == 0
                else ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / length**2
            )
            best, best_s = d, s + min(1.0, max(0.0, t)) * length
        s += length
    return best_s, best


def through(via: Expression) -> bool:
    return not any(Atom(kind) in via for kind in PARTIAL_VIAS) and not child(via, "type")


def position(item: Expression) -> Point:
    return at(item)[0]


def placed(footprint: Expression, local: Point) -> Point:
    """A footprint's point on the board."""
    return place(*at(footprint), local)


def barrels(board: Expression) -> list[tuple[Point, str | None]]:
    """Where the board's plated barrels are, with their nets: its through
    vias, and every footprint's plated through-hole pads."""
    out = [(position(v), net(v)) for v in children(board, "via") if through(v)]
    for footprint in children(board, "footprint"):
        out += [
            (placed(footprint, position(p)), net(p))
            for p in children(footprint, "pad")
            if str(p[2]) == PLATED
        ]
    return out


def stitching(board: Expression, ground: str) -> list[Gap]:
    """Each closed Edge.Cuts outline's largest gap between barrels on the
    ground net within EDGE_BAND of it, on the board."""
    outline = edge(board)
    vias = [p for p, n in barrels(board) if n == ground and on_board(p, outline)]
    gaps = []
    for loop in outlines(outline)[0]:
        perimeter = sum(math.dist(a, b) for a, b in polyline(loop, closed=True))
        near = sorted((s, p) for p in vias for s, d in [along(p, loop)] if d <= EDGE_BAND)
        if not near:
            gaps.append(Gap(perimeter, loop[0], loop[0], 0))
            continue
        spans = [(b[0] - a[0], a[1], b[1]) for a, b in itertools.pairwise(near)]
        spans.append((perimeter - near[-1][0] + near[0][0], near[-1][1], near[0][1]))
        length, start, end = max(spans)
        gaps.append(Gap(length, start, end, len(near)))
    return gaps


def drill(pad: Expression) -> float:
    """A pad's drill diameter, the smaller axis of an oval; 0 if none."""
    d = child(pad, "drill")
    sizes = [number(x) for x in (d or [])[1:] if isinstance(x, Atom) and x != "oval"]
    return min(sizes) if sizes else 0.0


def size(pad: Expression) -> float:
    """A pad's narrower dimension."""
    s = child(pad, "size")
    return min(number(x) for x in s[1:]) if s else 0.0


def mounting_hole(footprint: Expression) -> list[str]:
    """A mounting hole's problems: plated, ring, net."""
    reference = next(
        (str(p[2]) for p in children(footprint, "property") if p[1] == "Reference"), "?"
    )
    pads = children(footprint, "pad")
    what = f"mounting hole {reference} at {where(placed(footprint, (0, 0)))}"
    holes = [p for p in pads if drill(p) > 0]
    if not holes:
        return [f"{what}: no drilled pad"]
    hole = max(holes, key=drill)
    if str(hole[2]) != PLATED:
        return [f"{what}: not plated"]
    center = position(hole)
    radius = drill(hole) / 2
    land = [p for p in pads if math.dist(position(p), center) <= CONCENTRIC]
    ring = max(size(p) for p in land) / 2 - radius
    problems = []
    if ring < MIN_RING:
        problems.append(f"{what}: annular ring {ring:.2f} mm, under {MIN_RING:g} mm")
    if net(hole) is None:
        problems.append(f"{what}: on no net, not a ground tie; the owner decides")
    return problems


def check_finishing(board: Expression) -> list[str]:
    """The board's finishing problems, each a line."""
    if not board or board[0] != "kicad_pcb":
        raise BoardError("not a kicad_pcb")
    problems = []
    ground = plane_net(board)
    outline = edge(board)
    if ground is None:
        problems.append("no copper zone with a net: edge stitching not checked")
    elif outline:
        problems += [
            f"{EDGE} outline open at {where(p)}: not checked" for p in outlines(outline)[1]
        ]
        for gap in stitching(board, ground):
            if gap.length <= MAX_GAP:
                continue
            match gap.vias:
                case 0:
                    span = f"no via along the outline through {where(gap.start)}"
                case 1:
                    span = f"round the outline from its one via at {where(gap.start)}"
                case _:
                    span = f"between vias at {where(gap.start)} and {where(gap.end)}"
            problems.append(
                f"{ground} stitching gap {gap.length:.1f} mm, over {MAX_GAP:g} mm, {span}"
            )
    for footprint in children(board, "footprint"):
        if str(footprint[1]).rpartition(":")[2].startswith(MOUNTING_HOLE):
            problems += mounting_hole(footprint)
    return problems
