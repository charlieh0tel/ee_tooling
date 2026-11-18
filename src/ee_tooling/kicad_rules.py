"""A profile's KiCad rules in a board's project (docs/kicad-rules.md): its
custom DRC rules installed in the board's .kicad_dru between markers, the
board's own rules after them, and its Board Setup minimums in the
.kicad_pro, raised to, never lowered.

fab-release runs DRC with them on a scratch copy of the project, and
ee-tooling-adopt installs them in the board's own files.
"""

import copy
import importlib.resources
import json
import re
from dataclasses import dataclass
from typing import Any

from ee_tooling.profiles.profile import Rules

# The markers around the installed rules: the begin line names the profile.
BEGIN = "# --- ee_tooling: {} rules; written by adopt ---"
END = "# --- ee_tooling: end; the board's own rules follow and win ---"
BEGIN_LINE = re.compile(r"^# --- ee_tooling: .* rules; written by adopt ---[ \t]*$", re.M)
END_LINE = re.compile(rf"^{re.escape(END)}[ \t]*$", re.M)
# A .kicad_dru's version line, and the one a file starts with.
VERSION_LINE = re.compile(r"^[ \t]*\(version[ \t]+\d+\)[ \t]*\n?", re.M)
VERSION = "(version 1)"
# A section heading of a profile's .kicad_dru: "# --- <name> ---", with
# dashes to fill the line.
SECTION = re.compile(r"^# --- (.+?) -+[ \t]*$", re.M)
# A rule's name in a .kicad_dru.
RULE_NAME = re.compile(r'^[ \t]*\(rule[ \t]+"((?:[^"\\]|\\.)*)"', re.M)


def resource(path: str) -> str:
    """A file in the ee_tooling package, by its path there."""
    return importlib.resources.files("ee_tooling").joinpath(path).read_text()


def sections(text: str) -> list[tuple[str, str]]:
    """A .kicad_dru's sections, each its name and its text from its heading
    to the next; the text before the first heading is named ""."""
    starts = [(m.start(), m.group(1)) for m in SECTION.finditer(text)]
    bounds = [(0, ""), *starts]
    ends = [s for s, _ in starts] + [len(text)]
    return [(name, text[start:end]) for (start, name), end in zip(bounds, ends, strict=True)]


def installed(name: str, rules: Rules, options: object) -> str:
    """The section installed for profile name: its rules, without their
    version line, less each optional section whose option is false in
    options, between the markers."""
    leave_out = {s for option, s in rules.optional.items() if not getattr(options, option)}
    kept = "".join(t for n, t in sections(resource(rules.dru)) if n not in leave_out)
    body = VERSION_LINE.sub("", kept, count=1).strip("\n")
    return f"{BEGIN.format(name)}\n{body}\n{END}\n"


@dataclass(frozen=True)
class Dru:
    """A board's .kicad_dru, split: the section installed between the
    markers, if any, and the board's own rules, without the version line."""

    section: str | None
    own: str

    @classmethod
    def split(cls, text: str | None) -> "Dru":
        """ValueError if the markers are not one begin line, then one end
        line."""
        if text is None:
            return cls(None, "")
        begins, ends = list(BEGIN_LINE.finditer(text)), list(END_LINE.finditer(text))
        if not begins and not ends:
            return cls(None, VERSION_LINE.sub("", text, count=1).strip("\n"))
        if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].start():
            raise ValueError("ee_tooling's markers are not one begin line, then one end line")
        start, end = begins[0].start(), ends[0].end()
        section = text[start:end] + "\n"
        own = text[:start] + text[end:]
        return cls(section, VERSION_LINE.sub("", own, count=1).strip("\n"))

    def with_section(self, section: str) -> str:
        """The .kicad_dru with section installed, the board's own rules
        after it."""
        own = f"\n{self.own}\n" if self.own else ""
        return f"{VERSION}\n{section}{own}"

    def copied(self, section: str) -> list[str]:
        """The names of the board's own rules that the section has too: a
        copy of the profile's rules outside the markers, which wins over
        the section."""
        names = set(RULE_NAME.findall(section))
        return [n for n in RULE_NAME.findall(self.own) if n in names]


def board_setup(rules: Rules) -> dict[str, Any]:
    """The profile's Board Setup minimums, a .kicad_pro fragment."""
    found = json.loads(resource(rules.board_setup))
    if not isinstance(found, dict):
        raise ValueError(f"{rules.board_setup}: not a JSON object")
    return found


def put(table: dict[str, Any], key: str, value: object) -> None:
    """table[key] = value, a new key in sorted place when table's keys are
    sorted, as KiCad writes a .kicad_pro's: so KiCad's next save does not
    reorder it.  An existing key keeps its place."""
    if key in table or list(table) != sorted(table):
        table[key] = value
        return
    items = sorted([*table.items(), (key, value)], key=lambda item: item[0])
    table.clear()
    table.update(items)


def raised(project: dict[str, Any], least: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The project with each number in least raised to at least least's
    value, never lowered (a missing one set), and what was raised, as
    "a.b.c: old -> new"."""
    out = copy.deepcopy(project)
    changes: list[str] = []

    def walk(have: dict[str, Any], want: dict[str, Any], where: str) -> None:
        for key, value in want.items():
            path = f"{where}{key}"
            if isinstance(value, dict):
                if not isinstance(have.get(key), dict):
                    put(have, key, {})
                walk(have[key], value, f"{path}.")
                continue
            old = have.get(key)
            if not isinstance(old, int | float) or isinstance(old, bool) or old < value:
                put(have, key, value)
                changes.append(f"{path}: {'none' if old is None else old} -> {value}")

    walk(out, least, "")
    return out, changes


# A custom rule's constraint, by its kind, to the Board Setup minimum of
# that kind (.kicad_pro board.design_settings.rules), which a rule matching
# an item replaces for it in KiCad's DRC.  Not annular_width: Board Setup's
# (min_via_annular_width) holds for vias only (KiCad 10.0.8), and a pad's
# annular ring has no Board Setup minimum to loosen.
BOARD_SETUP_KEYS = {
    "track_width": "min_track_width",
    "via_diameter": "min_via_diameter",
    "hole_size": "min_through_hole_diameter",
    "hole_to_hole": "min_hole_to_hole",
    "hole_clearance": "min_hole_clearance",
    "clearance": "min_clearance",
    "edge_clearance": "min_copper_edge_clearance",
    "silk_clearance": "min_silk_clearance",
    "text_height": "min_text_height",
    "text_thickness": "min_text_thickness",
}
# A constraint's minimum in a .kicad_dru, in mm: its kind, its value.
MINIMUM = re.compile(r"\(constraint[ \t]+(\w+)[ \t]+\(min[ \t]+([0-9.]+)mm\)")
# Decimal places kept in a minimum written to a .kicad_dru (KiCad's 1 nm).
MM_PLACES = 6


def floored(section: str, project: dict[str, Any]) -> str:
    """section with each minimum of a kind Board Setup also holds raised to
    the project's Board Setup value where that is higher: a rule replaces
    Board Setup's constraint for what it matches, so a lower one would
    loosen the board's own limit there."""
    setup = project.get("board", {}).get("design_settings", {}).get("rules", {})

    def floor(m: re.Match[str]) -> str:
        key = BOARD_SETUP_KEYS.get(m.group(1))
        least = setup.get(key) if key is not None else None
        if not isinstance(least, int | float) or isinstance(least, bool):
            return m.group(0)
        if least <= float(m.group(2)):
            return m.group(0)
        mm = f"{least:.{MM_PLACES}f}".rstrip("0").rstrip(".")
        return m.group(0).replace(f"{m.group(2)}mm", f"{mm}mm")

    return MINIMUM.sub(floor, section)


@dataclass(frozen=True)
class Installed:
    """A board's rules files with a profile's rules installed."""

    dru: str  # the .kicad_dru's text
    section: str  # the section installed in it, floored to the board's Board Setup
    project: dict[str, Any]  # the .kicad_pro, Board Setup raised
    stale: bool  # the board's .kicad_dru lacks the section, or has another
    raised: list[str]  # the Board Setup values raised, as raised() gives them
    copied: list[str]  # the board's own rules that copy the section's


def install(
    dru: str | None, project: dict[str, Any], section: str, least: dict[str, Any]
) -> Installed:
    """The board's .kicad_dru (None if it has none) and .kicad_pro with
    Board Setup raised to least and section installed, each of its minimums
    floored to the raised Board Setup's (floored); ValueError if the
    .kicad_dru's markers are not in order."""
    found = Dru.split(dru)
    project, changes = raised(project, least)
    section = floored(section, project)
    return Installed(
        found.with_section(section),
        section,
        project,
        found.section != section,
        changes,
        found.copied(section),
    )


# The house ERC severities and connection grid, a .kicad_pro fragment; and
# KiCad's ERC severities, least strict first.
ERC_SETTINGS = "rules/erc_severities.json"
SEVERITIES = ("ignore", "warning", "error")


def erc_set(project: dict[str, Any], house: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The project with house's ERC severities, each kept where the
    project's is stricter (SEVERITIES), and house's other values (the
    connection grid) set; and what changed, as "a.b: old -> new"."""
    out = copy.deepcopy(project)
    changes: list[str] = []

    def walk(have: dict[str, Any], want: dict[str, Any], where: str) -> None:
        for key, value in want.items():
            path = f"{where}{key}"
            if isinstance(value, dict):
                if not isinstance(have.get(key), dict):
                    put(have, key, {})
                walk(have[key], value, f"{path}.")
                continue
            old = have.get(key)
            if value in SEVERITIES and old in SEVERITIES:
                if SEVERITIES.index(old) >= SEVERITIES.index(value):
                    continue
            elif old == value:
                continue
            put(have, key, value)
            changes.append(f"{path}: {'none' if old is None else old} -> {value}")

    walk(out, house, "")
    return out, changes


def copied_message(path: str, names: list[str]) -> str:
    """What to do about the board's own rules in path that copy the
    section's (Dru.copied): their count, one name a line, and where they
    are."""
    one = len(names) == 1
    return (
        f"{path}: {len(names)} {'rule' if one else 'rules'} below ee_tooling's end marker"
        f" {'copies' if one else 'copy'} the profile's and {'wins' if one else 'win'} over"
        " them:\n"
        + "".join(f"  {n}\n" for n in names)
        + f"remove {'this rule' if one else 'these rules'} from below the end marker in {path}"
    )
