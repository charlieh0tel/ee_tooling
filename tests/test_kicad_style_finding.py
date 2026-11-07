import re
from pathlib import Path

import pytest

from ee_tooling.kicad_style_finding import (
    FINISHING_RULES,
    NETCLASS_RULES,
    POSITION_FIELD,
    ROUTING_RULES,
    RULES,
    SCHEMATIC_RULES,
    SET_FIELDS,
    ZONE_RULES,
    found,
)

GROUPS = (SCHEMATIC_RULES, ZONE_RULES, FINISHING_RULES, ROUTING_RULES, NETCLASS_RULES)


def test_rule_ids_unique() -> None:
    assert sum(len(g) for g in GROUPS) == len(RULES)


def test_subject_fields_known() -> None:
    names = {"net", "pad", "pin", "capacitor", "reference", "netclass"}
    fields = {f for subject in RULES.values() for f in subject}
    assert fields <= names | SET_FIELDS | {POSITION_FIELD}


def test_finding_prints_as_before() -> None:
    f = found(
        "via-shared",
        "GND: via at (1, 2) serves R1 pad 2, R2 pad 2",
        net="GND",
        pads=frozenset({"R1 pad 2", "R2 pad 2"}),
    )
    assert str(f) == "GND: via at (1, 2) serves R1 pad 2, R2 pad 2"
    note = found("half-pair", "net D_P: named like half", note=True, net="D_P")
    assert str(note) == "note: net D_P: named like half"


@pytest.mark.parametrize("subject", [{}, {"net": "GND"}, {"net": "GND", "pad": "R1 pad 2"}])
def test_subject_must_be_the_rules(subject: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="via-shared: subject"):
        found("via-shared", "x", False, **subject)


def test_rules_documented() -> None:
    """docs/kicad-style-check.md's table lists every rule and its subject."""
    doc = Path(__file__).parent.parent / "docs" / "kicad-style-check.md"
    table = {
        m[1]: tuple(re.findall(r"`([a-z]+)`", m[2]))
        for m in re.finditer(r"^\| \w+ \| `([a-z-]+)` \| ([^|]*) \|", doc.read_text(), re.M)
    }
    assert table == RULES
