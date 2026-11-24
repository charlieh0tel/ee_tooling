from pathlib import Path

import pytest

from ee_tooling.placement import Rules, score
from ee_tooling.placement_kicad import load


def footprint(reference: str, x: float, y: float, side: str, angle: float = 0) -> str:
    """A footprint with one pad and a 4 x 2 mm courtyard, on side F or B,
    turned angle degrees."""
    return (
        f'(footprint "T:Part" (layer "{side}.Cu") (at {x} {y} {angle})'
        f' (property "Reference" "{reference}" (at 0 0) (layer "{side}.SilkS"))'
        f' (property "Value" "x" (at 0 0) (layer "{side}.Fab"))'
        f" (fp_rect (start -2 -1) (end 2 1) (stroke (width 0.05) (type solid))"
        f' (layer "{side}.CrtYd"))'
        f' (pad "1" smd rect (at 0 0) (size 1 1) (layers "{side}.Cu")))'
    )


def board(*items: str) -> str:
    return (
        "(kicad_pcb (version 20260206) (generator pcbnew)"
        " (general (thickness 1.6))"
        ' (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)'
        ' (31 "F.CrtYd" user) (29 "B.CrtYd" user))'
        " (gr_rect (start 0 0) (end 40 30) (stroke (width 0.05) (type solid))"
        ' (layer "Edge.Cuts"))'
        f" {' '.join(items)})"
    )


def test_load_sides(tmp_path: Path) -> None:
    """A bottom part's courtyard is read from its side, and overlaps only
    the bottom side's."""
    pytest.importorskip("pcbnew")
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(
        board(
            footprint("U1", 10, 10, "F"),
            footprint("U2", 10, 10, "B"),
            footprint("U3", 11, 10, "B"),
        )
    )
    layout = load(pcb)
    assert [f.back for f in layout.footprints] == [False, True, True]
    assert all(f.courtyard for f in layout.footprints)
    assert score(layout, Rules(frozenset(), ())).overlaps == (("U2", "U3"),)


def test_load_rotated(tmp_path: Path) -> None:
    """A part turned 45 degrees: its courtyard is the turned rectangle, so
    a part in its bounding box's corner does not overlap it."""
    pytest.importorskip("pcbnew")
    pcb = tmp_path / "b.kicad_pcb"
    # U4's corners lie about 2.1 mm from its center along each axis: U5's
    # courtyard, from (21.5, 21.5), is in the box but past the rectangle.
    pcb.write_text(board(footprint("U4", 20, 20, "F", 45), footprint("U5", 23.5, 22.5, "F")))
    layout = load(pcb)
    u4, u5 = (f.courtyard for f in layout.footprints)
    assert u4 is not None and u5 is not None
    assert len(u4.outlines[0]) == 4
    assert u4.box.overlaps(u5.box)
    assert score(layout, Rules(frozenset(), ())).overlaps == ()
