---
name: pcb-current-path
description: Compute DC resistance, IR drop, current density hotspots and current-carrying capacity of a PCB net between two connectors or parts, from a fab package (ODB++, Gerbers, stackup). Use when asked for trace/plane/pour resistance, voltage drop, "how much current can this path carry", ampacity, or bottlenecks between two refdes (e.g. "resistance of V12_IN from J7 when fed from J1").
---

# PCB current path: resistance and capacity

The solver is `~/src/ee_stuff/tools/odb_current/odb_current.py`. It's a PEP 723 script, so run it directly and uv supplies numpy, scipy, shapely and matplotlib. It reads **ODB++**, because ODB++ carries net names, component pins and per-layer net attribution. Gerbers alone don't.

## 1. Find the data

- Look for an `ODB/*.tgz`, `*.tgz` or extracted `odb/` directory in the fab package. The script takes the archive directly.
- **If there are only Gerbers:** say so and ask for the ODB++ export (or IPC-2581). A Gerber-only analysis means hand-tracing nets. Don't attempt that silently.
- **Stackup:** the script reads copper thickness from the ODB layer attributes. Altium writes `.copper_weight` in µm, and the script assumes µm when the value is > 5. Cross-check against the stackup table in the fab or assembly drawing PDF with `pdftotext -layout ... | grep -A30 Thickness`. If they disagree, override with `--cu pwr_1=17.5 ...` (µm).

## 2. Identify the net and the terminals

```bash
T=~/src/ee_stuff/tools/odb_current/odb_current.py
$T info  ODB.tgz --grep '12V|VIN'     # stackup, polarity, matching nets
$T pins  ODB.tgz J1 J7                 # pin -> net, coordinates, side
```

- **The user's net name may not match the design.** For example, "V12_IN" is actually `VIN_12V`. Confirm it by checking which net the named connector pins are on, and tell the user the real name.
- **Terminals** are written `REF:pin,pin`. A bare `REF` means all of that part's pins on the net. All pins in one terminal are tied together, which is the right model for paralleled connector pins.
- **Check design intent.** Ask, or infer from the schematic, which connector is the designed input and which are outputs. Read the protection parts in the chain (fuses, TVS, bulk caps) in that light. Don't lecture about missing protection on a path the user has deliberately chosen.

## 3. Solve

```bash
$T solve ODB.tgz --net VIN_12V --from J1:1,3,5 --to J7:2 --to U5 --to C1:1 --out OUTDIR
```

- **Outputs:**
  - resistance from `--from` to each `--to`, at 20 °C and at `--temps` (default 60 °C)
  - barrel segments carrying current (via share and an IPC-2221 rating)
  - current-density hotspots per layer, weakest first
  - `OUTDIR/result.json`
  - `OUTDIR/<layer>.png`: potential map and log current-density map
- **Several `--to` targets** reuse one factorization, which makes a "J1 to every load" table cheap.
- **Grid:** the default 50 µm uses about 1.5 GB and takes about 20 s for a 190 × 55 mm 10-layer board. Check convergence with `--grid 0.025` (about 4× the memory, ~1 min). On a real board the two agreed within 1%. If RAM is tight, swap is fine. `--bbox` crops, but only crop off true dead-ends: a crop that cuts a parallel loop changes the answer.
- **Look at the plots** (Read the PNGs) before trusting the numbers. Confirm the current goes where you expect and the plane regions look sane.

## 4. Sanity checks

- **stderr lists each negative plane region** it found connected to the net. A `WARNING: other-net holes also connect` line means the plane reconstruction is wrong, usually because the plane belongs to another net. Investigate it, don't report through it.
- **Negative plane layers:** a via with *no* antipad on a negative plane is direct-connected. A `thr…` feature is a thermal relief, with copper spokes in the gaps. A net whose holes have antipads on every GND layer but are missing them on one PWR layer means that layer has a split region for the net. Often that region *is* the main current path, even when the outer-layer copper looks unconnected.
- **Single small vias, thermal reliefs and antipad rows (via fences, connector pin fields)** that cut across a plane strip are the usual bottlenecks. Zoom into them by plotting the net geometry, as in `odbgeo`-style plots.
- **Dead-end branches carry no current.** Parallel paths, such as current dropping through pours and returning, are handled automatically.

## 5. Interpret capacity

- **Hotspot "w_eff"** is 1/(A per mm) at a local maximum. It includes current crowding at corners, so the IPC-2221 figures derived from it are conservative, and corner values move with the grid.
- **IPC-2221 is very conservative** for short features heat-sunk into planes inside a multilayer board. Report:
  1. the conservative IPC-2221 number at +10 / 20 / 30 °C
  2. an engineering estimate, usually 1.5–2× IPC-2221 for short pinches. Long narrow runs deserve closer to the IPC number.
  3. a recommendation to verify with a thermal test above the estimate.
- **Call out shared bottlenecks.** For example, one input via that also carries every other load's current.

## 6. Report

- Lead with the resistance at 20 °C (and hot), mV per amp, the conservative rating and the estimate.
- Give a series breakdown of the path: pads → vias → plane sections → thermal → pin, with mΩ for each. Sample the potential maps or `result.json` at points along the path for the segment drops.
- Add a bottleneck table: feature, location, cross-section in mil², IPC-2221 at +10/20/30 °C.
- **State the assumptions:**
  - via plating 20 µm (Class 2 average; `--plating`)
  - ρ = 1.724 µΩ·cm at 20 °C, +0.393 %/°C
  - copper thickness and its source
  - pins tied together
- **Offer an artifact** with the plots when the result will be shared. Copy the PNGs from OUTDIR and publish them as supporting files.
