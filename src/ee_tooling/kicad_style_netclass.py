"""A KiCad project's net classes against the house style (docs/kicad-style.md,
Board layout, Net classes): every power net has a net class; both nets of
every differential pair share a class named <ohms>Z_DIFF, or
<ohms>Z_DIFF_<INTERFACE> for one of INTERFACES, but for a Kelvin sense
pair (KELVIN), which is not impedance-controlled and which KiCad pairs by
name alone; a class named for an impedance is named so or <ohms>Z_SE;
every class a pattern or an assignment names is defined.

KiCad keeps net classes in the project file (.kicad_pro, net_settings):
the classes, explicit assignments of a net to classes (the schematic's
net class directives, written there), and patterns.  A net is in every
class whose assignment or pattern names it.  Nets come from the board's
pads; which are power comes from the board, the schematic and names.
"""

import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ee_tooling.kicad_style_board import NOTE, POWER_PIN_TYPES, UNCONNECTED, net_name
from ee_tooling.sexpr import Expression, child, children, parse

# The class every net is in; it does not count as one.
DEFAULT = "Default"
# KiCad's name for a net on one pad that is not to be connected.
# Pin electrical types that mark a net as power, in the schematic and on
# a pad's (pintype); a stacked pin's type is joined with "+".
PIN_TYPE_JOIN = "+"
# A power net's name, its last hierarchical part: a leading sign (+3V3,
# +5V, -12V), or a ground or supply stem with any suffix (GND, AGND,
# GNDA, VDD_USB, VCC_RF, VBUS).
POWER_NAME = re.compile(
    r"^(?:[+-][0-9A-Z]|[ADP]?GND|VDD|VCC|VSS|VEE|VBUS|VBAT|VIN|VSYS)[0-9A-Z_]*$", re.I
)
# What separates a hierarchical net name's sheets.
SHEET = "/"
# KiCad's differential pair: the last character that is not a digit or
# "_" is one of + - P N, and the net with it swapped exists (BOARD::
# MatchDpSuffix, BOARD::DpCoupledNet).
PAIR = re.compile(r"^(.*)([+\-PN])([0-9_]*)$")
COMPLEMENT = {"+": "-", "-": "+", "P": "N", "N": "P"}
# USB's pair names, USB_DP and USB_DM: KiCad pairs DP with DN, not DM.
USB_PAIR = re.compile(r"^(.*D)([PM])([0-9_]*)$")
USB_COMPLEMENT = {"P": "M", "M": "P"}
# Each pair form: its pattern, its swaps, and the characters that mark
# the positive net.
PAIR_FORMS = ((PAIR, COMPLEMENT, "+P"), (USB_PAIR, USB_COMPLEMENT, "P"))
# Half a pair, by its name, and the other half's: +/-, _P (but not a lone
# _N, often active low), and USB's DP/DM.
HALF_PAIR = re.compile(r"^(.*?)(\+|-|_P|DP|DM)([0-9_]*)$")
HALF_COMPLEMENT = {"+": "-", "-": "+", "_P": "_N", "DP": "DM", "DM": "DP"}
# A Kelvin sense pair, by its name: not impedance-controlled, it needs no
# class (KiCad pairs it by name; its class only sets width and gap).
KELVIN = re.compile(r"SENSE|SNS|KELVIN", re.I)
# A class name that says impedance: ohms (90Z, 50R, 100ohm), DIFF, _SE.
IMPEDANCE = re.compile(
    r"(?<![A-Z0-9.])\d+(?:\.\d+)?\s*(?:Z|R|OHMS?)(?![A-Z])|DIFF|_SE(?![A-Z])", re.I
)
# The interfaces a differential class may name, each with its skew rule
# in rules/jlcpcb.kicad_dru (docs/kicad-rules.md, Differential pairs).
# DVI shares HDMI's.
INTERFACES = ("USBFS", "USBHS", "USB3", "PCIE", "HDMI", "ETH1G", "ETH100")
# The house names for an impedance class, and for a differential one; a
# differential name's interface, if any, is group 1.
IMPEDANCE_CLASS = re.compile(r"^\d+(?:\.\d+)?Z_(?:DIFF(?:_[A-Z0-9]+)?|SE)$")
DIFF_CLASS = re.compile(r"^\d+(?:\.\d+)?Z_DIFF(?:_([A-Z0-9]+))?$")
DIFF_CLASS_FORM = "<ohms>Z_DIFF[_<INTERFACE>]"
SE_CLASS_FORM = "<ohms>Z_SE"


class ProjectError(ValueError):
    """A project file that parses but is not one KiCad could have written."""


@dataclass(frozen=True)
class Pad:
    reference: str
    number: str
    net: str
    pin_type: str | None  # the pad's (pintype), if the board has it


@dataclass(frozen=True)
class SchematicPower:
    """What the schematic says about power."""

    pin_types: dict[tuple[str, str], set[str]]  # by (reference, pin number)
    names: set[str]  # global power symbols' values: their nets' names
    local_names: set[str]  # local power symbols' values: a sheet's net's last part


def regex_matcher(pattern: str) -> Callable[[str], bool]:
    """KiCad's EDA_PATTERN_MATCH_REGEX: a pattern that does not compile
    matches nothing."""
    try:
        compiled = re.compile(pattern)
    except re.error:
        return lambda _: False
    return lambda text: compiled.search(text) is not None


def matcher(pattern: str) -> Callable[[str], bool]:
    """KiCad's net class pattern (EDA_COMBINED_MATCHER, CTX_NETCLASS): the
    whole name matches it as a regular expression (^ and $ added unless it
    starts and ends so) or as a wildcard (* any run, ? any one character,
    all else literal).  Case sensitive."""
    anchored = ("" if pattern.startswith("^") else "^") + pattern
    anchored += "" if pattern.endswith("$") else "$"
    wildcard = "".join("." if c == "?" else ".*" if c == "*" else re.escape(c) for c in pattern)
    as_regex = regex_matcher(anchored)
    as_wildcard = regex_matcher(f"^{wildcard}$")
    return lambda name: as_regex(name) or as_wildcard(name)


@dataclass(frozen=True)
class NetSettings:
    classes: set[str]  # defined classes, Default among them
    assignments: dict[str, list[str]]  # net name to class names
    patterns: list[tuple[Callable[[str], bool], str, str]]  # (matcher, pattern, class name)

    def classes_of(self, net: str) -> set[str]:
        """The classes net is in, by assignment or pattern, defined or not,
        Default aside."""
        named = set(self.assignments.get(net, []))
        named |= {c for match, _, c in self.patterns if match(net)}
        return named - {DEFAULT}


def net_settings(project: dict[str, Any]) -> NetSettings:
    """A parsed .kicad_pro's net_settings."""
    settings = project.get("net_settings")
    if not isinstance(settings, dict):
        raise ProjectError("no net_settings")
    classes = {str(c["name"]) for c in settings.get("classes") or [] if "name" in c}
    assignments = {
        str(net): [str(c) for c in names]
        for net, names in (settings.get("netclass_assignments") or {}).items()
    }
    patterns = [
        (matcher(str(p["pattern"])), str(p["pattern"]), str(p["netclass"]))
        for p in settings.get("netclass_patterns") or []
    ]
    return NetSettings(classes, assignments, patterns)


def property_value(expression: Expression, name: str) -> str | None:
    return next((str(p[2]) for p in children(expression, "property") if p[1] == name), None)


def board_pads(board: Expression) -> list[Pad]:
    """The board's pads that are on a net."""
    if not board or board[0] != "kicad_pcb":
        raise ProjectError("not a kicad_pcb")
    pads = []
    for footprint in children(board, "footprint"):
        reference = property_value(footprint, "Reference") or ""
        for pad in children(footprint, "pad"):
            net = net_name(pad)
            if net:
                pin_type = child(pad, "pintype")
                pads.append(
                    Pad(reference, str(pad[1]), net, str(pin_type[1]) if pin_type else None)
                )
    return pads


def library_pins(library: Expression) -> Iterator[Expression]:
    """A library symbol's pins, in every unit and body style."""
    yield from children(library, "pin")
    for unit in children(library, "symbol"):
        yield from library_pins(unit)


def schematic_power(sheets: list[Expression]) -> SchematicPower:
    """Power pins and power symbols across a schematic's sheets."""
    power = SchematicPower({}, set(), set())
    for sheet in sheets:
        if not sheet or sheet[0] != "kicad_sch":
            raise ProjectError("not a kicad_sch")
        libraries = {str(x[1]): x for x in children(child(sheet, "lib_symbols") or [], "symbol")}
        for symbol in children(sheet, "symbol"):
            name = child(symbol, "lib_name") or child(symbol, "lib_id")
            library = libraries.get(str(name[1])) if name else None
            if library is None:
                raise ProjectError(f"no lib_symbols entry for {name[1] if name else 'a symbol'}")
            flag = child(library, "power")
            if flag is not None:
                value = property_value(symbol, "Value") or ""
                local = len(flag) > 1 and flag[1] == "local"
                (power.local_names if local else power.names).add(value)
                continue
            references = {
                str(r[1])
                for project in children(child(symbol, "instances") or [], "project")
                for path in children(project, "path")
                for r in children(path, "reference")
            }
            references.add(property_value(symbol, "Reference") or "")
            for pin in library_pins(library):
                number = child(pin, "number")
                for reference in references:
                    if number and reference:
                        key = (reference, str(number[1]))
                        power.pin_types.setdefault(key, set()).add(str(pin[1]))
    return power


def power_nets(pads: list[Pad], power: SchematicPower) -> set[str]:
    """The nets that are power: by name, by power symbol, or by a power pin."""
    nets = set()
    for pad in pads:
        last = pad.net.rsplit(SHEET, 1)[-1]
        types = set(power.pin_types.get((pad.reference, pad.number), set()))
        if pad.pin_type:
            types |= set(pad.pin_type.split(PIN_TYPE_JOIN))
        if (
            POWER_NAME.match(last)
            or pad.net in power.names
            or last in power.local_names
            or types & POWER_PIN_TYPES
        ):
            nets.add(pad.net)
    return nets


def pairs(nets: set[str]) -> list[tuple[str, str]]:
    """The differential pairs among nets, each (positive, negative)."""
    found = set()
    for net in nets:
        for form, swap, positive in PAIR_FORMS:
            m = form.match(net)
            if m and (other := f"{m[1]}{swap[m[2]]}{m[3]}") in nets:
                found.add((net, other) if m[2] in positive else (other, net))
    return sorted(found)


def half_pairs(nets: set[str]) -> list[tuple[str, str]]:
    """Nets named like half a differential pair whose other half is not
    among nets, each with the name it lacks.  A lone _N is left out: it
    names an active-low signal as often as half a pair (HALF_PAIR)."""
    found = []
    for net in sorted(nets):
        m = HALF_PAIR.match(net)
        if m:
            other = f"{m[1]}{HALF_COMPLEMENT[m[2]]}{m[3]}"
            if other not in nets:
                found.append((net, other))
    return found


def check_netclasses(
    project: dict[str, Any], board: Expression | None, sheets: list[Expression]
) -> list[str]:
    """A project's net class problems, each a line: from its parsed
    .kicad_pro, its parsed board (None if it has none) and its parsed
    schematic sheets."""
    settings = net_settings(project)
    problems = [
        f"net class {name}: named for an impedance, not {DIFF_CLASS_FORM} or {SE_CLASS_FORM}"
        for name in sorted(settings.classes)
        if IMPEDANCE.search(name) and not IMPEDANCE_CLASS.match(name)
    ]
    problems += [
        f"net class {name}: interface {m[1]} not one of {', '.join(INTERFACES)}"
        for name in sorted(settings.classes)
        if (m := DIFF_CLASS.match(name)) and m[1] and m[1] not in INTERFACES
    ]
    used = {c for names in settings.assignments.values() for c in names}
    used |= {c for _, _, c in settings.patterns}
    problems += [
        f"net class {name}: assigned or in a pattern, but not defined"
        for name in sorted(used - settings.classes - {DEFAULT})
    ]
    if board is None:
        return problems
    pads = [p for p in board_pads(board) if not p.net.startswith(UNCONNECTED)]
    nets = {p.net for p in pads}
    power = power_nets(pads, schematic_power(sheets))

    def defined(net: str) -> set[str]:
        return settings.classes_of(net) & settings.classes

    problems += [f"power net {net}: no net class" for net in sorted(power) if not defined(net)]
    problems += [
        f"{NOTE}net {net}: named like half a differential pair, with no {other}"
        for net, other in half_pairs(nets - power)
    ]
    for positive, negative in pairs(nets - power):
        if KELVIN.search(positive):
            continue
        p, n = ({c for c in defined(x) if DIFF_CLASS.match(c)} for x in (positive, negative))
        if p & n:
            continue
        what = f"differential pair {positive} and {negative}"
        if p or n:
            problems.append(f"{what}: in different net classes {', '.join(sorted(p | n))}")
        else:
            problems.append(f"{what}: in no {DIFF_CLASS_FORM} net class")
    return problems


def sheets(root: Path) -> Iterator[Expression]:
    """A schematic's sheets, root first, each file once.  A sheet's file is
    found beside its parent's, else beside the root's."""
    seen: set[Path] = set()
    todo = [root]
    while todo:
        path = todo.pop(0)
        if path in seen:
            continue
        seen.add(path)
        sheet = parse(path.read_text())
        yield sheet
        for sub in children(sheet, "sheet"):
            name = property_value(sub, "Sheetfile")
            if not name:
                continue
            found = next((p for p in (path.parent / name, root.parent / name) if p.is_file()), None)
            if found is None:
                print(f"{path}: sheet file {name} not found, not checked", file=sys.stderr)
            else:
                todo.append(found.resolve())


def check_project(path: Path) -> list[str]:
    """A .kicad_pro's net class problems, with its board and schematic
    beside it, of the same name."""
    project = json.loads(path.read_text())
    if not isinstance(project, dict):
        raise ProjectError("not a kicad_pro")
    pcb = path.with_suffix(".kicad_pcb")
    sch = path.with_suffix(".kicad_sch")
    board = parse(pcb.read_text()) if pcb.is_file() else None
    if board is None:
        print(f"{path}: no {pcb.name}, nets not checked", file=sys.stderr)
    parsed = list(sheets(sch.resolve())) if sch.is_file() else []
    if not parsed:
        print(f"{path}: no {sch.name}, power taken from the board alone", file=sys.stderr)
    return check_netclasses(project, board, parsed)
