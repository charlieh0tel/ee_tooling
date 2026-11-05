import math
from pathlib import Path

import pytest

from ee_tooling.review_pairs import COUPLED_GAP, Track, gaps, report, track_distance, tracks
from ee_tooling.sexpr import parse

# H and L run side by side on F.Cu 0.3 mm apart (edge gap 0.1), then H
# alone on B.Cu; a short H stub is below the sampling minimum.
BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew")
  (net 1 "CAN_H") (net 2 "CAN_L")
  (segment (start 0 0) (end 10 0) (width 0.2) (layer "F.Cu") (net 1))
  (segment (start 0 0.3) (end 10 0.3) (width 0.2) (layer "F.Cu") (net 2))
  (segment (start 10 0) (end 10 0.2) (width 0.2) (layer "F.Cu") (net 1))
  (segment (start 10 0) (end 20 0) (width 0.2) (layer "B.Cu") (net 1))
  (segment (start 20 0) (end 30 0) (width 0.2) (layer "F.Cu") (net 1))
)
"""


def test_track_distance() -> None:
    a = Track("F.Cu", 0.2, ((0, 0), (10, 0)), 10)
    b = Track("F.Cu", 0.2, ((0, 1), (10, 1)), 10)
    c = Track("F.Cu", 0.2, ((5, 2), (5, 12)), 10)
    assert track_distance(a, b) == 1.0
    assert track_distance(a, c) == 2.0


def test_gaps() -> None:
    by_net = tracks(parse(BOARD))
    found = gaps(by_net["CAN_H"], by_net["CAN_L"])
    assert [g.layer for g in found] == ["F.Cu"]
    f = found[0]
    assert f.length == 20.0
    assert f.gaps == (0.1, 9.804)
    assert f.median == 0.1
    assert f.coupled == 0.5
    assert COUPLED_GAP == 0.3


def test_report(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    path.write_text(BOARD, encoding="utf-8")
    text = report(path, [("CAN_H", "CAN_L"), ("CAN_H", "NOPE")])
    assert "- CAN_H: length 30.2 mm (F.Cu 20.2, B.Cu 10.0), widths [0.2]" in text
    assert "min 0.10 median 0.10 max 9.80 mm; gap <= 0.3 mm over 50% of length" in text
    assert "- NOPE: no segments (is the net name right?)" in text


# H and L as concentric half circles, radii 2 and 2.3 (edge gap 0.1).
ARCS = """(kicad_pcb (version 20241229) (generator "pcbnew")
  (net 1 "CAN_H") (net 2 "CAN_L")
  (arc (start 2 0) (mid 0 2) (end -2 0) (width 0.2) (layer "F.Cu") (net 1))
  (arc (start 2.3 0) (mid 0 2.3) (end -2.3 0) (width 0.2) (layer "F.Cu") (net 2))
)
"""


def test_gaps_along_arcs() -> None:
    by_net = tracks(parse(ARCS))
    (f,) = gaps(by_net["CAN_H"], by_net["CAN_L"])
    assert f.length == pytest.approx(2 * math.pi, rel=1e-3)
    assert f.median == pytest.approx(0.1, abs=0.01)
    assert f.coupled == 1.0


def test_median_weighted_by_length() -> None:
    """Three short pieces far from L do not outvote one long coupled run."""
    h = [Track("F.Cu", 0.2, ((0, 0), (10, 0)), 10)] + [
        Track("F.Cu", 0.2, ((20 + i, 0), (21 + i, 0)), 1) for i in range(3)
    ]
    low = [Track("F.Cu", 0.2, ((0, 0.3), (10, 0.3)), 10)]
    (f,) = gaps(h, low)
    assert len(f.gaps) == 4 and f.gaps[2] > 10
    assert f.median == 0.1
