# Design: fab-release

`fab-release` and `fab.toml`, fab-neutral, with JLCPCB as one profile,
in ee_tooling 0.4.0.  They replace `jlcpcb-release` and `jlcpcb.toml`.

Decisions:

- The names below; a neutral core with profiles; no `kicad` profile; no
  alias for the old command.
- Per-board fab values, `[board.fab]`, required.
- The house BOM in the core; DNP parts in it at Qty 0.
- A fitted part with no LCSC number a warning, unless the board sets
  `require_lcsc`.
- The profile's rules added by the release, not refused when the
  board's copy is stale.
- KiCad 10 design variants: an optional `variants` list per board; a
  BOM, CPL, IPC-2581 and ODB++ per variant, the Gerbers and fab
  documents shared; a Gerber zip per variant when the variants'
  silkscreens differ; a variant that differs in copper, mask, paste,
  outline or drill, or that overrides a part's Footprint field,
  refused.
- A library a board reads lives inside its repository (house style);
  the source tgz holds `sources` beside the project's own files.
- `adopt` skips production, hidden and other repositories' directories
  when finding projects.
- `stackup-table` writes the stackup table the release requires.
- Git LFS (`docs/design/git-lfs.md`) in the same release.
- A flag day: every breaking change in 0.4.0.

Deferred (Later, below): `fab-setup`, the fab notes as groups, the
no-extra-cost via rules, library repositories under `adopt`, a build
from a snapshot of the commit, stock libraries' 3D models in the
source tgz.

## Names

| Before 0.4.0 | From 0.4.0 | Holds |
|---|---|---|
| `src/ee_tooling/jlcpcb_release.py` | `src/ee_tooling/fab_release.py` | the neutral core and `main()` |
| - | `src/ee_tooling/profiles/__init__.py` | `PROFILES`, a profile's name to its constructor |
| - | `src/ee_tooling/profiles/profile.py` | `GerberOptions`, `Rules`, `KICAD_GERBERS`, `Part`, `Placement`, the `Profile` Protocol and `write_csv`; imports nothing from ee_tooling |
| - | `src/ee_tooling/profiles/jlcpcb.py` | the JLCPCB profile: `Corrections` (`parts.toml`'s), `Options`, the BOM and CPL writers, `Jlcpcb` with its Gerber options and rules |
| - | `src/ee_tooling/project_libraries.py` | the libraries a project reads, inside the repository, outside it, or stock |
| - | `src/ee_tooling/kicad_rules.py` | a profile's rules in a board's `.kicad_dru` and `.kicad_pro`, for `adopt` and the release's DRC |
| - | `src/ee_tooling/stackup_table.py`, `drawing_sheet.py` | script `stackup-table` (`docs/stackup-table.md`) |
| script `jlcpcb-release` | script `fab-release` | no alias |
| `jlcpcb.toml` | `fab.toml` | boards, each with its `profile` |
| `skills/jlcpcb-release/` | `skills/fab-release/SKILL.md`, `skills/fab-release/jlcpcb.md` | the neutral skill; JLCPCB's ordering and rotation steps |
| `parts.toml`, `rules/jlcpcb*` | the same names | data the profile names |

- `fab_release.py` imports `PROFILES` and `profile.py`, never a
  profile's module.
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
sources = ["docs"]               # added to the source tgz; default []
profile = "jlcpcb"               # a key of PROFILES
variants = ["lite", "full"]      # KiCad 10 design variants; default []

[board.fab]                      # every key required
ipc_class = 2                    # IPC-A-600 / IPC-6012 class: 1, 2 or 3
base_material = "FR-4"
tg = 130                         # deg C, minimum
electrical_test = "100% netlist test per IPC-9252"

[board.jlcpcb]                   # the profile's options; optional
require_lcsc = true              # default false
```

- The first board is the default.
- There is no `copper` key: present, it is an error; the copper layers
  come from the board's stackup.
- An unknown key, at any level, is an error naming it, so later keys
  (`assembly`, `kind = "panel"`) are additive.
- Only the table named by `profile` is allowed beside `fab`.
- Each board's `production` is its own: two boards' the same, or one
  inside the other, is an error naming both, as a release's directory,
  `DATE-REV`, does not name its board.  `--force` removes an old release
  only if every file in it is the board's (`NAME-REV-*`).
- `production`, `directory` and `sources` are checked with their links
  followed too (a link may lead a production directory out of the
  repository, or make two names one directory).
- A `production` is inside the repository, and neither the same as nor
  holding any board's `directory` or `sources` path (the
  uncommitted-sources check leaves every production directory out);
  inside a board's directory is allowed (the repository-root layout).
- Each `sources` path is inside the repository; one that is absolute or
  leads out of it (`..`) is an error naming it.
- `adopt` writes the house values for `[board.fab]`; `fab-release`
  never applies them silently.

## The release

### Neutral core (`fab_release.py`, `fabrication.py`)

- Config, `Board`, `--date`, `--force`, `--allow-dirty`, `--retag`, and
  the release's tag (`NAME-YYYYMMDD-REV`).
- Before anything runs: the profile built (a bad `parts.toml` stops the
  run); each board's profile options checked; the board's files there;
  the Git LFS pointer check (`docs/design/git-lfs.md`, The release); the library paths
  (`project_libraries.py`, below); the schematic's sheet files, each
  there and inside the repository, naming it and the sheet that names
  it; the title block's rev; the release's directory and tag names; a
  stackup with a copper layer; the stackup table on User.Drawings
  matching it (kicad-style-check's rules, waivers not honored); the
  variants' names.
- Checks, before anything is written: ERC; DRC with schematic parity
  and the profile's rules (Rules, below); committed sources (below);
  each build's parts and placements (Design variants, below) and the
  profile's `check`.
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
- The title block's rev's text variables are resolved from the
  project's `text_variables`, as KiCad 10.0.8 draws the title block
  (`kicad-cli pcb export svg` of a board whose rev is `${MYREV}` showed
  the variable's value); one the project does not define is refused.  No
  rev is refused.
- `DATE-REV` (`--date` and the title block's rev) is one path
  component, and the tag `NAME-DATE-REV` a name `git check-ref-format`
  takes, or the release stops before anything runs, naming the value
  and where it comes from.

### Committed sources

- What the release reads: `fab.toml`, the board's directory, the
  project's files, its sheet files, `sources`, each library path its
  tables and 3D models name inside the repository, and the ee_tooling
  checkout it runs from when the repository holds it as a submodule or
  tracked files (its `parts.toml`, its rules; a package installed in a
  venv is not).  Every production directory is left out, as written and
  through a link.
- `git status --porcelain -z --untracked-files=all
  --ignore-submodules=none` over those paths, in the repository that
  holds each, literal pathspecs, git's output options set whatever the
  user's configuration; each path as written (a path through a link as
  the link: git refuses a path beyond one) and with its links followed;
  a path that is a link out of the repository is refused.  Each
  submodule holding a path, at every level, is asked of its parent by
  its gitlink (moved, or with modified or untracked content); each under
  a path asked also by its own status; whatever any `ignore` setting.
  An untracked file under the board's directory or a source counts; an
  ignored one there does not (the tgz holds tracked files only); an
  ignored file the release reads by name (`fab.toml`, the project's own
  files, a sheet, a library table, a library) counts.  Every git call
  of the check stops the release on a failure.
- HEAD's full hash is read as the release begins, before any input is
  read, and the uncommitted-sources check is run before any input is
  copied or ERC runs, HEAD unmoved by then: that is the baseline; COMMIT, the tag (`git
  tag -a NAME -m MSG <hash>`) and the printed line name it.  After the
  last export, before the old release is replaced, HEAD and the check
  are read again: a commit or a change since stops the release, nothing
  replaced.
- A build from uncommitted sources (`--allow-dirty`) is not tagged, and
  is refused when the release's tag exists, `--retag` or not: the tag
  would name a package it does not describe.

### Building and replacing

- A release is built in `PRODUCTION/.DATE-REV.building/` (hidden: the
  uncommitted-sources check and `adopt`'s project search skip it) and
  replaces the old one (`--force`) only after the last export; a failure
  removes the building directory and leaves the old release and its tag.
- The building directory is made exclusively, a `.lock` in it naming
  the host and process: another run's stops the run; one whose process
  is gone from this host (a killed run's) is removed, said on stderr;
  one from another host is not judged (a process id means nothing
  there), and stops the run, naming the command that removes it.  Just
  before the swap, the release directory is checked again: one another
  run made meanwhile is replaced only with `--force`, and only if all
  its files are the board's.
- The swap: the old release moved aside (`.DATE-REV.old`), the new one
  put in its place, the tag made or moved, then the old one removed.  A
  failure at any step (`git tag` refused: signing, a lock) puts the old
  release back: the old release with its old tag, or the new with its
  new tag, never a mix.
- The tag's message is `NAME REV: fab release PRODUCTION/DATE-REV`,
  with `; variants A, B` for a board with `variants`.

### The source tgz and the libraries

- The source tgz: the files git tracks (`git ls-files -z
  --recurse-submodules`) of the project's `.kicad_pcb`, `.kicad_sch`,
  `.kicad_pro`, `.kicad_dru`, `NAME.pretty`, `NAME.3dshapes`,
  `fp-lib-table` and `sym-lib-table`, every sheet file of the
  schematic's hierarchy, and `sources`; the PDFs and `COMMIT`; each file
  once; never `.git`, a production directory, or an untracked or
  ignored file under a source.  A `sources` path that is, or goes
  through, a link is refused (it would be archived as the bare link).
- A library a board reads lives inside the repository, normally a git
  submodule (`docs/kicad-style.md`, Files); a clone beside the
  repository is not supported.  Besides the project's own `NAME.pretty`
  and `NAME.3dshapes`, a library is in the archive only as one of
  `sources`.  The board file embeds each footprint and the schematic
  each symbol, so the archive rebuilds the board and its outputs; the
  3D models of a library not in `sources`, and of KiCad's stock
  libraries, are not in it.
- The release refuses, before anything runs, a path in the project's
  `fp-lib-table` or `sym-lib-table`, or a footprint's 3D model path in
  the board, that resolves outside the repository, naming each as
  written (`project_libraries.py`).  `${KIPRJMOD}`, and a relative path,
  are from the project's directory.  A path under one of KiCad's install
  variables for its stock libraries (`${KICADn_FOOTPRINT_DIR}`,
  `${KICADn_SYMBOL_DIR}`, `${KICADn_3DMODEL_DIR}`, n any version or
  none, and the legacy `${KISYSMOD}` and `${KISYS3DMOD}`) is a stock
  library: allowed, and not in the source tgz.  Any other variable
  (`${KICAD10_TEMPLATE_DIR}`, `${KICAD10_3RD_PARTY}`, a user's own), a
  KiCad 5 `$(NAME)` and a home directory (`~`) are refused: their value
  is the user's.  A path is placed with its links followed: a link
  inside the repository to a library outside it is outside.  An
  embedded model is not a path.
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
  the Gerber job file, `README.txt` (the map), `stackup.txt` and
  `fab-notes.txt`.
- Its BOM `NAME-REV-jlcpcb-bom.csv` (Comment, Designator, Footprint,
  LCSC Part #; DNP left out) and CPL `NAME-REV-jlcpcb-cpl.csv`, with
  `parts.toml`'s rotations and offsets.  A part in its BOM and not
  placed refuses the release.  A fitted part with no LCSC number is a
  warning, naming each one, on stderr; with `require_lcsc = true` in
  `[board.jlcpcb]` (default false), it refuses the release.  A part
  whose correction is not known yet (`parts.toml`'s `[unchecked]`) is a
  warning.
- IPC-2581's distributor: the `LCSC` field, distributor "LCSC".
- Rules: `rules/jlcpcb.kicad_dru` in sections (JLCPCB's limits, the
  house rules), and `rules/jlcpcb_board_setup.json`, the Board Setup
  minimums: copper clearance, copper to edge, hole clearance, hole to
  hole, through-hole diameter, track width, via annular width, via
  diameter (`docs/kicad-rules.md`).
- Its `[board.fab]` house values, which `adopt` writes: IPC class 2,
  FR-4, Tg 130, a 100% netlist test per IPC-9252.

## Names of the files

A file whose contents are shaped for one fab carries the profile's
name; a neutral file does not.  The Gerber zip is
`NAME-REV-gerbers-<profile>.zip` when the profile's Gerber options are
not kicad-cli's own (`KICAD_GERBERS`), `NAME-REV-gerbers.zip`
otherwise.

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

Internal, not public API (`profiles/profile.py`).  Data where data will
do; hooks only where a fab's output is code.

```python
@dataclass(frozen=True)
class GerberOptions:
    x2: bool  # X2 attributes
    protel_extensions: bool  # .gtl ..., or KiCad's .gbr
    subtract_soldermask: bool  # soldermask openings cut from the silkscreen
    separate_drill: bool  # PTH and NPTH in separate files


@dataclass(frozen=True)
class Rules:
    dru: str  # its .kicad_dru, in sections headed "# --- <name> ---"
    optional: dict[str, str]  # an option of its board options to the section left out when false
    board_setup: str  # its Board Setup minimums, a .kicad_pro fragment (JSON)


class Profile[Options](Protocol):
    name: str  # fab.toml's profile value; names its files
    gerbers: GerberOptions
    rules: Rules
    distributor: tuple[str, str] | None  # IPC-2581: the part-number field, the distributor
    fab_defaults: dict[str, Any]  # the [board.fab] values adopt writes for a new board

    def options(self, table: dict[str, Any]) -> Options: ...
    def check(
        self, parts: list[Part], placements: list[Placement], options: Options
    ) -> list[str]: ...
    def warnings(
        self, parts: list[Part], placements: list[Placement], options: Options
    ) -> list[str]: ...
    def outputs(self, base: str) -> list[str]: ...
    def assembly(
        self,
        dest: Path,
        base: str,
        parts: list[Part],
        placements: list[Placement],
        options: Options,
    ) -> list[Path]: ...
```

- `options` checks `[board.<name>]`, raising `ValueError` naming a bad
  key; the release calls it for every board before anything else, and
  passes what it returns to the other hooks.
- `check` gives refusals beyond the core's, `warnings` what is said on
  stderr ("variant X: " before each in a variant's build), `outputs` the
  names of the files `assembly` writes, and `assembly` writes its BOM
  and CPL into `dest`, returning them.
- `base` is the file stem, `NAME-REV`, or `NAME-REV-VARIANT` for a
  variant; the core calls `check`, `warnings` and `assembly` once per
  build, every build's `check` before anything is written.  `check`
  takes the rows, not the release: `profile.py` imports nothing from
  ee_tooling.
- `Rules.optional` leaves a section of the profile's `.kicad_dru` out
  when its option is false (`kicad_rules.installed`).  JLCPCB's is
  empty: no option uses it.
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
  which win, and whose Board Setup is raised to the profile's minimums.
  So a release is always checked against the current rules, and a
  change to ee_tooling's rules never blocks a release just for being
  newer.  A rule a board breaks fails DRC as any other; so does a
  warning (`--exit-code-violations`).
- Each minimum of the installed section of a kind Board Setup also
  holds is raised to the board's Board Setup value where that is higher
  (`kicad_rules.floored`): a rule replaces Board Setup's constraint for
  what it matches, so a lower one would loosen the board's own limit.
- A `.kicad_dru` that does not parse makes `kicad-cli pcb drc` (10.0.8)
  drop every custom rule, the profile's too, with no message and exit
  status 0 (a board's rule file with an unclosed rule: 94 violations
  fell to 60, all Board Setup's).  So a first DRC runs on a scratch copy
  whose rules end with a canary rule every item breaks
  (`ee_tooling canary`, an assertion of false); its report must name it,
  or the release stops, naming the board's `.kicad_dru`.
- A board's `.kicad_dru` whose markers are not one begin line, then one
  end line stops the release, naming it.
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
- Every other `kicad-cli` command (ERC, the exports) reads a second
  scratch copy, made the same way with the board's own `.kicad_dru` and
  `.kicad_pro`: `kicad-cli` (10.0.8) writes `NAME.kicad_prl` beside the
  project it reads, every command, so on the board's own files it would
  leave an untracked file that the next release's uncommitted-sources
  check refuses.
- `jlcpcb.kicad_dru` holds the limits and house sections.  The
  no-extra-cost via rules are not in it (Later).
- `adopt --yes` installs the same rules in the board's `.kicad_dru`,
  between markers, with the board's own rules after them, and raises
  Board Setup values in the `.kicad_pro` to at least the profile's,
  never lowering one.  That makes KiCad's own DRC in the editor match
  the release.  `fab-release` warns when the installed section is
  missing or stale, or Board Setup is below the minimums, naming
  `adopt --yes`, and when the board's own rules below the end marker
  copy the profile's (a hand-made copy), naming each and the file.

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
| Exclude from position files | `(in_pos_files no)` | `(exclude_from_pos_files yes)` | honored by the placement export; a part in the profile's BOM and not placed is refused by the profile's `check` |
| Exclude from board | `(on_board no)` | - | as without variants |
| Exclude from simulation | `(exclude_from_sim yes)` | - | ignored |
| A field's value (Value, LCSC, MPN, ...) | `(field (name "F") (value "V"))` | likewise | honored: the BOMs, the CPL's Val, `require_lcsc`, IPC-2581 and ODB++ take the variant's |
| Footprint field | likewise | - | refused: one board has one footprint per reference |

Built per variant, each with `--variant VARIANT`, one `kicad-cli` call
per variant (`pcb export pos` with several `--variant` and `${VARIANT}`
in the path writes one file, named with `${VARIANT}` literally):

- The BOM export, and again with `--exclude-dnp` for which parts are
  fitted (KiCad's DNP attribute: `${DNP}` in `--fields` gives a
  symbol's own field named DNP when it has one, 10.0.8, so a fitted part
  with a field DNP = "no" would read as not fitted), and the placement
  export (with
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

Checks per build, the default's too, beyond the core's:

- A part fitted in the variant's placement export and DNP in its BOM,
  or the reverse, is refused, naming it: the board's variant data is
  not the schematic's.  The board's DNP parts are those in a placement
  export without `--exclude-dnp` and not in the one with it.  DRC's
  schematic parity does not compare variants (10.0.8); Update PCB from
  Schematic syncs them.
- A part whose Value differs between the two exports is refused, for
  the same reason.
- A part whose BOM Footprint (`LIBRARY:NAME`) differs from its
  footprint's on the board (the board file's, placed or not) is refused
  (the footprint override above), another library's footprint of the
  same name too.

Parts are read from `kicad-cli`'s BOM export per variant, never from
the netlist: a netlist's `comp` holds every variant's overrides in its
`(variants (variant (name "X") (property (name "dnp") ...)))` (in XML,
`<variants>`), so a reader of every `property` under `comp` takes a
part DNP in one variant as DNP in all.  `review_netlist.py` reads only
a `comp`'s direct children, which hold the default variant.

## Fab notes

`fab-notes.txt` takes the IPC class, base material, minimum Tg and
electrical test from `[board.fab]`, and the layer count, thickness,
copper weights, finish, colors, impedance control and minimum features
from the board and its exports; `[TBD: ...]` marks what neither gives.

## Setup tools

- `adopt`: creates `fab.toml` for a new repository (one board per
  `*.kicad_pro`, profile `jlcpcb`, the profile's fab values), migrates
  `jlcpcb.toml`, adds missing `[board.fab]` keys, installs the rules
  section and raises Board Setup.  It sets `erc.rule_severities` in the
  `.kicad_pro` from `erc_severities.json`, keeping a board's own
  severity where it is stricter (ignore, warning, error), and
  `schematic.connection_grid_size`.  `adopt --check` fails while any of
  it is pending, saying what each file awaits (missing, out of date, to
  be migrated, to be updated); the dry run, `--yes` and `--check` take
  the boards from the `fab.toml` `--yes` leaves (`docs/adopting.md`).
- `adopt` finds the projects (`*.kicad_pro`) under the hardware
  directory, skipping the submodule, every production directory
  (`production/`, which `adopt` assigns, and each `production` an
  existing `jlcpcb.toml` or `fab.toml` names), every directory whose
  name starts with `.` (`.venv`, `.git`, `.claude/`, whose worktrees
  hold copies of the board's project), and every other repository (a
  directory holding a `.git`: a library's submodule, a clone).  So a
  project file in a release, a worktree or a library is not taken for a
  board.  It matters most with the KiCad project at the repository root
  (a supported layout: `docs/adopting.md`, `test_root_layout`), where
  the hardware directory is the root.
- `stackup-table`: writes or refreshes a board's stackup table from its
  stackup (`docs/stackup-table.md`); the release's refusal of a missing
  or stale table prints its command.
- There is no tool that adds a board or sets a value in `fab.toml`:
  `fab-setup` is deferred (Later).

## Migration

A flag day: the breaking changes land on `main` in one merge and are
released as 0.4.0.  A board repository not yet migrated pins its
submodule to `v0.2.2`, the last release before it.  A board repository
cannot release from 0.4.0 until it installs git-lfs, runs `adopt
--yes`, checks `fab.toml`, puts its libraries in the repository,
commits with `git add --renormalize .`, draws the stackup table and
passes ERC and DRC under the profile's rules.  `docs/adopting.md`,
Upgrading a board repository, gives the steps.

Breaking changes (`docs/releasing.md`: a board repository must act):

### fab.toml

1. `jlcpcb.toml` is replaced by `fab.toml` at the repository root;
   `jlcpcb.toml` is not read (`adopt --yes` migrates it).
2. `[[board]]` key `profile` is required: a key of `PROFILES`
   (`jlcpcb`).
3. `[board.fab]` is required, with every key: `ipc_class` (1, 2 or 3),
   `base_material`, `tg` (deg C, above 0), `electrical_test`.
4. `[[board]]` key `copper` is removed: present, it is an error.
5. An unknown key at any level is an error naming it; beside a board's
   keys the only tables are `fab` and the one its `profile` names; a
   `[board.jlcpcb]` option that is not `true` or `false` is an error;
   two `[[board]]` of one name are an error.
6. A `sources` path outside the repository (absolute, or leading out of
   it with `..`) is an error naming it.
7. A `sources` path that is, or goes through, a symbolic link is refused,
   naming the link.
8. A `production` outside the repository, the same as or holding a
   board's `directory` or a `sources` path, two boards' `production`
   the same or one inside the other, a `production` a link leads out of
   the repository, or two that a link makes one directory: an error
   naming them.

### adopt

9. `adopt --yes` writes `fab.toml` (one board per project, or migrated
   from `jlcpcb.toml`, which it removes when git holds it as it is;
   comments are not carried over), adds each missing `[board.fab]` key
   with the profile's house value, and moves
   `.claude/skills/jlcpcb-release/` to `.claude/skills/fab-release/`,
   renaming `jlcpcb-release` and `jlcpcb.toml` in its `.md` files.
10. `adopt --yes` edits each board's `NAME.kicad_dru` (the profile's
    rules between markers, the board's own after them) and
    `NAME.kicad_pro` (Board Setup minimums raised, never lowered; ERC
    severities from `erc_severities.json`, each kept where the board's is
    stricter; the connection grid 50 mil).
11. `adopt --check` also fails while `fab.toml` is missing and a KiCad
    project is there ("missing"), a file is still to be migrated ("to be
    migrated"), a board's rules section is missing or stale, its Board
    Setup below the minimums or its ERC severities or connection grid
    differ ("to be updated"), a managed hook is not executable in the
    work tree or the index ("out of date"), or an interrupted write left
    a scratch file ("left over").
12. `adopt`, every mode, stops before anything is done on: a symbolic
    link it would write, remove or rename through (but `AGENTS.md`,
    `.gitattributes` and a file it edits, linked to a regular file
    inside the repository), a link leaving the repository among them; a
    file it reads that is not UTF-8; `AGENTS.md`, `.gitattributes` or a
    board's `.kicad_dru` whose markers are not one begin line, then one
    end line; a board `directory` outside the repository or in another
    repository; two KiCad projects of one name when writing `fab.toml`;
    a bad `[board.<profile>]`; a `fab` given other than as a
    `[board.fab]` table, or `fab.toml`'s `[[board]]` headers not
    matching its boards ("N boards, M [[board]] headers"); a
    `.kicad_pro` that is not JSON; a file it must write that cannot be
    written; an edit due while KiCad's lock file for the board's
    project, board or schematic is there.

### The release's checks

13. A board with no stackup is refused (a warning before 0.4.0), as is
    a stackup with no copper layer.
14. A board without a stackup table on User.Drawings matching its
    stackup (kicad-style-check's stackup table rules, waivers not
    honored) is refused.
15. DRC runs on a scratch copy with the profile's current rules and
    Board Setup raised to its minimums: a board clean before may not
    be.
16. A board whose `.kicad_dru` kicad-cli does not parse (so DRC would
    run with no custom rule) is refused.
17. The uncommitted-sources check covers `fab.toml`, the board's
    directory, its sheet files, `sources`, each library path inside the
    repository, the ee_tooling checkout when the repository holds it,
    every submodule among them at any depth whatever its `ignore`
    setting, and a file the release reads that git ignores; a path it
    reads that is a link out of the repository is refused.
18. A library path (a project library table's, a 3D model's) outside
    the repository, through a link out of it, under a variable other
    than `${KIPRJMOD}` and KiCad's stock library variables, in KiCad 5's
    `$(NAME)` form or under `~` is refused, naming each as written.
19. A schematic sheet file that is not there, or is outside the
    repository, is refused, naming it and the sheet that names it.
20. A title-block rev using a text variable the project does not define
    is refused; a rev (or `--date`) that makes the release directory
    more than one path component, or the tag not a git tag name
    (`Rev A`), is refused before anything runs.
21. A part DNP in the schematic and fitted on the board, or the
    reverse, or whose Value or footprint differs between them, is
    refused, for the default build as for a variant's.
22. A build from uncommitted sources (`--allow-dirty`) of a release
    whose tag exists is refused, `--retag` or not.
23. `--force` stops on a release directory holding a file not the
    board's (`NAME-REV-*`).
24. A commit, or a change to what the release reads, during the build
    stops it, nothing replaced; another run's building directory stops
    a run.

### Outputs and names

25. Script `jlcpcb-release` is removed; `fab-release` replaces it, with
    no alias.
26. Skill `skills/jlcpcb-release` is renamed `skills/fab-release`
    (`SKILL.md`, and JLCPCB's steps in `jlcpcb.md`); a board
    repository's `.claude/skills/jlcpcb-release/` becomes
    `.claude/skills/fab-release/` (`adopt --yes` moves it).
27. `NAME-REV-bom.csv` is the house BOM; JLCPCB's BOM is
    `NAME-REV-jlcpcb-bom.csv`.
28. `NAME-REV-cpl.csv` is renamed `NAME-REV-jlcpcb-cpl.csv`.
29. `NAME-REV-jlcpcb.zip` is renamed `NAME-REV-gerbers-jlcpcb.zip`, and
    gains a User.Drawings Gerber, `stackup.txt` and `fab-notes.txt`;
    the board PDF gains a User.Drawings page.
30. The fab notes take the IPC class, base material, Tg and electrical
    test from `[board.fab]` (the base material from the stackup's
    dielectrics before 0.4.0).
31. The source tgz holds every sheet file of the schematic, and leaves
    out `.git` and every production directory; its `COMMIT` names the
    full hash.
32. The tag's message is `NAME REV: fab release PRODUCTION/DATE-REV`
    (`JLCPCB release` before 0.4.0).
33. `rules/jlcpcb.kicad_dru` is in sections; JLCPCB's track width, via
    diameter, hole size, via annular ring and hole-to-hole minimums are
    Board Setup values in `rules/jlcpcb_board_setup.json`, not rules;
    the no-extra-cost via rules are removed.
34. A release is built in the production directory's hidden
    `.DATE-REV.building/`, and the old one is kept aside in
    `.DATE-REV.old/` until the tag is made.

### Git LFS (`docs/design/git-lfs.md`)

35. `adopt` and `adopt --check` require git-lfs.
36. `adopt --check` fails on a plain blob of an LFS-tracked type at
    HEAD: a repository with committed archives, PDFs, STEP or VRML files
    renormalizes and commits.
37. `adopt --yes` writes a managed section at the top of
    `.gitattributes` and `.githooks/pre-push`; a board repository's own
    `pre-push` is replaced.
38. A push without git-lfs is refused.
39. The release refuses a board whose inputs hold an unfetched LFS
    pointer.
40. A board repository's host must support Git LFS: a push to one that
    does not is refused.

### Python API

41. `ee_tooling.jlcpcb_release` is removed.  `Board`, `Release`,
    `load_config`, `revision`, `TagPlan`, `tag_plan` and
    `repository_root` are in `ee_tooling.fab_release`; `CONFIG` is
    `"fab.toml"`; `Board` has no `copper` and has `profile`, `fab`,
    `options` and `variants`; `Release` takes the copper layers.
42. `Parts` is renamed `Corrections`, in `ee_tooling.profiles.jlcpcb`
    with `jlcpcb_bom` and `jlcpcb_cpl` (taking `Part` and `Placement`
    rows), `BOM_HEADER` and `CPL_HEADER`; `write_csv`, `Part`,
    `Placement` and `GerberOptions` are in
    `ee_tooling.profiles.profile`.
43. `fabrication.notes()` takes the board's `Fab`, its last argument;
    `fabrication.IPC_CLASS`, `TG`, `ELECTRICAL_TEST` and
    `BASE_MATERIAL` are removed.
44. `stitch_kicad.point` is removed: `kicad_style_board.parse_point`.

`docs/releasing.md`'s "`jlcpcb.toml` key" is "`fab.toml` key".

Design variants are not on this list: the `variants` key is new and
optional, and a board without it gets the files above.  Nor are
`require_lcsc` (optional, default false) and `stackup-table` (a new
script).

## Not verified

- The host's Git LFS allowances against a board repository's archives
  per release (`docs/design/git-lfs.md`, Cost).
- Whether JLCPCB's parser takes the Gerber zip with a User.Drawings
  Gerber, `stackup.txt` and `fab-notes.txt`: no quote upload has tried
  it.  User.Drawings may be taken as a layer, the text files rejected.
- Whether `lfs: true` with `submodules: true` fetches a submodule's Git
  LFS objects.
- KiCad's lock file names (`~NAME.kicad_pro.lck`, ...): inferred from
  libkicommon's `~*.lck`, not observed beside an open project.
- A building directory locked by a run on another host: no test makes
  one.

## Later

Not breaking, each deferred:

- Building from an immutable snapshot of the checked commit (a `git
  worktree` or `git archive` of it, its submodules too) rather than
  the work tree.  The uncommitted-sources check is a list of cases (the
  paths above, links, submodules, ignored files), and a build from the
  commit itself cannot take in anything else; at the cost of a checkout
  per release and the submodules' and Git LFS's objects fetched into
  it.
- `fab-setup`: add a board or set a value (`fab-setup add NAME
  [--profile P]`, `fab-setup set BOARD KEY VALUE`, `--interactive`),
  editing `fab.toml` with `tomlkit` (a new dependency) to keep its
  comments.
- The fab notes as groups chosen per board, and whether a value the
  board and `[board.fab]` cannot give is an error.
- JLCPCB's no-extra-cost via rules (a via hole of 0.3 mm or more, or of
  0.2 mm or more in a diameter of 0.45 mm or more), as an optional
  section (`Rules.optional`).
- Library repositories under `adopt` (`docs/design/git-lfs.md`,
  Library repositories): they copy the `.gitattributes` section by
  hand.
- 3D models from KiCad's stock libraries in the source tgz.

## Adversarial review

- Every board repository is blocked at the merge until it is migrated:
  a day's work per board, not a rename.
- The flag day also brings Git LFS: git-lfs on every machine and a
  renormalize commit per board repository.
- `NAME-REV-bom.csv` changing meaning invites uploading the house BOM to
  JLCPCB by habit.
- The Gerber zip's contents are unchecked against JLCPCB's parser
  (Not verified): User.Drawings may be taken as a layer, the text files
  rejected.
- DRC in the editor and in the release differ until `adopt --yes`; the
  release warns.
- Board Setup is raised, never lowered: a board moved to a looser fab
  keeps the stricter values.
- `adopt` editing `.kicad_pro` and `.kicad_dru` while KiCad has them open
  loses one side's edits.  It stops while KiCad's lock file is there; a
  KiCad on another machine sharing the files, or one that keeps no lock
  file, is not detected.
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
  and it rests on reading where KiCad 10.0.8 records variants (the
  `.kicad_pro`, the board, the overrides), formats that may change.
- Variants: on a project whose variants only the `.kicad_pro` lists
  (KiCad's GUI), `kicad-cli pcb export gerbers --variant X` (10.0.8)
  honors the footprints' overrides but gave the default variant's
  `${VARIANT}` silkscreen text, the same for every variant, on a scratch
  board; with the board's own `(variants)` list, each variant's, and
  `${VARIANT_DESC}` likewise (each variant's `(description ...)`).  The
  release warns, in a build with `variants`, when the board shows text
  with `${VARIANT}` or `${VARIANT_DESC}` on a layer it exports and its
  own list lacks a listed variant, naming the layers and the texts and
  the fix (pcbnew's `AddVariant`, then save); it changes nothing else.
- Variants: one tag for all of a release's variants: a variant added at
  the same revision later is a new release, a new date directory and a
  new tag, with every variant rebuilt.
