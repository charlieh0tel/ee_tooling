# KiCad rules

ee_tooling's rules for KiCad 10's own checks, in `src/ee_tooling/rules/`.
DRC and ERC check them live in the editor, and `jlcpcb-release` refuses
a board with DRC or ERC violations.

| File | What |
|---|---|
| `jlcpcb.kicad_dru` | custom DRC rules: JLCPCB's limits, JLCPCB's price, then the house rules |
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
  clearance (KiCad 10.0.8, `pcbnew/drc/drc_engine.cpp`).  So copper
  clearance, copper to edge and a via's hole clearance, which are lower
  at JLCPCB than a board would choose and which shape zone fills, are
  Board Setup minimums, not rules.
- A `track_angle` limit is written without a unit: KiCad 10.0.8 ignores
  one written `91deg`.
- `jlcpcb-release` runs `kicad-cli ... --exit-code-violations`, which
  fails on a warning as on an error.

## Installing in a board repository

By hand, with KiCad closed (`ee-tooling-adopt` does not yet):

1. `NAME.kicad_dru` is `jlcpcb.kicad_dru` followed by the board's own
   rules, without their `(version 1)` line.  The board's rules come
   last, so they win.
2. In `NAME.kicad_pro`, raise each of `board.design_settings.rules` to
   at least its value in `jlcpcb_board_setup.json` (Board Setup, Design
   Rules, Constraints).
3. In `NAME.kicad_pro`, set each of `erc.rule_severities` and
   `schematic.connection_grid_size` to its value in
   `erc_severities.json` (Schematic Setup, Electrical Rules, Violation
   Severity; Formatting, Connection grid).

## JLCPCB's numbers

Source: JLCPCB, Rigid PCB Manufacturing Capabilities,
<https://jlcpcb.com/capabilities/pcb-capabilities>, read 2026-11-01 (the
page is undated; it says "LDI equipment upgraded in June 2025").  FR-4,
1 oz outer copper, through vias.  Where the page gives a 1- and 2-layer
and a multilayer figure, the stricter is used, so one file serves 2, 4
and 6 layers.  mm.

| Page: item | 1-2 layer | Multilayer | Checked by |
|---|---|---|---|
| Traces: min. track width and spacing (1 oz) | 0.10 / 0.10 | 0.09 / 0.09 | `JLCPCB track width` 0.1; Board Setup `min_clearance` 0.1 |
| Traces: pad to track clearance (1 oz) | 0.1 | 0.1 | Board Setup `min_clearance` 0.1 |
| Traces: SMD pad to pad clearance (different nets) | 0.15 | 0.15 | not checked: a `clearance` rule would replace the net classes' |
| Traces: minimum SMD pad | 0.25 x 0.25 | 0.25 x 0.25 | `JLCPCB SMD pad size` (pads with copper) |
| Traces: BGA pad (ENIG) | 0.2 | 0.2 | `JLCPCB BGA pad size` (fabrication property BGA pad) |
| Traces: PTH annular ring (1 oz), absolute minimum | 0.18 | 0.15 | `JLCPCB annular ring` 0.18 |
| Drilling: min. via hole size / diameter | 0.15 / 0.25 | 0.15 / 0.25 | `JLCPCB hole size` 0.15, `JLCPCB via diameter` 0.25 |
| Drilling: via diameter over via hole | 0.1 | 0.1 | `JLCPCB via annular ring` 0.05 |
| Drilling: drill diameter | 0.15 - 6.3 | 0.15 - 6.3 | `JLCPCB hole size` 0.15 (6.3 and up is routed) |
| Drilling: min. non-plated holes | 0.50 | 0.50 | `JLCPCB non-plated hole size` |
| Drilling: min. plated slots width | 0.5 | 0.35 | `JLCPCB plated slot width` 0.5 |
| Drilling: min. non-plated slots | 1.0 | 1.0 | `JLCPCB non-plated slot width` |
| Drilling: via hole-to-hole spacing | 0.2 | 0.2 | `JLCPCB hole to hole` (all holes) |
| Drilling: pad hole-to-hole spacing | 0.45 | 0.45 | `JLCPCB pad hole to pad hole` (plated pads) |
| Outline: mouse bites, distance between two | 0.2 - 0.3 | 0.2 - 0.3 | `JLCPCB hole to hole` 0.2 (non-plated holes) |
| Traces: via hole to track | 0.2 | 0.2 | Board Setup `min_hole_clearance` 0.2 |
| Traces: NPTH to track | 0.2 | 0.2 | Board Setup `min_hole_clearance` 0.2 |
| Traces: PTH to track | 0.28 | 0.28 | `JLCPCB plated hole clearance` (outer layers) |
| Traces: inner layer PTH pad hole to copper | - | 0.3 | `JLCPCB inner plated hole clearance` |
| Outline: copper from routed edges and slots | 0.2 | 0.2 | Board Setup `min_copper_edge_clearance` 0.2 |
| Legend: minimum line width | 0.15 | 0.15 | `JLCPCB silkscreen text` thickness (text only) |
| Legend: minimum text height | 1.0 | 1.0 | `JLCPCB silkscreen text` height, overridden by the house's 0.8 |
| Legend: pad to silkscreen | 0.15 | 0.15 | `JLCPCB silkscreen to pad` |

`JLCPCB silkscreen to pad` holds a footprint's own silkscreen graphics
only off its own pads (Board Setup's silk clearance), since they are its
library's (`docs/kicad-style.md`, Silkscreen); its reference text is
held 0.15 mm off.

Price, the same page, Drilling, min. via hole size/diameter: a hole of
0.1 or 0.15, or of 0.2 or 0.25 in a diameter under 0.45, costs more.
`JLCPCB via hole at no extra cost` and `JLCPCB small via hole at no
extra cost` hold a via to a hole of 0.2, and 0.3 in a diameter under
0.45.

Not checked: 2 oz and heavier copper's figures; copper from a V-cut
edge (0.4; KiCad does not tell a V-cut from a routed edge); a
silkscreen graphic's line width (KiCad's library footprints draw silk at
0.12); character width to height, 1:6 ("preferred"); soldermask bridge
(0.10, 0.13 black and white) and expansion (1:1); same-net track
spacing (0.25); hatched grid width and spacing (0.25); castellated holes.

## House rules

From `docs/kicad-style.md`, after JLCPCB's, so that they win.

| Rule | Style | What |
|---|---|---|
| `Mounting hole ring` | Board finishing | a `MountingHole*` footprint's pad with a drill of 1 mm or more: annular ring 0.5 |
| `Mounting hole plated` | Board finishing | that pad plated |
| `Silkscreen text height` | Silkscreen | text at least 0.8 high: the house overrides JLCPCB's 1.0; the thickness stays JLCPCB's 0.15 |
| `No right-angle bends` | Routing | joined segments at 91 degrees or more (180 straight, 135 a 45-degree bend) |
| `Differential pair coupled` | Net classes, Routing | in a `*Z_DIFF` class: at most 5 mm uncoupled, 0.5 mm skew within the pair (house choices) |

- KiCad measures the annular ring to the nearest hole, so stitching
  vias within 0.5 mm of a mounting hole's drill fail
  `Mounting hole ring`; it takes a footprint's concentric pads together
  (KiCad's `..._Pad_TopBottom` passes).
- `track_angle` does not check a joint on a pad, nor one at a via.  It
  also reports a branch (a T) at 90 degrees, a segment doubling back (0)
  and a zero-length segment, and reports each joint twice.
- A differential pair's gap is its net class's: a `diff_pair_gap` rule
  would replace it, the router's gap included.

## ERC severities

`erc_severities.json` sets every ERC check KiCad 10.0.8 has
(`eeschema/erc/erc_item.cpp`; defaults, `erc_settings.cpp`), and the
connection grid, 100 mils (2.54 mm, `docs/kicad-style.md`), which
`endpoint_off_grid` measures against (eeschema manual, Electrical Rules
Check).  `pin_to_pin` at `ignore` turns off the pin conflict map's
warnings and errors; at anything else they stay as the map has them.
An error is always a mistake; a
warning is for the owner to judge; both fail `jlcpcb-release`.

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
| `endpoint_off_grid` | warning | error | style: every connection point on the 2.54 mm grid |
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
| Mounting holes with an annular ring of 0.5 | `Mounting hole ring` | no: KiCad's ring stops at the nearest hole, kicad-style-check's at the widest pad |
| Mounting holes on a net | none | no |
| Connection points on the grid | `endpoint_off_grid` | no: KiCad checks pins and wire ends, not labels, junctions or no-connects |
| No pin without a wire or no-connect | `pin_not_connected` | no: to KiCad a pin on another pin, or on a label, is connected |
| No-connects on pins | `no_connect_dangling` | yes |
| A junction on every join of three or more, and nowhere else | none | no |
| Schematic: the other rules | none | no |
