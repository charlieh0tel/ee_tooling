"""ee_tooling's KiCad rule files (src/ee_tooling/rules, docs/kicad-rules.md)."""

import importlib.resources
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ee_tooling.sexpr import Expression, child, children, number, parse

RULES = importlib.resources.files("ee_tooling").joinpath("rules")
DRU = RULES.joinpath("jlcpcb.kicad_dru").read_text()
BOARD_SETUP = json.loads(RULES.joinpath("jlcpcb_board_setup.json").read_text())

# KiCad 10's custom rule constraint types (pcbnew manual, Custom design
# rules, Constraint type).
CONSTRAINTS = {
    "annular_width",
    "assertion",
    "bridged_mask",
    "clearance",
    "connection_width",
    "courtyard_clearance",
    "creepage",
    "diff_pair_gap",
    "diff_pair_uncoupled",
    "disallow",
    "edge_clearance",
    "hole_clearance",
    "hole_size",
    "hole_to_hole",
    "length",
    "min_resolved_spokes",
    "physical_clearance",
    "physical_hole_clearance",
    "silk_clearance",
    "skew",
    "solder_mask_expansion",
    "solder_paste_abs_margin",
    "solder_paste_rel_margin",
    "text_height",
    "text_thickness",
    "thermal_relief_gap",
    "thermal_spoke_width",
    "track_angle",
    "track_segment_length",
    "track_width",
    "via_count",
    "via_dangling",
    "via_diameter",
    "zone_connection",
}
# The .kicad_dru's section headings, in order: JLCPCB's limits and price,
# then the house rules, which follow so that they win.
FAB_PREFIX = "JLCPCB "
HOUSE_HEADING = "# --- House rules"
# Board Setup's rule keys in a .kicad_pro (board.design_settings.rules).
BOARD_SETUP_KEYS = {"min_clearance", "min_copper_edge_clearance", "min_hole_clearance"}


def dru(text: str) -> list[Expression]:
    """A .kicad_dru's expressions: KiCad's comments are whole lines
    starting with #."""
    kept = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return [e for e in parse(f"({kept})") if isinstance(e, list)]


def rules(text: str) -> list[Expression]:
    return [e for e in dru(text) if e[0] == "rule"]


def names(text: str) -> list[str]:
    """The rules' names, each a quoted string."""
    found = [r[1] for r in rules(text)]
    assert all(isinstance(n, str) and n for n in found)
    return [str(n) for n in found]


def test_dru_parses() -> None:
    expressions = dru(DRU)
    assert expressions[0] == ["version", "1"]
    found = rules(DRU)
    assert len(found) == len(expressions) - 1
    assert len(set(names(DRU))) == len(found)
    for r in found:
        constraints = children(r, "constraint")
        assert constraints, r[1]
        assert {c[1] for c in constraints} <= CONSTRAINTS, r[1]


def test_dru_parse_drops_comments_only() -> None:
    text = '(version 1)\n  # a (comment\n(rule "a # b" (constraint track_width (min 1mm)))'
    assert names(text) == ["a # b"]


def test_house_rules_follow_jlcpcb() -> None:
    fab, house = DRU.split(HOUSE_HEADING)
    assert all(n.startswith(FAB_PREFIX) for n in names(fab))
    assert names(house)
    assert not any(n.startswith(FAB_PREFIX) for n in names(house))


def test_track_angle_unitless() -> None:
    """KiCad 10.0.8 ignores a track_angle limit with a unit ("91deg")."""
    angles = [c for r in rules(DRU) for c in children(r, "constraint") if c[1] == "track_angle"]
    assert angles
    for c in angles:
        for limit in ("min", "max"):
            if (value := child(c, limit)) is not None:
                number(value[1])


def test_board_setup() -> None:
    values = BOARD_SETUP["board"]["design_settings"]["rules"]
    assert set(values) == BOARD_SETUP_KEYS
    assert all(isinstance(v, float) and v > 0 for v in values.values())


# A board for kicad-cli: an outline, and tracks and a footprint each
# breaking one rule or none.
EDGE = " ".join(
    f'(gr_line (start {a}) (end {b}) (stroke (width 0.05) (type solid)) (layer "Edge.Cuts"))'
    for a, b in [("0 0", "40 0"), ("40 0", "40 30"), ("40 30", "0 30"), ("0 30", "0 0")]
)
LAYERS = (
    '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (5 "F.SilkS" user) (7 "B.SilkS" user)'
    ' (1 "F.Mask" user) (3 "B.Mask" user) (25 "Edge.Cuts" user))'
)


def segment(start: str, end: str, net: str, width: float = 0.25) -> str:
    return f'(segment (start {start}) (end {end}) (width {width}) (layer "F.Cu") (net "{net}"))'


def board(*items: str) -> str:
    return (
        '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")'
        f' (general (thickness 1.6)) (paper "A4") {LAYERS} (setup) {EDGE} {" ".join(items)})'
    )


def drc(tmp_path: Path, *items: str) -> list[str]:
    """kicad-cli's DRC of a board of items with the rules: each violation's
    type and description."""
    (tmp_path / "t.kicad_pcb").write_text(board(*items))
    (tmp_path / "t.kicad_dru").write_text(DRU)
    report = tmp_path / "drc.json"
    subprocess.run(
        [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "-o",
            str(report),
            str(tmp_path / "t.kicad_pcb"),
        ],
        check=True,
        capture_output=True,
    )
    violations = json.loads(report.read_text())["violations"]
    return [f"{v['type']}: {v['description']}" for v in violations if v["type"] != "track_dangling"]


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="no kicad-cli (KiCad 10) on the path")
def test_kicad_drc(tmp_path: Path) -> None:
    right_angle = [segment("5 5", "15 5", "A"), segment("15 5", "15 10", "A")]
    forty_five = [segment("5 15", "10 15", "B"), segment("10 15", "13 18", "B")]
    thin = [segment("20 5", "30 5", "C", width=0.09)]
    hole = (
        '(footprint "MountingHole:MountingHole_3.2mm_M3_Pad" (layer "F.Cu") (at 30 20)'
        ' (pad "1" thru_hole circle (at 0 0) (size 4 4) (drill 3.2) (layers "*.Cu" "*.Mask")'
        ' (net "GND")))'
    )
    found = drc(tmp_path, *right_angle, *forty_five, *thin, hole)
    angles = [v for v in found if v.startswith("track_angle: ")]
    assert angles and all("'No right-angle bends'" in v and "actual 90" in v for v in angles)
    assert any(v.startswith("track_width: ") and "'JLCPCB track width'" in v for v in found)
    assert any(
        v.startswith("annular_width: ") and "'Mounting hole ring'" in v and "actual 0.4" in v
        for v in found
    )
