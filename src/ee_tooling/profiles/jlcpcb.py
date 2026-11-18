"""JLCPCB: its Gerber options, its assembly BOM and placement (CPL) from
KiCad's, and its corrections to KiCad's placement, by LCSC part
(parts.toml)."""

import csv
import importlib.resources
import io
import math
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ee_tooling.profiles import GerberOptions

if TYPE_CHECKING:
    from ee_tooling.fab_release import Release

BOM_HEADER = ("Comment", "Designator", "Footprint", "LCSC Part #")
CPL_HEADER = ("Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer")


@dataclass(frozen=True)
class Parts:
    """JLCPCB's corrections to KiCad's placement, by LCSC part (parts.toml)."""

    rotation: dict[str, float]  # degrees added to KiCad's rotation
    offset: dict[str, tuple[float, float]]  # mm added, in the part's frame (rotation 0, y up)
    unchecked: dict[str, str]  # part description, for parts with no correction known yet

    @classmethod
    def load(cls, text: str) -> "Parts":
        t = tomllib.loads(text)
        return cls(
            rotation={k: float(v) for k, v in t.get("rotation", {}).items()},
            offset={k: (float(v[0]), float(v[1])) for k, v in t.get("offset", {}).items()},
            unchecked=dict(t.get("unchecked", {})),
        )

    @classmethod
    def builtin(cls) -> "Parts":
        return cls.load(importlib.resources.files("ee_tooling").joinpath("parts.toml").read_text())


def jlcpcb_bom(rows: Iterable[dict[str, str]]) -> tuple[list[list[str]], dict[str, str], list[str]]:
    """KiCad's grouped BOM in JLCPCB's columns.

    Returns the rows, each designator's LCSC part, and the designators of
    rows with no LCSC part.
    """
    out = []
    refs: dict[str, str] = {}
    missing = []
    for r in rows:
        lcsc = r["LCSC Part #"]
        out.append([r["Comment"], r["Designator"], r["Footprint"].split(":", 1)[-1], lcsc])
        for x in r["Designator"].split(","):
            refs[x.strip()] = lcsc
        if not lcsc:
            missing.append(r["Designator"])
    return out, refs, missing


def jlcpcb_cpl(
    rows: Iterable[dict[str, str]], refs: dict[str, str], parts: Parts
) -> tuple[list[list[str]], set[str]]:
    """KiCad's placement in JLCPCB's columns, for the parts in refs.

    Returns the rows and the designators placed.
    """
    out = []
    placed = set()
    for r in rows:
        ref = r["Ref"]
        if ref not in refs:
            continue
        lcsc = refs[ref]
        kicad_rot = float(r["Rot"])
        ox, oy = parts.offset.get(lcsc, (0.0, 0.0))
        t = math.radians(kicad_rot)
        x = float(r["PosX"]) + ox * math.cos(t) - oy * math.sin(t)
        y = float(r["PosY"]) + ox * math.sin(t) + oy * math.cos(t)
        rot = (kicad_rot + parts.rotation.get(lcsc, 0.0)) % 360
        layer = "Top" if r["Side"].lower() == "top" else "Bottom"
        out.append([ref, r["Val"], r["Package"], f"{x:.4f}mm", f"{y:.4f}mm", f"{rot:g}", layer])
        placed.add(ref)
    return out, placed


def write_csv(out: Path, header: Iterable[str], rows: Iterable[list[str]]) -> None:
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def bom(release: "Release", out: Path) -> dict[str, str]:
    """JLCPCB BOM, without not-fitted parts; return its designators' LCSC parts."""
    raw = release.work / "bom.csv"
    release.kicad(
        "sch",
        "export",
        "bom",
        "--exclude-dnp",
        "--fields",
        "Value,Reference,Footprint,LCSC,${QUANTITY}",
        "--labels",
        "Comment,Designator,Footprint,LCSC Part #,Qty",
        "--group-by",
        "Value,Footprint,LCSC",
        "--ref-range-delimiter",
        "",
        "-o",
        str(raw),
        release.path(release.board.sch),
    )
    rows, refs, missing = jlcpcb_bom(csv.DictReader(io.StringIO(raw.read_text())))
    write_csv(out, BOM_HEADER, rows)
    if missing:
        print("warning: no LCSC part number for", ", ".join(missing))
    return refs


def cpl(release: "Release", out: Path, refs: dict[str, str], parts: Parts) -> None:
    """JLCPCB placement for the parts in the BOM."""
    raw = release.work / "pos.csv"
    release.kicad(
        "pcb",
        "export",
        "pos",
        "--format",
        "csv",
        "--units",
        "mm",
        "--side",
        "both",
        "--exclude-dnp",
        "-o",
        str(raw),
        release.path(release.board.pcb),
    )
    rows, placed = jlcpcb_cpl(csv.DictReader(io.StringIO(raw.read_text())), refs, parts)
    write_csv(out, CPL_HEADER, rows)
    for lcsc, part in parts.unchecked.items():
        if lcsc in refs.values():
            print(f"check {part} ({lcsc}) in JLCPCB's placement preview: no correction known")
    if unplaced := set(refs) - placed:
        sys.exit(f"in the BOM but not placed: {', '.join(sorted(unplaced))}")


class Jlcpcb:
    """JLCPCB's PCB and assembly service."""

    name = "jlcpcb"
    gerbers = GerberOptions(
        x2=False, protel_extensions=True, subtract_soldermask=True, separate_drill=True
    )
    distributor: tuple[str, str] | None = ("LCSC", "LCSC")

    def __init__(self, parts: Parts) -> None:
        self.parts = parts

    @classmethod
    def builtin(cls) -> "Jlcpcb":
        """The profile with parts.toml's corrections."""
        return cls(Parts.builtin())

    def assembly(self, release: "Release", dest: Path, base: str) -> list[Path]:
        """Its BOM and CPL, with its parts' corrections."""
        bom_csv = dest / f"{base}-bom.csv"
        cpl_csv = dest / f"{base}-cpl.csv"
        refs = bom(release, bom_csv)
        cpl(release, cpl_csv, refs, self.parts)
        return [bom_csv, cpl_csv]
