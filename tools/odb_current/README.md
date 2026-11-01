# odb_current

DC resistance and current-capacity analysis of one net in an ODB++ export.

It builds the net's copper on every layer and couples the layers with via and pin barrels. Then it solves the 2-D conduction problem with a sparse direct solver. Copper comes from two sources:

- **Positive signal layers:** the EDA feature IDs.
- **Negative planes:** the plane region that touches the net's holes, where the holes have no antipad.

```bash
./odb_current.py info  board.tgz --grep 12V
./odb_current.py pins  board.tgz J1 J7
./odb_current.py solve board.tgz --net VIN_12V --from J1:1,3,5 --to J7:2 --to U5 --out out/
```

The solve outputs:

- resistance from `--from` to each `--to`
- current per via barrel
- current-density hotspots with IPC-2221 ratings
- `out/result.json`
- per-layer potential and current-density PNGs

Run `--help` for the options:

| Option | What it sets |
|---|---|
| `--grid` | cell size, mm |
| `--plating` | via barrel plating, mm |
| `--cu LAYER=um` | copper thickness override, µm |
| `--bbox` | crop region, mm |
| `--temps` | extra temperatures to report R at, °C |

Checked on Impulse_IMX8M Rev 3.4, VIN_12V:

| Path | 50 µm grid | Hand-built model |
|---|---|---|
| J1 → J7.2 | 25.0 mΩ | 25.1 mΩ at 25 µm |
| J2 → J7.2 | 96.8 mΩ | 96.2 mΩ at 25 µm |

The Claude Code skill that drives this tool is in `agent/skills/pcb-current-path/`.
