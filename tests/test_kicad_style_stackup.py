from ee_tooling.kicad_style_stackup import HEADER, check_stackup
from ee_tooling.sexpr import parse

# A two-layer stackup as KiCad 10 writes it, with the rows its stackup
# table gives it: (layer, type, material, thickness, color, Er, loss tangent).
SETUP = (
    "(setup (stackup"
    ' (layer "F.SilkS" (type "Top Silk Screen"))'
    ' (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))'
    ' (layer "F.Cu" (type "copper") (thickness 0.035))'
    ' (layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4")'
    " (epsilon_r 4.5) (loss_tangent 0.02))"
    ' (layer "B.Cu" (type "copper") (thickness 0.035))'
    ' (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))'
    ' (layer "B.SilkS" (type "Bottom Silk Screen"))'
    ' (copper_finish "HAL lead-free")))'
)
ROWS = [
    ["F.Silkscreen", "Top Silk Screen", "Not specified", "Not specified", "", "1", "0"],
    ["F.Mask", "Top Solder Mask", "Not specified", "0.01 mm", "", "3.3", "0"],
    ["F.Cu", "Copper", "Not specified", "0.035 mm", "", "1", "0"],
    ["Dielectric 1", "Core", "FR4", "1.51 mm", "", "4.5", "0.02"],
    ["B.Cu", "Copper", "Not specified", "0.035 mm", "", "1", "0"],
    ["B.Mask", "Bottom Solder Mask", "Not specified", "0.01 mm", "", "3.3", "0"],
    ["B.Silkscreen", "Bottom Silk Screen", "Not specified", "Not specified", "", "1", "0"],
]


def table(rows: list[list[str]], layer: str = "Dwgs.User", header: tuple[str, ...] = HEADER) -> str:
    """A stackup table as KiCad 10 writes it (pcb_io_kicad_sexpr.cpp)."""
    cells = " ".join(
        f'(table_cell "{text}" (start 0 0) (end 1 1))'
        for row in [list(header), *rows]
        for text in row
    )
    return (
        f'(table (column_count {len(header)}) (uuid "u") (layer "{layer}")'
        " (border (external yes) (header yes)) (separators (rows yes) (cols yes))"
        f" (cells {cells}))"
    )


def check(*items: str) -> list[str]:
    checked = check_stackup(parse(f"(kicad_pcb (version 20260206) {' '.join(items)})"))
    return [str(f) for f in checked.findings]


def test_matching_table() -> None:
    assert check(SETUP, table(ROWS)) == []


def test_mils() -> None:
    rows = [list(r) for r in ROWS]
    rows[3][3] = "59.45 mils"
    assert check(SETUP, table(rows)) == []


def test_no_stackup() -> None:
    assert check(table(ROWS)) == ["no physical stackup (Board Setup): no stackup table to check"]


def test_no_table() -> None:
    assert check(SETUP) == ["no stackup table (Place > Add Stackup Table) on User.Drawings"]


def test_old_group() -> None:
    assert check(SETUP, '(group "group-boardStackUp" (uuid "g") (members "a"))') == [
        "no stackup table (Place > Add Stackup Table) on User.Drawings"
        " (its group-boardStackUp predates KiCad 10's table: replace it)"
    ]


def test_other_table_ignored() -> None:
    other = table([["a", "b"]], header=("Rev", "Date"))
    assert check(SETUP, other) == ["no stackup table (Place > Add Stackup Table) on User.Drawings"]


def test_wrong_layer() -> None:
    assert check(SETUP, table(ROWS, layer="F.Fab")) == ["stackup table on F.Fab, not User.Drawings"]


def test_rows_changed() -> None:
    assert check(SETUP, table(ROWS[:-1])) == [
        "stackup table: 6 rows, the stackup 7 layers; out of date: replace it"
    ]


def test_values_changed() -> None:
    rows = [list(r) for r in ROWS]
    rows[3][2:7] = ["FR408", "1.6 mm", "", "4.5", "Not specified"]
    rows[2][3] = "0.07 mm"
    assert check(SETUP, table(rows)) == [
        "stackup table: F.Cu: thickness '0.07 mm', not 0.035; out of date: replace it",
        "stackup table: dielectric 1: material 'FR408', not 'FR4'; out of date: replace it",
        "stackup table: dielectric 1: thickness '1.6 mm', not 1.51; out of date: replace it",
        "stackup table: dielectric 1: loss tangent 'Not specified', not 0.02;"
        " out of date: replace it",
    ]
