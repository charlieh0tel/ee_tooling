# JLCPCB

A board with `profile = "jlcpcb"` in `fab.toml` (`SKILL.md`).

## Options

```toml
[board.jlcpcb]
require_lcsc = true    # default false
```

- `require_lcsc`: a fitted part with no LCSC number refuses the release,
  naming each, before anything is written.  Without it, such a part is a
  warning naming it, on stderr (with the variant in a variant's build),
  as is a part whose placement correction is not known yet.

A fitted part in its BOM and not in KiCad's placement export (excluded
from position files, or not on the board) refuses the release, naming
each, before anything is written.

`adopt` writes its house `[board.fab]` values: IPC class 2, FR-4, Tg
130 deg C, a 100% netlist test per IPC-9252.  Check them against the
order's options (material, Tg) before ordering.

## Its files

| File | Use |
|---|---|
| `NAME-REV-gerbers-jlcpcb.zip` | the PCB order's upload: Protel extensions, no X2, soldermask subtracted from silk; Excellon in mm, PTH and NPTH separate, absolute origin |
| `NAME-REV-jlcpcb-bom.csv` | the assembly order's BOM upload: Comment, Designator, Footprint, LCSC Part #; not-fitted parts left out |
| `NAME-REV-jlcpcb-cpl.csv` | the assembly order's placement upload: Designator, Val, Package, Mid X, Mid Y, Rotation, Layer; only parts in the BOM, with `parts.toml`'s corrections |

IPC-2581's distributor is LCSC, its part number the `LCSC` field.

Upload `NAME-REV-jlcpcb-bom.csv`, not the house BOM, `NAME-REV-bom.csv`,
whose columns JLCPCB does not read.

The Gerber zip is the PCB order's upload: JLCPCB's help forum says it
takes Gerbers, not ODB++
(https://jlc3dp.com/help/answers/detail/197-Can-ODB++-files-be-used-to-order,
2023-12).  Check the order page before uploading the ODB++ or IPC-2581
instead.

## Every order

- In the BOM preview, compare each matched part's MPN and package with
  the BOM's.
- Check stock for the scarce parts.
- Check the DFM review before paying.

## Rotations and offsets

JLCPCB draws each LCSC part from its own model, whose zero can differ
from KiCad's footprint, so corrections go by LCSC part and hold for
every board: `src/ee_tooling/parts.toml` in ee_tooling.  In JLCPCB's
placement preview, compare each part's magenta pin-1 dot with our pin 1
(silk mark or pad 1):

- turned: add the LCSC part and degrees to `[rotation]` (JLCPCB's dot
  90 deg counter-clockwise from ours: -90);
- shifted: add the LCSC part and the shift in mm to `[offset]`, in the
  part's own frame (KiCad rotation 0, y up);
- a part not yet seen in a preview goes in `[unchecked]`, and the tool
  says to check it.

Any other table, or a value not of its form (a rotation a finite
number, an offset `[x, y]`, a description a string), stops the release.

Both are in the part's own frame, as seen on the top.  KiCad places a
bottom-side footprint as its library footprint mirrored in y, then
turned by its rotation; so on the bottom the offset's y is negated
before it is turned, and the rotation correction is subtracted from
KiCad's rotation, not added.

Then commit in ee_tooling, update the board repository's copy, and
rebuild with `--force`.  A screenshot of the preview can be measured
against two features a known distance apart, such as mounting holes.

Passives, LEDs and SOD diodes have needed no correction so far.
Coordinates are KiCad's absolute ones (Y negative), which line up with
the Gerbers in JLCPCB's preview.
