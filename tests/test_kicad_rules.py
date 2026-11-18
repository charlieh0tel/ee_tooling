from dataclasses import dataclass
from typing import Any

import pytest

from ee_tooling import kicad_rules
from ee_tooling.kicad_rules import (
    BEGIN,
    END,
    Dru,
    board_setup,
    install,
    installed,
    raised,
    resource,
    sections,
)
from ee_tooling.profiles.jlcpcb import Jlcpcb, Options
from ee_tooling.profiles.profile import Rules

# A profile's .kicad_dru: its version and comment, then two sections.
DRU = """\
(version 1)

# a profile's rules

# --- Limits ------------------------------------------------------------
(rule "Track"
\t(constraint track_width (min 0.1mm)))

# --- Price -------------------------------------------------------------
(rule "Via"
\t(constraint hole_size (min 0.3mm)))
"""
RULES = Rules("dru", {"price": "Price"}, "setup")


@dataclass(frozen=True)
class PriceOptions:
    price: bool


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {"dru": DRU, "setup": '{"board": {"rules": {"min_clearance": 0.1}}}'}
    monkeypatch.setattr(kicad_rules, "resource", files.__getitem__)


def test_sections() -> None:
    found = sections(DRU)
    assert [n for n, _ in found] == ["", "Limits", "Price"]
    assert "".join(t for _, t in found) == DRU
    assert found[2][1].startswith("# --- Price ---")
    assert sections("(version 1)\n") == [("", "(version 1)\n")]


@pytest.mark.usefixtures("resources")
def test_installed_between_markers_without_version() -> None:
    text = installed("fab", RULES, PriceOptions(True))
    assert text.startswith("# --- ee_tooling: fab rules; written by adopt ---\n# a profile's")
    assert text.endswith(
        "(min 0.3mm)))\n# --- ee_tooling: end; the board's own rules follow and win ---\n"
    )
    assert "(version" not in text


@pytest.mark.usefixtures("resources")
def test_installed_leaves_out_an_optional_section() -> None:
    text = installed("fab", RULES, PriceOptions(False))
    assert '(rule "Track"' in text
    assert "Price" not in text
    assert '(rule "Via"' not in text


def test_jlcpcb_rules() -> None:
    """The JLCPCB profile's: its limits and the house rules; its Board
    Setup minimums."""
    text = installed("jlcpcb", Jlcpcb.rules, Options())
    assert '(rule "JLCPCB track width"' in text
    assert '(rule "Mounting hole plated"' in text
    assert "price" not in text.lower()
    assert [n for n, _ in sections(resource(Jlcpcb.rules.dru))][1:] == [
        "JLCPCB limits",
        "House rules (docs/kicad-style.md)",
    ]
    assert board_setup(Jlcpcb.rules)["board"]["design_settings"]["rules"]["min_clearance"] == 0.1


SECTION = f'{BEGIN.format("fab")}\n(rule "Track")\n{END}\n'
OWN = '(rule "Mine"\n\t(constraint track_width (min 0.2mm)))'


@pytest.mark.parametrize(
    ("text", "section", "own"),
    [
        (None, None, ""),
        ("", None, ""),
        (f"(version 1)\n\n{OWN}\n", None, OWN),
        (f"(version 1)\n{SECTION}\n{OWN}\n", SECTION, OWN),
        (f"(version 1)\n{SECTION}", SECTION, ""),
        (f"(version 1)\n{OWN}\n{SECTION}", SECTION, OWN),
    ],
)
def test_dru_split(text: str | None, section: str | None, own: str) -> None:
    assert Dru.split(text) == Dru(section, own)


@pytest.mark.parametrize(
    "text",
    [
        f"{SECTION}{SECTION}",
        f"{END}\n{BEGIN.format('fab')}\n",
        f"{BEGIN.format('fab')}\n",
        f"{END}\n",
    ],
)
def test_dru_split_refused(text: str) -> None:
    with pytest.raises(ValueError, match="markers are not one begin line, then one end line"):
        Dru.split(text)


def test_dru_with_section() -> None:
    """The version line, the section, then the board's own rules."""
    assert Dru(None, OWN).with_section(SECTION) == f"(version 1)\n{SECTION}\n{OWN}\n"
    assert Dru(None, "").with_section(SECTION) == f"(version 1)\n{SECTION}"
    new = f'{BEGIN.format("fab")}\n(rule "Other")\n{END}\n'
    replaced = Dru.split(f"(version 1)\n{SECTION}\n{OWN}\n").with_section(new)
    assert replaced == f"(version 1)\n{new}\n{OWN}\n"
    assert Dru.split(replaced) == Dru(new, OWN)


def test_dru_copied() -> None:
    """The board's own rules named as the section's: a copy outside the
    markers."""
    own = f'{OWN}\n(rule "Track"\n\t(constraint track_width (min 0.1mm)))'
    assert Dru(None, own).copied(SECTION) == ["Track"]
    assert Dru(None, OWN).copied(SECTION) == []


LEAST = {"board": {"design_settings": {"rules": {"a": 0.1, "b": 0.2, "c": 0.3}}}}


def test_raised_never_lowered() -> None:
    project: dict[str, Any] = {
        "board": {"design_settings": {"rules": {"a": 0.05, "b": 0.5, "d": 1}}},
        "other": 1,
    }
    out, changes = raised(project, LEAST)
    assert out == {
        "board": {"design_settings": {"rules": {"a": 0.1, "b": 0.5, "d": 1, "c": 0.3}}},
        "other": 1,
    }
    assert changes == [
        "board.design_settings.rules.a: 0.05 -> 0.1",
        "board.design_settings.rules.c: none -> 0.3",
    ]
    assert project["board"]["design_settings"]["rules"]["a"] == 0.05
    assert raised(out, LEAST) == (out, [])


def test_raised_into_an_empty_project() -> None:
    out, changes = raised({}, LEAST)
    assert out == LEAST
    assert len(changes) == 3


def test_raised_replaces_what_is_not_a_number() -> None:
    out, changes = raised({"board": {"design_settings": {"rules": {"a": True}}}}, LEAST)
    assert out["board"]["design_settings"]["rules"]["a"] == 0.1
    assert changes[0] == "board.design_settings.rules.a: True -> 0.1"


def test_install() -> None:
    found = install(f"(version 1)\n{OWN}\n", {}, SECTION, LEAST)
    assert found.dru == f"(version 1)\n{SECTION}\n{OWN}\n"
    assert found.stale
    assert found.project == LEAST
    assert found.copied == []
    again = install(found.dru, found.project, SECTION, LEAST)
    assert again.dru == found.dru
    assert not again.stale
    assert again.raised == []
