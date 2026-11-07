from ee_tooling.review_pages import labels

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
