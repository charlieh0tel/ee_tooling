import json
from dataclasses import dataclass
from typing import Any

import pytest

from ee_tooling import kicad_rules
from ee_tooling.kicad_rules import (
    BEGIN,
    END,
    ERC_SETTINGS,
    RULE_NAME,
    Dru,
    board_setup,
    copied_message,
    dropped,
    erc_set,
    floored,
    install,
    installed,
    raised,
    resource,
    retired_message,
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
    assert '(rule "JLCPCB annular ring"' in text
    assert "track_width" not in text
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


def test_erc_set_keeps_the_stricter() -> None:
    """ignore < warning < error: the board's kept where stricter; anything
    else, the grid too, set to the house's; the board's own others kept."""
    house = {
        "erc": {"rule_severities": {"a": "warning", "b": "warning", "c": "error", "d": "warning"}},
        "schematic": {"connection_grid_size": 50.0},
    }
    project: dict[str, Any] = {
        "erc": {"rule_severities": {"a": "error", "b": "ignore", "d": "odd", "e": "ignore"}},
        "schematic": {"connection_grid_size": 25.0},
    }
    out, changes = erc_set(project, house)
    assert out == {
        "erc": {
            "rule_severities": {
                "a": "error",
                "b": "warning",
                "d": "warning",
                "e": "ignore",
                "c": "error",
            }
        },
        "schematic": {"connection_grid_size": 50.0},
    }
    assert changes == [
        "erc.rule_severities.b: ignore -> warning",
        "erc.rule_severities.c: none -> error",
        "erc.rule_severities.d: odd -> warning",
        "schematic.connection_grid_size: 25.0 -> 50.0",
    ]
    assert erc_set(out, house) == (out, [])
    assert project["erc"]["rule_severities"]["b"] == "ignore"


def test_erc_set_house() -> None:
    """The house file: every value a severity, and the grid."""
    house = json.loads(resource(ERC_SETTINGS))
    assert set(house["erc"]["rule_severities"].values()) <= {"ignore", "warning", "error"}
    out, _ = erc_set({}, house)
    assert out == house


def test_copied_message() -> None:
    """A count, one name a line, the file and what to do."""
    assert copied_message("hw/b.kicad_dru", ["A", "B"]) == (
        "hw/b.kicad_dru: 2 rules below ee_tooling's end marker copy the profile's and win over"
        " them:\n  A\n  B\nremove these rules from below the end marker in hw/b.kicad_dru"
    )


def test_new_keys_in_sorted_place() -> None:
    """A key added to a sorted object goes where KiCad's save puts it; an
    unsorted object gets it at the end; an existing key keeps its place."""
    project = {
        "board": {"design_settings": {"rules": {"min_clearance": 0.05, "min_via_diameter": 0.5}}},
        "erc": {"rule_severities": {"zz_last": "error", "aa_first": "ignore"}},
        "meta": {"filename": "x.kicad_pro"},
        "schematic": {"page_layout_descr_file": ""},
    }
    out, _ = raised(
        project, {"board": {"design_settings": {"rules": {"min_hole_clearance": 0.25}}}}
    )
    assert list(out["board"]["design_settings"]["rules"]) == [
        "min_clearance",
        "min_hole_clearance",
        "min_via_diameter",
    ]
    house = {
        "erc": {"rule_severities": {"mm_middle": "error"}},
        "schematic": {"connection_grid_size": 50.0},
    }
    out, _ = erc_set(out, house)
    assert list(out["erc"]["rule_severities"]) == ["zz_last", "aa_first", "mm_middle"]
    assert list(out["schematic"]) == ["connection_grid_size", "page_layout_descr_file"]
    out, _ = raised(out, {"board": {"design_settings": {"rules": {"min_via_diameter": 0.6}}}})
    assert list(out["board"]["design_settings"]["rules"]) == [
        "min_clearance",
        "min_hole_clearance",
        "min_via_diameter",
    ]
    assert list(out) == ["board", "erc", "meta", "schematic"]


def setup_of(**rules: object) -> dict[str, Any]:
    return {"board": {"design_settings": {"rules": rules}}}


FLOOR_SECTION = (
    '(rule "a" (condition "x") (constraint hole_size (min 0.5mm)))\n'
    '(rule "b" (constraint text_height (min 0.8mm)) (constraint text_thickness (min 0.15mm)))\n'
    '(rule "c" (condition "A.Type == \'Pad\'") (constraint annular_width (min 0.18mm)))\n'
    '(rule "d" (constraint skew (max 1.27mm)))\n'
)


@pytest.mark.parametrize(
    ("setup", "want"),
    [
        ({}, FLOOR_SECTION),
        ({"min_through_hole_diameter": 0.3}, FLOOR_SECTION),
        (
            {"min_through_hole_diameter": 1.5, "min_text_thickness": 0.2},
            FLOOR_SECTION.replace("(min 0.5mm)", "(min 1.5mm)").replace(
                "(min 0.15mm)", "(min 0.2mm)"
            ),
        ),
        ({"min_via_annular_width": 0.3}, FLOOR_SECTION.replace("(min 0.18mm)", "(min 0.3mm)")),
        ({"min_text_height": True, "min_through_hole_diameter": "x"}, FLOOR_SECTION),
        ({"min_text_height": 1.0 / 3}, FLOOR_SECTION),
    ],
)
def test_floored(setup: dict[str, object], want: str) -> None:
    """A minimum raised to the board's Board Setup value of its kind where
    that is higher; booleans and text left."""
    assert floored(FLOOR_SECTION, setup_of(**setup)) == want


def test_floored_rounds() -> None:
    assert "(min 1.333333mm)" in floored(FLOOR_SECTION, setup_of(min_text_height=4 / 3))


def test_install_floors_and_goes_stale() -> None:
    """The section installed is floored to the board's Board Setup, raised
    first; a Board Setup raised later leaves it stale."""
    section = f"{BEGIN.format('fab')}\n{FLOOR_SECTION}{END}\n"
    least = setup_of(min_through_hole_diameter=0.15)
    found = install(None, setup_of(min_through_hole_diameter=0.6), section, least)
    assert "(constraint hole_size (min 0.6mm))" in found.section
    assert found.section in found.dru
    again = install(found.dru, found.project, section, least)
    assert not again.stale
    later = install(found.dru, setup_of(min_through_hole_diameter=0.7), section, least)
    assert later.stale and "(min 0.7mm)" in later.section


def test_dropped() -> None:
    old = '# a\n(rule "x" (c))\n\n(rule "y" (c))\n'
    new = '# a\n(rule "y" (c))\n'
    assert dropped(old, new) == (1, ["x"])
    assert dropped(None, new) == (0, [])
    assert dropped(new, new) == (0, [])


def test_silk_clearance_not_floored() -> None:
    """Board Setup's silkscreen clearance holds silkscreen to silkscreen:
    a silkscreen-to-pad rule is not raised to it."""
    section = '(rule "own" (constraint silk_clearance (min 0mm)))\n'
    assert floored(section, setup_of(min_silk_clearance=0.15)) == section


def test_raised_each_net_class() -> None:
    """A list of one object in the minimums stands for each net class:
    each value there raised, none lowered; one a class lacks is KiCad's
    default (0.3 for a microvia), not set; no class added."""
    project = {
        "net_settings": {
            "classes": [
                {"name": "Default", "microvia_diameter": 0.2},
                {"name": "Power", "microvia_diameter": 0.4},
                {"name": "Bare"},
            ]
        }
    }
    least = {"net_settings": {"classes": [{"microvia_diameter": 0.25}]}}
    out, changes = raised(project, least)
    assert [c.get("microvia_diameter") for c in out["net_settings"]["classes"]] == [0.25, 0.4, None]
    assert changes == ["net_settings.classes[Default].microvia_diameter: 0.2 -> 0.25"]
    assert raised({}, least) == ({"net_settings": {}}, [])


def test_retired_rules_named() -> None:
    """A hand copy of an older rules file below the end marker: its rules
    ee_tooling no longer has named, in force, to be removed."""
    own = (
        '(rule "JLCPCB track width"\n\t(constraint track_width (min 0.1mm)))\n'
        '(rule "JLCPCB via hole at no extra cost"\n\t(constraint hole_size (min 0.3mm)))\n'
        '(rule "Mine"\n\t(constraint track_width (min 0.2mm)))\n'
    )
    found = install(f"(version 1)\n{own}", {}, SECTION, {})
    assert found.retired == ["JLCPCB track width", "JLCPCB via hole at no extra cost"]
    assert retired_message("hw/b.kicad_dru", found.retired) == (
        "hw/b.kicad_dru: 2 rules below ee_tooling's end marker are retired from ee_tooling's"
        " rules and still in force:\n  JLCPCB track width\n  JLCPCB via hole at no extra cost\n"
        "remove these rules from below the end marker in hw/b.kicad_dru"
    )


def test_retired_not_current() -> None:
    """No rule the profile has now is among the retired."""
    current = set(RULE_NAME.findall(resource(Jlcpcb.rules.dru)))
    assert current and not current & kicad_rules.RETIRED
