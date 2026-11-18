import pytest

from ee_tooling.profiles.jlcpcb import Parts, jlcpcb_bom, jlcpcb_cpl

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
