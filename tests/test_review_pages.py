from ee_tooling.review_pages import labels

PAGE = """   ControlHubBoard
Sheet: /PWR/
File: PWR.kicad_sch   Sheet 36/51
"""


def test_labels() -> None:
    assert labels(PAGE) == ["Sheet: /PWR/", "File: PWR.kicad_sch"]


def test_labels_empty() -> None:
    assert labels("nothing here") == []
