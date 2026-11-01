import pytest

from ee_tooling.kicad_style_board import Point, place, unplace
from ee_tooling.kicad_style_routing import (
    check_bends,
    check_routing,
    inferred_decoupling,
    planes,
    read,
)
from ee_tooling.placement import Decoupling, Rules
from ee_tooling.sexpr import parse

FOUR = '(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal))'
WIDTH, HEIGHT = 40, 30


def edge() -> str:
    corners = [(0, 0), (WIDTH, 0), (WIDTH, HEIGHT), (0, HEIGHT)]
    return " ".join(
        f'(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (layer "Edge.Cuts"))'
        for a, b in zip(corners, corners[1:] + corners[:1], strict=True)
    )


def polygon(x0: float, y0: float, x1: float, y1: float, keyword: str = "polygon") -> str:
    return f"({keyword} (pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1})))"


def zone(net: str, layer: str, extra: str = "", outline: str = "") -> str:
    """A zone past the board's edge, unless outline says otherwise."""
    return (
        f'(zone (net "{net}") (locked yes) (layer "{layer}")'
        f" {outline or polygon(-1, -1, WIDTH + 1, HEIGHT + 1)} {extra})"
    )


def pad(
    number: str,
    x: float,
    y: float,
    net: str,
    size: tuple[float, float] = (0.8, 0.8),
    kind: str = "smd",
    layers: str = '"F.Cu" "F.Mask"',
    angle: float = 0,
    pintype: str = "",
) -> str:
    """A pad; angle is its own, footprint's added by footprint."""
    typed = f' (pintype "{pintype}")' if pintype else ""
    return (
        f'(pad "{number}" {kind} roundrect (at {x} {y} ANGLE+{angle})'
        f' (size {size[0]} {size[1]}) (layers {layers}) (net "{net}"){typed})'
    )


def footprint(
    reference: str,
    x: float,
    y: float,
    *pads: str,
    angle: float = 0,
    side: str = "F.Cu",
    courtyard: tuple[float, float, float, float] | None = None,
) -> str:
    """A footprint; its pads' angles get its own, as KiCad writes them."""
    placed = []
    for p in pads:
        head, _, rest = p.partition("ANGLE+")
        own, _, tail = rest.partition(")")
        placed.append(f"{head}{angle + float(own):g}){tail}")
    yard = ""
    if courtyard:
        x0, y0, x1, y1 = courtyard
        yard = f'(fp_rect (start {x0} {y0}) (end {x1} {y1}) (layer "{side[0]}.CrtYd"))'
    at = f"(at {x} {y} {angle:g})" if angle else f"(at {x} {y})"
    return (
        f'(footprint "Lib:Part" (layer "{side}") {at}'
        f' (property "Reference" "{reference}") {" ".join(placed)} {yard})'
    )


def segment(a: Point, b: Point, net: str, layer: str = "F.Cu", width: float = 0.25) -> str:
    return (
        f"(segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (width {width})"
        f' (layer "{layer}") (net "{net}"))'
    )


def via(x: float, y: float, net: str, top: str = "F.Cu", bottom: str = "B.Cu") -> str:
    return f'(via (at {x} {y}) (size 0.6) (drill 0.3) (layers "{top}" "{bottom}") (net "{net}"))'


def board(*items: str) -> str:
    return f"(kicad_pcb (version 20260206) {FOUR} {edge()} {' '.join(items)})"


# Copper elsewhere, so that a board counts as routed.
ELSEWHERE = via(35, 25, "OTHER")


def check(*items: str, rules: Rules | None = None) -> list[str]:
    return [str(f) for f in check_routing(parse(board(*items, ELSEWHERE)), rules).findings]


GND_PLANE = zone("GND", "In1.Cu")
SUPPLY_PLANE = zone("+3V3", "In2.Cu")


def test_place() -> None:
    for angle in (0, 90, 180, 270, -90):
        p = (1.0, 0.5)
        assert unplace((3, 4), angle, place((3, 4), angle, p)) == pytest.approx(p)


@pytest.mark.parametrize(
    ("angle", "side", "expected"),
    [
        # KiCad turns a footprint counterclockwise on screen (y down): at
        # 90 degrees its +x points up.  Its pads' (at) in the file are in
        # its frame, a bottom footprint's already mirrored, so both sides
        # place the same way (checked against track ends on KiCad 10
        # boards).
        (0, "F.Cu", (11.0, 20.5)),
        (90, "F.Cu", (10.5, 19.0)),
        (180, "F.Cu", (9.0, 19.5)),
        (270, "F.Cu", (9.5, 21.0)),
        (0, "B.Cu", (11.0, 20.5)),
        (90, "B.Cu", (10.5, 19.0)),
        (180, "B.Cu", (9.0, 19.5)),
        (270, "B.Cu", (9.5, 21.0)),
    ],
)
def test_pad_position(angle: float, side: str, expected: Point) -> None:
    layer = f'"{side}"'
    c = read(
        parse(
            board(
                footprint("R1", 10, 20, pad("1", 1, 0.5, "A", layers=layer), angle=angle, side=side)
            )
        )
    )
    (p,) = c.pads
    assert p.position == pytest.approx(expected)
    assert p.angle == angle
    assert p.layers == {side}


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_pad_extent(angle: float) -> None:
    """A 2 x 1 mm pad turned with its footprint: long along x at 0 and
    180, along y at 90 and 270."""
    c = read(parse(board(footprint("R1", 10, 20, pad("1", 0, 0, "A", size=(2, 1)), angle=angle))))
    (p,) = c.pads
    along_x = angle % 180 == 0
    assert p.contains((10.9, 20)) is along_x
    assert p.contains((10, 20.9)) is not along_x


def test_through_pad_layers() -> None:
    c = read(
        parse(
            board(
                footprint(
                    "J1", 5, 5, pad("1", 0, 0, "GND", kind="thru_hole", layers='"*.Cu" "*.Mask"')
                )
            )
        )
    )
    assert c.pads[0].layers == {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu"}
    assert c.pads[0].through


def test_old_net_syntax() -> None:
    text = board(footprint("R1", 5, 5, pad("1", 0, 0, "X"))).replace('(net "X")', '(net 3 "GND")')
    assert read(parse(text)).pads[0].net == "GND"


def test_planes() -> None:
    small = zone("+5V", "F.Cu", outline=polygon(1, 1, 5, 5))
    c = read(parse(board(GND_PLANE, zone("GND", "B.Cu"), small)))
    assert planes(c) == {"GND": {"In1.Cu", "B.Cu"}}
    assert planes(c, Rules(frozenset({"+5V"}), ())) == {"+5V": {"F.Cu"}}


def test_planes_skip_keepouts() -> None:
    keepout = zone("GND", "In2.Cu", extra="(keepout (vias not_allowed))")
    assert planes(read(parse(board(keepout)))) == {}


# A GND pad at (10, 10), 0.8 mm square.
GND_PAD = footprint("R1", 10, 10, pad("1", 0, 0, "GND"))


def test_stub() -> None:
    stub = segment((10, 10), (10.9, 10), "GND")
    assert check(GND_PLANE, GND_PAD, stub, via(10.9, 10, "GND")) == []


@pytest.mark.parametrize(
    ("tracks", "end"),
    [
        # Two tracks the same way.
        (
            [segment((10, 10), (10.5, 10), "GND"), segment((10.5, 10), (10.9, 10), "GND")],
            (10.9, 10),
        ),
        # Off N by less than AXIS_TOLERANCE.
        ([segment((10, 10), (10.01, 9.1), "GND")], (10.01, 9.1)),
        # S and W.
        ([segment((10, 10), (10, 10.9), "GND")], (10, 10.9)),
        ([segment((10, 10), (9.1, 10), "GND")], (9.1, 10)),
    ],
)
def test_stub_straight(tracks: list[str], end: Point) -> None:
    assert check(GND_PLANE, GND_PAD, *tracks, via(*end, "GND")) == []


def test_no_via() -> None:
    assert check(GND_PLANE, GND_PAD) == ["R1 pad 1 (GND) at (10, 10): no via into the plane"]


# A custom pad: a 0.8 mm square anchor, and primitives (a polygon, a circle
# rounding its end) reaching 2 mm to its -x side, so its copper is
# 2.4 x 0.8 mm, centered 0.8 mm off the anchor.
CUSTOM = (
    '(pad "1" smd custom (at 0 0 ANGLE+0) (size 0.8 0.8) (layers "F.Cu" "F.Mask")'
    ' (net "GND") (options (clearance outline) (anchor rect))'
    " (primitives (gr_poly (pts (xy -2 -0.4) (xy 0 -0.4) (xy 0 0.4) (xy -1.6 0.4))"
    " (width 0) (fill yes)) (gr_circle (center -1.6 0) (end -2 0) (width 0) (fill yes))))"
)


@pytest.mark.parametrize("angle", [0, 90])
def test_custom_pad_copper(angle: float) -> None:
    """A via off the primitive's end is measured from the copper, not the
    anchor: 0.9 mm from the anchor's center, 0.5 mm from the copper."""
    pad_board = footprint("J1", 10, 10, CUSTOM, angle=angle)
    (pad,) = read(parse(board(pad_board))).pads
    assert pad.size == pytest.approx((2.4, 0.8))
    assert pad.area() == pytest.approx(1.92)
    # -x in the pad's frame: on screen, at 90 degrees, that is +y (down).
    end = (7.5, 10.0) if angle == 0 else (10.0, 12.5)
    corner = (8.0, 10.0) if angle == 0 else (10.0, 12.0)
    assert pad.gap(end) == pytest.approx(0.5)
    assert pad.contains(corner)
    stub = check(GND_PLANE, pad_board, segment(corner, end, "GND"), via(*end, "GND"))
    assert stub == []


SHELL = pad("SH", 3, 0, "GND", size=(1.2, 1.2), kind="thru_hole", layers='"*.Cu" "*.Mask"')


def test_pin_traced_to_its_own_shell() -> None:
    """A connector's GND pin traced to its plated-through shell: the shell
    is its way into the plane, and the trace stays within the part."""
    j1 = footprint("J1", 10, 10, pad("A1", 0, 0, "GND"), SHELL)
    assert check(GND_PLANE, j1, segment((10, 10), (13, 10), "GND")) == []


def test_pin_traced_to_its_own_pad_with_a_via() -> None:
    """A QFN's GND pin traced to its exposed pad, which has the vias."""
    u1 = footprint("U1", 10, 10, pad("2", 0, 0, "GND"), pad("25", 2, 0, "GND", size=(1, 1)))
    tie = segment((10, 10), (12, 10), "GND")
    assert check(GND_PLANE, u1, tie, via(12, 10, "GND")) == []


def test_pin_traced_to_another_parts_shell() -> None:
    j1 = footprint("J1", 10, 10, pad("A1", 0, 0, "GND"))
    j2 = footprint("J2", 13, 10, SHELL.replace("(at 3 0", "(at 0 0"))
    assert check(GND_PLANE, j1, j2, segment((10, 10), (13, 10), "GND")) == [
        "J1 pad A1 (GND) at (10, 10): no via into the plane",
        "GND: trace from J1 pad A1 to J2 pad SH on F.Cu",
    ]


def test_via_outside_the_courtyard() -> None:
    """A via outside the part's courtyard is fine, even where one would fit."""
    r1 = footprint("R1", 10, 10, pad("1", 0, 0, "GND", size=(1, 1)), courtyard=(-2, -0.6, 0.6, 0.6))
    assert check(GND_PLANE, r1, segment((10, 10), (11.4, 10), "GND"), via(11.4, 10, "GND")) == []


@pytest.mark.parametrize("reference", ["H1", "H12"])
def test_mounting_hardware_needs_no_via(reference: str) -> None:
    standoff = footprint(reference, 10, 10, pad("1", 0, 0, "GND", size=(2, 2)))
    assert check(GND_PLANE, standoff) == []
    part = footprint("U1", 10, 10, pad("1", 0, 0, "GND", size=(2, 2)))
    assert check(GND_PLANE, part) == ["U1 pad 1 (GND) at (10, 10): no via into the plane"]


def test_via_off_the_stub() -> None:
    """A via near the pad but joined by no track does not count."""
    assert check(GND_PLANE, GND_PAD, via(10.9, 10, "GND")) == [
        "R1 pad 1 (GND) at (10, 10): no via into the plane"
    ]


@pytest.mark.parametrize(
    ("tracks", "bend"),
    [
        ([segment((10, 10), (10.7, 10.7), "GND")], []),  # diagonal
        (
            [segment((10, 10), (10.7, 10), "GND"), segment((10.7, 10), (10.7, 10.7), "GND")],
            ["GND: right-angle bend at (10.7, 10) on F.Cu"],
        ),
    ],
)
def test_stub_not_on_an_axis(tracks: list[str], bend: list[str]) -> None:
    assert check(GND_PLANE, GND_PAD, *tracks, via(10.7, 10.7, "GND")) == [
        "note: R1 pad 1 (GND): stub to the via at (10.7, 10.7) not N/S/E/W",
        *bend,
    ]


def test_stub_arc() -> None:
    arc = (
        '(arc (start 10 10) (mid 10.6 10.25) (end 11.2 10) (width 0.25) (layer "F.Cu") (net "GND"))'
    )
    assert check(GND_PLANE, GND_PAD, arc, via(11.2, 10, "GND")) == [
        "note: R1 pad 1 (GND): stub to the via at (11.2, 10) not N/S/E/W"
    ]


def test_stub_too_long() -> None:
    stub = segment((10, 10), (11.5, 10), "GND")
    assert check(GND_PLANE, GND_PAD, stub, via(11.5, 10, "GND")) == [
        "R1 pad 1 (GND): via at (11.5, 10) 1.10 mm from the pad, over 1 mm"
    ]


def test_via_in_pad() -> None:
    assert check(GND_PLANE, GND_PAD, via(10, 10, "GND")) == []


def qfn(center: str = "9") -> str:
    """A QFN's 2 x 2 mm center pad, and three of its small signal pads."""
    signals = [pad(str(k), 2, k - 2, f"S{k}", size=(0.3, 0.6)) for k in range(1, 4)]
    return footprint("U1", 10, 10, pad(center, 0, 0, "GND", size=(2, 2)), *signals)


@pytest.mark.parametrize("center", ["9", "EP"])
def test_thermal_pad(center: str) -> None:
    assert check(GND_PLANE, qfn(center), via(10, 10, "GND")) == [
        f"U1 pad {center} (GND): 2 x 2 mm thermal pad with 1 via, needs 2"
    ]
    assert check(GND_PLANE, qfn(center), via(9.5, 10, "GND"), via(10.5, 10, "GND")) == []


def test_big_pad_a_guideline() -> None:
    """A big pad that is not a thermal one: two vias advised, a note."""
    big = footprint("C1", 10, 10, pad("2", 0, 0, "GND", size=(1, 2.7)), pad("1", 3, 0, "X"))
    assert check(GND_PLANE, big, via(10, 10, "GND")) == [
        "note: C1 pad 2 (GND): 1 x 2.7 mm pad with 1 via, 2 advised"
    ]


def test_via_short_of_the_plane() -> None:
    """A blind via to In1.Cu does not reach a plane on In2.Cu."""
    stub = segment((10, 10), (10.9, 10), "GND")
    assert check(zone("GND", "In2.Cu"), GND_PAD, stub, via(10.9, 10, "GND", bottom="In1.Cu")) == [
        "R1 pad 1 (GND): via at (10.9, 10) does not reach the plane",
        "R1 pad 1 (GND) at (10, 10): no via into the plane",
    ]


# A polygon's points with a half disc from (10, 0) round (15, 5) to (10, 10).
ROUNDED = "(pts (xy 0 0) (arc (start 10 0) (mid 15 5) (end 10 10)) (xy 0 10))"


def test_zone_arcs() -> None:
    """A zone's outline and fill read their arcs: a point in the half disc
    is on the fill, and the outline reaches x = 15."""
    filled = f'(filled_polygon (layer "In1.Cu") {ROUNDED})'
    c = read(parse(board(zone("GND", "In1.Cu", extra=filled, outline=f"(polygon {ROUNDED})"))))
    z = c.zones[0]
    assert z.reaches("In1.Cu", [(14, 5)])
    assert max(p[0] for s in z.outline for p in s) == pytest.approx(15, abs=0.01)


def test_via_off_the_fill() -> None:
    """A filled plane is tested against its fill: here its left half."""
    filled = zone("GND", "In1.Cu", extra=polygon(0, 0, 20, HEIGHT, "filled_polygon"))
    stub = segment((10, 10), (10.9, 10), "GND")
    assert check(filled, GND_PAD, stub, via(10.9, 10, "GND")) == []
    far = footprint("R2", 30, 10, pad("1", 0, 0, "GND"))
    assert check(filled, far, segment((30, 10), (30.9, 10), "GND"), via(30.9, 10, "GND")) == [
        "R2 pad 1 (GND): via at (30.9, 10) does not reach the plane",
        "R2 pad 1 (GND) at (30, 10): no via into the plane",
    ]


def test_unfilled_zone_noted(capsys: pytest.CaptureFixture[str]) -> None:
    check(GND_PLANE, GND_PAD, via(10, 10, "GND"))
    assert "zone GND on In1.Cu not filled" in capsys.readouterr().err


@pytest.mark.parametrize(
    "items",
    [
        # A through-hole pad is on the plane itself.
        [
            GND_PLANE,
            footprint("J1", 10, 10, pad("1", 0, 0, "GND", kind="thru_hole", layers='"*.Cu"')),
        ],
        # No plane but on the pad's own layer.
        [zone("GND", "F.Cu"), GND_PAD],
        # Not a plane net.
        [GND_PLANE, footprint("R1", 10, 10, pad("1", 0, 0, "SIGNAL"))],
    ],
)
def test_vias_not_needed(items: list[str]) -> None:
    assert check(*items) == []


def test_pad_to_pad() -> None:
    two = footprint("R2", 12, 10, pad("1", 0, 0, "GND"))
    tie = segment((10, 10), (12, 10), "GND")
    assert check(GND_PLANE, GND_PAD, two, tie, via(10, 10, "GND"), via(12, 10, "GND")) == [
        "GND: trace from R1 pad 1 to R2 pad 1 on F.Cu"
    ]


def test_pads_on_each_other() -> None:
    """A footprint's pads on top of each other are one pad."""
    stacked = footprint("J1", 10, 10, pad("A1", 0, 0, "GND"), pad("B12", 0, 0, "GND"))
    assert (
        check(GND_PLANE, stacked, segment((10, 10), (10.9, 10), "GND"), via(10.9, 10, "GND")) == []
    )


def test_shared_via() -> None:
    two = footprint("R2", 11.8, 10, pad("1", 0, 0, "GND"))
    stubs = [segment((10, 10), (10.9, 10), "GND"), segment((11.8, 10), (10.9, 10), "GND")]
    assert check(GND_PLANE, GND_PAD, two, *stubs, via(10.9, 10, "GND")) == [
        "GND: via at (10.9, 10) serves R1 pad 1, R2 pad 1"
    ]


def test_no_tracks(capsys: pytest.CaptureFixture[str]) -> None:
    assert check_routing(parse(board(GND_PLANE, GND_PAD))).findings == []
    assert "no tracks or vias, not checked" in capsys.readouterr().err


# A decoupled supply: U1 pin 1 (+3V3) and pin 2 (GND), C1 above it, both
# planes inner.  The ground pads have stubs north or south to vias.
U1 = footprint(
    "U1",
    20,
    15,
    pad("1", -2, 0, "+3V3", size=(0.6, 1.2), pintype="power_in"),
    pad("2", 2, 0, "GND", size=(0.6, 1.2), pintype="power_in"),
    pad("3", 0, 2, "+3V3", size=(0.6, 0.6), pintype="input"),
)
C1 = footprint("C1", 20, 12, pad("1", -0.75, 0, "+3V3"), pad("2", 0.75, 0, "GND"))
GROUNDS = [
    segment((20.75, 12), (20.75, 11), "GND"),
    via(20.75, 11, "GND"),
    segment((22, 15), (22, 16.2), "GND"),
    via(22, 16.2, "GND"),
]
FEED = [segment((19.25, 12), (19.25, 11), "+3V3"), via(19.25, 11, "+3V3")]
TO_PIN = [
    segment((19.25, 12), (18.5, 12), "+3V3"),
    segment((18.5, 12), (18, 12.5), "+3V3"),
    segment((18, 12.5), (18, 15), "+3V3"),
]
SUPPLY = [GND_PLANE, SUPPLY_PLANE, U1, C1, *GROUNDS]
EN = [segment((20, 17), (20, 18.2), "+3V3"), via(20, 18.2, "+3V3")]  # U1 pad 3's own


def test_inferred_decoupling() -> None:
    c = read(parse(board(*SUPPLY)))
    assert inferred_decoupling(c) == [Decoupling("C1", "U1", "1", "2")]


def test_inferred_decoupling_limits() -> None:
    """Not a capacitor whose other net is not ground, not a pin typed other
    than power, not beyond DECOUPLING_REACH."""
    signal = footprint(
        "U1", 20, 15, pad("1", -2, 0, "RESET", pintype="input"), pad("2", 2, 0, "GND")
    )
    to_reset = footprint("C1", 20, 12, pad("1", -0.75, 0, "RESET"), pad("2", 0.75, 0, "GND"))
    assert inferred_decoupling(read(parse(board(signal, to_reset)))) == []
    far = footprint("C1", 20, 2, pad("1", -0.75, 0, "+3V3"), pad("2", 0.75, 0, "GND"))
    assert inferred_decoupling(read(parse(board(U1, far)))) == []
    across = footprint("C1", 20, 12, pad("1", -0.75, 0, "+3V3"), pad("2", 0.75, 0, "+5V"))
    assert inferred_decoupling(read(parse(board(U1, across)))) == []


def test_supply_through_the_capacitor() -> None:
    assert check(*SUPPLY, *FEED, *TO_PIN, *EN) == []


def test_supply_junction_in_the_capacitor_pad() -> None:
    """The feed's and the pin's tracks meeting off center inside the
    capacitor's pad run through it, not straight from one to the other."""
    feed = [segment((19.4, 12.2), (19.4, 11), "+3V3"), via(19.4, 11, "+3V3")]
    to_pin = [
        segment((19.4, 12.2), (18.5, 12.2), "+3V3"),
        segment((18.5, 12.2), (18, 12.7), "+3V3"),
        segment((18, 12.7), (18, 15), "+3V3"),
    ]
    assert check(*SUPPLY, *feed, *to_pin, *EN) == []


def test_supply_over_the_capacitor_pad() -> None:
    """A track from the pin over the capacitor's pad to the via runs
    through the pad."""
    over = [segment((18, 15), (18, 12), "+3V3"), segment((18, 12), (20, 12), "+3V3")]
    feed = [segment((20, 12), (20, 11), "+3V3"), via(20, 11, "+3V3")]
    assert check(*SUPPLY, *over, *feed, *EN) == [
        "+3V3: right-angle bend at (18, 12) on F.Cu",
        "+3V3: right-angle bend at (20, 12) on F.Cu",
    ]


def test_supply_via_beside_the_capacitor_pad() -> None:
    """A via at the capacitor's pad's edge, fed from it, that the pin's
    track meets on the pad joins through the pad."""
    to_pin = [
        segment((18, 15), (19.1, 14), "+3V3", width=0.4),
        segment((19.1, 14), (19.1, 12.15), "+3V3", width=0.4),
    ]
    feed = [segment((19.25, 12), (18.65, 12), "+3V3", width=0.4), via(18.65, 12, "+3V3")]
    assert check(*SUPPLY, *to_pin, *feed, *EN) == []


def test_supply_via_on_the_pins_track() -> None:
    """A via the pin's track meets off the capacitor's pad, though the
    track ends on it, bypasses the pad."""
    to_pin = [
        segment((18, 15), (18, 12.7), "+3V3", width=0.4),
        segment((18, 12.7), (18.5, 12.2), "+3V3", width=0.4),
        segment((18.5, 12.2), (19, 12.2), "+3V3", width=0.4),
    ]
    feed = [segment((19.25, 12), (18.55, 12), "+3V3", width=0.4), via(18.55, 12, "+3V3")]
    assert check(*SUPPLY, *to_pin, *feed, *EN) == [
        "U1 pad 1 (+3V3): reaches zone +3V3 on In2.Cu without C1"
    ]


def test_supply_pin_has_its_own_via() -> None:
    own = [segment((18, 15), (18, 16.2), "+3V3"), via(18, 16.2, "+3V3")]
    assert check(*SUPPLY, *FEED, *TO_PIN, *own, *EN) == [
        "U1 pad 1 (+3V3): reaches zone +3V3 on In2.Cu without C1"
    ]


def test_supply_shares_the_pin() -> None:
    """Another part on the pin's side of the capacitor."""
    other = footprint("R5", 16, 15, pad("1", 0, 0, "+3V3"))
    tap = segment((18, 15), (16, 15), "+3V3")
    assert check(*SUPPLY, *FEED, *TO_PIN, *EN, other, tap) == [
        "R5 pad 1 (+3V3) at (16, 15): no via into the plane",
        "+3V3: trace from U1 pad 1 to R5 pad 1 on F.Cu",
        "U1 pad 1 (+3V3): reaches R5 pad 1 without C1",
    ]


# A second capacitor, C2, with its own feed: the pin reaches it only
# through C1 and the plane.
C2 = footprint("C2", 15, 10, pad("1", 0.75, 0, "+3V3"), pad("2", -0.75, 0, "GND"))
TO_C2 = [
    segment((15.75, 10), (15.75, 9), "+3V3"),
    via(15.75, 9, "+3V3"),
    segment((14.25, 10), (14.25, 9), "GND"),
    via(14.25, 9, "GND"),
]
BOTH = Rules(
    frozenset({"GND", "+3V3"}),
    (Decoupling("C1", "U1", "1", "2"), Decoupling("C2", "U1", "1", "2")),
)


def test_supply_through_either_capacitor() -> None:
    assert check(*SUPPLY, C2, *TO_C2, *FEED, *TO_PIN, *EN, rules=BOTH) == []


def test_supply_past_both_capacitors() -> None:
    own = [segment((18, 15), (18, 16.2), "+3V3"), via(18, 16.2, "+3V3")]
    assert check(*SUPPLY, C2, *TO_C2, *FEED, *TO_PIN, *own, *EN, rules=BOTH) == [
        "U1 pad 1 (+3V3): reaches zone +3V3 on In2.Cu without C1 or C2"
    ]


def test_supply_not_joined() -> None:
    assert check(*SUPPLY, *FEED, *EN) == ["U1 pad 1 (+3V3): no copper to C1"]


def test_capacitor_fed_by_nothing() -> None:
    assert check(*SUPPLY, *TO_PIN, *EN) == ["U1 pad 1 (+3V3): C1 reaches no zone or other part"]


TO_PIN_V3 = [
    segment((19.25, 12), (18.5, 12), "V3"),
    segment((18.5, 12), (18, 12.5), "V3"),
    segment((18, 12.5), (18, 15), "V3"),
]


def test_capacitor_on_the_parts_own_output() -> None:
    """A regulator's output, decoupled and joined to nothing else."""
    u1 = footprint(
        "U1",
        20,
        15,
        pad("1", -2, 0, "V3", size=(0.6, 1.2), pintype="power_out"),
        pad("2", 2, 0, "GND", size=(0.6, 1.2), pintype="power_in"),
    )
    c1 = footprint("C1", 20, 12, pad("1", -0.75, 0, "V3"), pad("2", 0.75, 0, "GND"))
    assert check(GND_PLANE, u1, c1, *GROUNDS, *TO_PIN_V3) == []


def test_rules_decoupling() -> None:
    """The rules' map, not inference: with it naming another pin, U1 pad 1
    is a plane net's pad like any other."""
    good = Rules(frozenset({"GND", "+3V3"}), (Decoupling("C1", "U1", "1", "2"),))
    assert check(*SUPPLY, *FEED, *TO_PIN, *EN, rules=good) == []
    bad = Rules(frozenset({"GND", "+3V3"}), (Decoupling("C1", "U1", "7", "2"),))
    assert check(*SUPPLY, *FEED, *TO_PIN, *EN, rules=bad) == [
        "C1: no U1 pad 7 to decouple",
        "U1 pad 1 (+3V3) at (18, 15): no via into the plane",
        "+3V3: trace from U1 pad 1 to C1 pad 1 on F.Cu",
    ]


def test_rules_capacitor_off_the_net() -> None:
    rules = Rules(frozenset(), (Decoupling("C1", "U1", "3", "2"),))
    c1 = footprint("C1", 20, 12, pad("1", -0.75, 0, "+5V"), pad("2", 0.75, 0, "GND"))
    assert check(U1, c1, rules=rules) == ["C1: no pad on U1 pad 3's net +3V3"]


def bends(*items: str) -> list[str]:
    return [str(f) for f in check_bends(read(parse(board(*items))))]


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        ((15, 10), ["SIG: right-angle bend at (15, 5) on F.Cu"]),  # 90
        ((15.08, 10), ["SIG: right-angle bend at (15, 5) on F.Cu"]),  # 90.9: within tolerance
        ((10, 10), ["SIG: 45-degree bend at (15, 5) on F.Cu"]),  # sharper
        ((10, 5.001), ["SIG: 0-degree bend at (15, 5) on F.Cu"]),  # doubling back
        ((20, 10), []),  # a 45-degree bend: 135 between the tracks
        ((15.2, 10), []),  # 92.3 degrees
        ((25, 5), []),  # straight on
    ],
)
def test_bend(end: Point, expected: list[str]) -> None:
    assert bends(segment((5, 5), (15, 5), "SIG"), segment((15, 5), end, "SIG")) == expected


def test_bend_reversed_track() -> None:
    """A track written end to start bends the same."""
    assert bends(segment((5, 5), (15, 5), "SIG"), segment((15, 10), (15, 5), "SIG")) == [
        "SIG: right-angle bend at (15, 5) on F.Cu"
    ]


def test_branches_are_not_bends() -> None:
    """A T: a track ending on another's run, or three ends at a point."""
    run = segment((5, 5), (25, 5), "SIG")
    assert bends(run, segment((15, 5), (15, 10), "SIG")) == []
    split = [segment((5, 5), (15, 5), "SIG"), segment((15, 5), (25, 5), "SIG")]
    assert bends(*split, segment((15, 5), (15, 10), "SIG")) == []
    # Two ends at a point on a third track's run.
    assert bends(run, segment((15, 10), (15, 5), "SIG"), segment((15, 5), (20, 0), "SIG")) == []


def test_bends_apart() -> None:
    """Another layer's or net's track at the point makes no branch and no bend."""
    corner = [segment((5, 5), (15, 5), "SIG"), segment((15, 5), (15, 10), "SIG")]
    other = segment((15, 5), (15, 0), "SIG", layer="B.Cu")
    assert bends(*corner, other) == ["SIG: right-angle bend at (15, 5) on F.Cu"]
    assert bends(segment((5, 5), (15, 5), "SIG"), segment((15, 5), (15, 10), "OTHER")) == []


def test_bend_on_pad_or_via() -> None:
    corner = [segment((5, 5), (15, 5), "SIG"), segment((15, 5), (15, 10), "SIG")]
    assert bends(*corner, via(15, 5, "SIG")) == []
    assert bends(*corner, footprint("R1", 15, 5, pad("1", 0, 0, "SIG"))) == []
    assert bends(*corner, via(15, 5, "SIG", top="In1.Cu", bottom="B.Cu")) == [
        "SIG: right-angle bend at (15, 5) on F.Cu"
    ]


def test_zero_length_track_is_no_bend() -> None:
    assert bends(segment((5, 5), (15, 5), "SIG"), segment((15, 5), (15, 5), "SIG")) == []
