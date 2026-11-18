"""JLCPCB: its assembly BOM and placement (CPL) from KiCad's, and its
corrections to KiCad's placement, by LCSC part (parts.toml)."""

import importlib.resources
import math
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass

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
