"""Findings tables, linted before the gate and in the report.

A findings table is a Markdown table whose header has an ID column and
a Class column (a reviewer's table, `REVIEWER`) or a Severity column
(the report's, `REPORT`); every other table is skipped.  Each needs its
format's columns, in any order.  Each row needs an ID unique in the
file, a Class from `CLASSES` or a Severity from `SEVERITIES`, a
Rubric/Spec reference, Evidence naming a file, refdes, net, page or
coordinate, and, in a reviewer's table, a Consequence.  The lint says
what is missing; it does not judge the content.
"""

import re
from dataclasses import dataclass, field

# A reviewer's findings table: its columns, and the Class values.
REVIEWER = ("ID", "Class", "Rubric/Spec", "Finding", "Evidence", "Consequence")
CLASSES = ("Defect", "Spec mismatch", "Rubric miss", "Observation")
# The report's findings table: its columns, and the Severity values, which
# open the cell and may be followed by tags (FIRMWARE, `SUSPECTED`).
REPORT = ("ID", "Severity", "Rubric/Spec", "Finding", "Evidence")
SEVERITIES = ("Blocker", "Major", "Minor", "Note")
# A Class may carry this suffix: the brief asks for it in the Finding, but
# reviewers put it on the Class too.
SUSPECTED = "(suspected)"
# Evidence must name one of: a KiCad file, a refdes, a net path, a
# coordinate pair, a page, a line, a source file or a review evidence file.
# A net path's slash follows no letter or digit, so "and/or" is not one.
EVIDENCE = re.compile(
    r"\.kicad_(sch|pcb)|\b[A-Z]{1,3}\d{1,4}\b|(?<![A-Za-z0-9])/[A-Za-z0-9_]+"
    r"|\(\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*\)|\bpage\s*\d+|\bline\s*\d+"
    r"|\.(txt|md|csv|json|zen|py)\b"
)
# A consequence shorter than this is a label, not a consequence.
CONSEQUENCE_MIN = 10
# Table lines before the first data row: the header and its rule.
TABLE_HEAD = 2
# A cell separator, and a `|` written inside a cell.
UNESCAPED_BAR = re.compile(r"(?<!\\)\|")
ESCAPED_BAR = "\\|"


@dataclass(frozen=True)
class Problem:
    """A problem at a line of the file, counted from 1; 0 is the file."""

    line: int
    message: str


@dataclass
class Lint:
    """What the lint found: findings tables and data rows read, and the problems."""

    tables: int = 0
    rows: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def cells(line: str) -> list[str]:
    """A table row's cells; `\\|` is a `|` inside a cell, as in GitHub's
    Markdown."""
    row = line.strip().removeprefix("|")
    if row.endswith("|") and not row.endswith(ESCAPED_BAR):
        row = row[:-1]
    return [c.strip().replace(ESCAPED_BAR, "|") for c in UNESCAPED_BAR.split(row)]


def tables(markdown: str) -> list[tuple[int, list[str]]]:
    """The Markdown tables in the text: each its first line's number,
    from 1, and its `|` lines."""
    found: list[tuple[int, list[str]]] = []
    current: list[str] = []
    for n, line in enumerate(markdown.splitlines(), start=1):
        if line.strip().startswith("|"):
            if not current:
                first = n
            current.append(line)
        elif current:
            found.append((first, current))
            current = []
    if current:
        found.append((first, current))
    return found


def lint(markdown: str) -> Lint:
    """Lints every findings table; IDs are unique across them."""
    found = Lint()
    seen: set[str] = set()
    for first, lines in tables(markdown):
        header = cells(lines[0])
        if "ID" not in header:
            continue
        required: tuple[str, ...]
        if "Class" in header:
            required = REVIEWER
        elif "Severity" in header:
            required = REPORT
        else:
            continue
        found.tables += 1
        missing = [c for c in required if c not in header]
        if missing:
            found.problems.append(Problem(first, f"findings table lacks: {', '.join(missing)}"))
            continue
        column = {name: header.index(name) for name in required}
        for n, line in enumerate(lines[TABLE_HEAD:], start=first + TABLE_HEAD):
            row = cells(line)
            if len(row) != len(header):
                found.problems.append(Problem(n, f"{len(row)} cells, header has {len(header)}"))
                continue
            found.rows += 1
            found.problems += [Problem(n, m) for m in row_problems(row, column, seen)]
    if not found.tables:
        found.problems.append(Problem(0, "no findings table (an ID column and Class or Severity)"))
    return found


def row_problems(row: list[str], column: dict[str, int], seen: set[str]) -> list[str]:
    """A data row's problems; its ID joins seen."""
    found = []
    ident = row[column["ID"]]
    if not ident:
        found.append("empty ID")
    elif ident in seen:
        found.append(f"duplicate ID {ident}")
    seen.add(ident)
    if "Class" in column:
        kind = row[column["Class"]]
        if kind.removesuffix(SUSPECTED).strip() not in CLASSES:
            found.append(f"Class {kind!r} not in {CLASSES}")
    else:
        severity = row[column["Severity"]]
        if not severity.split()[:1] or severity.split()[0] not in SEVERITIES:
            found.append(f"Severity {severity!r} does not open with one of {SEVERITIES}")
    if not row[column["Rubric/Spec"]]:
        found.append("empty Rubric/Spec ('none' for a round 2 item)")
    if not EVIDENCE.search(row[column["Evidence"]]):
        found.append("Evidence names no file, refdes, net, page or point")
    if "Consequence" in column and len(row[column["Consequence"]]) < CONSEQUENCE_MIN:
        found.append("Consequence missing or too short")
    return found
