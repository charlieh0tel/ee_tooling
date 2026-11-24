import csv
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from test_fabrication import FAB

from ee_tooling.fab_release import Board, Release
from ee_tooling.profiles.jlcpcb import Corrections, Jlcpcb, Options, jlcpcb_bom, jlcpcb_cpl
from ee_tooling.profiles.profile import Part, Placement

CORRECTIONS = Corrections(
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
            Part("U2", "100n", "lib:C_0402", "C1525", dnp=True),
            Part("TP2", "TP", "TP", "", dnp=True),
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
    board = Board(sch.stem, Path("."), Path("production"), (), "jlcpcb", FAB)
    rows, _, _ = jlcpcb_bom(Release(sch.parent, board, tmp_path).parts())
    assert len(rows) > 1
    assert rows == kicad


def test_cpl_rotation_and_layer() -> None:
    rows, placed = jlcpcb_cpl(
        [place("U1", 1, -2, 0), place("U2", 0, 0, 270, "bottom"), place("R1", 0, 0, 90)],
        {"U1": "C1", "U2": "C2", "R1": "C9"},
        CORRECTIONS,
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
    rows, _ = jlcpcb_cpl([place("J1", 10, 20, rot)], {"J1": "C3"}, CORRECTIONS)
    assert rows[0][3:5] == [x, y]


BOTTOM = Corrections(
    rotation={"C90": 90.0, "C-90": -90.0},
    offset={"C3": (0.0, 1.5), "C5": (1.0, 2.0)},
    unchecked={},
)


@pytest.mark.parametrize(
    ("lcsc", "rot", "row"),
    [
        # A rotation correction turns a bottom-side part the other way.
        ("C90", 0, ["10.0000mm", "20.0000mm", "270"]),
        ("C90", 90, ["10.0000mm", "20.0000mm", "0"]),
        ("C-90", 0, ["10.0000mm", "20.0000mm", "90"]),
        ("C-90", 270, ["10.0000mm", "20.0000mm", "0"]),
        # An offset's y is mirrored, then turned with the part.
        ("C3", 0, ["10.0000mm", "18.5000mm", "0"]),
        ("C3", 90, ["11.5000mm", "20.0000mm", "90"]),
        ("C5", 90, ["12.0000mm", "21.0000mm", "90"]),
        ("C5", 30, ["11.8660mm", "18.7679mm", "30"]),
    ],
)
def test_cpl_bottom_corrections_mirror(lcsc: str, rot: float, row: list[str]) -> None:
    """KiCad's bottom-side footprint is its library footprint mirrored in y,
    then turned by its rotation (checked against pcbnew: a pad at library
    (1, -2), y up, flipped and turned 90 deg, is at (-2, 1))."""
    rows, _ = jlcpcb_cpl([place("J1", 10, 20, rot, "bottom")], {"J1": lcsc}, BOTTOM)
    assert rows[0][3:6] == row


def test_cpl_skips_parts_not_in_bom() -> None:
    rows, placed = jlcpcb_cpl([place("H1", 0, 0, 0)], {"U1": "C1"}, CORRECTIONS)
    assert rows == []
    assert placed == set()


def test_builtin_corrections() -> None:
    corrections = Corrections.builtin()
    assert corrections.rotation
    assert all(len(v) == 2 for v in corrections.offset.values())
    assert not set(corrections.unchecked) & (set(corrections.rotation) | set(corrections.offset))


@pytest.mark.parametrize(
    ("require_lcsc", "lcsc", "refusals"),
    [
        (False, "", []),
        (True, "C1525", []),
        (True, "", ["no LCSC part number for R1,R2 (require_lcsc)"]),
    ],
)
def test_check_require_lcsc(require_lcsc: bool, lcsc: str, refusals: list[str]) -> None:
    parts = [
        Part("C1", "100n", "lib:C", "C1525"),
        *(Part(r, "0R", "lib:R", lcsc) for r in ("R1", "R2")),
    ]
    parts.append(Part("R3", "0R", "lib:R", "", dnp=True))
    placements = [place(r, 0, 0, 0) for r in ("C1", "R1", "R2")]
    assert Jlcpcb(CORRECTIONS).check(parts, placements, Options(require_lcsc)) == refusals


def test_check_not_placed() -> None:
    """A fitted part in the BOM and not placed refuses the release, named;
    a DNP part, or a placement not in the BOM, does not."""
    parts = [
        Part("C1", "100n", "lib:C", "C1525"),
        Part("R2", "0R", "lib:R", ""),
        Part("R1", "0R", "lib:R", ""),
        Part("R3", "0R", "lib:R", "", dnp=True),
    ]
    profile = Jlcpcb(CORRECTIONS)
    assert profile.check(parts, [place("C1", 0, 0, 0), place("H1", 0, 0, 0)], Options(True)) == [
        "no LCSC part number for R2,R1 (require_lcsc)",
        "in the BOM but not placed: R1, R2",
    ]
    placed = [place(r, 0, 0, 0) for r in ("C1", "R1", "R2")]
    assert profile.check(parts, placed, Options()) == []


def test_options() -> None:
    profile = Jlcpcb(CORRECTIONS)
    assert profile.options({}) == Options(require_lcsc=False)
    assert profile.options({"require_lcsc": True}) == Options(require_lcsc=True)


def test_warnings() -> None:
    """A fitted part with no LCSC number, unless require_lcsc refuses it;
    a part with no correction known."""
    parts = [Part("C1", "1u", "lib:C", "C4"), Part("R1", "0R", "lib:R", "")]
    profile = Jlcpcb(CORRECTIONS)
    check = "check a new part (C4) in JLCPCB's placement preview: no correction known"
    assert profile.warnings(parts, [], Options()) == ["no LCSC part number for R1", check]
    assert profile.warnings(parts, [], Options(require_lcsc=True)) == [check]


@pytest.mark.parametrize(
    "text",
    ["[rotation]\n[rotation]\n", '[rotation]\nC1 = "x"\n', "[offset]\nC1 = 1\n", "rotation = 1\n"],
)
def test_corrections_refused(text: str) -> None:
    """A parts.toml that does not load: ValueError, never a traceback."""
    with pytest.raises(ValueError):
        Corrections.load(text)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[rotations]\nC1 = 90\n", r"unknown table 'rotations'"),
        ("[rotation]\nC1 = true\n", r"rotation C1: True is not a finite number"),
        ("[rotation]\nC1 = nan\n", r"rotation C1: nan is not a finite number"),
        ("[rotation]\nC1 = inf\n", r"rotation C1: inf is not a finite number"),
        ('[offset]\nC1 = "12"\n', r"offset C1: '12' is not two numbers"),
        ("[offset]\nC1 = [1.0, 2.0, 3.0]\n", r"offset C1: \[1.0, 2.0, 3.0\] is not two numbers"),
        ("[offset]\nC1 = [1.0]\n", r"offset C1: \[1.0\] is not two numbers"),
        ("[offset]\nC1 = [1.0, false]\n", r"offset C1: False is not a finite number"),
        ("[offset]\nC1 = [1.0, -inf]\n", r"offset C1: -inf is not a finite number"),
        ("[unchecked]\nC1 = 1\n", r"unchecked C1: 1 is not a description"),
    ],
)
def test_corrections_malformed(text: str, error: str) -> None:
    """A misspelled table, or a value not of its form, is refused, naming it."""
    with pytest.raises(ValueError, match=error):
        Corrections.load(text)


def test_corrections_load() -> None:
    corrections = Corrections.load(
        '[rotation]\nC1 = -90\nC2 = 45.5\n[offset]\nC3 = [0, 1.5]\n[unchecked]\nC4 = "a part"\n'
    )
    assert corrections == Corrections(
        rotation={"C1": -90.0, "C2": 45.5}, offset={"C3": (0.0, 1.5)}, unchecked={"C4": "a part"}
    )
    assert Corrections.load("") == Corrections({}, {}, {})


def test_builtin_corrections_name_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(cls: type[Corrections], text: str) -> Corrections:
        raise ValueError("bad")

    monkeypatch.setattr(Corrections, "load", classmethod(bad))
    with pytest.raises(ValueError, match=r"parts.toml: bad$"):
        Corrections.builtin()
