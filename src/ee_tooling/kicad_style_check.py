"""Check KiCad schematics and boards against the house style.

kicad-style-check FILE...: each problem on a line, `FILE: problem`.
Exits 1 if there are problems (a line starting `note: ` is for the owner
to judge, and does not count), 2 if a file cannot be read; an unreadable
file is never clean.  A .kicad_pcb is checked by kicad_style_board.py's
zone rules; a .kicad_sch by the drawing rules (docs/kicad-style.md,
Schematic drawing):
connection points on the grid; no pin touching another part's pin, no
power symbol on a pin, no pin on a wire's run; a wire off every pin, at
least one grid unit long and the pin's only one; a junction on every
join of three or more and nowhere else; no-connects on pins; no
zero-length or overlapping wires; Manufacturer, MPN and LCSC on every
part in the BOM; every capacitor's value showing its dielectric or type,
and its voltage.
"""

import argparse
import itertools
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ee_tooling.kicad_style_board import CAPACITOR, NOTE, check_board
from ee_tooling.sexpr import Atom, Expression, child, children, number, parse

GRID = 2.54  # mm (docs/kicad-style.md)
# Coordinates are compared rounded to this many places (KiCad's files keep 4).
PLACES = 3
# A coordinate this close to a grid line is on it, mm.
GRID_TOLERANCE = 1e-3
BOM_FIELDS = ("Manufacturer", "MPN", "LCSC")
# A ceramic dielectric (EIA class code), or a capacitor type.
DIELECTRIC = re.compile(
    r"\b(?:C0G|NP0|X[5-8][PRSTV]|Y5V|Z5U)\b"
    r"|\b(?:Al|aluminum|electrolytic|Ta|tantalum|polymer|film|mica)\b",
    re.I,
)
VOLTAGE = re.compile(r"\b\d+(?:\.\d+)?\s*[kK]?V\b")
LABELS = ("label", "global_label", "hierarchical_label")

Point = tuple[float, float]  # mm, KiCad's schematic axes (y down)


class SchematicError(ValueError):
    """A schematic that parses but is not one KiCad could have written."""


@dataclass(frozen=True)
class Pin:
    reference: str  # a symbol's reference, or a sheet's name
    number: str  # a pin's number, or a sheet pin's name
    point: Point
    power: bool  # a power symbol's pin
    hidden: bool = False  # drawn hidden: only a no-connect may go on it

    def __str__(self) -> str:
        return f"{self.reference} pin {self.number}"


@dataclass(frozen=True)
class Part:
    """A symbol in the BOM's terms, one per reference."""

    reference: str
    value: str
    fields: dict[str, str]  # the instance's properties


@dataclass
class Schematic:
    wires: list[tuple[Point, Point]] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)
    no_connects: list[Point] = field(default_factory=list)
    labels: list[tuple[str, Point]] = field(default_factory=list)
    pins: list[Pin] = field(default_factory=list)
    parts: dict[str, Part] = field(default_factory=dict)  # bought parts, by reference


def point(x: float, y: float) -> Point:
    """A coordinate pair as compared: rounded, with -0 made 0."""
    return round(x, PLACES) + 0.0, round(y, PLACES) + 0.0


def at(expression: Expression) -> tuple[float, float, float]:
    """An item's (at X Y [ANGLE])."""
    a = child(expression, "at")
    if a is None:
        raise SchematicError(f"{expression[0]} has no (at)")
    return number(a[1]), number(a[2]), number(a[3]) if len(a) > 3 else 0.0


def flag(expression: Expression, keyword: str) -> bool:
    """Whether (keyword yes), or a bare keyword atom, is set."""
    if Atom(keyword) in expression:
        return True
    c = child(expression, keyword)
    return c is not None and (len(c) == 1 or c[1] == "yes")


def properties(expression: Expression) -> dict[str, str]:
    return {str(p[1]): str(p[2]) for p in children(expression, "property")}


def transform(origin: Point, rotation: float, mirror: str | None, pin: Point) -> Point:
    """A library pin's point on the sheet: rotated counterclockwise and
    then mirrored in the library's axes (y up), then placed at origin
    with y flipped.  KiCad allows only quarter turns."""
    quarter = round(rotation / 90) % 4
    cos, sin = ((1, 0), (0, 1), (-1, 0), (0, -1))[quarter]
    x, y = pin[0] * cos - pin[1] * sin, pin[0] * sin + pin[1] * cos
    if mirror == "y":
        x = -x
    elif mirror == "x":
        y = -y
    return point(origin[0] + x, origin[1] - y)


def unit_pins(library: Expression, unit: int, body_style: int) -> list[Expression]:
    """A library symbol's pins in one unit and body style, with those
    common to all (unit or style 0).  Its units are (symbol NAME_U_B)."""
    pins = []
    for u in children(library, "symbol"):
        _, u_text, b_text = str(u[1]).rsplit("_", 2)
        if int(u_text) in (0, unit) and int(b_text) in (0, body_style):
            pins += children(u, "pin")
    return pins


def read(sheet: Expression) -> Schematic:
    """A schematic file's drawing, from its parsed text."""
    if not sheet or sheet[0] != "kicad_sch":
        raise SchematicError("not a kicad_sch")
    s = Schematic()
    libraries = {str(x[1]): x for x in children(child(sheet, "lib_symbols") or [], "symbol")}
    for w in children(sheet, "wire"):
        xy = children(child(w, "pts") or [], "xy")
        if len(xy) != 2:
            raise SchematicError(f"a wire with {len(xy)} points")
        s.wires.append(
            (point(number(xy[0][1]), number(xy[0][2])), point(number(xy[1][1]), number(xy[1][2])))
        )
    s.junctions = [point(*at(j)[:2]) for j in children(sheet, "junction")]
    s.no_connects = [point(*at(n)[:2]) for n in children(sheet, "no_connect")]
    s.labels = [(str(x[1]), point(*at(x)[:2])) for k in LABELS for x in children(sheet, k)]
    for sub in children(sheet, "sheet"):
        name = properties(sub).get("Sheetname", "sheet")
        s.pins += [Pin(name, str(p[1]), point(*at(p)[:2]), False) for p in children(sub, "pin")]
    for sym in children(sheet, "symbol"):
        lib_id = str((child(sym, "lib_id") or ["", ""])[1])
        library = libraries.get(lib_id)
        if library is None:
            raise SchematicError(f"no lib_symbols entry for {lib_id!r}")
        fields = properties(sym)
        reference = fields.get("Reference", "?")
        power = child(library, "power") is not None
        x, y, rotation = at(sym)
        m = child(sym, "mirror")
        mirror = str(m[1]) if m else None
        unit = int(number((child(sym, "unit") or ["", Atom("1")])[1]))
        style = child(sym, "body_style") or child(sym, "convert")
        for p in unit_pins(library, unit, int(number(style[1])) if style else 1):
            px, py, _ = at(p)
            number_text = str((child(p, "number") or ["", "?"])[1])
            sheet_point = transform((x, y), rotation, mirror, (px, py))
            # A power symbol's pin is its connection, hidden or not.
            hidden = flag(p, "hide") and not power
            s.pins.append(Pin(reference, number_text, sheet_point, power, hidden))
        bought = not power and flag(sym, "in_bom") and not flag(sym, "dnp")
        if bought and not reference.startswith("#"):
            s.parts[reference] = Part(reference, fields.get("Value", ""), fields)
    return s


def on_run(p: Point, wire: tuple[Point, Point]) -> bool:
    """Whether p lies on the wire, strictly between its ends."""
    a, b = wire
    if p in (a, b):
        return False
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > GRID_TOLERANCE:
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    return 0 < dot < math.dist(a, b) ** 2


def overlap(w1: tuple[Point, Point], w2: tuple[Point, Point]) -> bool:
    """Whether two collinear wires share a length."""
    (a, b), (c, d) = w1, w2
    if a == b or c == d:
        return False
    if not all(
        abs((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])) <= GRID_TOLERANCE
        for q in (c, d)
    ):
        return False
    length = math.dist(a, b)

    def along(q: Point) -> float:
        return ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / length

    low, high = sorted((along(c), along(d)))
    return min(length, high) - max(0.0, low) > GRID_TOLERANCE


def where(p: Point) -> str:
    return f"({p[0]:g}, {p[1]:g})"


def off_grid(p: Point, grid: float) -> bool:
    return any(abs(v / grid - round(v / grid)) * grid > GRID_TOLERANCE for v in p)


def check(s: Schematic, grid: float = GRID) -> list[str]:
    """The drawing's problems, each a line."""
    problems = []
    ends = Counter(p for w in s.wires if w[0] != w[1] for p in w)
    pins_at: dict[Point, list[Pin]] = defaultdict(list)
    for pin in s.pins:
        if not pin.hidden:
            pins_at[pin.point].append(pin)

    points: list[tuple[str, Point]] = [("wire end", p) for w in s.wires for p in w]
    points += [("junction", p) for p in s.junctions]
    points += [("no-connect", p) for p in s.no_connects]
    points += [(f"label {text!r}", p) for text, p in s.labels]
    points += [(str(pin), pin.point) for pin in s.pins if not pin.hidden]
    problems += [
        f"off the {grid:g} mm grid: {what} at {where(p)}"
        for what, p in dict.fromkeys(points)
        if off_grid(p, grid)
    ]

    for p, here in pins_at.items():
        powered = [x for x in here if x.power]
        others = [x for x in here if not x.power]
        if powered and others:
            problems.append(f"power symbol on a pin: {', '.join(map(str, here))} at {where(p)}")
        if len({x.reference for x in others}) > 1:
            problems.append(f"pins touching: {', '.join(map(str, others))} at {where(p)}")
        runs = [w for w in s.wires if on_run(p, w)]
        if runs:
            problems.append(f"pin on a wire's run: {', '.join(map(str, here))} at {where(p)}")
        elif ends[p] == 0 and p not in s.no_connects and len(here) == 1:
            problems.append(f"pin with no wire: {here[0]} at {where(p)}")
        if ends[p] > 1:
            problems.append(f"wires join at a pin: {', '.join(map(str, here))} at {where(p)}")
        for w in s.wires:
            if p in w and w[0] != w[1] and math.dist(*w) < grid - GRID_TOLERANCE:
                problems.append(
                    f"pin wire shorter than {grid:g} mm: {', '.join(map(str, here))} at {where(p)}"
                )

    branches: Counter[Point] = Counter(ends)
    for p, here in pins_at.items():
        branches[p] += len(here)
    for p in set(branches) | set(s.junctions):
        branches[p] += 2 * sum(on_run(p, w) for w in s.wires)
    joins = {p for p, n in branches.items() if n >= 3}
    junctions = set(s.junctions)
    problems += [f"join with no junction at {where(p)}" for p in sorted(joins - junctions)]
    problems += [f"junction not at a join at {where(p)}" for p in sorted(junctions - joins)]

    any_pin = {pin.point for pin in s.pins}
    problems += [f"no-connect off a pin at {where(p)}" for p in s.no_connects if p not in any_pin]
    problems += [f"zero-length wire at {where(a)}" for a, b in s.wires if a == b]
    problems += [
        f"overlapping wires {where(w1[0])}-{where(w1[1])} and {where(w2[0])}-{where(w2[1])}"
        for w1, w2 in itertools.combinations(s.wires, 2)
        if overlap(w1, w2)
    ]

    for part in sorted(s.parts.values(), key=lambda x: x.reference):
        missing = [f for f in BOM_FIELDS if not part.fields.get(f, "").strip()]
        if missing:
            problems.append(f"{part.reference}: no {', '.join(missing)}")
        if CAPACITOR.match(part.reference) and not (
            DIELECTRIC.search(part.value) and VOLTAGE.search(part.value)
        ):
            problems.append(
                f"{part.reference}: value {part.value!r}"
                " does not show dielectric or type, and voltage"
            )
    return problems


def check_file(path: Path, grid: float) -> list[str]:
    match path.suffix:
        case ".kicad_sch":
            return check(read(parse(path.read_text())), grid)
        case ".kicad_pcb":
            return check_board(parse(path.read_text()))
        case _:
            raise ValueError("not a .kicad_sch or .kicad_pcb")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("files", nargs="+", type=Path, metavar="FILE")
    ap.add_argument("--grid", type=float, default=GRID, help="a schematic's grid, mm")
    a = ap.parse_args()
    found = 0
    unreadable = False
    for path in a.files:
        try:
            problems = check_file(path, a.grid)
        except (OSError, UnicodeDecodeError, ValueError, IndexError) as e:
            print(f"{path}: cannot check: {e}", file=sys.stderr)
            unreadable = True
            continue
        for line in problems:
            print(f"{path}: {line}")
        found += sum(not line.startswith(NOTE) for line in problems)
    sys.exit(2 if unreadable else 1 if found else 0)
