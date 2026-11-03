# Design: fab-release

A proposal, for review before any code.  It replaces `jlcpcb-release`
and `jlcpcb.toml` with a fab-neutral `fab-release` and `fab.toml`, with
JLCPCB as one profile.  Decided in review: the names below, a neutral
core with profiles, required per-board fab values, the house BOM in the
core, rules added by the release (not refused when stale), no `kicad`
profile yet, no alias for the old command, a flag day, and the fab
notes deferred.  Open: the questions at the end.

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
  DNP, LCSC, MPN, Manufacturer, Datasheet.
- One ungrouped BOM export and one placement export, parsed into `Part`
  and `Placement` rows for the house BOM and the profile.

### JLCPCB profile (`profiles/jlcpcb.py`)

- Gerber options: Protel extensions, no X2, soldermask subtracted from
  silk; Excellon in mm, PTH and NPTH separate, absolute origin.
- Its Gerber zip, `NAME-REV-gerbers-jlcpcb.zip`: the Gerbers, drill,
  `README.txt` (the map), `stackup.txt` and `fab-notes.txt`.
- Its BOM `NAME-REV-jlcpcb-bom.csv` (Comment, Designator, Footprint,
  LCSC Part #; DNP left out) and CPL `NAME-REV-jlcpcb-cpl.csv`, with
  `parts.toml`'s rotations and offsets.  A fitted part with no LCSC
  number refuses the release, naming each one.
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
        self, release: Release, parts: list[Part], placements: list[Placement]
    ) -> list[Path]:
        ...
        # writes its BOM and CPL; returns them
```

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
- `fab-setup`: adds a board or sets a value (`fab-setup add NAME
  [--profile P]`, `fab-setup set BOARD KEY VALUE`, `--interactive`),
  editing `fab.toml` with `tomlkit` (a new dependency) to keep its
  comments.

## Migration

A flag day: the breaking commits land on `main` in one merge and are
released as 0.2.0.  `v0.1.0` is tagged on `main` before the merge, the
pin for a board repository not yet migrated.  A board repository cannot release from the merge
until it runs `adopt --yes`, checks `[board.fab]`, draws the stackup
table and passes DRC under the profile's rules.

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
14. JLCPCB profile: a fitted part with no LCSC number is refused (now a
    warning).
15. `adopt --yes` sets the board's ERC severities and connection grid.

`docs/releasing.md`'s "`jlcpcb.toml` key" becomes "`fab.toml` key".

## Order of work

Refactor first, with no change in behavior; each commit passes every
check, with its docs.

0. Tag `v0.1.0` on `main`, after CI.
1. Split `jlcpcb_release.py` into `fab_release.py` and
   `profiles/jlcpcb.py`; same command, same files.
2. `Profile`, `GerberOptions`, `PROFILES`; the core calls the JLCPCB
   profile.  Same files (a test compares a fixture release's).
3. The core parses the BOM and placement exports into `Part` and
   `Placement`; the profile writes from them.  Same files.

Then one PR, breaking, merged as one:

4. `fab.toml`, `fab-release`, `profile` and its options; unknown keys
   refused; `jlcpcb-release` removed.
5. `adopt`: write and migrate `fab.toml`; move the skill.
6. Copper layers from the stackup; refuse a board with none.
7. `[board.fab]` required.
8. The house BOM; JLCPCB's files take its name; the Gerber zip's name.
9. User.Drawings in the Gerbers, the map and the board PDF; the
   documents in the Gerber zip.
10. Refuse a board without a matching stackup table.
11. The rules in sections; DRC on a scratch
    copy with them; `adopt` installs them and raises Board Setup; the
    stale warning.
12. The JLCPCB profile refuses a fitted part with no LCSC number.
13. `adopt` sets the ERC severities and connection grid.
14. Release 0.2.0; tag after CI.

Later, not breaking: `fab-setup`; the fab notes; the no-extra-cost via
rules.

## Adversarial review

- Every board repository is blocked at the merge until it is migrated:
  a day's work per board, not a rename.
- `NAME-REV-bom.csv` changing meaning invites uploading the house BOM to
  JLCPCB by habit.
- The Gerber zip's new contents are unchecked against JLCPCB's parser:
  User.Drawings may be taken as a layer, the text files rejected.  A
  quote upload, with no order, checks it before step 9 merges.
- DRC in the editor and in the release differ until `adopt --yes`; the
  release warns.
- A fitted part JLCPCB is not to place (a test point, a hand-fitted
  connector) needs DNP or Exclude from BOM, or the release is refused.
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

## Open

1. Does the house BOM include DNP parts, marked (proposed), or leave them
   out?
