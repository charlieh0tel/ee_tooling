---
name: jlcpcb-release
description: Build a KiCad board's JLCPCB release package (Gerber/drill zip, ODB++, BOM, placement, source tgz) under its production directory, YYYYMMDD-REV/, and fix part rotations and offsets from JLCPCB's placement preview. Use when the user asks to release, package, or order a board from JLCPCB.
---

# JLCPCB release

`jlcpcb-release` (ee_tooling) builds the package.  Run it anywhere in the
board's git repository:

```
uv run --project PATH/TO/ee_tooling jlcpcb-release            # the first board
uv run --project PATH/TO/ee_tooling jlcpcb-release BOARD      # another
uv run --project PATH/TO/ee_tooling jlcpcb-release --date 20261027 --force   # rebuild a day's release
```

A repository with ee_tooling as a uv dependency runs plain
`uv run jlcpcb-release` from that project.  The repository's own
instructions may name a board-specific skill with its ordering checklist:
follow it as well.

## Configuration

`jlcpcb.toml` at the repository root, one `[[board]]` each:

```toml
[[board]]
name = "widget"                    # the KiCad project
directory = "hardware/widget"      # holds widget.kicad_pcb and widget.kicad_sch
copper = ["F.Cu", "B.Cu"]          # copper layers, top to bottom
production = "production"          # where YYYYMMDD-REV/ goes
sources = ["docs"]                 # added to the source tgz
```

Paths are from the repository root.  The source tgz holds the project's
`.kicad_pcb`, `.kicad_sch`, `.kicad_pro`, `.kicad_dru`, `.pretty`,
`.3dshapes`, `fp-lib-table` and `sym-lib-table` that exist, then
`sources`.

It stops unless ERC is clean, DRC (with schematic parity) is clean, and
the board's directory and `sources` have no uncommitted changes
(`--allow-dirty` only for a trial; the tgz's COMMIT file then says
`-dirty`).

## Before running

1. The board's title-block revision is the release's REV: bump it in the
   board and schematic first if this is a new revision.
2. Commit the board and its sources.

## What it writes

`PRODUCTION/YYYYMMDD-REV/`, NAME being the KiCad project:

| File | Use |
|---|---|
| `NAME-REV-jlcpcb.zip` | JLCPCB PCB order: Gerbers (each copper layer, mask, paste, silk, Edge.Cuts; Protel extensions, no X2, soldermask subtracted from silk) and Excellon drill in mm, PTH and NPTH separate |
| `NAME-REV-odb.tgz` | ODB++ job (one `odb/` directory: `matrix/`, `steps/`, ...), mm, 6 decimals: for the record and for ODB++ tools such as `tools/odb_current`; not the order's upload |
| `NAME-REV-bom.csv` | assembly BOM: Comment, Designator, Footprint, LCSC Part #; not-fitted parts left out |
| `NAME-REV-cpl.csv` | assembly placement: Designator, Val, Package, Mid X, Mid Y, Rotation, Layer; only parts in the BOM |
| `NAME-REV-source.tgz` | the design files, `sources`, schematic and board PDFs, and COMMIT |

The Gerber zip is the PCB order's upload: JLCPCB's help forum says it
takes Gerbers, not ODB++
(https://jlc3dp.com/help/answers/detail/197-Can-ODB++-files-be-used-to-order,
2023-12).  Check the order page before uploading the ODB++ instead.

Commit the production directory after the order is placed, and do not
change it afterwards.

## Every order

- In the BOM preview, compare each matched part's MPN and package with
  the BOM's.
- Check stock for the scarce parts.
- Check the DFM review before paying.

## Rotations and offsets

JLCPCB draws each LCSC part from its own model, whose zero can differ
from KiCad's footprint, so corrections go by LCSC part and hold for
every board: `src/ee_tooling/parts.toml` in ee_tooling.  In JLCPCB's
placement preview, compare each part's magenta pin-1 dot with our pin 1
(silk mark or pad 1):

- turned: add the LCSC part and degrees to `[rotation]` (JLCPCB's dot
  90 deg counter-clockwise from ours: -90);
- shifted: add the LCSC part and the shift in mm to `[offset]`, in the
  part's own frame (KiCad rotation 0, y up);
- a part not yet seen in a preview goes in `[unchecked]`, and the tool
  says to check it.

Then commit in ee_tooling, update the board repository's copy, and
rebuild with `--force`.  A screenshot of the preview can be measured
against two features a known distance apart, such as mounting holes.

Passives, LEDs and SOD diodes have needed no correction so far.
Coordinates are KiCad's absolute ones (Y negative), which line up with
the Gerbers in JLCPCB's preview.
