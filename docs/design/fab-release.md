# Design: fab-release

A proposal, for review before any code.  It replaces `jlcpcb-release`
and `jlcpcb.toml` with a fab-neutral `fab-release` and `fab.toml`, with
JLCPCB as one profile.  Decided in review: the names below, a neutral
core with profiles, required per-board fab values, the house BOM in the
core, rules added by the release (not refused when stale), no `kicad`
profile yet, no alias for the old command, a flag day, DNP parts at
Qty 0 in the house BOM, and a fitted part with no LCSC number a warning
unless the board requires LCSC numbers.  Decided, in the flag day:
KiCad 10 design variants, an optional `variants` list per board, with a
BOM, CPL, IPC-2581 and ODB++ per variant and the Gerbers and fab
documents shared; a Gerber zip per variant when the variants'
silkscreens differ; a variant that differs in copper, mask, paste,
outline or drill, or that overrides a part's Footprint field, refused.
A library a board reads lives inside its repository (house style); the
source tgz holds only `sources` beside the project's files; `adopt` skips production and hidden directories when finding
projects; Git LFS (`docs/design/git-lfs.md`) lands in the same merge.
Deferred: the fab notes and the no-extra-cost via rules.  Nothing
is open.

## Names

| Now | After | Holds |
|---|---|---|
| `src/ee_tooling/jlcpcb_release.py` | `src/ee_tooling/fab_release.py` | the neutral core and `main()` |
| - | `src/ee_tooling/profiles/__init__.py` | `PROFILES` (name to the profile's constructor) and `DEFAULT` |
| - | `src/ee_tooling/profiles/profile.py` | `GerberOptions`, `Part`, `Placement` and the `Profile` Protocol; imports nothing from ee_tooling |
| - | `src/ee_tooling/profiles/jlcpcb.py` | the JLCPCB profile: `Corrections` (`parts.toml`'s), the BOM and CPL writers, `Jlcpcb` with its Gerber options and rules |
| script `jlcpcb-release` | script `fab-release` | no alias |
| `jlcpcb.toml` | `fab.toml` | boards, each with its `profile` |
| `skills/jlcpcb-release/` | `skills/fab-release/SKILL.md`, `skills/fab-release/jlcpcb.md` | the neutral skill; JLCPCB's ordering and rotation steps |
| `parts.toml`, `rules/jlcpcb*` | unchanged | data the profile names |

- `fab_release.py` imports `PROFILES` and `profile.py`, never a
  profile's module.
- `DEFAULT` is the profile of the boards in `jlcpcb.toml`; it goes
  when `fab.toml`'s required `profile` key arrives (step 5).
- `fab_release.py`, as `kicad_style_check.py` is its script's name;
  "release" alone also means ee_tooling's own `vX.Y.Z` releases.
- One `profile` per board: a project has one `.kicad_dru`, so it meets
  one fab's rules at a time.

## fab.toml

```toml
[[board]]
name = "widget"                  # the KiCad project
directory = "hardware/widget"    # holds widget.kicad_pcb, .kicad_sch, .kicad_pro
production = "production"        # where YYYYMMDD-REV/ goes
sources = ["docs"]               # added to the source tgz
profile = "jlcpcb"               # a key of PROFILES

[board.fab]                      # every key required
ipc_class = 2                    # IPC-A-600 / IPC-6012 class
base_material = "FR-4"
tg = 130                         # deg C, minimum
electrical_test = "100% netlist test per IPC-9252"
```

- The first board is the default, as now.
- `copper` is gone: the copper layers come from the board's stackup.
- An unknown key, at any level, is an error naming it, so later keys
  (`assembly`, `kind = "panel"`) are additive.
- Only the table named by `profile` is allowed beside `fab`.
- Each board's `production` is its own: two boards' the same, or one
  inside the other, is an error naming both, as a release's directory,
  `DATE-REV`, does not name its board.  `--force` removes an old release
  only if every file in it is the board's (`NAME-REV-*`).
- A `production` is inside the repository, and neither the same as nor
  holding any board's `directory` or `sources` path (the
  uncommitted-sources check leaves every production directory out);
  inside a board's directory is allowed (the repository-root layout).
  Every git call of the check stops the release on a failure.
- `DATE-REV` (`--date` and the title block's rev) is one path
  component, and the tag `NAME-DATE-REV` a name `git check-ref-format`
  takes, or the release stops before anything runs, naming the value
  and where it comes from.
- A release is built in `PRODUCTION/.DATE-REV.building/` (hidden: the
  uncommitted-sources check and `adopt`'s project search skip it) and
  replaces the old one (`--force`) only after the last export; a failure
  removes the building directory and leaves the old release and its tag.
- Each `sources` path is inside the repository; one that is absolute or
  leads out of it (`..`) is an error naming it.
- House values for `[board.fab]` are written by `adopt` and the setup
  tool, never applied silently by `fab-release`.

## The split

### Neutral core (`fab_release.py`, `fabrication.py`)

- Config, `Board`, `--date`, `--force`, `--allow-dirty`, `--retag`, and
  the release's tag (`NAME-YYYYMMDD-REV`).
- Checks, before anything is written: ERC; DRC with schematic parity
  and the profile's rules (below); committed sources and libraries (each
  path the project's library tables and 3D models name inside the
  repository, a submodule's with `git status --ignore-submodules=none`,
  which reports a submodule's modified or untracked content and new
  commits whatever its `ignore` setting, and never a path inside it);
  the same paths free of unfetched Git LFS pointers; a stackup; the
  stackup table on User.Drawings matching it (kicad-style-check's rule);
  every `[board.fab]` value; the profile's options and its `check` hook.
- Exports: Gerbers (copper, mask, paste, silk, Edge.Cuts and
  User.Drawings), drill, the Gerber job file and map, ODB++
  (`NAME-REV-odb.tgz`), IPC-2581 revision C (`NAME-REV-ipc2581.zip`),
  `stackup.txt`, `fab-notes.txt`, the schematic and board PDFs (the
  board's with User.Drawings), and the source tgz.
- The house BOM, `NAME-REV-bom.csv`: Reference, Value, Footprint, Qty,
  DNP, LCSC, MPN, Manufacturer, Datasheet.  A DNP part is a row of its
  own with Qty 0 and DNP `yes`, never grouped with fitted parts.
- One ungrouped BOM export and one placement export, parsed into `Part`
  and `Placement` rows for the house BOM and the profile.  BOMs are
  grouped in Python on the resolved field values: `kicad-cli`'s
  `--group-by` groups on the fields' text before `${...}` text
  variables are resolved.
- The source tgz: the project's `.kicad_pcb`, `.kicad_sch`,
  `.kicad_pro`, `.kicad_dru`, `.pretty`, `.3dshapes`, `fp-lib-table` and
  `sym-lib-table` that exist, every sheet file of the schematic's
  hierarchy inside the repository (in the uncommitted-sources check
  too), `sources`, the PDFs and `COMMIT`; never
  `.git` or a board's production directory under a source.  A
  library a board reads lives inside the repository, normally a git
  submodule (`docs/kicad-style.md`, Files); a clone beside the
  repository is not supported.  A library is in the archive only as one
  of `sources`.  The board file embeds each footprint and the schematic
  each symbol, so the archive rebuilds the board and its outputs; the 3D
  models of a library not in `sources`, and of KiCad's stock libraries,
  are not in it.
- The release refuses, before anything runs, a path in the project's
  `fp-lib-table` or `sym-lib-table`, or a footprint's 3D model path in
  the board, that resolves outside the repository, naming each as
  written (`project_libraries.py`).  `${KIPRJMOD}`, and a relative path,
  are from the project's directory.  A path under one of KiCad's install
  variables (`${KICAD10_FOOTPRINT_DIR}`, `${KICAD10_SYMBOL_DIR}`,
  `${KICAD10_3DMODEL_DIR}`, their older versions, `${KISYS3DMOD}`) is a
  stock library: allowed, and not in the source tgz.  Any other
  variable (`${KICAD10_3RD_PARTY}`, a user's own) is refused: its value
  is the user's.  An embedded model is not a path.
- A footprint's 3D model path with no variable, relative, is found as
  KiCad 10.0.8 finds it (`kicad-cli pcb export step` on a scratch board:
  a model present only beside the project, or only beside the
  footprint's library, loads; present in both, the project's): from the
  project's directory, else from the directory of the footprint's
  library (its `LIBRARY` of `LIBRARY:NAME` in the project's
  `fp-lib-table`).  `${KIPRJMOD}/...` is not looked up beside the
  library.  Found outside the repository: refused.  Found nowhere (a
  missing model does not change the fabrication files): a warning
  naming the footprint and the path, and the library when the table
  lacks it.  A stock library's footprint's relative model is stock.

### JLCPCB profile (`profiles/jlcpcb.py`)

- Gerber options: Protel extensions, no X2, soldermask subtracted from
  silk; Excellon in mm, PTH and NPTH separate, absolute origin.
- Its Gerber zip, `NAME-REV-gerbers-jlcpcb.zip`: the Gerbers, drill,
  `README.txt` (the map), `stackup.txt` and `fab-notes.txt`.
- Its BOM `NAME-REV-jlcpcb-bom.csv` (Comment, Designator, Footprint,
  LCSC Part #; DNP left out) and CPL `NAME-REV-jlcpcb-cpl.csv`, with
  `parts.toml`'s rotations and offsets.  A fitted part with no LCSC
  number is a warning, naming each one, as now; with the board option
  `require_lcsc = true` in `[board.jlcpcb]` (default false), it refuses
  the release.
- IPC-2581's distributor: the `LCSC` field, distributor "LCSC".
- Rules: `rules/jlcpcb.kicad_dru` in sections (limits, house), and
  `rules/jlcpcb_board_setup.json`, the Board Setup minimums.
- Its `[board.fab]` house values for `adopt`.

## Names of the files

A file whose contents are shaped for one fab carries the profile's
name; a neutral file does not.  The Gerber zip is
`NAME-REV-gerbers-<profile>.zip` when the profile sets fab-specific
Gerber options or contents, `NAME-REV-gerbers.zip` otherwise.

| File | From |
|---|---|
| `NAME-REV-gerbers-jlcpcb.zip` | profile |
| `NAME-REV-gerber-map.txt`, `-stackup.txt`, `-fab-notes.txt` | core |
| `NAME-REV-odb.tgz`, `NAME-REV-ipc2581.zip` | core |
| `NAME-REV-bom.csv` | core: the house BOM |
| `NAME-REV-jlcpcb-bom.csv`, `NAME-REV-jlcpcb-cpl.csv` | profile |
| `NAME-REV-source.tgz` | core |

With design variants (below), the files that differ by variant carry
it after REV; the others are built once and shared:

| File | Built |
|---|---|
| `NAME-REV-gerbers-jlcpcb.zip` | once, when every variant's Gerbers are the same |
| `NAME-REV-VARIANT-gerbers-jlcpcb.zip` | per variant, in its place, when the silkscreens differ |
| `NAME-REV-gerber-map.txt`, `-stackup.txt`, `-fab-notes.txt` | once |
| `NAME-REV-VARIANT-bom.csv` | per variant |
| `NAME-REV-VARIANT-jlcpcb-bom.csv`, `NAME-REV-VARIANT-jlcpcb-cpl.csv` | per variant |
| `NAME-REV-VARIANT-odb.tgz`, `NAME-REV-VARIANT-ipc2581.zip` | per variant |
| `NAME-REV-source.tgz` | once; its `pdf/` holds `NAME-VARIANT-schematic.pdf` and `NAME-VARIANT-board.pdf` per variant |

## The profile interface

Internal, not public API.  Data where data will do; hooks only where a
fab's output is code.

```python
@dataclass(frozen=True)
class GerberOptions:
    """kicad-cli's Gerber and drill export, as a fab reads it."""

    x2: bool  # X2 attributes
    protel_extensions: bool  # .gtl ..., or KiCad's .gbr
    subtract_soldermask: bool
    separate_drill: bool  # PTH and NPTH in separate files


@dataclass(frozen=True)
class Rules:
    """A profile's KiCad rules, package resources."""

    dru: str  # its .kicad_dru, sections headed "# --- <name> ---"
    optional: dict[
        str, str
    ]  # an option in [board.<profile>] to the section it leaves out when false
    board_setup: str  # its Board Setup minimums, a .kicad_pro fragment


class Profile(Protocol):
    name: str  # fab.toml's profile value; names its files
    gerbers: GerberOptions  # its Gerber and drill export
    rules: Rules
    distributor: tuple[str, str] | None  # IPC-2581: the part-number field, the distributor
    fab_defaults: dict[str, Any]  # [board.fab] values adopt writes

    def options(self, table: dict[str, Any]) -> Any:
        ...
        # checks [board.<name>]; raises ValueError naming a bad key

    def check(self, parts: list[Part], placements: list[Placement], options: Any) -> list[str]:
        ...
        # refusals beyond the core's, before anything is written

    def outputs(self, base: str) -> list[str]:
        ...
        # the names of the files assembly writes

    def assembly(
        self,
        dest: Path,
        base: str,
        parts: list[Part],
        placements: list[Placement],
        options: Any,
    ) -> list[Path]:
        ...
        # writes its BOM and CPL into dest, named by base; returns them
```

- `base` is the file stem, `NAME-REV`, or `NAME-REV-VARIANT` for a
  variant; the core calls `check` and `assembly` once per build.
  `options` is what `options` returned for the board (`require_lcsc`).
  `check` takes the rows, not the release: `profile.py` imports nothing
  from ee_tooling.
- `PROFILES` maps a profile's name to its constructor, not to an
  instance.  The release builds the profile before anything else, so a
  bad `parts.toml` stops the run before `--force` removes an old
  release.

- The PCB half (`gerbers`, `rules`) and the assembly half (`assembly`,
  `distributor`) are separate, so an `assembly = "<profile>"` key can
  later name a different assembler.

## Rules

- `fab-release` runs DRC on a scratch copy of the project whose
  `.kicad_dru` is the profile's rules, then the board's own rules,
  which win.  So a release is always
  checked against the current rules, and a change to ee_tooling's rules
  never blocks a release just for being newer.  A rule a board breaks
  fails DRC as any other.
- It checks Board Setup against the profile's minimums the same way, on
  the scratch copy, raised to at least the profile's values.
- It never writes the board's files.  The scratch copy is the project's
  `.kicad_pcb`, `.kicad_sch` and `.kicad_prl` copied (a link would let
  anything KiCad writes beside them reach the board's), its `.kicad_dru`
  and `.kicad_pro` written, `fp-info-cache` left out, and every other
  entry of its directory linked, never read (an odd entry, a broken
  link or a FIFO, is harmless; an unreadable board or schematic stops
  the release, naming it), at the directory's path from
  the repository's root under the scratch directory, beside a link to
  every other entry of each of its ancestors up to the root:
  `kicad-cli pcb drc` checks footprints against their libraries
  (10.0.8: `lib_footprint_issues` for a library not found), so a library
  path relative to the project (`${KIPRJMOD}/../lib`) must find what it
  finds for the board.  A library is inside the repository (refused
  otherwise), so nothing above the root is needed.
- `jlcpcb.kicad_dru` holds the limits and house sections only: the
  price section's no-extra-cost via rules are deferred, and are not in
  it.
- `adopt --yes` installs the same rules in the board's `.kicad_dru`,
  between markers, with the board's own rules after them, and raises
  Board Setup values in the `.kicad_pro` to at least the profile's,
  never lowering one.  That makes KiCad's own DRC in the editor match
  the release.  `fab-release` warns when the installed section is
  missing or stale, naming `adopt --yes`.

```
(version 1)
# --- ee_tooling: jlcpcb rules; written by adopt ---
<the profile's rules, without their (version 1)>
# --- ee_tooling: end; the board's own rules follow and win ---
<the board's own rules>
```

## Design variants

KiCad 10 design variants: one schematic and one board, several builds
that differ in which parts are fitted and in their fields.  A board
with no `variants` key releases as above, file for file.

```toml
[[board]]
name = "widget"
# ...
variants = ["lite", "full"]      # KiCad 10 design variants; default []
```

- `variants`: optional, a list of the project's variant names, each a
  build.  Absent or `[]`: one build, KiCad's default variant, and the
  names without VARIANT.  Present: one build per name, and the default
  variant is not built.
- A name is letters, digits, `.`, `_` and `-`, not starting with `-`;
  names are unique ignoring case, as in KiCad.  Otherwise an error
  naming it.
- The project's variants are every name KiCad 10.0.8 records one by:
  the `.kicad_pro`'s list (`schematic.variants`, `[{"name": "X", ...}]`:
  where KiCad's GUI keeps them, with no list in the board), the board's
  top-level `(variants (variant (name "X")))` (pcbnew's `AddVariant`
  writes one), and each name a footprint's `(variant (name "X") ...)`
  or a schematic symbol instance's `(instances (project (path (variant
  (name "X") ...))))` override uses: `kicad-cli --variant X` honors an
  override whose name no list holds.
- A name in `variants` that the project lacks is an error, before
  anything is written: `kicad-cli` given an unknown `--variant` exports
  the default variant and exits 0.
- Two listed variants whose files would share a name (`a` and
  `a-jlcpcb`: `NAME-REV-a-jlcpcb-bom.csv`, each profile naming its own
  files by `outputs(base)`) are refused, naming both, before anything is
  written.
- A project variant not in `variants` is a warning naming it, as is a
  project with variants and no `variants` key.
- One release builds every listed variant: one commit, one production
  directory `YYYYMMDD-REV/`, one tag `NAME-YYYYMMDD-REV`, whose
  message names the variants.  No option builds a subset.

What a variant can override (KiCad 10.0.8), and what the release does
with it:

| Override | Schematic, in the instance's `(variant)` | Board, in the footprint's `(variant)` | The release |
|---|---|---|---|
| Do not populate | `(dnp yes)` | `(dnp yes)` | honored: a Qty 0 row in the house BOM; left out of JLCPCB's BOM and CPL |
| Exclude from BOM | `(in_bom no)` | `(exclude_from_bom yes)` | honored: no row in any BOM |
| Exclude from position files | `(in_pos_files no)` | `(exclude_from_pos_files yes)` | honored by the placement export; a part in the BOM and not placed is refused, as now |
| Exclude from board | `(on_board no)` | - | as without variants |
| Exclude from simulation | `(exclude_from_sim yes)` | - | ignored |
| A field's value (Value, LCSC, MPN, ...) | `(field (name "F") (value "V"))` | likewise | honored: the BOMs, the CPL's Val, `require_lcsc`, IPC-2581 and ODB++ take the variant's |
| Footprint field | likewise | - | refused: one board has one footprint per reference |

Built per variant, each with `--variant VARIANT`, one `kicad-cli` call
per variant (`pcb export pos` with several `--variant` and `${VARIANT}`
in the path writes one file, named with `${VARIANT}` literally):

- The BOM export (with the `DNP` field) and the placement export (with
  `--exclude-dnp`), parsed into `Part` and `Placement` rows; from them
  the house BOM and the profile's `assembly`.
- IPC-2581 and ODB++: their component data differ by variant (Value,
  `populate="false"`, `.no_pop`).
- The schematic and board PDFs, in the source tgz.

Built once, shared by the variants: the Gerber map, `stackup.txt`,
`fab-notes.txt` and the source tgz.  They depend on the files' names,
the stackup, the Gerber job's design rules and the drill, which do not
differ by variant.  With a zip per variant, the map's title names the
files in each `NAME-REV-VARIANT-gerbers-jlcpcb.zip`.

The Gerbers and drill of every variant are exported, the Gerbers with
`--variant` (`pcb export drill` takes none in 10.0.8), and compared file
by file, less their comment lines and the job file's creation date:

- All the same: one Gerber zip, `NAME-REV-gerbers-jlcpcb.zip`, as
  without variants.
- Different only in F.Silkscreen, B.Silkscreen or User.Drawings: a
  Gerber zip per variant, `NAME-REV-VARIANT-gerbers-jlcpcb.zip`, and no
  shared one.  Each holds that variant's Gerbers, the drill, the Gerber
  job file, `README.txt` (the map), `stackup.txt` and `fab-notes.txt`.
  The release names the layers that differ on stderr.  Silkscreen text
  can differ by variant: `${VARIANT}`, `${VARIANT_DESC}`, or a variant
  field shown on the silkscreen.
- Different in copper, F.Mask, B.Mask, F.Paste, B.Paste, Edge.Cuts or
  the drill: refused, naming the variant and the layer.  A variant does
  not change the bare board's electrical or mechanical content.

In KiCad 10.0.8, a part DNP in a variant changes none of these layers:
its pads keep their copper, mask and paste, so one stencil serves every
variant.  Only F.Fab and B.Fab, not in the Gerber zip, show a variant's
values and, with `--crossout-DNP-footprints-on-fab-layers`, its DNP
parts.  So text is the only way a variant's Gerbers differ, and text
on copper or mask is refused.

With a Gerber zip per variant, each is a separate bare-board order: a
fab makes, panels and charges for each silkscreen apart.  The board
PDF is per variant already; the tag and the production directory are
one per release, as without differing Gerbers.

Checks per variant, beyond the core's:

- A part fitted in the variant's placement export and DNP in its BOM,
  or the reverse, is refused, naming it: the board's variant data is
  not the schematic's.  The board's DNP parts are those in a placement
  export without `--exclude-dnp` and not in the one with it.  DRC's schematic parity does not compare
  variants (10.0.8); Update PCB from Schematic syncs them.
- A part whose Value differs between the two exports is refused, for
  the same reason.
- A part whose BOM Footprint differs from the board's footprint is
  refused (the footprint override above).

Parts are read from `kicad-cli`'s BOM export per variant, never from
the netlist: a netlist's `comp` holds every variant's overrides in its
`(variants (variant (name "X") (property (name "dnp") ...)))` (in XML,
`<variants>`), so a reader of every `property` under `comp` takes a
part DNP in one variant as DNP in all.  `review_netlist.py` reads only
a `comp`'s direct children, which hold the default variant.

## Fab notes

Deferred: unchanged by this design.  `fab-notes.txt` fills what the
board says and marks the rest `[TBD: ...]`.  Later: whether notes
become groups chosen per board, and whether a value the board and
`[board.fab]` cannot give is an error.

## Setup tools

- `adopt`: creates `fab.toml` for a new repository (one board per
  `*.kicad_pro`, profile `jlcpcb`, the profile's fab values), migrates
  `jlcpcb.toml`, adds missing `[board.fab]` keys, installs the rules
  section and raises Board Setup.  It sets `erc.rule_severities` in the
  `.kicad_pro` from `erc_severities.json`, keeping a board's own
  severity where it is stricter (ignore, warning, error), and
  `schematic.connection_grid_size`.  `adopt --check` fails on a missing
  key.
- `adopt` finds the projects (`*.kicad_pro`) under the hardware
  directory, skipping the submodule, as now, and also every production
  directory (`production/`, which `adopt` assigns, and each `production`
  an existing `jlcpcb.toml` or `fab.toml` names) and every directory
  whose name starts with `.` (`.venv`, `.git`, `.claude/`, whose
  worktrees hold copies of the board's project).  So a project file in
  a release, or in a worktree, is not taken for a board.  It matters
  most with the KiCad project at the repository root (a supported
  layout: `docs/adopting.md`, `test_root_layout`), where the hardware
  directory is the root.  Today's `adopt.py` skips only the submodule.
- `fab-setup`: adds a board or sets a value (`fab-setup add NAME
  [--profile P]`, `fab-setup set BOARD KEY VALUE`, `--interactive`),
  editing `fab.toml` with `tomlkit` (a new dependency) to keep its
  comments.

## Migration

A flag day: the breaking commits land on `main` in one merge and are
released as 0.4.0.  The last release before it (`v0.2.0` or any
later release before 0.4.0) is the pin for a board repository not yet
migrated.  A board repository cannot release from the merge until it
runs `adopt --yes`, checks `[board.fab]`, draws the stackup table and
passes DRC under the profile's rules.  For Git LFS
(`docs/design/git-lfs.md`), it also installs git-lfs on each machine
and commits `git add --renormalize .`.

Breaking changes:

1. Script `jlcpcb-release` removed; `fab-release` replaces it.
2. `jlcpcb.toml` replaced by `fab.toml`.
3. `[[board]]` key `copper` removed: present, it is an error.
4. `[[board]]` key `profile` required.
5. `[board.fab]` required, with every key.
6. A `sources` path outside the repository (absolute, or leading out
   of it with `..`) is an error naming it.
7. A library the board reads (a project library table's path, a 3D
   model's) outside the repository, or under a variable not KiCad's
   own, is refused, naming each path as written.
8. Two boards' production directories the same, or one inside the
   other, are an error naming both; `--force` stops on a release
   directory holding a file not the board's (`NAME-REV-*`).
9. A `production` outside the repository, or the same as or holding a
   board's `directory` or a `sources` path, is an error.
10. A title-block rev (or `--date`) that makes the release directory more
    than one path component, or the tag not a git tag name (`Rev A`), is
    refused before anything runs.
11. A board with no stackup is refused (now a warning).
12. A board without a stackup table on User.Drawings matching its stackup
    is refused.
13. DRC runs with the profile's current rules: a board clean today may
    not be.
14. `NAME-REV-bom.csv` becomes the house BOM; JLCPCB's is
    `NAME-REV-jlcpcb-bom.csv`.
15. `NAME-REV-cpl.csv` renamed `NAME-REV-jlcpcb-cpl.csv`.
16. `NAME-REV-jlcpcb.zip` renamed `NAME-REV-gerbers-jlcpcb.zip`, and it
    gains a User.Drawings Gerber, `stackup.txt` and `fab-notes.txt`; the
    board PDF gains User.Drawings.
17. Skill `skills/jlcpcb-release` renamed `skills/fab-release`; a board
    repository's `.claude/skills/jlcpcb-release/` becomes
    `.claude/skills/fab-release/` (`adopt` moves it).
18. Python: `ee_tooling.jlcpcb_release` removed; `Board` and `Release`
    move to `ee_tooling.fab_release`; `Parts`, renamed `Corrections`,
    and the BOM and CPL to `ee_tooling.profiles.jlcpcb`; `Part` and
    `Placement` are in `ee_tooling.profiles.profile`.
19. `adopt --yes` sets the board's ERC severities and connection grid.
20. `adopt` and `adopt --check` require git-lfs.
21. `adopt --check` fails on a plain blob of an LFS-tracked type at
    HEAD: a repository with committed archives, PDFs, STEP or VRML files
    renormalizes and commits.
22. `adopt --yes` writes a managed section of `.gitattributes` and
    `.githooks/pre-push`; a board repository's own `pre-push` is
    replaced.
23. A push without git-lfs is refused.
24. The release refuses a board whose inputs hold an unfetched LFS
    pointer.
25. A board repository's host must support Git LFS: a push to one that
    does not is refused.

`docs/releasing.md`'s "`jlcpcb.toml` key" becomes "`fab.toml` key".

Design variants land in the same merge and are not on this list: the
`variants` key is new and optional, and a board without it gets the
files above unchanged.

## Order of work

One PR, merged as one: the refactor first, with no change in behavior,
then the breaking work.  Each commit passes every check, with its docs.

The refactor:

1. Split `jlcpcb_release.py` into `fab_release.py` and
   `profiles/jlcpcb.py`; same command, same files.
2. `Profile`, `GerberOptions`, `PROFILES`; the core calls the JLCPCB
   profile.  Same files (a test compares a fixture release's).
3. The core parses the BOM and placement exports into `Part` and
   `Placement`; the profile writes from them; `Parts` renamed
   `Corrections`; `profiles/profile.py`.  Same files.
4. `adopt`: one marked-section function for `AGENTS.md` and
   `.gitattributes`; same files.  Built after step 15, as the first of
   the Git LFS commits.

The breaking work:

5. `fab.toml`, `fab-release`, `profile` and its options; unknown keys
   refused; `jlcpcb-release` removed; `DEFAULT` removed.
6. `adopt`: write and migrate `fab.toml`; move the skill; skip the
   production directories and hidden directories when finding projects.
7. `sources` limited to the repository.  Docs:
   `skills/fab-release/SKILL.md` says what the source tgz holds; a
   library lives inside the repository and is in the archive only as one
   of `sources`.
8. Copper layers from the stackup; refuse a board with none.
9. `[board.fab]` required.
10. The house BOM; JLCPCB's files take its name; the Gerber zip's name.
11. Design variants: the `variants` key and its name checks; the
    per-variant exports and files; the Gerbers compared across
    variants, a zip per variant when only silkscreen differs; the
    per-variant cross-checks.
12. User.Drawings in the Gerbers, the map and the board PDF; the
    documents in the Gerber zip.
13. Refuse a board without a matching stackup table.
14. The rules in sections; DRC on a scratch
    copy with them; `adopt` installs them and raises Board Setup; the
    stale warning.
15. `adopt` sets the ERC severities and connection grid.
16. The release refuses an unfetched LFS pointer among its inputs, in
    `fab_release.py`, once, before any variant's build.
17. The managed `.gitattributes` section and `.githooks/pre-push`; a
    board repository's own `pre-push` named, with its text, before it is
    replaced.
18. `adopt` requires git-lfs, installs the local filters and pulls when
    a pointer remains; `--check` runs `git lfs fsck --pointers HEAD`.
19. The CI template installs git-lfs where the runner lacks it;
    `docs/adopting.md`; ee_tooling's `kicad` job fails if the git-lfs
    tests are skipped.
20. Release 0.4.0; tag after CI.

Before the merge: the host's LFS allowances against a board
repository's archives per release (`docs/design/git-lfs.md`, Cost).

Later, not breaking: `fab-setup`; the fab notes; the no-extra-cost via
rules.

## Adversarial review

- Every board repository is blocked at the merge until it is migrated:
  a day's work per board, not a rename.
- The flag day also brings Git LFS: git-lfs on every machine and a
  renormalize commit per board repository.
- `NAME-REV-bom.csv` changing meaning invites uploading the house BOM to
  JLCPCB by habit.
- The Gerber zip's new contents are unchecked against JLCPCB's parser:
  User.Drawings may be taken as a layer, the text files rejected.  A
  quote upload, with no order, checks it before step 12 merges.
- DRC in the editor and in the release differ until `adopt --yes`; the
  release warns.
- Board Setup is raised, never lowered: a board moved to a looser fab
  keeps the stricter values.
- `adopt` editing `.kicad_pro` and `.kicad_dru` while KiCad has them open
  loses one side's edits; it can warn, not detect.
- One implementation of an interface: it will be wrong where a second
  fab differs.  Kept internal, so it is cheap to change.
- The house rules in `jlcpcb.kicad_dru` cite JLCPCB's numbers; a second
  profile needs them as a template.
- Not served, not foreclosed: PCB and assembly from different fabs (an
  `assembly` key later); KiKit panels, which have no schematic (a
  `kind = "panel"` board later).
- Variants: a board with a `variants` key builds no default variant; a
  build of the base design needs a variant of its own in KiCad.
- Variants: a silkscreen marking per variant splits one bare board into
  as many orders as variants, each with its own setup charge and
  minimum quantity; a label or a Fab-layer marking keeps one order.  The
  release says so on stderr, not as an error.
- Variants: a Gerber zip per variant makes uploading the wrong one to
  the fab a new mistake; the zip carries the variant in its name only.
- Variants: comparing every variant's Gerbers costs a Gerber and drill
  export per variant, a few seconds each.
- Variants: the allowed layers rest on KiCad 10.0.8 not changing paste
  or mask for a DNP part; a later KiCad that removes a DNP part's paste
  turns every variant with DNP parts into a refusal, found by the first
  release on it.
- Variants: a footprint swap per variant (a different package in one
  land pattern) is refused; serving it later needs the board's
  footprint checked against each variant's.
- Variants in the flag day: the merge grows by a step, and its first
  user is a board migrating at the same time, so a fault in either is
  found together.
- Variants: listing a variant takes a release's work per variant: a BOM,
  placement, IPC-2581, ODB++, two PDFs and a Gerber comparison each.
  ODB++ and IPC-2581 repeat the copper per variant; the production
  directory grows by about one of each per variant.
- Variants: every listed variant is built at every release, so one
  build's broken data (a missing LCSC number with `require_lcsc`)
  blocks the others; removing it from `variants` is the way out.
- Variants: the cross-checks find a board whose variants were not
  updated from the schematic, which DRC does not; they find DNP and
  Value differences, not other fields'.
- Variants: `kicad-cli` takes an unknown variant name as the default
  without an error; the release's own name check is the only guard,
  and it rests on reading the board's `(variants)`, a KiCad 10 format
  that may change.
- Variants: one tag for all of a release's variants: a variant added at
  the same revision later is a new release, a new date directory and a
  new tag, with every variant rebuilt.
