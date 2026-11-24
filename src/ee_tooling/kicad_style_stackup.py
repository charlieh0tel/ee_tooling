"""A KiCad 10 board's stackup table against the house style
(docs/kicad-style.md, Board layout, Board finishing).

The board has a physical stackup (Board Setup), and KiCad 10's stackup
table (Place > Add Stackup Table) on User.Drawings, matching it.  KiCad
writes the table once, as a (table ...) of seven columns headed HEADER,
one row per layer or dielectric sublayer, top first; it does not follow
later changes to the stackup.  A row matches its layer when its type,
and its material, thickness, color, Er and loss tangent where the stackup
gives them (KiCad prints its own defaults for the rest), match the
stackup's as printed.  The header is found in English only.
"""

import math
import re

from ee_tooling.fabrication import StackupLayer, stackup, text_of
from ee_tooling.kicad_style_finding import STACKUP_RULES, Checked, Finding, found
from ee_tooling.sexpr import Expression, child, children

# KiCad 10's stackup table header (pcbnew/board_tables/board_stackup_table.cpp).
HEADER = ("Layer Name", "Type", "Material", "Thickness", "Color", "Epsilon R", "Loss Tangent")
# The columns compared with the stackup (all but the layer's name), by
# position in HEADER.
TYPE, MATERIAL, THICKNESS, COLOR, EPSILON_R, LOSS_TANGENT = 1, 2, 3, 4, 5, 6
# The layer the table belongs on: KiCad's canonical name, and its user name.
LAYER = "Dwgs.User"
LAYER_NAME = "User.Drawings"
# The group a stackup table made before KiCad 9 is, of separate texts.
OLD_GROUP = "group-boardStackUp"
# A thickness as KiCad prints it in the user's units, and each unit in mm.
LENGTH = re.compile(r"^\s*([\d.]+)\s*(mm|mils?|in|um|µm)\s*$")
UNIT_MM = {"mm": 1.0, "mil": 0.0254, "mils": 0.0254, "in": 25.4, "um": 0.001, "µm": 0.001}
# A thickness in a unit other than mm this close matches, mm: KiCad
# rounds it.  A ratio (Er, loss tangent) and a thickness in mm match as
# printed, to %g's six significant digits, as stackup-table writes them.
THICKNESS_TOLERANCE = 0.0005
PRINTED_UNIT = "mm"


def cells(table: Expression) -> list[str]:
    return [text_of(c[1]) for c in children(child(table, "cells") or [], "table_cell")]


def rows(table: Expression) -> list[list[str]]:
    """A stackup table's rows after its header, each len(HEADER) cells."""
    texts = cells(table)
    return [texts[i : i + len(HEADER)] for i in range(len(HEADER), len(texts), len(HEADER))]


def tables(board: Expression) -> list[Expression]:
    """The board's stackup tables: its tables headed HEADER."""
    headed = []
    for table in children(board, "table"):
        count = child(table, "column_count")
        if count is not None and str(count[1]) == str(len(HEADER)):
            if tuple(cells(table)[: len(HEADER)]) == HEADER:
                headed.append(table)
    return headed


def length_matches(text: str, wanted: float) -> bool:
    """Whether a printed thickness matches wanted, mm; never if not a
    length ("Not specified")."""
    m = LENGTH.match(text)
    if not m:
        return False
    if m[2] == PRINTED_UNIT:
        return f"{float(m[1]):g}" == f"{wanted:g}"
    return math.isclose(float(m[1]) * UNIT_MM[m[2]], wanted, abs_tol=THICKNESS_TOLERANCE)


def ratio_matches(text: str, wanted: float) -> bool:
    try:
        return f"{float(text):g}" == f"{wanted:g}"
    except ValueError:
        return False


def differences(row: list[str], layer: StackupLayer) -> list[str]:
    """What a table row says that its stackup layer does not."""
    out = []
    # KiCad gives a type initial capitals, stackup-table capitalizes it.
    if layer.type and row[TYPE].casefold() != layer.type.casefold():
        out.append(f"type {row[TYPE]!r}, not {layer.type!r}")
    if layer.material and row[MATERIAL] != layer.material:
        out.append(f"material {row[MATERIAL]!r}, not {layer.material!r}")
    if layer.thickness is not None and not length_matches(row[THICKNESS], layer.thickness):
        out.append(f"thickness {row[THICKNESS]!r}, not {layer.thickness:g}")
    if layer.color and row[COLOR] != layer.color:
        out.append(f"color {row[COLOR]!r}, not {layer.color!r}")
    ratios = (
        ("Er", EPSILON_R, layer.epsilon_r),
        ("loss tangent", LOSS_TANGENT, layer.loss_tangent),
    )
    for what, column, wanted in ratios:
        if wanted is not None and not ratio_matches(row[column], wanted):
            out.append(f"{what} {row[column]!r}, not {wanted:g}")
    return out


def check_stackup(board: Expression) -> Checked:
    """The board's stackup table problems.  The table rules run only on a
    board with a stackup, the row rules only where there is a table."""
    found_stackup = stackup(board)
    if found_stackup is None:
        message = "no physical stackup (Board Setup): no stackup table to check"
        return Checked([found("stackup-missing", message)], frozenset({"stackup-missing"}))
    candidates = tables(board)
    if not candidates:
        old = any(str(g[1]) == OLD_GROUP for g in children(board, "group"))
        why = f" (its {OLD_GROUP} predates KiCad 10's table: replace it)" if old else ""
        message = f"no stackup table (Place > Add Stackup Table) on {LAYER_NAME}{why}"
        return Checked(
            [found("stackup-table-missing", message)],
            frozenset({"stackup-missing", "stackup-table-missing"}),
        )
    problems: list[Finding] = []
    for table in candidates:
        layer = child(table, "layer")
        name = str(layer[1]) if layer is not None else "?"
        if name != LAYER:
            problems.append(
                found(
                    "stackup-table-layer", f"stackup table on {name}, not {LAYER_NAME}", layer=name
                )
            )
        found_rows = rows(table)
        if len(found_rows) != len(found_stackup.layers):
            problems.append(
                found(
                    "stackup-table-rows",
                    f"stackup table: {len(found_rows)} rows, the stackup"
                    f" {len(found_stackup.layers)} layers; out of date: replace it",
                )
            )
            continue
        for row, item in zip(found_rows, found_stackup.layers, strict=True):
            problems += [
                found(
                    "stackup-table-row",
                    f"stackup table: {item.name}: {d}; out of date: replace it",
                    layer=item.name,
                )
                for d in differences(row, item)
            ]
    return Checked(problems, frozenset(STACKUP_RULES))
