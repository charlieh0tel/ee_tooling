# KiCad rules

ee_tooling's rules for KiCad 10's own checks, in rule sets: each the
files below as an ee_tooling release shipped them, in
`src/ee_tooling/rules/VERSION/`, VERSION being the release that
introduced it.  The newest set is the current one; a board follows it
unless its `fab.toml` pins another (Pinning a board's rules, below).
This document describes the current set.
DRC and ERC check them live in the editor, and `fab-release` refuses
a board with DRC or ERC violations.

| File | What |
|---|---|
| `jlcpcb.kicad_dru` | custom DRC rules, in sections headed `# --- <name> ---`: JLCPCB's limits, then the house rules |
| `jlcpcb_board_setup.json` | a `.kicad_pro` fragment: Board Setup minimums from JLCPCB's limits |
| `erc_severities.json` | a `.kicad_pro` fragment: every ERC check's severity, and the connection grid |

## How KiCad applies them

- A project has one custom rules file, `NAME.kicad_dru` beside
  `NAME.kicad_pro`.
- For each kind of constraint, the last rule in the file that matches
  wins (pcbnew manual, Custom design rules).
- A custom rule replaces, for what it matches, the Board Setup
  constraint of its kind, and a custom `clearance` rule the net classes'
  clearances.  Board Setup's minimum clearance is a floor under every
  clearance (KiCad 10.0.8, `pcbnew/drc/drc_engine.cpp`).  So a rule
  installed with a lower minimum than the board's own Board Setup value
  would loosen the board's limit for what it matches (checked with
  `kicad-cli pcb drc`: a board with Board Setup track width 0.5 and a
  0.1 mm `track_width` rule passes its 0.15 mm tracks).
- Hence: every limit that holds for every item of its kind is a Board
  Setup minimum, raised to and never lowered: copper clearance, copper
  to edge, hole clearance, track width, via diameter, hole size (through
  hole diameter), a via's annular width, hole to hole.  The custom rules
  are only those Board Setup cannot express: a condition (plated or not,
  slots, pads, a footprint), a layer, an assertion, a skew.  And each of
  their minimums of a kind Board Setup also holds (`kicad_rules.py`,
  `BOARD_SETUP_KEYS`) is raised to the board's Board Setup value where
  that is higher, when installed and in `fab-release`'s DRC, so no rule
  loosens the board's own limit.  A board that later raises a Board
  Setup value has a stale section: `adopt --check` fails, and
  `fab-release` warns, until `adopt --yes`.
- A minimum is raised only where the Board Setup value constrains what
  the rule does (checked with `kicad-cli pcb drc` on a synthetic board,
  one Board Setup value made strict at a time, no custom rules):

  | Board Setup | What it holds (10.0.8) | The profile's rules raised to it |
  |---|---|---|
  | `min_track_width` | tracks | none left (Board Setup's own) |
  | `min_via_diameter` | through vias | none left |
  | `min_microvia_drill` | microvia holes | none |
  | a net class's `microvia_diameter` | its microvias' diameter (Board Setup's `min_microvia_diameter` is not checked in 10.0.8) | none: raised to 0.25 in each net class that sets it |
  | `min_through_hole_diameter` | via, PTH and NPTH holes | non-plated hole, plated and non-plated slot |
  | `min_via_annular_width` | vias and PTH pads | the PTH pad's annular ring (`JLCPCB annular ring`) |
  | `min_hole_to_hole` | hole pairs | pad hole to pad hole, mounting hole vias |
  | `min_hole_clearance` | via, PTH and NPTH holes to copper | plated hole clearance, outer and inner |
  | `min_text_height`, `min_text_thickness` | silkscreen text only (not Fab, not copper) | silkscreen text, silkscreen text height |
  | `min_silk_clearance` | silkscreen to silkscreen only | none: the silkscreen to pad rules are not raised to it |
- `fab-release` runs `kicad-cli ... --exit-code-violations`, which
  fails on a warning as on an error.

## In a board repository

`ee-tooling-adopt --yes` (`docs/adopting.md`) installs a board's
profile's rules from the board's rule set (`kicad_rules.py`), with
KiCad closed: KiCad does not
reload a project file changed under it, and saving from KiCad
overwrites the change.

1. `NAME.kicad_dru`: the profile's rules, without their `(version 1)`
   line, between markers, then the board's own rules, which come last
   and so win.  In a `.kicad_dru` the later of two rules for the same
   item wins:

   ```
   (version 1)
   <the board's rules before the markers, if any: the section wins over them>
   # --- ee_tooling: jlcpcb rules; written by adopt ---
   # ee_tooling rule set VERSION
   <the profile's rules>
   # --- ee_tooling: end; the board's own rules follow and win ---
   <the board's own rules>
   ```

   The line after the begin marker names the rule set; the 0.4.0 set's
   section, as boards installed it before sections named their set,
   has no such line.

   A rerun replaces the section between the markers and keeps the rest
   where it is: the board's rules before the begin marker stay before
   it, those after the end marker after it, and the version line first.
   A file without markers gets the section after its version line, its
   rules after the section.
   A rule between the markers that is not ee_tooling's (neither in the
   section nor in an older rules file of ee_tooling's: put there by
   hand) is named on stderr, with its lines' count, as it goes; a value
   ee_tooling changed is not.  A Board Setup value that is not a finite
   number (NaN or Infinity in the `.kicad_pro`) raises no minimum.
   A rule of the board's own after the markers with the name of one of
   the section's (a copy made by hand) is named on stderr, and left: it
   wins over the section's (one before the markers loses to it, and is
   not named).  So is a rule of the board's own, before or after the
   markers, with the name of one an older rules file of ee_tooling's had and the board's rule set does
   not (a hand copy of an older rules file: `JLCPCB track width`, `JLCPCB via diameter`,
   `JLCPCB hole size`, `JLCPCB via annular ring`, `JLCPCB hole to hole`,
   the two `via hole at no extra cost` rules, `Mounting hole ring`, `No
   right-angle bends`, `Differential pair coupled`): retired, still in
   force; remove it.  Markers other than one begin line, then one end line (an
   end before the begin, two of either) stop `adopt` ("install the rules
   by hand") and `fab-release`, naming the file: fix them by hand.
2. `NAME.kicad_pro`: each of `board.design_settings.rules` raised to at
   least its value in `jlcpcb_board_setup.json` (Board Setup, Design
   Rules, Constraints), never lowered, and each net class's
   `microvia_diameter` raised to the profile's where the class sets a
   lower one (one that does not set it has KiCad's 0.3); then the
   section's minimums are raised to these values where they are higher
   (above).
3. `NAME.kicad_pro`: each of `erc.rule_severities` set to its value in
   `erc_severities.json` (Schematic Setup, Electrical Rules, Violation
   Severity), kept where the board's is stricter (`ignore`, then
   `warning`, then `error`); a check the file does not name is kept as
   the board has it; and `schematic.connection_grid_size` set to the
   file's (Formatting, Connection grid).  The dry run and `--yes` name
   each value changed, old and new: a connection grid of 25 mil made 50
   with `endpoint_off_grid` an error turns the endpoints off the new grid
   into ERC errors.

A key added to a `.kicad_pro` object whose keys are in sorted order, as
KiCad saves them, goes in its sorted place, so KiCad's next save does
not reorder the file; in an object not in that order it goes last.

`fab-release` runs DRC on a scratch copy of the project (its
`.kicad_pcb`, `.kicad_sch` and `.kicad_prl` copied, every other entry
of its directory linked, at its own path from the
repository's root under the scratch directory beside links to its
ancestors' other entries up to the root, so a library path relative to
the project finds the same library) with the same rules installed and
Board Setup raised, so a release is checked against the board's rule
set whatever the board's files hold; it never writes the board's
files.  It warns when the board's `.kicad_dru` lacks the set's
section, or its Board Setup is below the set's minimums, naming
`adopt --yes`.

## Pinning a board's rules

A board's `[[board]]` in `fab.toml` may pin its rule set:

```toml
rules = "0.4.0"
```

Without `rules`, the board follows the current set.  With it, that set
is the board's in `adopt` and in `fab-release`:

- `adopt --yes` installs that set's section, raises Board Setup to its
  minimums and sets its ERC severities and connection grid; `--check`
  compares the board with that set, so a newer ee_tooling with a newer
  set does not report the board as to be updated.  A section changed
  by hand is out of date as for any board.
- `fab-release` runs DRC, and its canary copy, with that set, and warns
  against it.
- A retired rule (above) is one an older rules file had and the pinned
  set does not.
- `adopt` (the dry run, `--yes` and `--check`) and `fab-release` say
  once on stderr, when the pinned set is not the current one:
  `NAME: ee_tooling's rules pinned at VERSION by fab.toml; the current
  rules are CURRENT`.  Pinned at the current set, nothing.
- A version ee_tooling does not have is an error naming the versions
  it has: `rules '0.3.0' is not one of 0.4.0`.  The first set is
  0.4.0.

A pin holds the rules: the section, the Board Setup minimums and the
ERC values.  It does not hold the code that applies them (the markers,
the Board Setup raise, a minimum raised to the board's Board Setup
value; tests hold each set's output, `docs/releasing.md`, Rule sets),
KiCad's own DRC and ERC, the release's other checks, kicad-style-check,
or the tools: pin the submodule to a tag (`docs/adopting.md`) for
those.

Nothing is ever lowered.  A board pinned at an older set after a newer
one was installed gets the older section back, and the older set's
connection grid; its Board Setup values stay as raised, and an ERC
severity stays where it is stricter, so its DRC is the older set's
rules with the board's own, possibly higher, Board Setup minimums.

To unpin, remove `rules`: the board follows the current set, and
`adopt --check` reports it to be updated until `adopt --yes` installs
that set.  Pinning at the set the board has installed changes nothing
in its files.

## JLCPCB's numbers

Source: JLCPCB, Rigid PCB Manufacturing Capabilities,
<https://jlcpcb.com/capabilities/pcb-capabilities>, read 2026-11-01 (the
page is undated; it says "LDI equipment upgraded in June 2025").  FR-4,
1 oz outer copper, through vias.  Where the page gives a 1- and 2-layer
and a multilayer figure, the stricter is used, so one file serves 2, 4
and 6 layers.  mm.

| Page: item | 1-2 layer | Multilayer | Checked by |
|---|---|---|---|
| Traces: min. track width and spacing (1 oz) | 0.10 / 0.10 | 0.09 / 0.09 | Board Setup `min_track_width` 0.1, `min_clearance` 0.1 |
| Traces: pad to track clearance (1 oz) | 0.1 | 0.1 | Board Setup `min_clearance` 0.1 |
| Traces: SMD pad to pad clearance (different nets) | 0.15 | 0.15 | not checked: a `clearance` rule would replace the net classes' |
| Traces: minimum SMD pad | 0.25 x 0.25 | 0.25 x 0.25 | `JLCPCB SMD pad size` (pads with copper) |
| Traces: BGA pad (ENIG) | 0.2 | 0.2 | `JLCPCB BGA pad size` (fabrication property BGA pad) |
| Traces: PTH annular ring (1 oz), absolute minimum | 0.18 | 0.15 | `JLCPCB annular ring` 0.18 (pads) |
| Drilling: min. via hole size / diameter | 0.15 / 0.25 | 0.15 / 0.25 | Board Setup `min_through_hole_diameter` 0.15, `min_via_diameter` 0.25 (through vias); `min_microvia_drill` 0.15 and each net class's `microvia_diameter` raised to 0.25 (microvias) |
| Drilling: via diameter over via hole | 0.1 | 0.1 | Board Setup `min_via_annular_width` 0.05 |
| Drilling: drill diameter | 0.15 - 6.3 | 0.15 - 6.3 | Board Setup `min_through_hole_diameter` 0.15 (6.3 and up is routed) |
| Drilling: min. non-plated holes | 0.50 | 0.50 | `JLCPCB non-plated hole size` |
| Drilling: min. plated slots width | 0.5 | 0.35 | `JLCPCB plated slot width` 0.5 |
| Drilling: min. non-plated slots | 1.0 | 1.0 | `JLCPCB non-plated slot width` |
| Drilling: via hole-to-hole spacing | 0.2 | 0.2 | Board Setup `min_hole_to_hole` 0.2 (all holes) |
| Drilling: pad hole-to-hole spacing | 0.45 | 0.45 | `JLCPCB pad hole to pad hole` (plated pads) |
| Outline: mouse bites, distance between two | 0.2 - 0.3 | 0.2 - 0.3 | Board Setup `min_hole_to_hole` 0.2 (non-plated holes) |
| Traces: via hole to track | 0.2 | 0.2 | Board Setup `min_hole_clearance` 0.2 |
| Traces: NPTH to track | 0.2 | 0.2 | Board Setup `min_hole_clearance` 0.2 |
| Traces: PTH to track | 0.28 | 0.28 | `JLCPCB plated hole clearance` (outer layers) |
| Traces: inner layer PTH pad hole to copper | - | 0.3 | `JLCPCB inner plated hole clearance` |
| Outline: copper from routed edges and slots | 0.2 | 0.2 | Board Setup `min_copper_edge_clearance` 0.2 |
| Legend: minimum line width | 0.15 | 0.15 | `JLCPCB silkscreen text` thickness (text only) |
| Legend: minimum text height | 1.0 | 1.0 | `JLCPCB silkscreen text` height, overridden by the house's 0.8 |
| Legend: pad to silkscreen | 0.15 | 0.15 | `JLCPCB silkscreen to pad` |


Not checked: JLCPCB's price (the same page, Drilling, min. via hole
size/diameter: a hole of 0.1 or 0.15, or of 0.2 or 0.25 in a diameter
under 0.45, costs more), as no-extra-cost via rules are deferred
(`docs/design/fab-release.md`); 2 oz and heavier copper's figures; copper from a V-cut
edge (0.4; KiCad does not tell a V-cut from a routed edge); a
silkscreen graphic's line width (KiCad's library footprints draw silk at
0.12); character width to height, 1:6 ("preferred"); soldermask bridge
(0.10, 0.13 black and white) and expansion (1:1); same-net track
spacing (0.25); hatched grid width and spacing (0.25); castellated holes.

## House rules

From `docs/kicad-style.md`, after JLCPCB's, so that they win.

| Rule | Style | What |
|---|---|---|
| `Mounting hole plated` | Board finishing | a `MountingHole*` footprint's pad with a drill of 1 mm or more plated |
| `Mounting hole vias` | Board finishing | between pads of one `MountingHole*` footprint (its stitching vias): JLCPCB's via hole-to-hole spacing, 0.2, not its pad spacing, 0.45 |
| `Footprint silkscreen to its own pads` | Silkscreen | a footprint's own silkscreen graphics take no clearance to its own pads, since they are its library's; its reference text keeps JLCPCB's 0.15 |
| `Silkscreen text height` | Silkscreen | text at least 0.8 high: the house overrides JLCPCB's 1.0; the thickness stays JLCPCB's 0.15 |
| `Differential pair skew, ...` | Net classes, Routing | skew within a pair, by the interface its class names (Differential pairs, below) |

- The mounting hole's 0.5 mm ring is kicad-style-check's, which
  measures the land: KiCad's annular width stops at the nearest hole, so
  a mounting hole's stitching vias within 0.5 mm of its drill would fail
  it.
- `A.Reference == B.Reference` tells one footprint's pads (KiCad 10.0.8
  compares `A.Parent` by reference too).
- No right-angle bends is kicad-style-check's: KiCad's `track_angle`
  reports a branch (a T) at 90 degrees as it does a bend.  (It also
  ignores a limit written with a unit, `91deg`, in KiCad 10.0.8.)
- A differential pair's gap is its net class's: a `diff_pair_gap` rule
  would replace it, the router's gap included.

## Differential pairs

A pair's class names its interface, `<ohms>Z_DIFF_<INTERFACE>`
(`90Z_DIFF_USBHS`), and the interface sets the skew within the pair
(KiCad's `skew` with `within_diff_pairs`: the difference of the two
nets' routed lengths).  kicad-style-check reports a suffix not in this
table (`kicad_style_netclass.py`, `INTERFACES`).  DVI's TMDS pairs take
`HDMI`.

| Suffix | Interface | Skew within the pair, mm | Source |
|---|---|---|---|
| `USBFS` | USB 2.0 full speed | 1.27 | TI SPRAAR7J, Table A-2: USB2.0, 50 mil |
| `USBHS` | USB 2.0 high speed | 1.27 | TI SPRAAR7J, Table A-2: USB2.0, 50 mil |
| `USB3` | USB 3.x SuperSpeed | 0.127 | TI SPRAAR7J, Table A-2: USB3, 5 mil |
| `PCIE` | PCI Express | 0.127 | TI SPRAAR7J, Table A-2: PCIe, 5 mil |
| `HDMI` | HDMI TMDS | 0.127 | TI SPRAAR7J, Table A-2: HDMI, 5 mil |
| `ETH1G` | Ethernet 1000BASE-T MDI | 0.508 | TI SNLA387, MDI Traces: 20 mil for 1G |
| `ETH100` | Ethernet 100BASE-TX MDI | 1.27 | TI SNLA387, MDI Traces: 50 mil for 100M |

Sources:

- TI, High-Speed Interface Layout Guidelines, SPRAAR7J (November 2018,
  revised February 2023), Appendix A, Table A-2 (AM57xx/DRA7xx): "Skew
  within any USB2.0 differential pair", 50 mil; "Skew within any
  USB3/SATA/PCIe/HDMI differential pair", 5 mil.  The other tables of
  Appendix A give the same figures.
  <https://www.ti.com/lit/an/spraar7j/spraar7j.pdf>
- TI, Ethernet PHY PCB Design Layout Checklist, SNLA387 (June 2021),
  section 2, MDI Traces: "length-matched within 20 mils for 1G
  transmissions and within 50 mils for 100M or 10M transmissions".
  <https://www.ti.com/lit/an/snla387/snla387.pdf>

No rule: a class naming no interface (`90Z_DIFF`), since the figures
above run from 0.127 to 1.27 mm and one default would either fail most
USB 2.0 pairs or pass a USB 3 pair ten times over its figure; LVDS and
MIPI, and a pair's uncoupled length, for want of a citable figure.

## ERC severities

`erc_severities.json` sets every ERC check KiCad 10.0.8 has
(`eeschema/erc/erc_item.cpp`; defaults, `erc_settings.cpp`), and the
connection grid, 50 mils (1.27 mm, `docs/kicad-style.md`), which
`endpoint_off_grid` measures against (eeschema manual, Electrical Rules
Check).  `pin_to_pin` at `ignore` turns off the pin conflict map's
warnings and errors; at anything else they stay as the map has them.
An error is always a mistake; a
warning is for the owner to judge; both fail `fab-release`.

| Check | KiCad's default | Here | Why |
|---|---|---|---|
| `bus_definition_conflict` | error | error | a mistake |
| `bus_entry_needed` | error | error | a mistake |
| `bus_to_bus_conflict` | error | error | a mistake |
| `bus_to_net_conflict` | error | error | a mistake |
| `different_unit_footprint` | error | error | a mistake |
| `different_unit_net` | error | error | a mistake |
| `duplicate_reference` | error | error | a mistake |
| `duplicate_sheet_names` | error | error | a mistake |
| `endpoint_off_grid` | warning | error | style: every connection point on the 1.27 mm grid |
| `extra_units` | error | error | a mistake |
| `field_name_whitespace` | warning | warning | may be meant |
| `footprint_filter` | ignore | ignore | a part's footprint is chosen for its LCSC part, often outside the symbol's filters |
| `footprint_link_issues` | warning | warning | `--schematic-parity` catches a wrong footprint at release |
| `four_way_junction` | ignore | ignore | style allows a four-way join, with its junction |
| `ground_pin_not_ground` | warning | warning | may be meant (an isolated ground) |
| `hier_label_mismatch` | error | error | a mistake |
| `isolated_pin_label` | warning | warning | may be meant (a test point's net) |
| `label_dangling` | error | error | a mistake |
| `label_multiple_wires` | warning | error | such a label names no one net |
| `lib_symbol_issues` | warning | warning | for the owner: a library not in the table |
| `lib_symbol_mismatch` | warning | warning | for the owner: a symbol edited in place |
| `missing_bidi_pin` | warning | warning | may be meant (an unused unit) |
| `missing_input_pin` | warning | warning | may be meant (an unused unit) |
| `missing_power_pin` | error | error | a unit's supply left off |
| `missing_unit` | warning | warning | may be meant (an unused unit) |
| `multiple_net_names` | warning | error | a net has one name; KiCad keeps one, and net class patterns may miss it |
| `net_not_bus_member` | warning | warning | for the owner |
| `no_connect_connected` | warning | error | a pin is either connected or not |
| `no_connect_dangling` | warning | error | style: no-connects on pins |
| `pin_not_connected` | error | error | style: every pin wired or no-connect |
| `pin_not_driven` | error | error | a mistake |
| `pin_to_pin` | warning | warning | the pin conflict map's warnings are judgments |
| `power_pin_not_driven` | error | error | a mistake (a `PWR_FLAG` where a supply comes from a connector) |
| `same_local_global_label` | warning | warning | may be meant |
| `same_local_global_power` | warning | warning | may be meant |
| `similar_label_and_power` | warning | warning | may be meant |
| `similar_labels` | warning | warning | may be meant |
| `similar_power` | warning | warning | may be meant |
| `simulation_model_issue` | ignore | ignore | boards are not simulated |
| `single_global_label` | ignore | ignore | a global label used once is not a mistake |
| `stacked_pin_name` | warning | warning | may be meant |
| `unannotated` | error | error | a mistake |
| `unconnected_wire_endpoint` | warning | error | a wire end on nothing is unfinished |
| `undefined_netclass` | error | error | a mistake |
| `unit_value_mismatch` | error | error | a mistake |
| `unresolved_variable` | error | error | a mistake |
| `wire_dangling` | error | error | a mistake |

## Overlap with kicad-style-check

| kicad-style-check rule | KiCad's check | Redundant |
|---|---|---|
| Mounting holes plated | `Mounting hole plated` | yes |
| Mounting holes with an annular ring of 0.5 | none | no |
| Mounting holes on a net | none | no |
| No right-angle bends | none: `track_angle` also reports a T | no |
| Connection points on the grid | `endpoint_off_grid` | no: KiCad checks pins and wire ends, not labels, junctions or no-connects |
| No pin without a wire or no-connect | `pin_not_connected` | no: to KiCad a pin on another pin, or on a label, is connected |
| No-connects on pins | `no_connect_dangling` | yes |
| A junction on every join of three or more, and nowhere else | none | no |
| Schematic: the other rules | none | no |
