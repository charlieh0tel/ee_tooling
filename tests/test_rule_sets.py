"""What each rule set installs in a board pinned at it, held byte for
byte: the code that applies a rule set (the markers, Board Setup raised,
each minimum raised to the board's Board Setup, the ERC settings) must not
change what a released set does to a board (docs/releasing.md, Rule
sets)."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from ee_tooling.adopt import rules_edits
from ee_tooling.fab_release import Board, board_rules
from ee_tooling.fabrication import Fab
from ee_tooling.kicad_rules import rule_sets
from ee_tooling.profiles.jlcpcb import Jlcpcb

# Each released rule set's expected output, a directory named for it, of
# each board's NAME.kicad_dru and NAME.kicad_pro as adopt --yes writes
# them, and NAME.drc.kicad_pro as fab-release's DRC reads it.
GOLDEN = Path(__file__).parent / "rule_sets"
# Set, a missing expected file is written (never one that is there).
WRITE_GOLDEN = "EE_TOOLING_WRITE_GOLDEN"
FAB = Fab(2, "FR-4", 135.0, "yes")
# The board's own rule after the section.
OWN_RULE = '(rule "Mine"\n\t(constraint track_width (min 0.2mm)))\n'
# A board below every minimum: Board Setup raised, the section's minimums
# kept; ERC severities laxer than the house's, one stricter, one the house
# does not name.
LOW: dict[str, Any] = {
    "board": {"design_settings": {"rules": {"min_clearance": 0.05, "min_text_height": 0.5}}},
    "erc": {
        "rule_severities": {
            "endpoint_off_grid": "ignore",
            "four_way_junction": "error",
            "my_own_check": "ignore",
        }
    },
    "meta": {"filename": "low.kicad_pro", "version": 3},
    "net_settings": {
        "classes": [
            {"name": "Default", "microvia_diameter": 0.2},
            {"name": "Power"},
        ]
    },
    "schematic": {"connection_grid_size": 25.0},
}
# A board above every minimum, each of its Board Setup values distinct and
# above every rule's minimum: any minimum raised to one of them shows which.
HIGH: dict[str, Any] = {
    "board": {
        "design_settings": {
            "rules": {
                "min_clearance": 1.101,
                "min_connection": 1.102,
                "min_copper_edge_clearance": 1.103,
                "min_groove_width": 1.104,
                "min_hole_clearance": 1.105,
                "min_hole_to_hole": 1.106,
                "min_microvia_diameter": 1.107,
                "min_microvia_drill": 1.108,
                "min_silk_clearance": 1.109,
                "min_text_height": 1.111,
                "min_text_thickness": 1.112,
                "min_through_hole_diameter": 1.113,
                "min_track_width": 1.114,
                "min_via_annular_width": 1.115,
                "min_via_diameter": 1.116,
                "solder_mask_to_copper_clearance": 1.117,
            }
        }
    },
    "erc": {"rule_severities": {"field_name_whitespace": "error"}},
    "meta": {"filename": "high.kicad_pro", "version": 3},
    "net_settings": {"classes": [{"name": "Default", "microvia_diameter": 1.118}]},
    "schematic": {"connection_grid_size": 50.0},
}
BOARDS = {"low": LOW, "high": HIGH}


def outputs(root: Path, name: str, project: dict[str, Any], version: str) -> dict[str, str]:
    """What adopt --yes writes for the board pinned at version, and what
    fab-release's DRC reads, by golden file name."""
    board = root / "hw"
    board.mkdir()
    (board / f"{name}.kicad_pro").write_text(json.dumps(project, indent=2) + "\n")
    (board / f"{name}.kicad_dru").write_text(f"(version 1)\n\n{OWN_RULE}")
    config = (
        f'[[board]]\nname = "{name}"\ndirectory = "hw"\nprofile = "jlcpcb"\nrules = "{version}"\n'
    )
    edits = {e.path.name: e.text for e in rules_edits(root, config)}
    b = Board(name, Path("hw"), Path("production"), (), "jlcpcb", FAB, rules=version)
    profile = Jlcpcb.builtin()
    drc = board_rules(root, b, profile, profile.options({}))
    assert drc.dru == edits[f"{name}.kicad_dru"], "fab-release's DRC reads adopt's .kicad_dru"
    return {
        **edits,
        f"{name}.drc.kicad_pro": json.dumps(drc.project, indent=2) + "\n",
    }


def held(root: Path, name: str, version: str) -> None:
    """Board name pinned at version gets the expected output, byte for
    byte."""
    found = outputs(root, name, BOARDS[name], version)
    expected = GOLDEN / version
    missing = [f for f in found if not (expected / f).exists()]
    if missing and os.environ.get(WRITE_GOLDEN):
        expected.mkdir(parents=True, exist_ok=True)
        for f in missing:
            (expected / f).write_text(found[f])
        missing = []
    assert not missing, (
        f"{expected}: no {', '.join(missing)}: a new rule set's expected output;"
        f" {WRITE_GOLDEN}=1 uv run pytest {Path(__file__).name} writes it"
    )
    for f, text in found.items():
        assert text == (expected / f).read_text(), f"{version}: {f} differs"


@pytest.mark.parametrize("version", rule_sets())
@pytest.mark.parametrize("name", list(BOARDS))
def test_rule_set_output_held(tmp_path: Path, version: str, name: str) -> None:
    """A released rule set installs in each board what it did when it was
    released, byte for byte, in adopt and in fab-release's DRC."""
    held(tmp_path, name, version)


@pytest.mark.parametrize("name", list(BOARDS))
def test_rule_set_output_held_under_a_newer_set(tmp_path: Path, name: str, newer_set: str) -> None:
    """A newer set current changes nothing for a board pinned at 0.4.0."""
    held(tmp_path, name, "0.4.0")


def test_every_set_held() -> None:
    """Each rule set the package has has its expected output, and nothing
    else does."""
    assert sorted(d.name for d in GOLDEN.iterdir()) == sorted(rule_sets())
