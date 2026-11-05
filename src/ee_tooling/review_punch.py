"""A punch list checked against its report (skills/design-review/SKILL.md,
sections 8 and 9):

- every finding ID it cites is a finding of the report;
- every report finding at `MUST_SEVERITIES`, after adjudication, is on
  it, unless dropped, waived or struck in its Recommendation, its ID
  cell or the Adjudication table;
- every question number it cites is in the report's Unresolved
  questions.

An ID is a token PREFIX-N whose prefix one of the report's finding IDs
has, so a part such as SOT-23 is not taken for one.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from ee_tooling.review_findings import SEVERITIES, TABLE_HEAD, Problem, cells, tables

# Severities, after adjudication, that the punch list must carry.
MUST_SEVERITIES = frozenset({"Blocker", "Major"})
# A token that may be a finding ID.
ID = re.compile(r"\b([A-Z]+)-(\d+)\b")
# A finding taken out in adjudication.
DROPPED = re.compile(r"\b(dropped|waived|struck)\b", re.I)
# Strikethrough around an ID.
STRUCK = "~~"
# A severity changed in adjudication: `Minor -> Major`.
REGRADED = re.compile(rf"\b({'|'.join(SEVERITIES)})\s*->\s*({'|'.join(SEVERITIES)})\b")
# A column of the Adjudication table, which no findings table has.
DECISION = "Decision"
# The report's section of numbered open questions.
QUESTIONS = re.compile(r"^##\s+Unresolved questions\s*$", re.I)
QUESTION_ITEM = re.compile(r"^\s*(\d+)\.\s")
# A question cited by number: `question 7`, `questions 7 and 8`; only the
# plural takes a list, so `question 6, 16.5 V` cites 6 alone.
QUESTION_CITE = re.compile(
    r"\bquestion\s+(\d+)|\bquestions\s+(\d+(?:(?:\s*,\s*|\s+and\s+|\s+or\s+)\d+)*)", re.I
)


@dataclass
class Finding:
    """A report finding: where it is, its severity after adjudication, and
    whether adjudication took it out."""

    line: int
    severity: str
    dropped: bool


@dataclass
class Report:
    """What the punch list is checked against."""

    findings: dict[str, Finding] = field(default_factory=dict)
    questions: set[int] = field(default_factory=set)

    @property
    def prefixes(self) -> set[str]:
        return {m.group(1) for i in self.findings if (m := ID.fullmatch(i))}

    def ids(self, text: str) -> Iterator[str]:
        """The finding IDs in text."""
        prefixes = self.prefixes
        return (m.group(0) for m in ID.finditer(text) if m.group(1) in prefixes)


def report(markdown: str) -> Report:
    found = Report()
    adjudication: list[list[str]] = []
    for first, lines in tables(markdown):
        header = cells(lines[0])
        if DECISION in header:
            adjudication += [cells(line) for line in lines[TABLE_HEAD:]]
            continue
        if "ID" not in header or "Severity" not in header:
            continue
        column = {name: header.index(name) for name in header}
        for n, line in enumerate(lines[TABLE_HEAD:], start=first + TABLE_HEAD):
            row = cells(line)
            if len(row) != len(header):
                continue
            ident = row[column["ID"]]
            words = row[column["Severity"]].split()
            recommendation = row[column["Recommendation"]] if "Recommendation" in column else ""
            found.findings[ident.strip(STRUCK).strip()] = Finding(
                n,
                words[0] if words else "",
                STRUCK in ident or bool(DROPPED.search(recommendation)),
            )
    for row in adjudication:
        rest = " ".join(row[1:])
        for ident in found.ids(row[0]):
            finding = found.findings.get(ident)
            if finding is None:
                continue
            finding.dropped |= bool(DROPPED.search(rest))
            regraded = REGRADED.findall(rest)
            if regraded:
                finding.severity = regraded[-1][1]
    in_questions = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_questions = bool(QUESTIONS.match(line))
        elif in_questions and (m := QUESTION_ITEM.match(line)):
            found.questions.add(int(m.group(1)))
    return found


def check(report_markdown: str, punch_markdown: str) -> tuple[list[Problem], list[Problem]]:
    """The problems: in the punch list, and in the report."""
    known = report(report_markdown)
    if not known.findings:
        return [], [Problem(0, "no findings table (an ID column and Severity) in the report")]
    in_punch: list[Problem] = []
    cited: set[str] = set()
    for n, line in enumerate(punch_markdown.splitlines(), start=1):
        for ident in known.ids(line):
            cited.add(ident)
            if ident not in known.findings:
                in_punch.append(Problem(n, f"{ident} is not a finding of the report"))
        for m in QUESTION_CITE.finditer(line):
            for number in map(int, re.findall(r"\d+", m.group(1) or m.group(2))):
                if number not in known.questions:
                    in_punch.append(
                        Problem(n, f"question {number} is not in the report's Unresolved questions")
                    )
    in_report = [
        Problem(f.line, f"{ident} ({f.severity}) is not on the punch list")
        for ident, f in known.findings.items()
        if f.severity in MUST_SEVERITIES and not f.dropped and ident not in cited
    ]
    return in_punch, in_report
