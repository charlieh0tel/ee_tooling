"""A reviewer's findings table, linted before the gate.

The table is Markdown with at least the `REQUIRED` columns, in any
order.  Each row needs a unique ID, a Class from `CLASSES`, a
Rubric/Spec reference, Evidence naming a file, refdes, net, page or
coordinate, and a Consequence.  The lint says what is missing; it does
not judge the content.
"""

import re
from dataclasses import dataclass, field

REQUIRED = ("ID", "Class", "Rubric/Spec", "Finding", "Evidence", "Consequence")
CLASSES = ("Defect", "Spec mismatch", "Rubric miss", "Observation")
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


@dataclass(frozen=True)
class Problem:
    """Row 0 is the header."""

    row: int
    message: str


@dataclass
class Lint:
    """What the lint found: data rows read, and the problems."""

    rows: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def tables(markdown: str) -> list[list[str]]:
    """The Markdown tables in the text: each a list of its `|` lines."""
    found: list[list[str]] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def findings_table(markdown: str) -> list[str] | None:
    """The table whose header has every REQUIRED column, or None."""
    for table in tables(markdown):
        if all(c in cells(table[0]) for c in REQUIRED):
            return table
    return None


def lint(markdown: str) -> Lint:
    """Lints the findings table: the one with the REQUIRED columns.
    Without one, the problem names the columns the first table lacks."""
    found = Lint()
    all_tables = tables(markdown)
    if not all_tables:
        found.problems.append(Problem(0, "no Markdown table"))
        return found
    lines = findings_table(markdown)
    if lines is None:
        missing = [c for c in REQUIRED if c not in cells(all_tables[0][0])]
        found.problems.append(
            Problem(0, f"no findings table; first table lacks: {', '.join(missing)}")
        )
        return found
    header = cells(lines[0])
    column = {name: header.index(name) for name in REQUIRED}
    seen: set[str] = set()
    for n, line in enumerate(lines[TABLE_HEAD:], start=1):
        row = cells(line)
        if len(row) != len(header):
            found.problems.append(Problem(n, f"{len(row)} cells, header has {len(header)}"))
            continue
        found.rows += 1
        ident = row[column["ID"]]
        if not ident:
            found.problems.append(Problem(n, "empty ID"))
        elif ident in seen:
            found.problems.append(Problem(n, f"duplicate ID {ident}"))
        seen.add(ident)
        if row[column["Class"]].removesuffix(SUSPECTED).strip() not in CLASSES:
            found.problems.append(Problem(n, f"Class {row[column['Class']]!r} not in {CLASSES}"))
        if not row[column["Rubric/Spec"]]:
            found.problems.append(Problem(n, "empty Rubric/Spec ('none' for a round 2 item)"))
        if not EVIDENCE.search(row[column["Evidence"]]):
            found.problems.append(Problem(n, "Evidence names no file, refdes, net, page or point"))
        if len(row[column["Consequence"]]) < CONSEQUENCE_MIN:
            found.problems.append(Problem(n, "Consequence missing or too short"))
    return found
