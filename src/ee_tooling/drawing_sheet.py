"""A KiCad 10 board's drawing sheet: its paper, and the frame and title
block of KiCad's default drawing sheet on it (docs/stackup-table.md, The
drawing frame).

The paper sizes, the frame and the title block are as kicad-cli 10 plots
them (pcb export svg of a board with each (paper ...)), to 0.01 mm.  A
project's own drawing sheet (.kicad_wks) is not read; custom_sheet names
one, so that a caller can say so.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ee_tooling.placement import Box
from ee_tooling.sexpr import Expression, child, number

# Each named paper size KiCad 10 has, landscape: width and height, mm.
PAPER = {
    "A5": (210.0, 148.0),
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "A": (279.4, 215.9),
    "B": (431.8, 279.4),
    "C": (558.8, 431.8),
    "D": (863.6, 558.8),
    "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9),
    "USLegal": (355.6, 215.9),
    "USLedger": (431.8, 279.4),
    "GERBER": (812.8, 812.8),
}
# The paper of a board with no (paper ...), and the name of a size given
# as (paper "User" WIDTH HEIGHT).  Portrait puts the shorter side across,
# a User size's too.
DEFAULT_PAPER = "A4"
USER = "User"
PORTRAIT = "portrait"
# KiCad's default drawing sheet: its frame (inside the border's 2 mm
# ruler) this far in from each edge of the paper, and its title block
# this wide and high, in the frame's bottom right corner, mm.
FRAME_INSET = 12.0
TITLE_BLOCK_WIDTH = 108.0
TITLE_BLOCK_HEIGHT = 32.0
# Where a project file (.kicad_pro) names the board's own drawing sheet.
PROJECT_SECTION = "pcbnew"
PROJECT_SHEET = "page_layout_descr_file"


class SheetError(ValueError):
    """A board's (paper ...) KiCad 10 does not have."""


@dataclass(frozen=True)
class Sheet:
    """A board's paper, as KiCad lays it out: the page's top left corner
    is the board's origin."""

    paper: str  # its name, as (paper ...) gives it
    width: float  # mm
    height: float  # mm

    @property
    def frame(self) -> Box:
        """The area inside the default drawing sheet's frame."""
        return Box(FRAME_INSET, FRAME_INSET, self.width - FRAME_INSET, self.height - FRAME_INSET)

    @property
    def title_block(self) -> Box:
        f = self.frame
        return Box(f.right - TITLE_BLOCK_WIDTH, f.bottom - TITLE_BLOCK_HEIGHT, f.right, f.bottom)


def sheet(board: Expression) -> Sheet:
    """The board's paper, from its (paper ...)."""
    paper = child(board, "paper")
    if paper is None:
        width, height = PAPER[DEFAULT_PAPER]
        return Sheet(DEFAULT_PAPER, width, height)
    name = str(paper[1]) if len(paper) > 1 else ""
    if name == USER:
        if len(paper) < 4:
            raise SheetError(f"(paper {USER!r}) without a width and height")
        width, height = number(paper[2]), number(paper[3])
    elif name in PAPER:
        width, height = PAPER[name]
    else:
        raise SheetError(f"paper {name!r}, a size KiCad 10 does not have")
    if PORTRAIT in paper[2:]:
        width, height = min(width, height), max(width, height)
    return Sheet(name, width, height)


def custom_sheet(project: Path) -> str:
    """The drawing sheet a project file names for its board; "" for
    KiCad's default, or no project file."""
    if not project.is_file():
        return ""
    found = json.loads(project.read_text(encoding="utf-8"))
    return str(found.get(PROJECT_SECTION, {}).get(PROJECT_SHEET, "") or "")
