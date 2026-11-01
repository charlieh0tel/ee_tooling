# KiCad house style

Rules for schematics and boards, for anyone (or any agent) editing them.

## Files

- A board's `.kicad_*` files are its owner's: edit them only when handed
  over, with KiCad closed.
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
- Reference and value text level and clear of wires.  Every bought part
  carries Manufacturer, MPN and LCSC fields; every capacitor shows its
  dielectric type and working voltage ("50V X7R").

## Board layout

- Routed by hand, net by net.  No autorouter artifacts: meanders, mixed
  widths, needless jogs, stubs.  Differential pairs run together.
- Plane nets get a short N/S/E/W stub to a via (more than one for a big
  pad), never a trace from pad to pad.
- Decoupling capacitors placed and turned for the shortest connections
  to their part's VDD (or VCC) and GND pins.  With several on one pin,
  the smallest value goes closest to the package.
- Mounting holes in the board's corners; corners rounded.  Keep the
  board clear of the drawing sheet's title block.
- The area under a module is not a keepout unless something physical
  says so.

## Silkscreen

- No silk across a via.  Silk beside a via, or touching its ring's
  outer edge, is fine.
- Clear silk by moving vias or reference labels, never by editing a
  footprint's own silk (outlines, ticks, pin-1 marks).
- Legends (an LED's "5V", "STAT") stay where they are.
- If neither a via nor a label can move, list the conflict for the
  board's owner.
- Check each fix on a render.
