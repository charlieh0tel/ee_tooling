# Design: fab-release

A proposal, for review before any code.  It replaces `jlcpcb-release`
and `jlcpcb.toml` with a fab-neutral `fab-release` and `fab.toml`, JLCPCB
becoming one profile.  Decided in review: the names, a neutral core with
profiles, per-board fab values that are required and written with house
defaults, `tomlkit`, the documents inside the Gerber zip, and migration
with no alias.

## What changes for a board repository

| Now | After |
|---|---|
| `jlcpcb.toml` | `fab.toml` |
| `uv run jlcpcb-release [BOARD]` | `uv run fab-release [BOARD]` |
| `copper = [...]` per board | read from the board's stackup |
| no fab values | `[board.fab]`, required |
| a board with no stackup releases | refused |
| `.claude/skills/jlcpcb-release/` | `.claude/skills/fab-release/` |

`adopt --yes` migrates `jlcpcb.toml` to `fab.toml` (the profile
`jlcpcb`, the house fab values written in) and renames the project
skill.  No alias: one commit lists the breaking change, and the board
repositories are told.

## fab.toml

```toml
[[board]]
name = "oncore-jig"                    # the KiCad project
directory = "hardware/oncore-jig"
production = "production/oncore-jig"
sources = ["PLAN.md", "docs"]
profile = "jlcpcb"                     # jlcpcb or kicad

[board.fab]                            # every key required
ipc_class = 2                          # IPC-A-600 / IPC-6012 class
base_material = "FR-4"
tg = 150                               # deg C, minimum
electrical_test = "100% netlist test per IPC-9252"
notes = ["standards", "materials", "process", "data-integrity"]
```

- The first board is the default, as now.
- `fab-release` refuses a board missing any `[board.fab]` key, naming it.
- House defaults (the values above) live in one place in ee_tooling,
  used only by the setup tools, never silently by `fab-release`.
- `notes` chooses the fab-notes groups, in order; see Fab notes.

## The release

`fab-release [BOARD] [--date] [--force] [--allow-dirty]` writes
`<production>/YYYYMMDD-REV/`:

| File | From | Profile |
|---|---|---|
| `NAME-REV-gerbers.zip` | Gerbers, drill, job file, and `README.txt` (the map), `stackup.txt`, `fab-notes.txt` | core |
| `NAME-REV-gerber-map.txt`, `-stackup.txt`, `-fab-notes.txt` | the same documents, beside the zip | core |
| `NAME-REV-odb.tgz` | the ODB++ job | core |
| `NAME-REV-source.tgz` | the design files and `sources` | core |
| `NAME-REV-bom.csv`, `-cpl.csv` | JLCPCB's columns, `parts.toml`'s corrections | jlcpcb |
| `NAME-REV-bom.csv`, `-pos.csv` | KiCad's own BOM and position files | kicad |

The Gerber zip's name loses `jlcpcb`.  Its extra documents are unverified
with JLCPCB's parser until a real order; the skill says to check the
first one.

Checks before writing, as now plus: ERC and DRC clean; sources
committed (unless `--allow-dirty`); a stackup, and its table on
User.Drawings matching it (kicad-style-check, `kicad_style_stackup.py`);
every fab value.

New: User.Drawings, the stackup table's layer, plotted as a Gerber in
the zip (the map names it a drawing, not a layer to build) and in the
board PDF.  Today's release plots neither.  `stackup.txt` stays for
what the table lacks: each layer's Gerber, the board-wide settings
(finish, edge plating ...) and the impedance requirements.  Whether
JLCPCB's parser ignores a drawing Gerber is unverified.

## Profiles

A profile is a small Python object in `src/ee_tooling/profiles/`:

```python
class Profile(Protocol):
    name: str

    def assembly(self, release: Release, out: Path) -> list[Path]: ...

    rules: str | None  # its .kicad_dru in package data, if any
```

- `jlcpcb`: the BOM and CPL in JLCPCB's columns, `parts.toml` (moved to
  `src/ee_tooling/profiles/jlcpcb_parts.toml`), its ordering checklist
  in the skill, and its `.kicad_dru` (the DRC rules work under way).
- `kicad`: KiCad's own BOM and position CSV, for a fab that takes them.
- Gerber file naming stays KiCad's Protel extensions for both, which
  JLCPCB and most fabs read; a profile may override it if a fab needs
  its own names.

## Fab notes

`fabrication.py`'s notes become groups, chosen per board by `notes`:

| Group | Notes |
|---|---|
| `standards` | IPC-A-600 class; IPC-6012 for rigid boards |
| `materials` | base material, Tg, copper weights, finish, mask and silk colors (from the stackup and `[board.fab]`) |
| `process` | plating, LPI mask per IPC-SM-840, finished hole sizes, impedance control per the stackup's table, electrical test |
| `data-integrity` | the data not modified without approval; no holes added; markings only where the board marks them |

The wording of each note is ee_tooling's, generic; the values are the
board's.  No `[TBD]`: a value the board and `[board.fab]` cannot give is
an error before release.

## Setup tools

- `adopt`: creates `fab.toml` for a new repository (one board per
  `*.kicad_pro`, profile `jlcpcb`, house fab values), migrates
  `jlcpcb.toml`, and adds missing `[board.fab]` keys with house
  defaults.  `adopt --check` fails on a missing key.
- `fab-setup`: adds a board or sets values.
  - `fab-setup add NAME [--profile P]`
  - `fab-setup set BOARD KEY VALUE`
  - `fab-setup --interactive`: asks each value, offering the current or
    house default.  Every value is also settable by flag, for agents.
- Both edit `fab.toml` with `tomlkit` (a new dependency), keeping its
  comments and layout.

## Order of work

Each step one commit or a few, all checks passing, docs with code:

1. Refactor, no behavior change: split `jlcpcb_release.py` into a
   neutral core (`release.py`: checks, Gerbers, drill, ODB++, documents,
   source) and `profiles/jlcpcb.py` (BOM, CPL, parts).
2. Fab notes as groups; `[TBD]` placeholders become required values.
3. `fab.toml`, `fab-release`, the `kicad` profile; `copper` from the
   stackup; refuse without a stackup or fab values.  Breaking.
4. `adopt`: create and migrate `fab.toml`; the skill renamed.
5. `fab-setup`, with `tomlkit`.
6. The profile's `.kicad_dru`, once the DRC rules work lands.

## Open

- The `kicad` profile's BOM: KiCad's default fields, or a house field
  set (Manufacturer, MPN, and the fab's part number)?
- Should `fab-release` also refuse a board whose `.kicad_dru` differs
  from its profile's?
- Panels (oncore-jig's two boards with tabs): one board in `fab.toml`,
  as now.
