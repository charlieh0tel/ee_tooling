import math

import pytest

from ee_tooling.kicad_style_board import BoardError, Point, edge
from ee_tooling.kicad_style_finishing import (
    EDGE_BAND,
    MAX_GAP,
    check_finishing,
    outlines,
    placed,
    plane_net,
    stitching,
)
from ee_tooling.sexpr import parse

# Synthetic boards: a 40 x 30 mm rectangle, a GND plane past its edge.
WIDTH, HEIGHT = 40.0, 30.0
INSET = 1.0  # stitching vias' distance in from the edge, mm
PITCH = 5.0  # stitching vias' spacing, mm


def line(a: Point, b: Point) -> str:
    return f'(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (layer "Edge.Cuts"))'


def rectangle(x0: float, y0: float, x1: float, y1: float) -> str:
    return f'(gr_rect (start {x0} {y0}) (end {x1} {y1}) (layer "Edge.Cuts"))'


def zone(name: str = "GND", layer: str = "B.Cu", extra: str = "", size: float = 2) -> str:
    return (
        f'(zone (net "{name}") (layer "{layer}") {extra}'
        f" (polygon (pts (xy -2 -2) (xy {WIDTH * size} -2)"
        f" (xy {WIDTH * size} {HEIGHT * size}) (xy -2 {HEIGHT * size}))))"
    )


def via(p: Point, name: str = "GND", kind: str = "") -> str:
    return (
        f'(via {kind} (at {p[0]} {p[1]}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
        f' (net "{name}"))'
    )


def ring_of_vias(x0: float, y0: float, x1: float, y1: float, skip: Point | None = None) -> str:
    """Vias INSET in from a rectangle's edge, PITCH apart, but none within
    PITCH of skip."""
    a, b, c, d = x0 + INSET, y0 + INSET, x1 - INSET, y1 - INSET
    points = [(a + k * PITCH, b) for k in range(int((c - a) / PITCH) + 1)]
    points += [(a + k * PITCH, d) for k in range(int((c - a) / PITCH) + 1)]
    points += [(a, b + k * PITCH) for k in range(1, int((d - b) / PITCH) + 1)]
    points += [(c, b + k * PITCH) for k in range(int((d - b) / PITCH) + 1)]
    points += [(c, d)]
    kept = [p for p in dict.fromkeys(points) if skip is None or math.dist(p, skip) > PITCH]
    return " ".join(via(p) for p in kept)


def pad(
    kind: str = "thru_hole",
    at: Point = (0, 0),
    size: float = 6.4,
    drill: float | None = 3.2,
    net: str | None = "GND",
    layers: str = '"*.Cu" "*.Mask"',
) -> str:
    hole = f"(drill {drill})" if drill else ""
    wire = f'(net "{net}")' if net else ""
    return (
        f'(pad "1" {kind} circle (at {at[0]} {at[1]}) (size {size} {size}) {hole}'
        f" (layers {layers}) {wire})"
    )


def footprint(name: str, at: str, *pads: str, reference: str = "H1") -> str:
    return (
        f'(footprint "{name}" (layer "F.Cu") (at {at})'
        f' (property "Reference" "{reference}" (at 0 0)) {" ".join(pads)})'
    )


# KiCad's MountingHole_3.2mm_M3_Pad_Via: a 6.4 mm land, eight vias at 2.4 mm.
VIA_PADS = [
    pad(at=(2.4 * math.cos(k * math.pi / 4), 2.4 * math.sin(k * math.pi / 4)), size=0.8, drill=0.5)
    for k in range(8)
]
PAD_VIA = "MountingHole:MountingHole_3.2mm_M3_Pad_Via"


def board(*items: str) -> str:
    return f"(kicad_pcb (version 20260206) {' '.join(items)})"


def problems(*items: str) -> list[str]:
    return check_finishing(parse(board(*items)))


EDGE = rectangle(0, 0, WIDTH, HEIGHT)
STITCHED = ring_of_vias(0, 0, WIDTH, HEIGHT)


def test_clean() -> None:
    assert problems(EDGE, zone(), STITCHED) == []


def test_not_a_board() -> None:
    with pytest.raises(BoardError):
        check_finishing(parse("(kicad_sch)"))


def test_plane_net_covers_the_most_area() -> None:
    small = zone("+3V3", "In2.Cu", size=1)
    keepout = zone("+5V", "F.Cu", extra="(keepout (tracks not_allowed))", size=9)
    silk = zone("+5V", "F.SilkS", size=9)
    assert plane_net(parse(board(small, zone(), keepout, silk))) == "GND"
    assert plane_net(parse(board(small))) == "+3V3"
    assert plane_net(parse(board(small, zone("+5V", "In1.Cu", size=9)))) == "+5V"
    assert plane_net(parse(board(EDGE))) is None


@pytest.mark.parametrize("ground", ["GND", "/power/GND"])
def test_plane_net_named_ground(ground: str) -> None:
    """GND, though a power pour covers more."""
    big = zone("+3V3", "In2.Cu", size=9)
    assert plane_net(parse(board(big, zone(ground, "In1.Cu", size=1)))) == ground


def test_no_plane() -> None:
    assert problems(EDGE, STITCHED) == ["no copper zone with a net: edge stitching not checked"]


def test_gap() -> None:
    hole = (20.0, HEIGHT - INSET)
    found = problems(EDGE, zone(), ring_of_vias(0, 0, WIDTH, HEIGHT, skip=hole))
    assert found == [
        f"GND stitching gap {3 * PITCH:.1f} mm, over {MAX_GAP:g} mm,"
        " between vias at (26, 29) and (11, 29)"
    ]


def test_gap_round_a_corner_is_along_the_outline() -> None:
    corner = (WIDTH - INSET, HEIGHT - INSET)
    (gap,) = stitching(parse(board(EDGE, ring_of_vias(0, 0, WIDTH, HEIGHT, skip=corner))), "GND")
    # (39, 21) round the corner to (31, 29): 9 mm, then 9 mm, along the edges.
    assert gap.length == pytest.approx(18.0)
    assert (gap.start, gap.end) == ((39.0, 21.0), (31.0, 29.0))


@pytest.mark.parametrize(
    "spoiler",
    [
        via((20, HEIGHT - INSET), "+3V3"),
        via((20, HEIGHT - INSET), kind="blind"),
        via((20, HEIGHT - EDGE_BAND - 0.1)),
        via((20, HEIGHT + INSET)),
    ],
    ids=["other net", "blind", "too far in", "off the board"],
)
def test_vias_that_do_not_stitch(spoiler: str) -> None:
    gaps = ring_of_vias(0, 0, WIDTH, HEIGHT, skip=(20.0, HEIGHT - INSET))
    assert len(problems(EDGE, zone(), gaps, spoiler)) == 1


def test_via_in_band_stitches() -> None:
    gaps = ring_of_vias(0, 0, WIDTH, HEIGHT, skip=(20.0, HEIGHT - INSET))
    fill = via((21, HEIGHT - EDGE_BAND))
    assert problems(EDGE, zone(), gaps, fill) == []


def test_ground_pad_stitches() -> None:
    gaps = ring_of_vias(0, 0, WIDTH, HEIGHT, skip=(20.0, HEIGHT - INSET))
    shield = footprint("Connector:SMA", "20 29 90", pad(size=1.5, drill=1), reference="J1")
    assert problems(EDGE, zone(), gaps, shield) == []


def test_no_vias() -> None:
    assert problems(EDGE, zone()) == [
        f"GND stitching gap {2 * (WIDTH + HEIGHT):.1f} mm, over {MAX_GAP:g} mm,"
        " no via along the outline through (0, 0)"
    ]


def test_one_via() -> None:
    assert problems(EDGE, zone(), via((20, INSET))) == [
        f"GND stitching gap {2 * (WIDTH + HEIGHT):.1f} mm, over {MAX_GAP:g} mm,"
        " round the outline from its one via at (20, 1)"
    ]


def test_arc_outline() -> None:
    """A round board, r 10: vias on a circle 1 mm in, 30 degrees apart
    (4.7 mm along the edge), one missing."""
    circle = '(gr_circle (center 20 15) (end 30 15) (layer "Edge.Cuts"))'
    vias = [
        via((20 + 9 * math.cos(math.radians(a)), 15 + 9 * math.sin(math.radians(a))))
        for a in range(0, 360, 30)
    ]
    assert problems(circle, zone(), *vias) == []
    (found,) = problems(circle, zone(), *vias[1:])
    assert found.startswith("GND stitching gap 10.5 mm, over")


def test_arcs_chain_into_one_outline() -> None:
    """A rounded rectangle: four lines and four arcs make one outline."""
    r = 3.0
    w, h = WIDTH, HEIGHT
    shapes = [
        line((r, 0), (w - r, 0)),
        line((w, r), (w, h - r)),
        line((w - r, h), (r, h)),
        line((0, h - r), (0, r)),
    ]
    s = r * (1 - math.sqrt(0.5))
    for (ax, ay), (mx, my), (bx, by) in [
        ((w - r, 0), (w - s, s), (w, r)),
        ((w, h - r), (w - s, h - s), (w - r, h)),
        ((r, h), (s, h - s), (0, h - r)),
        ((0, r), (s, s), (r, 0)),
    ]:
        shapes.append(
            f'(gr_arc (start {ax} {ay}) (mid {mx} {my}) (end {bx} {by}) (layer "Edge.Cuts"))'
        )
    found, open_ends = outlines(edge(parse(board(*shapes))))
    assert len(found) == 1 and open_ends == []
    assert problems(*shapes, zone(), STITCHED) == []


def test_open_outline() -> None:
    lines = [line((0, 0), (WIDTH, 0)), line((WIDTH, 0), (WIDTH, HEIGHT))]
    assert "Edge.Cuts outline open at (40, 30): not checked" in problems(*lines, zone())


def test_small_slot_needs_no_vias() -> None:
    slot = rectangle(18, 14, 22, 15)  # 10 mm round
    assert problems(EDGE, zone(), STITCHED, slot) == []


def test_large_cutout_needs_vias() -> None:
    cutout = rectangle(10, 10, 30, 20)
    (found,) = problems(EDGE, zone(), STITCHED, cutout)
    assert found.startswith("GND stitching gap 60.0 mm") and "no via" in found


def test_panel() -> None:
    """Two boards side by side: each outline stitched on its own."""
    right = rectangle(50, 0, 50 + WIDTH, HEIGHT)
    assert len(problems(EDGE, right, zone(size=3), STITCHED)) == 1
    both = ring_of_vias(50, 0, 50 + WIDTH, HEIGHT)
    assert problems(EDGE, right, zone(size=3), STITCHED, both) == []


def hole(*pads: str, name: str = PAD_VIA, at: str = "20 15") -> list[str]:
    return problems(EDGE, zone(), STITCHED, footprint(name, at, *pads))


def test_mounting_hole_clean() -> None:
    assert hole(pad(), *VIA_PADS) == []


def test_mounting_hole_top_only_land() -> None:
    """KiCad's _Pad_TopOnly: a 3.6 mm barrel and a 6.4 mm top land."""
    top = pad("connect", size=6.4, drill=None, layers='"F.Cu" "F.Mask"')
    assert hole(pad(size=3.6), top, *VIA_PADS) == []


def test_mounting_hole_not_plated() -> None:
    assert hole(
        pad("np_thru_hole", size=3.2, net=None), name="MountingHole:MountingHole_3.2mm_M3"
    ) == ["mounting hole H1 at (20, 15): not plated"]


def test_mounting_hole_ring() -> None:
    assert hole(pad(size=3.6), *VIA_PADS) == [
        "mounting hole H1 at (20, 15): annular ring 0.20 mm, under 0.5 mm"
    ]


@pytest.mark.parametrize("net", [None, "unconnected-(H1-Pad1)"])
def test_mounting_hole_no_net(net: str | None) -> None:
    assert hole(pad(net=net)) == [
        "mounting hole H1 at (20, 15): on no net, not a ground tie; the owner decides"
    ]


def test_mounting_hole_rotated() -> None:
    """The hole off the footprint's origin, rotated 90 degrees, lands
    where KiCad puts it."""
    fp = footprint(PAD_VIA, "10 10 90", pad(at=(5, 0)))
    assert placed(parse(fp), (5, 0)) == pytest.approx((10, 5))


def test_mounting_hole_ground_tie() -> None:
    """A hole on the ground net needs no vias round it."""
    assert hole(pad()) == []


@pytest.mark.parametrize(
    "name",
    [
        "Connector_Dsub:DSUB-9_Socket_Horizontal_P2.77x2.84mm_MountingHolesOffset9.12mm",
        "Connector_USB:USB_C_Receptacle",
        "MountingHole:ToolingHole_1.152mm",
    ],
)
def test_not_mounting_holes(name: str) -> None:
    assert hole(pad("np_thru_hole", size=3.2, net=None), name=name) == []
