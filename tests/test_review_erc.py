import json
from pathlib import Path

from ee_tooling.review_erc import NOISE, summary, violations

REPORT = {
    "sheets": [
        {
            "path": "/PWR/",
            "violations": [
                {
                    "severity": "error",
                    "type": "pin_not_connected",
                    "description": "Pin not connected",
                    "items": [
                        {"description": "Symbol U1 Pin 3"},
                        {"description": "x"},
                        {"description": "y"},
                    ],
                },
                {
                    "severity": "warning",
                    "type": "lib_symbol_issues",
                    "description": "no lib",
                    "items": [],
                },
            ],
        }
    ]
}


def test_violations() -> None:
    found = violations(REPORT)
    assert len(found) == 2
    assert (
        found[0].line()
        == "- error pin_not_connected | /PWR/ | Pin not connected | Symbol U1 Pin 3; x"
    )


def test_summary_drops_noise(tmp_path: Path) -> None:
    path = tmp_path / "erc.json"
    path.write_text(json.dumps(REPORT), encoding="utf-8")
    text = summary(path)
    assert "- warning lib_symbol_issues: 1 (noise, not listed)" in text
    assert text.count("lib_symbol_issues") == 1
    assert "- error pin_not_connected | /PWR/" in text
    assert "lib_symbol_issues" in NOISE
