"""How two nets are routed relative to each other: on each layer, the
edge-to-edge gap from each segment of the first net to the nearest
segment of the second.  Says whether a differential pair (CAN, USB,
RS-485) is routed as a pair."""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ee_tooling.kicad_style_board import Point, Segment, distance, xy
from ee_tooling.kicad_style_routing import text
from ee_tooling.review_board import item_net, net_table
from ee_tooling.sexpr import Expression, child, children, number, parse

# Segments of the first net shorter than this are not sampled.
SEGMENT_MIN = 0.5  # mm
# Points sampled along a segment for the segment-to-segment distance.
SAMPLES = 10
# A gap at or under this counts as coupled.
COUPLED_GAP = 0.3  # mm


@dataclass(frozen=True)
class Track:
    """One segment of a net."""

    layer: str
    width: float
    segment: Segment

    @property
    def length(self) -> float:
        return math.dist(*self.segment)

    def point(self, t: float) -> Point:
        (x0, y0), (x1, y1) = self.segment
        return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)


@dataclass(frozen=True)
class LayerGaps:
    """One layer's gaps, length-weighted."""

    layer: str
    length: float  # of the first net's sampled segments
    gaps: tuple[float, ...]  # sorted, one per sampled segment
    coupled: float  # share of length with gap <= COUPLED_GAP


def track_distance(a: Track, b: Track) -> float:
    """Center-line distance between two segments, sampled."""
    ts = [i / SAMPLES for i in range(SAMPLES + 1)]
    return min(min(distance(a.point(t), b.segment), distance(b.point(t), a.segment)) for t in ts)


def tracks(board: Expression) -> dict[str, list[Track]]:
    table = net_table(board)
    found: defaultdict[str, list[Track]] = defaultdict(list)
    for s in children(board, "segment"):
        start, end = xy(s, "start"), xy(s, "end")
        width = child(s, "width")
        if start is None or end is None or width is None:
            continue
        found[item_net(s, table)].append(Track(text(s, "layer"), number(width[1]), (start, end)))
    return found


def gaps(first: list[Track], second: list[Track]) -> list[LayerGaps]:
    per_layer: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)
    for a in first:
        if a.length < SEGMENT_MIN:
            continue
        candidates = [b for b in second if b.layer == a.layer]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda b: track_distance(a, b))
        gap = track_distance(a, nearest) - (a.width + nearest.width) / 2
        per_layer[a.layer].append((gap, a.length))
    found = []
    for layer, items in sorted(per_layer.items()):
        items.sort()
        total = sum(length for _, length in items)
        coupled = sum(length for gap, length in items if gap <= COUPLED_GAP)
        found.append(LayerGaps(layer, total, tuple(round(g, 3) for g, _ in items), coupled / total))
    return found


def report(path: Path, pairs: list[tuple[str, str]]) -> str:
    by_net = tracks(parse(path.read_text(encoding="utf-8")))
    lines = [f"# Pair routing: {path.name}", ""]
    for first, second in pairs:
        lines.append(f"## {first} / {second}")
        for net in (first, second):
            found = by_net.get(net, [])
            if not found:
                lines.append(f"- {net}: no segments (is the net name right?)")
                continue
            on = dict(Counter(t.layer for t in found))
            widths = sorted({t.width for t in found})
            length = sum(t.length for t in found)
            lines.append(f"- {net}: length {length:.1f} mm, widths {widths}, layers {on}")
        for lg in gaps(by_net.get(first, []), by_net.get(second, [])):
            g = lg.gaps
            lines.append(
                f"- layer {lg.layer}: {lg.length:.1f} mm of {first}; edge gap to nearest {second}: "
                f"min {g[0]:.2f} median {g[len(g) // 2]:.2f} max {g[-1]:.2f} mm; "
                f"gap <= {COUPLED_GAP} mm over {100 * lg.coupled:.0f}% of length"
            )
        lines.append("")
    return "\n".join(lines)
