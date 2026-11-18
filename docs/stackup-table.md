# stackup-table

`stackup-table BOARD [-o OUT] [--at X,Y] [--check]` writes a board's
stackup table on User.Drawings from its physical stackup (Board Setup),
as KiCad 10's Place > Add Stackup Table makes it, for the house style
(`docs/kicad-style.md`, Board finishing).  `kicad-style-check` checks
the result (`docs/kicad-style-check.md`, Stackup table).

```
uv run stackup-table NAME/NAME.kicad_pcb
```

It reads and writes the board file itself, without pcbnew.  Close the
board in KiCad first: KiCad does not reload a board changed under it,
and saving from KiCad overwrites the change.  The board is written in
place unless `-o`.

## What it writes

- No stackup table (a table headed `Layer Name`, `Type`, `Material`,
  `Thickness`, `Color`, `Epsilon R`, `Loss Tangent`, as the checker
  finds it): one is added, its top left corner at `--at`, else at the
  first spot where it fits (Placement).  A pre-KiCad 10 table (the
  group `group-boardStackUp`) is left in place, and said so on stderr.
- A stackup table whose cells differ from what the stackup gives, or on
  a layer other than User.Drawings: rewritten where it is, or at
  `--at`, on User.Drawings (a move is said on stderr).  Its uuids, lock,
  border and separators are kept.
- A stackup table that matches: left as it is, unless `--at` moves it.
- More than one stackup table and `--at`: refused, as `--at` would put
  each at the one point.

Only the table's text in the file changes, written in the file's line
ending (its first line's: `\r\n` in a checkout that converts them);
the rest of the file is kept byte for byte.  A second run changes
nothing.  The file is written beside the board and renamed over it, its
mode kept, so a write that fails (a full disk) leaves the board as it
was.

## The drawing frame

The board's paper is its `(paper ...)`: a named size (A5 to A0, A to E,
USLetter, USLegal, USLedger, GERBER), or `"User" WIDTH HEIGHT` in mm;
`portrait` puts the shorter side across.  A board with no `(paper ...)`
is on A4, landscape.  The page's top left corner is the board's origin.

On it, KiCad's default drawing sheet has its frame (inside the border's
2 mm ruler) 12 mm in from each edge of the paper, and its title block,
108 x 32 mm, in the frame's bottom right corner.  These are as
`kicad-cli` 10 plots the default sheet for each paper size
(`src/ee_tooling/drawing_sheet.py`).

A project file (`.kicad_pro`) beside the board that names its own
drawing sheet (`.kicad_wks`) is said on stderr, and the frame is still
taken as the default sheet's: the custom sheet is not read.

## Placement

A table fits where it lies wholly inside the frame, clear of the title
block and of the board outline's (Edge.Cuts) extent.  A new table, with
no `--at`, goes at the first of these spots where it fits:

1. 5 mm right of the outline, level with its top.
2. 5 mm below the outline, level with its left edge.
3. 5 mm left of the outline, level with its top.
4. 5 mm above the outline, level with its left edge.
5. 5 mm in from the frame's top left corner.
6. 5 mm in from the frame's top right corner.
7. 5 mm in from the frame's bottom left corner.
8. 5 mm above the title block, 5 mm in from the frame's right edge.

A board with no outline tries 5 to 8 only.  Where none fits, nothing is
written and it exits 2: give `--at`, or a larger paper size.

A table at `--at`, or one kept where it is, is not moved to fit: where
it leaves the frame, or lies over the title block or the outline, that
is said on stderr, the table is still written, and the exit status is
the same (with `--check` too).

## The table

The cells are the text KiCad 10's own table gives (its
`board_stackup_table.cpp`): a row per layer or dielectric sublayer, top
first; the layer's user name (Board Setup, Board Layers), or
`Dielectric N`, `Dielectric N (k/m)` for sublayers; the type with only
its first letter capital; thicknesses in mm; KiCad's `Not specified`
and defaults where the stackup leaves a value unset (Er 3.3 for solder
mask, 4.5 and loss tangent 0.02 for a dielectric, 1 and 0 otherwise).
The header is in English, as the checker finds it.

Text is 1.5 mm, as KiCad's; column widths are estimated from KiCad's
stroke font, so they can be a little wider than the GUI's.  A
thickness shown in other units in the GUI's table is rewritten in mm.

## Exit status

| Status | When |
|---|---|
| 0 | written, or up to date |
| 1 | `--check`, and the table is missing or would change (nothing is written) |
| 2 | the board unreadable, its paper not one KiCad 10 has, no physical stackup, no room for a new table in the frame (and no `--at`), a rotated stackup table (replace it in KiCad), or `--at` with more than one stackup table |
