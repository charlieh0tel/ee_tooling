import json
from pathlib import Path
from typing import Any

import pytest

from ee_tooling.kicad_style_netclass import (
    ProjectError,
    check_netclasses,
    check_project,
    matcher,
    net_settings,
    pairs,
)
from ee_tooling.sexpr import Expression, parse


def project(
    classes: tuple[str, ...] = (),
    patterns: tuple[tuple[str, str], ...] = (),
    assignments: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """A .kicad_pro's JSON, with patterns as (pattern, class)."""
    return {
        "meta": {"filename": "p.kicad_pro", "version": 3},
        "net_settings": {
            "meta": {"version": 5},
            "classes": [{"name": n} for n in ("Default", *classes)],
            "netclass_assignments": assignments or {},
            "netclass_patterns": [{"netclass": c, "pattern": p} for p, c in patterns],
        },
    }


def pad(number: str, net: str, pin_type: str | None = None) -> str:
    kind = f' (pintype "{pin_type}")' if pin_type else ""
    return f'(pad "{number}" smd rect (at 0 0) (net "{net}"){kind})'


def footprint(reference: str, *pads: str) -> str:
    return f'(footprint "Lib:FP" (property "Reference" "{reference}") {" ".join(pads)})'


def board(*footprints: str) -> Expression:
    return parse(f"(kicad_pcb (version 20260206) {' '.join(footprints)})")


def library(name: str, *pins: tuple[str, str], power: str | None = None) -> str:
    """A lib_symbols entry with pins as (electrical type, number), in a unit."""
    flag = f"({power})" if power else ""
    body = " ".join(f'(pin {t} line (at 0 0 0) (length 2.54) (number "{n}"))' for t, n in pins)
    return f'(symbol "{name}" {flag} (symbol "{name.split(":")[-1]}_1_1" {body}))'


def symbol(lib_id: str, reference: str, value: str = "", *instances: str) -> str:
    paths = " ".join(f'(path "/x" (reference "{r}") (unit 1))' for r in instances)
    return (
        f'(symbol (lib_id "{lib_id}") (at 0 0 0) (unit 1)'
        f' (property "Reference" "{reference}") (property "Value" "{value}")'
        f' (instances (project "p" {paths})))'
    )


def schematic(libraries: list[str], symbols: list[str], extra: str = "") -> Expression:
    return parse(
        f"(kicad_sch (version 20250114) (lib_symbols {' '.join(libraries)})"
        f" {' '.join(symbols)} {extra})"
    )


@pytest.mark.parametrize(
    ("pattern", "name", "matches"),
    [
        ("GND", "GND", True),
        ("GND", "AGND", False),
        ("GND", "GNDA", False),
        ("vcc", "VCC", False),  # case sensitive
        ("+3V3", "+3V3", True),  # no regex, but a wildcard
        ("+3V3", "x+3V3", False),
        ("/D+", "/D+", True),  # the wildcard
        ("/D+", "/D", True),  # the regex: one or more D
        ("/D+", "/DD", True),
        ("/D+", "/D++", False),
        ("Net-(J2-PWR)", "Net-(J2-PWR)", True),
        ("Net-(J2-PWR)", "Net-J2-PWR", True),  # the regex's group
        ("osc*", "osc1", True),
        ("osc*", "xosc1", False),
        ("*clk", "/a/clk", True),
        ("in\\d+", "in12", True),
        ("in\\d+", "in", False),
        ("x?y", "xy", True),  # the regex
        ("x?y", "xay", True),  # the wildcard
        ("^ab", "ab", True),
        ("^ab", "abc", False),  # $ is added
        ("ab.*$", "abc", True),
        ("[QR]S", "QS", True),
        ("[QR]S", "[QR]S", True),
        ("[QR]S", "S", False),
        ("(", "(", True),  # no regex, but a wildcard
    ],
)
def test_matcher(pattern: str, name: str, matches: bool) -> None:
    assert matcher(pattern)(name) is matches


def test_classes_of_composite() -> None:
    settings = net_settings(
        project(
            ("A", "B", "C"),
            (("/D-", "B"), ("/D*", "C"), ("X", "A")),
            {"/D-": ["A", "Default"], "Y": ["Nope"]},
        )
    )
    assert settings.classes_of("/D-") == {"A", "B", "C"}
    assert settings.classes_of("Y") == {"Nope"}
    assert settings.classes_of("Z") == set()


def test_no_net_settings() -> None:
    with pytest.raises(ProjectError):
        check_netclasses({"meta": {}}, None, [])


def test_not_a_board() -> None:
    with pytest.raises(ProjectError):
        check_netclasses(project(), parse("(kicad_sch)"), [])


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("90Z_DIFF", False),
        ("50Z_SE", False),
        ("100.5Z_DIFF", False),
        ("Power", False),
        ("Sense", False),
        ("DDR4_CMD", False),
        ("FPGA_HP", False),
        ("USB_90Z", True),
        ("90ohm", True),
        ("90Ohm-diff_CSI", True),
        ("zse_50r", True),
        ("USB_DIFF", True),
        ("CLK_SE", True),
        ("90Z_diff", True),
    ],
)
def test_impedance_class_names(name: str, bad: bool) -> None:
    problems = check_netclasses(project((name,)), None, [])
    assert problems == (
        [f"net class {name}: named for an impedance, not <ohms>Z_DIFF or <ohms>Z_SE"] if bad else []
    )


def test_undefined_class() -> None:
    problems = check_netclasses(
        project(("A",), (("X", "Gone"),), {"Y": ["Lost", "A", "Default"]}), None, []
    )
    assert problems == [
        "net class Gone: assigned or in a pattern, but not defined",
        "net class Lost: assigned or in a pattern, but not defined",
    ]


def test_power_by_name() -> None:
    nets = ["GND", "AGND", "GNDA", "+3V3", "+5V", "-12V", "/sub/VDD_USB", "VBUS", "VCC_RF"]
    signals = ["/SDA", "/D+", "VREF", "/V_SENSE", "/SENSE+"]
    b = board(footprint("J1", *(pad(str(n), x) for n, x in enumerate(nets + signals))))
    problems = check_netclasses(project(), b, [])
    assert problems == [f"power net {n}: no net class" for n in sorted(nets)]


def test_power_classed_by_pattern_or_assignment() -> None:
    b = board(footprint("J1", pad("1", "GND"), pad("2", "+5V"), pad("3", "VBUS")))
    settings = project(("Power",), (("GND", "Power"),), {"+5V": ["Power"], "VBUS": ["Default"]})
    assert check_netclasses(settings, b, []) == ["power net VBUS: no net class"]


def test_power_in_undefined_class() -> None:
    b = board(footprint("J1", pad("1", "GND")))
    assert check_netclasses(project(), b, []) == ["power net GND: no net class"]
    problems = check_netclasses(project((), (("GND", "Nope"),)), b, [])
    assert "power net GND: no net class" in problems


def test_power_by_pad_pin_type() -> None:
    b = board(
        footprint(
            "U1",
            pad("1", "Net-(U1-VREG)", "power_out"),
            pad("2", "Net-(U1-AVDD)", "power_in+no_connect"),
            pad("3", "Net-(U1-IO)", "bidirectional"),
        )
    )
    assert check_netclasses(project(), b, []) == [
        "power net Net-(U1-AVDD): no net class",
        "power net Net-(U1-VREG): no net class",
    ]


def test_power_by_schematic_pin_type() -> None:
    b = board(
        footprint("U1", pad("8", "Net-(U1-VCC)"), pad("1", "Net-(U1-A)")),
        footprint("U2", pad("8", "Net-(U2-VCC)")),
    )
    sheet = schematic(
        [library("Lib:IC", ("power_in", "8"), ("input", "1"))],
        [symbol("Lib:IC", "U?", "IC", "U1", "U2")],  # one sheet, two instances
    )
    assert check_netclasses(project(), b, [sheet]) == [
        "power net Net-(U1-VCC): no net class",
        "power net Net-(U2-VCC): no net class",
    ]


def test_power_by_symbol() -> None:
    b = board(footprint("J1", pad("1", "HT"), pad("2", "/sheet/AUX"), pad("3", "/AUX2")))
    sheet = schematic(
        [
            library("power:HT", ("power_in", "1"), power="power"),
            library("power:AUX", ("power_in", "1"), power="power local"),
        ],
        [symbol("power:HT", "#PWR01", "HT"), symbol("power:AUX", "#PWR02", "AUX")],
    )
    assert check_netclasses(project(), b, [sheet]) == [
        "power net /sheet/AUX: no net class",
        "power net HT: no net class",
    ]


def test_lib_name_preferred() -> None:
    sheet = schematic(
        [library("IC_1", ("power_in", "8"))],
        ['(symbol (lib_name "IC_1") (lib_id "Lib:IC") (property "Reference" "U1"))'],
    )
    b = board(footprint("U1", pad("8", "Net-(U1-VCC)")))
    assert check_netclasses(project(), b, [sheet]) == ["power net Net-(U1-VCC): no net class"]


def test_missing_library_symbol() -> None:
    sheet = schematic([], [symbol("Lib:IC", "U1")])
    with pytest.raises(ProjectError):
        check_netclasses(project(), board(), [sheet])


def test_unconnected_and_old_net_syntax() -> None:
    b = parse(
        "(kicad_pcb (version 20241229)"
        ' (footprint "L:F" (property "Reference" "J1")'
        ' (pad "1" smd rect (net 1 "GND"))'
        ' (pad "2" smd rect (net 2 "unconnected-(U1-VCC-Pad2)") (pintype "power_in"))'
        ' (pad "3" smd rect)))'
    )
    assert check_netclasses(project(), b, []) == ["power net GND: no net class"]


@pytest.mark.parametrize(
    ("nets", "found"),
    [
        ({"/D+", "/D-"}, [("/D+", "/D-")]),
        ({"/D+"}, []),
        ({"CLK_P", "CLK_N"}, [("CLK_P", "CLK_N")]),
        ({"TXP1", "TXN1"}, [("TXP1", "TXN1")]),
        ({"D+_2", "D-_2"}, [("D+_2", "D-_2")]),
        ({"USB_DP", "USB_DM"}, [("USB_DP", "USB_DM")]),
        ({"USB_DP", "USB_DN"}, [("USB_DP", "USB_DN")]),
        ({"clk_p", "clk_n"}, []),  # KiCad's suffixes are upper case
        ({"A_1", "B_1"}, []),
        ({"Net-(U1-D+)", "Net-(U1-D-)"}, []),
    ],
)
def test_pairs(nets: set[str], found: list[tuple[str, str]]) -> None:
    assert pairs(nets) == found


def pair_board(*nets: str) -> Expression:
    return board(footprint("J1", *(pad(str(n), x) for n, x in enumerate(nets))))


def test_pair_in_diff_class() -> None:
    b = pair_board("/D+", "/D-")
    assert check_netclasses(project(("90Z_DIFF",), (("/D*", "90Z_DIFF"),)), b, []) == []


def test_pair_in_no_diff_class() -> None:
    b = pair_board("/D+", "/D-", "USB_DP", "USB_DM")
    problems = check_netclasses(project(("50Z_SE",), (("/D*", "50Z_SE"),)), b, [])
    assert problems == [
        "differential pair /D+ and /D-: in no <ohms>Z_DIFF net class",
        "differential pair USB_DP and USB_DM: in no <ohms>Z_DIFF net class",
    ]


@pytest.mark.parametrize("name", ["/SENSE", "ISNS_", "KELVIN", "/I_sense"])
def test_kelvin_pair_needs_no_class(name: str) -> None:
    b = pair_board(f"{name}+", f"{name}-")
    assert check_netclasses(project(), b, []) == []


def test_pair_in_different_diff_classes() -> None:
    b = pair_board("/D+", "/D-")
    settings = project(("90Z_DIFF", "100Z_DIFF"), (("/D+", "90Z_DIFF"),), {"/D-": ["100Z_DIFF"]})
    assert check_netclasses(settings, b, []) == [
        "differential pair /D+ and /D-: in different net classes 100Z_DIFF, 90Z_DIFF"
    ]


def test_half_a_pair_classed() -> None:
    b = pair_board("/D+", "/D-")
    settings = project(("90Z_DIFF",), (), {"/D+": ["90Z_DIFF"]})
    assert check_netclasses(settings, b, []) == [
        "differential pair /D+ and /D-: in different net classes 90Z_DIFF"
    ]


def test_power_is_not_a_pair() -> None:
    b = board(footprint("U1", pad("1", "VP", "power_in"), pad("2", "VN", "power_in")))
    settings = project(("Power",), (("V?", "Power"),))
    assert check_netclasses(settings, b, []) == []


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_check_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pro = write(tmp_path / "b.kicad_pro", json.dumps(project(("USB_90Z",))))
    write(tmp_path / "b.kicad_pcb", f"(kicad_pcb {footprint('U1', pad('8', 'Net-(U1-VCC)'))})")
    write(
        tmp_path / "b.kicad_sch",
        '(kicad_sch (lib_symbols) (sheet (property "Sheetname" "s")'
        ' (property "Sheetfile" "sub/s.kicad_sch"))'
        ' (sheet (property "Sheetfile" "gone.kicad_sch")))',
    )
    write(
        tmp_path / "sub" / "s.kicad_sch",
        f"(kicad_sch (lib_symbols {library('Lib:IC', ('power_in', '8'))})"
        f" {symbol('Lib:IC', 'U1')}"
        ' (sheet (property "Sheetfile" "s.kicad_sch")))',  # itself again, beside it
    )
    assert check_project(pro) == [
        "net class USB_90Z: named for an impedance, not <ohms>Z_DIFF or <ohms>Z_SE",
        "power net Net-(U1-VCC): no net class",
    ]
    assert "gone.kicad_sch not found" in capsys.readouterr().err


def test_check_project_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pro = write(tmp_path / "b.kicad_pro", json.dumps(project(("90ohm",))))
    assert check_project(pro) == [
        "net class 90ohm: named for an impedance, not <ohms>Z_DIFF or <ohms>Z_SE"
    ]
    err = capsys.readouterr().err
    assert "no b.kicad_pcb, nets not checked" in err
    assert "no b.kicad_sch" in err


def test_check_project_not_json_object(tmp_path: Path) -> None:
    pro = write(tmp_path / "b.kicad_pro", "[]")
    with pytest.raises(ProjectError):
        check_project(pro)
