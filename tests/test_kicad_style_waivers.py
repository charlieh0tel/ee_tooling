import subprocess
import sys
from pathlib import Path

import pytest

from ee_tooling.kicad_style_finding import Finding, found
from ee_tooling.kicad_style_waivers import Waiver, WaiverError, load, matches, waive

TRACE = """
[[waiver]]
rule = "trace-pad-to-pad"
net = "GND"
pads = ["J8 pad 3", "J7 pad 3"]
reason = "GND return across the breakaway tab."
"""

# A 4 x 1 mm board, under the stitching gap round, with two through-hole
# GND pins traced pad to pad on F.Cu.
BOARD = """(kicad_pcb (version 20260206)
 (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal))
 (gr_rect (start 0 0) (end 4 1) (layer "Edge.Cuts"))
 (zone (net "GND") (locked yes) (layer "In1.Cu")
  (polygon (pts (xy -1 -1) (xy 5 -1) (xy 5 2) (xy -1 2))))
 (footprint "Lib:Part" (layer "F.Cu") (at 1 0.5) (property "Reference" "J7")
  (pad "3" thru_hole circle (at 0 0) (size 0.6 0.6) (drill 0.3) (layers "*.Cu") (net "GND")))
 (footprint "Lib:Part" (layer "F.Cu") (at 3 0.5) (property "Reference" "J8")
  (pad "3" thru_hole circle (at 0 0) (size 0.6 0.6) (drill 0.3) (layers "*.Cu") (net "GND")))
 (segment (start 3 0.5) (end 1 0.5) (width 0.25) (layer "F.Cu") (net "GND")))
"""
FINDING = "GND: trace from J7 pad 3 to J8 pad 3 on F.Cu [trace-pad-to-pad]"


def trace(*pads: str) -> Finding:
    return found("trace-pad-to-pad", "t", net="GND", pads=frozenset(pads))


def test_load() -> None:
    (w,) = load(TRACE)
    assert w == Waiver(
        "trace-pad-to-pad",
        (("net", "GND"), ("pads", frozenset({"J7 pad 3", "J8 pad 3"}))),
        "GND return across the breakaway tab.",
    )
    assert str(w) == "trace-pad-to-pad net=GND; pads=J7 pad 3, J8 pad 3"


def test_load_lists_waive_each() -> None:
    text = """
[[waiver]]
rule = "stub-direction"
net = "GND"
pad = ["R1 pad 2", "U5 pad 3"]
reason = "Diagonal stubs to clear the 0402 row."

[[waiver]]
rule = "via-shared"
net = ["GND", "AGND"]
pads = [["R1 pad 2", "Q1 pad 2"], ["R2 pad 2", "Q2 pad 2"]]
reason = "r"

[[waiver]]
rule = "off-grid"
at = [[1.27, 2.54], [3, 4]]
reason = "r"

[[waiver]]
rule = "no-outline"
reason = "r"
"""
    waivers = load(text)
    assert [w.rule for w in waivers] == ["stub-direction"] * 2 + ["via-shared"] * 4 + [
        "off-grid"
    ] * 2 + ["no-outline"]
    assert waivers[1].subject == (("net", "GND"), ("pad", "U5 pad 3"))
    assert waivers[6].subject == (("at", (1.27, 2.54)),)
    assert waivers[8].subject == ()
    (one,) = load('[[waiver]]\nrule = "off-grid"\nat = [1, 2]\nreason = "r"\n')
    assert one.subject == (("at", (1.0, 2.0)),)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[[waiver]\n", "Expected"),
        ('rule = "x"\n', "unexpected rule: only"),
        ('waiver = "x"\n', "not an array of tables"),
        ('[[waiver]]\nrule = "nope"\nreason = "r"\n', "unknown rule 'nope'"),
        ('[[waiver]]\nrule = "no-outline"\n', r"waiver 1 \(no-outline\): no reason"),
        ('[[waiver]]\nrule = "no-outline"\nreason = "  "\n', "no reason"),
        (
            '[[waiver]]\nrule = "trace-pad-to-pad"\nnet = "GND"\nreason = "r"\n',
            "missing pads; unexpected none",
        ),
        (
            '[[waiver]]\nrule = "no-outline"\nnet = "GND"\nreason = "r"\n',
            "missing none; unexpected net",
        ),
        (
            '[[waiver]]\nrule = "trace-pad-to-pad"\nnet = "GND"\npads = "J7 pad 3"\nreason = "r"\n',
            "pads: not a list of names",
        ),
        ('[[waiver]]\nrule = "off-grid"\nat = [1]\nreason = "r"\n', r"at: not \[x, y\]"),
        ('[[waiver]]\nrule = "power-net-class"\nnet = 3\nreason = "r"\n', "net: not a name"),
    ],
)
def test_load_refuses(text: str, message: str) -> None:
    with pytest.raises(WaiverError, match=message):
        load(text)


def test_set_matches_as_a_set() -> None:
    (w,) = load(TRACE)
    assert matches(w, trace("J7 pad 3", "J8 pad 3"))
    assert not matches(w, trace("J7 pad 3", "J8 pad 3", "J9 pad 3"))
    assert not matches(w, trace("J7 pad 3"))


def test_position_within_tolerance() -> None:
    (w,) = load('[[waiver]]\nrule = "off-grid"\nat = [1.27, 2.54]\nreason = "r"\n')
    assert matches(w, found("off-grid", "o", at=(1.275, 2.54)))
    assert not matches(w, found("off-grid", "o", at=(1.29, 2.54)))
    assert not matches(w, found("junction-not-at-join", "j", at=(1.27, 2.54)))


def test_waive() -> None:
    waivers = load(TRACE) + load('[[waiver]]\nrule = "no-outline"\nreason = "r"\n')
    a = trace("J7 pad 3", "J8 pad 3")
    b = trace("J1 pad 1", "J2 pad 1")
    kept, waived, used = waive(waivers, [a, b])
    assert kept == [b]
    assert waived == [(a, waivers[0])]
    assert used == {0}


def run(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from ee_tooling.kicad_style_check import main; main()",
            *map(str, args),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def pcb(tmp_path: Path) -> Path:
    path = tmp_path / "b.kicad_pcb"
    path.write_text(BOARD)
    return path


def test_command_unwaived(pcb: Path) -> None:
    r = run(pcb)
    assert (r.returncode, r.stdout) == (1, f"{pcb}: {FINDING}\n")


def test_command_waived(pcb: Path) -> None:
    toml = pcb.parent / "kicad-style.toml"
    toml.write_text(TRACE)
    r = run(pcb)
    assert (r.returncode, r.stdout) == (0, "")
    assert f"{pcb}: 1 waived ({toml})" in r.stderr
    r = run("--waived", pcb)
    assert r.returncode == 0
    assert r.stdout == f"{pcb}: waived: {FINDING}: GND return across the breakaway tab.\n"


def test_command_stale(pcb: Path) -> None:
    toml = pcb.parent / "kicad-style.toml"
    toml.write_text(
        TRACE.replace('"J8 pad 3"', '"J8 pad 3", "J9 pad 3"')
        + '[[waiver]]\nrule = "off-grid"\nat = [1.27, 2.54]\nreason = "on the schematic"\n'
    )
    r = run(pcb)
    assert r.returncode == 1
    assert r.stdout == (
        f"{pcb}: {FINDING}\n"
        f"{toml}: stale waiver: trace-pad-to-pad net=GND; pads=J7 pad 3, J8 pad 3, J9 pad 3\n"
    )


def test_command_waivers_option(pcb: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.toml"
    other.write_text(TRACE)
    (tmp_path / "kicad-style.toml").write_text("not toml [")
    assert run("--waivers", other, pcb).returncode == 0
    r = run("--waivers", tmp_path / "missing.toml", pcb)
    assert r.returncode == 2
    assert "missing.toml: cannot check:" in r.stderr


def test_command_bad_waivers(pcb: Path) -> None:
    toml = pcb.parent / "kicad-style.toml"
    toml.write_text('[[waiver]]\nrule = "trace-pad-to-pad"\nnet = "GND"\npads = []\n')
    r = run(pcb)
    assert (r.returncode, r.stdout) == (2, "")
    assert f"{toml}: cannot check: waiver 1 (trace-pad-to-pad): no reason" in r.stderr
