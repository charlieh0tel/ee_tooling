"""A KiCad netlist (`kicad-cli sch export netlist --format kicadsexpr`)
condensed for a reviewer.

nets.txt:       `NAME: PINCOUNT : REF.PIN(FUNCTION/TYPE) ...`, one net a line
components.txt: `REF | VALUE | FOOTPRINT | SHEET | MPN | FLAGS`, one part a line
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ee_tooling.kicad_style_routing import text
from ee_tooling.sexpr import Expression, child, children, parse

# The first line of nets.txt and of components.txt, naming the columns.
NETS_HEADER = "# net name: pin count : ref.pin(pinfunction/type) ...\n"
COMPONENTS_HEADER = "ref | value | footprint | sheet | MPN | flags\n"
# A component's children, or properties, that go in its flags column when
# present (kicad-cli writes `(property (name "dnp"))` with no value).
FLAGS = ("dnp", "exclude_from_bom")
# The property holding the manufacturer part number.
MPN_PROPERTY = "MPN"


@dataclass(frozen=True)
class Component:
    """One `(comp ...)` of the netlist."""

    ref: str
    value: str
    footprint: str
    sheet: str
    mpn: str
    flags: str

    def line(self) -> str:
        return " | ".join((self.ref, self.value, self.footprint, self.sheet, self.mpn, self.flags))


@dataclass(frozen=True)
class Net:
    """One `(net ...)` of the netlist; nodes are `REF.PIN(FUNCTION/TYPE)`."""

    name: str
    nodes: tuple[str, ...]

    def line(self) -> str:
        return f"{self.name}: {len(self.nodes)} : {' '.join(self.nodes)}"


def refdes_key(ref: str) -> tuple[str, int]:
    """Sorts R2 before R10: the letters, then the number."""
    return (re.sub(r"\d", "", ref), int(re.sub(r"\D", "", ref) or 0))


def properties(component: Expression) -> dict[str, str]:
    return {text(p, "name"): text(p, "value") for p in children(component, "property")}


def components(netlist: Expression) -> list[Component]:
    section = child(netlist, "components") or []
    found = []
    for c in children(section, "comp"):
        props = properties(c)
        flags = " ".join(k for k in FLAGS if child(c, k) is not None or k in props)
        found.append(
            Component(
                ref=text(c, "ref"),
                value=text(c, "value"),
                footprint=text(c, "footprint"),
                sheet=text(child(c, "sheetpath") or [], "names"),
                mpn=props.get(MPN_PROPERTY, ""),
                flags=flags,
            )
        )
    return sorted(found, key=lambda c: refdes_key(c.ref))


def nets(netlist: Expression) -> list[Net]:
    section = child(netlist, "nets") or []
    found = [
        Net(
            name=text(n, "name"),
            nodes=tuple(
                f"{text(x, 'ref')}.{text(x, 'pin')}({text(x, 'pinfunction')}/{text(x, 'pintype')})"
                for x in children(n, "node")
            ),
        )
        for n in children(section, "net")
    ]
    return sorted(found, key=lambda n: n.name)


def condense(netlist_path: Path, out_dir: Path) -> tuple[int, int]:
    """Writes nets.txt and components.txt under out_dir; the counts written."""
    netlist = parse(netlist_path.read_text(encoding="utf-8"))
    parts = components(netlist)
    found = nets(netlist)
    (out_dir / "components.txt").write_text(
        COMPONENTS_HEADER + "".join(p.line() + "\n" for p in parts), encoding="utf-8"
    )
    (out_dir / "nets.txt").write_text(
        NETS_HEADER + "".join(n.line() + "\n" for n in found), encoding="utf-8"
    )
    return len(parts), len(found)
