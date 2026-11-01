"""Check KiCad schematics and boards against the house style.

kicad-style-check FILE...: each problem on a line, `FILE: problem [RULE]`,
RULE its id (kicad_style_finding.RULES), but those waived by the
kicad-style.toml beside the file (kicad_style_waivers.py); a waiver that
waives nothing is a problem.
Exits 1 if there are problems (a line starting `note: ` is for the owner
to judge, and does not count), 2 if a file cannot be read; an unreadable
file is never clean.  Notes on what a check cannot decide go to stderr.
By the file's suffix:
- .kicad_pro: its net classes (kicad_style_netclass.py), with the board
  and schematic of its name beside it;
- .kicad_pcb: its zones (kicad_style_board.py), finishing
  (kicad_style_finishing.py), stackup table (kicad_style_stackup.py) and
  routing (kicad_style_routing.py), the last with placement.toml beside
  it if there is one (placement.py);
- .kicad_sch: the drawing rules (docs/kicad-style.md, Schematic drawing):
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

from ee_tooling.kicad_style_board import CAPACITOR, check_board
from ee_tooling.kicad_style_finding import SCHEMATIC_RULES, Checked, Finding, found
from ee_tooling.kicad_style_finishing import check_finishing
from ee_tooling.kicad_style_netclass import check_project
from ee_tooling.kicad_style_routing import check_routing
from ee_tooling.kicad_style_stackup import check_stackup
from ee_tooling.kicad_style_waivers import WAIVERS, Waiver, WaiverError, load, waive
from ee_tooling.placement import Rules
from ee_tooling.placement_kicad import RULES
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
    shown: str  # the Value and every field shown on the sheet, joined
    dnp: bool  # marked do not populate: its problems are notes


@dataclass
class Schematic:
    wires: list[tuple[Point, Point]] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)
    no_connects: list[Point] = field(default_factory=list)
    labels: list[tuple[str, Point]] = field(default_factory=list)
    pins: list[Pin] = field(default_factory=list)
    parts: dict[str, Part] = field(default_factory=dict)  # BOM parts, DNP too, by reference


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


def shown(expression: Expression) -> str:
    """The Value and every property not hidden, joined by spaces.  KiCad 9
    and later hide a property with its own (hide yes), KiCad 8 in its
    (effects)."""
    texts = [
        str(p[2])
        for p in children(expression, "property")
        if p[1] == "Value"
        or not (flag(p, "hide") or flag(child(p, "effects") or ["effects"], "hide"))
    ]
    return " ".join(texts)


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
        if not power and flag(sym, "in_bom") and not reference.startswith("#"):
            s.parts[reference] = Part(
                reference, fields.get("Value", ""), fields, shown(sym), flag(sym, "dnp")
            )
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


def check(s: Schematic, grid: float = GRID) -> Checked:
    """The drawing's findings."""
    problems: list[Finding] = []
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
        found("off-grid", f"off the {grid:g} mm grid: {what} at {where(p)}", at=p)
        for what, p in dict.fromkeys(points)
        if off_grid(p, grid)
    ]

    for p, here in pins_at.items():
        powered = [x for x in here if x.power]
        others = [x for x in here if not x.power]
        named = ", ".join(map(str, here))
        pins = frozenset(map(str, here))
        if powered and others:
            problems.append(
                found("power-on-pin", f"power symbol on a pin: {named} at {where(p)}", pins=pins)
            )
        if len({x.reference for x in others}) > 1:
            problems.append(
                found(
                    "pins-touching",
                    f"pins touching: {', '.join(map(str, others))} at {where(p)}",
                    pins=frozenset(map(str, others)),
                )
            )
        runs = [w for w in s.wires if on_run(p, w)]
        if runs:
            problems.append(
                found("pin-on-wire", f"pin on a wire's run: {named} at {where(p)}", pins=pins)
            )
        elif ends[p] == 0 and p not in s.no_connects and len(here) == 1:
            problems.append(
                found("pin-no-wire", f"pin with no wire: {here[0]} at {where(p)}", pin=str(here[0]))
            )
        if ends[p] > 1:
            problems.append(
                found("wires-join-at-pin", f"wires join at a pin: {named} at {where(p)}", pins=pins)
            )
        for w in s.wires:
            if p in w and w[0] != w[1] and math.dist(*w) < grid - GRID_TOLERANCE:
                problems.append(
                    found(
                        "pin-wire-short",
                        f"pin wire shorter than {grid:g} mm: {named} at {where(p)}",
                        pins=pins,
                    )
                )

    branches: Counter[Point] = Counter(ends)
    for p, here in pins_at.items():
        branches[p] += len(here)
    for p in set(branches) | set(s.junctions):
        branches[p] += 2 * sum(on_run(p, w) for w in s.wires)
    joins = {p for p, n in branches.items() if n >= 3}
    junctions = set(s.junctions)
    problems += [
        found("join-no-junction", f"join with no junction at {where(p)}", at=p)
        for p in sorted(joins - junctions)
    ]
    problems += [
        found("junction-not-at-join", f"junction not at a join at {where(p)}", at=p)
        for p in sorted(junctions - joins)
    ]

    any_pin = {pin.point for pin in s.pins}
    problems += [
        found("no-connect-off-pin", f"no-connect off a pin at {where(p)}", at=p)
        for p in s.no_connects
        if p not in any_pin
    ]
    problems += [
        found("zero-length-wire", f"zero-length wire at {where(a)}", at=a)
        for a, b in s.wires
        if a == b
    ]
    problems += [
        found(
            "overlapping-wires",
            f"overlapping wires {where(w1[0])}-{where(w1[1])} and {where(w2[0])}-{where(w2[1])}",
            at=w1[0],
        )
        for w1, w2 in itertools.combinations(s.wires, 2)
        if overlap(w1, w2)
    ]

    for part in sorted(s.parts.values(), key=lambda x: x.reference):
        name = f"{part.reference} (DNP)" if part.dnp else part.reference
        missing = [f for f in BOM_FIELDS if not part.fields.get(f, "").strip()]
        if missing:
            problems.append(
                found(
                    "part-fields",
                    f"{name}: no {', '.join(missing)}",
                    note=part.dnp,
                    reference=part.reference,
                )
            )
        if CAPACITOR.match(part.reference) and not (
            DIELECTRIC.search(part.shown) and VOLTAGE.search(part.shown)
        ):
            problems.append(
                found(
                    "capacitor-value",
                    f"{name}: value {part.value!r} and its shown fields"
                    " do not show dielectric or type, and voltage",
                    note=part.dnp,
                    reference=part.reference,
                )
            )
    return Checked(problems, frozenset(SCHEMATIC_RULES))


def check_file(path: Path, grid: float) -> Checked:
    match path.suffix:
        case ".kicad_sch":
            return check(read(parse(path.read_text())), grid)
        case ".kicad_pcb":
            board = parse(path.read_text())
            rules_file = path.parent / RULES
            rules = Rules.load(rules_file.read_text()) if rules_file.exists() else None
            parts = [
                check_board(board),
                check_finishing(board),
                check_stackup(board),
                check_routing(board, rules),
            ]
            return Checked(
                [f for c in parts for f in c.findings], frozenset().union(*(c.ran for c in parts))
            )
        case ".kicad_pro":
            return check_project(path)
        case _:
            raise ValueError("not a .kicad_sch, .kicad_pcb or .kicad_pro")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("files", nargs="+", type=Path, metavar="FILE")
    ap.add_argument("--grid", type=float, default=GRID, help="a schematic's grid, mm")
    ap.add_argument(
        "--waivers",
        type=Path,
        metavar="FILE",
        help=f"the waivers for every FILE, not the {WAIVERS} beside each",
    )
    ap.add_argument("--waived", action="store_true", help="print each waived finding")
    a = ap.parse_args()
    counted = 0
    unreadable = False
    loaded: dict[Path, list[Waiver] | None] = {}  # waivers file: its waivers, None if bad
    ran: dict[Path, set[str]] = defaultdict(set)  # waivers file: rules run on its files
    used: dict[Path, set[int]] = defaultdict(set)  # waivers file: its waivers that matched
    for path in a.files:
        source = a.waivers or path.parent / WAIVERS
        if source not in loaded:
            try:
                loaded[source] = load(source.read_text()) if a.waivers or source.is_file() else []
            except (OSError, UnicodeDecodeError, WaiverError) as e:
                print(f"{source}: cannot check: {e}", file=sys.stderr)
                loaded[source] = None
        waivers = loaded[source]
        if waivers is None:
            unreadable = True
            continue
        try:
            checked = check_file(path, a.grid)
        except (OSError, UnicodeDecodeError, ValueError, IndexError, KeyError) as e:
            print(f"{path}: cannot check: {e}", file=sys.stderr)
            unreadable = True
            continue
        kept, waived, hits = waive(waivers, checked.findings)
        used[source] |= hits
        ran[source] |= checked.ran
        for f in kept:
            print(f"{path}: {f} [{f.rule}]")
        counted += sum(not f.note for f in kept)
        if waived:
            print(f"{path}: {len(waived)} waived ({source})", file=sys.stderr)
        if a.waived:
            for f, w in waived:
                print(f"{path}: waived: {f} [{f.rule}]: {w.reason}")
    for source, waivers in loaded.items():
        for i, w in enumerate(waivers or []):
            if i not in used[source] and w.rule in ran[source]:
                print(f"{source}: stale waiver: {w}")
                counted += 1
    sys.exit(2 if unreadable else 1 if counted else 0)
