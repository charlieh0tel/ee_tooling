import subprocess
import sys
from pathlib import Path

import pytest

from ee_tooling.kicad_style_check import Point, SchematicError, check, read, transform
from ee_tooling.sexpr import parse

# A two-pin part with pins 5.08 mm above (1) and below (2) its origin; a
# ground symbol whose hidden pin is at its origin; a two-unit part whose
# units have different pins, sharing a supply pin in unit 0.
LIBRARY = """(lib_symbols
	(symbol "Device:R"
		(symbol "R_1_1"
			(pin passive line (at 0 5.08 270) (length 2.54) (name "~") (number "1"))
			(pin passive line (at 0 -5.08 90) (length 2.54) (name "~") (number "2"))))
	(symbol "Device:C"
		(symbol "C_1_1"
			(pin passive line (at 0 5.08 270) (length 2.54) (name "~") (number "1"))
			(pin passive line (at 0 -5.08 90) (length 2.54) (name "~") (number "2"))))
	(symbol "power:GND" (power)
		(symbol "GND_1_1"
			(pin power_in line (at 0 0 270) (length 0) hide (name "GND") (number "1"))))
	(symbol "Dual:Gate"
		(symbol "Gate_0_1"
			(pin power_in line (at 0 10.16 270) (length 2.54) (name "V") (number "8")))
		(symbol "Gate_1_1" (pin input line (at -5.08 0 0) (length 2.54) (name "A") (number "1")))
		(symbol "Gate_2_1" (pin input line (at 5.08 0 180) (length 2.54) (name "B") (number "5"))
			(pin no_connect line (at 5.08 -2.54 180) (length 2.54) (hide yes)
				(name "NC") (number "6")))))
"""

BOUGHT = {"Manufacturer": "Yageo", "MPN": "RC0603", "LCSC": "C1"}


def symbol(
    lib_id: str,
    reference: str,
    x: float,
    y: float,
    rotation: int = 0,
    mirror: str | None = None,
    unit: int = 1,
    value: str = "10k",
    fields: dict[str, str] | None = None,
    in_bom: bool = True,
    dnp: bool = False,
) -> str:
    properties = {"Reference": reference, "Value": value, **(BOUGHT if fields is None else fields)}
    text = "".join(f' (property "{k}" "{v}" (at 0 0 0))' for k, v in properties.items())
    flip = f" (mirror {mirror})" if mirror else ""
    return (
        f'(symbol (lib_id "{lib_id}") (at {x} {y} {rotation}){flip} (unit {unit})'
        f" (in_bom {'yes' if in_bom else 'no'}) (dnp {'yes' if dnp else 'no'}){text})"
    )


def wire(a: Point, b: Point) -> str:
    return f"(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})))"


def junction(p: Point) -> str:
    return f"(junction (at {p[0]} {p[1]}) (diameter 0))"


def no_connect(p: Point) -> str:
    return f"(no_connect (at {p[0]} {p[1]}))"


def schematic(*items: str) -> str:
    return f"(kicad_sch (version 20250114) {LIBRARY} {' '.join(items)})"


# R1 and C1 side by side, tops joined by a wire, bottoms each wired down to
# a ground symbol: every rule kept.
CLEAN = (
    symbol("Device:R", "R1", 50.8, 50.8),
    symbol("Device:C", "C1", 76.2, 50.8, value="100nF 50V X7R"),
    symbol("power:GND", "#PWR01", 50.8, 60.96, fields={}, in_bom=False),
    symbol("power:GND", "#PWR02", 76.2, 60.96, fields={}, in_bom=False),
    wire((50.8, 45.72), (50.8, 40.64)),
    wire((76.2, 45.72), (76.2, 40.64)),
    wire((50.8, 40.64), (76.2, 40.64)),
    wire((50.8, 55.88), (50.8, 60.96)),
    wire((76.2, 55.88), (76.2, 60.96)),
)


def problems(*items: str, grid: float = 2.54) -> list[str]:
    return check(read(parse(schematic(*items))), grid)


def test_clean() -> None:
    assert problems(*CLEAN) == []


@pytest.mark.parametrize(
    ("added", "expected"),
    [
        (
            (wire((101.6, 101.6), (101.6, 102.6)),),
            "off the 2.54 mm grid: wire end at (101.6, 102.6)",
        ),
        (
            (symbol("Device:R", "R2", 50.8, 60.96),),
            "pins touching: R1 pin 2, R2 pin 1 at (50.8, 55.88)",
        ),
        (
            (symbol("power:GND", "#PWR03", 50.8, 55.88, fields={}, in_bom=False),),
            "power symbol on a pin: R1 pin 2, #PWR03 pin 1 at (50.8, 55.88)",
        ),
        (
            (wire((45.72, 55.88), (55.88, 55.88)),),
            "pin on a wire's run: R1 pin 2 at (50.8, 55.88)",
        ),
        ((symbol("Device:R", "R3", 127, 50.8),), "pin with no wire: R3 pin 1 at (127, 45.72)"),
        (
            (wire((50.8, 45.72), (45.72, 45.72)),),
            "wires join at a pin: R1 pin 1 at (50.8, 45.72)",
        ),
        (
            (wire((63.5, 40.64), (63.5, 35.56)),),
            "join with no junction at (63.5, 40.64)",
        ),
        ((junction((50.8, 40.64)),), "junction not at a join at (50.8, 40.64)"),
        ((no_connect((101.6, 101.6)),), "no-connect off a pin at (101.6, 101.6)"),
        ((wire((101.6, 101.6), (101.6, 101.6)),), "zero-length wire at (101.6, 101.6)"),
        (
            (wire((55.88, 40.64), (66.04, 40.64)),),
            "overlapping wires (50.8, 40.64)-(76.2, 40.64) and (55.88, 40.64)-(66.04, 40.64)",
        ),
    ],
)
def test_drawing_rules(added: tuple[str, ...], expected: str) -> None:
    assert expected in problems(*CLEAN, *added)


def test_pin_wire_shorter_than_grid() -> None:
    short = [*CLEAN[:4], wire((50.8, 45.72), (50.8, 44.45)), *CLEAN[5:]]
    assert "pin wire shorter than 2.54 mm: R1 pin 1 at (50.8, 45.72)" in problems(*short)


def test_junction_at_a_tee() -> None:
    tee = (wire((63.5, 40.64), (63.5, 35.56)), junction((63.5, 40.64)))
    assert not [p for p in problems(*CLEAN, *tee) if "junction" in p]


def test_bom_fields() -> None:
    items = [
        *CLEAN,
        symbol("Device:R", "R4", 127, 50.8, fields={"MPN": "x"}),
        symbol("Device:R", "R5", 152.4, 50.8, fields={}, dnp=True),
        symbol("Device:R", "R6", 177.8, 50.8, fields={}, in_bom=False),
    ]
    fields = [p for p in problems(*items) if ": no " in p]
    assert fields == ["R4: no Manufacturer, LCSC"]


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("100nF 50V X7R", True),
        ("10u 25V X5R", True),
        ("22p 50 V C0G", True),
        ("100uF 25V Al", True),
        ("10u 16V tantalum", True),
        ("470u 6.3V Polymer", True),
        ("100u 25V", False),
        ("10u 16V Tag", False),
        ("100n", False),
        ("100n X7R", False),
        ("100n 50V", False),
    ],
)
def test_capacitor_value(value: str, ok: bool) -> None:
    items = [
        symbol("Device:C", "C1", 76.2, 50.8, value=value)
        if s.startswith('(symbol (lib_id "Device:C"')
        else s
        for s in CLEAN
    ]
    found = [p for p in problems(*items) if p.startswith("C1:")]
    assert found == (
        [] if ok else [f"C1: value {value!r} does not show dielectric or type, and voltage"]
    )


# A library pin at (2.54, 5.08), y up, on a symbol at (100, 100): where it
# lands for each rotation and mirror, worked by hand from KiCad's rule
# (rotate counterclockwise, then mirror, in y-up axes; then flip y).
TRANSFORMS = [
    (0, None, (102.54, 94.92)),
    (90, None, (94.92, 97.46)),
    (180, None, (97.46, 105.08)),
    (270, None, (105.08, 102.54)),
    (0, "y", (97.46, 94.92)),
    (90, "y", (105.08, 97.46)),
    (180, "y", (102.54, 105.08)),
    (270, "y", (94.92, 102.54)),
    (0, "x", (102.54, 105.08)),
    (90, "x", (94.92, 102.54)),
    (180, "x", (97.46, 94.92)),
    (270, "x", (105.08, 97.46)),
]


@pytest.mark.parametrize(("rotation", "mirror", "expected"), TRANSFORMS)
def test_transform(rotation: int, mirror: str | None, expected: Point) -> None:
    assert transform((100.0, 100.0), rotation, mirror, (2.54, 5.08)) == expected


@pytest.mark.parametrize(("rotation", "mirror", "expected"), TRANSFORMS)
def test_instance_pins(rotation: int, mirror: str | None, expected: Point) -> None:
    library = LIBRARY.replace("(at 0 5.08 270)", "(at 2.54 5.08 270)", 1)
    text = f"(kicad_sch {library} {symbol('Device:R', 'R1', 100, 100, rotation, mirror)})"
    pins = {p.number: p.point for p in read(parse(text)).pins}
    assert pins["1"] == expected


def test_units() -> None:
    s = read(
        parse(
            schematic(
                symbol("Dual:Gate", "U1", 50.8, 50.8, unit=1),
                symbol("Dual:Gate", "U1", 101.6, 50.8, unit=2),
            )
        )
    )
    pins = sorted((p.number, p.point, p.hidden) for p in s.pins)
    assert pins == [
        ("1", (45.72, 50.8), False),
        ("5", (106.68, 50.8), False),
        ("6", (106.68, 53.34), True),
        ("8", (50.8, 40.64), False),
        ("8", (101.6, 40.64), False),
    ]
    assert list(s.parts) == ["U1"]


def test_hidden_pins() -> None:
    gate = (
        symbol("Dual:Gate", "U1", 101.6, 50.8, unit=2),
        wire((106.68, 50.8), (111.76, 50.8)),
        wire((101.6, 40.64), (101.6, 35.56)),
    )
    found = problems(*gate)
    assert not [p for p in found if "U1 pin 6" in p]
    with_nc = problems(*gate, no_connect((106.68, 53.34)))
    assert not [p for p in with_nc if "no-connect" in p]


def test_power_symbol_pin_counts_though_hidden() -> None:
    lone = symbol("power:GND", "#PWR09", 127, 127, fields={}, in_bom=False)
    assert "pin with no wire: #PWR09 pin 1 at (127, 127)" in problems(lone)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("(kicad_pcb)", "not a kicad_sch"),
        ('(kicad_sch (symbol (lib_id "x:y") (at 0 0 0)))', "no lib_symbols entry"),
        ("(kicad_sch (wire (pts (xy 0 0))))", "1 points"),
        ("(kicad_sch (junction))", "no \\(at\\)"),
    ],
)
def test_not_a_schematic(text: str, message: str) -> None:
    with pytest.raises(SchematicError, match=message):
        read(parse(text))


def run(*files: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from ee_tooling.kicad_style_check import main; main()",
            *map(str, files),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_command_exit_status(tmp_path: Path) -> None:
    clean = tmp_path / "clean.kicad_sch"
    clean.write_text(schematic(*CLEAN))
    bad = tmp_path / "bad.kicad_sch"
    bad.write_text(schematic(*CLEAN, junction((50.8, 40.64))))
    broken = tmp_path / "broken.kicad_sch"
    broken.write_text("(kicad_sch (wire")
    assert run(clean).returncode == 0
    r = run(bad)
    assert r.returncode == 1
    assert r.stdout == f"{bad}: junction not at a join at (50.8, 40.64)\n"
    r = run(clean, broken)
    assert r.returncode == 2
    assert f"{broken}: cannot check:" in r.stderr
    assert run(tmp_path / "missing.kicad_sch").returncode == 2
