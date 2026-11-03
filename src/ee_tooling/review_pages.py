"""The pages of a schematic PDF (`kicad-cli sch export pdf`) mapped to
their sheets, from the title block text poppler's pdftotext reads."""

import re
import subprocess
from pathlib import Path

# The title-block strings KiCad prints on a page: a sheet path runs to
# the end of its line or a run of spaces (the title block's padding).
LABEL = re.compile(r"(Sheet: */.*?(?=\s{2,}|$)|File: *\S+\.kicad_sch)", re.M)
# Labels kept per page: the sheet path and the file.
LABELS_KEPT = 2
PAGE_COUNT = re.compile(r"^Pages:\s+(\d+)", re.M)


def page_count(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], check=True, capture_output=True, text=True)
    m = PAGE_COUNT.search(out.stdout)
    if m is None:
        raise ValueError(f"pdfinfo gave no page count for {pdf}")
    return int(m.group(1))


def page_text(pdf: Path, page: int) -> str:
    cmd = ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def labels(text: str) -> list[str]:
    """A page's sheet and file labels, in the order printed."""
    return LABEL.findall(text)[:LABELS_KEPT]


def page_map(pdf: Path) -> list[str]:
    """`page-NN: Sheet: /PATH File: NAME.kicad_sch`, one line a page."""
    return [
        f"page-{i:02d}: {' '.join(labels(page_text(pdf, i)))}"
        for i in range(1, page_count(pdf) + 1)
    ]
