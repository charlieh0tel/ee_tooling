# design-review

`design-review` makes the evidence an adversarial design review starts
from, and lints a reviewer's findings before the gate.  The process it
serves is `skills/design-review/SKILL.md`.  It needs `kicad-cli`
(KiCad 10) and poppler (`pdfinfo`, `pdftotext`, `pdftoppm`) on the path.

```
uv run design-review evidence DIR NAME OUT         # everything below, into OUT
uv run design-review netlist NETLIST OUT            # nets.txt, components.txt
uv run design-review board NAME.kicad_pcb           # board summary, stdout
uv run design-review erc erc.json                   # ERC summary, stdout
uv run design-review pages NAME.pdf                 # PDF page -> sheet, stdout
uv run design-review pairs NAME.kicad_pcb CAN_H,CAN_L  # pair routing gaps
uv run design-review findings TABLE.md              # lint; exit 1 on problems
```

## evidence: review_evidence.py

From `DIR/NAME.kicad_sch` and `DIR/NAME.kicad_pcb` into `OUT`:

| File | Source |
|---|---|
| `erc.json`, `erc.md` | `kicad-cli sch erc --severity-all`; the summary drops `lib_symbol_issues` |
| `netlist.net`, `nets.txt`, `components.txt` | `sch export netlist`, condensed (below) |
| `bom.csv` | `sch export bom`, grouped by value, footprint and MPN |
| `sch/NAME.pdf`, `sch/*.svg` | `sch export pdf`, `sch export svg` |
| `drc.json`, `drc-parity.json` | `pcb drc --severity-all`, without and with `--schematic-parity` |
| `pcb/*.svg` | each of the board's copper layers, then mask, paste, silk and fab, one a file with Edge.Cuts |
| `pcb/positions.csv`, `pcb/drill/` | `pcb export pos`, `pcb export drill` with a PDF map |
| `png/render-top.png`, `png/render-bottom.png` | `pcb render`, 3600 x 2000 |
| `png/LAYERS.png` | `pcb export pdf` of each copper layer, then silk with fab, F.Cu with silk, mask with paste; rasterized at 220 dpi |
| `board.md` | the board summary (below) |

A failed export, or a tool not on the path, is printed on stderr and
the rest run; a summary whose source was not exported (`nets.txt` and
`components.txt`, `erc.md`) is skipped, on stderr; those files, and
`erc.json` and `netlist.net`, are removed before a run, so none is
left from an earlier one.  The exit status is 1 if any export failed.
`--dry-run` prints the commands and writes nothing.

## netlist: review_netlist.py

`nets.txt`: `NAME: PINCOUNT : REF.PIN(FUNCTION/TYPE) ...`, one net a
line, sorted by name.  `components.txt`: `REF | VALUE | FOOTPRINT |
SHEET | MPN | FLAGS`, sorted by reference; flags are `dnp` and
`exclude_from_bom`.

## board: review_board.py

Thickness, copper layers, stackup, setup clearances, outline bounding
box, track count and length per layer and width histogram, via count
and size histogram, every zone (net, layers, priority, connection,
fill, bounding box; keepouts with their rules), the routing of every
power net (`POWER_NET`) and the 25 longest other nets, every
footprint with side, position and angle, fiducials, test points,
mounting holes and DNP parts, and the silkscreen text: footprint
properties (Reference, Value) and texts, and board texts, less the
hidden ones.

## pairs: review_pairs.py

Each net's length per layer and widths.  For each pair and layer: the
first net's length, and the edge-to-edge gap from each of its tracks
(segments and arcs, 0.5 mm and longer; an arc in chords) to the
nearest track of the second net on that layer: minimum, median
(weighted by length), maximum, and the share of length with a gap of
0.3 mm or less.  A pair routed as a pair shows a near-constant gap and
a high share.

## pages: review_pages.py

`page-NN: Sheet: /PATH/ File: NAME.kicad_sch` from each page's title
block, for pointing a reviewer at a page.

## findings: review_findings.py

A Markdown table with the columns `ID`, `Class`, `Rubric/Spec`,
`Finding`, `Evidence`, `Consequence` (any order, more allowed).  Each
row: a unique ID; a Class of Defect, Spec mismatch, Rubric miss or
Observation; a Rubric/Spec reference (`none` for a round 2 item);
Evidence naming a KiCad file, refdes, net path, coordinate pair, page,
line, source file (`.zen`, `.py`) or evidence file; a Consequence of
10 characters or more.  A Class may carry `(suspected)`.
The findings table is the one whose header has those columns; other
tables (ledgers) are ignored.  Problems print as `FILE: row N: problem`;
row 0 is the header.
