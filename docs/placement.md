# Placement score

`placement-score` measures a board's placement, to compare one
placement with another and to catch broken rules.  From a board
repository's `hardware/`:

```
uv run placement-score NAME/NAME.kicad_pcb --render placed.png
```

It reads the board with KiCad's `pcbnew`, so the venv is the one
`adopt` makes, with system site packages (`docs/adopting.md`).

## What it measures

| Line | What |
|---|---|
| `crossings` | crossings between different nets' ratsnest lines; each net's ratsnest is its pads' minimum spanning tree, poured nets left out |
| `ratsnest` | the ratsnest's total length, mm |
| `overlaps` | courtyards that share area, by bounding box on the footprint's side; touching is not overlapping |
| `problems` | rules broken: a decoupling capacitor farther from its pin than a larger one on the same pin (`docs/kicad-style.md`), a value that is not a capacitance, a reference, pin or net that is not there |
| `decoupling C` | the capacitor's distance to its part's supply pin and to its ground pin, each from its pad on that pin's net |
| `cross A B n` | how many times nets A and B cross |

It exits non-zero on an overlap or a problem; crossings and length are
a score, not a rule, and secondary to the placement rules in
`docs/kicad-style.md`.  `--render PNG` draws the outline, courtyards,
pads (poured nets' in green), references and each net's ratsnest in
its own color, at `--scale` px per mm (16).

## placement.toml

Beside the board, or named by `--rules`:

```toml
# Nets poured as planes, left out of the ratsnest.
poured = ["GND"]

# One per capacitor: the part it decouples, the supply pin it serves and
# the part's ground pin, by pin number.
[[decoupling]]
capacitor = "C1"
part = "U1"
supply = "14"
ground = "7"
```

The capacitor's value (its Value field: `100n`, `4.7uF 25V X5R`,
`4u7`) orders capacitors on one supply pin, smallest closest.
