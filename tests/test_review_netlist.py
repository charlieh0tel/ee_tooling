from pathlib import Path

from ee_tooling.review_netlist import components, condense, nets, refdes_key
from ee_tooling.sexpr import parse

NETLIST = """(export (version "E")
  (components
    (comp (ref "R10") (value "10k") (footprint "R_0402")
      (property (name "MPN") (value "RC0402FR-0710KL"))
      (sheetpath (names "/PWR/") (tstamps "/a/")))
    (comp (ref "R2") (value "1k") (footprint "R_0402") (dnp yes)
      (sheetpath (names "/") (tstamps "/")))
    (comp (ref "C1") (value "100n") (footprint "C_0402")
      (property (name "exclude_from_bom")) (property (name "dnp"))
      (sheetpath (names "/") (tstamps "/"))))
  (nets
    (net (code "2") (name "/PWR/VIN")
      (node (ref "R10") (pin "1") (pinfunction "") (pintype "passive"))
      (node (ref "C1") (pin "1") (pintype "passive")))
    (net (code "1") (name "GND")
      (node (ref "C1") (pin "2") (pintype "passive")))))
"""


def test_refdes_key() -> None:
    assert sorted(["R10", "R2", "C1", "IC1"], key=refdes_key) == ["C1", "IC1", "R2", "R10"]


def test_components() -> None:
    parts = components(parse(NETLIST))
    assert [p.ref for p in parts] == ["C1", "R2", "R10"]
    assert parts[2].line() == "R10 | 10k | R_0402 | /PWR/ | RC0402FR-0710KL | "
    assert parts[1].flags == "dnp"
    assert parts[0].flags == "dnp exclude_from_bom"


def test_nets() -> None:
    found = nets(parse(NETLIST))
    assert [n.name for n in found] == ["/PWR/VIN", "GND"]
    assert found[0].line() == "/PWR/VIN: 2 : R10.1(/passive) C1.1(/passive)"


def test_condense(tmp_path: Path) -> None:
    src = tmp_path / "n.net"
    src.write_text(NETLIST, encoding="utf-8")
    assert condense(src, tmp_path) == (3, 2)
    lines = (tmp_path / "nets.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# net name") and len(lines) == 3
    assert (tmp_path / "components.txt").read_text(encoding="utf-8").count("\n") == 4
