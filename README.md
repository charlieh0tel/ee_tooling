# ee_tooling

Tools, agent skills and house style for KiCad boards made at JLCPCB.

Licensed `MIT OR Apache-2.0` (`LICENSE-MIT`, `LICENSE-APACHE`).

| Path | What |
|---|---|
| `src/ee_tooling/jlcpcb_release.py` | `jlcpcb-release`: a board's JLCPCB order package from its KiCad files |
| `src/ee_tooling/parts.toml` | JLCPCB's rotation and offset corrections, by LCSC part |
| `tools/odb_current/` | DC resistance and current capacity of a net, from ODB++ |
| `skills/` | agent skills for the tools: `jlcpcb-release`, `pcb-current-path` |
| `docs/kicad-style.md` | schematic, layout and silkscreen rules |
| `docs/releasing.md` | versions, tags and cutting a release |
| `docs/adopting.md` | taking ee_tooling into a board repository |
| `src/ee_tooling/adopt.py` | `ee-tooling-adopt`: puts ee_tooling in place in a board repository |
| `src/ee_tooling/templates/` | the files `ee-tooling-adopt` writes |

## Use from a board's repository

As a git submodule following `main`, put in place by
`ee-tooling-adopt` (`docs/adopting.md`):

```
uvx --from git+https://github.com/charlieh0tel/ee_tooling ee-tooling-adopt --yes
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
