from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ee_tooling import kicad_rules
from ee_tooling.kicad_rules import (
    BEGIN,
    END,
    RULE_NAME,
    Dru,
    board_setup,
    copied_message,
    current,
    dropped,
    erc_set,
    erc_settings,
    floored,
    install,
    installed,
    known,
    pinned_message,
    raised,
    retired,
    retired_message,
    rule_file,
    rule_set,
    rule_sets,
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
    monkeypatch.setattr(kicad_rules, "rule_file", lambda _, name: files[name])


def test_sections() -> None:
    found = sections(DRU)
    assert [n for n, _ in found] == ["", "Limits", "Price"]
    assert "".join(t for _, t in found) == DRU
    assert found[2][1].startswith("# --- Price ---")
    assert sections("(version 1)\n") == [("", "(version 1)\n")]


@pytest.mark.usefixtures("resources")
def test_installed_between_markers_without_version() -> None:
    text = installed("fab", RULES, PriceOptions(True), "0.4.0")
    assert text.startswith("# --- ee_tooling: fab rules; written by adopt ---\n# a profile's")
    assert text.endswith(
        "(min 0.3mm)))\n# --- ee_tooling: end; the board's own rules follow and win ---\n"
    )
    assert "(version" not in text


@pytest.mark.usefixtures("resources")
def test_installed_names_its_set() -> None:
    """A set after 0.4.0 is named on the line after the begin marker; 0.4.0's
    section, as boards have it installed, names none."""
    text = installed("fab", RULES, PriceOptions(True), "1.0.0")
    assert text.startswith(
        "# --- ee_tooling: fab rules; written by adopt ---\n# ee_tooling rule set 1.0.0\n"
        "# a profile's"
    )
    assert Dru.split(f"(version 1)\n{text}").section == text
    assert "rule set" not in installed("fab", RULES, PriceOptions(True), "0.4.0")


@pytest.mark.usefixtures("resources")
def test_installed_leaves_out_an_optional_section() -> None:
    text = installed("fab", RULES, PriceOptions(False), "1.0.0")
    assert '(rule "Track"' in text
    assert "Price" not in text
    assert '(rule "Via"' not in text


def test_jlcpcb_rules() -> None:
    """The JLCPCB profile's: its limits and the house rules; its Board
    Setup minimums."""
    text = installed("jlcpcb", Jlcpcb.rules, Options(), current())
    assert '(rule "JLCPCB annular ring"' in text
    assert "track_width" not in text
    assert '(rule "Mounting hole plated"' in text
    assert "price" not in text.lower()
    assert [n for n, _ in sections(rule_file(current(), Jlcpcb.rules.dru))][1:] == [
        "JLCPCB limits",
        "House rules (docs/kicad-style.md)",
    ]
    assert (
        board_setup(Jlcpcb.rules, current())["board"]["design_settings"]["rules"]["min_clearance"]
        == 0.1
    )


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
    found = install(f"(version 1)\n{OWN}\n", {}, SECTION, LEAST, frozenset())
    assert found.dru == f"(version 1)\n{SECTION}\n{OWN}\n"
    assert found.stale
    assert found.project == LEAST
    assert found.copied == []
    again = install(found.dru, found.project, SECTION, LEAST, frozenset())
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
    house = erc_settings(current())
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
    found = install(None, setup_of(min_through_hole_diameter=0.6), section, least, frozenset())
    assert "(constraint hole_size (min 0.6mm))" in found.section
    assert found.section in found.dru
    again = install(found.dru, found.project, section, least, frozenset())
    assert not again.stale
    later = install(found.dru, setup_of(min_through_hole_diameter=0.7), section, least, frozenset())
    assert later.stale and "(min 0.7mm)" in later.section


def test_dropped() -> None:
    old = '# a\n(rule "x" (c))\n\n(rule "y" (c))\n'
    new = '# a\n(rule "y" (c))\n'
    assert dropped(old, new, kicad_rules.RETIRED) == (1, ["x"])
    assert dropped(None, new, kicad_rules.RETIRED) == (0, [])
    assert dropped(new, new, kicad_rules.RETIRED) == (0, [])


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
    found = install(f"(version 1)\n{own}", {}, SECTION, {}, retired(Jlcpcb.rules, current()))
    assert found.retired == ["JLCPCB track width", "JLCPCB via hole at no extra cost"]
    assert retired_message("hw/b.kicad_dru", found.retired) == (
        "hw/b.kicad_dru: 2 rules below ee_tooling's end marker are retired from ee_tooling's"
        " rules and still in force:\n  JLCPCB track width\n  JLCPCB via hole at no extra cost\n"
        "remove these rules from below the end marker in hw/b.kicad_dru"
    )


def test_retired_not_current() -> None:
    """No rule the profile has now is among the retired."""
    names = set(RULE_NAME.findall(rule_file(current(), Jlcpcb.rules.dru)))
    assert names and not names & kicad_rules.RETIRED


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_floored_ignores_a_value_not_finite(value: float) -> None:
    """A Board Setup value NaN or Infinity (JSON's extensions): no floor,
    never "nanmm"."""
    assert floored(FLOOR_SECTION, setup_of(min_through_hole_diameter=value)) == FLOOR_SECTION


def test_dropped_only_foreign_rules() -> None:
    """A value changed, a retired rule, a comment: none counted."""
    new = '(rule "y" (constraint hole_size (min 0.5mm)))\n'
    old = (
        '# old comment\n(rule "y" (constraint hole_size (min 0.4mm)))\n'
        '(rule "JLCPCB track width" (constraint track_width (min 0.1mm)))\n'
    )
    assert dropped(old, new, kicad_rules.RETIRED) == (0, [])
    mine = old + '(rule "mine"\n\t(constraint track_width (min 0.3mm)))\n'
    assert dropped(mine, new, kicad_rules.RETIRED) == (2, ["mine"])


def test_rule_sets_by_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oldest first, by number; an entry not a directory named X.Y.Z is not
    a set."""
    for name in ("0.10.0", "0.9.0", "0.4.0", "notes", "1.0"):
        (tmp_path / name).mkdir()
    (tmp_path / "0.5.0").write_text("")
    (tmp_path / "0.10.0" / "f").write_text("text")
    monkeypatch.setattr(kicad_rules, "rules_directory", lambda: tmp_path)
    assert rule_sets() == ["0.4.0", "0.9.0", "0.10.0"]
    assert current() == "0.10.0"
    assert rule_file("0.10.0", "f") == "text"


def test_package_rule_sets() -> None:
    """The package's rules directory holds rule sets only, the oldest
    0.4.0, each with every profile's files."""
    entries = [d.name for d in kicad_rules.rules_directory().iterdir()]
    assert entries and sorted(entries) == sorted(rule_sets())
    assert rule_sets()[0] == "0.4.0"
    for version in rule_sets():
        for name in (Jlcpcb.rules.dru, Jlcpcb.rules.board_setup, kicad_rules.ERC_SETTINGS):
            assert rule_file(version, name)


def test_rule_set_pinned(newer_set: str) -> None:
    """None is the current set; a set ee_tooling has, that set; anything
    else an error naming the sets."""
    assert rule_set(None) == newer_set == current()
    assert rule_set("0.4.0") == "0.4.0"
    with pytest.raises(ValueError, match=rf"^rules '0\.3\.0' is not one of 0\.4\.0, {newer_set}$"):
        rule_set("0.3.0")
    with pytest.raises(ValueError, match=r"^'rules' is not a string$"):
        rule_set(4)


def test_pinned_message(newer_set: str) -> None:
    """Said for a pin to a set other than the current one only."""
    assert pinned_message("b", None) is None
    assert pinned_message("b", newer_set) is None
    assert pinned_message("b", "0.4.0") == (
        f"b: ee_tooling's rules pinned at 0.4.0 by fab.toml; the current rules are {newer_set}"
    )


def test_retired_by_set(newer_set: str) -> None:
    """A rule a later set retired is retired for that set only; one a later
    set added is not retired for an earlier one; each is known."""
    assert "JLCPCB BGA pad size" in retired(Jlcpcb.rules, newer_set)
    assert "JLCPCB BGA pad size" not in retired(Jlcpcb.rules, "0.4.0")
    assert "Test only rule" not in retired(Jlcpcb.rules, "0.4.0")
    assert retired(Jlcpcb.rules, "0.4.0") == kicad_rules.RETIRED
    assert {"JLCPCB BGA pad size", "Test only rule"} <= known(Jlcpcb.rules)
    assert kicad_rules.RETIRED <= known(Jlcpcb.rules)


def test_dropped_from_another_set(newer_set: str) -> None:
    """A section of one set replaced by another's: no rule of either is
    taken for one put there by hand."""
    old = installed("jlcpcb", Jlcpcb.rules, Options(), "0.4.0")
    new = installed("jlcpcb", Jlcpcb.rules, Options(), newer_set)
    assert dropped(new, old, known(Jlcpcb.rules)) == (0, [])
    assert dropped(old, new, known(Jlcpcb.rules)) == (0, [])


def test_optional_sections_in_every_set() -> None:
    """Each section a profile's option leaves out is in every rule set: the
    option names it, in code, for every set."""
    for version in rule_sets():
        names = {n for n, _ in sections(rule_file(version, Jlcpcb.rules.dru))}
        assert set(Jlcpcb.rules.optional.values()) <= names
