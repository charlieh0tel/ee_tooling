"""An ERC report (`kicad-cli sch erc --format json --severity-all`)
summarized for a reviewer: counts by severity and type, then each
violation on a line, less the types that carry no design information."""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Violation types that only say the symbol library is not configured
# where the export ran.
NOISE = frozenset({"lib_symbol_issues"})
# Item descriptions quoted per violation.
ITEMS_SHOWN = 2


@dataclass(frozen=True)
class Violation:
    """One ERC violation, with the sheet it is on."""

    severity: str
    type: str
    sheet: str
    description: str
    items: tuple[str, ...]

    def line(self) -> str:
        items = "; ".join(self.items[:ITEMS_SHOWN])
        return f"- {self.severity} {self.type} | {self.sheet} | {self.description} | {items}"


def violations(report: dict[str, Any]) -> list[Violation]:
    return [
        Violation(
            severity=str(v.get("severity", "")),
            type=str(v.get("type", "")),
            sheet=str(sheet.get("path", "")),
            description=str(v.get("description", "")),
            items=tuple(str(i.get("description", "")) for i in v.get("items", [])),
        )
        for sheet in report.get("sheets", [])
        for v in sheet.get("violations", [])
    ]


def summary(path: Path) -> str:
    found = violations(json.loads(path.read_text(encoding="utf-8")))
    counts = Counter((v.severity, v.type) for v in found)
    lines = [f"# ERC summary: {path.name}", "", "## Counts by severity and type", ""]
    for (severity, kind), n in sorted(counts.items()):
        noise = " (noise, not listed)" if kind in NOISE else ""
        lines.append(f"- {severity} {kind}: {n}{noise}")
    lines += ["", "## Violations", ""]
    lines += [v.line() for v in found if v.type not in NOISE]
    return "\n".join(lines) + "\n"
