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
variants = ["lite", "full"]        # KiCad 10 design variants built; optional, default []

[board.fab]                        # every key required
ipc_class = 2                      # IPC-A-600 / IPC-6012 class: 1, 2 or 3
base_material = "FR-4"
tg = 130                           # deg C, minimum
electrical_test = "100% netlist test per IPC-9252"

[board.jlcpcb]                     # the profile's options, optional (jlcpcb.md)
require_lcsc = true
```

Every key but `sources`, `variants` and the profile's table is required.
`[board.fab]` says what the fab is to build to that the board cannot
say; the fabrication notes take it.  `adopt` writes the profile's house
values, never `fab-release`: check them.  A key it
does not know, at any level, is an error naming it; the only tables
allowed are `fab` and the one named by `profile`.  Paths are from the repository root.
`adopt` writes `production` for a lone board and `production/<name>` for
each of several.  There is no `copper` key: the copper layers, top to
bottom, are the stackup's.  Each `sources` path is inside the repository: one that
is absolute or leads out of it (`..`) is an error naming it.

## Design variants

KiCad 10's design variants: one schematic and board, several builds
differing in which parts are fitted and in their fields.  Without
`variants` (or with `[]`), the release builds KiCad's default variant.
With it, it builds each variant listed, and not the default: one
release, one production directory, one tag, whose message names the
variants.

- A name is letters, digits, `.`, `_` and `-`, not starting with `-`;
  names are unique ignoring case.  Otherwise an error naming it.
- A listed variant the project lacks is an error, before anything
  runs.  The project's variants are those its `.kicad_pro` lists (where
  KiCad's GUI keeps them), the board's top-level `(variants ...)`, and
  each name a footprint's or a schematic symbol's variant override
  uses: `kicad-cli` takes an unknown `--variant` as the
  default.  A variant of the project (the board's, or one in the
  schematic's symbol instances) not listed is a warning naming it, as is
  a project with variants and none listed.
- Per variant, with `kicad-cli ... --variant`: the BOM and placement
  exports, so the house BOM, the profile's BOM and CPL, `require_lcsc`
  and every other profile check; IPC-2581, ODB++ and the schematic and
  board PDFs (in the source tgz, `pdf/NAME-VARIANT-schematic.pdf` and
  `pdf/NAME-VARIANT-board.pdf`).
- Each variant's schematic and board compared, as DRC's schematic
  parity does not compare variants: a part DNP in the variant's BOM and
  fitted on its board (a placement export with `--exclude-dnp` and one
  without), or the reverse, a different Value, or a different footprint
  (a variant's Footprint override: one board has one footprint per
  reference) refuses the release, naming the variant and the part.  Run
  Update PCB from Schematic.
- Each variant's Gerbers are exported and compared with the first's,
  less comment lines and dates.  The same: one Gerber zip, as without
  variants.  Different only in F.Silkscreen, B.Silkscreen or
  User.Drawings (`${VARIANT}` on the silkscreen): a Gerber zip per
  variant, `NAME-REV-VARIANT-gerbers-jlcpcb.zip`, and no shared one,
  said on stderr: each is a bare-board order of its own.  Different
  anywhere else (copper, mask, paste, Edge.Cuts, the drill, the job
  file): refused, naming the variant and the layer.  The drill export
  takes no variant.
- Built once: the Gerber map (its title naming each zip), the stackup,
  the fabrication notes and the source tgz.

## The source tgz

`NAME-REV-source.tgz` holds the project's `.kicad_pcb`, `.kicad_sch`,
`.kicad_pro`, `.kicad_dru`, `.pretty`, `.3dshapes`, `fp-lib-table` and
`sym-lib-table` that exist (directories whole), then `sources`, then
the schematic and board PDFs (`pdf/`) and `COMMIT` (the commit, `-dirty`
if built from uncommitted sources, and when it was built).

A library the board reads (footprints, symbols, 3D models) lives inside
the repository, normally a git submodule (`docs/kicad-style.md`,
Files); a clone beside the repository is not supported.  The archive
holds a library only when it is one of `sources`.  The board file embeds
each footprint and the schematic each symbol, so the archive rebuilds
the board and its outputs; the 3D models of a library not in `sources`,
and of KiCad's stock libraries, are not in it.  The release refuses, before
anything runs, a path in the project's `fp-lib-table` or
`sym-lib-table`, or a footprint's 3D model path, that leads outside the
repository or uses a variable other than `${KIPRJMOD}` and KiCad's own
install variables (`${KICAD10_3DMODEL_DIR}` and the like, KiCad's stock
libraries), naming each path.

It stops unless the board has a stackup and a stackup table on
User.Drawings matching it (kicad-style-check's rules, waivers not
honored: `stackup-table` writes or refreshes the table), ERC is clean,
DRC (with schematic parity, the profile's rules and Board Setup
minimums, on a scratch copy of the project: `docs/kicad-rules.md`) is
clean,
the board's directory and `sources` have no uncommitted changes
(every board's production directory left out, so a rebuild with
`--force` over an uncommitted release goes on)
(`--allow-dirty` only for a trial; the tgz's COMMIT file then says
`-dirty`), and the profile's own checks pass (`jlcpcb.md`), all before
anything is written.

Before any of those, once for every variant, it stops on a Git LFS
pointer among its inputs (the board's files and the source tgz's,
directories walked): a file under
1024 bytes whose first line is git-lfs's `version
https://git-lfs.github.com/spec/v1`, as a clone without git-lfs leaves
it.  It names each; `--force` and `--allow-dirty` do not pass it.  The
fix: install git-lfs, then `git lfs install --local --skip-repo` and
`git lfs pull`.

## The tag

It tags the commit the package was built from (the tgz's COMMIT),
annotated, `NAME-YYYYMMDD-REV`, with the message `NAME REV: fab
release PRODUCTION/YYYYMMDD-REV` (with `; variants A, B` for a board
with `variants`).  It does not push the tag: it prints
the command, `git push origin refs/tags/NAME-YYYYMMDD-REV`.  Run that
only when the owner says to.

- A build from uncommitted sources (`--allow-dirty`) is not tagged.
- A rebuild (`--force`) of the commit the tag is on leaves the tag.
- A tag already on another commit stops it before anything is replaced,
  saying why: HEAD has moved since the release was built, so the
  rebuilt package would not match its tag.  Committing the production
  directory moves HEAD, so rebuild (`--force`) before committing it;
  after, rebuild with `--retag`, which moves the tag (the printed push
  then has `--force`), or check out the tagged commit.  Once the order
  is placed, the release is fixed: don't move its tag.

## Before running

1. The board's title-block revision is the release's REV: bump it in the
   board and schematic first if this is a new revision.
2. Set the board's stackup in Board Setup, Physical Stackup, if it has
   none: the release takes the copper layers from it, and refuses a
   board without one.
3. Run `stackup-table` on the board (close it in KiCad first) if its
   stackup table is missing or out of date: the release refuses it.
4. Commit the board and its sources.

## What it writes

`PRODUCTION/YYYYMMDD-REV/`, NAME being the KiCad project.  With
`variants`, the BOMs, CPL, ODB++ and IPC-2581 are per variant,
`NAME-REV-VARIANT-...`, and the Gerber zip too where the silkscreens
differ (Design variants):

| File | Use |
|---|---|
| `NAME-REV-gerbers-jlcpcb.zip` | the PCB order: Gerbers (each copper layer, mask, paste, silk, Edge.Cuts, User.Drawings with the stackup table) and Excellon drill in mm, in the profile's options (`jlcpcb.md`), and the Gerber job file; `README.txt`, the Gerber map; `stackup.txt` and `fab-notes.txt`, the stackup and the fabrication notes, the notes naming the documents by their names in the zip.  `NAME-REV-gerbers.zip` for a profile with kicad-cli's own Gerber options |
| `NAME-REV-gerber-map.txt` | the Gerber map, as in the zip: each file's name, KiCad layer, function (copper L1 top to Ln bottom, mask, paste, silkscreen, drawings, outline, plated and non-plated drill), side and polarity, from KiCad's Gerber job file and the drill files' X2 comments |
| `NAME-REV-stackup.txt` | the board's stackup (Board Setup, Physical Stackup): each layer's and dielectric sublayer's type, material, thickness, Er, loss tangent, color and Gerber; total thickness and the board's thickness setting; copper finish, impedance-controlled dielectric, edge connector, castellated pads, edge plating; then the impedance requirements, from net classes whose names say impedance (`[TBD: ...]` for a name not `<ohms>Z_DIFF[_<INTERFACE>]` or `<ohms>Z_SE`). |
| `NAME-REV-fab-notes.txt` | numbered fabrication notes: generic (IPC-6012 and IPC-A-600, plating, LPI mask per IPC-SM-840, finished hole sizes, Gerbers not to be modified, no added tooling holes or marking), filled from `[board.fab]`: the IPC class, base material, minimum Tg and electrical test; from the board: layer count, finished thickness, copper weights, finish and mask and silk colors from the stackup; impedance control from the net classes; minimum track and space from KiCad's Gerber job file, minimum drill from the drill files.  `[TBD: ...]` marks what neither can say (anything the stackup lacks): fill it in before sending the notes |
| `NAME-REV-odb.tgz` | ODB++ job (one `odb/` directory: `matrix/`, `steps/`, ...), mm, 6 decimals: for the record and for ODB++ tools such as `tools/odb_current`; not the order's upload |
| `NAME-REV-ipc2581.zip` | IPC-2581 revision C, mm, 6 decimals: a zip of one XML file, `NAME-REV-ipc2581.xml`; its BOM from MPN, Manufacturer and the profile's distributor field: for the record and for IPC-2581 tools; not the order's upload |
| `NAME-REV-bom.csv` | the house BOM, fab-neutral, for the record and for buying parts: Reference, Value, Footprint (LIBRARY:NAME), Qty, DNP, LCSC, MPN, Manufacturer, Datasheet.  Fitted parts grouped on every column but Reference and Qty; each DNP part a row of its own, Qty 0, DNP `yes`; a part excluded from the BOM has no row.  Not an assembly order's upload |
| `NAME-REV-jlcpcb-bom.csv`, `NAME-REV-jlcpcb-cpl.csv` | the profile's assembly BOM and placement (`jlcpcb.md`) |
| `NAME-REV-source.tgz` | the design files, `sources`, schematic and board PDFs (the board's a page per copper layer, silkscreen, Fab layer and User.Drawings), and COMMIT |

Commit the production directory after the order is placed, and do not
change it afterwards.
