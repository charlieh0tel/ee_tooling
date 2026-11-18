# ee_tooling

Tools, agent skills and house style for KiCad boards made at JLCPCB.

Licensed `MIT OR Apache-2.0` (`LICENSE-MIT`, `LICENSE-APACHE`).

| Path | What |
|---|---|
| `src/ee_tooling/fab_release.py` | `fab-release`: a board's order package for its fab, from its KiCad files and `fab.toml` |
| `src/ee_tooling/profiles/` | what a release shapes for one fab: `PROFILES`, by name; `profile.py`, the interface (`Profile`, `GerberOptions`, `Part`, `Placement`); `jlcpcb.py`, JLCPCB's Gerber options, IPC-2581 distributor, board options (`require_lcsc`), assembly BOM and placement, and its corrections from `parts.toml` |
| `src/ee_tooling/fabrication.py` | a board's fabrication documents for `fab-release`: the Gerber map, the stackup and the fabrication notes |
| `src/ee_tooling/placement*.py` | `placement-score`: a board's ratsnest crossings and length, decoupling distances and order, courtyard overlaps, and a render (`docs/placement.md`) |
| `src/ee_tooling/stitch*.py` | `stitch BOARD`: ground stitching vias along every board edge and on a grid, checked by KiCad's DRC (`docs/stitch.md`) |
| `src/ee_tooling/stackup_table.py`, `drawing_sheet.py` | `stackup-table BOARD`: a board's stackup table on User.Drawings, written or refreshed from its physical stackup, inside the drawing sheet's frame (`docs/stackup-table.md`) |
| `src/ee_tooling/sexpr.py` | KiCad's S-expressions: parse, write, edit in place, library symbol lookup (`KICAD10_SYMBOL_DIR`, else `/usr/share/kicad/symbols`) |
| `src/ee_tooling/kicad_style_*.py` | `kicad-style-check FILE...`: a schematic, board or project against `docs/kicad-style.md`, less the waivers in its `kicad-style.toml` (`docs/kicad-style-check.md`) |
| `src/ee_tooling/rules/` | KiCad's own DRC rules, Board Setup minimums and ERC severities: JLCPCB's limits and the house rules (`docs/kicad-rules.md`) |
| `src/ee_tooling/kicad_rules.py` | a profile's rules in a board's project: its `.kicad_dru` section between markers and its Board Setup raised, for `adopt` and `fab-release`'s DRC (`docs/kicad-rules.md`) |
| `src/ee_tooling/step_model.py` | STEP stand-in models (boxes, faceted cylinders) for parts whose makers' models may not be redistributed |
| `src/ee_tooling/parts.toml` | JLCPCB's rotation and offset corrections, by LCSC part |
| `tools/odb_current/` | DC resistance and current capacity of a net, from ODB++ |
| `src/ee_tooling/review*.py` | `design-review`: the evidence (kicad-cli exports, netlist, board and ERC summaries, pair routing gaps) findings lint and punch-list check of an adversarial design review (`docs/design-review.md`) |
| `skills/` | agent skills for the tools: `fab-release` (with each profile's ordering steps), `pcb-current-path`, `design-review` |
| `docs/kicad-style.md` | schematic, layout and silkscreen rules |
| `docs/releasing.md` | versions, tags and cutting a release |
| `docs/adopting.md` | taking ee_tooling into a board repository |
| `docs/design/` | proposals under review, before code |
| `adopt`, `src/ee_tooling/adopt.py` | `ee-tooling-adopt`: puts ee_tooling in place in a board repository, run from its submodule |
| `src/ee_tooling/templates/` | the files `ee-tooling-adopt` writes |

## Use from a board's repository

As a git submodule following `main`, with the rest put in place by its
`adopt` script (`docs/adopting.md`):

```
git submodule add -b main https://github.com/charlieh0tel/ee_tooling.git hardware/ee_tooling
hardware/ee_tooling/adopt --yes
cd hardware && uv run fab-release
```

`fab-release` reads `fab.toml` at the repository root; the
`fab-release` skill says what goes in it.  It needs `kicad-cli`
(KiCad 10) on the path.

## Development

```
make hooks                 # once per clone
make hook-test
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
uv run pytest --cov=ee_tooling --cov-report=term-missing   # coverage
```

Tests that need `kicad-cli` (KiCad 10), KiCad's `pcbnew` module,
KiCad's demo projects (`/usr/share/kicad/demos`) or poppler are
skipped without them.  `pcbnew` belongs to the system Python, so it
needs a venv made with `uv venv --system-site-packages --python
/usr/bin/python3`, and that Python 3.12 or later.  CI's `kicad` job
installs KiCad and its demos from the `kicad/kicad-10.0-releases` PPA
and poppler, fails if the KiCad installed is not 10, makes such a
venv, and fails if any of those tests is skipped.
