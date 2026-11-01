# kicad-style-check

`kicad-style-check FILE...` checks KiCad 10 files against
`docs/kicad-style.md`, choosing the checks by the file's suffix.  It
prints one problem a line, `FILE: problem`, and exits 1 on any problem,
2 if a file cannot be read; an unreadable file is never clean.  A line
starting `note:` is for the board's owner to judge and does not fail the
check.  What a check cannot decide (an unfilled zone, a board with no
tracks) is said on stderr.

```
uv run kicad-style-check NAME/NAME.kicad_sch NAME/NAME.kicad_pcb NAME/NAME.kicad_pro
```

The thresholds below are named constants in each module.

KiCad's own DRC and ERC check some of these with ee_tooling's rule
files; `docs/kicad-rules.md` lists the overlap.

## Schematic (.kicad_sch): kicad_style_check.py

- Connection points (wire ends, junctions, no-connects, labels, pins) on
  the grid: `--grid`, 2.54 mm.
- No pin touching another part's pin, no power symbol on a pin, no pin
  on a wire's run, no pin without a wire or no-connect.
- A pin's wire at least one grid unit long, and its only wire.
- A junction on every join of three or more, and nowhere else.
- No-connects on pins (hidden pins count); no zero-length or
  overlapping wires.
- Manufacturer, MPN and LCSC on every part in the BOM and not DNP.
- Every capacitor's value (`C` and a number) showing a dielectric (C0G,
  NP0, X5R, X7R ...) or a type (Al, electrolytic, Ta, tantalum, polymer,
  film, mica), and a voltage.

Pins are placed as KiCad does: rotated counterclockwise, then mirrored,
in the library's y-up axes, then y flipped.  Labels, text placement and
wires versus labels are not checked.

## Board (.kicad_pcb)

### Zones: kicad_style_board.py

- No filled graphic shape on a copper layer: pours are zones.
- Every zone locked, keepouts included.
- A copper zone whose outline lies on the board anywhere, rather than
  past its edge or in a slot or cutout, is a `note:` (along the edge
  counts as on it).

### Finishing: kicad_style_finishing.py

- Edge stitching: along every closed Edge.Cuts outline (a board's, a
  panel's, a slot's or cutout's), barrels of the ground net within 2 mm
  of it, no more than 10 mm apart along it.  A barrel is a through via
  or a plated through-hole pad.  The ground net is `GND` (or `.../GND`)
  if it has a copper zone, else the one whose copper zones cover the
  most area; a board with no copper zone is not checked.  An outline under 10 mm round needs none.
- Mounting holes (footprints named `MountingHole*`): plated, with an
  annular ring of at least 0.5 mm, and on a net (a ground tie, needing no
  vias).  A hole on no net is reported for the owner to decide.

### Routing: kicad_style_routing.py

Plane nets are the `poured` nets of `placement.toml` beside the board
(`docs/placement.md`), or else a net whose zone on a layer covers half
the board or more.

- Every SMD pad on a plane net whose plane is on another layer: a stub
  of straight tracks to a via reaching the plane, the via's center
  within 1 mm of the pad's copper.  A stub not within 1 degree of N, S,
  E or W is a `note:`.  An exposed or thermal pad (named `EP`, `PAD`
  or `TAB`, or the largest pad of a part with 3 or more, twice the next
  one's area) needs 2 vias; any other pad of 2.5 mm² or more gets a
  `note:` advising 2.
- Mounting hardware (references `H` and a number: holes, standoffs) is
  exempt.  A via outside the part's courtyard is fine.
- A pad traced to a pad of its own part that reaches the plane (a
  connector's plated-through shell, a QFN's exposed pad with vias) needs
  no via of its own.
- No via serving two pads; no trace on a plane net from pad to pad but
  within one part.
- A supply pin is reached through its decoupling capacitor: with the
  capacitor's pad taken away, the pin reaches no zone and no other
  part's pad.  The decoupling map is `placement.toml`'s, or inferred: a
  capacitor across a supply and a ground (`GND`, `AGND`, `VSS`, `0V`
  ...) decouples the nearest `U` or `IC` supply pin within 5 mm on that
  supply and ground (a power pin, where the board gives pin types).
- No right-angle bend: two tracks' ends meeting on one layer and net,
  where no other track ends or runs and off any pad or via, at 90
  degrees between them (within 1 degree) or sharper.  A track ending on
  another's run (a T) is a branch, not a bend.
- A board with no tracks or vias is not checked.

## Project (.kicad_pro): kicad_style_netclass.py

Net classes, from the project's `net_settings`, with the board and root
schematic of its name beside it; class membership follows KiCad's own
patterns and assignments.

- Every power net in a class other than Default.  A power net is one
  named for a supply (`+3V3`, `VDD...`, `VCC...`, `GND`, `VBUS`, `VIN`
  ...), named by a power symbol, or on a power pin.
- Every differential pair (KiCad's `+`/`-` or `P`/`N`, and USB's
  `DP`/`DM`) in one `<ohms>Z_DIFF` class, but a Kelvin sense pair (its
  name says `SENSE`, `SNS` or `KELVIN`).
- A class named for an impedance named `<ohms>Z_DIFF` or `<ohms>Z_SE`.
- A net named like half a pair (ending `+`, `-`, `_P`, `DP` or `DM`) with
  no other half is a `note:`; a lone `_N`, often active low, is not.
- A class assigned or in a pattern but not defined.
