# kicad-style-check

`kicad-style-check FILE...` checks KiCad 10 files against
`docs/kicad-style.md`, choosing the checks by the file's suffix.  It
prints one problem a line, `FILE: problem [RULE]`, RULE being its rule's
id (below), and exits 1 on any problem, 2 if a file cannot be read; an
unreadable file is never clean.  A line starting `note:` is for the
board's owner to judge and does not fail the check.  What a check cannot
decide (an unfilled zone, a board with no tracks) is said on stderr.  A
finding the owner has waived (Waivers, below) is not printed.

```
uv run kicad-style-check NAME/NAME.kicad_sch NAME/NAME.kicad_pcb NAME/NAME.kicad_pro
```

The thresholds below are named constants in each module.

KiCad's own DRC and ERC check some of these with ee_tooling's rule
files; `docs/kicad-rules.md` lists the overlap.

## Schematic (.kicad_sch): kicad_style_check.py

- Connection points (wire ends, junctions, no-connects, labels, pins) on
  the grid: `--grid`, 1.27 mm (`GRID`).  Each library symbol's pins a
  multiple of 2.54 mm (`SYMBOL_GRID`) from its first pin: KiCad's own
  two-pin symbols put theirs at 3.81 mm from their origin.  A note, since
  a few of KiCad's own fail it.
- No pin touching another part's pin, no power symbol on a pin, no pin
  on a wire's run, no pin without a wire or no-connect.
- A pin's wire at least 2.54 mm long (`PIN_WIRE`), and its only wire.
- A junction on every join of three or more, and nowhere else.
- No-connects on pins (hidden pins count); no zero-length or
  overlapping wires.
- Manufacturer, MPN and LCSC on every part in the BOM.  On a DNP part,
  this and the capacitor rule below are notes, not problems.
- Every capacitor's value (`C` and a number), or a field shown on the
  sheet (not hidden), showing a dielectric (C0G, NP0, X5R, X7R ...) or a
  type (Al, electrolytic, Ta, tantalum, polymer, film, mica), and a
  voltage.

Pins are placed as KiCad does: rotated counterclockwise, then mirrored,
in the library's y-up axes, then y flipped.  Labels, text placement and
wires versus labels are not checked.

## Board (.kicad_pcb)

### Zones: kicad_style_board.py

- No filled graphic shape on a copper layer: pours are zones.
- Every zone locked, keepouts included.
- A copper zone whose outline lies on the board anywhere, rather than
  past its edge or in a slot or cutout, is a `note:` (along the edge
  counts as on it).  A point of the outline that faces another net's
  zone on each of the zone's copper layers, as a split plane's does, is
  not: inside that zone, or no further from its outline than 0.5 mm
  (KiCad's default zone clearance, `ZONE_CLEARANCE_MM`) or either
  zone's own clearance.  A keepout faces nothing.

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

### Stackup table: kicad_style_stackup.py

- A physical stackup (Board Setup), and KiCad 10's stackup table (Place
  > Add Stackup Table: a table headed `Layer Name`, `Type`, `Material`,
  `Thickness`, `Color`, `Epsilon R`, `Loss Tangent`) on User.Drawings.
  The header is found in English only.  A pre-KiCad 10 table (the group
  `group-boardStackUp`) is reported for replacing.
- The table matching the stackup: a row per layer or dielectric
  sublayer, and each material, thickness (any of KiCad's units), Er and
  loss tangent the stackup gives.

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
- A supply pin is reached through its decoupling capacitors: with
  their pads taken away, the pin reaches no zone and no other part's
  pad; each capacitor reaches one, unless the net has no zone and no
  other part's pad (the part's own output, such as a regulator's).  The decoupling map is `placement.toml`'s, or inferred: a
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
  `DP`/`DM`) in one `<ohms>Z_DIFF` or `<ohms>Z_DIFF_<INTERFACE>` class,
  but a Kelvin sense pair (its name says `SENSE`, `SNS` or `KELVIN`).
- A class named for an impedance named `<ohms>Z_DIFF`,
  `<ohms>Z_DIFF_<INTERFACE>` or `<ohms>Z_SE`; the interface one of
  `docs/kicad-rules.md`'s (Differential pairs), `INTERFACES`.
- A net named like half a pair (ending `+`, `-`, `_P`, `DP` or `DM`) with
  no other half is a `note:`; a lone `_N`, often active low, is not.
- A class assigned or in a pattern but not defined.

## Waivers

`kicad-style.toml` beside the checked file (the board's directory)
holds the findings its owner has judged intentional; `--waivers FILE`
uses FILE for every file instead.  Each `[[waiver]]` names a rule's id,
every one of its subject fields (Rule ids, below), and a non-empty
`reason`:

```toml
[[waiver]]
rule = "trace-pad-to-pad"
net = "GND"
pads = ["J7 pad 3", "J8 pad 3"]
reason = "GND return across the breakaway tab, beside +5V and 1PPS."
```

- A waiver waives a finding of its rule whose subject is equal on every
  field: a set field (`pads`, `pins`, `nets`, `layers`) as a set, so a
  third pad is not waived; `at`, `[x, y]` in mm, within 0.01 mm.  It
  ignores the finding's values: a waived stub's via moved further is
  still waived.
- A field given a list of its values (a list of lists for a set field, a
  list of `[x, y]` for `at`) waives each.
- A waived finding, note or problem, is not printed or counted; stderr
  says `FILE: N waived (WAIVERS)`.  `--waived` prints each, not counted:
  `FILE: waived: <finding> [RULE]: <reason>`.
- A waiver that matches no finding is stale, a problem:
  `WAIVERS: stale waiver: RULE FIELD=VALUE; ...`.  It is judged only for
  rules that ran on a file given: a schematic rule's waiver is not stale
  when only the board is checked, nor a routing rule's on a board with no
  tracks.
- An unknown rule, a missing or unexpected field, a value of the wrong
  form, a missing or empty reason, or a file that is not TOML: the files
  it covers cannot be checked (exit 2).
- No patterns, and no turning a rule off for a whole board.

## Rule ids

Each finding names its rule's id and its subject: the fields that say
what it is about.  A pad is written as printed, `R1 pad 2`; a pin
`U1 pin 3`; `layer` a layer's name as the file writes it (`F.Cu`, or a
dielectric's in the stackup); `pads`, `pins`, `nets` and `layers` are
sets; `at` is a position in mm, used only where nothing else names the
item.  Ids and subject fields are part of the semver API.

| Checker | Id | Subject | Finding |
|---|---|---|---|
| Schematic | `off-grid` | `at` | a connection point off the grid |
| Schematic | `symbol-pin-off-grid` | `pin` | a library symbol's pin, `Lib:Name pin 2`, off the symbol grid from its first pin (note) |
| Schematic | `power-on-pin` | `pins` | a power symbol on a pin |
| Schematic | `pins-touching` | `pins` | pins of two parts touching |
| Schematic | `pin-on-wire` | `pins` | a pin on a wire's run |
| Schematic | `pin-no-wire` | `pin` | a pin with no wire or no-connect |
| Schematic | `wires-join-at-pin` | `pins` | two or more wires at a pin |
| Schematic | `pin-wire-short` | `pins` | a pin's wire under one grid unit |
| Schematic | `join-no-junction` | `at` | a join of three or more with no junction |
| Schematic | `junction-not-at-join` | `at` | a junction not at a join |
| Schematic | `no-connect-off-pin` | `at` | a no-connect off every pin |
| Schematic | `zero-length-wire` | `at` | a zero-length wire |
| Schematic | `overlapping-wires` | `at` | overlapping wires, at the first one's start |
| Schematic | `part-fields` | `reference` | a BOM part missing Manufacturer, MPN or LCSC |
| Schematic | `capacitor-value` | `reference` | a capacitor not showing dielectric and voltage |
| Zones | `filled-shape` | `at` | a filled shape on copper, at its first point |
| Zones | `no-outline` | none | no Edge.Cuts outline |
| Zones | `zone-unlocked` | `net`, `layers` | a zone not locked |
| Zones | `zone-on-board` | `net`, `layers` | a zone's outline on the board (note) |
| Finishing | `stitching-unchecked` | none | no copper zone with a net |
| Finishing | `outline-open` | `at` | an open Edge.Cuts outline |
| Finishing | `stitching-gap` | `net`, `at` | a stitching gap over 10 mm, at its start |
| Finishing | `mounting-hole-no-drill` | `reference` | a mounting hole with no drilled pad |
| Finishing | `mounting-hole-unplated` | `reference` | a mounting hole not plated |
| Finishing | `mounting-hole-ring` | `reference` | a mounting hole's ring under 0.5 mm |
| Finishing | `mounting-hole-no-net` | `reference` | a mounting hole on no net |
| Stackup | `stackup-missing` | none | no physical stackup (Board Setup) |
| Stackup | `stackup-table-missing` | none | no stackup table on User.Drawings |
| Stackup | `stackup-table-layer` | `layer` | a stackup table on another layer, the one it is on |
| Stackup | `stackup-table-rows` | none | a stackup table with a row count not the stackup's |
| Stackup | `stackup-table-row` | `layer` | a stackup table's row not matching its stackup layer |
| Routing | `decoupling-no-pin` | `capacitor`, `pin` | placement.toml's pin not on the board |
| Routing | `decoupling-no-pad` | `capacitor`, `pin` | the capacitor has no pad on the pin's net |
| Routing | `via-off-plane` | `net`, `pad` | a stub's via not reaching the plane |
| Routing | `stub-direction` | `net`, `pad` | a stub not N/S/E/W (note) |
| Routing | `stub-length` | `net`, `pad` | a stub's via over 1 mm from the pad |
| Routing | `thermal-vias` | `net`, `pad` | a thermal pad with too few vias |
| Routing | `big-pad-vias` | `net`, `pad` | a big pad with too few vias (note) |
| Routing | `pad-no-via` | `net`, `pad` | a pad with no via into the plane |
| Routing | `via-shared` | `net`, `pads` | a via serving two or more pads |
| Routing | `trace-pad-to-pad` | `net`, `pads` | a trace on a plane net from pad to pad |
| Routing | `supply-no-copper` | `pin`, `capacitor` | no copper from the pin to its capacitor |
| Routing | `supply-bypass` | `pin` | the pin reaching a source past its capacitors |
| Routing | `supply-unfed` | `pin`, `capacitor` | the capacitor reaching no zone or other part |
| Routing | `bend` | `net`, `layer`, `at` | a right-angle or sharper bend, at its corner |
| Project | `class-impedance-name` | `netclass` | a class named for an impedance, misnamed |
| Project | `class-interface` | `netclass` | a differential class naming an unknown interface |
| Project | `class-undefined` | `netclass` | a class assigned or in a pattern, not defined |
| Project | `power-net-class` | `net` | a power net with no class |
| Project | `half-pair` | `net` | a net named like half a pair (note) |
| Project | `pair-class` | `nets` | a pair not in one Z_DIFF class |
