---
name: design-review
description: Adversarial design review of a KiCad board (schematic, layout, mistake-proofing) against its spec and the ee-dev-process checklists, with a panel of reviewers, a gate that keeps "could do" noise and settled points out of the report, a severity-ordered punch list, an adjudication interview and a decision log. Use when asked to review a board, re-review one after fixes, or triage reviewer output.
---

# Design review

Review only: never edit the design, its spec, its zen or its KiCad
files.  Every step works on a scratch copy.  Tools: `design-review`
(ee_tooling, `docs/design-review.md`), `kicad-cli` 10, poppler, and
`codex` for the independent passes.  Rubric: the ee-dev-process
checklists (`pcba/schematic-checklist.md`, `pcba/pcb-checklist.md`,
`pcba/library-checklist.md`); they are guidance, not law.

Outputs go in the review repository under `BOARD/docs/reviews/`:
`YYYY-MM-DD-design-review.md`, `YYYY-MM-DD-punch-list.md`,
`prior-art.md`, `candidates.md`, `decisions.md`.  One commit per
change, no AI attribution.

## 1. Pin the design

- Design repository, commit, branch, and `git status` (dirty files
  listed).  The export used: a committed `schematic/` and `layout/`,
  else a vendor drop; say which.
- Copy to a scratch directory; if the board is `layout.kicad_pcb`,
  repair it there with `kicad-layout-repair`.  All later paths are the
  scratch copy.
- Record all of this in the report header (`templates/report.md`).

## 2. Prior art

Build `prior-art.md` (`templates/prior-art.md`) before any reviewer
runs.  Sources, each read in full: the design repository's `README`,
`docs/`, spec, zen comments and commit messages; this repository's
earlier reports and `decisions.md` for the board; sibling boards'
`decisions.md` for house rules.  One line per settled point: what,
where decided, the citation.  Evidence that something was decided
counts as settled, even without a rationale.

## 3. Evidence

```
uv run --project PATH/TO/ee_tooling design-review evidence SCRATCH NAME SCRATCH/review
uv run --project PATH/TO/ee_tooling design-review pages SCRATCH/review/sch/NAME.pdf > SCRATCH/review/sch/pages.txt
```

Sheets the root does not instantiate (ERC `label_dangling`, parity
`extra_footprint`) get a standalone `sch export pdf` and `netlist`
under `review/sch/standalone/`.  Read `erc.md`, `drc*.json` and
`board.md` yourself before writing the brief: the known issues go in
it.

## 4. Brief

`templates/BRIEF.md`, filled in: the design in five lines, the file
index, naming conventions (hierarchical vs dotted net names, module
names), `prior-art.md` by reference, spec-vs-board discrepancies
already noticed, and the output format.  Reviewers report Class and
Consequence, never severity.

## 5. Round 1: spec and rubric

Scope is fixed: does the design meet its spec, and does it meet the
rubric.  Derive the domains from the sheet list and the zen module
tree (power, MCU and digital, connectors and buses, analog, actuators,
layout are the usual cut); confirm the split with the owner, then fan
out in parallel, each reviewer with `templates/reviewer.md`: the
brief, its domain's sheets and pages, the rubric sections and spec
sections it walks, and the specific things to attack.  Codex runs
whole-schematic and whole-layout (`templates/codex-schematic.md`,
`templates/codex-layout.md`, read-only sandbox, layer PNGs attached to
the layout pass).  Each reviewer returns a rubric ledger (item: pass,
fail, N/A, not verifiable; evidence), a spec ledger, a findings table,
Checked and OK, Could not verify, Unresolved questions.

Lint every reviewer's output before reading it:
`design-review findings REVIEWER.md`.

## 6. Gate

A finding enters the report only if all four hold:

1. Evidence names a file and a refdes, net, page or coordinate.
2. Consequence is specific: what fails, under what condition, by how
   much.
3. Not in `prior-art.md`, or it says why the settlement no longer
   holds.
4. You reproduced it from the netlist, board file or render yourself.
   The file wins over any reviewer.

Everything else:

- Observation without a failed rubric item or spec line: `candidates.md`,
  with the reviewer's text and the reason it stopped at the gate.
- Settled: the report's "Previously settled" section, with the citation.
- Rubric miss that does not apply to this board: the ledger says N/A
  with the reason.

Round 1 reviewers never see `candidates.md`; you use it for dedupe.

## 7. Severity

Assigned by you, after the gate, once per finding: Blocker (will not
work, unsafe), Major (likely field, safety or yield problem), Minor
(should fix), Note (document or decide).  `(suspected)` when a
datasheet, harness or system fact is needed.  Reviewer counts do not
matter.

## 8. Report and punch list

Report: `templates/report.md`.  IDs by area: S schematic, L layout, K
mistake-proofing, D documentation.  Findings tables by area, then
Previously settled, Checked and OK, Could not verify, Adjudication,
Unresolved questions, Reproduction (every command and measurement a
reader needs to repeat a number), the ledgers as an appendix.  Lint
it: `design-review findings REPORT.md`.

Punch list: `templates/punch-list.md`, derived from the report, Must
(Blocker, Major), Should (Minor), Could (Note), Nits (cosmetic
whatever the severity), Open questions.  Design actions only: no
bring-up or firmware to-do items; a firmware consequence is a finding
tagged FIRMWARE.  `[ ]` open, `[x]` done, `[-]` waived with the reason.

## 9. Adjudication

Interview the owner: one question per failed-but-contestable rubric
item and per open question, in punch-list order, with the finding, the
evidence and your recommendation.  Each decision goes in
`decisions.md` (`templates/decisions.md`) with the date, then into the
report's Adjudication table and inline on the finding, and the punch
list, each in its own commit.

- Dropped: the item is deleted from the punch list; it stays in the
  report and `decisions.md`.
- "Must check, no change yet": a Must outcome; prove the point (by
  calculation, datasheet or bench), and change parts only if it fails.
- Answered questions are deleted from the report's list and the rest
  renumbered from 1.  Every "question N" in `decisions.md`, the punch
  list and the report's rows is updated in the same commit; a
  reference to an answered question is reworded without a number.

## 10. Round 2, optional

An open-ended adversarial pass: what the spec and rubric do not cover.
Reviewers get the brief, `prior-art.md` and `candidates.md` as seed,
and the same output format with Rubric/Spec `none`.  The same gate; what
passes joins the report with the next free IDs.

## 11. Re-review

On a new design commit: steps 1 to 7 again, then a diff against the
last report: fixed (cite the fixing commit), still open, new, and
decisions carried forward.  The diff is a new dated report that links
the old one; the punch list is updated in place.

## Unresolved questions

List them at the end of every message, and in the report.
