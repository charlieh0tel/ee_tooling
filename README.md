# ee_tooling

Tools, agent skills and house style for KiCad boards made at JLCPCB.

Licensed `MIT OR Apache-2.0` (`LICENSE-MIT`, `LICENSE-APACHE`).

| Path | What |
|---|---|
| `src/ee_tooling/jlcpcb_release.py` | `jlcpcb-release`: a board's JLCPCB order package from its KiCad files |
| `src/ee_tooling/fabrication.py` | a board's fabrication documents for `jlcpcb-release`: the Gerber map, the stackup and the fabrication notes |
| `src/ee_tooling/placement*.py` | `placement-score`: a board's ratsnest crossings and length, decoupling distances and order, courtyard overlaps, and a render (`docs/placement.md`) |
| `src/ee_tooling/stitch*.py` | `stitch BOARD`: ground stitching vias along every board edge and on a grid, checked by KiCad's DRC (`docs/stitch.md`) |
| `src/ee_tooling/sexpr.py` | KiCad's S-expressions: parse, write, library symbol lookup (`KICAD10_SYMBOL_DIR`, else `/usr/share/kicad/symbols`) |
| `src/ee_tooling/kicad_style_*.py` | `kicad-style-check FILE...`: a schematic, board or project against `docs/kicad-style.md` (`docs/kicad-style-check.md`) |
| `src/ee_tooling/step_model.py` | STEP stand-in models (boxes, faceted cylinders) for parts whose makers' models may not be redistributed |
| `src/ee_tooling/parts.toml` | JLCPCB's rotation and offset corrections, by LCSC part |
| `tools/odb_current/` | DC resistance and current capacity of a net, from ODB++ |
| `skills/` | agent skills for the tools: `jlcpcb-release`, `pcb-current-path` |
| `docs/kicad-style.md` | schematic, layout and silkscreen rules |
| `docs/releasing.md` | versions, tags and cutting a release |
| `docs/adopting.md` | taking ee_tooling into a board repository |
| `adopt`, `src/ee_tooling/adopt.py` | `ee-tooling-adopt`: puts ee_tooling in place in a board repository, run from its submodule |
| `src/ee_tooling/templates/` | the files `ee-tooling-adopt` writes |

## Use from a board's repository

As a git submodule following `main`, with the rest put in place by its
`adopt` script (`docs/adopting.md`):

```
git submodule add -b main https://github.com/charlieh0tel/ee_tooling.git hardware/ee_tooling
hardware/ee_tooling/adopt --yes
cd hardware && uv run jlcpcb-release
```

`jlcpcb-release` reads `jlcpcb.toml` at the repository root; the
`jlcpcb-release` skill says what goes in it.  It needs `kicad-cli`
(KiCad 10) on the path.

## Development

```
make hooks                 # once per clone
make hook-test
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```
