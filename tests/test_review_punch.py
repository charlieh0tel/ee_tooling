from ee_tooling.review_punch import check, report

# Major S-1 on the list; Major S-2 missing; Minor S-3 regraded to Major
# in adjudication and missing; Major S-4 dropped in adjudication; Major
# S-5 waived in its Recommendation; Major L-1 struck; Note L-2 regraded
# to nothing that needs listing.
REPORT = """# Widget review

## Findings: schematic

| ID | Severity | Rubric/Spec | Finding | Evidence | Recommendation | Source |
|---|---|---|---|---|---|---|
| S-1 | Major | sch 1 | a | R1 | fix | M |
| S-2 | Major FIRMWARE | sch 1 | b | R2 | fix | M |
| S-3 | Minor (suspected) | sch 1 | c | R3 | fix | M |
| S-4 | Major | sch 1 | d | R4 | fix | M |
| S-5 | Major | sch 1 | e | R5 | Adjudicated: waived | M |

## Findings: layout

| ID | Severity | Rubric/Spec | Finding | Evidence | Recommendation | Source |
|---|---|---|---|---|---|---|
| ~~L-1~~ | Major | pcb 1 | f | U1 | fix | L |
| L-2 | Note | pcb 1 | g | U2 | fix | L |

## Adjudication

| Rubric item or ID | Decision | Effect |
|---|---|---|
| S-3, regulator | Must | Minor -> Major |
| S-4 | Dropped | waived |

## Unresolved questions

1. Which way (S-1)?
2. How much (S-2)?

## Reproduction
"""

PUNCH = """# Widget punch list

## Must

- [ ] Decide which way (question 1). The SOT-23 part runs hot (S-1).
- [ ] An item for a finding not in the report (S-9).
- [ ] Questions 1 and 3 together; question 2, 16.5 V not 16 (S-1).
"""


def test_report() -> None:
    found = report(REPORT)
    assert sorted(found.findings) == ["L-1", "L-2", "S-1", "S-2", "S-3", "S-4", "S-5"]
    assert found.prefixes == {"S", "L"}
    assert found.questions == {1, 2}
    assert found.findings["S-2"].severity == "Major"
    assert found.findings["S-3"].severity == "Major"
    assert found.findings["S-4"].dropped and found.findings["S-5"].dropped
    assert found.findings["L-1"].dropped and not found.findings["L-2"].dropped
    assert found.findings["S-1"].line == 7


def test_check() -> None:
    in_punch, in_report = check(REPORT, PUNCH)
    assert [(p.line, p.message) for p in in_punch] == [
        (6, "S-9 is not a finding of the report"),
        (7, "question 3 is not in the report's Unresolved questions"),
    ]
    assert [(p.line, p.message) for p in in_report] == [
        (8, "S-2 (Major) is not on the punch list"),
        (9, "S-3 (Major) is not on the punch list"),
    ]


def test_part_numbers_are_not_ids() -> None:
    """SOT-23 has no finding's prefix; S-23 does."""
    in_punch, _ = check(REPORT, "SOT-23 and TO-220 (S-23)\n")
    assert [p.message for p in in_punch] == ["S-23 is not a finding of the report"]


def test_no_findings_in_report() -> None:
    in_punch, in_report = check("| ID | Sev |\n|---|---|\n| S-1 | Major |\n", PUNCH)
    assert in_punch == []
    assert [p.message for p in in_report] == [
        "no findings table (an ID column and Severity) in the report"
    ]


# A findings table with a `|` inside a cell, written plain and escaped.
PIPED = """| ID | Severity | Rubric/Spec | Finding | Evidence | Recommendation | Source |
|---|---|---|---|---|---|---|
| S-1 | Minor | sch 1 | a | R1 | fix | M |
| S-2 | Major | sch 1 | EN tied A|B | R2 | fix | M |
| S-3 | Major | sch 1 | EN tied A\\|B | R3 | fix | M |
"""


def test_row_with_a_bar() -> None:
    """A row whose cells do not match the header is reported, not dropped;
    an escaped bar is part of its cell."""
    _, in_report = check(PIPED, "S-1\n")
    assert [(p.line, p.message) for p in in_report] == [
        (4, "8 cells, header has 7"),
        (5, "S-3 (Major) is not on the punch list"),
    ]


def test_only_unreadable_rows() -> None:
    lines = PIPED.splitlines()
    _, in_report = check("\n".join([*lines[:2], lines[3]]), "")
    assert [p.message for p in in_report] == ["8 cells, header has 7"]
