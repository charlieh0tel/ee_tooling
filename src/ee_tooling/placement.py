"""Score a board's placement, on plain data (placement_kicad.py reads a
board into it).

Each net's ratsnest is its pads' minimum spanning tree, leaving out the
nets poured as planes.  The score counts crossings between different
nets' ratsnest lines and sums their length; it measures each decoupling
capacitor's distance to its part's supply and ground pins, and finds
overlapping courtyards and decoupling capacitors out of order (the
smallest value goes closest to the pin, docs/kicad-style.md).
"""

import itertools
import math
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass

Point = tuple[float, float]  # mm, in KiCad's axes (y down)
Edge = tuple[Point, Point]

# Pads KiCad leaves on no net, or on a net of their own, have no ratsnest.
UNCONNECTED = "unconnected-"

# SI prefixes in a capacitor's value, in farads.
PREFIXES = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0}
# A capacitor's value: "100n", "4.7uF", "10 uF 25V X5R"; and the form with
# the prefix for the decimal point, "4u7".  Either ends the first word.
CAPACITANCE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([pnum]?)F?(?![\w.])")
PREFIX_POINT = re.compile(r"^\s*(\d+)([pnum])(\d+)F?(?![\w.])")

# Below this, an orientation is collinear (mm squared).
COLLINEAR = 1e-9


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangle."""

    left: float
    top: float
    right: float
    bottom: float

    def overlaps(self, other: "Box") -> bool:
        """Whether they share area; touching edges do not."""
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )


@dataclass(frozen=True)
class Pad:
    number: str
    net: str  # "" for none
    position: Point
    box: Box


@dataclass(frozen=True)
class Footprint:
    reference: str
    value: str
    position: Point
    pads: tuple[Pad, ...]
    courtyard: Box | None  # on the footprint's own side; None if it has none

    def pad(self, number: str) -> Pad:
        try:
            return next(p for p in self.pads if p.number == number)
        except StopIteration:
            raise KeyError(f"{self.reference} has no pad {number}") from None


@dataclass(frozen=True)
class Layout:
    footprints: tuple[Footprint, ...]
    outline: Box  # the board's extent, from Edge.Cuts

    def footprint(self, reference: str) -> Footprint:
        try:
            return next(f for f in self.footprints if f.reference == reference)
        except StopIteration:
            raise KeyError(f"no footprint {reference}") from None


@dataclass(frozen=True)
class Decoupling:
    """A capacitor decoupling one supply pin of a part."""

    capacitor: str
    part: str
    supply: str  # the part's supply pin number
    ground: str  # the part's ground pin number


@dataclass(frozen=True)
class Rules:
    """A board's placement rules (placement.toml)."""

    poured: frozenset[str]  # nets poured as planes, left out of the ratsnest
    decoupling: tuple[Decoupling, ...]

    @classmethod
    def load(cls, text: str) -> "Rules":
        t = tomllib.loads(text)
        return cls(
            poured=frozenset(t.get("poured", ())),
            decoupling=tuple(
                Decoupling(d["capacitor"], d["part"], str(d["supply"]), str(d["ground"]))
                for d in t.get("decoupling", ())
            ),
        )


@dataclass(frozen=True)
class Distance:
    """A decoupling capacitor's nearest pads' distances to its part's pins, mm."""

    decoupling: Decoupling
    supply: float
    ground: float


@dataclass(frozen=True)
class Score:
    crossings: tuple[tuple[str, str], ...]  # one per crossing, the two nets sorted
    length: float  # the ratsnest's, mm
    decoupling: tuple[Distance, ...]
    overlaps: tuple[tuple[str, str], ...]  # courtyards, by reference
    problems: tuple[str, ...]  # rules broken, each a line


def spanning_tree(points: list[Point]) -> list[Edge]:
    """Prim's minimum spanning tree over distinct points."""
    if len(points) < 2:
        return []
    # Each point outside the tree: its distance to the tree, and that tree point.
    nearest = {p: (math.dist(p, points[0]), points[0]) for p in points[1:]}
    edges = []
    while nearest:
        p = min(nearest, key=lambda q: nearest[q][0])
        edges.append((nearest.pop(p)[1], p))
        for q, (d, _) in nearest.items():
            if (to_p := math.dist(q, p)) < d:
                nearest[q] = (to_p, p)
    return edges


def orientation(p: Point, q: Point, r: Point) -> int:
    """1 if p, q, r turn one way, -1 the other, 0 if collinear."""
    v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (v > COLLINEAR) - (v < -COLLINEAR)


def crosses(a: Edge, b: Edge) -> bool:
    """Whether two segments cross at a point inside both; sharing an end,
    touching or running collinear is not a crossing."""
    (p, q), (r, s) = a, b
    if len({p, q, r, s}) < 4:
        return False
    return (
        orientation(p, q, r) * orientation(p, q, s) < 0
        and orientation(r, s, p) * orientation(r, s, q) < 0
    )


def ratsnest(layout: Layout, poured: frozenset[str]) -> dict[str, list[Edge]]:
    """Each net's ratsnest, by net, leaving out the poured nets."""
    points: dict[str, list[Point]] = defaultdict(list)
    for f in layout.footprints:
        for pad in f.pads:
            if pad.net and not pad.net.startswith(UNCONNECTED) and pad.net not in poured:
                points[pad.net].append(pad.position)
    # Pads at one point (a footprint's stacked or repeated pads) count once.
    return {net: spanning_tree(list(dict.fromkeys(p))) for net, p in sorted(points.items())}


def capacitance(value: str) -> float | None:
    """A capacitor's value in farads, or None if it does not read as one."""
    if m := CAPACITANCE.match(value):
        number, prefix = m.groups()
        return float(number) * PREFIXES[prefix]
    if m := PREFIX_POINT.match(value):
        whole, prefix, fraction = m.groups()
        return float(f"{whole}.{fraction}") * PREFIXES[prefix]
    return None


def distances(layout: Layout, d: Decoupling) -> Distance:
    """d's capacitor pads' distances to its part's pins, each from the
    capacitor pad on the pin's net."""
    capacitor, part = layout.footprint(d.capacitor), layout.footprint(d.part)

    def to_pin(number: str) -> float:
        pin = part.pad(number)
        on_net = [p for p in capacitor.pads if p.net == pin.net]
        if not on_net:
            raise ValueError(f"{d.capacitor} has no pad on {d.part} pin {number}'s net {pin.net}")
        return min(math.dist(p.position, pin.position) for p in on_net)

    return Distance(d, to_pin(d.supply), to_pin(d.ground))


def order_problems(layout: Layout, measured: list[Distance]) -> list[str]:
    """Capacitors on one pin whose values do not grow with distance."""
    by_pin: dict[tuple[str, str], list[Distance]] = defaultdict(list)
    for m in measured:
        by_pin[m.decoupling.part, m.decoupling.supply].append(m)
    problems = []
    for (part, supply), on_pin in by_pin.items():
        nearest_first = sorted(on_pin, key=lambda m: m.supply)
        values = []
        for m in nearest_first:
            reference = m.decoupling.capacitor
            value = layout.footprint(reference).value
            farads = capacitance(value)
            if farads is None:
                problems.append(f"{reference}: value {value!r} is not a capacitance")
                break
            values.append((farads, reference, value))
        else:
            for (near, near_ref, near_value), (far, far_ref, far_value) in itertools.pairwise(
                values
            ):
                if far < near:
                    problems.append(
                        f"{part} pin {supply}: {far_ref} ({far_value}) is farther than"
                        f" {near_ref} ({near_value}); the smallest goes closest"
                    )
    return problems


def score(layout: Layout, rules: Rules) -> Score:
    lines = [(net, e) for net, edges in ratsnest(layout, rules.poured).items() for e in edges]
    crossings = tuple(
        (min(n1, n2), max(n1, n2))
        for (n1, e1), (n2, e2) in itertools.combinations(lines, 2)
        if n1 != n2 and crosses(e1, e2)
    )
    problems = []
    measured = []
    for d in rules.decoupling:
        try:
            measured.append(distances(layout, d))
        except (KeyError, ValueError) as e:
            problems.append(str(e.args[0]))
    problems += order_problems(layout, measured)
    yards = [(f.reference, f.courtyard) for f in layout.footprints if f.courtyard]
    overlaps = tuple(
        (r1, r2) for (r1, b1), (r2, b2) in itertools.combinations(yards, 2) if b1.overlaps(b2)
    )
    return Score(
        crossings=crossings,
        length=sum(math.dist(*e) for _, e in lines),
        decoupling=tuple(measured),
        overlaps=overlaps,
        problems=tuple(problems),
    )


def report(s: Score) -> list[str]:
    """The score as ASCII lines, the summary first."""
    out = [
        f"crossings {len(s.crossings)}  ratsnest {s.length:.0f} mm  overlaps {len(s.overlaps)}"
        f"  problems {len(s.problems)}"
    ]
    out += [
        f"decoupling {m.decoupling.capacitor}: {m.supply:.2f} mm to {m.decoupling.part}"
        f" pin {m.decoupling.supply}, {m.ground:.2f} mm to pin {m.decoupling.ground}"
        for m in s.decoupling
    ]
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for pair in s.crossings:
        counts[pair] += 1
    out += [f"cross {a} {b} {n}" for (a, b), n in sorted(counts.items())]
    out += [f"overlap {a} {b}" for a, b in s.overlaps]
    out += [f"problem: {p}" for p in s.problems]
    return out
