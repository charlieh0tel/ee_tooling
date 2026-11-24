"""A profile's KiCad rules in a board's project (docs/kicad-rules.md): its
custom DRC rules installed in the board's .kicad_dru between markers, the
board's own rules after them, and its Board Setup minimums in the
.kicad_pro, raised to, never lowered.  The rules come from a rule set, the
rules files as an ee_tooling release shipped them: the current one, or the
one a board pins in fab.toml.

fab-release runs DRC with them on a scratch copy of the project, and
ee-tooling-adopt installs them in the board's own files.
"""

import copy
import importlib.resources
import json
import math
import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from ee_tooling.profiles.profile import Rules

# The markers around the installed rules: the begin line names the profile,
# and the line after it the rule set, but for UNRECORDED's, whose section
# boards had installed before a section named its set.
BEGIN = "# --- ee_tooling: {} rules; written by adopt ---"
SET_LINE = "# ee_tooling rule set {}"
UNRECORDED = "0.4.0"
END = "# --- ee_tooling: end; the board's own rules follow and win ---"
BEGIN_LINE = re.compile(r"^# --- ee_tooling: .* rules; written by adopt ---[ \t]*$", re.M)
END_LINE = re.compile(rf"^{re.escape(END)}[ \t]*$", re.M)
# A .kicad_dru's version line, and the one a file starts with.
VERSION_LINE = re.compile(r"^[ \t]*\(version[ \t]+\d+\)[ \t]*\n?", re.M)
VERSION = "(version 1)"
# A section heading of a profile's .kicad_dru: "# --- <name> ---", with
# dashes to fill the line.
SECTION = re.compile(r"^# --- (.+?) -+[ \t]*$", re.M)
# The rules ee_tooling's rules files had before its first rule set and no
# set has (git log -p -- src/ee_tooling/rules/): a board's hand copy of an
# older file keeps them in force below the markers.
RETIRED = frozenset(
    {
        "Differential pair coupled",
        "JLCPCB hole size",
        "JLCPCB hole to hole",
        "JLCPCB small via hole at no extra cost",
        "JLCPCB track width",
        "JLCPCB via annular ring",
        "JLCPCB via diameter",
        "JLCPCB via hole at no extra cost",
        "Mounting hole ring",
        "No right-angle bends",
    }
)
# A rule's name in a .kicad_dru.
RULE_NAME = re.compile(r'^[ \t]*\(rule[ \t]+"((?:[^"\\]|\\.)*)"', re.M)


# KiCad's lock file for a file it has open: ~NAME.EXT.lck beside it
# (libkicommon's "~*.lck").
LOCK = "~{}.lck"


def lock_of(path: Path) -> Path | None:
    """KiCad's lock file for path, if it is there."""
    lock = path.with_name(LOCK.format(path.name))
    return lock if lock.exists() else None


def lock_message(lock: str, what: str) -> str:
    """Why nothing is written while lock is there: KiCad has what open,
    or crashed with it open and left the lock behind."""
    return (
        f"{lock}: KiCad has {what} open, which it would overwrite; close it, then rerun.  KiCad"
        " leaves this lock file behind when it crashes: remove it when KiCad is not running"
    )


# The package's directory of rule sets, each a directory named for the
# ee_tooling version that introduced it (X.Y.Z), holding every profile's
# rules files and ERC_SETTINGS as that release shipped them.
RULE_SETS = "rules"
SET_NAME = re.compile(r"\d+\.\d+\.\d+")


def rules_directory() -> Traversable:
    """The package's directory of rule sets."""
    return importlib.resources.files("ee_tooling").joinpath(RULE_SETS)


def rule_sets() -> list[str]:
    """The rule sets' versions, oldest first."""
    names = [
        d.name for d in rules_directory().iterdir() if d.is_dir() and SET_NAME.fullmatch(d.name)
    ]
    return sorted(names, key=lambda v: tuple(int(n) for n in v.split(".")))


def current() -> str:
    """The newest rule set's version."""
    return rule_sets()[-1]


def rule_file(version: str, name: str) -> str:
    """The text of rule set version's file name."""
    return rules_directory().joinpath(version, name).read_text()


# fab.toml's [[board]] key that pins the board's rule set.
PIN = "rules"


def rule_set(pin: object) -> str:
    """The rule set a board's pin (its PIN value) names, the current one if
    None; ValueError if it is not a string or names no set."""
    if pin is None:
        return current()
    if not isinstance(pin, str):
        raise ValueError(f"{PIN!r} is not a string")
    if pin not in (sets := rule_sets()):
        raise ValueError(f"{PIN} {pin!r} is not one of {', '.join(sets)}")
    return pin


def pinned_message(name: str, pin: str | None) -> str | None:
    """What board name's pin says, where it holds the board to a set other
    than the current one; else None."""
    if pin is None or pin == current():
        return None
    return (
        f"{name}: ee_tooling's rules pinned at {pin} by fab.toml; the current rules are {current()}"
    )


def rule_names(rules: Rules, version: str) -> set[str]:
    """The names of the rules of the profile's .kicad_dru in rule set
    version."""
    return set(RULE_NAME.findall(rule_file(version, rules.dru)))


def retired(rules: Rules, version: str) -> frozenset[str]:
    """The names of the rules an older rules file of the profile's had
    (RETIRED, and each older set's) that rule set version does not."""
    older = rule_sets()[: rule_sets().index(version)]
    had = RETIRED.union(*(rule_names(rules, v) for v in older))
    return had - rule_names(rules, version)


def known(rules: Rules) -> frozenset[str]:
    """The names of every rule the profile's rules files have had, in any
    set or before them."""
    return RETIRED.union(*(rule_names(rules, v) for v in rule_sets()))


def sections(text: str) -> list[tuple[str, str]]:
    """A .kicad_dru's sections, each its name and its text from its heading
    to the next; the text before the first heading is named ""."""
    starts = [(m.start(), m.group(1)) for m in SECTION.finditer(text)]
    bounds = [(0, ""), *starts]
    ends = [s for s, _ in starts] + [len(text)]
    return [(name, text[start:end]) for (start, name), end in zip(bounds, ends, strict=True)]


def installed(name: str, rules: Rules, options: object, version: str) -> str:
    """The section installed for profile name from rule set version: its
    rules, without their version line, less each optional section whose
    option is false in options, between the markers."""
    leave_out = {s for option, s in rules.optional.items() if not getattr(options, option)}
    kept = "".join(t for n, t in sections(rule_file(version, rules.dru)) if n not in leave_out)
    body = VERSION_LINE.sub("", kept, count=1).strip("\n")
    recorded = "" if version == UNRECORDED else f"{SET_LINE.format(version)}\n"
    return f"{BEGIN.format(name)}\n{recorded}{body}\n{END}\n"


@dataclass(frozen=True)
class Dru:
    """A board's .kicad_dru, split: the section installed between the
    markers, if any, the board's own rules after them (all of them, without
    markers), and its own rules before them, each without the version
    line."""

    section: str | None
    own: str
    before: str = ""

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
        before, versions = VERSION_LINE.subn("", text[:start], count=1)
        after = text[end:] if versions else VERSION_LINE.sub("", text[end:], count=1)
        return cls(text[start:end] + "\n", after.strip("\n"), before.strip("\n"))

    def with_section(self, section: str) -> str:
        """The .kicad_dru with section installed: the version line, the
        board's rules from before the markers, the section, then the
        board's own rules after it."""
        before = f"{self.before}\n\n" if self.before else ""
        own = f"\n{self.own}\n" if self.own else ""
        return f"{VERSION}\n{before}{section}{own}"

    def copied(self, section: str) -> list[str]:
        """The names of the board's own rules after the section that the
        section has too: a copy of the profile's rules below the markers,
        which wins over the section."""
        names = set(RULE_NAME.findall(section))
        return [n for n in RULE_NAME.findall(self.own) if n in names]

    def retired(self, names: frozenset[str]) -> list[str]:
        """The names of the board's own rules, before or after the markers,
        that are among names, the
        rules ee_tooling's older rules files had and the board's rule set
        does not (retired): a copy of an older file, in force and no
        longer wanted."""
        return [n for n in RULE_NAME.findall(f"{self.before}\n{self.own}") if n in names]


def fragment(version: str, name: str) -> dict[str, Any]:
    """Rule set version's .kicad_pro fragment name; ValueError if it is not
    a JSON object."""
    found = json.loads(rule_file(version, name))
    if not isinstance(found, dict):
        raise ValueError(f"{RULE_SETS}/{version}/{name}: not a JSON object")
    return found


def board_setup(rules: Rules, version: str) -> dict[str, Any]:
    """The profile's Board Setup minimums in rule set version, a .kicad_pro
    fragment."""
    return fragment(version, rules.board_setup)


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
    "a.b.c: old -> new".  A list in least of one object stands for every
    object of the project's list there (each net class): a number one of
    them has is raised alike; one it lacks is left to KiCad's default, and
    no object is added."""
    out = copy.deepcopy(project)
    changes: list[str] = []

    def walk(have: dict[str, Any], want: dict[str, Any], where: str) -> None:
        for key, value in want.items():
            path = f"{where}{key}"
            if isinstance(value, list):
                found = have.get(key)
                for item in found if isinstance(found, list) else []:
                    if not isinstance(item, dict):
                        continue
                    for least_item in value:
                        for k, v in least_item.items():
                            old = item.get(k)
                            if (
                                isinstance(old, int | float)
                                and not isinstance(old, bool)
                                and old < v
                            ):
                                put(item, k, v)
                                changes.append(f"{path}[{item.get('name', '?')}].{k}: {old} -> {v}")
                continue
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
# an item replaces for it in KiCad's DRC; only where the Board Setup value
# constrains what the profile's rules of that kind do (kicad-cli 10.0.8):
# min_through_hole_diameter, min_hole_clearance: via, PTH and NPTH holes;
# min_via_annular_width: vias and PTH pads; min_hole_to_hole: hole pairs;
# min_text_height and min_text_thickness: silkscreen text, as the profile's
# silkscreen rules.  Not silk_clearance: Board Setup's min_silk_clearance
# holds silkscreen to silkscreen, the profile's rules silkscreen to pads (a
# footprint's own exempt).
BOARD_SETUP_KEYS = {
    "track_width": "min_track_width",
    "via_diameter": "min_via_diameter",
    "hole_size": "min_through_hole_diameter",
    "annular_width": "min_via_annular_width",
    "hole_to_hole": "min_hole_to_hole",
    "hole_clearance": "min_hole_clearance",
    "text_height": "min_text_height",
    "text_thickness": "min_text_thickness",
}
# A constraint's minimum in a .kicad_dru, in mm: its kind, its value.
MINIMUM = re.compile(r"\(constraint[ \t]+(\w+)[ \t]+\(min[ \t]+([0-9.]+)mm\)")
# Decimal places kept in a minimum written to a .kicad_dru (KiCad's 1 nm).
MM_PLACES = 6


def dropped(old: str | None, new: str, ours: frozenset[str]) -> tuple[int, list[str]]:
    """What replacing section old with new removes that is not ee_tooling's:
    the rules between the markers that neither new nor ours (known: every
    rules file of ee_tooling's) has, put there by hand; their count of
    non-blank lines and their names.  A value ee_tooling changed, or a rule
    of another set, is not counted."""
    if old is None:
        return 0, []
    ours = ours | set(RULE_NAME.findall(new))
    starts = [(m.start(), m.group(1)) for m in RULE_NAME.finditer(old)]
    ends = [s for s, _ in starts[1:]] + [len(old)]
    foreign = [
        (name, old[start:end])
        for (start, name), end in zip(starts, ends, strict=True)
        if name not in ours
    ]
    lines = sum(
        1
        for _, text in foreign
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    return lines, [name for name, _ in foreign]


def floored(section: str, project: dict[str, Any]) -> str:
    """section with each minimum of a kind Board Setup also holds raised to
    the project's Board Setup value where that is higher: a rule replaces
    Board Setup's constraint for what it matches, so a lower one would
    loosen the board's own limit there."""
    setup = project.get("board", {}).get("design_settings", {}).get("rules", {})

    def floor(m: re.Match[str]) -> str:
        key = BOARD_SETUP_KEYS.get(m.group(1))
        least = setup.get(key) if key is not None else None
        if (
            not isinstance(least, int | float)
            or isinstance(least, bool)
            or not math.isfinite(least)
        ):
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
    retired: list[str]  # the board's own rules ee_tooling's files no longer have


def install(
    dru: str | None,
    project: dict[str, Any],
    section: str,
    least: dict[str, Any],
    gone: frozenset[str],
) -> Installed:
    """The board's .kicad_dru (None if it has none) and .kicad_pro with
    Board Setup raised to least and section installed, each of its minimums
    floored to the raised Board Setup's (floored); gone, the rules the
    section's set retired (retired).  ValueError if the .kicad_dru's
    markers are not in order."""
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
        found.retired(gone),
    )


# A rule set's house ERC severities and connection grid, a .kicad_pro
# fragment; and KiCad's ERC severities, least strict first.
ERC_SETTINGS = "erc_severities.json"
SEVERITIES = ("ignore", "warning", "error")


def erc_settings(version: str) -> dict[str, Any]:
    """Rule set version's ERC severities and connection grid."""
    return fragment(version, ERC_SETTINGS)


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


def retired_message(path: str, names: list[str]) -> str:
    """What to do about the board's own rules in path that ee_tooling's
    rules files no longer have (Dru.retired)."""
    one = len(names) == 1
    return (
        f"{path}: {len(names)} {'rule' if one else 'rules'} outside ee_tooling's markers"
        f" {'is' if one else 'are'} retired from ee_tooling's rules and still in force:\n"
        + "".join(f"  {n}\n" for n in names)
        + f"remove {'this rule' if one else 'these rules'} from outside the markers in {path}"
    )


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
