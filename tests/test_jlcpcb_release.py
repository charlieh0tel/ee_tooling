from pathlib import Path

import pytest

from ee_tooling.jlcpcb_release import Board, Parts, jlcpcb_bom, jlcpcb_cpl, load_config, revision

CONFIG = """
[[board]]
name = "main"
directory = "hardware/main"
copper = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
production = "production"
sources = ["hardware/main/main.kicad_sym", "docs"]

[[board]]
name = "jig"
directory = "hardware/jig"
copper = ["F.Cu", "B.Cu"]
production = "production/jig"
"""

PARTS = Parts(
    rotation={"C1": -90.0, "C2": 180.0},
    offset={"C3": (0.0, 1.5)},
    unchecked={"C4": "a new part"},
)


def place(ref: str, x: float, y: float, rot: float, side: str = "top") -> dict[str, str]:
    return {
        "Ref": ref,
        "Val": "v",
        "Package": "p",
        "PosX": str(x),
        "PosY": str(y),
        "Rot": str(rot),
        "Side": side,
    }


def test_config_boards_in_order() -> None:
    boards = load_config(CONFIG)
    assert list(boards) == ["main", "jig"]
    main = boards["main"]
    assert main.pcb == Path("hardware/main/main.kicad_pcb")
    assert main.sch == Path("hardware/main/main.kicad_sch")
    assert main.layers[:4] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert "Edge.Cuts" in main.layers
    assert boards["jig"].sources == ()


def test_config_without_boards() -> None:
    with pytest.raises(ValueError, match="no \\[\\[board\\]\\]"):
        load_config("")


def test_config_missing_key() -> None:
    with pytest.raises(KeyError):
        load_config('[[board]]\nname = "x"\n')


def test_source_paths_only_present_project_files(tmp_path: Path) -> None:
    d = tmp_path / "hardware/main"
    (d / "main.pretty").mkdir(parents=True)
    for f in ("main.kicad_pcb", "main.kicad_sch", "main.kicad_pro", "fp-lib-table"):
        (d / f).touch()
    b = load_config(CONFIG)["main"]
    assert b.source_paths(tmp_path) == [
        Path("hardware/main/main.kicad_pcb"),
        Path("hardware/main/main.kicad_sch"),
        Path("hardware/main/main.kicad_pro"),
        Path("hardware/main/main.pretty"),
        Path("hardware/main/fp-lib-table"),
        Path("hardware/main/main.kicad_sym"),
        Path("docs"),
    ]


def test_revision() -> None:
    pcb = '(kicad_pcb (title_block (title "x") (date "d") (rev "1.2.3")) (net 0 ""))'
    assert revision(pcb) == "1.2.3"
    assert revision('(kicad_pcb (rev "9"))') is None


def test_bom() -> None:
    rows, refs, missing = jlcpcb_bom(
        [
            {
                "Comment": "100n",
                "Designator": "C1,C2",
                "Footprint": "lib:C_0402",
                "LCSC Part #": "C1525",
            },
            {"Comment": "TP", "Designator": "TP1", "Footprint": "TP", "LCSC Part #": ""},
        ]
    )
    assert rows == [["100n", "C1,C2", "C_0402", "C1525"], ["TP", "TP1", "TP", ""]]
    assert refs == {"C1": "C1525", "C2": "C1525", "TP1": ""}
    assert missing == ["TP1"]


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


def test_board_is_frozen() -> None:
    b = Board("x", Path("d"), ("F.Cu",), Path("p"), ())
    with pytest.raises(AttributeError):
        b.name = "y"  # type: ignore[misc]
