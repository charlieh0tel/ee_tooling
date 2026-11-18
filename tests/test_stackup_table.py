import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_kicad_style_stackup import SETUP, table

from ee_tooling.drawing_sheet import Sheet, SheetError, custom_sheet, sheet
from ee_tooling.fabrication import stackup
from ee_tooling.kicad_style_stackup import HEADER, check_stackup, rows, tables
from ee_tooling.placement import Box
from ee_tooling.sexpr import Expression, child, children, parse
from ee_tooling.stackup_table import (
    GAP,
    MARGIN,
    TableError,
    column_widths,
    layer_names,
    refresh,
    spots,
    stackup_rows,
    text_width,
)

# A two-layer board as KiCad 10 writes one, with SETUP's stackup and a
# 40 x 30 mm outline at (10, 20), that kicad-cli 10 reads.
LAYERS = (
    '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (1 "F.Mask" user) (3 "B.Mask" user)'
    ' (5 "F.SilkS" user "F.Silkscreen") (7 "B.SilkS" user "B.Silkscreen")'
    ' (17 "Dwgs.User" user "User.Drawings") (19 "Cmts.User" user "User.Comments")'
    ' (25 "Edge.Cuts" user))'
)
OUTLINE = (10, 20, 50, 50)
# SETUP's rows as KiCad 10's own stackup table gives them.
UNSET = "Not specified"
KICAD_ROWS = [
    ["F.Silkscreen", "Top silk screen", UNSET, UNSET, UNSET, "1", "0"],
    ["F.Mask", "Top solder mask", UNSET, "0.01 mm", UNSET, "3.3", "0"],
    ["F.Cu", "Copper", "", "0.035 mm", "", "1", "0"],
    ["Dielectric 1", "Core", "FR4", "1.51 mm", UNSET, "4.5", "0.02"],
    ["B.Cu", "Copper", "", "0.035 mm", "", "1", "0"],
    ["B.Mask", "Bottom solder mask", UNSET, "0.01 mm", UNSET, "3.3", "0"],
    ["B.Silkscreen", "Bottom silk screen", UNSET, UNSET, UNSET, "1", "0"],
]  # fmt: skip


def board(
    *items: str,
    setup: str = SETUP,
    paper: str = "",
    outline: tuple[float, float, float, float] | None = OUTLINE,
) -> str:
    """A board with no (paper ...) unless paper; its outline left, top,
    right and bottom."""
    edge = ""
    if outline is not None:
        left, top, right, bottom = outline
        edge = (
            f"(gr_rect (start {left:g} {top:g}) (end {right:g} {bottom:g})"
            ' (stroke (width 0.05) (type solid)) (layer "Edge.Cuts"))'
        )
    head = [f"(paper {paper})"] if paper else []
    body = "\n\t".join([*head, LAYERS, setup, edge, *items])
    return f'(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n\t{body}\n)\n'


def inside(rows_: list[list[str]], at: tuple[float, float] = (100, 100)) -> str:
    """table(rows_), its cells at at: by default in the A4 frame, off the
    board."""
    x, y = at
    cell = f"(start {x:g} {y:g}) (end {x + 1:g} {y + 1:g})"
    return table(rows_).replace("(start 0 0) (end 1 1)", cell)


def run(text: str, at: tuple[float, float] | None = None) -> tuple[str, list[str], list[str]]:
    r = refresh(text, parse(text), at)
    return r.text, r.changes, r.notes


def corner(t: Expression) -> tuple[str, str]:
    start = child(children(child(t, "cells") or [], "table_cell")[0], "start")
    assert start is not None
    return str(start[1]), str(start[2])


def test_rows_as_kicad_gives_them() -> None:
    pcb = parse(board())
    found = stackup(pcb)
    assert found is not None
    assert stackup_rows(found, layer_names(pcb)) == KICAD_ROWS


def test_sublayers_and_values() -> None:
    """A dielectric's sublayers numbered; the stackup's own material,
    color, Er and loss tangent; a layer with no user name by its own."""
    setup = (
        '(setup (stackup (layer "F.Mask" (type "Top Solder Mask") (thickness 0.02)'
        ' (material "Liquid Ink") (color "Green") (epsilon_r 3.8))'
        ' (layer "F.Paste" (type "Top Solder Paste"))'
        ' (layer "dielectric 2" (type "prepreg") (thickness 0.1) (material "FR408")'
        " (epsilon_r 3.7) (loss_tangent 0.0095) addsublayer (thickness 0.2)"
        ' (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))))'
    )
    pcb = parse(f"(kicad_pcb {setup})")
    found = stackup(pcb)
    assert found is not None
    assert stackup_rows(found, layer_names(pcb)) == [
        ["F.Mask", "Top solder mask", "Liquid Ink", "0.02 mm", "Green", "3.8", "0"],
        ["F.Paste", "Top solder paste", "", "Not specified", "", "1", "0"],
        ["Dielectric 2 (1/2)", "Prepreg", "FR408", "0.1 mm", "Not specified", "3.7", "0.0095"],
        ["Dielectric 2 (2/2)", "Prepreg", "FR4", "0.2 mm", "Not specified", "4.4", "0.02"],
    ]


def test_column_widths() -> None:
    """Each column wide enough for its widest text, a header's stroke the
    thicker; a character the font table lacks taken as the widest."""
    widths = column_widths([list(HEADER), ["x"] * 7, ["m" * 20, *["x"] * 6]])
    assert widths[0] == round(text_width("m" * 20, 0.2) + 2 * MARGIN, 4)
    assert widths[1] == round(text_width("Type", 0.3) + 2 * MARGIN, 4)
    assert text_width(chr(0xB5), 0.2) == text_width("m", 0.2)


def test_add() -> None:
    """A board without a table gets one beside its outline; the rest of
    the file is kept byte for byte, and the checker finds nothing."""
    before = board()
    after, changes, notes = run(before)
    assert changes == [f"stackup table at ({50 + GAP:g}, 20): missing"] and notes == []
    assert after.startswith(before[: before.rindex(")")].rstrip())
    assert after.endswith("\n\t)\n)\n")
    pcb = parse(after)
    assert check_stackup(pcb).findings == []
    (t,) = tables(pcb)
    assert rows(t) == KICAD_ROWS and corner(t) == (f"{50 + GAP:g}", "20")
    assert run(after) == (after, [], [])


def test_add_at() -> None:
    """--at is followed, past the frame and over the board too, and said."""
    after, changes, notes = run(board(), (1.234567, -2))
    assert changes == ["stackup table at (1.23, -2): missing"]
    assert notes == [
        "stackup table at (1.23, -2): past the A4 drawing sheet's frame, over the board outline"
    ]
    (t,) = tables(parse(after))
    assert corner(t) == ("1.2346", "-2")
    assert run(after, (1.234567, -2))[1] == []


def test_no_outline() -> None:
    """No outline: the frame's top left corner."""
    after, changes, notes = run(f"(kicad_pcb {SETUP})")
    assert changes == [f"stackup table at ({12 + GAP:g}, {12 + GAP:g}): missing"] and notes == []
    assert check_stackup(parse(after)).findings == []


def test_falls_back_below() -> None:
    """No room right of a wide board: below it."""
    after, changes, notes = run(board(outline=(20, 20, 200, 50)))
    assert changes == ["stackup table at (20, 55): missing"] and notes == []
    (t,) = tables(parse(after))
    assert corner(t) == ("20", "55")


def test_portrait() -> None:
    """Portrait A4 is 210 mm across: no room right of the board there."""
    outline = (20, 20, 80, 50)
    assert run(board(paper='"A4"', outline=outline))[1] == ["stackup table at (85, 20): missing"]
    assert run(board(paper='"A4" portrait', outline=outline))[1] == [
        "stackup table at (20, 55): missing"
    ]


def test_nothing_fits() -> None:
    """A board filling a User sheet leaves no room: an error naming --at."""
    with pytest.raises(TableError, match=r"no room for the \d+ x 34 mm .*User.*give --at"):
        run(board(paper='"User" 400 200', outline=(100, 40, 300, 150)))


def test_spots_order() -> None:
    """Right, below, left and above the outline; then the frame's top left,
    top right and bottom left corners, and above the title block."""
    page = Sheet("A4", 297, 210)
    assert spots(10, 4, page, Box(100, 50, 150, 80)) == [
        (155, 50),
        (100, 85),
        (85, 50),
        (100, 41),
        (17, 17),
        (270, 17),
        (17, 189),
        (270, 157),
    ]


@pytest.mark.parametrize(
    ("paper", "width", "height"),
    [
        (None, 297, 210),
        ('"A3"', 420, 297),
        ('"A3" portrait', 297, 420),
        ('"User" 150 100', 150, 100),
        ('"User" 150 100 portrait', 100, 150),
        ('"USLetter"', 279.4, 215.9),
    ],
)
def test_sheet(paper: str | None, width: float, height: float) -> None:
    """The paper; no (paper ...) is A4.  The frame 12 mm in, the title
    block 108 x 32 mm in its bottom right corner (kicad-cli's plot)."""
    found = sheet(parse(f"(kicad_pcb (paper {paper}))" if paper else "(kicad_pcb)"))
    assert (found.width, found.height) == (width, height)
    assert found.frame == Box(12, 12, width - 12, height - 12)
    assert found.title_block == Box(width - 120, height - 44, width - 12, height - 12)


@pytest.mark.parametrize("paper", ['"A9"', '"User"'])
def test_sheet_unknown(paper: str) -> None:
    with pytest.raises(SheetError):
        sheet(parse(f"(kicad_pcb (paper {paper}))"))


def test_custom_sheet(tmp_path: Path) -> None:
    project = tmp_path / "b.kicad_pro"
    assert custom_sheet(project) == ""
    project.write_text('{"pcbnew": {"page_layout_descr_file": ""}}')
    assert custom_sheet(project) == ""
    project.write_text('{"pcbnew": {"page_layout_descr_file": "logo.kicad_wks"}}')
    assert custom_sheet(project) == "logo.kicad_wks"


def test_no_stackup() -> None:
    with pytest.raises(TableError, match="no physical stackup"):
        run(board(setup="(setup (pad_to_mask_clearance 0))"))


def test_refresh_in_place() -> None:
    """An out-of-date table rewritten where it is, its uuids, lock and
    border kept; the text around it untouched."""
    stale = table([["x"] * 7]).replace('(uuid "u")', '(uuid "u") (locked yes)')
    stale = stale.replace("(start 0 0)", "(start 70 80)", 1).replace("(start 0 0)", "(start 71 81)")
    stale = stale.replace("(end 1 1)", "(end 72 82)")
    before = board(stale, '(gr_text "after" (at 0 0) (layer "F.SilkS") (uuid "w"))')
    after, changes, notes = run(before)
    assert changes == ["stackup table at (70, 80): out of date"] and notes == []
    start = before.index("(table")
    assert after[:start] == before[:start]
    assert after.endswith(before[before.index(")\n\t(gr_text") + 1 :])
    pcb = parse(after)
    assert check_stackup(pcb).findings == []
    (t,) = tables(pcb)
    assert corner(t) == ("70", "80") and child(t, "uuid") == ["uuid", "u"]
    assert child(t, "locked") == ["locked", "yes"]
    assert child(t, "border") == ["border", ["external", "yes"], ["header", "yes"]]
    assert run(after) == (after, [], [])


def test_up_to_date() -> None:
    text = board(inside(KICAD_ROWS))
    assert run(text) == (text, [], [])


def test_kept_past_frame() -> None:
    """A table kept where it is, past the frame or over the title block:
    said, not moved, and not a change."""
    text = board(inside(KICAD_ROWS, (280, 190)))
    assert run(text) == (text, [], ["stackup table at (280, 190): over the title block"])
    _, changes, notes = run(board(inside([["x"] * 7], (290, 190))))
    assert changes == ["stackup table at (290, 190): out of date"]
    assert notes == ["stackup table at (290, 190): past the A4 drawing sheet's frame"]


def test_wrong_layer() -> None:
    after, changes, notes = run(board(inside(KICAD_ROWS).replace("Dwgs.User", "Cmts.User")))
    assert changes == ["stackup table at (100, 100): out of date"]
    assert notes == ["stackup table on Cmts.User: it goes on User.Drawings"]
    assert check_stackup(parse(after)).findings == []


def test_moved_by_at() -> None:
    after, changes, notes = run(board(inside(KICAD_ROWS)), (60, 60))
    assert changes == ["stackup table at (60, 60): out of date"] and notes == []
    (t,) = tables(parse(after))
    assert corner(t) == ("60", "60")


def test_rotated() -> None:
    rotated = table(KICAD_ROWS).replace("(end 1 1)", "(end 1 1) (angle 90)", 1)
    with pytest.raises(TableError, match="rotated"):
        run(board(rotated))


def test_old_group() -> None:
    _, _, notes = run(board('(group "group-boardStackUp" (uuid "g") (members "a"))'))
    assert notes == ["the old group-boardStackUp stays: delete it in KiCad"]


def stackup_table(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from ee_tooling.stackup_table import main; main()", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_main(tmp_path: Path) -> None:
    """--check exits 1 and writes nothing; a run writes; a second finds
    it up to date; -o writes elsewhere."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(board())
    r = stackup_table(str(pcb), "--check")
    assert r.returncode == 1 and r.stdout.endswith(": missing\n")
    assert pcb.read_text() == board()
    r = stackup_table(str(pcb))
    assert r.returncode == 0 and r.stdout == f"{pcb}: stackup table at (55, 20): missing: written\n"
    written = pcb.read_text()
    r = stackup_table(str(pcb), "--check")
    assert r.returncode == 0 and r.stdout == f"{pcb}: stackup table up to date\n"
    out = tmp_path / "out.kicad_pcb"
    assert stackup_table(str(pcb), "-o", str(out)).returncode == 0
    assert out.read_text() == written == pcb.read_text()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (board(setup="(setup)"), "no physical stackup"),
        (board(paper='"A9"'), "paper 'A9'"),
        ("(kicad_pcb", "left open"),
        (None, "No such file"),
    ],
)
def test_main_errors(tmp_path: Path, text: str | None, message: str) -> None:
    pcb = tmp_path / "b.kicad_pcb"
    if text is not None:
        pcb.write_text(text)
    r = stackup_table(str(pcb))
    assert r.returncode == 2 and message in r.stderr and r.stdout == ""


def test_main_own_sheet(tmp_path: Path) -> None:
    """A project naming its own drawing sheet: said on stderr."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(board())
    (tmp_path / "b.kicad_pro").write_text('{"pcbnew": {"page_layout_descr_file": "x.kicad_wks"}}')
    r = stackup_table(str(pcb), "--check")
    assert r.returncode == 1
    assert r.stderr == (
        f"{pcb}: its own drawing sheet x.kicad_wks is not read:"
        " the frame is KiCad's default sheet's\n"
    )


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="needs kicad-cli")
def test_kicad_reads_it(tmp_path: Path) -> None:
    """KiCad loads and saves the board with the table as written: the
    checker still finds nothing, and a run after finds it up to date."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(board())
    assert stackup_table(str(pcb)).returncode == 0
    r = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", "--force", str(pcb)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    saved = parse(pcb.read_text())
    assert child(saved, "generator_version") is not None
    assert check_stackup(saved).findings == []
    (t,) = tables(saved)
    assert rows(t) == KICAD_ROWS and corner(t) == (f"{50 + GAP:g}", "20")
    r = stackup_table(str(pcb), "--check")
    assert r.returncode == 0, r.stdout
