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
Each board's `production` is its own: two boards' the same, or one
inside the other, is an error naming both (a release's directory,
`YYYYMMDD-REV`, does not name its board), and `--force` replaces a
release only if every file in it is the board's (`NAME-REV-*`).  A
`production` is inside the repository and neither the same as nor
holding a board's `directory` or a `sources` path; inside a board's
directory is allowed.
`adopt` writes `production` for a lone board and `production/<name>` for
each of several.  There is no `copper` key: the copper layers, top to
bottom, are the stackup's.  Each `sources` path is inside the repository: one that
is absolute or leads out of it (`..`) is an error naming it, and one
that is, or goes through, a symbolic link is refused, naming the link
(the tgz would hold the bare link): name the link's target.  A
`fab.toml` that does not parse, a board name not in it, a file it names
that is not there, or a `[board.<profile>]` the profile refuses stops
the release, naming it.

## Design variants

KiCad 10's design variants: one schematic and board, several builds
differing in which parts are fitted and in their fields.  Without
`variants` (or with `[]`), the release builds KiCad's default variant.
With it, it builds each variant listed, and not the default: one
release, one production directory, one tag, whose message names the
variants.

- A name is letters, digits, `.`, `_` and `-`, not starting with `-`;
  names are unique ignoring case.  Otherwise an error naming it.  Two
  variants whose files would share a name (`a` and `a-jlcpcb`:
  `NAME-REV-a-jlcpcb-bom.csv`) are refused, naming both, before
  anything is written.
- A listed variant the project lacks is an error, before anything
  runs.  The project's variants are those its `.kicad_pro` lists (where
  KiCad's GUI keeps them), the board's top-level `(variants ...)`, and
  each name a footprint's or a schematic symbol's variant override
  uses: `kicad-cli` takes an unknown `--variant` as the
  default.  A variant of the project (its `.kicad_pro`'s, the board's,
  or one an override names) not listed is a warning naming it, as is a
  project with variants and none listed.
- Per variant, with `kicad-cli ... --variant`: the BOM and placement
  exports, so the house BOM, the profile's BOM and CPL, `require_lcsc`
  and every other profile check; IPC-2581, ODB++ and the schematic and
  board PDFs (in the source tgz, `pdf/NAME-VARIANT-schematic.pdf` and
  `pdf/NAME-VARIANT-board.pdf`).
- Each build's schematic and board compared (the default build's too),
  as DRC's schematic parity does not compare variants: a part DNP in the
  build's BOM and
  fitted on its board (a placement export with `--exclude-dnp` and one
  without), or the reverse, a different Value, or a different footprint,
  `LIBRARY:NAME` against the board file's (a variant's Footprint
  override: one board has one footprint per reference) refuses the release, naming the variant and the part.  Run
  Update PCB from Schematic.  Whether a part is fitted is KiCad's DNP
  attribute (a BOM export with `--exclude-dnp`), never a field named DNP.
- Each variant's Gerbers are exported and compared with the first's,
  less comment lines and dates.  The same: one Gerber zip, as without
  variants.  Different only in F.Silkscreen, B.Silkscreen or
  User.Drawings (`${VARIANT}` on the silkscreen): a Gerber zip per
  variant, `NAME-REV-VARIANT-gerbers-jlcpcb.zip`, and no shared one,
  said on stderr: each is a bare-board order of its own.  Different
  anywhere else (copper, mask, paste, Edge.Cuts, the drill, the job
  file): refused, naming the variant and the layer.  The drill export
  takes no variant.
- `${VARIANT}` and `${VARIANT_DESC}` take each variant's value in
  `kicad-cli`'s exports (10.0.8) only when the board's own top-level
  `(variants (variant (name ...)) ...)` names the variant; with the
  variants only in the `.kicad_pro` (where KiCad's GUI keeps them),
  every variant's Gerbers get the same text.  So a release with
  `variants` warns, naming the layers and texts, when such a text is on
  a layer it exports (copper, mask, paste, silkscreen, Edge.Cuts,
  User.Drawings, Fab) and the board's list lacks a listed variant: add
  the variants to the board's list (pcbnew's `AddVariant`, then save).
  A warning only: the release is made as without it.
- Built once: the Gerber map (its title naming each zip), the stackup,
  the fabrication notes and the source tgz.

## The source tgz

`NAME-REV-source.tgz` holds the files git tracks (submodules' too) of
the project's `.kicad_pcb`, `.kicad_sch`, `.kicad_pro`, `.kicad_dru`,
`.pretty`, `.3dshapes`, `fp-lib-table` and `sym-lib-table`, every sheet
file of the schematic's hierarchy, and `sources`; then the schematic and
board PDFs (`pdf/`) and `COMMIT` (the commit, `-dirty` if built from
uncommitted sources, and when it was built).  It holds each file once,
and never an untracked or ignored file (KiCad's backups, a venv), `.git`
or a board's production directory, wherever they fall under a source
(`sources = ["."]`).

A library the board reads (footprints, symbols, 3D models) lives inside
the repository, normally a git submodule (`docs/kicad-style.md`,
Files); a clone beside the repository is not supported.  The archive
holds a library only when it is one of `sources`, apart from the
project's own `NAME.pretty` and `NAME.3dshapes`, which it holds always.  The board file embeds
each footprint and the schematic each symbol, so the archive rebuilds
the board and its outputs; the 3D models of a library not in `sources`,
and of KiCad's stock libraries, are not in it.  The release refuses, before
anything runs, a path in the project's `fp-lib-table` or
`sym-lib-table`, or a footprint's 3D model path, that leads outside the
repository (links followed) or uses a variable other than `${KIPRJMOD}`
and KiCad's stock library variables (`${KICADn_FOOTPRINT_DIR}`,
`${KICADn_SYMBOL_DIR}`, `${KICADn_3DMODEL_DIR}`, `${KISYSMOD}`,
`${KISYS3DMOD}`), or a `$(NAME)` or `~` path, naming each with its
fix: a library table's path is pointed at the library once it is in the
repository; a 3D model's path relative to its footprint's library needs
no change once that library is in; another model's is renamed
`${KIPRJMOD}`-relative.  A relative 3D model path is looked
up as KiCad does, from the project's directory, then from the
footprint's library's directory (`fp-lib-table`); one found nowhere is a
warning naming the footprint and the path.

It stops, before anything runs, on a sheet file the schematic names that
is not there, or is outside the repository, naming it and the sheet
that names it: `kicad-cli` would take it as an empty sheet, its parts
gone from ERC, parity and every BOM.  It stops unless the board has a stackup and a stackup table on
User.Drawings matching it (kicad-style-check's rules, waivers not
honored: `stackup-table` writes or refreshes the table), ERC is clean,
DRC (with schematic parity, the profile's rules and Board Setup
minimums, on a scratch copy of the project: `docs/kicad-rules.md`) is
clean, kicad-cli having read the custom rules (a rule that does not
parse makes it drop them all, silently: the release checks with a
canary rule, and stops naming the board's `.kicad_dru`),
what it reads has no uncommitted changes: `fab.toml`, the board's
directory, its sheets, `sources`, each library its tables and 3D model
paths name (in `sources` or not), and the ee_tooling it runs from when
the repository holds it as a submodule or tracked files (its
`parts.toml`, its rules; not a package installed in a venv).  Every
board's production directory is left out (as written and through a
link), so a rebuild with `--force` over an uncommitted release goes on.
An untracked file under the board's directory or a source counts as
uncommitted; an ignored one there does not (it is not in the tgz), but
an ignored file the release reads by name (`fab.toml`, the project's own
files, a sheet, a library table, a library) does.  A path is asked of the
repository that holds it, and each submodule holding it, at every
level, by its gitlink in its parent: a submodule moved to another
commit, or with modified or untracked content, counts, whatever any
`ignore` setting or the user's git configuration.  A link is checked as
itself and as its target; one leading out of the repository is
refused.  None of them holds an unfetched Git LFS pointer,
(`--allow-dirty` only for a trial; the tgz's COMMIT file then says
`-dirty`), and the profile's own checks pass (`jlcpcb.md`), all before
anything is written.

Before any of those, once for every variant, it stops on a Git LFS
pointer among its inputs: first the board, schematic and project files,
before it reads them, then what they name and the source tgz's files,
directories walked, but `.git` and every production directory (as the
tgz).  A pointer is a non-empty file under 1024 bytes whose first line
is git-lfs's `version https://git-lfs.github.com/spec/v1` (or the older
`version https://hawser.github.com/spec/v1`), as a clone without
git-lfs leaves it.  It names each; `--force` and `--allow-dirty` do not
pass it.  The fix, printed for each repository the pointers are in (the
board repository, a library's submodule): install git-lfs, then
`git [-C SUBMODULE] lfs install --local --skip-repo` and
`git [-C SUBMODULE] lfs pull`.

It warns, naming `adopt --yes`, when the board's `.kicad_dru` lacks the
profile's current section or its Board Setup is below the profile's
minimums, and names each rule below the end marker that copies the
profile's: KiCad's own DRC in the editor then differs from the
release's.

## The tag

It reads HEAD's hash as it begins, before it reads any input, and runs
the uncommitted-sources check before it copies any (HEAD must not have
moved by then); the tgz's COMMIT, the tag and the printed line all name
that commit.  After
the last export it reads HEAD and the check again: a commit, or a change
to what the release reads, made while it ran stops it, the old release
and tag left as they were.  It tags that commit (the tgz's COMMIT),
annotated, `NAME-YYYYMMDD-REV`, with the message `NAME REV: fab
release PRODUCTION/YYYYMMDD-REV` (with `; variants A, B` for a board
with `variants`).  It does not push the tag: it prints
the command, `git push origin refs/tags/NAME-YYYYMMDD-REV` (the
reference shell-quoted where it needs it).  Run that
only when the owner says to.

- A build from uncommitted sources (`--allow-dirty`) is not tagged, and
  never replaces a release that has its tag, `--retag` or not: the tag
  would name a package it does not describe.  Build such a trial under
  another `--date`.
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
   board and schematic first if this is a new revision.  A text variable
   in it (`${REVISION_NUMBER}`) takes its value from the project's text
   variables (Project Setup, Text Variables), as KiCad's title block
   shows it; one the project does not define stops the release, as does
   a title block with no rev ("no revision in NAME.kicad_pcb's title
   block").  `YYYYMMDD-REV`
   must be one path component and `NAME-YYYYMMDD-REV` a git tag name
   (`git check-ref-format`: no spaces, `..`, `~`, `^`, `:`, ...): the
   release refuses one that is not, before anything runs.
2. Set the board's stackup in Board Setup, Physical Stackup, if it has
   none: the release takes the copper layers from it, and refuses a
   board without one.
3. Run `uv run stackup-table PATH/TO/NAME.kicad_pcb` (from the hardware
   tools' uv project, `hardware/` or the repository root; close the
   board in KiCad first) if its stackup table is missing or out of date:
   the release refuses it, printing the command that runs from where it
   ran, `uv run --project PROJECT stackup-table PATH`.
4. Commit the board and its sources.

## What it writes

`PRODUCTION/YYYYMMDD-REV/`, NAME being the KiCad project.  `kicad-cli`
reads scratch copies of the project, never the board's own files (it
writes `NAME.kicad_prl` beside what it reads).  
A run holds the lock `PRODUCTION/.YYYYMMDD-REV.lock/` (naming its host
and process) from before it writes anything until its tag is made and
the old release removed: another run's lock stops it; a lock whose
process is gone from this host (a killed run's) is taken over, said on
stderr; one from another host is left for you to remove (the command is
printed).  The release is built in `PRODUCTION/.YYYYMMDD-REV.building/`;
after the last export the old release is moved aside to
`.YYYYMMDD-REV.old/`, the new one put in its place, the tag made or
moved, then the old one removed.  Each hidden directory holds a
`.gitignore` of everything, so `git add -A` takes none of them.

The invariant: the release directory holds the package the tag names,
or, between the moves, that package is in `.YYYYMMDD-REV.old/`; the
package a tag names is never removed.  A failure, `git tag`'s too, puts
the old release back.  A killed run's leftovers are recovered by the
next run, under the lock, before anything else, each step said on
stderr: the building directory removed (never tagged); `.old` with no
release in place put back; `.old` beside a release, the one whose
COMMIT is the tag's commit kept and the other removed, or, neither
named by the tag, the old one kept (a fresh release killed before its
tag is left in place, untagged).  With
`variants`, the BOMs, CPL, ODB++ and IPC-2581 are per variant,
`NAME-REV-VARIANT-...`, and the Gerber zip too where the silkscreens
differ (Design variants):

| File | Use |
|---|---|
| `NAME-REV-gerbers-jlcpcb.zip` | the PCB order: Gerbers (each copper layer, mask, paste, silk, Edge.Cuts, User.Drawings with the stackup table) and Excellon drill in mm, in the profile's options (`jlcpcb.md`), and the Gerber job file; `README.txt`, the Gerber map; `stackup.txt` and `fab-notes.txt`, the stackup and the fabrication notes, the notes naming the documents by their names in the zip.  `NAME-REV-gerbers.zip` for a profile with kicad-cli's own Gerber options |
| `NAME-REV-gerber-map.txt` | the Gerber map, as in the zip: each file's name, KiCad layer, function (copper L1 top to Ln bottom, mask, paste, silkscreen, drawings, outline, plated and non-plated drill), side and polarity, from KiCad's Gerber job file and the drill files' X2 comments |
| `NAME-REV-stackup.txt` | the board's stackup (Board Setup, Physical Stackup): each layer's and dielectric sublayer's type, material, thickness, Er, loss tangent, color and Gerber; total thickness and the board's thickness setting; copper finish, impedance-controlled dielectric, edge connector, castellated pads, edge plating; then the impedance requirements, from net classes whose names say impedance (`[TBD: ...]` for a name not `<ohms>Z_DIFF[_<INTERFACE>]` or `<ohms>Z_SE`). |
| `NAME-REV-fab-notes.txt` | numbered fabrication notes: generic (IPC-6012 and IPC-A-600, plating, LPI mask per IPC-SM-840, finished hole sizes, Gerbers not to be modified, no added tooling holes or marking), filled from `[board.fab]`: the IPC class, base material, minimum Tg and electrical test; from the board: layer count, finished thickness, copper weights, finish and mask and silk colors from the stackup; impedance control from the net classes; minimum track and space from KiCad's Gerber job file, minimum drill from the drill files.  `[TBD: ...]` marks what neither can say (anything the stackup lacks), each said on stderr with what sets it (a color or finish in Board Setup, Physical Stackup): set it and build again, or fill it in before sending the notes |
| `NAME-REV-odb.tgz` | ODB++ job (one `odb/` directory: `matrix/`, `steps/`, ...), mm, 6 decimals: for the record and for ODB++ tools such as `tools/odb_current`; not the order's upload |
| `NAME-REV-ipc2581.zip` | IPC-2581 revision C, mm, 6 decimals: a zip of one XML file, `NAME-REV-ipc2581.xml`; its BOM from MPN, Manufacturer and the profile's distributor field: for the record and for IPC-2581 tools; not the order's upload |
| `NAME-REV-bom.csv` | the house BOM, fab-neutral, for the record and for buying parts: Reference, Value, Footprint (LIBRARY:NAME), Qty, DNP, LCSC, MPN, Manufacturer, Datasheet.  Fitted parts grouped on every column but Reference and Qty, their DNP cell empty; each DNP part a row of its own, Qty 0, DNP `yes`; a part excluded from the BOM has no row.  Not an assembly order's upload |
| `NAME-REV-jlcpcb-bom.csv`, `NAME-REV-jlcpcb-cpl.csv` | the profile's assembly BOM and placement (`jlcpcb.md`): the BOM and CPL to upload for JLCPCB's assembly, not the house BOM |
| `NAME-REV-source.tgz` | the design files, `sources`, schematic and board PDFs (the board's a page per copper layer, silkscreen, Fab layer and User.Drawings), and COMMIT |

Commit the production directory after the order is placed, and do not
change it afterwards.
