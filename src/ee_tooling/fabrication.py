"""A board's fabrication documents, from its KiCad files and the files
kicad-cli writes for the release: the Gerber map and the stackup.

The Gerber map comes from KiCad's Gerber job file (.gbrjob), which names
each Gerber's X2 file function even when the Gerbers have no X2, and from
the Excellon files' TF.FileFunction comments.  The stackup comes from the
board's (setup (stackup ...)), its impedance requirements from the
project's net classes whose names say impedance.
"""

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ee_tooling.kicad_style_netclass import (
    DIFF_CLASS,
    DIFF_CLASS_FORM,
    IMPEDANCE,
    IMPEDANCE_CLASS,
    SE_CLASS_FORM,
)
from ee_tooling.sexpr import Atom, Expression, Item, child, children, number

GERBER_JOB_SUFFIX = ".gbrjob"
DRILL_SUFFIX = ".drl"
# An Excellon file's X2 file function comment, e.g. "Plated,1,2,PTH".
DRILL_FUNCTION = re.compile(r"^; #@! TF\.FileFunction,(.*)$", re.M)
# The words for a Gerber X2 file function's type and side.
FUNCTIONS = {
    "Copper": "copper",
    "SolderMask": "solder mask",
    "SolderPaste": "solder paste",
    "Legend": "silkscreen",
    "Profile": "board outline",
    "Plated": "drill, plated",
    "NonPlated": "drill, non-plated",
}
SIDES = {"Top": "top", "Bot": "bottom", "Inr": "inner"}
# What stands in an empty cell.
NONE = "-"
MAP_HEADER = ("File", "Layer", "Function", "Side", "Polarity")


@dataclass(frozen=True)
class GerberFile:
    """A file in the Gerber zip."""

    path: str  # its name in the zip
    function: tuple[str, ...]  # its X2 file function, split at commas; () for the job file
    polarity: str  # Positive or Negative; "" if none
    layer: str  # the KiCad layer it is, or ""

    @property
    def description(self) -> str:
        if not self.function:
            return "Gerber job file (JSON)"
        kind, *rest = self.function
        words = FUNCTIONS.get(kind, kind)
        match kind:
            case "Copper":
                return f"{words} {rest[0]}"
            case "Plated" | "NonPlated":
                return f"{words} ({rest[2]}), L{rest[0]} to L{rest[1]}"
        return words

    @property
    def side(self) -> str:
        if self.function and self.function[0] == "Copper":
            return SIDES.get(self.function[2], self.function[2])
        if len(self.function) > 1 and self.function[1] in SIDES:
            return SIDES[self.function[1]]
        return ""


def table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A fixed-width text table; an empty cell shows NONE."""
    cells = [list(header), *([c or NONE for c in r] for r in rows)]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines) + "\n"


def gerber_files(directory: Path, name: str, layers: Iterable[str]) -> list[GerberFile]:
    """The Gerber, drill and job files kicad-cli wrote into directory for
    the KiCad project name, from its Gerber job file and the drill files'
    comments: the Gerbers in the job's order, then the drill files, then
    the job file."""
    jobs = sorted(directory.glob(f"*{GERBER_JOB_SUFFIX}"))
    if len(jobs) != 1:
        raise ValueError(f"{directory}: {len(jobs)} Gerber job files, not one")
    job = json.loads(jobs[0].read_text())
    by_stem = {f"{name}-{layer.replace('.', '_')}": layer for layer in layers}
    out = [
        GerberFile(
            path=a["Path"],
            function=tuple(a["FileFunction"].split(",")),
            polarity=a.get("FilePolarity", ""),
            layer=by_stem.get(Path(a["Path"]).stem, ""),
        )
        for a in job["FilesAttributes"]
    ]
    for drill in sorted(directory.glob(f"*{DRILL_SUFFIX}")):
        m = DRILL_FUNCTION.search(drill.read_text())
        if m is None:
            raise ValueError(f"{drill}: no TF.FileFunction comment")
        out.append(GerberFile(drill.name, tuple(m.group(1).strip().split(",")), "", ""))
    out.append(GerberFile(jobs[0].name, (), "", ""))
    return out


def gerber_map(title: str, files: Iterable[GerberFile]) -> str:
    """What each file in the Gerber zip is."""
    rows = [(f.path, f.layer, f.description, f.side, f.polarity.lower()) for f in files]
    return (
        f"{title}\n\n"
        "Gerber RS-274X, mm, 4.6 format; Excellon drill, mm, decimal; finished hole sizes.\n"
        "Copper layers L1 (top) to Ln (bottom).\n\n" + table(MAP_HEADER, rows)
    )


# A stackup layer's (type) that is copper, and the Gerber X2 file function
# of each other type that has a Gerber.
COPPER = "copper"
STACKUP_FUNCTIONS = {
    "Top Silk Screen": ("Legend", "Top"),
    "Top Solder Paste": ("SolderPaste", "Top"),
    "Top Solder Mask": ("SolderMask", "Top"),
    "Bottom Solder Mask": ("SolderMask", "Bot"),
    "Bottom Solder Paste": ("SolderPaste", "Bot"),
    "Bottom Silk Screen": ("Legend", "Bot"),
}
# The atom that starts a dielectric layer's next sublayer.
SUBLAYER = "addsublayer"
# The board-wide stackup settings the stackup document gives, by keyword.
STACKUP_SETTINGS = {
    "copper_finish": "Copper finish",
    "dielectric_constraints": "Impedance-controlled dielectric",
    "edge_connector": "Edge connector",
    "castellated_pads": "Castellated pads",
    "edge_plating": "Edge plating",
}
# The ohms in a class name that says impedance.
OHMS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:Z|R|OHMS?)", re.I)
DIFFERENTIAL = "differential"
SINGLE_ENDED = "single-ended"
HOUSE_FORMS = f"{DIFF_CLASS_FORM} or {SE_CLASS_FORM}"
# What marks a value the release cannot know, for the owner to fill in.
TBD = "[TBD: {}]"
STACKUP_HEADER = (
    "Ln",
    "Layer",
    "Type",
    "Material",
    "Thickness mm",
    "Er",
    "Loss tan",
    "Color",
    "File",
)
IMPEDANCE_HEADER = ("Net class", "Ohms", "Kind", "Track mm", "Pair width mm", "Pair gap mm")


@dataclass(frozen=True)
class StackupLayer:
    """One layer, or one sublayer of a dielectric, of a board's stackup."""

    name: str  # KiCad's: F.Cu, dielectric 1, F.Mask, ...
    type: str  # KiCad's: copper, core, prepreg, Top Solder Mask, ...
    thickness: float | None  # mm
    material: str
    epsilon_r: float | None
    loss_tangent: float | None
    color: str


@dataclass(frozen=True)
class Stackup:
    """A board's (setup (stackup ...)), top to bottom."""

    layers: tuple[StackupLayer, ...]
    settings: dict[str, str]  # STACKUP_SETTINGS keyword to its value, those present

    @property
    def thickness(self) -> float:
        return sum(x.thickness or 0.0 for x in self.layers)


@dataclass(frozen=True)
class Impedance:
    """A net class whose name says impedance (kicad_style_netclass.IMPEDANCE)."""

    net_class: str
    ohms: str  # "" if the name gives none
    kind: str  # DIFFERENTIAL or SINGLE_ENDED; "" if not named in the house form
    track_width: float | None  # mm
    pair_width: float | None
    pair_gap: float | None


def text_of(item: Item) -> str:
    return item if isinstance(item, str) else str(item)


def sublayers(layer: Expression) -> list[dict[str, Item]]:
    """A stackup layer's properties, one dict per sublayer, by keyword."""
    out: list[dict[str, Item]] = [{}]
    for item in layer[2:]:
        if isinstance(item, Atom) and item == SUBLAYER:
            out.append({})
        elif isinstance(item, list) and len(item) > 1 and isinstance(item[0], str):
            out[-1][item[0]] = item[1]
    return out


def stackup(pcb: Expression) -> Stackup | None:
    """The board's stackup, or None if it has none."""
    setup = child(pcb, "setup")
    section = child(setup, "stackup") if setup is not None else None
    if section is None:
        return None
    layers = []
    for layer in children(section, "layer"):
        props = sublayers(layer)
        kind = text_of(props[0].get("type", ""))
        for p in props:
            layers.append(
                StackupLayer(
                    name=text_of(layer[1]),
                    type=kind,
                    thickness=number(p["thickness"]) if "thickness" in p else None,
                    material=text_of(p.get("material", "")),
                    epsilon_r=number(p["epsilon_r"]) if "epsilon_r" in p else None,
                    loss_tangent=number(p["loss_tangent"]) if "loss_tangent" in p else None,
                    color=text_of(p.get("color", "")),
                )
            )
    settings = {
        k: text_of(found[1])
        for k in STACKUP_SETTINGS
        if (found := child(section, k)) is not None and len(found) > 1
    }
    return Stackup(tuple(layers), settings)


def board_thickness(pcb: Expression) -> float | None:
    """The board's (general (thickness)), mm."""
    general = child(pcb, "general")
    found = child(general, "thickness") if general is not None else None
    return number(found[1]) if found is not None else None


def impedances(project: dict[str, Any]) -> list[Impedance]:
    """The project's net classes whose names say impedance, in its order."""
    out = []
    for c in project.get("net_settings", {}).get("classes") or []:
        name = str(c.get("name", ""))
        if not IMPEDANCE.search(name):
            continue
        m = OHMS.search(name)
        if DIFF_CLASS.match(name):
            kind = DIFFERENTIAL
        elif IMPEDANCE_CLASS.match(name):
            kind = SINGLE_ENDED
        else:
            kind = ""
        out.append(
            Impedance(
                net_class=name,
                ohms=m.group(1) if m else "",
                kind=kind,
                track_width=c.get("track_width"),
                pair_width=c.get("diff_pair_width"),
                pair_gap=c.get("diff_pair_gap"),
            )
        )
    return out


def mm(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def stackup_file(layer: StackupLayer, copper: int | None, files: Iterable[GerberFile]) -> str:
    """The Gerber written for a stackup layer, copper being its L number."""
    key = ("Copper", f"L{copper}") if copper is not None else STACKUP_FUNCTIONS.get(layer.type)
    if key is None:
        return ""
    return next((f.path for f in files if f.function[: len(key)] == key), "")


def impedance_document(rows: Sequence[Impedance]) -> str:
    if not rows:
        return f"Impedance control: none (no net class named {HOUSE_FORMS}).\n"
    cells = [
        (
            i.net_class,
            i.ohms or TBD.format("ohms"),
            i.kind or TBD.format(f"name not {HOUSE_FORMS}"),
            mm(i.track_width) if i.kind != DIFFERENTIAL else "",
            mm(i.pair_width) if i.kind != SINGLE_ENDED else "",
            mm(i.pair_gap) if i.kind != SINGLE_ENDED else "",
        )
        for i in rows
    ]
    return (
        "Impedance requirements, from the net classes.  JLCPCB's impedance calculator\n"
        "sets the widths for its stackup; the design's are below.\n\n"
        + table(IMPEDANCE_HEADER, cells)
    )


def stackup_document(
    title: str,
    found: Stackup | None,
    thickness: float | None,
    files: Sequence[GerberFile],
    impedance: Sequence[Impedance],
) -> str:
    """The stackup, each layer's Gerber, its settings and the impedance
    requirements; thickness is the board's thickness setting, mm."""
    setting = f"{mm(thickness)} mm" if thickness is not None else "none"
    if found is None:
        body = (
            "The board has no stackup: set one in KiCad's Board Setup, Physical Stackup.\n"
            f"Board thickness setting: {setting}.\n"
        )
    else:
        rows = []
        copper = 0
        for x in found.layers:
            n = None
            if x.type == COPPER:
                copper += 1
                n = copper
            rows.append(
                (
                    f"L{n}" if n is not None else "",
                    x.name,
                    x.type,
                    x.material,
                    mm(x.thickness),
                    mm(x.epsilon_r),
                    mm(x.loss_tangent),
                    x.color,
                    stackup_file(x, n, files),
                )
            )
        settings = "".join(f"{STACKUP_SETTINGS[k]}: {v}\n" for k, v in found.settings.items())
        body = (
            table(STACKUP_HEADER, rows)
            + f"\nTotal thickness: {mm(found.thickness)} mm"
            + f" (board thickness setting: {setting}).\n"
            + settings
        )
    return f"{title}\n\n{body}\n{impedance_document(impedance)}"
