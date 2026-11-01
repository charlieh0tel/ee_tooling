"""A KiCad 10 board's stackup table against the house style
(docs/kicad-style.md, Board layout, Board finishing).

The board has a physical stackup (Board Setup), and KiCad 10's stackup
table (Place > Add Stackup Table) on User.Drawings, matching it.  KiCad
writes the table once, as a (table ...) of seven columns headed HEADER,
one row per layer or dielectric sublayer, top first; it does not follow
later changes to the stackup.  A row matches its layer when its material,
thickness, Er and loss tangent match each the stackup gives (KiCad prints
its own defaults for the rest).  The header is found in English only.
"""

import math
import re

from ee_tooling.fabrication import StackupLayer, stackup, text_of
from ee_tooling.kicad_style_finding import STACKUP_RULES, Checked, Finding, found
from ee_tooling.sexpr import Expression, child, children

# KiCad 10's stackup table header (pcbnew/board_tables/board_stackup_table.cpp).
HEADER = ("Layer Name", "Type", "Material", "Thickness", "Color", "Epsilon R", "Loss Tangent")
# The columns compared with the stackup, by position in HEADER.
MATERIAL, THICKNESS, EPSILON_R, LOSS_TANGENT = 2, 3, 5, 6
# The layer the table belongs on: KiCad's canonical name, and its user name.
LAYER = "Dwgs.User"
LAYER_NAME = "User.Drawings"
# The group a stackup table made before KiCad 9 is, of separate texts.
OLD_GROUP = "group-boardStackUp"
# A thickness as KiCad prints it in the user's units, and each unit in mm.
LENGTH = re.compile(r"^\s*([\d.]+)\s*(mm|mils?|in|um|µm)\s*$")
UNIT_MM = {"mm": 1.0, "mil": 0.0254, "mils": 0.0254, "in": 25.4, "um": 0.001, "µm": 0.001}
# Values this close match: KiCad prints a few digits.
THICKNESS_TOLERANCE = 0.0005  # mm
RATIO_TOLERANCE = 0.005  # Er and loss tangent, absolute


def cells(table: Expression) -> list[str]:
    return [text_of(c[1]) for c in children(child(table, "cells") or [], "table_cell")]


def tables(board: Expression) -> list[Expression]:
    """The board's stackup tables: its tables headed HEADER."""
    headed = []
    for table in children(board, "table"):
        count = child(table, "column_count")
        if count is not None and str(count[1]) == str(len(HEADER)):
            if tuple(cells(table)[: len(HEADER)]) == HEADER:
                headed.append(table)
    return headed


def length(text: str) -> float | None:
    """A printed thickness in mm; None if not a length ("Not specified")."""
    m = LENGTH.match(text)
    return float(m[1]) * UNIT_MM[m[2]] if m else None


def ratio(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def close(found: float | None, wanted: float, tolerance: float) -> bool:
    return found is not None and math.isclose(found, wanted, abs_tol=tolerance)


def differences(row: list[str], layer: StackupLayer) -> list[str]:
    """What a table row says that its stackup layer does not."""
    out = []
    if layer.material and row[MATERIAL] != layer.material:
        out.append(f"material {row[MATERIAL]!r}, not {layer.material!r}")
    checks = (
        ("thickness", THICKNESS, length, layer.thickness, THICKNESS_TOLERANCE),
        ("Er", EPSILON_R, ratio, layer.epsilon_r, RATIO_TOLERANCE),
        ("loss tangent", LOSS_TANGENT, ratio, layer.loss_tangent, RATIO_TOLERANCE),
    )
    for what, column, parse, wanted, tolerance in checks:
        if wanted is not None and not close(parse(row[column]), wanted, tolerance):
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
        texts = cells(table)
        rows = [texts[i : i + len(HEADER)] for i in range(len(HEADER), len(texts), len(HEADER))]
        if len(rows) != len(found_stackup.layers):
            problems.append(
                found(
                    "stackup-table-rows",
                    f"stackup table: {len(rows)} rows, the stackup {len(found_stackup.layers)}"
                    " layers; out of date: replace it",
                )
            )
            continue
        for row, item in zip(rows, found_stackup.layers, strict=True):
            problems += [
                found(
                    "stackup-table-row",
                    f"stackup table: {item.name}: {d}; out of date: replace it",
                    layer=item.name,
                )
                for d in differences(row, item)
            ]
    return Checked(problems, frozenset(STACKUP_RULES))
