# stitch

`stitch BOARD [-o OUT] [options]` adds through vias on a board's ground
net, for the house style's edge stitching (`docs/kicad-style.md`, Board
finishing) and a light grid over the board, then refills the zones.
`kicad-style-check` checks the result (`docs/kicad-style-check.md`,
Finishing).

```
uv run stitch NAME/NAME.kicad_pcb
```

It reads and writes the board with KiCad's pcbnew, so it runs in a venv
made with `uv venv --system-site-packages --python /usr/bin/python3`
(`ee-tooling-adopt` makes a board repository's so), and needs
`kicad-cli`.  Close the board in KiCad first.  The board is written in
place unless `-o`; the project file (`.kicad_pro`) must be beside the
board written, for DRC's rules.

## What it places, in order

Each via goes at the clear spot nearest its mark, within `--reach`.  A
mark with a ground barrel (a through via or a plated through-hole pad)
already near it needs none.

1. `--at X,Y`, each, in KiCad's coordinates (mm, as the status bar
   shows them): spots the rest cannot reach.
2. The ring: along every closed Edge.Cuts outline (a board's, a panel's
   boards', a slot's or cutout's), `--pitch` apart, `--inset` in from
   it on the board.  None within half the pitch of a ground barrel.
3. Gaps: where a gap over 10 mm (`kicad_style_finishing.MAX_GAP`) is
   still left along an outline, marks half the pitch apart along it,
   with twice the reach.
4. The grid: `--grid` apart over the board, half a grid or more in from
   every outline; none within `--near` of a ground barrel.  `--grid 0`
   for none.

| Option | Default | What |
|---|---|---|
| `--net` | kicad-style-check's ground net | the net |
| `--via D/DRILL` | the board's current via size | mm |
| `--pitch` | 5 | ring marks apart, mm |
| `--inset` | 1.3 | ring marks in from the outline, mm |
| `--grid` | 10 | the grid's pitch, mm |
| `--near` | 4 | a grid mark with a ground barrel this close needs no via, mm |
| `--reach` | 1.5 | a via this far from its mark at most, mm |

## A clear spot

- The via's copper keeps every other net's pad, track and via at the
  larger of the two items' own clearances (pcbnew's netclass ones).
- Its hole keeps every hole at Board Setup's hole-to-hole minimum.
- It is on the board, its copper Board Setup's copper-to-edge clearance
  in from every outline.
- It is outside every rule area that forbids vias (the board's, or a
  footprint's), and every mounting hole's courtyard.

## After placing

`kicad-cli pcb drc --refill-zones --save-board` refills the zones and
runs DRC with the board's own rules, its `.kicad_dru` included.  Each
new via a violation names (a custom rule's clearance, a via no zone
reaches) is taken out, and the zones refilled, up to 3 times.

It prints what it added, each mark without room and each via taken out,
then any stitching gap still over 10 mm, and exits 1 if one is left: the
owner decides it, with `--at` or by hand.
