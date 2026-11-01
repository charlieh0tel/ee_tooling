"""A board's fabrication documents, from its KiCad files and the files
kicad-cli writes for the release: the Gerber map.

The Gerber map comes from KiCad's Gerber job file (.gbrjob), which names
each Gerber's X2 file function even when the Gerbers have no X2, and from
the Excellon files' TF.FileFunction comments.
"""

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

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
