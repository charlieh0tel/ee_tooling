"""Where a board's ground stitching vias go (docs/stitch.md); stitch_kicad.py
places them.

A mark is where a via is wanted; the via goes at the clear spot nearest
it.  Ring marks run along every closed Edge.Cuts outline (a board's, a
panel's boards', a slot's or cutout's), Settings.pitch apart, each
Settings.inset in from the outline, on the board.  Gap marks fill a gap
over MAX_GAP left between the ground's barrels along an outline once the
ring is placed, Settings.pitch / 2 apart.  Grid marks lie on a
Settings.grid lattice over the board, at least half a grid in from any
outline, where the ring does not reach.
"""

import itertools
import math
from collections.abc import Iterator
from dataclasses import dataclass

from ee_tooling.kicad_style_board import Point, Segment, distance, edge, on_board, polyline
from ee_tooling.kicad_style_finishing import EDGE_BAND, MAX_GAP, along, outlines
from ee_tooling.sexpr import Expression

# How many times a mark is pushed off a nearer outline, and how near
# inset counts as at it.
PUSHES = 4
TOLERANCE = 1e-6


@dataclass(frozen=True)
class Settings:
    """Where marks go and how far a via may move from one, mm."""

    pitch: float = MAX_GAP / 2  # between ring marks, along the outline
    inset: float = 1.3  # a ring mark's distance in from the outline
    grid: float = MAX_GAP  # the grid's pitch; 0 for no grid
    near: float = 4.0  # a grid mark with a ground barrel this close needs no via
    reach: float = 1.5  # how far a via may move from its mark
    step: float = 0.25  # the search's step, within reach


def ring_marks(board: Expression, settings: Settings) -> list[Point]:
    """Points along each closed outline, settings.pitch apart, each
    settings.inset in from it on the board's side."""
    segments = edge(board)
    marks = []
    for loop in outlines(segments)[0]:
        sides = list(polyline(loop, closed=True))
        perimeter = sum(math.dist(a, b) for a, b in sides)
        count = max(1, math.ceil(perimeter / settings.pitch))
        for k in range(count):
            mark = inset_point(sides, perimeter * k / count, settings.inset, segments)
            if mark is not None:
                marks.append(mark)
    return marks


def gap_marks(board: Expression, grounds: list[Point], settings: Settings) -> list[Point]:
    """Marks along each closed outline's gaps over MAX_GAP between the
    ground barrels within EDGE_BAND of it, settings.pitch / 2 apart."""
    segments = edge(board)
    marks = []
    for loop in outlines(segments)[0]:
        sides = list(polyline(loop, closed=True))
        perimeter = sum(math.dist(a, b) for a, b in sides)
        found = sorted(s for p in grounds for s, d in [along(p, loop)] if d <= EDGE_BAND)
        if not found:
            continue
        starts = [*found, found[0] + perimeter]
        for a, b in itertools.pairwise(starts):
            if b - a <= MAX_GAP:
                continue
            count = math.ceil((b - a) / (settings.pitch / 2))
            for k in range(1, count):
                at = (a + (b - a) * k / count) % perimeter
                mark = inset_point(sides, at, settings.inset, segments)
                if mark is not None:
                    marks.append(mark)
    return marks


def inset_point(
    sides: list[Segment], at: float, inset: float, segments: list[Segment]
) -> Point | None:
    """The point at distance `at` along a closed outline, moved inset
    along its normal to the board's side (the side on the board, the
    clearer if both are); None if neither is on the board."""
    for a, b in sides:
        length = math.dist(a, b)
        if at <= length or (a, b) == sides[-1]:
            t = at / length if length else 0.0
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            nx, ny = ((a[1] - b[1]) / length, (b[0] - a[0]) / length) if length else (0.0, 0.0)
            sides_on = [
                p
                for sign in (1, -1)
                if on_board(p := (x + sign * inset * nx, y + sign * inset * ny), segments)
            ]
            best = max(sides_on, key=lambda p: clearance(p, segments), default=None)
            return None if best is None else push(best, inset, segments)
        at -= length
    return None


def push(p: Point, inset: float, segments: list[Segment]) -> Point:
    """p moved away from any outline nearer than inset (a corner's other
    side), to inset from it, keeping it on the board."""
    for _ in range(PUSHES):
        near = min(segments, key=lambda s: distance(p, s))
        d = distance(p, near)
        if d >= inset * (1 - TOLERANCE):
            break
        (ax, ay), (bx, by) = near
        length = math.dist(*near)
        nx, ny = (ay - by) / length, (bx - ax) / length
        moved = [(p[0] + k * (inset - d) * nx, p[1] + k * (inset - d) * ny) for k in (1, -1)]
        p = max(
            (q for q in moved if on_board(q, segments)),
            key=lambda q: distance(q, near),
            default=p,
        )
    return p


def clearance(p: Point, segments: list[Segment]) -> float:
    """p's distance from the nearest outline."""
    return min((distance(p, s) for s in segments), default=math.inf)


def grid_marks(board: Expression, settings: Settings) -> list[Point]:
    """The grid's points on the board, half a grid or more in from any
    outline."""
    segments = edge(board)
    if settings.grid <= 0 or not segments:
        return []
    xs = [p[0] for s in segments for p in s]
    ys = [p[1] for s in segments for p in s]
    marks = []
    y = min(ys) + settings.grid
    while y < max(ys):
        x = min(xs) + settings.grid
        while x < max(xs):
            p = (x, y)
            if on_board(p, segments) and clearance(p, segments) >= settings.grid / 2:
                marks.append(p)
            x += settings.grid
        y += settings.grid
    return marks


def spots(mark: Point, settings: Settings) -> Iterator[Point]:
    """Candidate via centers within settings.reach of a mark, nearest
    first, on a settings.step lattice centered on it."""
    n = int(settings.reach / settings.step)
    offsets = [
        (i * settings.step, j * settings.step)
        for i in range(-n, n + 1)
        for j in range(-n, n + 1)
        if math.hypot(i, j) * settings.step <= settings.reach
    ]
    offsets.sort(key=lambda d: (math.hypot(*d), d))
    for dx, dy in offsets:
        yield (mark[0] + dx, mark[1] + dy)
