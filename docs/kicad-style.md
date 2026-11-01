# KiCad house style

Rules for schematics and boards, for anyone (or any agent) editing them.

## Files

- A board's `.kicad_*` files are its owner's: edit them only when handed
  over, with KiCad closed.
- Board work goes in the repository's board file, in git, checkpoint by
  checkpoint: stop at each checkpoint agreed with the owner for review,
  and commit a step only once it has been reviewed.
- The schematic's notes, text blocks and title block are documentation:
  keep them current in the same commit as the design change.

## Schematic drawing

- Every coordinate chosen on the 2.54 mm grid, so it reads as drawn by
  hand: signal left to right, supplies up, ground down.
- A wire of at least one grid unit off every pin before it joins
  anything.  No pin on a rail, no power or ground symbol on a pin, no
  pin touching a pin.
- A junction dot on every join of three or more.
- Wires, not labels.  A label only where a wire can't reasonably go,
  and then it lies on the net, on a wire's run with its text along the
  wire; never hung off a wire's end to the left or right.
- A differential pair is one in the schematic: its two nets named so
  KiCad pairs them, `NAME_P`/`NAME_N` or `NAME+`/`NAME-` (USB's `D+`/`D-`).
- A sense resistor gets a Kelvin connection: its sense nets leave its
  pads separately from the current path, as a differential pair.
- Reference and value text level and clear of wires.  Every part in
  the BOM, DNP included, carries Manufacturer, MPN and LCSC fields;
  every capacitor shows its dielectric or type and its working voltage
  ("50V X7R", "25V tantalum"), in its value or a field shown on the
  sheet.

`kicad-style-check` checks these rules (`docs/kicad-style-check.md`); a
break judged intentional is waived, with its reason, in the board's
`kicad-style.toml`.

## Board layout

### Placement

- Decoupling capacitors tight against their part's package, placed and
  turned for the shortest connections to its VDD (or VCC) and GND pins,
  the supply pad in line with the supply pin (same x or same y).  With
  several on one pin, the smallest value goes closest to the package.
- A pad lines up with the pin it connects to: a filter capacitor on a
  module's pin sits inboard, its signal pad level with that pin.
- Related parts in rows or columns, at one rotation.  A pull-up or
  pull-down in line with the part it serves; matching groups laid out
  identically.
- Parts spread out where the board has room; only decoupling
  capacitors go tight.
- Placement is tidied by these rules before it is shown.  The ratsnest
  crossing count (`placement-score`, `docs/placement.md`) is a
  secondary guide, not the goal.
- Mounting holes in the board's corners; corners rounded.  Keep the
  board clear of the drawing sheet's title block.
- The area under a module is not a keepout unless something physical
  says so.
- Edge.Cuts, connectors and connectorized modules (an RP2040 board,
  say) are locked once their places are decided.

### Net classes

- Power nets get a net class, even at the default width.
- Every differential pair (USB, say) and every controlled-impedance net
  gets one, named for its impedance and kind: `90Z_DIFF`, `50Z_SE`.  A
  Kelvin sense pair, not impedance-controlled, needs none: KiCad pairs
  it by its names.
- Otherwise a net class wherever a group of nets shares a width,
  clearance or impedance.

### Routing

- Before routing, agree the stackup and the overall strategy with the
  board's owner, including whether to pour ground on the top layer too
  (often worthwhile).
- Planes first, always: every plane net's zone (GND and any other) is
  drawn and filled at the start of routing, so every review shows the
  planes.  The vias tying pads to the planes usually come next, but it
  depends.
- Routed by hand, net by net.  No autorouter artifacts: meanders, mixed
  widths, needless jogs, stubs.  Differential pairs, Kelvin sense pairs
  included, run together.
- No right-angle bends: every 90-degree bend is cut with a 45-degree
  segment.
- Where there is room, a track leaves a pad or via straight, about 1 mm,
  before its first bend, and arrives the same way.  A preference, not a
  rule: sometimes it cannot be done.
- Neighboring SMD pads on one net are joined through their middles,
  center to center, not along their ends.
- An output whose straight exit would cross its neighbors' may escape
  under the package, between the pad rows, leaving the other pins'
  exits free.
- With a ground plane, every GND pad gets a short stub to a via into
  the plane (more than one for an exposed or thermal pad, and as a
  guideline for any big one), N/S/E/W by preference, the
  via inside the part's courtyard where it fits (outside is fine where
  it does not).  Other plane nets and power stubs the same.  Never a
  trace from pad to pad.  Mounting hardware tied to ground (a mounting
  hole's pad, a standoff) needs no via.
- A supply runs from its trace or plane to the decoupling capacitor,
  then from the capacitor to the package's pin.  A pin with more than
  one capacitor is fed through at least one of them.  A regulator's input and
  output pins each go to their capacitor first, before anything else,
  the capacitors as near the pins as possible.

### Zones

- Pours are copper zones, never filled shapes on a copper layer.  A
  zone's outline usually runs past the board's edge on every side, so
  the board's edge clearance, not the outline, bounds the fill; not a
  hard rule (a panel's breakaway tabs, a split plane).
- Zones are locked.

`kicad-style-check` checks the zone, routing, net class and finishing
rules it can (`docs/kicad-style-check.md`).
KiCad's DRC checks mounting holes, right-angle bends and differential
pairs' coupling with ee_tooling's rules (`docs/kicad-rules.md`).

### Board finishing

- Stitching vias around the board's edge (`stitch`, `docs/stitch.md`).
- Mounting holes plated, with an annular ring.  A hole tied to ground
  needs no vias of its own.
- A physical stackup in Board Setup, and its table (Place > Add Stackup
  Table) on User.Drawings.  KiCad does not update the table: replace it
  after any stackup change.

## Silkscreen

- On a board that is not dense, every reference designator horizontal,
  on the same side of its part within a group, and clear of pads and
  other silk.
- No silk across a via.  Silk beside a via, or touching its ring's
  outer edge, is fine.
- Clear silk by moving vias or reference labels, never by editing a
  footprint's own silk (outlines, ticks, pin-1 marks).
- Legends (an LED's "5V", "STAT") stay where they are.
- If neither a via nor a label can move, list the conflict for the
  board's owner.
- Check each fix on a render.
