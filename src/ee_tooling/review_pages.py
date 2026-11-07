"""The pages of a schematic PDF (`kicad-cli sch export pdf`) mapped to
their sheets, from the title block text poppler's pdftotext reads."""

import re
import subprocess
from pathlib import Path

# The title-block strings KiCad prints on a page: a sheet path runs to
# the end of its line or a run of spaces (the title block's padding); a
# file name runs to its first .kicad_sch, spaces and all.
LABEL = re.compile(r"(Sheet: */.*?(?=\s{2,}|$)|File: *\S.*?\.kicad_sch)", re.M)
# Labels kept per page: the sheet path and the file.
LABELS_KEPT = 2
# pdfinfo's page count line.
PAGE_COUNT = re.compile(r"^Pages:\s+(\d+)", re.M)


class PagesError(Exception):
    """A PDF poppler could not read, or poppler not on the path."""


def poppler(cmd: list[str]) -> str:
    """A poppler tool's stdout; a failure, or the tool missing, is a
    PagesError naming the command."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except FileNotFoundError as e:
        raise PagesError(f"{cmd[0]}: not on the path") from e
    except subprocess.CalledProcessError as e:
        raise PagesError(f"{' '.join(cmd)}: exit {e.returncode}: {e.stderr.strip()}") from e


def page_count(pdf: Path) -> int:
    m = PAGE_COUNT.search(poppler(["pdfinfo", str(pdf)]))
    if m is None:
        raise PagesError(f"pdfinfo gave no page count for {pdf}")
    return int(m.group(1))


def page_text(pdf: Path, page: int) -> str:
    return poppler(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"])


def labels(text: str) -> list[str]:
    """A page's sheet and file labels, in the order printed."""
    return LABEL.findall(text)[:LABELS_KEPT]


def page_map(pdf: Path) -> list[str]:
    """`page-NN: Sheet: /PATH File: NAME.kicad_sch`, one line a page."""
    return [
        f"page-{i:02d}: {' '.join(labels(page_text(pdf, i)))}"
        for i in range(1, page_count(pdf) + 1)
    ]
