"""A release through the real kicad-cli (KiCad 10), of KiCad's StickHub demo
made a two-variant board with its footprint library outside the board's
directory."""

import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from ee_tooling.fab_release import Release, main
from ee_tooling.kicad_rules import board_setup, install, installed
from ee_tooling.profiles.jlcpcb import Jlcpcb, Options
from ee_tooling.sexpr import parse
from ee_tooling.stackup_table import refresh

DEMO = Path("/usr/share/kicad/demos/stickhub")
NAME = "StickHub"
# Generic names: the board's directory and its library's, from the root.
BOARD = Path("hw/board")
LIBRARY = Path("hw/libs/fp.pretty")
# The demo's own rules, after the profile's: what its layout needs.
OWN_RULES = """
(rule "Demo silkscreen text"
\t(layer "?.Silkscreen")
\t(constraint text_height (min 0.4mm))
\t(constraint text_thickness (min 0.1mm)))
(rule "Demo hole clearance"
\t(constraint hole_clearance (min 0mm)))
(rule "Demo edge clearance"
\t(constraint edge_clearance (min 0mm)))
"""
FAB_TOML = """\
[[board]]
name = "StickHub"
directory = "hw/board"
production = "production"
profile = "jlcpcb"
variants = ["lite", "full"]

[board.fab]
ipc_class = 2
base_material = "FR-4"
tg = 130
electrical_test = "100% netlist test per IPC-9252"
"""
IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.com")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *IDENTITY, *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def board(root: Path) -> None:
    """The demo in root/BOARD: its library at LIBRARY, named
    ${KIPRJMOD}-relative; a title-block rev; every footprint placed;
    variants lite and full in the .kicad_pro, as KiCad's GUI keeps them,
    R1 DNP in lite on the board and in the schematic; the profile's rules
    installed with the demo's own; a stackup table."""
    directory = root / BOARD
    shutil.copytree(DEMO, directory)
    shutil.move(directory / "footprints.pretty", root / LIBRARY)
    table = directory / "fp-lib-table"
    table.write_text(
        table.read_text().replace("${KIPRJMOD}/footprints.pretty", "${KIPRJMOD}/../libs/fp.pretty")
    )
    pcb = directory / f"{NAME}.kicad_pcb"
    text = pcb.read_text().replace("\t(layers", '\t(title_block\n\t\t(rev "A")\n\t)\n\t(layers', 1)
    text = re.sub(r"\(attr exclude_from_pos_files\)", "(attr through_hole)", text)
    text = text.replace(
        "exclude_from_pos_files allow_soldermask_bridges", "allow_soldermask_bridges"
    )
    at = text.index('(property "Reference" "R1"')
    end = text.index("\n\t\t(embedded_fonts", at)
    text = f'{text[:end]}\n\t\t(variant\n\t\t\t(name "lite")\n\t\t\t(dnp yes)\n\t\t){text[end:]}'
    # R2's field DNP, as the schematic's below.
    at = text.index('(property "Reference" "R2"')
    text = (
        text[:at]
        + '(property "DNP" "no"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)\n'
        '\t\t\t(uuid "5d0c0a1e-0000-4000-8000-000000000002")\n'
        "\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t\t)\n\t\t" + text[at:]
    )
    pcb.write_text(refresh(text, parse(text)).text)
    sch = directory / f"{NAME}.kicad_sch"
    instance = '(reference "R1")\n\t\t\t\t\t(unit 1)'
    text = sch.read_text().replace(
        instance,
        instance
        + '\n\t\t\t\t\t(variant\n\t\t\t\t\t\t(name "lite")\n\t\t\t\t\t\t(dnp yes)\n\t\t\t\t\t)',
        1,
    )
    # R2, fitted, with a field of its own named DNP, which ${DNP} would give.
    at = text.index('(property "Reference" "R2"')
    field = (
        '(property "DNP" "no"\n\t\t\t(at 0 0 0)\n'
        "\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n\t\t)\n\t\t"
    )
    sch.write_text(text[:at] + field + text[at:])
    project = directory / f"{NAME}.kicad_pro"
    settings = json.loads(project.read_text())
    settings["schematic"]["variants"] = [{"name": "lite"}, {"name": "full"}]
    # The footprints the attributes above changed differ from the library's.
    settings["board"]["design_settings"]["rule_severities"]["lib_footprint_mismatch"] = "ignore"
    found = install(
        OWN_RULES,
        settings,
        installed("jlcpcb", Jlcpcb.rules, Options()),
        board_setup(Jlcpcb.rules),
    )
    (directory / f"{NAME}.kicad_dru").write_text(found.dru)
    project.write_text(json.dumps(found.project, indent=2) + "\n")
    (root / "fab.toml").write_text(FAB_TOML)


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="no kicad-cli (KiCad 10) on the path")
@pytest.mark.skipif(not DEMO.exists(), reason=f"no KiCad demo in {DEMO}")
def test_release_with_kicad_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each variant's BOM and placement exports take --variant (R1 DNP in
    lite only: the ${DNP} column, the CPL); the Gerbers compared (the same:
    one zip); DRC on the scratch copy, its library found beside the board's
    directory."""
    root = tmp_path / "repo"
    root.mkdir()
    board(root)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "board")
    calls: list[tuple[str, ...]] = []
    run = Release.run

    def logged(self: Release, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return run(self, *args)

    monkeypatch.setattr(Release, "run", logged)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118"])
    monkeypatch.chdir(root)
    main()
    dest = root / "production/20261118-A"
    files = sorted(f.name for f in dest.iterdir())
    assert "StickHub-A-gerbers-jlcpcb.zip" in files
    assert not [f for f in files if f.endswith("-gerbers-jlcpcb.zip") and "-lite-" in f]

    def bom(variant: str) -> dict[str, str]:
        rows = csv.DictReader(io.StringIO((dest / f"StickHub-A-{variant}-bom.csv").read_text()))
        return {ref: r["DNP"] for r in rows for ref in r["Reference"].split(",")}

    assert bom("lite")["R1"] == "yes"
    assert bom("full")["R1"] == ""
    assert bom("full")["R2"] == ""
    for variant, placed in (("lite", False), ("full", True)):
        cpl = (dest / f"StickHub-A-{variant}-jlcpcb-cpl.csv").read_text()
        assert ("\nR1," in cpl) is placed
    exports = [c for c in calls if c[:4] in (("kicad-cli", "sch", "export", "bom"),)]
    assert [c[c.index("--variant") + 1] for c in exports] == ["lite", "lite", "full", "full"]
    assert ["--exclude-dnp" in c for c in exports] == [False, True, False, True]
    placements = [c for c in calls if c[:4] == ("kicad-cli", "pcb", "export", "pos")]
    assert [c[c.index("--variant") + 1] for c in placements] == ["lite", "lite", "full", "full"]
    canary, drc = (c for c in calls if c[:3] == ("kicad-cli", "pcb", "drc"))
    assert canary[-1].endswith(f"rules/{BOARD}/{NAME}.kicad_pcb")
    assert drc[-1].endswith(f"project/{BOARD}/{NAME}.kicad_pcb")
    with zipfile.ZipFile(dest / "StickHub-A-gerbers-jlcpcb.zip") as z:
        assert "StickHub-User_Drawings.gbr" in z.namelist()
    assert "differ" not in capsys.readouterr().err
