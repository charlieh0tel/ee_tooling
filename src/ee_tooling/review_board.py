"""A board (.kicad_pcb) summarized for a reviewer: stackup, outline,
tracks, vias, zones, the routing of the power and the longest nets,
footprints, and silkscreen text.  A reviewer reads this, and greps the
board for the rest."""

import itertools
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ee_tooling.kicad_style_board import (
    Point,
    arc,
    at,
    edge,
    layers,
    net_name,
    outline_points,
    xy,
)
from ee_tooling.kicad_style_check import flag
from ee_tooling.kicad_style_netclass import POWER_NAME, SHEET
from ee_tooling.kicad_style_routing import text
from ee_tooling.review_netlist import refdes_key
from ee_tooling.sexpr import Atom, Expression, child, children, number, parse

COPPER_TYPES = frozenset({"signal", "power", "mixed"})
# Nets in the routing table whatever their length, by their own name (the
# last hierarchical part), besides kicad_style_netclass's POWER_NAME: an
# analog or digital supply (AVDD, DVCC), a rail written V3V3 or V1P8, an
# output, chassis, and a regulator's switch node or bootstrap (SW, LX,
# BST, numbered, nothing after).
POWER_NET = re.compile(
    r"^(?:[AD]V(?:DD|CC)[0-9A-Z_]*|V\d+[VP]\d*(?:_[0-9A-Z]+)?|VOUT\d*|CHASSIS|(?:SW|LX|BST)\d*)$",
    re.I,
)
# Nets other than the power nets in the routing table, longest first.
LONGEST_NETS = 25
# Footprints and references that are fiducials, test points and mounting holes.
FIDUCIAL = re.compile(r"fiducial", re.I)
TEST_POINT = re.compile(r"^TP\d")
MOUNTING = re.compile(r"MountingHole|^H\d")
# Setup fields reported.
SETUP_FIELDS = (
    "pad_to_mask_clearance",
    "solder_mask_min_width",
    "pad_to_paste_clearance",
    "pad_to_paste_clearance_ratio",
    "allow_soldermask_bridges_in_footprints",
)
STACKUP_FIELDS = ("copper_finish", "dielectric_constraints", "edge_connector", "castellated_pads")
SILK = frozenset({"F.SilkS", "B.SilkS"})


def power_net(net: str) -> bool:
    """Whether a net is power by its own name."""
    last = net.rsplit(SHEET, 1)[-1]
    return bool(POWER_NAME.match(last) or POWER_NET.match(last))


@dataclass
class Routing:
    """A net's tracks: count, length, width range and segments per layer."""

    segments: int = 0
    length: float = 0.0
    width_min: float = math.inf
    width_max: float = 0.0
    layers: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class Placed:
    """A footprint on the board."""

    ref: str
    value: str
    library: str
    side: str
    x: float
    y: float
    angle: float
    through_hole: bool
    dnp: bool


def copper_layers(board: Expression) -> list[tuple[str, str]]:
    """The board's copper layers, top to bottom, each with its type."""
    return [
        (str(layer[1]), str(layer[2]))
        for layer in child(board, "layers") or []
        if isinstance(layer, list) and len(layer) > 2 and layer[2] in COPPER_TYPES
    ]


def net_table(board: Expression) -> dict[str, str]:
    """Net number to name, from the board's net list."""
    return {str(n[1]): str(n[2]) for n in children(board, "net") if len(n) > 2}


def item_net(item: Expression, table: dict[str, str]) -> str:
    """An item's net name: named on the item, else looked up by number."""
    named = net_name(item)
    if named is not None:
        return named
    n = child(item, "net")
    return table.get(str(n[1]), str(n[1])) if n and len(n) > 1 else ""


def track_path(item: Expression) -> list[Point] | None:
    """A (segment ...) or (arc ...) track's center line as points, an
    arc's walked through its mid; None, said on stderr, without its
    points."""
    start, end, middle = xy(item, "start"), xy(item, "end"), xy(item, "mid")
    if start is None or end is None or (item[0] == "arc" and middle is None):
        print(f"skipped: ({item[0]} ...) without its start, end or mid", file=sys.stderr)
        return None
    return arc(start, middle, end) if middle is not None else [start, end]


def path_length(points: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in itertools.pairwise(points))


def outline(board: Expression) -> tuple[int, tuple[float, float, float, float]] | None:
    """Edge.Cuts segment count and bounding box (xmin, xmax, ymin, ymax)."""
    segments = edge(board)
    if not segments:
        return None
    xs = [p[0] for s in segments for p in s]
    ys = [p[1] for s in segments for p in s]
    return len(segments), (min(xs), max(xs), min(ys), max(ys))


def placed(footprint: Expression) -> Placed:
    props = {
        text(p, "name") or str(p[1]): str(p[2])
        for p in children(footprint, "property")
        if len(p) > 2
    }
    (x, y), angle = at(footprint)
    attributes = child(footprint, "attr") or []
    return Placed(
        ref=props.get("Reference", "?"),
        value=props.get("Value", ""),
        library=str(footprint[1]),
        side=text(footprint, "layer"),
        x=x,
        y=y,
        angle=angle,
        through_hole=any(len(p) > 2 and p[2] == "thru_hole" for p in children(footprint, "pad")),
        dnp=any(isinstance(a, Atom) and a == "dnp" for a in attributes),
    )


def on_silk(item: Expression) -> bool:
    """A footprint's text or property printed on a silkscreen: on a silk
    layer and not hidden, by (hide yes) or an (effects ... hide)."""
    hidden = flag(item, "hide") or flag(child(item, "effects") or [], "hide")
    return text(item, "layer") in SILK and not hidden


def summary(path: Path) -> str:
    board = parse(path.read_text(encoding="utf-8"))
    table = net_table(board)
    out: list[str] = [f"# Board summary: {path.name}"]
    out.append(f"\nthickness: {text(child(board, 'general') or [], 'thickness')} mm")

    out.append("\n## Copper layers\n")
    out += [f"- {name} ({kind})" for name, kind in copper_layers(board)]

    setup = child(board, "setup") or []
    stackup = child(setup, "stackup")
    out.append("\n## Stackup\n")
    if stackup is None:
        out.append("(no stackup section)")
    else:
        out.append("| layer | type | thickness mm | material | er | loss |")
        out.append("|---|---|---|---|---|---|")
        for layer in children(stackup, "layer"):
            out.append(
                f"| {layer[1]} | {text(layer, 'type')} | {text(layer, 'thickness')} "
                f"| {text(layer, 'material')} | {text(layer, 'epsilon_r')} "
                f"| {text(layer, 'loss_tangent')} |"
            )
        out += [f"- {k}: {text(stackup, k)}" for k in STACKUP_FIELDS if child(stackup, k)]
    out.append("\n## Setup\n")
    out += [f"- {k}: {text(setup, k)}" for k in SETUP_FIELDS if child(setup, k)]

    box = outline(board)
    if box is not None:
        n, (x0, x1, y0, y1) = box
        out.append(
            f"\n## Outline\n\nEdge.Cuts segments: {n}; bbox x {x0:.2f}..{x1:.2f} "
            f"({x1 - x0:.1f} mm), y {y0:.2f}..{y1:.2f} ({y1 - y0:.1f} mm)"
        )

    routing: defaultdict[str, Routing] = defaultdict(Routing)
    layer_length: defaultdict[str, float] = defaultdict(float)
    widths: Counter[float] = Counter()
    for s in children(board, "segment") + children(board, "arc"):
        center = track_path(s)
        if center is None:
            continue
        length = path_length(center)
        w = child(s, "width")
        width = number(w[1]) if w else 0.0
        name = text(s, "layer")
        r = routing[item_net(s, table)]
        r.segments += 1
        r.length += length
        r.width_min = min(r.width_min, width)
        r.width_max = max(r.width_max, width)
        r.layers[name] += 1
        layer_length[name] += length
        widths[width] += 1
    out.append("\n## Tracks\n")
    per_layer = ", ".join(f"{k} {v:.0f}" for k, v in sorted(layer_length.items()))
    out.append(
        f"segments: {sum(r.segments for r in routing.values())}; length per layer (mm): {per_layer}"
    )
    out.append(
        "width histogram (mm: count): "
        + ", ".join(f"{k:.3g}: {v}" for k, v in sorted(widths.items()))
    )

    via_sizes: Counter[tuple[str, str]] = Counter()
    via_nets: Counter[str] = Counter()
    via_kinds: Counter[str] = Counter()
    for v in children(board, "via"):
        via_sizes[(text(v, "size"), text(v, "drill"))] += 1
        via_nets[item_net(v, table)] += 1
        via_kinds[next((k for k in ("blind", "micro") if k in v), "through")] += 1
    out.append("\n## Vias\n")
    out.append(f"count: {sum(via_sizes.values())}; kinds: {dict(via_kinds)}")
    out.append(
        "size/drill histogram: "
        + ", ".join(f"{s}/{d}: {n}" for (s, d), n in sorted(via_sizes.items()))
    )

    out.append("\n## Zones\n")
    out.append(
        "| net | layers | priority | min_thickness | clearance | pad connect "
        "| thermal gap/bridge | filled | island removal | bbox |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    zone_layers: defaultdict[str, set[str]] = defaultdict(set)
    for z in children(board, "zone"):
        net = text(z, "net_name") or item_net(z, table)
        on = layers(z)
        connect = child(z, "connect_pads")
        mode = (
            str(connect[1])
            if connect and len(connect) > 1 and isinstance(connect[1], str)
            else "thermal"
        )
        fill = child(z, "fill") or []
        points = [
            p
            for polygon in children(z, "polygon")
            for p in outline_points(child(polygon, "pts") or [])
        ]
        bbox = ""
        if points:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            bbox = f"{min(xs):.0f}..{max(xs):.0f} x {min(ys):.0f}..{max(ys):.0f}"
        keepout = child(z, "keepout")
        if keepout is not None:
            rules = " ".join(
                f"{k[0]}={k[1]}" for k in keepout if isinstance(k, list) and len(k) > 1
            )
            priority = text(z, "priority") or 0
            out.append(
                f"| KEEPOUT {net} | {' '.join(on)} | {priority} | | | | | | {rules} | {bbox} |"
            )
            continue
        zone_layers[net].update(on)
        filled = "yes" if any(isinstance(a, Atom) and a == "yes" for a in fill) else "no"
        out.append(
            f"| {net} | {' '.join(on)} | {text(z, 'priority') or 0} | {text(z, 'min_thickness')} "
            f"| {text(connect or [], 'clearance')} | {mode} "
            f"| {text(fill, 'thermal_gap')}/{text(fill, 'thermal_bridge_width')} | {filled} "
            f"| {text(fill, 'island_removal_mode')} | {bbox} |"
        )

    out.append("\n## Routing of the power nets and the longest nets\n")
    out.append("| net | segments | length mm | width min/max | layers | vias | zone layers |")
    out.append("|---|---|---|---|---|---|---|")
    longest = 0
    for net, r in sorted(routing.items(), key=lambda kv: -kv[1].length):
        if not power_net(net):
            if longest == LONGEST_NETS:
                continue
            longest += 1
        per = " ".join(f"{k}:{v}" for k, v in sorted(r.layers.items()))
        out.append(
            f"| {net} | {r.segments} | {r.length:.0f} | {r.width_min:.3g}/{r.width_max:.3g} "
            f"| {per} | {via_nets.get(net, 0)} "
            f"| {' '.join(sorted(zone_layers.get(net, set())))} |"
        )
    unrouted = sorted(n for n in zone_layers if n not in routing)
    out.append(f"\nnets with zones but no tracks: {', '.join(unrouted)}")

    out.append("\n## Footprints\n")
    parts = sorted(
        (placed(fp) for fp in children(board, "footprint")), key=lambda p: refdes_key(p.ref)
    )
    sides = Counter(p.side for p in parts)
    specials = {
        "fiducials": [
            f"{p.ref} ({p.x:.1f},{p.y:.1f}) {p.side}"
            for p in parts
            if FIDUCIAL.search(p.library) or FIDUCIAL.search(p.value)
        ],
        "test points": [p.ref for p in parts if TEST_POINT.search(p.ref)],
        "mounting": [
            f"{p.ref} ({p.x:.1f},{p.y:.1f})"
            for p in parts
            if MOUNTING.search(p.library) or MOUNTING.search(p.ref)
        ],
        "DNP": [p.ref for p in parts if p.dnp],
    }
    through = sum(p.through_hole for p in parts)
    out.append(f"count: {len(parts)}; by side: {dict(sides)}; through-hole: {through}")
    out += [f"- {k} ({len(v)}): {', '.join(v) if v else 'none'}" for k, v in specials.items()]
    out.append("\n| ref | value | footprint | side | x | y | angle |")
    out.append("|---|---|---|---|---|---|---|")
    for p in parts:
        dnp = " (DNP)" if p.dnp else ""
        out.append(
            f"| {p.ref}{dnp} | {p.value} | {p.library} | {p.side} "
            f"| {p.x:.2f} | {p.y:.2f} | {p.angle:g} |"
        )

    out.append("\n## Silkscreen text\n")
    for fp in children(board, "footprint"):
        ref = placed(fp).ref
        for prop in children(fp, "property"):
            if on_silk(prop) and len(prop) > 2 and not isinstance(prop[2], Atom):
                out.append(f"- {prop[1]} {prop[2]!r} on {ref}")
        for t in children(fp, "fp_text"):
            if on_silk(t) and len(t) > 2 and not isinstance(t[2], Atom):
                out.append(f"- {t[2]!r} on {ref}")
    for t in children(board, "gr_text"):
        if text(t, "layer") in SILK and len(t) > 1 and not isinstance(t[1], Atom):
            (x, y), _ = at(t)
            out.append(f"- {text(t, 'layer')}: {t[1]!r} at ({x:.1f},{y:.1f})")
    return "\n".join(out) + "\n"
