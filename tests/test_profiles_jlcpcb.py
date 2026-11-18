import csv
import io
import shutil
import subprocess
from pathlib import Path

import pytest

from ee_tooling.fab_release import Board, Part, Placement, Release
from ee_tooling.profiles.jlcpcb import Parts, jlcpcb_bom, jlcpcb_cpl

PARTS = Parts(
    rotation={"C1": -90.0, "C2": 180.0},
    offset={"C3": (0.0, 1.5)},
    unchecked={"C4": "a new part"},
)


def place(ref: str, x: float, y: float, rot: float, side: str = "top") -> Placement:
    return Placement(ref, "v", "p", x, y, rot, side)


def test_bom() -> None:
    rows, refs, missing = jlcpcb_bom(
        [
            Part("C1", "100n", "lib:C_0402", "C1525"),
            Part("C2", "100n", "lib:C_0402", "C1525"),
            Part("C3", "100n", "lib:C_0402", "C307331"),
            Part("R1", "0R", "lib:R_0402", ""),
            Part("TP1", "TP", "TP", ""),
            Part("U1", "100n", "lib:C_0402", "C1525"),
        ]
    )
    assert rows == [
        ["100n", "C1,C2,U1", "C_0402", "C1525"],
        ["100n", "C3", "C_0402", "C307331"],
        ["0R", "R1", "R_0402", ""],
        ["TP", "TP1", "TP", ""],
    ]
    assert refs == {
        "C1": "C1525",
        "C2": "C1525",
        "U1": "C1525",
        "C3": "C307331",
        "R1": "",
        "TP1": "",
    }
    assert missing == ["R1", "TP1"]


# KiCad's demo schematics with LCSC fields.
DEMOS = Path("/usr/share/kicad/demos")
LCSC_DEMOS = [
    "royalblue54L_feather/RoyalBlue54L-Feather.kicad_sch",
    "openair-max/One-Air-Max.kicad_sch",
    "cm5_minima/CM5_MINIMA_3.kicad_sch",
]


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="no kicad-cli (KiCad 10) on the path")
@pytest.mark.skipif(not DEMOS.exists(), reason=f"no KiCad demos in {DEMOS}")
@pytest.mark.parametrize("demo", LCSC_DEMOS)
def test_bom_groups_as_kicad_cli(tmp_path: Path, demo: str) -> None:
    """The BOM from the release's parts, as kicad-cli's own grouped BOM."""
    sch = DEMOS / demo
    grouped = tmp_path / "grouped.csv"
    subprocess.run(
        [
            "kicad-cli",
            "sch",
            "export",
            "bom",
            "--exclude-dnp",
            "--fields",
            "Value,Reference,Footprint,LCSC",
            "--labels",
            "Comment,Designator,Footprint,LCSC Part #",
            "--group-by",
            "Value,Footprint,LCSC",
            "--ref-range-delimiter",
            "",
            "-o",
            str(grouped),
            str(sch),
        ],
        capture_output=True,
        check=True,
    )
    kicad = [
        [r["Comment"], r["Designator"], r["Footprint"].split(":", 1)[-1], r["LCSC Part #"]]
        for r in csv.DictReader(io.StringIO(grouped.read_text()))
    ]
    board = Board(sch.stem, Path("."), (), Path("production"), ())
    rows, _, _ = jlcpcb_bom(Release(sch.parent, board, tmp_path).parts())
    assert len(rows) > 1
    assert rows == kicad


def test_cpl_rotation_and_layer() -> None:
    rows, placed = jlcpcb_cpl(
        [place("U1", 1, -2, 0), place("U2", 0, 0, 270, "bottom"), place("R1", 0, 0, 90)],
        {"U1": "C1", "U2": "C2", "R1": "C9"},
        PARTS,
    )
    assert rows == [
        ["U1", "v", "p", "1.0000mm", "-2.0000mm", "270", "Top"],
        ["U2", "v", "p", "0.0000mm", "0.0000mm", "90", "Bottom"],
        ["R1", "v", "p", "0.0000mm", "0.0000mm", "90", "Top"],
    ]
    assert placed == {"U1", "U2", "R1"}


@pytest.mark.parametrize(
    ("rot", "x", "y"),
    [(0, "10.0000mm", "21.5000mm"), (90, "8.5000mm", "20.0000mm"), (180, "10.0000mm", "18.5000mm")],
)
def test_cpl_offset_turns_with_the_part(rot: float, x: str, y: str) -> None:
    rows, _ = jlcpcb_cpl([place("J1", 10, 20, rot)], {"J1": "C3"}, PARTS)
    assert rows[0][3:5] == [x, y]


def test_cpl_skips_parts_not_in_bom() -> None:
    rows, placed = jlcpcb_cpl([place("H1", 0, 0, 0)], {"U1": "C1"}, PARTS)
    assert rows == []
    assert placed == set()


def test_builtin_parts() -> None:
    parts = Parts.builtin()
    assert parts.rotation
    assert all(len(v) == 2 for v in parts.offset.values())
    assert not set(parts.unchecked) & (set(parts.rotation) | set(parts.offset))
