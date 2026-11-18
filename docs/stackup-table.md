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
  finds it): one is added, its top left corner at `--at`, else 5 mm
  right of the board outline's (Edge.Cuts) top right corner.  A board
  with no outline needs `--at`.  A pre-KiCad 10 table (the group
  `group-boardStackUp`) is left in place, and said so on stderr.
- A stackup table whose cells differ from what the stackup gives, or on
  a layer other than User.Drawings: rewritten where it is, or at
  `--at`, on User.Drawings (a move is said on stderr).  Its uuids, lock,
  border and separators are kept.
- A stackup table that matches: left as it is, unless `--at` moves it.

Only the table's text in the file changes; the rest of the file is kept
byte for byte.  A second run changes nothing.

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
| 2 | the board unreadable, no physical stackup, no outline and no `--at`, or a rotated stackup table (replace it in KiCad) |
