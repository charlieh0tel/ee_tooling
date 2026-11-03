from pathlib import Path

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
    a = Track("F.Cu", 0.2, ((0, 0), (10, 0)))
    b = Track("F.Cu", 0.2, ((0, 1), (10, 1)))
    c = Track("F.Cu", 0.2, ((5, 2), (5, 12)))
    assert track_distance(a, b) == 1.0
    assert track_distance(a, c) == 2.0


def test_gaps() -> None:
    by_net = tracks(parse(BOARD))
    found = gaps(by_net["CAN_H"], by_net["CAN_L"])
    assert [g.layer for g in found] == ["F.Cu"]
    f = found[0]
    assert f.length == 20.0
    assert f.gaps == (0.1, 9.804)
    assert f.coupled == 0.5
    assert COUPLED_GAP == 0.3


def test_report(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    path.write_text(BOARD, encoding="utf-8")
    text = report(path, [("CAN_H", "CAN_L"), ("CAN_H", "NOPE")])
    assert "- CAN_H: length 30.2 mm, widths [0.2], layers {'F.Cu': 3, 'B.Cu': 1}" in text
    assert "min 0.10 median 9.80 max 9.80 mm; gap <= 0.3 mm over 50% of length" in text
    assert "- NOPE: no segments (is the net name right?)" in text
