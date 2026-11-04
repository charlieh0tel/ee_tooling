"""A KiCad board's routing against the house style (docs/kicad-style.md,
Board layout, Routing), read from the file, not through pcbnew.

Ground vias (`check_vias`, `check_pad_to_pad`): every SMD pad on a plane
net (`planes`) whose plane lies on another layer has a stub to a via
into the plane: one or more straight tracks, all N/S/E/W the same way,
the via's center within STUB_LENGTH of the pad.  An exposed or thermal pad
(`thermal_pads`) has BIG_PAD_VIAS or more, in it or on stubs; for any
other pad of BIG_PAD_AREA or more that is a note.  A via outside the part's
courtyard is fine.  Mounting hardware (MOUNTING_HARDWARE) is exempt: tied
to ground, it needs no via.  A pad traced to a pad of its own part that
reaches the plane (a connector's shell, a QFN's exposed pad) needs none
either.  No via serves two pads, and no trace on a plane net runs from
pad to pad but within a part.  A
decoupled supply pin is exempt, being fed through its capacitor, and so
is the run from it to its capacitors; a capacitor's supply pad needs no
via of its own (the supply check sees that it is fed).

Supply through the capacitor (`check_supply`): a decoupled supply pin
reaches no zone and no other part's pad except through one of its
capacitors' pads, and each capacitor reaches one.  The decoupling map is
placement.toml's (placement.py's Rules), or else `inferred_decoupling`.

Copper connects so: a track's end on another track of its layer and net
(within half that track's width); a track's copper over a pad or a via;
a via's center on a pad; an item on a zone's fill, tested against the
fill's polygons when the file has them.  An unfilled zone is taken to
reach all its net's copper on its layer inside its outline; the check
says so on stderr, once per zone.  Pads are their rotated rectangles,
whatever their shape (a custom pad: its anchor).

KiCad's file, as read here: lengths in mm, y down; an item names its net
`(net "NAME")` (older files `(net N "NAME")`: the name is last); a pad's
`(at X Y A)` is in its footprint's frame, placed by turning it A degrees
counterclockwise on screen about the footprint's `(at)`, the same on
either side (a bottom footprint's pads are written already mirrored);
the pad's own angle in the file includes the footprint's; the board's
`(layers ...)` table lists copper in stack order.
"""

import itertools
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from ee_tooling.kicad_style_board import (
    CAPACITOR,
    COPPER,
    NOTE,
    POWER_PIN_TYPES,
    STEP,
    UNCONNECTED,
    BoardError,
    Point,
    Segment,
    arc,
    at,
    distance,
    edge,
    layers,
    net_name,
    on_board,
    place,
    polyline,
    segments,
    unplace,
    where,
)
from ee_tooling.placement import Decoupling, Rules
from ee_tooling.sexpr import Expression, child, children, number

# A net is a plane on a layer when one of its copper zones there covers at
# least this share of the board: a pour over most of the board, not a
# local patch under a part.  An inner layer alone does not make a plane
# (inner layers carry signals and small pours too), and a two-layer
# board's bottom pour is a plane.
PLANE_COVERAGE = 0.5
# The board is sampled on a grid this many points across each way to
# measure a zone's coverage.
COVERAGE_SAMPLES = 40
# A stub's via center lies at most this far from its pad's copper, mm: a
# 0.6 mm via (KiCad's default) clearing its pad by 0.2 mm sits at 0.5 mm,
# so 1 mm leaves 0.5 mm to dodge a neighbor and no more.
STUB_LENGTH = 1.0
# A stub's tracks run within this of N/S/E/W, degrees.  KiCad keeps
# 0.1 um, so a true N/S/E/W track is within 0.01 degree; anything routed
# at 45 degrees is far outside.
AXIS_TOLERANCE = 1.0
# An exposed or thermal pad needs BIG_PAD_VIAS; any other pad this large,
# mm squared, should have them, a note: above any chip pad up to 1206
# (KiCad's 1206 pad is 1.15 x 1.8 mm, 2.1 mm squared), below a 3 x 3 mm
# QFN's 1.7 x 1.7 mm exposed pad.
BIG_PAD_AREA = 2.5
BIG_PAD_VIAS = 2
# An exposed or thermal pad: named so, or the largest pad of a part with
# at least THERMAL_PADS pads, THERMAL_RATIO times the next largest's area
# or more (a QFN's center pad, a SOT-223's tab).
THERMAL_NAMES = frozenset(("EP", "PAD", "TAB"))
THERMAL_PADS = 3
THERMAL_RATIO = 2.0
# Points on a pad's or via's edge are tested for fill this far inside it,
# mm, so a thermal spoke that meets it is found.
INSET = 0.01
# Points a pad is tested for fill at, along each side.
PAD_SAMPLES = 4
# Points a via is tested for fill at, round its ring.
VIA_SAMPLES = 8
# A fill's segments are filed in bands this tall, mm, to test points fast.
BAND = 0.5
# A point this close to copper is on it, mm (KiCad keeps 0.1 um).
ON_COPPER = 1e-3
# An inferred decoupling capacitor's supply pad lies at most this far from
# its pin, mm; past it a capacitor is bulk, not one pin's.
DECOUPLING_REACH = 5.0
# References inferred decoupling looks at: capacitors, and the packages
# (ICs) they decouple.
# Mounting hardware's reference: a mounting hole or standoff, tied to
# ground by its own copper, needs no via.
MOUNTING_HARDWARE = re.compile(r"^H\d+$")
PACKAGE = re.compile(r"^(?:U|IC)\d+$")
# A ground net's name, after any sheet path.
GROUND = re.compile(r"^(?:.*/)?(?:[ADP]?GND|VSS|0V)\w*$", re.I)
# The pin types (from the schematic) of a pin decoupling serves; a pin
# with no type may be one too.
ALL_COPPER = "*.Cu"


def net(item: Expression) -> str:
    """An item's net, "" if it has none."""
    return net_name(item) or ""


def meets_box(a: Point, b: Point, half: tuple[float, float]) -> bool:
    """Whether segment a-b meets the box |x| <= half[0], |y| <= half[1]
    (Liang-Barsky)."""
    low, high = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in (
        (-dx, a[0] + half[0]),
        (dx, half[0] - a[0]),
        (-dy, a[1] + half[1]),
        (dy, half[1] - a[1]),
    ):
        if p == 0:
            if q < 0:
                return False
        elif p < 0:
            low = max(low, q / p)
        else:
            high = min(high, q / p)
    return low <= high


@dataclass(frozen=True)
class Pad:
    reference: str
    number: str
    net: str  # "" for none
    position: Point
    angle: float  # degrees, counterclockwise on screen, footprint's included
    size: tuple[float, float]  # its copper's box, in the pad's own frame
    layers: frozenset[str]  # copper only
    through: bool  # plated through hole
    pintype: str  # the schematic pin's electrical type, "" if not given
    # The box's center in the pad's own frame: off the anchor when a custom
    # pad's primitives reach further one way.
    offset: Point = (0.0, 0.0)

    def local(self, p: Point) -> Point:
        """p in the pad's frame, from its copper box's center."""
        u, v = unplace(self.position, self.angle, p)
        return u - self.offset[0], v - self.offset[1]

    def __str__(self) -> str:
        return f"{self.reference} pad {self.number}"

    def gap(self, p: Point) -> float:
        """p's distance from the pad's copper; 0 on or in it."""
        u, v = self.local(p)
        return math.hypot(max(abs(u) - self.size[0] / 2, 0), max(abs(v) - self.size[1] / 2, 0))

    def contains(self, p: Point) -> bool:
        return self.gap(p) <= ON_COPPER

    def segment_gap(self, s: Segment) -> float:
        """s's distance from the pad's copper; 0 if it crosses it."""
        a, b = (self.local(p) for p in s)
        half = (self.size[0] / 2, self.size[1] / 2)
        if meets_box(a, b, half):
            return 0.0
        corners = [(x * half[0], y * half[1]) for x in (-1, 1) for y in (-1, 1)]
        return min(
            self.gap(s[0]), self.gap(s[1]), *(distance(corner, (a, b)) for corner in corners)
        )

    def same_copper(self, other: "Pad") -> bool:
        """Whether two pads are one: a footprint's pads on top of each other."""
        return self.reference == other.reference and (
            self.contains(other.position) or other.contains(self.position)
        )

    def area(self) -> float:
        return self.size[0] * self.size[1]

    def edge_points(self) -> list[Point]:
        """Points just inside its edge, and its center."""
        w, h = self.size[0] / 2 - INSET, self.size[1] / 2 - INSET
        corners = [(-w, -h), (w, -h), (w, h), (-w, h)]
        out = [(0.0, 0.0)]
        for a, b in polyline(corners, closed=True):
            out += [
                (a[0] + (b[0] - a[0]) * k / PAD_SAMPLES, a[1] + (b[1] - a[1]) * k / PAD_SAMPLES)
                for k in range(PAD_SAMPLES)
            ]
        return [
            place(self.position, self.angle, (u + self.offset[0], v + self.offset[1]))
            for u, v in out
        ]


@dataclass(frozen=True)
class Track:
    """A segment or an arc."""

    points: tuple[Point, ...]  # start to end; two for a segment, more for an arc
    width: float
    layer: str
    net: str

    @property
    def ends(self) -> tuple[Point, Point]:
        return self.points[0], self.points[-1]

    def pieces(self) -> list[Segment]:
        return polyline(list(self.points), closed=False)

    def straight(self) -> bool:
        return len(self.points) == 2

    def touches(self, p: Point, reach: float = 0.0) -> bool:
        """Whether p is on its copper, or within reach of it."""
        return any(distance(p, s) <= self.width / 2 + reach + ON_COPPER for s in self.pieces())

    def over(self, pad: Pad) -> bool:
        return any(pad.segment_gap(s) <= self.width / 2 + ON_COPPER for s in self.pieces())

    def samples(self) -> list[Point]:
        out = []
        for a, b in self.pieces():
            n = max(1, math.ceil(math.dist(a, b) / STEP))
            out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
        return [*out, self.points[-1]]


@dataclass(frozen=True)
class Via:
    position: Point
    size: float
    layers: frozenset[str]  # every copper layer it spans
    net: str

    def touches(self, p: Point) -> bool:
        return math.dist(p, self.position) <= self.size / 2 + ON_COPPER

    def ring(self) -> list[Point]:
        """Its center and points just inside its edge."""
        r = self.size / 2 - INSET
        x, y = self.position
        return [self.position] + [
            (
                x + r * math.cos(math.tau * k / VIA_SAMPLES),
                y + r * math.sin(math.tau * k / VIA_SAMPLES),
            )
            for k in range(VIA_SAMPLES)
        ]


class Fill:
    """One filled polygon of a zone; holes are joined to its outline, as
    KiCad writes them, so a point is in it when a ray crosses it an odd
    number of times."""

    def __init__(self, points: list[Point]) -> None:
        # Its segments by the bands of y each spans.
        self.bands: dict[int, list[Segment]] = defaultdict(list)
        for s in polyline(points, closed=True):
            low, high = sorted((s[0][1], s[1][1]))
            for band in range(math.floor(low / BAND), math.floor(high / BAND) + 1):
                self.bands[band].append(s)

    def contains(self, p: Point) -> bool:
        return on_board(p, self.bands.get(math.floor(p[1] / BAND), []))


@dataclass
class Zone:
    net: str
    layers: list[str]  # copper
    outline: list[Segment]
    fills: dict[str, list[Fill]]  # by layer; empty if not filled

    def __str__(self) -> str:
        return f"zone {self.net} on {', '.join(self.layers)}"

    def reaches(self, layer: str, points: Iterable[Point]) -> bool:
        """Whether copper at any of points on layer is on the zone."""
        if layer not in self.layers:
            return False
        if self.fills:
            return any(f.contains(p) for f in self.fills.get(layer, []) for p in points)
        return any(on_board(p, self.outline) for p in points)


@dataclass
class Copper:
    """A board's copper, by kind."""

    stack: list[str]  # copper layers, top to bottom
    pads: list[Pad] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    outline: list[Segment] = field(default_factory=list)  # Edge.Cuts


def copper_of(item: Expression, stack: list[str]) -> list[str]:
    named = layers(item)
    if ALL_COPPER in named:
        return list(stack)
    return [x for x in named if x in stack]


def text(item: Expression, keyword: str) -> str:
    c = child(item, keyword)
    return str(c[1]) if c and len(c) > 1 else ""


def copper_box(pad: Expression) -> tuple[tuple[float, float], Point]:
    """A pad's copper as a box in its own frame: its size, and its center.
    A custom pad's copper is its anchor and its primitives, each shape
    widened by half its stroke."""
    size = child(pad, "size")
    if size is None:
        raise BoardError(f"pad {pad[1]} without size")
    w, h = number(size[1]) / 2, number(size[2]) / 2
    xs, ys = [-w, w], [-h, h]
    for shape in (child(pad, "primitives") or [])[1:]:
        if not isinstance(shape, list):
            continue
        stroke = child(shape, "width")
        half = number(stroke[1]) / 2 if stroke else 0.0
        if shape[0] == "gr_circle":
            # Its extent exactly, not its chords'.
            center, end = (child(shape, k) for k in ("center", "end"))
            if center is None or end is None:
                raise BoardError(f"pad {pad[1]}: a gr_circle without center and end")
            cx, cy = number(center[1]), number(center[2])
            half += math.dist((cx, cy), (number(end[1]), number(end[2])))
            points: list[Point] = [(cx, cy)]
        else:
            points = [q for s in segments(shape) for q in s]
        for x, y in points:
            xs += [x - half, x + half]
            ys += [y - half, y + half]
    return (max(xs) - min(xs), max(ys) - min(ys)), (
        (max(xs) + min(xs)) / 2,
        (max(ys) + min(ys)) / 2,
    )


def read(board: Expression) -> Copper:
    """A board's copper, from its parsed file."""
    if not board or board[0] != "kicad_pcb":
        raise BoardError("not a kicad_pcb")
    table = child(board, "layers") or []
    stack = [str(x[1]) for x in table[1:] if isinstance(x, list) and str(x[1]).endswith(COPPER)]
    c = Copper(stack, outline=edge(board))
    for f in children(board, "footprint"):
        origin, angle = at(f)
        reference = next((str(p[2]) for p in children(f, "property") if p[1] == "Reference"), "?")
        for p in children(f, "pad"):
            position, pad_angle = at(p)
            box, offset = copper_box(p)
            c.pads.append(
                Pad(
                    reference,
                    str(p[1]),
                    net(p),
                    place(origin, angle, position),
                    pad_angle,
                    box,
                    frozenset(copper_of(p, stack)),
                    p[2] == "thru_hole",
                    text(p, "pintype"),
                    offset,
                )
            )
    for s in children(board, "segment") + children(board, "arc"):
        on = copper_of(s, stack)
        if not on:
            continue
        ends = [child(s, k) for k in ("start", "mid", "end")]
        points = [(number(e[1]), number(e[2])) for e in ends if e]
        if s[0] == "arc":
            if len(points) != 3:
                raise BoardError("an arc without start, mid and end")
            points = arc(*points)
        elif len(points) != 2:
            raise BoardError("a segment without start and end")
        width = child(s, "width")
        c.tracks.append(Track(tuple(points), number(width[1]) if width else 0.0, on[0], net(s)))
    for v in children(board, "via"):
        position, _ = at(v)
        named = [x for x in layers(v) if x in stack]
        if len(named) != 2:
            raise BoardError(f"a via at {where(position)} without two copper layers")
        top, bottom = sorted(stack.index(x) for x in named)
        size = child(v, "size")
        c.vias.append(
            Via(
                position,
                number(size[1]) if size else 0.0,
                frozenset(stack[top : bottom + 1]),
                net(v),
            )
        )
    for z in children(board, "zone"):
        on = copper_of(z, stack)
        if child(z, "keepout") or not on or not net(z):
            continue
        pts = children(child(child(z, "polygon") or [], "pts") or [], "xy")
        if not pts:
            raise BoardError(f"zone {net(z)} has no polygon")
        fills: dict[str, list[Fill]] = defaultdict(list)
        for fp in children(z, "filled_polygon"):
            xy = children(child(fp, "pts") or [], "xy")
            fills[text(fp, "layer") or on[0]].append(
                Fill([(number(q[1]), number(q[2])) for q in xy])
            )
        c.zones.append(
            Zone(
                net(z),
                on,
                polyline([(number(q[1]), number(q[2])) for q in pts], closed=True),
                dict(fills),
            )
        )
    return c


def connected(name: str) -> bool:
    return bool(name) and not name.startswith(UNCONNECTED)


def board_samples(c: Copper) -> list[Point]:
    """Points on a grid over the board, those on it."""
    if not c.outline:
        return []
    xs = [p[0] for s in c.outline for p in s]
    ys = [p[1] for s in c.outline for p in s]
    grid = [
        (
            min(xs) + (max(xs) - min(xs)) * (i + 0.5) / COVERAGE_SAMPLES,
            min(ys) + (max(ys) - min(ys)) * (j + 0.5) / COVERAGE_SAMPLES,
        )
        for i in range(COVERAGE_SAMPLES)
        for j in range(COVERAGE_SAMPLES)
    ]
    return [p for p in grid if on_board(p, c.outline)]


def planes(c: Copper, rules: Rules | None = None) -> dict[str, frozenset[str]]:
    """Each plane net's plane layers.  With rules naming poured nets, those
    are the plane nets, on every layer they have a zone; else a net is a
    plane on a layer where a zone of it covers PLANE_COVERAGE of the
    board."""
    found: dict[str, set[str]] = defaultdict(set)
    if rules and rules.poured:
        for z in c.zones:
            if z.net in rules.poured:
                found[z.net].update(z.layers)
    else:
        samples = board_samples(c)
        for z in c.zones:
            covered = sum(on_board(p, z.outline) for p in samples)
            if samples and covered / len(samples) >= PLANE_COVERAGE:
                found[z.net].update(z.layers)
    return {k: frozenset(v) for k, v in found.items()}


# A node of a net's copper: a kind ("pad", "via", "track", "zone") and its
# index in Copper's list of that kind.
Node = tuple[str, int]


class Net:
    """One net's copper and what connects to what."""

    def __init__(self, c: Copper, name: str) -> None:
        self.copper = c
        self.name = name
        self.pads = [i for i, p in enumerate(c.pads) if p.net == name]
        self.tracks = [i for i, t in enumerate(c.tracks) if t.net == name]
        self.vias = [i for i, v in enumerate(c.vias) if v.net == name]
        self.zones = [i for i, z in enumerate(c.zones) if z.net == name]
        # Each node's neighbors.
        self.links: dict[Node, set[Node]] = defaultdict(set)
        for i in self.tracks:
            t = c.tracks[i]
            # Its ends joining other tracks directly: those on no pad or
            # via, which the tracks join through instead.
            free = [
                e
                for e in t.ends
                if not any(t.layer in c.pads[j].layers and c.pads[j].contains(e) for j in self.pads)
                and not any(t.layer in c.vias[j].layers and c.vias[j].touches(e) for j in self.vias)
            ]
            for j in self.tracks:
                if j != i and c.tracks[j].layer == t.layer:
                    if any(c.tracks[j].touches(e) for e in free):
                        self.link(("track", i), ("track", j))
            for j in self.pads:
                if t.layer in c.pads[j].layers and t.over(c.pads[j]):
                    self.link(("track", i), ("pad", j))
            for j in self.vias:
                v = c.vias[j]
                if t.layer in v.layers and t.touches(v.position, v.size / 2):
                    self.link(("track", i), ("via", j))
        for i in self.vias:
            v = c.vias[i]
            for j in self.pads:
                if v.layers & c.pads[j].layers and c.pads[j].contains(v.position):
                    self.link(("via", i), ("pad", j))
        for k in self.zones:
            z = c.zones[k]
            for i in self.pads:
                p = c.pads[i]
                if any(z.reaches(x, p.edge_points()) for x in p.layers):
                    self.link(("zone", k), ("pad", i))
            for i in self.vias:
                v = c.vias[i]
                if any(z.reaches(x, v.ring()) for x in v.layers):
                    self.link(("zone", k), ("via", i))
            for i in self.tracks:
                t = c.tracks[i]
                if z.reaches(t.layer, t.samples()):
                    self.link(("zone", k), ("track", i))

    def link(self, a: Node, b: Node) -> None:
        self.links[a].add(b)
        self.links[b].add(a)

    def reach(self, start: Node, without: Iterable[int] = ()) -> Iterator[Node]:
        """The nodes copper joins to start, nearest first, not passing
        through the pads without: neither one nor a track that runs over it
        (its ends off it)."""
        blocked: set[Node] = set()
        for i in without:
            pad = self.copper.pads[i]
            blocked |= {("pad", i)} | {
                m
                for m in self.links[("pad", i)]
                if m[0] == "track"
                and not any(pad.contains(e) for e in self.copper.tracks[m[1]].ends)
            }
        seen = {start}
        queue = [start]
        while queue:
            n = queue.pop(0)
            yield n
            for m in sorted(self.links[n]):
                if m not in seen and m not in blocked:
                    seen.add(m)
                    queue.append(m)

    def via_on_plane(self, via: int, plane: frozenset[str], pad_layers: frozenset[str]) -> bool:
        """Whether a via reaches a plane layer other than the pad's."""
        v = self.copper.vias[via]
        return any(
            self.copper.zones[k].reaches(x, v.ring())
            for x in (v.layers & plane) - pad_layers
            for k in self.zones
        )

    def describe(self, n: Node) -> str:
        kind, i = n
        match kind:
            case "pad":
                return str(self.copper.pads[i])
            case "zone":
                return str(self.copper.zones[i])
            case "via":
                return f"via at {where(self.copper.vias[i].position)}"
            case _:
                return f"track at {where(self.copper.tracks[i].ends[0])}"


def axis(a: Point, b: Point) -> int | None:
    """The N/S/E/W bearing from a to b, degrees (0 east, y down), or None
    if it is more than AXIS_TOLERANCE off one."""
    bearing = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360
    nearest = round(bearing / 90) * 90 % 360
    off = abs((bearing - nearest + 180) % 360 - 180)
    return nearest if off <= AXIS_TOLERANCE else None


@dataclass(frozen=True)
class Stub:
    """The tracks from a pad to where they stop: a via, or else a branch,
    a pad or nothing."""

    tracks: tuple[int, ...]  # indices in Copper.tracks, the pad's first
    points: tuple[Point, ...]  # where the tracks join, the pad's end first
    via: int | None  # index in Copper.vias

    def straight(self, c: Copper) -> bool:
        """Whether its tracks are straight and all run one way, N/S/E/W."""
        if not all(c.tracks[t].straight() for t in self.tracks):
            return False
        bearings = {axis(a, b) for a, b in itertools.pairwise(self.points) if a != b}
        return len(bearings) == 1 and None not in bearings


def stubs(n: Net, pad: int) -> list[Stub]:
    """The tracks leaving a pad, each followed while it runs on alone."""
    c = n.copper
    p = c.pads[pad]
    out = []
    for i in sorted(m[1] for m in n.links[("pad", pad)] if m[0] == "track"):
        t = c.tracks[i]
        start, end = sorted(t.ends, key=p.gap)
        if p.contains(end):
            continue
        chain, points, here = [i], [start, end], end
        via = None
        while True:
            if any(c.pads[j].contains(here) for j in n.pads):
                break  # on to another pad: no stub
            vias = [j for j in n.vias if t.layer in c.vias[j].layers and c.vias[j].touches(here)]
            if vias:
                via = vias[0]
                break
            onward = [
                j
                for j in n.tracks
                if j != chain[-1] and c.tracks[j].layer == t.layer and c.tracks[j].touches(here)
            ]
            if len(onward) != 1 or onward[0] in chain:
                break
            ahead = c.tracks[onward[0]]
            here = max(ahead.ends, key=lambda e: math.dist(e, points[-1]))
            chain.append(onward[0])
            points.append(here)
        out.append(Stub(tuple(chain), tuple(points), via))
    return out


def pads_by_reference(c: Copper) -> dict[str, list[Pad]]:
    out: dict[str, list[Pad]] = defaultdict(list)
    for p in c.pads:
        out[p.reference].append(p)
    return out


def inferred_decoupling(c: Copper) -> list[Decoupling]:
    """Each capacitor (CAPACITOR) across a supply and a ground net
    (GROUND), as the decoupling of the nearest supply pin, within
    DECOUPLING_REACH of its pad, of a package (PACKAGE) with a pin on that
    ground too.  A pin whose type is given must be one of POWER_PIN_TYPES."""
    by_reference = pads_by_reference(c)
    out = []
    for reference, pads in sorted(by_reference.items()):
        on_nets = [p for p in pads if p.layers]
        if not CAPACITOR.match(reference) or len(on_nets) != 2:
            continue
        grounds = [p for p in on_nets if GROUND.match(p.net)]
        supplies = [p for p in on_nets if connected(p.net) and not GROUND.match(p.net)]
        if len(grounds) != 1 or len(supplies) != 1:
            continue
        ground, supply = grounds[0], supplies[0]
        pins = [
            (math.dist(p.position, supply.position), p.reference, p.number, p)
            for part, parts in by_reference.items()
            if PACKAGE.match(part) and any(q.net == ground.net for q in parts)
            for p in parts
            if p.net == supply.net and (p.pintype in POWER_PIN_TYPES or not p.pintype)
        ]
        if not pins:
            continue
        d, _, _, pin = min(pins, key=lambda x: x[:3])
        if d > DECOUPLING_REACH:
            continue
        pin_ground = next(q for q in by_reference[pin.reference] if q.net == ground.net)
        out.append(Decoupling(reference, pin.reference, pin.number, pin_ground.number))
    return out


@dataclass(frozen=True)
class Feed:
    """A decoupled pin and its capacitor's pad on the pin's net."""

    capacitor: str
    pin: int  # index in Copper.pads
    capacitor_pad: int  # index in Copper.pads


def feeds(c: Copper, decoupling: Iterable[Decoupling]) -> tuple[list[Feed], list[str]]:
    """decoupling's pins and capacitor pads, and problems finding them."""
    out, problems = [], []
    for d in decoupling:
        pin = next(
            (i for i, p in enumerate(c.pads) if p.reference == d.part and p.number == d.supply),
            None,
        )
        if pin is None:
            problems.append(f"{d.capacitor}: no {d.part} pad {d.supply} to decouple")
            continue
        supply = c.pads[pin].net
        pad = next(
            (i for i, p in enumerate(c.pads) if p.reference == d.capacitor and p.net == supply),
            None,
        )
        if pad is None:
            problems.append(f"{d.capacitor}: no pad on {c.pads[pin]}'s net {supply}")
            continue
        out.append(Feed(d.capacitor, pin, pad))
    return out, problems


def check_vias(
    c: Copper, plane: dict[str, frozenset[str]], exempt: set[int], fed: set[int]
) -> list[str]:
    """Rule one: each SMD pad on a plane net, but those in exempt, has
    stubs to vias into the plane, or for those in fed (a capacitor's
    supply pad) may have none, or is traced to a pad of its own part that
    reaches the plane (its plated-through shell, a pad with a via); no via
    serves two pads."""
    problems = []
    thermal = thermal_pads(c)
    for name in sorted(plane):
        n = Net(c, name)
        served: dict[int, list[int]] = defaultdict(list)  # via: the pads it serves
        via_less: list[int] = []  # pads with no via of their own
        reaches: set[int] = set()  # pads that reach the plane themselves
        for i in n.pads:
            p = c.pads[i]
            if p.through or i in exempt or not p.layers or not (plane[name] - p.layers):
                continue
            what = f"{p} ({name})"
            reaching = {
                j
                for j in n.vias
                if p.contains(c.vias[j].position) and n.via_on_plane(j, plane[name], p.layers)
            }
            for s in stubs(n, i):
                if s.via is None:
                    continue
                v = c.vias[s.via]
                at_via = f"via at {where(v.position)}"
                if not n.via_on_plane(s.via, plane[name], p.layers):
                    problems.append(f"{what}: {at_via} does not reach the plane")
                    continue
                reaching.add(s.via)
                if not s.straight(c):
                    problems.append(f"{NOTE}{what}: stub to the {at_via} not N/S/E/W")
                gap = p.gap(v.position)
                if gap > STUB_LENGTH:
                    problems.append(
                        f"{what}: {at_via} {gap:.2f} mm from the pad, over {STUB_LENGTH:g} mm"
                    )
            for j in reaching:
                if not any(p.same_copper(c.pads[k]) for k in served[j]):
                    served[j].append(i)
            if reaching:
                reaches.add(i)
            elif i not in fed:
                via_less.append(i)
            if reaching and len(reaching) < BIG_PAD_VIAS:
                size = f"{round(p.size[0], 2):g} x {round(p.size[1], 2):g} mm"
                if i in thermal:
                    problems.append(
                        f"{what}: {size} thermal pad with {len(reaching)} via, needs {BIG_PAD_VIAS}"
                    )
                elif p.area() >= BIG_PAD_AREA:
                    problems.append(
                        f"{NOTE}{what}: {size} pad with {len(reaching)} via, {BIG_PAD_VIAS} advised"
                    )
        for i in via_less:
            p = c.pads[i]
            own = [
                k
                for k in track_tied(n, i)
                if c.pads[k].reference == p.reference and (c.pads[k].through or k in reaches)
            ]
            if not own:
                problems.append(f"{p} ({name}) at {where(p.position)}: no via into the plane")
        for j, pads in sorted(served.items()):
            if len(pads) > 1:
                problems.append(
                    f"{name}: via at {where(c.vias[j].position)} serves"
                    f" {', '.join(str(c.pads[i]) for i in pads)}"
                )
    return problems


def thermal_pads(c: Copper) -> set[int]:
    """The exposed or thermal pads (THERMAL_NAMES, THERMAL_PADS,
    THERMAL_RATIO), by index."""
    out = {i for i, p in enumerate(c.pads) if p.number.upper() in THERMAL_NAMES}
    for pads in pads_by_reference(c).values():
        if len(pads) < THERMAL_PADS:
            continue
        largest, second = sorted(pads, key=lambda p: p.area(), reverse=True)[:2]
        if largest.area() >= THERMAL_RATIO * second.area():
            out.add(c.pads.index(largest))
    return out


def track_tied(n: Net, pad: int) -> set[int]:
    """The pads tracks alone join to pad: not through a via or another pad."""
    tied: set[int] = set()
    seen: set[Node] = set()
    todo: list[Node] = [m for m in n.links[("pad", pad)] if m[0] == "track"]
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        for m in n.links[node]:
            if m[0] == "track":
                todo.append(m)
            elif m[0] == "pad" and m[1] != pad:
                tied.add(m[1])
    return tied


def check_pad_to_pad(
    c: Copper, plane: dict[str, frozenset[str]], groups: list[set[int]]
) -> list[str]:
    """Rule one: no trace on a plane net runs from pad to pad, but within
    a group (a decoupled pin and its capacitors' pads) or a part (a QFN's
    pin to its exposed pad, a connector's pins to its shell)."""
    problems = []
    for name in sorted(plane):
        n = Net(c, name)
        seen: set[int] = set()
        for i in n.tracks:
            if i in seen:
                continue
            # The tracks joined to i on its layer, not through a pad or via.
            run: set[int] = set()
            todo = [i]
            while todo:
                t = todo.pop()
                if t not in run:
                    run.add(t)
                    todo += [m[1] for m in n.links[("track", t)] if m[0] == "track"]
            seen |= run
            pads = sorted({m[1] for t in run for m in n.links[("track", t)] if m[0] == "pad"})
            for k, a in enumerate(pads):
                for b in pads[k + 1 :]:
                    if c.pads[a].reference == c.pads[b].reference or any(
                        {a, b} <= g for g in groups
                    ):
                        continue
                    problems.append(
                        f"{name}: trace from {c.pads[a]} to {c.pads[b]} on {c.tracks[i].layer}"
                    )
    return problems


def source(n: Node, own: set[int]) -> bool:
    """Whether a node can feed a pin: a zone, or a pad not in own."""
    return n[0] == "zone" or (n[0] == "pad" and n[1] not in own)


def check_supply(c: Copper, fed: list[Feed]) -> list[str]:
    """Rule two: each decoupled pin reaches no zone and no other part's pad
    but through one of its capacitors' pads, and each capacitor reaches
    one."""
    problems = []
    by_pin: dict[int, set[int]] = defaultdict(set)  # pin: its capacitors' pads
    names: dict[int, list[str]] = defaultdict(list)  # pin: its capacitors, in order
    for f in fed:
        by_pin[f.pin].add(f.capacitor_pad)
        names[f.pin].append(f.capacitor)
    nets: dict[str, Net] = {}
    bypasses: dict[int, Node | None] = {}  # pin: a source it reaches past every capacitor
    for f in fed:
        pin = c.pads[f.pin]
        n = nets.setdefault(pin.net, Net(c, pin.net))
        what = f"{pin} ({pin.net})"
        cap: Node = ("pad", f.capacitor_pad)

        # Not sources: the pin's part's pads, and its capacitors'.
        own = {i for i in n.pads if c.pads[i].reference == pin.reference} | by_pin[f.pin]
        if cap not in n.reach(("pad", f.pin)):
            problems.append(f"{what}: no copper to {f.capacitor}")
            continue
        if f.pin not in bypasses:
            bypasses[f.pin] = next(
                (m for m in n.reach(("pad", f.pin), without=by_pin[f.pin]) if source(m, own)),
                None,
            )
            if bypass := bypasses[f.pin]:
                without = " or ".join(names[f.pin])
                problems.append(f"{what}: reaches {n.describe(bypass)} without {without}")
        if bypasses[f.pin]:
            continue
        if not any(source(m, own) for m in n.reach(cap)):
            problems.append(f"{what}: {f.capacitor} reaches no zone or other part")
    return problems


def check_routing(board: Expression, rules: Rules | None = None) -> list[str]:
    """The board's routing problems, each a line.  rules give the plane
    nets (poured) and the decoupling map; without them both are inferred."""
    c = read(board)
    if not c.tracks and not c.vias:
        print("routing: no tracks or vias, not checked", file=sys.stderr)
        return []
    for z in c.zones:
        if not z.fills:
            print(
                f"routing: {z} not filled: taken to reach its net's copper inside its outline",
                file=sys.stderr,
            )
    plane = planes(c, rules)
    decoupling = rules.decoupling if rules and rules.decoupling else inferred_decoupling(c)
    fed, problems = feeds(c, decoupling)
    groups: dict[int, set[int]] = defaultdict(set)  # pin: it and its capacitors' pads
    for f in fed:
        groups[f.pin] |= {f.pin, f.capacitor_pad}
    hardware = {i for i, p in enumerate(c.pads) if MOUNTING_HARDWARE.match(p.reference)}
    problems += check_vias(c, plane, set(groups) | hardware, {f.capacitor_pad for f in fed})
    problems += check_pad_to_pad(c, plane, list(groups.values()))
    problems += check_supply(c, fed)
    return list(dict.fromkeys(problems))
