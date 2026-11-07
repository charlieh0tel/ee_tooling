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
uv run design-review findings FILE.md               # lint; exit 1 on problems
uv run design-review punch-list REPORT.md PUNCH.md  # check; exit 1 on problems
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
box, track count and length per layer and width histogram, via count,
kinds (through, blind, buried, micro) and size histogram, every zone
(net, layers, priority, connection, fill, bounding box; keepouts with
their rules), the routing of every
power net and the 25 longest other nets, every
footprint with side, position and angle, fiducials, test points,
mounting holes and DNP parts, and the silkscreen text: footprint
properties (Reference, Value) and texts, and board texts, less the
hidden ones.  A power net is one whose own name (its last hierarchical
part) is a supply or ground (`kicad_style_netclass.POWER_NAME`: +3V3,
-12V, GND, AGND, VDD_IO, VBUS ...), an analog or digital supply (AVDD,
DVCC), a rail written V3V3 or V1P8, VOUT, CHASSIS, or a switch node or
bootstrap (SW, LX, BST, numbered).

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

Lints every findings table in a Markdown file: a table whose header has
`ID` and either `Class` (a reviewer's) or `Severity` (the report's);
other tables (ledgers, adjudication) are skipped.  A reviewer's table
has the columns `ID`, `Class`, `Rubric/Spec`, `Finding`, `Evidence`,
`Consequence`; the report's `ID`, `Severity`, `Rubric/Spec`,
`Finding`, `Evidence` (any order, more allowed).  Each row: an ID
unique in the file; a Class of Defect, Spec mismatch, Rubric miss or
Observation, which may carry `(suspected)`, or a Severity opening with
Blocker, Major, Minor or Note, which may carry tags (`FIRMWARE`,
`(suspected)`); a Rubric/Spec reference (`none` for a round 2 item);
Evidence naming a KiCad file, refdes, net path, coordinate pair, page,
line, source file (`.zen`, `.py`) or evidence file; in a reviewer's
table, a Consequence of 10 characters or more.  Problems print as
`FILE:LINE: problem`; line 0 is the file.

## punch-list: review_punch.py

Checks a punch list against its report: every finding ID the punch
list cites is in the report's findings tables (ID and Severity
columns); every finding at Blocker or Major, after the Adjudication
table's regrading (`Minor -> Major`), is cited on the punch list
unless dropped, waived or struck (its Recommendation cell, the
Adjudication table, or `~~` around its ID); every question number
cited (`question 7`, `questions 7 and 8`) is in the report's
Unresolved questions.  An ID is a token `PREFIX-N` whose prefix one of
the report's IDs has, so a part such as SOT-23 is not one.  Problems
print as `FILE:LINE: problem`, against the punch list or the report.

