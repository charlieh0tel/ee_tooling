import pytest

from ee_tooling.review_findings import CLASSES, EVIDENCE, REVIEWER, SEVERITIES, cells, lint

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
    assert found.ok and (found.tables, found.rows) == (1, 2)


def test_header_missing_column() -> None:
    found = lint("| ID | Class | Finding |\n|---|---|---|\n| a | Defect | b |\n")
    lacking = ", ".join(c for c in REVIEWER if c not in ("ID", "Class", "Finding"))
    assert [(p.line, p.message) for p in found.problems] == [
        (1, f"findings table lacks: {lacking}")
    ]


def test_other_tables_skipped() -> None:
    ledger = (
        "## Rubric ledger\n\n| Item | Result | Evidence |\n|---|---|---|\n| 2.1 | pass | x |\n\n"
        "| Rubric item or ID | Decision | Effect |\n|---|---|---|\n| S-1 | Must | fix |\n\n"
    )
    found = lint(ledger + "## Findings\n\n" + GOOD)
    assert found.ok and (found.tables, found.rows) == (1, 2)


def test_no_findings_table() -> None:
    message = "no findings table (an ID column and Class or Severity)"
    assert [p.message for p in lint("just prose").problems] == [message]
    assert [p.message for p in lint("| ID | Finding |\n|---|---|\n| a | b |\n").problems] == [
        message
    ]


def test_row_problems() -> None:
    bad = HEADER + (
        "| P-1 | Maybe | | x | nothing cited | short |\n"
        "| P-1 | Defect | sch 1 | y | R1 | long enough consequence |\n"
        "| P-3 | Defect | sch 1 | y |\n"
    )
    found = lint(bad)
    messages = [(p.line, p.message) for p in found.problems]
    assert (3, f"Class 'Maybe' not in {CLASSES}") in messages
    assert (3, "empty Rubric/Spec ('none' for a round 2 item)") in messages
    assert (3, "Evidence names no file, refdes, net, page or point") in messages
    assert (3, "Consequence missing or too short") in messages
    assert (4, "duplicate ID P-1") in messages
    assert (5, "4 cells, header has 6") in messages
    assert found.rows == 2


REPORT_HEADER = (
    "| ID | Severity | Rubric/Spec | Finding | Evidence | Recommendation | Source |\n"
    "|---|---|---|---|---|---|---|\n"
)


def test_report_tables() -> None:
    """The report's tables, one per section, with an adjudication table
    first; Severity may carry tags; IDs are unique across the tables."""
    report = (
        "## Adjudication\n\n| Rubric item or ID | Decision | Effect |\n|---|---|---|\n"
        "| S-1 | Must | fix |\n\n## Findings: schematic\n\n"
        + REPORT_HEADER
        + "| S-1 | Major | sch 3.1 | Q1 hot | page 14 | two 27k | M |\n"
        "| S-2 | Minor FIRMWARE | sch 5.2 | trip | /CH1_ISENSE | gain | H |\n"
        "\n## Findings: layout\n\n"
        + REPORT_HEADER
        + "| L-1 | Note (suspected) | pcb 2 | gap | (12.5, 40) | widen | L |\n"
        "| S-2 | Severe | | dup | nothing | x | L |\n"
    )
    found = lint(report)
    assert (found.tables, found.rows) == (2, 4)
    assert [(p.line, p.message) for p in found.problems] == [
        (19, "duplicate ID S-2"),
        (19, f"Severity 'Severe' does not open with one of {SEVERITIES}"),
        (19, "empty Rubric/Spec ('none' for a round 2 item)"),
        (19, "Evidence names no file, refdes, net, page or point"),
    ]


def test_suspected_class() -> None:
    row = "| A-1 | Defect (suspected) | r | f | R1 | a consequence long enough |\n"
    assert lint(HEADER + row).ok


def test_evidence_forms() -> None:
    forms = (
        "(12.5, 40)",
        "/CAN2/CAN_H",
        "/mcu/sda",
        "page 3",
        "line 288",
        "board.md",
        "X.kicad_pcb",
        "Power.zen:12",
    )
    for evidence in forms:
        row = f"| A-1 | Defect | r | f | {evidence} | a consequence long enough |\n"
        assert lint(HEADER + row).ok, evidence


def test_evidence_not_a_net_path() -> None:
    row = "| A-1 | Defect | r | f | high and/or low | a consequence long enough |\n"
    assert [p.message for p in lint(HEADER + row).problems] == [
        "Evidence names no file, refdes, net, page or point"
    ]


def test_cells_escaped_bar() -> None:
    """`\\|` is a `|` inside a cell, at the row's end too."""
    assert cells("| a | b \\| c | d |") == ["a", "b | c", "d"]
    assert cells("| a | b \\|") == ["a", "b |"]
    assert cells("|x|y|") == ["x", "y"]


@pytest.mark.parametrize(
    ("evidence", "named"),
    [
        *(("R141 DNP", True), ("SW3", True), ("LED2", True), ("TP4", True), ("JP1", True)),
        *(("W2 jumper wire", True), ("G1 oscillator", True), ("RT1 thermistor", True)),
        *(("DDR4 timing", False), ("A4 drawing", False), ("USB2 port", False)),
    ],
)
def test_evidence_refdes(evidence: str, named: bool) -> None:
    """A refdes has a reference prefix; a part name like DDR4 is not one."""
    assert bool(EVIDENCE.search(evidence)) is named
