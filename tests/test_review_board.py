import math
from pathlib import Path

import pytest

from ee_tooling.review_board import (
    LONGEST_NETS,
    copper_layers,
    item_net,
    net_table,
    outline,
    path_length,
    placed,
    summary,
    track_path,
)
from ee_tooling.sexpr import parse

BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0.05)
    (stackup (layer "F.Cu" (type "copper") (thickness 0.035)) (copper_finish "ENIG")))
  (net 0 "") (net 1 "GND") (net 2 "V12V") (net 3 "SIG")
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts"))
  (gr_line (start 50 0) (end 50 30) (layer "Edge.Cuts"))
  (gr_line (start 50 30) (end 0 30) (layer "Edge.Cuts"))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts"))
  (gr_text "REV A" (at 5 28) (layer "F.SilkS"))
  (footprint "Lib:R_0402" (layer "F.Cu") (at 10 10 90)
    (property "Reference" "R1") (property "Value" "10k")
    (attr smd dnp)
    (fp_text user "pin 1" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "V12V")))
  (footprint "Lib:Fiducial_1mm" (layer "F.Cu") (at 2 2)
    (property "Reference" "FID1") (property "Value" "Fiducial"))
  (footprint "Lib:MountingHole_M3" (layer "F.Cu") (at 45 25)
    (property "Reference" "H1") (property "Value" "M3")
    (pad "" thru_hole circle (at 0 0) (size 3 3) (drill 3) (layers "*.Cu")))
  (segment (start 10 10) (end 20 10) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 20 10) (end 20 15) (width 0.25) (layer "B.Cu") (net 2))
  (arc (start 30 10) (mid 32 12) (end 34 10) (width 0.2) (layer "B.Cu") (net 3))
  (via (at 20 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))
  (zone (net 1) (layers "F.Cu" "B.Cu") (priority 1) (min_thickness 0.25)
    (connect_pads (clearance 0.3)) (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 1 1) (xy 49 1) (xy 49 29) (xy 1 29))))
  (zone (net 0) (net_name "") (layer "F.Cu") (keepout (tracks not_allowed) (vias allowed))
    (polygon (pts (xy 30 20) (xy 40 20) (xy 40 25) (xy 30 25))))
)
"""


def test_net_lookup() -> None:
    board = parse(BOARD)
    table = net_table(board)
    assert table == {"0": "", "1": "GND", "2": "V12V", "3": "SIG"}
    segment = next(s for s in board if isinstance(s, list) and s[0] == "segment")
    assert item_net(segment, table) == "V12V"


def test_arc_track_length() -> None:
    """A half circle of radius 2: its length is 2 pi, not the 4 mm chord."""
    arc = parse('(arc (start 30 10) (mid 32 12) (end 34 10) (width 0.2) (layer "B.Cu"))')
    path = track_path(arc)
    assert path is not None
    assert path_length(path) == pytest.approx(2 * math.pi, rel=1e-3)


def test_track_path_without_points(capsys: pytest.CaptureFixture[str]) -> None:
    assert track_path(parse('(arc (start 0 0) (end 1 0) (layer "F.Cu"))')) is None
    assert "skipped: (arc ...) without its start, end or mid" in capsys.readouterr().err


def test_copper_layers() -> None:
    assert copper_layers(parse(BOARD)) == [("F.Cu", "signal"), ("B.Cu", "signal")]


def test_outline() -> None:
    assert outline(parse(BOARD)) == (4, (0.0, 50.0, 0.0, 30.0))


def test_placed() -> None:
    board = parse(BOARD)
    parts = [placed(fp) for fp in board if isinstance(fp, list) and fp[0] == "footprint"]
    r1, fid, h1 = parts
    assert (r1.ref, r1.value, r1.angle, r1.dnp, r1.through_hole) == ("R1", "10k", 90.0, True, False)
    assert fid.ref == "FID1" and h1.through_hole


def test_summary(tmp_path: Path) -> None:
    path = tmp_path / "b.kicad_pcb"
    path.write_text(BOARD, encoding="utf-8")
    text = summary(path)
    assert "thickness: 1.6 mm" in text
    assert "- copper_finish: ENIG" in text
    assert "bbox x 0.00..50.00 (50.0 mm), y 0.00..30.00 (30.0 mm)" in text
    assert "segments: 3; length per layer (mm): B.Cu 11, F.Cu 10" in text
    assert "size/drill histogram: 0.6/0.3: 1" in text
    assert (
        "| GND | F.Cu B.Cu | 1 | 0.25 | 0.3 | thermal | 0.5/0.5 | yes |  | 1..49 x 1..29 |" in text
    )
    assert (
        "| KEEPOUT  | F.Cu | 0 | | | | | | tracks=not_allowed vias=allowed | 30..40 x 20..25 |"
        in text
    )
    assert "| V12V | 2 | 15 | 0.25/0.5 | B.Cu:1 F.Cu:1 | 1 |  |" in text
    assert "nets with zones but no tracks: GND" in text
    assert "count: 3; by side: {'F.Cu': 3}; through-hole: 1" in text
    assert "- fiducials (1): FID1 (2.0,2.0) F.Cu" in text
    assert "- mounting (1): H1 (45.0,25.0)" in text
    assert "- DNP (1): R1" in text
    assert "| R1 (DNP) | 10k | Lib:R_0402 | F.Cu | 10.00 | 10.00 | 90 |" in text
    assert "- 'pin 1' on R1" in text
    assert "- F.SilkS: 'REV A' at (5.0,28.0)" in text


def test_longest_nets_besides_power(tmp_path: Path) -> None:
    """Power nets, however long, do not use up the longest-nets count."""
    names = [f"V{i}V" for i in range(LONGEST_NETS)] + [f"SIG{i}" for i in range(LONGEST_NETS + 1)]
    nets = " ".join(f'(net {i + 1} "{n}")' for i, n in enumerate(names))
    tracks = " ".join(
        f'(segment (start 0 {i}) (end {100 - i} {i}) (width 0.2) (layer "F.Cu") (net {i + 1}))'
        for i in range(len(names))
    )
    path = tmp_path / "b.kicad_pcb"
    path.write_text(f"(kicad_pcb {nets} {tracks})", encoding="utf-8")
    text = summary(path)
    shown = [n for n in names if f"| {n} |" in text]
    assert shown == names[:-1]
