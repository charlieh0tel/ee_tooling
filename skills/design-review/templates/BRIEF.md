# BOARD design review: shared brief

You are one reviewer in a panel.  Assume the design has defects and go
looking for them.  Every finding cites evidence from the files below
(file plus reference designator, net, pin, page or coordinate).
Separate what you verified from what you suspect; no speculation stated
as fact.  Report Class and Consequence, never severity.

Read-only: do not modify anything under SCRATCH.  Read-only shell
(grep, python3, kicad-cli) and the Read tool on PDFs and PNGs are fine.

## The design

- BOARD: five lines on what it is and what it drives.
- Source: zen and generator, KiCad version, layer count, size,
  footprint count; design repository and commit.
- Spec: SCRATCH/SPEC.md, design intent; stale where prior-art.md says.

## Files

SCRATCH/: SPEC.md, BOARD.zen, BOARD.kicad_sch and the sheet files
(grep, do not read whole: each has a lib_symbols block),
BOARD.kicad_pcb (large: use board.md, or grep).

SCRATCH/review/:
- nets.txt, components.txt, bom.csv, netlist.net
- erc.md, erc.json, drc.json, drc-parity.json
- sch/BOARD.pdf, sch/pages.txt (page -> sheet), sch/standalone/
- board.md, pcb/positions.csv, pcb/drill/
- png/render-top.png, png/render-bottom.png, png/LAYER.png

Rubric: RUBRIC/pcba/schematic-checklist.md, pcb-checklist.md,
library-checklist.md.  Guidance, not law: an item that does not apply
is N/A with the reason.

Prior art: SCRATCH/review/prior-art.md.  Do not re-report what it
settles; build on it, or say why a settlement no longer holds.

## Conventions

- Net names: schematic `/SHEET/NET`, board `SHEET.NET`.
- Modules: NAME (what), ...

## Already known

1. ...

## Spec vs. board discrepancies noticed, not yet investigated

- ...

## Output (ASCII, US English, concise)

1. `## Rubric ledger`: table `Item | Result | Evidence`, Result one of
   pass, fail, N/A, not verifiable; every item of your sections.
2. `## Spec ledger`: table `Spec section | Result | Evidence`.
3. `## Findings`: table `ID | Class | Rubric/Spec | Finding | Evidence
   | Consequence`.  ID `X-n` with your letter.  Class: Defect (will not
   work, unsafe, damages a part), Spec mismatch, Rubric miss,
   Observation.  Consequence: what fails, when, by how much.
   `(suspected)` in Finding when a datasheet or system fact is needed.
4. `## Checked and OK`: what you verified clean, one line each.
5. `## Could not verify`: what needs a datasheet, drawing, harness or
   missing source.
6. `## Unresolved questions`: for the design owner.
