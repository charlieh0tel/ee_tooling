import itertools
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ee_tooling.kicad_style_board import Point, distance, edge, on_board
from ee_tooling.kicad_style_finishing import MAX_GAP, stitching
from ee_tooling.sexpr import Expression, parse
from ee_tooling.stitch import Settings, gap_marks, grid_marks, ring_marks, spots

SETTINGS = Settings()


def rectangle(x0: float, y0: float, x1: float, y1: float) -> str:
    return f'(gr_rect (start {x0} {y0}) (end {x1} {y1}) (layer "Edge.Cuts"))'


def board(*items: str) -> Expression:
    return parse(f"(kicad_pcb (version 20260206) {' '.join(items)})")


def nearest_edge(p: Point, b: Expression) -> float:
    return min(distance(p, s) for s in edge(b))


def test_ring_rectangle() -> None:
    b = board(rectangle(0, 0, 40, 30))
    marks = ring_marks(b, SETTINGS)
    # 140 mm round, 5 mm apart.
    assert len(marks) == 28
    assert all(on_board(m, edge(b)) for m in marks)
    assert all(math.isclose(nearest_edge(m, b), SETTINGS.inset) for m in marks)


def test_ring_cutout() -> None:
    """A cutout's marks are outside it, on the board."""
    b = board(rectangle(0, 0, 40, 30), rectangle(15, 10, 25, 20))
    cutout = [m for m in ring_marks(b, SETTINGS) if 12 < m[0] < 28 and 7 < m[1] < 23]
    assert len(cutout) == 8
    assert all(not (15 < x < 25 and 10 < y < 20) for x, y in cutout)
    assert all(math.isclose(nearest_edge(m, b), SETTINGS.inset) for m in cutout)


def test_ring_small_outline() -> None:
    """An outline shorter than the pitch gets one mark."""
    assert len(ring_marks(board(rectangle(0, 0, 2, 1)), SETTINGS)) == 1


def test_gap_marks() -> None:
    b = board(rectangle(0, 0, 40, 30))
    # Barrels along the bottom edge only: the rest of the outline is one gap.
    grounds = [(float(x), 29.0) for x in range(5, 40, 5)]
    marks = gap_marks(b, grounds, SETTINGS)
    assert marks
    assert all(m[1] < 29 - SETTINGS.inset / 2 or m[0] < 5 or m[0] > 35 for m in marks)
    gaps = sorted({round(math.dist(a, c), 6) for a, c in itertools.pairwise(marks)})
    assert gaps[0] <= SETTINGS.pitch / 2 + 1e-6


def test_no_gap_marks() -> None:
    b = board(rectangle(0, 0, 40, 30))
    stitched = ring_marks(b, SETTINGS)
    assert gap_marks(b, stitched, SETTINGS) == []
    assert gap_marks(b, [], SETTINGS) == []


def test_grid() -> None:
    b = board(rectangle(0, 0, 40, 30))
    assert grid_marks(b, SETTINGS) == [(10, 10), (20, 10), (30, 10), (10, 20), (20, 20), (30, 20)]
    assert grid_marks(b, Settings(grid=0)) == []


def test_grid_skips_cutout() -> None:
    b = board(rectangle(0, 0, 40, 30), rectangle(15, 5, 25, 25))
    assert grid_marks(b, SETTINGS) == [(10, 10), (30, 10), (10, 20), (30, 20)]


def test_spots() -> None:
    found = list(spots((10, 10), SETTINGS))
    assert found[0] == (10, 10)
    distances = [math.dist(p, (10, 10)) for p in found]
    assert distances == sorted(distances)
    assert max(distances) <= SETTINGS.reach
    assert len(found) == len(set(found))


# The end-to-end test: a 40 x 30 mm two-layer board, GND poured on both
# sides, a track on another net across it, no vias.
TRACK = '(segment (start 5 15) (end 35 15) (width 0.25) (layer "F.Cu") (net "SIG"))'


def zone(layer: str) -> str:
    return (
        f'(zone (net "GND") (layer "{layer}")'
        " (connect_pads (clearance 0.3)) (min_thickness 0.25)"
        " (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))"
        " (polygon (pts (xy -2 -2) (xy 42 -2) (xy 42 32) (xy -2 32))))"
    )


def pcb_text(*items: str) -> str:
    return (
        "(kicad_pcb (version 20260206) (generator pcbnew)"
        " (general (thickness 1.6))"
        ' (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))'
        " (gr_rect (start 0 0) (end 40 30) (stroke (width 0.05) (type solid))"
        ' (layer "Edge.Cuts"))'
        f" {' '.join(items)} {zone('F.Cu')} {zone('B.Cu')})"
    )


BOARD = pcb_text(TRACK)


def run_stitch(
    tmp_path: Path, text: str, path: str | None = None, options: tuple[str, ...] = ()
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """stitch run on a board of text, with a project file beside it, its
    options, and PATH path if given."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(text)
    (tmp_path / "b.kicad_pro").write_text(json.dumps({"meta": {"filename": "b.kicad_pro"}}))
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ee_tooling.stitch_kicad import main; main()",
            str(pcb),
            *options,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=None if path is None else {**os.environ, "PATH": path},
    )
    return pcb, r


def via_points(board: Expression) -> list[Point]:
    return [
        (float(str(at[1])), float(str(at[2])))
        for v in board
        if isinstance(v, list) and v and v[0] == "via"
        for at in v
        if isinstance(at, list) and at[0] == "at"
    ]


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="needs kicad-cli")
def test_stitch_board(tmp_path: Path) -> None:
    pytest.importorskip("pcbnew")
    pcb, r = run_stitch(tmp_path, BOARD)
    assert r.returncode == 0, r.stderr
    after = parse(pcb.read_text())
    vias = [v for v in after if isinstance(v, list) and v and v[0] == "via"]
    assert vias
    assert all(g.length <= MAX_GAP for g in stitching(after, "GND"))
    # Each clear of the track: its half width, the via's radius and 0.2 mm,
    # KiCad's default clearance.
    radius = float(str(next(c for c in vias[0] if isinstance(c, list) and c[0] == "size")[1])) / 2
    for v in vias:
        at = next(c for c in v if isinstance(c, list) and c[0] == "at")
        p = (float(str(at[1])), float(str(at[2])))
        assert distance(p, ((5, 15), (35, 15))) >= 0.125 + radius + 0.2 - 1e-6


# A 6 x 2.4 mm SMD pad on GND near the top edge, where the ring's marks are.
SMD_PAD = (
    '(footprint "T:Pad" (layer "F.Cu") (at 10 2)'
    ' (pad "1" smd rect (at 0 0) (size 6 2.4) (layers "F.Cu" "F.Paste" "F.Mask")'
    ' (net "GND")))'
)


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="needs kicad-cli")
def test_stitch_keeps_out_of_own_smd_pad(tmp_path: Path) -> None:
    """No via in an SMD pad of its own net: it keeps the pad at its clearance."""
    pytest.importorskip("pcbnew")
    pcb, r = run_stitch(tmp_path, pcb_text(TRACK, SMD_PAD))
    assert r.returncode == 0, r.stderr
    vias = via_points(parse(pcb.read_text()))
    assert vias
    # The via's radius (KiCad's default via, 0.6 mm) and 0.2 mm, KiCad's
    # default clearance, from the pad's rectangle.
    radius = 0.3
    for x, y in vias:
        dx = max(abs(x - 10) - 3, 0.0)
        dy = max(abs(y - 2) - 1.2, 0.0)
        assert math.hypot(dx, dy) >= radius + 0.2 - 1e-6, (x, y)


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="needs kicad-cli")
def test_stitch_via_in_pad(tmp_path: Path) -> None:
    """With --via-in-pad, a via may go in an SMD pad of its own net."""
    pytest.importorskip("pcbnew")
    pcb, r = run_stitch(tmp_path, pcb_text(TRACK, SMD_PAD), options=("--via-in-pad",))
    assert r.returncode == 0, r.stderr
    vias = via_points(parse(pcb.read_text()))
    assert any(abs(x - 10) <= 3 and abs(y - 2) <= 1.2 for x, y in vias), vias


def test_stitch_without_kicad_cli(tmp_path: Path) -> None:
    """Without kicad-cli, stitch says so and leaves the board as it was."""
    empty = tmp_path / "bin"
    empty.mkdir()
    pcb, r = run_stitch(tmp_path, BOARD, path=str(empty))
    assert r.returncode != 0
    assert "kicad-cli" in r.stderr
    assert "Traceback" not in r.stderr
    assert pcb.read_bytes() == BOARD.encode()


def test_stitch_drc_failing(tmp_path: Path) -> None:
    """When kicad-cli fails, stitch says so and leaves the board as it was."""
    pytest.importorskip("pcbnew")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "kicad-cli"
    fake.write_text("#!/bin/sh\necho broken >&2\nexit 3\n")
    fake.chmod(0o755)
    pcb, r = run_stitch(tmp_path, BOARD, path=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert r.returncode != 0
    assert "kicad-cli" in r.stderr
    assert "broken" in r.stderr
    assert "Traceback" not in r.stderr
    assert pcb.read_bytes() == BOARD.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.kicad_pcb", "b.kicad_pro", "bin"]
