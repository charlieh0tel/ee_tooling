"""JLCPCB: its Gerber options, its assembly BOM and placement (CPL) from
KiCad's, and its corrections to KiCad's placement, by LCSC part
(parts.toml)."""

import csv
import importlib.resources
import math
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ee_tooling.profiles.profile import GerberOptions, Part, Placement

BOM_HEADER = ("Comment", "Designator", "Footprint", "LCSC Part #")
CPL_HEADER = ("Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer")


@dataclass(frozen=True)
class Corrections:
    """JLCPCB's corrections to KiCad's placement, by LCSC part (parts.toml)."""

    rotation: dict[str, float]  # degrees added to KiCad's rotation
    offset: dict[str, tuple[float, float]]  # mm added, in the part's frame (rotation 0, y up)
    unchecked: dict[str, str]  # part description, for parts with no correction known yet

    @classmethod
    def load(cls, text: str) -> "Corrections":
        t = tomllib.loads(text)
        return cls(
            rotation={k: float(v) for k, v in t.get("rotation", {}).items()},
            offset={k: (float(v[0]), float(v[1])) for k, v in t.get("offset", {}).items()},
            unchecked=dict(t.get("unchecked", {})),
        )

    @classmethod
    def builtin(cls) -> "Corrections":
        return cls.load(importlib.resources.files("ee_tooling").joinpath("parts.toml").read_text())


@dataclass(frozen=True)
class Options:
    """A board's [board.jlcpcb] options."""

    require_lcsc: bool = False  # a fitted part with no LCSC number refuses the release


def jlcpcb_bom(parts: Iterable[Part]) -> tuple[list[list[str]], dict[str, str], list[str]]:
    """The parts in JLCPCB's columns, grouped by Value, Footprint and LCSC
    in the order of each group's first part, as kicad-cli's --group-by
    groups them.  kicad-cli groups on the fields' text before text variables
    (${...}) are resolved; these are resolved.

    Returns the rows, each designator's LCSC part, and the designators of
    rows with no LCSC part.
    """
    groups: dict[tuple[str, str, str], list[Part]] = {}
    for p in parts:
        groups.setdefault((p.value, p.footprint, p.lcsc), []).append(p)
    out = []
    refs: dict[str, str] = {}
    missing = []
    for group in groups.values():
        first = group[0]
        designators = ",".join(p.reference for p in group)
        out.append([first.value, designators, first.footprint.split(":", 1)[-1], first.lcsc])
        refs.update((p.reference, first.lcsc) for p in group)
        if not first.lcsc:
            missing.append(designators)
    return out, refs, missing


def jlcpcb_cpl(
    placements: Iterable[Placement], refs: dict[str, str], corrections: Corrections
) -> tuple[list[list[str]], set[str]]:
    """The placements in JLCPCB's columns, for the parts in refs.

    Returns the rows and the designators placed.
    """
    out = []
    placed = set()
    for p in placements:
        if p.reference not in refs:
            continue
        lcsc = refs[p.reference]
        ox, oy = corrections.offset.get(lcsc, (0.0, 0.0))
        t = math.radians(p.rotation)
        x = p.x + ox * math.cos(t) - oy * math.sin(t)
        y = p.y + ox * math.sin(t) + oy * math.cos(t)
        rot = (p.rotation + corrections.rotation.get(lcsc, 0.0)) % 360
        layer = "Top" if p.side.lower() == "top" else "Bottom"
        out.append([p.reference, p.value, p.package, f"{x:.4f}mm", f"{y:.4f}mm", f"{rot:g}", layer])
        placed.add(p.reference)
    return out, placed


def write_csv(out: Path, header: Iterable[str], rows: Iterable[list[str]]) -> None:
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


class Jlcpcb:
    """JLCPCB's PCB and assembly service."""

    name = "jlcpcb"
    gerbers = GerberOptions(
        x2=False, protel_extensions=True, subtract_soldermask=True, separate_drill=True
    )
    distributor: tuple[str, str] | None = ("LCSC", "LCSC")

    def __init__(self, corrections: Corrections) -> None:
        self.corrections = corrections

    @classmethod
    def builtin(cls) -> "Jlcpcb":
        """The profile with parts.toml's corrections."""
        return cls(Corrections.builtin())

    def options(self, table: dict[str, Any]) -> Options:
        """[board.jlcpcb]: require_lcsc, a boolean, default false."""
        known = {f.name for f in fields(Options)}
        if unknown := sorted(set(table) - known):
            raise ValueError(f"unknown key {', '.join(repr(k) for k in unknown)}")
        for key, found in table.items():
            if not isinstance(found, bool):
                raise ValueError(f"{key!r} is not true or false")
        return Options(**table)

    def check(self, parts: list[Part], placements: list[Placement], options: Options) -> list[str]:
        """With require_lcsc, a fitted part with no LCSC number refuses the
        release, naming each."""
        _, _, missing = jlcpcb_bom(parts)
        if options.require_lcsc and missing:
            return [f"no LCSC part number for {', '.join(missing)} (require_lcsc)"]
        return []

    def assembly(
        self,
        dest: Path,
        base: str,
        parts: list[Part],
        placements: list[Placement],
        options: Options,
    ) -> list[Path]:
        """Its BOM, without not-fitted parts, and its CPL of the BOM's parts,
        with its corrections."""
        bom_csv = dest / f"{base}-bom.csv"
        cpl_csv = dest / f"{base}-cpl.csv"
        bom, refs, missing = jlcpcb_bom(parts)
        write_csv(bom_csv, BOM_HEADER, bom)
        if missing:
            print("warning: no LCSC part number for", ", ".join(missing))
        cpl, placed = jlcpcb_cpl(placements, refs, self.corrections)
        write_csv(cpl_csv, CPL_HEADER, cpl)
        for lcsc, desc in self.corrections.unchecked.items():
            if lcsc in refs.values():
                print(f"check {desc} ({lcsc}) in JLCPCB's placement preview: no correction known")
        if unplaced := set(refs) - placed:
            sys.exit(f"in the BOM but not placed: {', '.join(sorted(unplaced))}")
        return [bom_csv, cpl_csv]
