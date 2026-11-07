"""A KiCad board against the house style's zone rules (docs/kicad-style.md,
Board layout): pours are copper zones, never filled shapes on a copper
layer; every zone is locked.  A copper zone whose outline lies on the
board, rather than past its edge (or in a slot or cutout), is a note: the
whole-board fill is the usual rule, not a hard one.  An outline that faces
another net's zone on each of its layers, as a split plane's does, is not
on the board alone.
"""

import itertools
import math
import re
import sys

from ee_tooling.kicad_style_finding import ZONE_RULES, Checked, found
from ee_tooling.sexpr import Atom, Expression, child, children, number

Point = tuple[float, float]  # mm, KiCad's axes (y down)
Segment = tuple[Point, Point]

EDGE = "Edge.Cuts"
COPPER = ".Cu"  # a copper layer's name ends so: F.Cu, In1.Cu, B.Cu, *.Cu
FILLABLE = ("gr_poly", "gr_rect", "gr_circle")
EDGE_SHAPES = ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly")
# What a shape's (fill ...) says when it is not filled.
UNFILLED = ("no", "none")
# Arcs and circles are walked as chords no longer than this, and a zone's
# outline is tested for lying on the board at points this far apart, mm.
STEP = 0.25
# A point this close to the board's edge is on it, mm: a zone's outline
# along the edge is not past it.
ON_EDGE = 0.001
# KiCad's default zone clearance (pcbnew/zones.h, ZONE_CLEARANCE_MM), mm.
# A zone's outline inside another net's zone on its layer, or no further
# from it than this or either zone's own clearance, faces it, as a split
# plane's halves do.
SPLIT_GAP = 0.5


# KiCad names a pad's net so when nothing else is on it.
UNCONNECTED = "unconnected-"
# A schematic pin's types that carry power.
POWER_PIN_TYPES = frozenset(("power_in", "power_out"))
# A capacitor's reference.
CAPACITOR = re.compile(r"^C\d+$")


class BoardError(ValueError):
    """A board that parses but is not one KiCad could have written."""


def xy(expression: Expression, keyword: str) -> Point | None:
    c = child(expression, keyword)
    return (number(c[1]), number(c[2])) if c else None


def required(expression: Expression, *keywords: str) -> list[Point]:
    points = [xy(expression, k) for k in keywords]
    if any(p is None for p in points):
        raise BoardError(f"a {expression[0]} without {' and '.join(keywords)}")
    return [p for p in points if p is not None]


def polyline(points: list[Point], closed: bool) -> list[Segment]:
    ends = [*points, points[0]] if closed else points
    return list(itertools.pairwise(ends))


def circle(center: Point, radius: float, start: float, sweep: float) -> list[Point]:
    """Points along a circle from angle start through sweep, radians."""
    n = max(2, math.ceil(abs(sweep) * radius / STEP))
    return [
        (
            center[0] + radius * math.cos(start + sweep * k / n),
            center[1] + radius * math.sin(start + sweep * k / n),
        )
        for k in range(n + 1)
    ]


def arc(start: Point, middle: Point, end: Point) -> list[Point]:
    """Points along the arc from start through middle to end."""
    (ax, ay), (bx, by), (cx, cy) = start, middle, end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if d == 0:
        return [start, end]
    ux = (
        (ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)
    ) / d
    uy = (
        (ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)
    ) / d
    a0, am, a1 = (math.atan2(p[1] - uy, p[0] - ux) for p in (start, middle, end))
    sweep = (a1 - a0) % math.tau
    if (am - a0) % math.tau > sweep:  # the middle is on the other way round
        sweep -= math.tau
    return circle((ux, uy), math.dist((ux, uy), start), a0, sweep)


def segments(shape: Expression) -> list[Segment]:
    """A graphic shape's outline as straight segments."""
    match shape[0]:
        case "gr_line":
            a, b = required(shape, "start", "end")
            return [(a, b)]
        case "gr_arc":
            a, m, b = required(shape, "start", "mid", "end")
            return polyline(arc(a, m, b), closed=False)
        case "gr_rect":
            (x0, y0), (x1, y1) = required(shape, "start", "end")
            return polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)
        case "gr_circle":
            center, end = required(shape, "center", "end")
            return polyline(circle(center, math.dist(center, end), 0, math.tau), closed=True)
        case _:
            points = outline_points(child(shape, "pts") or [])
            if not points:
                raise BoardError(f"a {shape[0]} without points")
            return polyline(points, closed=True)


def outline_points(pts: Expression) -> list[Point]:
    """A (pts ...) list's points in order: each (xy X Y), and each
    (arc (start) (mid) (end)) as points along it; any other item is
    skipped, on stderr."""
    points: list[Point] = []
    for item in pts[1:]:
        if not isinstance(item, list) or not item:
            print(f"skipped: {item!r} in a polygon's points", file=sys.stderr)
            continue
        match item[0]:
            case "xy":
                points.append((number(item[1]), number(item[2])))
            case "arc":
                a, m, b = required(item, "start", "mid", "end")
                points.extend(arc(a, m, b))
            case _:
                print(f"skipped: ({item[0]} ...) in a polygon's points", file=sys.stderr)
    return points


def net_name(item: Expression) -> str | None:
    """An item's net: (net "NAME") in KiCad 10, (net N "NAME") before it;
    None if it has none, or only a number."""
    n = child(item, "net")
    if not n or len(n) < 2 or isinstance(n[-1], Atom) or not isinstance(n[-1], str):
        return None
    return n[-1]


def at(item: Expression) -> tuple[Point, float]:
    """An item's (at X Y [ANGLE]): its position, and its angle in degrees."""
    a = child(item, "at")
    if a is None:
        raise BoardError(f"a {item[0]} without at")
    return (number(a[1]), number(a[2])), number(a[3]) if len(a) > 3 else 0.0


def place(origin: Point, angle: float, p: Point) -> Point:
    """p, in a footprint's frame at origin turned angle degrees
    (counterclockwise on screen, y down), on the board.  A bottom-side
    footprint's file already mirrors its frame."""
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    return origin[0] + p[0] * c + p[1] * s, origin[1] - p[0] * s + p[1] * c


def unplace(origin: Point, angle: float, p: Point) -> Point:
    """place's inverse: a board point in a footprint's frame."""
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    dx, dy = p[0] - origin[0], p[1] - origin[1]
    return dx * c - dy * s, dx * s + dy * c


def layers(item: Expression) -> list[str]:
    one = child(item, "layer")
    if one:
        return [str(one[1])]
    return [str(x) for x in (child(item, "layers") or [])[1:]]


def edge(board: Expression) -> list[Segment]:
    """The board's Edge.Cuts outlines, as segments."""
    return [
        s
        for kind in EDGE_SHAPES
        for shape in children(board, kind)
        if EDGE in layers(shape)
        for s in segments(shape)
    ]


def nearest(p: Point, s: Segment) -> Point:
    """The point of s nearest p."""
    (ax, ay), (bx, by) = s
    length = math.dist(*s) ** 2
    t = 0.0 if length == 0 else ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / length
    t = min(1.0, max(0.0, t))
    return ax + t * (bx - ax), ay + t * (by - ay)


def distance(p: Point, s: Segment) -> float:
    return math.dist(p, nearest(p, s))


def on_board(p: Point, outline: list[Segment]) -> bool:
    """Whether p is on the board: on its edge, or inside its outlines (an
    odd number of them cross a ray from p, so slots and cutouts are off)."""
    if any(distance(p, s) <= ON_EDGE for s in outline):
        return True
    x, y = p
    crossings = 0
    for (x0, y0), (x1, y1) in outline:
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            crossings += 1
    return crossings % 2 == 1


def walk(outline: list[Point]) -> list[Point]:
    """Points along a closed outline, STEP apart."""
    out = []
    for a, b in polyline(outline, closed=True):
        n = max(1, math.ceil(math.dist(a, b) / STEP))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    return out


def locked(item: Expression) -> bool:
    if Atom("locked") in item:
        return True
    c = child(item, "locked")
    return c is not None and (len(c) == 1 or c[1] == "yes")


def where(p: Point) -> str:
    return f"({round(p[0], 2):g}, {round(p[1], 2):g})"


def zone_name(zone: Expression) -> str:
    """A zone as a finding names it: its net and layers."""
    net = child(zone, "net")
    return f"zone {net[1] if net and len(net) > 1 else 'with no net'} on {', '.join(layers(zone))}"


def zone_outline(zone: Expression) -> list[Point]:
    """A zone's outline, its (polygon)'s points, arcs included."""
    pts = outline_points(child(child(zone, "polygon") or [], "pts") or [])
    if not pts:
        raise BoardError(f"{zone_name(zone)} has no polygon")
    return pts


def zone_clearance(zone: Expression) -> float:
    """A zone's own clearance, (connect_pads ... (clearance X)), mm; 0 if
    it has none."""
    c = child(child(zone, "connect_pads") or [], "clearance")
    return number(c[1]) if c else 0.0


# A copper zone as a neighbor of another: its net, layers, outline as
# segments, and own clearance, mm.
Neighbor = tuple[str | None, list[str], list[Segment], float]


def faces(p: Point, zone: Expression, neighbors: list[Neighbor]) -> bool:
    """Whether p, on zone's outline, faces another net's zone on each of
    zone's copper layers: is inside it, or within the split gap of its
    outline."""
    net, gap = net_name(zone), max(SPLIT_GAP, zone_clearance(zone))
    copper = [x for x in layers(zone) if x.endswith(COPPER)]
    return bool(copper) and all(
        any(
            other != net
            and layer in on
            and (
                on_board(p, outline)
                or min(distance(p, s) for s in outline) <= max(gap, clearance) + ON_EDGE
            )
            for other, on, outline, clearance in neighbors
        )
        for layer in copper
    )


def check_board(board: Expression) -> Checked:
    """The board's zone findings."""
    if not board or board[0] != "kicad_pcb":
        raise BoardError("not a kicad_pcb")
    problems = []
    for kind in FILLABLE:
        for shape in children(board, kind):
            fill = child(shape, "fill")
            on_copper = [x for x in layers(shape) if x.endswith(COPPER)]
            if on_copper and fill and str(fill[1]) not in UNFILLED:
                first = segments(shape)[0][0]
                problems.append(
                    found(
                        "filled-shape",
                        f"filled {kind} on {', '.join(on_copper)} at {where(first)}:"
                        " pours are zones",
                        at=first,
                    )
                )
    outline = edge(board)
    if not outline:
        problems.append(found("no-outline", f"no {EDGE} outline"))
    zones = [(zone, zone_name(zone), zone_outline(zone)) for zone in children(board, "zone")]
    neighbors = [
        (net_name(zone), layers(zone), polyline(points, closed=True), zone_clearance(zone))
        for zone, _, points in zones
        if not child(zone, "keepout")
    ]
    for zone, what, points in zones:
        net, on = net_name(zone) or "", frozenset(layers(zone))
        if not locked(zone):
            problems.append(found("zone-unlocked", f"{what}: not locked", net=net, layers=on))
        if child(zone, "keepout") or not outline:
            continue
        inside = next(
            (p for p in walk(points) if on_board(p, outline) and not faces(p, zone, neighbors)),
            None,
        )
        if inside:
            problems.append(
                found(
                    "zone-on-board",
                    f"{what}: outline on the board at {where(inside)}, not past its edge",
                    note=True,
                    net=net,
                    layers=on,
                )
            )
    return Checked(problems, frozenset(ZONE_RULES))
