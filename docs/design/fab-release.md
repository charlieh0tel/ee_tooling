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
The source tgz leaves out libraries outside the repository, and says
so.  Deferred: the fab notes and the no-extra-cost via rules.  Nothing
is open.

## Names

| Now | After | Holds |
|---|---|---|
| `src/ee_tooling/jlcpcb_release.py` | `src/ee_tooling/fab_release.py` | the neutral core and `main()` |
| - | `src/ee_tooling/profiles/__init__.py` | `Profile`, and `PROFILES` (name to profile) |
| - | `src/ee_tooling/profiles/jlcpcb.py` | the JLCPCB profile: BOM, CPL, `Parts`, Gerber options, rules |
| script `jlcpcb-release` | script `fab-release` | no alias |
| `jlcpcb.toml` | `fab.toml` | boards, each with its `profile` |
| `skills/jlcpcb-release/` | `skills/fab-release/SKILL.md`, `skills/fab-release/jlcpcb.md` | the neutral skill; JLCPCB's ordering and rotation steps |
| `parts.toml`, `rules/jlcpcb*` | unchanged | data the profile names |

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
tg = 150                         # deg C, minimum
electrical_test = "100% netlist test per IPC-9252"
```

- The first board is the default, as now.
- `copper` is gone: the copper layers come from the board's stackup.
- An unknown key, at any level, is an error naming it, so later keys
  (`assembly`, `kind = "panel"`) are additive.
- Only the table named by `profile` is allowed beside `fab`.
- Each `sources` path is inside the repository; one that is absolute or
  leads out of it (`..`) is an error naming it.
- House values for `[board.fab]` are written by `adopt` and the setup
  tool, never applied silently by `fab-release`.

## The split

### Neutral core (`fab_release.py`, `fabrication.py`)

- Config, `Board`, `--date`, `--force`, `--allow-dirty`, `--retag`, and
  the release's tag (`NAME-YYYYMMDD-REV`).
- Checks, before anything is written: ERC; DRC with schematic parity
  and the profile's rules (below); committed sources; a stackup; the
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
  and `Placement` rows for the house BOM and the profile.
- The source tgz: the project's `.kicad_pcb`, `.kicad_sch`,
  `.kicad_pro`, `.kicad_dru`, `.pretty`, `.3dshapes`, `fp-lib-table` and
  `sym-lib-table` that exist, `sources`, the PDFs and `COMMIT`.  A
  library outside the repository (a sibling clone named by a relative
  path in a library table or a 3D model path) is not in it.  The board
  file embeds each footprint and the schematic each symbol, so the
  archive rebuilds the board and its outputs; the 3D models of parts
  from such a library are missing from it.

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
    gerbers: GerberOptions | None  # None: the core's neutral options
    rules: Rules
    distributor: tuple[str, str] | None  # IPC-2581: the part-number field, the distributor
    fab_defaults: dict[str, Any]  # [board.fab] values adopt writes

    def options(self, table: dict[str, Any]) -> Any:
        ...
        # checks [board.<name>]; raises ValueError naming a bad key

    def check(self, release: Release) -> list[str]:
        ...
        # refusals beyond the core's

    def assembly(
        self,
        release: Release,
        stem: str,
        parts: list[Part],
        placements: list[Placement],
    ) -> list[Path]:
        ...
        # writes its BOM and CPL, named from stem; returns them
```

- `stem` is `NAME-REV`, or `NAME-REV-VARIANT` for a variant; the core
  calls `assembly` once per build.

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
- It never writes the board's files.
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
- The project's variants are those the board's `(variants (variant
  (name "X")))` names, and the names in the schematic's symbol
  instances, `(instances (project (path (variant (name "X") ...))))`.
- A name in `variants` that the board lacks is an error, before
  anything is written: `kicad-cli` given an unknown `--variant` exports
  the default variant and exits 0.
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

The Gerbers and drill of every variant are exported, each with
`--variant`, and compared layer by layer, less their date and comment
lines:

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
  not the schematic's.  DRC's schematic parity does not compare
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
passes DRC under the profile's rules.

Breaking changes:

1. Script `jlcpcb-release` removed; `fab-release` replaces it.
2. `jlcpcb.toml` replaced by `fab.toml`.
3. `[[board]]` key `copper` removed: present, it is an error.
4. `[[board]]` key `profile` required.
5. `[board.fab]` required, with every key.
6. A board with no stackup is refused (now a warning).
7. A board without a stackup table on User.Drawings matching its stackup
   is refused.
8. DRC runs with the profile's current rules: a board clean today may
   not be.
9. `NAME-REV-bom.csv` becomes the house BOM; JLCPCB's is
   `NAME-REV-jlcpcb-bom.csv`.
10. `NAME-REV-cpl.csv` renamed `NAME-REV-jlcpcb-cpl.csv`.
11. `NAME-REV-jlcpcb.zip` renamed `NAME-REV-gerbers-jlcpcb.zip`, and it
    gains a User.Drawings Gerber, `stackup.txt` and `fab-notes.txt`; the
    board PDF gains User.Drawings.
12. Skill `skills/jlcpcb-release` renamed `skills/fab-release`; a board
    repository's `.claude/skills/jlcpcb-release/` becomes
    `.claude/skills/fab-release/` (`adopt` moves it).
13. Python: `ee_tooling.jlcpcb_release` removed; `Board` and `Release`
    move to `ee_tooling.fab_release`, `Parts` and the BOM and CPL to
    `ee_tooling.profiles.jlcpcb`.
14. `adopt --yes` sets the board's ERC severities and connection grid.

`docs/releasing.md`'s "`jlcpcb.toml` key" becomes "`fab.toml` key".

Design variants land in the same merge and are not on this list: the
`variants` key is new and optional, and a board without it gets the
files above unchanged.

## Order of work

Refactor first, with no change in behavior; each commit passes every
check, with its docs.

1. Split `jlcpcb_release.py` into `fab_release.py` and
   `profiles/jlcpcb.py`; same command, same files.
2. `Profile`, `GerberOptions`, `PROFILES`; the core calls the JLCPCB
   profile.  Same files (a test compares a fixture release's).
3. The core parses the BOM and placement exports into `Part` and
   `Placement`; the profile writes from them.  Same files.

Then one PR, breaking, merged as one:

4. `fab.toml`, `fab-release`, `profile` and its options; unknown keys
   refused; `jlcpcb-release` removed.
5. `adopt`: write and migrate `fab.toml`; move the skill; skip the
   production directories and hidden directories when finding projects.
6. `sources` limited to the repository.  Docs:
   `skills/fab-release/SKILL.md` says what the source tgz holds, and
   that it leaves out libraries outside the repository and their 3D
   models.
7. Copper layers from the stackup; refuse a board with none.
8. `[board.fab]` required.
9. The house BOM; JLCPCB's files take its name; the Gerber zip's name.
10. Design variants: the `variants` key and its name checks; the
    per-variant exports and files; the Gerbers compared across
    variants, a zip per variant when only silkscreen differs; the
    per-variant cross-checks.
11. User.Drawings in the Gerbers, the map and the board PDF; the
    documents in the Gerber zip.
12. Refuse a board without a matching stackup table.
13. The rules in sections; DRC on a scratch
    copy with them; `adopt` installs them and raises Board Setup; the
    stale warning.
14. `adopt` sets the ERC severities and connection grid.
15. Release 0.4.0; tag after CI.

Later, not breaking: `fab-setup`; the fab notes; the no-extra-cost via
rules.

## Adversarial review

- Every board repository is blocked at the merge until it is migrated:
  a day's work per board, not a rename.
- `NAME-REV-bom.csv` changing meaning invites uploading the house BOM to
  JLCPCB by habit.
- The Gerber zip's new contents are unchecked against JLCPCB's parser:
  User.Drawings may be taken as a layer, the text files rejected.  A
  quote upload, with no order, checks it before step 11 merges.
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
