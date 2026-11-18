"""Write a KiCad 10 board's stackup table from its physical stackup
(docs/stackup-table.md).

stackup-table BOARD [-o OUT] [--at X,Y] [--check]: the table KiCad's
Place > Add Stackup Table makes, on User.Drawings, from the stackup in
Board Setup.  A board without one gets one, its top left corner at --at,
else at the first of the spots (spots) where it lies inside the drawing
sheet's frame, clear of its title block and the board outline.  Each
stackup table it has (kicad_style_stackup.tables) whose cells or layer
differ from what the stackup gives is rewritten where it is, or at --at;
one on another layer is moved to User.Drawings.  A table kept where it
is, or put at --at, that leaves the frame is said on stderr.  Only the
table's text in the file changes; the rest is kept byte for byte.
Thicknesses are in mm.

Column widths are estimated from the stroke font's advances, not
measured as KiCad's own table sizes them, so they can differ from the
GUI's by a fraction of a millimeter.
"""

import argparse
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from ee_tooling.drawing_sheet import Sheet, SheetError, custom_sheet, sheet
from ee_tooling.fabrication import (
    COPPER,
    DIELECTRICS,
    MASK_TYPES,
    SILK_TYPES,
    Stackup,
    mm,
    stackup,
)
from ee_tooling.kicad_style_board import Point, edge, parse_point, where
from ee_tooling.kicad_style_stackup import (
    EPSILON_R,
    HEADER,
    LAYER,
    LAYER_NAME,
    LOSS_TANGENT,
    OLD_GROUP,
    THICKNESS,
    rows,
    tables,
)
from ee_tooling.placement import Box
from ee_tooling.sexpr import (
    Atom,
    Expression,
    Item,
    ParseError,
    child,
    children,
    parse,
    spans,
    write,
)

# A cell's text as KiCad 10's stackup table has it
# (pcbnew/board_tables/board_stackup_table.cpp): its size, the stroke
# thickness of the header's and of the rows', mm.
TEXT_SIZE = 1.5
HEADER_THICKNESS = 0.3
ROW_THICKNESS = 0.2
# A cell's margin on each side, and the header's and a row's height, mm,
# as KiCad 10's own table has them for that text.
MARGIN = 1.0025
HEADER_HEIGHT = 4.305
ROW_HEIGHT = 4.205
# The border's and the separators' width, mm (KiCad's DEFAULT_LINE_WIDTH).
LINE_WIDTH = 0.1
# The columns KiCad right-justifies.
RIGHT = frozenset({THICKNESS, EPSILON_R, LOSS_TANGENT})
# What KiCad prints for a value a layer's type has but the stackup leaves
# unset, or a thickness its type does not have.
NOT_SPECIFIED = "Not specified"
# A new table this far from the board outline, or in from the drawing
# sheet's frame, mm.
GAP = 5.0
# Each printable ASCII character's advance in KiCad 10's stroke font, in
# EMths of the text size (measured with pcbnew's PCB_TEXT); any other
# character is taken as the widest.
EM = 21
ADVANCES = {
    8: "`",
    10: "ijI!',.:;",
    11: "l",
    12: "ft^",
    13: "r",
    14: "()[\\]{}",
    15: "~",
    16: 'vyJT"*_ ',
    17: "ksxzL",
    18: "ceAFVY?",
    19: "abdghnopquE",
    20: "0123456789SXZ$|",
    21: "BCDGKPR#",
    22: "wHNOQU/",
    24: "MW%",
    26: "&+-<=>",
    27: "@",
    28: "m",
}
ADVANCE = {c: units for units, chars in ADVANCES.items() for c in chars}
WIDEST = max(ADVANCES)
# A text's width is taken as its advances and this many stroke
# thicknesses.  Its extent, measured with pcbnew, is at most its advances
# and two; KiCad wraps a cell's text wider than the cell's inside.
STROKES = 3


@dataclass(frozen=True)
class Defaults:
    """What KiCad 10 prints for a stackup layer of a type when the stackup
    does not say: as its stackup table shows it, and Board Setup's FR4."""

    material: str  # "" for a type with no material
    color: str  # "" for a type with no color
    thickness: bool  # False for a type with no thickness
    epsilon_r: float
    loss_tangent: float


COPPER_DEFAULTS = Defaults("", "", True, 1.0, 0.0)
DIELECTRIC_DEFAULTS = Defaults("FR4", NOT_SPECIFIED, True, 4.5, 0.02)
MASK_DEFAULTS = Defaults(NOT_SPECIFIED, NOT_SPECIFIED, True, 3.3, 0.0)
SILK_DEFAULTS = Defaults(NOT_SPECIFIED, NOT_SPECIFIED, False, 1.0, 0.0)
# Solder paste, and any type KiCad 10 does not have.
OTHER_DEFAULTS = Defaults("", "", False, 1.0, 0.0)


class TableError(ValueError):
    """A board whose stackup table cannot be written."""


@dataclass
class Result:
    """A board's text with its stackup tables written, and what changed."""

    text: str
    changes: list[str] = field(default_factory=list)  # what is written, one line each
    notes: list[str] = field(default_factory=list)  # for stderr


def defaults(kind: str) -> Defaults:
    if kind == COPPER:
        return COPPER_DEFAULTS
    if kind in DIELECTRICS:
        return DIELECTRIC_DEFAULTS
    if kind in MASK_TYPES.values():
        return MASK_DEFAULTS
    if kind in SILK_TYPES.values():
        return SILK_DEFAULTS
    return OTHER_DEFAULTS


def layer_names(board: Expression) -> dict[str, str]:
    """Each board layer's user name, by its canonical name, those that
    have one: (layers (5 "F.SilkS" user "F.Silkscreen") ...)."""
    found = child(board, "layers") or []
    return {
        str(layer[1]): str(layer[3])
        for layer in found[1:]
        if isinstance(layer, list) and len(layer) > 3 and not isinstance(layer[3], Atom | list)
    }


def stackup_rows(found: Stackup, names: dict[str, str]) -> list[list[str]]:
    """The stackup table's rows KiCad 10 gives the stackup, top first: a
    dielectric's name capitalized and, with sublayers, numbered."""
    counts = Counter(x.name for x in found.layers)
    seen: Counter[str] = Counter()
    out = []
    for x in found.layers:
        seen[x.name] += 1
        given = defaults(x.type)
        if x.type in DIELECTRICS:
            name = x.name.capitalize()
            if counts[x.name] > 1:
                name += f" ({seen[x.name]}/{counts[x.name]})"
        else:
            name = names.get(x.name, x.name)
        er = given.epsilon_r if x.epsilon_r is None else x.epsilon_r
        loss = given.loss_tangent if x.loss_tangent is None else x.loss_tangent
        out.append(
            [
                name,
                x.type.capitalize(),
                x.material or given.material,
                f"{mm(x.thickness or 0.0)} mm" if given.thickness else NOT_SPECIFIED,
                x.color or given.color,
                f"{er:g}",
                f"{loss:g}",
            ]
        )
    return out


def text_width(text: str, thickness: float) -> float:
    """text's width at TEXT_SIZE, estimated, mm."""
    units = sum(ADVANCE.get(c, WIDEST) for c in text)
    return TEXT_SIZE * units / EM + STROKES * thickness


def column_widths(texts: list[list[str]]) -> list[float]:
    """Each column wide enough for its widest text, the header first."""
    return [
        round(
            max(
                text_width(row[column], HEADER_THICKNESS if n == 0 else ROW_THICKNESS)
                for n, row in enumerate(texts)
            )
            + 2 * MARGIN,
            4,
        )
        for column in range(len(HEADER))
    ]


def row_heights(texts: list[list[str]]) -> list[float]:
    """Each row's height, the header first."""
    return [HEADER_HEIGHT] + [ROW_HEIGHT] * (len(texts) - 1)


def node(keyword: str, *items: Item) -> Expression:
    return [Atom(keyword), *items]


def stroke() -> Expression:
    return node("stroke", node("width", LINE_WIDTH), node("type", Atom("default")))


def cell(text: str, start: Point, end: Point, header: bool, right: bool, id_: str) -> Expression:
    return node(
        "table_cell",
        text,
        node("start", *start),
        node("end", *end),
        node("margins", MARGIN, MARGIN, MARGIN, MARGIN),
        node("span", 1, 1),
        node("layer", LAYER),
        node("uuid", id_),
        node(
            "effects",
            node(
                "font",
                node("size", TEXT_SIZE, TEXT_SIZE),
                node("thickness", HEADER_THICKNESS if header else ROW_THICKNESS),
            ),
            node("justify", Atom("right" if right and not header else "left")),
        ),
    )


def build(texts: list[list[str]], origin: Point, old: Expression | None) -> Expression:
    """A stackup table of texts, the header first, its top left corner at
    origin.  An old table's uuids, lock, border and separators are kept."""
    widths = column_widths(texts)
    heights = row_heights(texts)
    xs = [origin[0] + sum(widths[:n]) for n in range(len(widths) + 1)]
    ys = [origin[1] + sum(heights[:n]) for n in range(len(heights) + 1)]
    old_cells = children(child(old, "cells") or [], "table_cell") if old is not None else []
    ids = [str((child(c, "uuid") or ["", ""])[1]) for c in old_cells]
    cells = []
    for r, row in enumerate(texts):
        for c, text in enumerate(row):
            n = r * len(HEADER) + c
            cells.append(
                cell(
                    text,
                    (round(xs[c], 4), round(ys[r], 4)),
                    (round(xs[c + 1], 4), round(ys[r + 1], 4)),
                    r == 0,
                    c in RIGHT,
                    ids[n] if n < len(ids) and ids[n] else str(uuid.uuid4()),
                )
            )
    kept = {k: child(old, k) for k in ("uuid", "locked", "border", "separators")} if old else {}
    border = node("border", node("external", Atom("yes")), node("header", Atom("yes")), stroke())
    separators = node("separators", node("rows", Atom("yes")), node("cols", Atom("yes")), stroke())
    table: Expression = [
        Atom("table"),
        node("column_count", len(HEADER)),
        kept.get("uuid") or node("uuid", str(uuid.uuid4())),
    ]
    locked = kept.get("locked")
    if locked is not None:
        table.append(locked)
    table += [
        node("layer", LAYER),
        kept.get("border") or border,
        kept.get("separators") or separators,
        node("column_widths", *widths),
        node("row_heights", *heights),
        node("cells", *cells),
    ]
    return table


def origin(table: Expression) -> Point:
    """A table's top left corner: its first cell's start."""
    first = next(iter(children(child(table, "cells") or [], "table_cell")), None)
    start = child(first, "start") if first is not None else None
    if start is None:
        raise TableError("a stackup table with no cell start")
    return float(str(start[1])), float(str(start[2]))


def outline_box(board: Expression) -> Box | None:
    """The board outline's (Edge.Cuts) extent; None if it has none."""
    points = [p for s in edge(board) for p in s]
    if not points:
        return None
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return Box(min(xs), min(ys), max(xs), max(ys))


def table_box(table: Expression) -> Box:
    """A table's extent: its cells'."""
    corners = [
        (float(str(c[1])), float(str(c[2])))
        for cell_ in children(child(table, "cells") or [], "table_cell")
        for c in (child(cell_, "start"), child(cell_, "end"))
        if c is not None
    ]
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    return Box(min(xs), min(ys), max(xs), max(ys))


def problems(box: Box, page: Sheet, outline: Box | None) -> list[str]:
    """Why a table there is out of place; none if it is not."""
    out = []
    if not page.frame.contains(box):
        out.append(f"past the {page.paper} drawing sheet's frame")
    if box.overlaps(page.title_block):
        out.append("over the title block")
    if outline is not None and box.overlaps(outline):
        out.append("over the board outline")
    return out


def spots(width: float, height: float, page: Sheet, outline: Box | None) -> list[Point]:
    """Where a new table of that size may go, in order of preference: GAP
    right of the board outline, below, left and above it, each from its
    top or left edge; then GAP in from the frame's top left, top right
    and bottom left corners, and above the title block."""
    out = []
    if outline is not None:
        out += [
            (outline.right + GAP, outline.top),
            (outline.left, outline.bottom + GAP),
            (outline.left - GAP - width, outline.top),
            (outline.left, outline.top - GAP - height),
        ]
    f = page.frame
    out += [
        (f.left + GAP, f.top + GAP),
        (f.right - GAP - width, f.top + GAP),
        (f.left + GAP, f.bottom - GAP - height),
        (f.right - GAP - width, page.title_block.top - GAP - height),
    ]
    return out


def place(texts: list[list[str]], page: Sheet, outline: Box | None) -> Point:
    """The first spot where a new table of texts fits."""
    width, height = sum(column_widths(texts)), sum(row_heights(texts))
    for x, y in spots(width, height, page, outline):
        if not problems(Box(x, y, x + width, y + height), page, outline):
            return round(x, 4), round(y, 4)
    raise TableError(
        f"no room for the {width:.0f} x {height:.0f} mm stackup table inside the {page.paper}"
        " drawing sheet's frame, clear of its title block and the board outline:"
        " give --at, or a larger paper size"
    )


def refresh(text: str, board: Expression, at: Point | None = None) -> Result:
    """text, the board's, with its stackup tables made to match its stackup:
    one added if it has none, each that differs rewritten."""
    found = stackup(board)
    if found is None:
        raise TableError("no physical stackup (Board Setup) to make a stackup table of")
    texts = [list(HEADER), *stackup_rows(found, layer_names(board))]
    if at is not None:
        at = (round(at[0], 4), round(at[1], 4))
    page = sheet(board)
    outline = outline_box(board)
    result = Result(text)
    lists = [i for i in board if isinstance(i, list)]
    located = spans(text)
    edits: list[tuple[tuple[int, int], Expression]] = []
    present = tables(board)
    for table in present:
        if any(child(c, "angle") for c in children(child(table, "cells") or [], "table_cell")):
            raise TableError("a rotated stackup table: replace it in KiCad")
        corner = origin(table)
        layer = str((child(table, "layer") or ["", "?"])[1])
        moved = at is not None and at != corner
        if rows(table) == texts[1:] and layer == LAYER and not moved:
            new = table
        else:
            new = build(texts, at or corner, table)
            span = located[next(n for n, i in enumerate(lists) if i is table)]
            edits.append((span, new))
            result.changes.append(f"stackup table at {where(at or corner)}: out of date")
        if layer != LAYER:
            result.notes.append(f"stackup table on {layer}: it goes on {LAYER_NAME}")
        wrong = problems(table_box(new), page, outline)
        if wrong:
            result.notes.append(f"stackup table at {where(at or corner)}: {', '.join(wrong)}")
    if not present:
        corner = at or place(texts, page, outline)
        end = located[-1][1]
        new = build(texts, corner, None)
        edits.append(((end, end), new))
        result.changes.append(f"stackup table at {where(corner)}: missing")
        wrong = problems(table_box(new), page, outline)
        if wrong:
            result.notes.append(f"stackup table at {where(corner)}: {', '.join(wrong)}")
        if any(str(g[1]) == OLD_GROUP for g in children(board, "group")):
            result.notes.append(f"the old {OLD_GROUP} stays: delete it in KiCad")
    for (start, end), new in sorted(edits, key=lambda e: e[0], reverse=True):
        written = write(new, 1)
        piece = written.lstrip("\t") if start != end else "\n" + written
        result.text = result.text[:start] + piece + result.text[end:]
    return result


def fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser(prog="stackup-table", description=__doc__.split("\n\n")[0])
    ap.add_argument("board", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="write here, not to BOARD")
    ap.add_argument(
        "--at",
        type=parse_point,
        metavar="X,Y",
        help="the table's top left corner, mm (default: where it is; a new one in the frame)",
    )
    ap.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 if the table would change"
    )
    a = ap.parse_args()
    try:
        text = a.board.read_bytes().decode("utf-8")
        result = refresh(text, parse(text), a.at)
    except (OSError, UnicodeDecodeError, ParseError, TableError, SheetError) as e:
        fail(f"{a.board}: {e}")
    project = a.board.with_suffix(".kicad_pro")
    try:
        own = custom_sheet(project)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        own = ""
        print(f"{project}: not read, so its drawing sheet is not known: {e}", file=sys.stderr)
    if own:
        result.notes.append(
            f"its own drawing sheet {own} is not read: the frame is KiCad's default sheet's"
        )
    for note in result.notes:
        print(f"{a.board}: {note}", file=sys.stderr)
    if not result.changes:
        print(f"{a.board}: stackup table up to date")
    if a.check:
        for change in result.changes:
            print(f"{a.board}: {change}")
        sys.exit(1 if result.changes else 0)
    out = a.output or a.board
    if result.changes or out != a.board:
        try:
            out.write_bytes(result.text.encode("utf-8"))
        except OSError as e:
            fail(f"{out}: {e}")
    for change in result.changes:
        print(f"{out}: {change}: written")
