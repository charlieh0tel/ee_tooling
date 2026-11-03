from ee_tooling.review_findings import CLASSES, REQUIRED, lint

HEADER = (
    "| ID | Class | Rubric/Spec | Finding | Evidence | Consequence |\n|---|---|---|---|---|---|\n"
)
GOOD = HEADER + (
    "| P-1 | Defect | sch 2.2 | EN floats | nets.txt R141 DNP "
    "| MCU reset leaves BAT_EN undefined |\n"
    "| P-2 | Observation | none | could add TP | page 36 "
    "| a bring-up probe point would be handy |\n"
)


def test_good() -> None:
    found = lint(GOOD)
    assert found.ok and found.rows == 2


def test_header_missing_column() -> None:
    found = lint("| ID | Finding |\n|---|---|\n| a | b |\n")
    lacking = ", ".join(c for c in REQUIRED if c not in ("ID", "Finding"))
    assert [p.message for p in found.problems] == [
        f"no findings table; first table lacks: {lacking}"
    ]


def test_findings_table_after_other_tables() -> None:
    ledger = (
        "## Rubric ledger\n\n| Item | Result | Evidence |\n|---|---|---|\n| 2.1 | pass | x |\n\n"
    )
    found = lint(ledger + "## Findings\n\n" + GOOD)
    assert found.ok and found.rows == 2


def test_no_table() -> None:
    assert lint("just prose").problems[0].message == "no Markdown table"


def test_row_problems() -> None:
    bad = HEADER + (
        "| P-1 | Maybe | | x | nothing cited | short |\n"
        "| P-1 | Defect | sch 1 | y | R1 | long enough consequence |\n"
        "| P-3 | Defect | sch 1 | y |\n"
    )
    found = lint(bad)
    messages = [(p.row, p.message) for p in found.problems]
    assert (1, f"Class 'Maybe' not in {CLASSES}") in messages
    assert (1, "empty Rubric/Spec ('none' for a round 2 item)") in messages
    assert (1, "Evidence names no file, refdes, net, page or point") in messages
    assert (1, "Consequence missing or too short") in messages
    assert (2, "duplicate ID P-1") in messages
    assert (3, "4 cells, header has 6") in messages
    assert found.rows == 2


def test_suspected_class() -> None:
    row = "| A-1 | Defect (suspected) | r | f | R1 | a consequence long enough |\n"
    assert lint(HEADER + row).ok


def test_evidence_forms() -> None:
    forms = (
        "(12.5, 40)",
        "/CAN2/CAN_H",
        "page 3",
        "line 288",
        "board.md",
        "X.kicad_pcb",
        "Power.zen:12",
    )
    for evidence in forms:
        row = f"| A-1 | Defect | r | f | {evidence} | a consequence long enough |\n"
        assert lint(HEADER + row).ok, evidence
