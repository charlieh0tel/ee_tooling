import math

import pytest

from ee_tooling.placement import (
    Box,
    Decoupling,
    Footprint,
    Layout,
    Pad,
    Point,
    Rules,
    capacitance,
    crosses,
    ratsnest,
    report,
    score,
    spanning_tree,
)
from ee_tooling.placement_render import render

OUTLINE = Box(0.0, 0.0, 20.0, 10.0)
PAD_HALF = 0.25  # mm


def pad(number: str, net: str, x: float, y: float) -> Pad:
    return Pad(number, net, (x, y), Box(x - PAD_HALF, y - PAD_HALF, x + PAD_HALF, y + PAD_HALF))


def part(
    reference: str, pads: list[Pad], courtyard: Box | None = None, value: str = "x"
) -> Footprint:
    x = sum(p.position[0] for p in pads) / len(pads)
    y = sum(p.position[1] for p in pads) / len(pads)
    return Footprint(reference, value, (x, y), tuple(pads), courtyard)


def test_spanning_tree_shortest() -> None:
    points: list[Point] = [(0, 0), (10, 0), (1, 0), (11, 0)]
    edges = spanning_tree(points)
    assert len(edges) == 3
    assert sum(math.dist(*e) for e in edges) == pytest.approx(11.0)


@pytest.mark.parametrize("points", [[], [(1.0, 1.0)]])
def test_spanning_tree_trivial(points: list[Point]) -> None:
    assert spanning_tree(points) == []


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (((0, 0), (2, 2)), ((0, 2), (2, 0)), True),
        (((0, 0), (1, 1)), ((0, 2), (2, 0)), False),  # touches at its end
        (((0, 0), (2, 2)), ((2, 2), (4, 0)), False),  # shares an end
        (((0, 0), (2, 0)), ((1, 0), (3, 0)), False),  # collinear
        (((0, 0), (2, 0)), ((0, 1), (2, 1)), False),  # parallel
    ],
)
def test_crosses(a: tuple[Point, Point], b: tuple[Point, Point], expected: bool) -> None:
    assert crosses(a, b) is expected
    assert crosses(b, a) is expected


@pytest.mark.parametrize(
    ("value", "farads"),
    [
        ("100n", 100e-9),
        ("100nF", 100e-9),
        ("4.7uF 25V X5R", 4.7e-6),
        ("4u7", 4.7e-6),
        ("10p", 10e-12),
        ("1", 1.0),
        ("22uF 50V X7R", 22e-6),
    ],
)
def test_capacitance(value: str, farads: float) -> None:
    assert capacitance(value) == pytest.approx(farads)


@pytest.mark.parametrize("value", ["DNP", "", "100k", "n100", "4u7k", "1.2.3", "10uH"])
def test_not_a_capacitance(value: str) -> None:
    assert capacitance(value) is None


def crossing_layout() -> Layout:
    """Nets A and B cross once; GND, poured, crosses both."""
    return Layout(
        (
            part("R1", [pad("1", "A", 0, 0), pad("2", "B", 0, 4)]),
            part("R2", [pad("1", "B", 4, 0), pad("2", "A", 4, 4)]),
            part("R3", [pad("1", "GND", 2, -1), pad("2", "unconnected-(R3-Pad2)", 9, 9)]),
            part("R4", [pad("1", "GND", 2, 5), pad("2", "", 8, 8)]),
        ),
        OUTLINE,
    )


def test_ratsnest_leaves_out_poured_and_unconnected() -> None:
    nets = ratsnest(crossing_layout(), frozenset({"GND"}))
    assert set(nets) == {"A", "B"}
    assert ratsnest(crossing_layout(), frozenset()).keys() == {"A", "B", "GND"}


def test_ratsnest_one_point_once() -> None:
    layout = Layout((part("J1", [pad("MP", "SHIELD", 1, 1), pad("MP", "SHIELD", 1, 1)]),), OUTLINE)
    assert ratsnest(layout, frozenset()) == {"SHIELD": []}


def test_score_crossings_and_length() -> None:
    s = score(crossing_layout(), Rules(frozenset({"GND"}), ()))
    assert s.crossings == (("A", "B"),)
    assert s.length == pytest.approx(2 * math.dist((0, 0), (4, 4)))
    with_ground = score(crossing_layout(), Rules(frozenset(), ()))
    assert len(with_ground.crossings) == 3


def test_overlaps_but_not_touching() -> None:
    layout = Layout(
        (
            part("U1", [pad("1", "A", 1, 1)], Box(0, 0, 2, 2)),
            part("C1", [pad("1", "A", 3, 1)], Box(2, 0, 4, 2)),  # touches U1
            part("C2", [pad("1", "A", 2, 3)], Box(1.5, 1.5, 3, 4)),  # overlaps U1 and C1
            part("R1", [pad("1", "A", 9, 9)]),  # no courtyard
        ),
        OUTLINE,
    )
    assert score(layout, Rules(frozenset(), ())).overlaps == (("U1", "C2"), ("C1", "C2"))


def decoupled(values: dict[str, str], positions: dict[str, float]) -> Layout:
    """U1's supply pin 1 at x=0 on VDD, ground pin 2 at x=0, y=5; each
    capacitor at its x, its pad 1 on VDD at y=0, pad 2 on GND at y=5."""
    caps = [
        part(r, [pad("1", "VDD", x, 0), pad("2", "GND", x, 5)], value=values[r])
        for r, x in positions.items()
    ]
    u1 = part("U1", [pad("1", "VDD", 0, 0), pad("2", "GND", 0, 5)])
    return Layout((u1, *caps), OUTLINE)


def rules(*capacitors: str) -> Rules:
    return Rules(frozenset({"GND"}), tuple(Decoupling(c, "U1", "1", "2") for c in capacitors))


def test_decoupling_distances() -> None:
    layout = decoupled({"C1": "100n"}, {"C1": 3.0})
    s = score(layout, rules("C1"))
    (d,) = s.decoupling
    assert (d.supply, d.ground) == pytest.approx((3.0, 3.0))
    assert s.problems == ()


def test_smallest_closest() -> None:
    layout = decoupled({"C1": "100n", "C2": "10u"}, {"C1": 1.0, "C2": 2.0})
    assert score(layout, rules("C1", "C2")).problems == ()


def test_smallest_not_closest() -> None:
    layout = decoupled({"C1": "100nF 50V X7R", "C2": "4u7"}, {"C1": 2.0, "C2": 1.0})
    (problem,) = score(layout, rules("C1", "C2")).problems
    assert problem == (
        "U1 pin 1: C1 (100nF 50V X7R) is farther than C2 (4u7); the smallest goes closest"
    )


def test_decoupling_value_not_a_capacitance() -> None:
    layout = decoupled({"C1": "100n", "C2": "DNP"}, {"C1": 1.0, "C2": 2.0})
    assert score(layout, rules("C1", "C2")).problems == ("C2: value 'DNP' is not a capacitance",)


def test_decoupling_bad_rules() -> None:
    layout = decoupled({"C1": "100n"}, {"C1": 1.0})
    s = score(
        layout,
        Rules(
            frozenset(),
            (
                Decoupling("C9", "U1", "1", "2"),
                Decoupling("C1", "U1", "7", "2"),
                Decoupling("C1", "U1", "2", "2"),
            ),
        ),
    )
    assert s.problems == (
        "no footprint C9",
        "U1 has no pad 7",
    )
    # C1 pad 2 is on GND, so to "supply" pin 2 it measures from that pad.
    assert [d.supply for d in s.decoupling] == [pytest.approx(1.0)]


def test_decoupling_capacitor_off_the_net() -> None:
    layout = Layout(
        (
            part("U1", [pad("1", "VDD", 0, 0), pad("2", "GND", 0, 5)]),
            part("C1", [pad("1", "VIO", 1, 0), pad("2", "GND", 1, 5)], value="100n"),
        ),
        OUTLINE,
    )
    assert score(layout, rules("C1")).problems == ("C1 has no pad on U1 pin 1's net VDD",)


def test_rules_load() -> None:
    r = Rules.load(
        'poured = ["GND", "VDD"]\n'
        '[[decoupling]]\ncapacitor = "C1"\npart = "U1"\nsupply = 14\nground = "7"\n'
    )
    assert r == Rules(frozenset({"GND", "VDD"}), (Decoupling("C1", "U1", "14", "7"),))
    assert Rules.load("") == Rules(frozenset(), ())


def test_report() -> None:
    lines = report(score(crossing_layout(), Rules(frozenset({"GND"}), ())))
    assert lines == ["crossings 1  ratsnest 11 mm  overlaps 0  problems 0", "cross A B 1"]
    assert all(line.isascii() for line in lines)


def test_render_size_and_ratsnest() -> None:
    image = render(crossing_layout(), Rules(frozenset({"GND"}), ()), scale=10)
    assert image.size == (220, 120)  # 20 x 10 mm and a 1 mm margin, at 10 px per mm
    # The middle of A's line, from R1 pad 1 (0, 0) to R2 pad 2 (4, 4), is drawn.
    assert image.getpixel((30, 30)) != (255, 255, 255)
