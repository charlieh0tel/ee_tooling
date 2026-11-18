---
name: fab-release
description: Build a KiCad board's release package for its fab (Gerber/drill zip, ODB++, IPC-2581, BOM, placement, source tgz) under its production directory, YYYYMMDD-REV/, and, for JLCPCB, fix part rotations and offsets from its placement preview. Use when the user asks to release, package, or order a board.
---

# Fab release

`fab-release` (ee_tooling) builds the package.  Run it anywhere in the
board's git repository:

```
uv run --project PATH/TO/ee_tooling fab-release            # the first board
uv run --project PATH/TO/ee_tooling fab-release BOARD      # another
uv run --project PATH/TO/ee_tooling fab-release --date 20261027 --force   # rebuild a day's release
uv run --project PATH/TO/ee_tooling fab-release --date 20261027 --force --retag   # and move its tag
```

A repository with ee_tooling as a uv dependency runs plain
`uv run fab-release` from that project.  The repository's own
instructions may name a board-specific skill with its ordering checklist:
follow it as well.  Each profile's ordering steps are in this directory:
`jlcpcb.md` for `profile = "jlcpcb"`.

## Configuration

`fab.toml` at the repository root, one `[[board]]` each:

```toml
[[board]]
name = "widget"                    # the KiCad project
directory = "hardware/widget"      # holds widget.kicad_pcb and widget.kicad_sch
production = "production"          # where YYYYMMDD-REV/ goes
sources = ["docs"]                 # added to the source tgz; optional, default []
profile = "jlcpcb"                 # the fab: jlcpcb

[board.jlcpcb]                     # the profile's options, optional (jlcpcb.md)
require_lcsc = true
```

Every key but `sources` and the profile's table is required.  A key it
does not know, at any level, is an error naming it; the only table
allowed is the one named by `profile`.  Paths are from the repository root.
`adopt` writes `production` for a lone board and `production/<name>` for
each of several.  There is no `copper` key: the copper layers, top to
bottom, are the stackup's.  Each `sources` path is inside the repository: one that
is absolute or leads out of it (`..`) is an error naming it.

## The source tgz

`NAME-REV-source.tgz` holds the project's `.kicad_pcb`, `.kicad_sch`,
`.kicad_pro`, `.kicad_dru`, `.pretty`, `.3dshapes`, `fp-lib-table` and
`sym-lib-table` that exist (directories whole), then `sources`, then
the schematic and board PDFs (`pdf/`) and `COMMIT` (the commit, `-dirty`
if built from uncommitted sources, and when it was built).

It leaves out every library outside the repository: a sibling clone
named by a relative path (`${KIPRJMOD}/../lib`) in a library table or a
3D model path.  The board file embeds each footprint and the schematic
each symbol, so the archive rebuilds the board and its outputs; the 3D
models of parts from such a library are missing from it.  A library the
archive must hold goes inside the repository (a submodule) and in
`sources`.

It stops unless ERC is clean, DRC (with schematic parity) is clean,
the board's directory and `sources` have no uncommitted changes
(`--allow-dirty` only for a trial; the tgz's COMMIT file then says
`-dirty`), and the profile's own checks pass (`jlcpcb.md`), all before
anything is written.

## The tag

It tags the commit the package was built from (the tgz's COMMIT),
annotated, `NAME-YYYYMMDD-REV`, with the message `NAME REV: JLCPCB
release PRODUCTION/YYYYMMDD-REV`.  It does not push the tag: it prints
the command, `git push origin refs/tags/NAME-YYYYMMDD-REV`.  Run that
only when the owner says to.

- A build from uncommitted sources (`--allow-dirty`) is not tagged.
- A rebuild (`--force`) of the commit the tag is on leaves the tag.
- A tag already on another commit stops it before anything is replaced;
  `--retag` moves the tag, and the printed push then has `--force`.
  Once the order is placed, the release is fixed: don't move its tag.

## Before running

1. The board's title-block revision is the release's REV: bump it in the
   board and schematic first if this is a new revision.
2. Set the board's stackup in Board Setup, Physical Stackup, if it has
   none: the release takes the copper layers from it, and refuses a
   board without one.
3. Commit the board and its sources.

## What it writes

`PRODUCTION/YYYYMMDD-REV/`, NAME being the KiCad project:

| File | Use |
|---|---|
| `NAME-REV-jlcpcb.zip` | the PCB order: Gerbers (each copper layer, mask, paste, silk, Edge.Cuts) and Excellon drill in mm, in the profile's options (`jlcpcb.md`); `README.txt`, the Gerber map |
| `NAME-REV-gerber-map.txt` | the Gerber map, as in the zip: each file's name, KiCad layer, function (copper L1 top to Ln bottom, mask, paste, silkscreen, outline, plated and non-plated drill), side and polarity, from KiCad's Gerber job file and the drill files' X2 comments |
| `NAME-REV-stackup.txt` | the board's stackup (Board Setup, Physical Stackup): each layer's and dielectric sublayer's type, material, thickness, Er, loss tangent, color and Gerber; total thickness and the board's thickness setting; copper finish, impedance-controlled dielectric, edge connector, castellated pads, edge plating; then the impedance requirements, from net classes whose names say impedance (`[TBD: ...]` for a name not `<ohms>Z_DIFF[_<INTERFACE>]` or `<ohms>Z_SE`). |
| `NAME-REV-fab-notes.txt` | numbered fabrication notes: generic (IPC-6012 and IPC-A-600 class 2 by default, plating, LPI mask per IPC-SM-840, finished hole sizes, Gerbers not to be modified, no added tooling holes or marking), filled from the board: layer count, finished thickness, copper weights, base material, finish and mask and silk colors from the stackup; impedance control from the net classes; minimum track and space from KiCad's Gerber job file, minimum drill from the drill files.  `[TBD: ...]` marks what the board cannot say (Tg, electrical test, and anything the stackup lacks): fill it in before sending the notes |
| `NAME-REV-odb.tgz` | ODB++ job (one `odb/` directory: `matrix/`, `steps/`, ...), mm, 6 decimals: for the record and for ODB++ tools such as `tools/odb_current`; not the order's upload |
| `NAME-REV-ipc2581.zip` | IPC-2581 revision C, mm, 6 decimals: a zip of one XML file, `NAME-REV-ipc2581.xml`; its BOM from MPN, Manufacturer and the profile's distributor field: for the record and for IPC-2581 tools; not the order's upload |
| `NAME-REV-bom.csv`, `NAME-REV-cpl.csv` | the profile's assembly BOM and placement (`jlcpcb.md`) |
| `NAME-REV-source.tgz` | the design files, `sources`, schematic and board PDFs, and COMMIT |

Commit the production directory after the order is placed, and do not
change it afterwards.
