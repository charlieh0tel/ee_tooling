import shutil
from pathlib import Path

import pytest

from ee_tooling import review_pages
from ee_tooling.review_pages import PagesError, labels, page_map, poppler

PAGE = """   Widget
Sheet: /PWR/
File: PWR.kicad_sch   Sheet 36/51
"""


def test_labels() -> None:
    assert labels(PAGE) == ["Sheet: /PWR/", "File: PWR.kicad_sch"]


def test_labels_spaces_and_hyphens() -> None:
    page = "Sheet: /Power Supply/Buck-5V/   Size: A4\nFile: buck-5v.kicad_sch   Sheet 3/9\n"
    assert labels(page) == ["Sheet: /Power Supply/Buck-5V/", "File: buck-5v.kicad_sch"]


def test_labels_empty() -> None:
    assert labels("nothing here") == []


def test_labels_file_with_spaces() -> None:
    page = "Sheet: /Power/   Size: A4\nFile: power supply.kicad_sch   Sheet 3/9\n"
    assert labels(page) == ["Sheet: /Power/", "File: power supply.kicad_sch"]


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="poppler not on the path")
def test_missing_pdf(tmp_path: Path) -> None:
    """pdfinfo failing is a PagesError."""
    with pytest.raises(PagesError, match=r"^pdfinfo .*: exit"):
        page_map(tmp_path / "none.pdf")


def test_tool_not_on_the_path() -> None:
    with pytest.raises(PagesError, match="no-such-poppler-tool: not on the path"):
        poppler(["no-such-poppler-tool"])


def test_poppler_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_pages, "POPPLER_TIMEOUT", 0.05)
    with pytest.raises(PagesError, match=r"sleep 5: over 0.05 s"):
        poppler(["sleep", "5"])
