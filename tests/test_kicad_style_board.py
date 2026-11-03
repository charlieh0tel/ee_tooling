import math
import subprocess
import sys
from pathlib import Path

import pytest

from ee_tooling.kicad_style_board import (
    BoardError,
    Point,
    arc,
    check_board,
    on_board,
    outline_points,
    segments,
)
from ee_tooling.sexpr import parse


def line(a: Point, b: Point, layer: str = "Edge.Cuts") -> str:
    return f'(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (layer "{layer}"))'


def rectangle(x0: float, y0: float, x1: float, y1: float) -> str:
    """An Edge.Cuts outline as four lines."""
    return " ".join(
        line(a, b)
        for a, b in [
            ((x0, y0), (x1, y0)),
            ((x1, y0), (x1, y1)),
            ((x1, y1), (x0, y1)),
            ((x0, y1), (x0, y0)),
        ]
    )


def zone(
    x0: float, y0: float, x1: float, y1: float, locked: str = "(locked yes)", extra: str = ""
) -> str:
    return (
        f'(zone (net "GND") {locked} (layer "F.Cu") {extra}'
        f" (polygon (pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1}))))"
    )


def board(*items: str) -> str:
    return f"(kicad_pcb (version 20260206) {' '.join(items)})"


def problems(*items: str) -> list[str]:
    return check_board(parse(board(*items)))


EDGE = rectangle(0, 0, 50, 30)


def test_clean() -> None:
    assert problems(EDGE, zone(-2, -2, 52, 32)) == []


@pytest.mark.parametrize(
    ("shape", "flagged"),
    [
        ('(gr_poly (pts (xy 1 1) (xy 5 1) (xy 5 5)) (fill yes) (layer "F.Cu"))', True),
        ('(gr_rect (start 1 1) (end 5 5) (fill solid) (layer "In1.Cu"))', True),
        ('(gr_circle (center 9 9) (end 10 9) (fill yes) (layer "B.Cu"))', True),
        ('(gr_poly (pts (xy 1 1) (xy 5 1) (xy 5 5)) (fill no) (layer "F.Cu"))', False),
        ('(gr_rect (start 1 1) (end 5 5) (fill none) (layer "F.Cu"))', False),
        ('(gr_poly (pts (xy 1 1) (xy 5 1) (xy 5 5)) (fill yes) (layer "F.SilkS"))', False),
    ],
)
def test_filled_shapes(shape: str, flagged: bool) -> None:
    found = [p for p in problems(EDGE, shape) if "pours are zones" in p]
    assert bool(found) is flagged


def test_filled_shape_message() -> None:
    shape = '(gr_poly (pts (xy 1 1) (xy 5 1) (xy 5 5)) (fill yes) (layer "F.Cu"))'
    assert problems(EDGE, shape) == ["filled gr_poly on F.Cu at (1, 1): pours are zones"]


def test_unlocked_zone() -> None:
    assert problems(EDGE, zone(-2, -2, 52, 32, locked="")) == ["zone GND on F.Cu: not locked"]
    assert problems(EDGE, zone(-2, -2, 52, 32, locked="locked")) == []
    assert problems(EDGE, zone(-2, -2, 52, 32, locked="(locked no)")) == [
        "zone GND on F.Cu: not locked"
    ]


@pytest.mark.parametrize(
    "outline",
    [
        (5, -2, 52, 32),  # stops inside on the left
        (-2, -2, 52, 29),  # stops inside at the bottom
        (0, 0, 50, 30),  # on the edge
        (10, 10, 20, 20),  # an island
    ],
)
def test_zone_not_past_the_edge(outline: tuple[float, float, float, float]) -> None:
    (found,) = problems(EDGE, zone(*outline))
    assert found.startswith("note: zone GND on F.Cu: outline on the board at ")


def test_zone_edge_in_a_slot() -> None:
    """Two boards with a 2 mm slot between them, a zone each."""
    edges = (rectangle(0, 0, 20, 30), rectangle(22, 0, 50, 30))
    assert problems(*edges, zone(-2, -2, 21, 32), zone(21, -2, 52, 32)) == []


def test_zone_around_a_cutout() -> None:
    cutout = rectangle(20, 10, 30, 20)
    assert problems(EDGE, cutout, zone(-2, -2, 52, 32)) == []
    (found,) = problems(EDGE, cutout, zone(-2, -2, 25, 32))
    assert "outline on the board at (25, " in found


def test_round_board() -> None:
    edge = '(gr_circle (center 10 10) (end 20 10) (layer "Edge.Cuts"))'
    assert problems(edge, zone(-1, -1, 21, 21)) == []
    assert problems(edge, zone(1, 1, 19, 19))  # its corners clear, its sides on the board


def test_rounded_corners() -> None:
    """A board with 2 mm arcs at its corners; a zone square at the corners
    clears the arcs."""
    r = 2.0
    s = math.sqrt(0.5) * r
    edges = [
        line((r, 0), (50 - r, 0)),
        line((50, r), (50, 30 - r)),
        line((50 - r, 30), (r, 30)),
        line((0, 30 - r), (0, r)),
        f'(gr_arc (start 0 {r}) (mid {r - s} {r - s}) (end {r} 0) (layer "Edge.Cuts"))',
        f'(gr_arc (start {50 - r} 0) (mid {50 - r + s} {r - s}) (end 50 {r}) (layer "Edge.Cuts"))',
        f"(gr_arc (start 50 {30 - r}) (mid {50 - r + s} {30 - r + s}) (end {50 - r} 30)"
        ' (layer "Edge.Cuts"))',
        f'(gr_arc (start {r} 30) (mid {r - s} {30 - r + s}) (end 0 {30 - r}) (layer "Edge.Cuts"))',
    ]
    assert problems(*edges, zone(-0.5, -0.5, 50.5, 30.5)) == []
    assert not on_board((0.2, 0.2), [s for e in edges for s in segments(parse(e))])


def test_keepout_only_locked() -> None:
    keepout = zone(10, 10, 20, 20, extra="(keepout (tracks not_allowed))")
    assert problems(EDGE, zone(-2, -2, 52, 32), keepout) == []
    unlocked = zone(10, 10, 20, 20, locked="", extra="(keepout (tracks not_allowed))")
    assert problems(EDGE, zone(-2, -2, 52, 32), unlocked) == ["zone GND on F.Cu: not locked"]


def test_no_edge() -> None:
    assert problems(zone(-2, -2, 52, 32)) == ["no Edge.Cuts outline"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("(kicad_sch)", "not a kicad_pcb"),
        ('(kicad_pcb (zone (net "GND") (layer "F.Cu")))', "has no polygon"),
        ('(kicad_pcb (gr_line (start 0 0) (layer "Edge.Cuts")))', "without start and end"),
    ],
)
def test_not_a_board(text: str, message: str) -> None:
    with pytest.raises(BoardError, match=message):
        check_board(parse(text))


@pytest.mark.parametrize(
    ("start", "middle", "end"),
    [
        ((1.0, 0.0), (math.sqrt(0.5), math.sqrt(0.5)), (0.0, 1.0)),  # counterclockwise
        ((0.0, 1.0), (math.sqrt(0.5), math.sqrt(0.5)), (1.0, 0.0)),  # clockwise
        ((1.0, 0.0), (-1.0, 0.0), (0.0, -1.0)),  # three quarters round
    ],
)
def test_arc(start: Point, middle: Point, end: Point) -> None:
    points = arc(start, middle, end)
    assert points[0] == pytest.approx(start)
    assert points[-1] == pytest.approx(end)
    assert all(math.hypot(*p) == pytest.approx(1.0) for p in points)
    nearest_middle = min(math.dist(p, middle) for p in points)
    assert nearest_middle < 0.25


# A board too small round its edge to need stitching (kicad_style_finishing),
# with no tracks to route, so only the zone rules apply.
SMALL = rectangle(0, 0, 2, 2)


def test_command(tmp_path: Path) -> None:
    good = tmp_path / "good.kicad_pcb"
    good.write_text(board(SMALL, zone(-1, -1, 3, 3)))
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text(board(SMALL, zone(-1, -1, 3, 3, locked="")))
    other = tmp_path / "board.txt"
    other.write_text(board(EDGE))

    def run(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from ee_tooling.kicad_style_check import main; main()",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    assert run(good).returncode == 0
    r = run(bad)
    assert (r.returncode, r.stdout) == (1, f"{bad}: zone GND on F.Cu: not locked\n")
    noted = tmp_path / "noted.kicad_pcb"
    noted.write_text(board(SMALL, zone(1, -1, 3, 3)))
    r = run(noted)
    assert (r.returncode, r.stdout.startswith(f"{noted}: note: zone GND")) == (0, True)
    r = run(other)
    assert r.returncode == 2
    assert "not a .kicad_sch, .kicad_pcb or .kicad_pro" in r.stderr


def test_polygon_with_arcs() -> None:
    """A KiCad 10 polygon holds arcs among its points; a semicircle is
    walked, not skipped, and the polygon closes."""
    shape = parse(
        "(gr_poly (pts (xy 0 0) (arc (start 10 0) (mid 15 5) (end 10 10)) (xy 0 10))"
        ' (layer "Edge.Cuts"))'
    )
    points = outline_points(shape[1])  # type: ignore[arg-type]
    assert points[0] == (0, 0) and points[1] == (10, 0) and points[-1] == (0, 10)
    assert any(math.isclose(p[0], 15, abs_tol=0.01) for p in points)
    found = segments(shape)
    assert found[-1] == ((0, 10), (0, 0))


def test_polygon_without_points() -> None:
    with pytest.raises(BoardError, match="a gr_poly without points"):
        segments(parse('(gr_poly (pts) (layer "Edge.Cuts"))'))


def test_polygon_unknown_item(capsys: pytest.CaptureFixture[str]) -> None:
    shape = parse("(pts (xy 0 0) (bezier (pts)) (xy 1 0) loose (xy 1 1))")
    assert outline_points(shape) == [(0, 0), (1, 0), (1, 1)]
    err = capsys.readouterr().err
    assert "skipped: (bezier ...) in a polygon's points" in err
    assert "skipped: 'loose' in a polygon's points" in err
