import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from test_fabrication import FAB
from test_profiles_jlcpcb import CORRECTIONS

from ee_tooling import fab_release
from ee_tooling.fab_release import (
    Board,
    Release,
    TagPlan,
    board_footprints,
    check_variants,
    cross_checks,
    gerber_differences,
    gerber_zip,
    house_bom,
    lfs_pointer,
    lfs_pointers,
    load_config,
    located_production_errors,
    main,
    output_clash,
    pointer_message,
    profile_options,
    project_variants,
    release_name_problem,
    repository_root,
    resolved_revision,
    revision,
    settings_variants,
    silkscreen_only,
    tag_plan,
    tag_refused,
    variant_names,
    variant_text_warning,
)
from ee_tooling.fabrication import Fab, GerberFile, stackup
from ee_tooling.kicad_rules import board_setup, install, installed
from ee_tooling.kicad_style_stackup import HEADER
from ee_tooling.profiles.jlcpcb import Corrections, Jlcpcb, Options
from ee_tooling.profiles.profile import KICAD_GERBERS, GerberOptions, Part, Placement
from ee_tooling.sexpr import Expression, parse, quote
from ee_tooling.stackup_table import layer_names, stackup_rows

CONFIG = """
[[board]]
name = "main"
directory = "hardware/main"
production = "production/main"
sources = ["hardware/main/main.kicad_sym", "docs"]
profile = "jlcpcb"

[board.fab]
ipc_class = 2
base_material = "FR-4"
tg = 150
electrical_test = "100% netlist test per IPC-9252"

[[board]]
name = "jig"
directory = "hardware/jig"
production = "production/jig"
profile = "jlcpcb"

[board.fab]
ipc_class = 2
base_material = "FR-4"
tg = 150
electrical_test = "100% netlist test per IPC-9252"
"""


def test_config_boards_in_order() -> None:
    boards = load_config(CONFIG)
    assert list(boards) == ["main", "jig"]
    main = boards["main"]
    assert main.pcb == Path("hardware/main/main.kicad_pcb")
    assert main.sch == Path("hardware/main/main.kicad_sch")
    assert boards["jig"].sources == ()
    assert main.profile == "jlcpcb"
    assert main.fab == Fab(2, "FR-4", 150.0, "100% netlist test per IPC-9252")


# A board's [board.fab] as an inline table, so keys can follow it.
FAB_TABLE = (
    'fab = { ipc_class = 2, base_material = "FR-4", tg = 150,'
    ' electrical_test = "100% netlist test per IPC-9252" }'
)
# A board with every key, for the config's error tests.
BOARD = f"""\
[[board]]
name = "b"
directory = "hw"
production = "production"
profile = "jlcpcb"
{FAB_TABLE}
"""


def test_config_without_boards() -> None:
    with pytest.raises(ValueError, match="no \\[\\[board\\]\\]"):
        load_config("")


@pytest.mark.parametrize("key", ["name", "directory", "production", "profile", "fab"])
def test_config_missing_key(key: str) -> None:
    text = "".join(line + "\n" for line in BOARD.splitlines() if not line.startswith(key))
    with pytest.raises(ValueError, match=f"no '{key}'$|no \\[board.{key}\\]$"):
        load_config(text)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (f"version = 1\n{BOARD}", r"^unknown key 'version'$"),
        (f'{BOARD}colour = "red"\n', r"^\[\[board\]\] 'b': unknown key 'colour'$"),
        (f"{BOARD}[board.pcbway]\n", r"^\[\[board\]\] 'b': unknown key 'pcbway'$"),
        (BOARD.replace('"jlcpcb"', '"pcbway"'), r"profile 'pcbway' is not one of jlcpcb$"),
        (BOARD.replace('"hw"', "1"), r"'directory' is not a string$"),
        (
            f'{BOARD}copper = ["F.Cu"]\n',
            r"^\[\[board\]\] 'b': 'copper' is gone: the copper layers come from the board's"
            " stackup$",
        ),
        (f"{BOARD}sources = [1]\n", r"'sources' is not a list of strings$"),
        (
            BOARD.replace('"jlcpcb"', '["jlcpcb"]'),
            r"^\[\[board\]\] 'b': 'profile' is not a string$",
        ),
        (BOARD.replace('"jlcpcb"', "{ x = 1 }"), r"^\[\[board\]\] 'b': 'profile' is not a string$"),
        *(
            (
                f'{BOARD}sources = ["{x}"]\n',
                rf"^\[\[board\]\] 'b': source '{x}' is outside the repository$",
            )
            for x in ("/home/lib", "..", "../lib", "docs/../../lib")
        ),
        (BOARD + BOARD, r"^\[\[board\]\] 'b' is there twice$"),
        ('board = "b"\n', r"no \[\[board\]\]"),
        ('board = ["b"]\n', r"no \[\[board\]\]"),
    ],
)
def test_config_errors(text: str, error: str) -> None:
    """Each bad config is an error naming what is wrong."""
    with pytest.raises(ValueError, match=error):
        load_config(text)


def test_config_profile_options() -> None:
    """The profile's table, beside the board's keys, unchecked until the
    profile checks it."""
    b = load_config(f"{BOARD}[board.jlcpcb]\nrequire_lcsc = true\nlater = 1\n")["b"]
    assert b.options == {"require_lcsc": True, "later": 1}
    assert load_config(BOARD)["b"].options == {}
    with pytest.raises(ValueError, match=r"^\[\[board\]\] 'b': 'jlcpcb' is not a table$"):
        load_config(f"{BOARD}jlcpcb = true\n")


@pytest.mark.parametrize(
    ("table", "error"),
    [
        ("later = 1", "unknown key 'later'"),
        ('require_lcsc = "yes"', "'require_lcsc' is not true or false"),
    ],
)
def test_profile_options_refused(table: str, error: str) -> None:
    boards = load_config(f"{BOARD}[board.jlcpcb]\n{table}\n")
    with pytest.raises(ValueError, match=rf"^\[\[board\]\] 'b': \[board.jlcpcb\]: {error}$"):
        profile_options(boards, {"jlcpcb": Jlcpcb(CORRECTIONS)})


def test_profile_options_default() -> None:
    boards = load_config(BOARD)
    assert profile_options(boards, {"jlcpcb": Jlcpcb(CORRECTIONS)}) == {"b": Options()}


def test_config_sources_inside() -> None:
    """A path that only passes through .. stays inside."""
    text = f'{BOARD}sources = ["docs/../lib", "./docs", "..lib"]\n'
    assert load_config(text)["b"].sources == (Path("docs/../lib"), Path("docs"), Path("..lib"))


def fab_table(**changes: object) -> str:
    """BOARD with its [board.fab] changed: a key set, or with None, removed."""
    table = {"ipc_class": 2, "base_material": '"FR-4"', "tg": 150, "electrical_test": '"x"'}
    table.update(changes)
    keys = ", ".join(f"{k} = {v}" for k, v in table.items() if v is not None)
    return BOARD.replace(FAB_TABLE, f"fab = {{ {keys} }}")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"tg": None}, "no 'tg'"),
        ({"tg": None, "ipc_class": None}, "no 'ipc_class', 'tg'"),
        ({"colour": '"red"'}, "unknown key 'colour'"),
        ({"ipc_class": 4}, "'ipc_class' is not one of 1, 2, 3"),
        ({"ipc_class": '"2"'}, "'ipc_class' is not one of 1, 2, 3"),
        ({"ipc_class": "true"}, "'ipc_class' is not one of 1, 2, 3"),
        ({"tg": 0}, "'tg' is not a temperature, deg C"),
        ({"tg": '"150"'}, "'tg' is not a temperature, deg C"),
        ({"tg": "nan"}, "'tg' is not a temperature, deg C"),
        ({"tg": "inf"}, "'tg' is not a temperature, deg C"),
        ({"base_material": '" "'}, "'base_material' is not a string, or is empty"),
        ({"electrical_test": 1}, "'electrical_test' is not a string, or is empty"),
    ],
)
def test_config_fab_refused(changes: dict[str, object], error: str) -> None:
    with pytest.raises(ValueError, match=rf"^\[\[board\]\] 'b': \[board.fab\]: {error}$"):
        load_config(fab_table(**changes))


def test_config_fab_values() -> None:
    assert load_config(fab_table(tg=135.5, ipc_class=3))["b"].fab == Fab(3, "FR-4", 135.5, "x")
    with pytest.raises(ValueError, match=r"^\[\[board\]\] 'b': 'fab' is not a table$"):
        load_config(BOARD.replace(FAB_TABLE, "fab = 1"))


def test_config_board_without_name() -> None:
    with pytest.raises(ValueError, match=r"^\[\[board\]\] 1: no 'name'$"):
        load_config(BOARD.replace('name = "b"\n', ""))


def test_source_paths_only_present_project_files(tmp_path: Path) -> None:
    d = tmp_path / "hardware/main"
    (d / "main.pretty").mkdir(parents=True)
    for f in ("main.kicad_pcb", "main.kicad_sch", "main.kicad_pro", "fp-lib-table"):
        (d / f).touch()
    b = load_config(CONFIG)["main"]
    assert b.source_paths(tmp_path) == [
        Path("hardware/main/main.kicad_pcb"),
        Path("hardware/main/main.kicad_sch"),
        Path("hardware/main/main.kicad_pro"),
        Path("hardware/main/main.pretty"),
        Path("hardware/main/fp-lib-table"),
        Path("hardware/main/main.kicad_sym"),
        Path("docs"),
    ]


def test_house_bom() -> None:
    """Fitted parts grouped on every field but the reference; a DNP part a
    row of its own at Qty 0, never with fitted parts or other DNP parts."""
    rows = house_bom(
        [
            Part("C1", "100n", "lib:C", "C1", False, "M1", "Maker", "d"),
            Part("C2", "100n", "lib:C", "C1", True, "M1", "Maker", "d"),
            Part("C3", "100n", "lib:C", "C1", False, "M1", "Maker", "d"),
            Part("C4", "100n", "lib:C", "C1", False, "M2", "Maker", "d"),
            Part("C5", "100n", "lib:C", "C1", True, "M1", "Maker", "d"),
            Part("R1", "0R", "lib:R", ""),
        ]
    )
    assert rows == [
        ["C1,C3", "100n", "lib:C", "2", "", "C1", "M1", "Maker", "d"],
        ["C2", "100n", "lib:C", "0", "yes", "C1", "M1", "Maker", "d"],
        ["C4", "100n", "lib:C", "1", "", "C1", "M2", "Maker", "d"],
        ["C5", "100n", "lib:C", "0", "yes", "C1", "M1", "Maker", "d"],
        ["R1", "0R", "lib:R", "1", "", "", "", "", ""],
    ]
    assert house_bom([]) == []


def test_gerber_zip_name() -> None:
    """The profile's name, unless its Gerber options are kicad-cli's own."""
    profile = Jlcpcb(CORRECTIONS)
    assert gerber_zip("b-1", profile) == "b-1-gerbers-jlcpcb.zip"
    profile.gerbers = KICAD_GERBERS
    assert gerber_zip("b-1", profile) == "b-1-gerbers.zip"


def test_revision() -> None:
    pcb = '(kicad_pcb (title_block (title "x") (date "d") (rev "1.2.3")) (net 0 ""))'
    assert revision(pcb) == "1.2.3"
    assert revision('(kicad_pcb (rev "9"))') is None


def test_board_is_frozen() -> None:
    b = Board("x", Path("d"), Path("p"), (), "jlcpcb", FAB)
    with pytest.raises(AttributeError):
        b.name = "y"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("distributor", "flags"),
    [
        (("LCSC", "LCSC"), ["--bom-col-dist-pn", "LCSC", "--bom-col-dist", "LCSC"]),
        (None, []),
    ],
)
def test_ipc2581_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distributor: tuple[str, str] | None,
    flags: list[str],
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(Release, "kicad", lambda self, *args: calls.append(args))
    r = Release(tmp_path, load_config(CONFIG)["jig"], tmp_path / "work")
    out = tmp_path / "jig-1-ipc2581.zip"
    r.ipc2581(out, distributor)
    assert calls == [
        (
            "pcb",
            "export",
            "ipc2581",
            "--version",
            "C",
            "--units",
            "mm",
            "--precision",
            "6",
            "--compress",
            "--bom-col-mfg-pn",
            "MPN",
            "--bom-col-mfg",
            "Manufacturer",
            *flags,
            "-o",
            str(out),
            str(tmp_path / "hardware/jig/jig.kicad_pcb"),
        )
    ]


def gerber_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: GerberOptions
) -> list[list[str]]:
    """kicad-cli's Gerber and drill calls for options."""
    calls: list[list[str]] = []

    def kicad(self: Release, *args: str) -> None:
        calls.append(list(args))
        out = Path(args[args.index("-o") + 1])
        for name, text in (FAKE_GERBERS if args[2] == "gerbers" else FAKE_DRILL).items():
            (out / name).write_text(text)

    monkeypatch.setattr(Release, "kicad", kicad)
    b = Board("b", Path("hw"), Path("production"), (), "jlcpcb", FAB)
    work = tmp_path / str(options)
    work.mkdir()
    Release(tmp_path, b, work, ("F.Cu", "In1.Cu", "B.Cu")).export_gerbers(work / "g", options)
    return [[a.replace(str(work), "WORK") for a in c] for c in calls]


@pytest.mark.parametrize(
    ("option", "call", "removed", "added"),
    [
        ("x2", 0, "--no-x2", None),
        ("subtract_soldermask", 0, "--subtract-soldermask", None),
        ("protel_extensions", 0, None, "--no-protel-ext"),
        ("separate_drill", 1, "--excellon-separate-th", None),
    ],
)
def test_gerber_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    call: int,
    removed: str | None,
    added: str | None,
) -> None:
    """Each Gerber option the other way from JLCPCB's: one flag removed or
    added, before --check-zones, and nothing else changed."""
    options = Jlcpcb.gerbers
    expected = gerber_calls(tmp_path, monkeypatch, options)
    if removed is not None:
        expected[call].remove(removed)
    if added is not None:
        expected[call].insert(expected[call].index("--check-zones"), added)
    flipped = replace(options, **{option: not getattr(options, option)})
    assert gerber_calls(tmp_path, monkeypatch, flipped) == expected


def test_odb_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(Release, "kicad", lambda self, *args: calls.append(args))
    r = Release(tmp_path, load_config(CONFIG)["jig"], tmp_path / "work")
    out = tmp_path / "jig-1-odb.tgz"
    r.odb(out)
    assert calls == [
        (
            "pcb",
            "export",
            "odb",
            "--units",
            "mm",
            "--precision",
            "6",
            "--compression",
            "tgz",
            "--check-zones",
            "-o",
            str(out),
            str(tmp_path / "hardware/jig/jig.kicad_pcb"),
        )
    ]


@pytest.mark.parametrize(
    ("dirty", "tagged", "retag", "plan"),
    [
        (True, None, False, TagPlan.DIRTY),
        (True, "b" * 40, False, TagPlan.DIRTY),
        (False, None, False, TagPlan.CREATE),
        (False, None, True, TagPlan.CREATE),
        (False, "a" * 40, False, TagPlan.PRESENT),
        (False, "b" * 40, False, TagPlan.REFUSE),
        (False, "b" * 40, True, TagPlan.MOVE),
    ],
)
def test_tag_plan(dirty: bool, tagged: str | None, retag: bool, plan: TagPlan) -> None:
    assert tag_plan(dirty, tagged, "a" * 40, retag) is plan


# A git identity for the test's commits and tags, which CI lacks.
IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An annotated tag on HEAD, found by tagged, and moved only when asked."""
    for k, v in IDENTITY.items():
        monkeypatch.setenv(k, v)
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "one")
    r = Release(tmp_path, load_config(CONFIG)["jig"], tmp_path / "work")
    name = "jig-20261102-1"
    assert r.tagged(name) is None
    first = r.head()
    r.tag(name, "jig 1: fab release production/20261102-1", move=False)
    assert r.tagged(name) == first
    assert git(tmp_path, "cat-file", "-t", f"refs/tags/{name}") == "tag"
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "two")
    with pytest.raises(SystemExit):
        r.tag(name, "again", move=False)
    assert r.tagged(name) == first
    r.tag(name, "moved", move=True)
    assert r.tagged(name) == r.head() != first


# A release of a fixture board from start to end, kicad-cli being a fake
# that logs its arguments and writes FAKE_OUTPUTS: each export's files, by
# its subcommand, as name to contents, "" naming the -o file itself.  The
# placement export without --exclude-dnp is "pcb export pos all"; a
# variant's export, if it differs from the default's, is "<subcommand>
# @<variant>".
FAKE_KICAD_CLI = """
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["FAKE_KICAD_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
key = " ".join(args[:3] if args[1] == "export" else args[:2])
if key == "pcb export pos" and "--exclude-dnp" not in args:
    key += " all"
outputs = json.loads(os.environ["FAKE_OUTPUTS"])
if "--variant" in args:
    variant = f"{key} @{args[args.index('--variant') + 1]}"
    key = variant if variant in outputs else key
out = pathlib.Path(args[args.index("-o") + 1])
if key.startswith("sch export bom") and "--exclude-dnp" in args:
    # kicad-cli's BOM of the fitted parts: the fake's BOM's, by its DNP column.
    import csv, io
    rows = list(csv.reader(io.StringIO(outputs[key][""])))
    keep = [rows[0]] + [r for r in rows[1:] if r[rows[0].index("DNP")] == ""]
    text = io.StringIO()
    csv.writer(text, quoting=csv.QUOTE_ALL, lineterminator="\\n").writerows(keep)
    outputs = {key: {"": text.getvalue()}}
# As kicad-cli 10.0.8 does: NAME.kicad_prl beside the project it reads.
board = pathlib.Path(args[-1])
board.with_suffix(".kicad_prl").write_text("{}\\n")
for name, text in outputs[key].items():
    (out / name if name else out).write_text(text)
sys.exit(5 if key in os.environ.get("FAKE_FAIL", "").split(",") else 0)
"""

# What the fixture board's kicad-cli exports hold, in kicad-cli 10's forms.
FAKE_GERBERS = {
    "b-job.gbrjob": json.dumps(
        {
            "FilesAttributes": [
                {"Path": "b-F_Cu.gtl", "FileFunction": "Copper,L1,Top", "FilePolarity": "Positive"},
                {
                    "Path": "b-In1_Cu.g1",
                    "FileFunction": "Copper,L2,Inr",
                    "FilePolarity": "Positive",
                },
                {"Path": "b-B_Cu.gbl", "FileFunction": "Copper,L3,Bot", "FilePolarity": "Positive"},
                {
                    "Path": "b-F_Mask.gts",
                    "FileFunction": "SolderMask,Top",
                    "FilePolarity": "Negative",
                },
                {
                    "Path": "b-B_Mask.gbs",
                    "FileFunction": "SolderMask,Bot",
                    "FilePolarity": "Negative",
                },
                {
                    "Path": "b-F_Silkscreen.gto",
                    "FileFunction": "Legend,Top",
                    "FilePolarity": "Positive",
                },
                {
                    "Path": "b-User_Drawings.gbr",
                    "FileFunction": "Other,User",
                    "FilePolarity": "Positive",
                },
                {"Path": "b-Edge_Cuts.gm1", "FileFunction": "Profile,NP"},
            ],
            "DesignRules": [{"Layers": "Outer", "PadToPad": 0.2, "MinLineWidth": 0.15}],
        }
    ),
    "b-F_Cu.gtl": "G04 F.Cu*\nX1000Y2000D02*\n",
    "b-In1_Cu.g1": "G04 In1.Cu*\nX2000Y2000D02*\n",
    "b-B_Cu.gbl": "G04 B.Cu*\nX3000Y2000D02*\n",
    "b-F_Mask.gts": "G04 F.Mask*\nX4000Y2000D02*\n",
    "b-B_Mask.gbs": "G04 B.Mask*\nX5000Y2000D02*\n",
    "b-F_Silkscreen.gto": "G04 F.Silkscreen*\nX6000Y2000D02*\n",
    "b-User_Drawings.gbr": "G04 User.Drawings*\nX7000Y2000D02*\n",
    "b-Edge_Cuts.gm1": "G04 Edge.Cuts*\nX8000Y2000D02*\n",
}
FAKE_DRILL = {
    "b-PTH.drl": "M48\n; #@! TF.FileFunction,Plated,1,3,PTH\nMETRIC\nT1C0.300\n%\n",
    "b-NPTH.drl": "M48\n; #@! TF.FileFunction,NonPlated,1,3,NPTH\nMETRIC\nT1C3.200\n%\n",
}
FAKE_BOM = """\
"Reference","Value","Footprint","LCSC","MPN","Manufacturer","Datasheet","DNP"
"C1","100n","Capacitor_SMD:C_0402_1005Metric","C1525","CL05B104KO5NNNC","Samsung","",""
"C2","100n","Capacitor_SMD:C_0402_1005Metric","C1525","CL05B104KO5NNNC","Samsung","",""
"C3","100n","Capacitor_SMD:C_0402_1005Metric","C307331","","","",""
"C4","100n","Capacitor_SMD:C_0402_1005Metric","C1525","CL05B104KO5NNNC","Samsung","","DNP"
"C10","100n","Capacitor_SMD:C_0402_1005Metric","C1525","CL05B104KO5NNNC","Samsung","",""
"D1","LED","LED_SMD:LED_0603_1608Metric","C2","","","",""
"J1","Conn_01x02","Connector_JST:JST_SH_SM02B","C3","","","",""
"R1","10k","Resistor_SMD:R_0402_1005Metric","C25744","","","",""
"R2","10k","Resistor_SMD:R_0402_1005Metric","C25744","","","",""
"R3","10k","Resistor_SMD:R_0402_1005Metric","C25744","","","","DNP"
"R4","10k","Resistor_SMD:R_0402_1005Metric","C25744","","","","DNP"
"TP1","TP","TestPoint:TestPoint_Pad_D1.0mm","","","","",""
"TP2","TP","TestPoint:TestPoint_Pad_D1.0mm","","","","",""
"U1","TPS1","Package_TO_SOT_SMD:SOT-23-5","C1","TPS1-Q1","TI","https://ti.com/tps1.pdf",""
"U2","NEW1","Package_SO:SOIC-8_3.9x4.9mm_P1.27mm","C4","","","",""
"""
FAKE_POS = """\
Ref,Val,Package,PosX,PosY,Rot,Side
"C1","100n","C_0402_1005Metric",101.500000,-60.250000,90.000000,top
"C2","100n","C_0402_1005Metric",103.500000,-60.250000,-90.000000,top
"C3","100n","C_0402_1005Metric",105.000000,-62.000000,0.000000,top
"C10","100n","C_0402_1005Metric",107.000000,-62.000000,180.000000,top
"D1","LED","LED_0603_1608Metric",110.000000,-70.000000,0.000000,bottom
"H1","MountingHole","MountingHole_3.2mm_M3",95.000000,-55.000000,0.000000,top
"J1","Conn_01x02","JST_SH_SM02B",120.000000,-65.000000,90.000000,top
"R1","10k","R_0402_1005Metric",104.000000,-58.000000,0.000000,top
"R2","10k","R_0402_1005Metric",106.000000,-58.000000,270.000000,bottom
"TP1","TP","TestPoint_Pad_D1.0mm",98.000000,-75.000000,0.000000,bottom
"TP2","TP","TestPoint_Pad_D1.0mm",99.000000,-75.000000,0.000000,bottom
"U1","TPS1","SOT-23-5",112.250000,-61.125000,45.000000,top
"U2","NEW1","SOIC-8_3.9x4.9mm_P1.27mm",115.000000,-61.000000,0.000000,top
"""
# The placement export with the DNP parts too.
FAKE_POS_ALL = (
    FAKE_POS
    + """\
"C4","100n","C_0402_1005Metric",108.000000,-62.000000,0.000000,top
"R3","10k","R_0402_1005Metric",108.000000,-58.000000,0.000000,top
"R4","10k","R_0402_1005Metric",109.000000,-58.000000,0.000000,top
"""
)
FAKE_OUTPUTS = {
    "sch erc": {"": "ERC report\n"},
    "pcb drc": {"": "DRC report\n"},
    "pcb export gerbers": FAKE_GERBERS,
    "pcb export drill": FAKE_DRILL,
    "pcb export odb": {"": "ODB++ job\n"},
    "pcb export ipc2581": {"": "IPC-2581 zip\n"},
    "sch export bom": {"": FAKE_BOM},
    "pcb export pos": {"": FAKE_POS},
    "pcb export pos all": {"": FAKE_POS_ALL},
    "sch export pdf": {"": "schematic PDF\n"},
    "pcb export pdf": {"": "board PDF\n"},
}

# The fixture board's repository: its files, by path from the root.
FIXTURE = {
    "fab.toml": """\
[[board]]
name = "b"
directory = "hw"
production = "production"
sources = ["docs"]
profile = "jlcpcb"

[board.fab]
ipc_class = 2
base_material = "FR-4"
tg = 150
electrical_test = "100% netlist test per IPC-9252"
""",
    "hw/b.kicad_pcb": """\
(kicad_pcb (version 20250114) (general (thickness 1.6))
  (title_block (title "b") (rev "1"))
  (setup
    (stackup
      (layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4")
        (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.0175))
      (layer "dielectric 2" (type "core") (thickness 1.2) (material "FR4")
        (epsilon_r 4.6) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (color "Green") (thickness 0.01))
      (copper_finish "ENIG"))))
""",
    "hw/b.kicad_sch": "(kicad_sch)\n",
    "hw/b.kicad_pro": json.dumps(
        {
            "board": {
                "design_settings": {
                    "rules": {
                        "min_clearance": 0.15,
                        "min_copper_edge_clearance": 0.2,
                        "min_hole_clearance": 0.25,
                    }
                }
            },
            "net_settings": {
                "classes": [
                    {"name": "Default", "track_width": 0.2},
                    {"name": "90Z_DIFF_USB", "diff_pair_width": 0.2, "diff_pair_gap": 0.15},
                ]
            },
        }
    ),
    "hw/fp-lib-table": "(fp_lib_table)\n",
    "docs/notes.txt": "notes\n",
}


def stackup_table(pcb: str) -> str:
    """A stackup table matching the board's stackup, as kicad-style-check
    reads one: a table headed HEADER on User.Drawings."""
    board = parse(pcb)
    found = stackup(board)
    assert found is not None
    texts = [list(HEADER), *stackup_rows(found, layer_names(board))]
    cells = " ".join(f"(table_cell {quote(c)})" for row in texts for c in row)
    return f'(table (column_count {len(HEADER)}) (layer "Dwgs.User") (cells {cells}))'


def with_stackup_table(pcb: str) -> str:
    """The board's text with a stackup table matching its stackup."""
    return pcb.rstrip().removesuffix(")") + f"\n  {stackup_table(pcb)})\n"


FIXTURE["hw/b.kicad_pcb"] = with_stackup_table(FIXTURE["hw/b.kicad_pcb"])
# The board's own rule, after the rules adopt installed.
OWN_RULE = '(rule "Board edge"\n\t(constraint edge_clearance (min 0.5mm)))\n'
SECTION = installed("jlcpcb", Jlcpcb.rules, Options())
FIXTURE["hw/b.kicad_dru"] = f"(version 1)\n{SECTION}\n{OWN_RULE}"


# The JLCPCB profile's warnings of the fixture's parts, on stderr.
PROFILE_WARNINGS = (
    "warning: no LCSC part number for TP1,TP2\n"
    "warning: check a new part (C4) in JLCPCB's placement preview: no correction known\n"
)


def profile_quiet(err: str) -> str:
    """stderr without the profile's warnings of the fixture's parts, in
    any build."""
    return "".join(
        line
        for line in err.splitlines(keepends=True)
        if "no LCSC part number for TP1,TP2" not in line and "placement preview" not in line
    )


def builtin_corrections(cls: type[Corrections]) -> Corrections:
    return CORRECTIONS


def release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builtin: Callable[[type[Corrections]], Corrections] = builtin_corrections,
    args: tuple[str, ...] = (),
    config: str = "",
) -> tuple[Path, list[list[str]]]:
    """Release the fixture board with the fake kicad-cli, builtin for
    Corrections.builtin, args after --date and config added to its
    fab.toml; return the repository and kicad-cli's calls, the root as ROOT
    and the work directory as WORK."""
    root = tmp_path / "repo"
    for name, text in {**FIXTURE, "fab.toml": FIXTURE["fab.toml"] + config}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    for k, v in IDENTITY.items():
        monkeypatch.setenv(k, v)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "board")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "kicad-cli"
    fake.write_text(f"#!{sys.executable}\n{FAKE_KICAD_CLI}")
    fake.chmod(0o755)
    log = tmp_path / "kicad-cli.log"
    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KICAD_LOG", str(log))
    monkeypatch.setenv("FAKE_OUTPUTS", json.dumps(FAKE_OUTPUTS))
    monkeypatch.setattr(tempfile, "tempdir", str(work))
    monkeypatch.setattr(Corrections, "builtin", classmethod(builtin))
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", *args])
    monkeypatch.chdir(root)
    main()
    return root, kicad_calls(tmp_path)


def kicad_calls(tmp_path: Path) -> list[list[str]]:
    """The fake kicad-cli's calls in release(tmp_path, ...), the root as
    ROOT and the work directory as WORK."""
    log, work, root = tmp_path / "kicad-cli.log", tmp_path / "tmp", tmp_path / "repo"
    if not log.exists():
        return []
    text = re.sub(re.escape(str(work)) + r"/[^/\"]+", "WORK", log.read_text())
    return [json.loads(line) for line in text.replace(str(root), "ROOT").splitlines()]


RELEASE_CALLS = [
    ["sch", "erc", "--exit-code-violations", "-o", "WORK/erc.rpt", "WORK/board/hw/b.kicad_sch"],
    [
        "pcb",
        "drc",
        "--schematic-parity",
        "--exit-code-violations",
        "-o",
        "WORK/drc.rpt",
        # The scratch copy, at the board directory's path in the repository.
        "WORK/project/hw/b.kicad_pcb",
    ],
    [
        "sch",
        "export",
        "bom",
        "--fields",
        "Reference,Value,Footprint,LCSC,MPN,Manufacturer,Datasheet",
        "-o",
        "WORK/bom.csv",
        "WORK/board/hw/b.kicad_sch",
    ],
    # The fitted parts: the DNP attribute, which a field cannot shadow.
    [
        "sch",
        "export",
        "bom",
        "--fields",
        "Reference,Value,Footprint,LCSC,MPN,Manufacturer,Datasheet",
        "--exclude-dnp",
        "-o",
        "WORK/bom-fitted.csv",
        "WORK/board/hw/b.kicad_sch",
    ],
    [
        "pcb",
        "export",
        "pos",
        "--format",
        "csv",
        "--units",
        "mm",
        "--side",
        "both",
        "--exclude-dnp",
        "-o",
        "WORK/pos.csv",
        "WORK/board/hw/b.kicad_pcb",
    ],
    # Every placement, DNP too, for the schematic-board DNP cross-check.
    [
        "pcb",
        "export",
        "pos",
        "--format",
        "csv",
        "--units",
        "mm",
        "--side",
        "both",
        "-o",
        "WORK/pos-all.csv",
        "WORK/board/hw/b.kicad_pcb",
    ],
    [
        "pcb",
        "export",
        "gerbers",
        "--layers",
        "F.Cu,In1.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts,"
        "User.Drawings",
        "--no-x2",
        "--subtract-soldermask",
        "--check-zones",
        "-o",
        "WORK/gerbers",
        "WORK/board/hw/b.kicad_pcb",
    ],
    [
        "pcb",
        "export",
        "drill",
        "--format",
        "excellon",
        "--excellon-units",
        "mm",
        "--excellon-zeros-format",
        "decimal",
        "--excellon-oval-format",
        "alternate",
        "--excellon-separate-th",
        "--drill-origin",
        "absolute",
        "-o",
        "WORK/gerbers/",
        "WORK/board/hw/b.kicad_pcb",
    ],
    [
        "pcb",
        "export",
        "odb",
        "--units",
        "mm",
        "--precision",
        "6",
        "--compression",
        "tgz",
        "--check-zones",
        "-o",
        "ROOT/production/.20261118-1.building/b-1-odb.tgz",
        "WORK/board/hw/b.kicad_pcb",
    ],
    [
        "pcb",
        "export",
        "ipc2581",
        "--version",
        "C",
        "--units",
        "mm",
        "--precision",
        "6",
        "--compress",
        "--bom-col-mfg-pn",
        "MPN",
        "--bom-col-mfg",
        "Manufacturer",
        "--bom-col-dist-pn",
        "LCSC",
        "--bom-col-dist",
        "LCSC",
        "-o",
        "ROOT/production/.20261118-1.building/b-1-ipc2581.zip",
        "WORK/board/hw/b.kicad_pcb",
    ],
    ["sch", "export", "pdf", "-o", "WORK/pdf/b-schematic.pdf", "WORK/board/hw/b.kicad_sch"],
    [
        "pcb",
        "export",
        "pdf",
        "--mode-multipage",
        "--layers",
        "F.Cu,In1.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Fab,B.Fab,User.Drawings",
        "--common-layers",
        "Edge.Cuts",
        "-o",
        "WORK/pdf/b-board.pdf",
        "WORK/board/hw/b.kicad_pcb",
    ],
]

# The fixture's release: each text file, by name.
RELEASE_TEXT = {
    "b-1-bom.csv": """\
Reference,Value,Footprint,Qty,DNP,LCSC,MPN,Manufacturer,Datasheet\r
"C1,C2,C10",100n,Capacitor_SMD:C_0402_1005Metric,3,,C1525,CL05B104KO5NNNC,Samsung,\r
C3,100n,Capacitor_SMD:C_0402_1005Metric,1,,C307331,,,\r
C4,100n,Capacitor_SMD:C_0402_1005Metric,0,yes,C1525,CL05B104KO5NNNC,Samsung,\r
D1,LED,LED_SMD:LED_0603_1608Metric,1,,C2,,,\r
J1,Conn_01x02,Connector_JST:JST_SH_SM02B,1,,C3,,,\r
"R1,R2",10k,Resistor_SMD:R_0402_1005Metric,2,,C25744,,,\r
R3,10k,Resistor_SMD:R_0402_1005Metric,0,yes,C25744,,,\r
R4,10k,Resistor_SMD:R_0402_1005Metric,0,yes,C25744,,,\r
"TP1,TP2",TP,TestPoint:TestPoint_Pad_D1.0mm,2,,,,,\r
U1,TPS1,Package_TO_SOT_SMD:SOT-23-5,1,,C1,TPS1-Q1,TI,https://ti.com/tps1.pdf\r
U2,NEW1,Package_SO:SOIC-8_3.9x4.9mm_P1.27mm,1,,C4,,,\r
""",
    "b-1-jlcpcb-bom.csv": """\
Comment,Designator,Footprint,LCSC Part #\r
100n,"C1,C2,C10",C_0402_1005Metric,C1525\r
100n,C3,C_0402_1005Metric,C307331\r
LED,D1,LED_0603_1608Metric,C2\r
Conn_01x02,J1,JST_SH_SM02B,C3\r
10k,"R1,R2",R_0402_1005Metric,C25744\r
TP,"TP1,TP2",TestPoint_Pad_D1.0mm,\r
TPS1,U1,SOT-23-5,C1\r
NEW1,U2,SOIC-8_3.9x4.9mm_P1.27mm,C4\r
""",
    "b-1-jlcpcb-cpl.csv": """\
Designator,Val,Package,Mid X,Mid Y,Rotation,Layer\r
C1,100n,C_0402_1005Metric,101.5000mm,-60.2500mm,90,Top\r
C2,100n,C_0402_1005Metric,103.5000mm,-60.2500mm,270,Top\r
C3,100n,C_0402_1005Metric,105.0000mm,-62.0000mm,0,Top\r
C10,100n,C_0402_1005Metric,107.0000mm,-62.0000mm,180,Top\r
D1,LED,LED_0603_1608Metric,110.0000mm,-70.0000mm,180,Bottom\r
J1,Conn_01x02,JST_SH_SM02B,118.5000mm,-65.0000mm,90,Top\r
R1,10k,R_0402_1005Metric,104.0000mm,-58.0000mm,0,Top\r
R2,10k,R_0402_1005Metric,106.0000mm,-58.0000mm,270,Bottom\r
TP1,TP,TestPoint_Pad_D1.0mm,98.0000mm,-75.0000mm,0,Bottom\r
TP2,TP,TestPoint_Pad_D1.0mm,99.0000mm,-75.0000mm,0,Bottom\r
U1,TPS1,SOT-23-5,112.2500mm,-61.1250mm,315,Top\r
U2,NEW1,SOIC-8_3.9x4.9mm_P1.27mm,115.0000mm,-61.0000mm,0,Top\r
""",
    "b-1-gerber-map.txt": """\
b 1: the files in b-1-gerbers-jlcpcb.zip

Gerber RS-274X, mm, 4.6 format; Excellon drill, mm, decimal; finished hole sizes.
Copper layers L1 (top) to Ln (bottom).

File                 Layer          Function                            Side    Polarity
-------------------  -------------  ----------------------------------  ------  --------
b-F_Cu.gtl           F.Cu           copper L1                           top     positive
b-In1_Cu.g1          In1.Cu         copper L2                           inner   positive
b-B_Cu.gbl           B.Cu           copper L3                           bottom  positive
b-F_Mask.gts         F.Mask         solder mask                         top     negative
b-B_Mask.gbs         B.Mask         solder mask                         bottom  negative
b-F_Silkscreen.gto   F.Silkscreen   silkscreen                          top     positive
b-User_Drawings.gbr  User.Drawings  drawings                            -       positive
b-Edge_Cuts.gm1      Edge.Cuts      board outline                       -       -
b-NPTH.drl           -              drill, non-plated (NPTH), L1 to L3  -       -
b-PTH.drl            -              drill, plated (PTH), L1 to L3       -       -
b-job.gbrjob         -              Gerber job file (JSON)              -       -
""",
    "b-1-stackup.txt": """\
b 1: stackup

Ln  Layer         Type                Material  Thickness mm  Er   Loss tan  Color  File
--  ------------  ------------------  --------  ------------  ---  --------  -----  ------------
-   F.Mask        Top Solder Mask     -         0.01          -    -         Green  b-F_Mask.gts
L1  F.Cu          copper              -         0.035         -    -         -      b-F_Cu.gtl
-   dielectric 1  prepreg             FR4       0.2           4.4  0.02      -      -
L2  In1.Cu        copper              -         0.0175        -    -         -      b-In1_Cu.g1
-   dielectric 2  core                FR4       1.2           4.6  0.02      -      -
L3  B.Cu          copper              -         0.035         -    -         -      b-B_Cu.gbl
-   B.Mask        Bottom Solder Mask  -         0.01          -    -         Green  b-B_Mask.gbs

Total thickness: 1.5075 mm (board thickness setting: 1.6 mm).
Copper finish: ENIG

Impedance requirements, from the net classes.  JLCPCB's impedance calculator
sets the widths for its stackup; the design's are below.

Net class     Ohms  Kind          Track mm  Pair width mm  Pair gap mm
------------  ----  ------------  --------  -------------  -----------
90Z_DIFF_USB  90    differential  -         0.2            0.15
""",
    "b-1-fab-notes.txt": """\
b 1: fabrication notes
Unless otherwise specified.

1.  STANDARD: build and inspect to IPC-6012 and IPC-A-600, class 2.
2.  BASE MATERIAL: FR-4; Tg 150 deg C minimum.
3.  LAYERS: 3 copper, L1 (top) to L3 (bottom), per b-1-gerber-map.txt.
4.  THICKNESS: finished board 1.5075 mm, per b-1-stackup.txt.
5.  COPPER: finished weights L1 35 um (1 oz), L2 17.5 um (0.5 oz), L3 35 um (1 oz).
6.  PLATING: plated holes per IPC-6012 for the class above.
7.  FINISH: ENIG.
8.  SOLDER MASK: LPI per IPC-SM-840 over bare copper; top and bottom, green.
9.  SILKSCREEN: top, [TBD: color]; no scaling or size changes without approval.
10. IMPEDANCE CONTROL: yes, per b-1-stackup.txt; the fab may adjust trace widths to meet the \
impedances.
11. MINIMUM FEATURES: track 0.15 mm, space 0.2 mm, finished drill 0.3 mm.
12. ELECTRICAL TEST: 100% netlist test per IPC-9252.
13. DRILLING: drill file sizes are finished hole sizes; plated and non-plated holes are in \
separate files.
14. GERBER FILES: do not modify the supplied files without the owner's approval.
15. TOOLING HOLES: none in the board area but those in the drill files.
16. MARKING: no added marking, logo or order number in the board area without approval.
""",
}


def test_release_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A release's files, byte for byte, what it prints, its tag and its
    kicad-cli calls."""
    root, calls = release(tmp_path, monkeypatch)
    commit = git(root, "rev-parse", "--short", "HEAD")
    dest = root / "production/20261118-1"
    files = sorted(f.name for f in dest.iterdir())
    out, err = capsys.readouterr()
    assert err == PROFILE_WARNINGS
    assert out == (
        "tagged b-20261118-1; push it with: git push origin refs/tags/b-20261118-1\n"
        f"production/20261118-1 from {commit}:\n" + "".join(f"  {f}\n" for f in files)
    )
    assert git(root, "tag", "-n") == "b-20261118-1    b 1: fab release production/20261118-1"
    assert git(root, "status", "--porcelain") == "?? production/"
    assert calls == RELEASE_CALLS
    assert files == [
        "b-1-bom.csv",
        "b-1-fab-notes.txt",
        "b-1-gerber-map.txt",
        "b-1-gerbers-jlcpcb.zip",
        "b-1-ipc2581.zip",
        "b-1-jlcpcb-bom.csv",
        "b-1-jlcpcb-cpl.csv",
        "b-1-odb.tgz",
        "b-1-source.tgz",
        "b-1-stackup.txt",
    ]
    for name, text in RELEASE_TEXT.items():
        assert (dest / name).read_bytes() == text.encode(), name
    assert (dest / "b-1-odb.tgz").read_text() == "ODB++ job\n"
    assert (dest / "b-1-ipc2581.zip").read_text() == "IPC-2581 zip\n"
    with zipfile.ZipFile(dest / "b-1-gerbers-jlcpcb.zip") as z:
        assert {i.filename: z.read(i).decode() for i in z.infolist()} == {
            **dict(sorted({**FAKE_GERBERS, **FAKE_DRILL}.items())),
            "README.txt": RELEASE_TEXT["b-1-gerber-map.txt"],
            "stackup.txt": RELEASE_TEXT["b-1-stackup.txt"],
            "fab-notes.txt": RELEASE_TEXT["b-1-fab-notes.txt"]
            .replace("per b-1-gerber-map.txt", "per README.txt")
            .replace("per b-1-stackup.txt", "per stackup.txt"),
        }
    with tarfile.open(dest / "b-1-source.tgz") as t:
        tgz = {m.name: t.extractfile(m) for m in t.getmembers() if m.isfile()}
        assert [m.name for m in t.getmembers()] == [
            "b-1-source/hw/b.kicad_pcb",
            "b-1-source/hw/b.kicad_sch",
            "b-1-source/hw/b.kicad_pro",
            "b-1-source/hw/b.kicad_dru",
            "b-1-source/hw/fp-lib-table",
            "b-1-source/docs",
            "b-1-source/docs/notes.txt",
            "b-1-source/pdf/b-board.pdf",
            "b-1-source/pdf/b-schematic.pdf",
            "b-1-source/COMMIT",
        ]
        contents = {k: f.read().decode() for k, f in tgz.items() if f is not None}
    assert re.fullmatch(
        rf"commit {commit}\nbuilt \d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\d\n",
        contents.pop("b-1-source/COMMIT"),
    )
    assert contents == {
        **{f"b-1-source/{k}": v for k, v in FIXTURE.items() if k.startswith(("hw/", "docs/"))},
        "b-1-source/pdf/b-board.pdf": "board PDF\n",
        "b-1-source/pdf/b-schematic.pdf": "schematic PDF\n",
    }


def test_release_stops_on_bad_parts_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parts.toml that does not load stops the release before ERC, and
    before a --force replaces the release."""

    def broken(cls: type[Corrections]) -> Corrections:
        try:
            return Corrections.load("[rotation]\n[rotation]\n")
        except ValueError as e:
            raise ValueError(f"parts.toml: {e}") from e

    old = tmp_path / "repo/production/20261118-1/old.txt"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    with pytest.raises(SystemExit, match=r"^profile's data: parts.toml: Cannot declare"):
        release(tmp_path, monkeypatch, broken, ("--force",))
    assert not (tmp_path / "kicad-cli.log").exists()
    assert old.read_text() == "kept\n"


def test_release_refused_by_its_profile_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """require_lcsc with a fitted part with no LCSC number stops the release
    after the checks and the BOM and placement exports, before a --force
    replaces the release, and names the parts."""
    old = tmp_path / "repo/production/20261118-1/old.txt"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    with pytest.raises(SystemExit, match=r"^no LCSC part number for TP1,TP2 \(require_lcsc\)$"):
        release(
            tmp_path, monkeypatch, args=("--force",), config="[board.jlcpcb]\nrequire_lcsc = true\n"
        )
    assert kicad_calls(tmp_path) == RELEASE_CALLS[:6]
    assert old.read_text() == "kept\n"
    assert git(tmp_path / "repo", "tag") == ""


def test_release_refuses_a_part_not_placed_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fitted part in the BOM and not in the placement export stops the
    release before a --force replaces the release, naming it."""
    old = tmp_path / "repo/production/20261118-1/old.txt"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    pos = "".join(line + "\n" for line in FAKE_POS.splitlines() if '"U2"' not in line)
    every = "".join(line + "\n" for line in FAKE_POS_ALL.splitlines() if '"U2"' not in line)
    monkeypatch.setitem(FAKE_OUTPUTS, "pcb export pos", {"": pos})
    monkeypatch.setitem(FAKE_OUTPUTS, "pcb export pos all", {"": every})
    with pytest.raises(SystemExit, match=r"^in the BOM but not placed: U2$"):
        release(tmp_path, monkeypatch, args=("--force",))
    assert kicad_calls(tmp_path) == RELEASE_CALLS[:6]
    assert sorted(p.name for p in old.parent.iterdir()) == ["old.txt"]
    assert git(tmp_path / "repo", "tag") == ""


def test_release_stops_on_bad_options_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(
        SystemExit, match=r"fab.toml: \[\[board\]\] 'b': \[board.jlcpcb\]: unknown key 'x'$"
    ):
        release(tmp_path, monkeypatch, config="[board.jlcpcb]\nx = 1\n")
    assert kicad_calls(tmp_path) == []


@pytest.mark.parametrize(
    ("stackup", "error"),
    [
        ("", "b.kicad_pcb has no stackup: set one in Board Setup, Physical Stackup"),
        (
            '(setup (stackup (layer "dielectric 1" (type "core") (thickness 1.6))))',
            "b.kicad_pcb's stackup has no copper layer",
        ),
    ],
)
def test_release_refuses_a_board_without_copper_in_its_stackup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stackup: str, error: str
) -> None:
    """Before anything is checked or written."""
    pcb = f'(kicad_pcb (general (thickness 1.6)) (title_block (rev "1")) {stackup})\n'
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    with pytest.raises(SystemExit, match=f"^{re.escape(error)}$"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []
    assert not (tmp_path / "repo/production").exists()


def test_config_variants() -> None:
    """Optional, default none; each a build."""
    assert load_config(BOARD)["b"].variants == ()
    text = f'{BOARD}variants = ["lite", "Full.2", "_x", "a-b"]\n'
    assert load_config(text)["b"].variants == ("lite", "Full.2", "_x", "a-b")


@pytest.mark.parametrize(
    ("variants", "error"),
    [
        ('["-x"]', "variant '-x' is not letters, digits, '.', '_' and '-', not starting with '-'"),
        ('["a b"]', "variant 'a b' is not letters, digits"),
        ('[""]', "variant '' is not letters, digits"),
        ('["a/b"]', "variant 'a/b' is not letters, digits"),
        ('["lite", "LITE"]', "variant 'LITE' is there twice, ignoring case"),
        ('"lite"', "'variants' is not a list of strings"),
    ],
)
def test_config_variants_refused(variants: str, error: str) -> None:
    with pytest.raises(ValueError, match=rf"^\[\[board\]\] 'b': {re.escape(error)}"):
        load_config(f"{BOARD}variants = {variants}\n")


# Where KiCad records a project's variants: a .kicad_pro's list (its GUI),
# a board's top-level list (pcbnew's AddVariant) and a footprint's
# override, and a schematic's symbol instance's overrides.
VARIANTS_PRO = {
    "schematic": {"variants": [{"name": "lite", "description": "Lite"}, {"name": "full"}]},
    "board": {"variants": "not a list"},
}
VARIANTS_PCB = (
    '(kicad_pcb (variants (variant (name "lite")) (variant (name "full")))'
    ' (footprint "R" (variant (name "lite") (dnp yes)) (variant (name "jig") (dnp yes))))'
)
VARIANTS_SCH = (
    '(kicad_sch (symbol (instances (project "b" (path "/x" (reference "R1")'
    ' (variant (name "lite") (dnp yes)) (variant (name "proto") (in_bom no)))))))'
)


def test_settings_variants() -> None:
    assert settings_variants(VARIANTS_PRO) == ["lite", "full"]
    assert settings_variants({"schematic": {"variants": []}}) == []
    assert settings_variants({"variants": [{"name": 1}, "x", {"name": "a"}]}) == ["a"]


def test_variant_names() -> None:
    assert variant_names(parse(VARIANTS_PCB)) == ["lite", "full", "jig"]
    assert variant_names(parse("(kicad_pcb)")) == []
    assert variant_names(parse(VARIANTS_SCH)) == ["lite", "proto"]


@pytest.mark.parametrize(
    ("settings", "pcb", "sch", "found"),
    [
        (VARIANTS_PRO, "(kicad_pcb)", "(kicad_sch)", ["lite", "full"]),
        ({}, VARIANTS_PCB, "(kicad_sch)", ["lite", "full", "jig"]),
        ({}, "(kicad_pcb)", VARIANTS_SCH, ["lite", "proto"]),
        (VARIANTS_PRO, VARIANTS_PCB, VARIANTS_SCH, ["lite", "full", "jig", "proto"]),
        ({}, "(kicad_pcb)", "(kicad_sch)", []),
    ],
)
def test_project_variants(settings: object, pcb: str, sch: str, found: list[str]) -> None:
    """Each place KiCad records a variant, alone and together, each once."""
    assert project_variants(settings, parse(pcb), [parse(sch)]) == found


@pytest.mark.parametrize(
    ("listed", "errors", "warnings"),
    [
        (["lite", "full"], [], ["variant proto of the project is not built"]),
        (["lite"], [], ["variants full, proto of the project are not built"]),
        (
            [],
            [],
            [
                "the project has variants lite, full, proto, and fab.toml lists none:"
                " the default variant is built"
            ],
        ),
        (
            ["lite", "full", "proto", "pro"],
            [
                "variant 'pro' is not one of the project's (its .kicad_pro, board or schematic)",
            ],
            [],
        ),
        (
            ["Lite", "full"],
            [
                "variant 'Lite' is not one of the project's (its .kicad_pro, board or schematic)"
                " (it has 'lite')"
            ],
            ["variant proto of the project is not built"],
        ),
    ],
)
def test_check_variants(listed: list[str], errors: list[str], warnings: list[str]) -> None:
    """A listed variant the project lacks is an error; a project variant
    not listed is a warning, singular or plural."""
    assert check_variants(listed, ["lite", "full", "proto"]) == (errors, warnings)


def test_check_variants_one() -> None:
    assert check_variants([], ["lite"]) == (
        [],
        ["the project has variant lite, and fab.toml lists none: the default variant is built"],
    )
    assert check_variants([], []) == ([], [])


def placement(ref: str, value: str = "10k", package: str = "R_0402") -> Placement:
    return Placement(ref, value, package, 0.0, 0.0, 0.0, "top")


@pytest.mark.parametrize(
    ("part", "fitted", "problems"),
    [
        (Part("R1", "10k", "lib:R_0402", ""), True, []),
        (Part("R1", "10k", "lib:R_0402", "", dnp=True), False, []),
        (
            Part("R1", "10k", "lib:R_0402", "", dnp=True),
            True,
            ["R1: DNP in the schematic, fitted on the board"],
        ),
        (
            Part("R1", "10k", "lib:R_0402", ""),
            False,
            ["R1: fitted in the schematic, DNP on the board"],
        ),
        (
            Part("R1", "4k7", "lib:R_0402", ""),
            True,
            ["R1: Value '4k7' in the schematic, '10k' on the board"],
        ),
        (
            Part("R1", "10k", "lib:R_0603", ""),
            True,
            [
                "R1: Footprint 'lib:R_0603' in the schematic, 'R_0402' on the board:"
                " a variant cannot change a footprint"
            ],
        ),
        (Part("H1", "MountingHole", "lib:H", ""), False, []),
    ],
)
def test_cross_checks(part: Part, fitted: bool, problems: list[str]) -> None:
    """A variant's schematic and board compared: a part not on the board
    (H1) is not compared."""
    placed = [placement("R1")]
    assert cross_checks([part], placed if fitted else [], placed) == problems


def gerber_directory(path: Path, files: dict[str, str]) -> Path:
    path.mkdir()
    for name, text in files.items():
        (path / name).write_text(text)
    return path


# A variant's Gerber directory: each layer's file, by name.
VARIANT_GERBERS = {
    "b-F_Cu.gtl": "G04 date 1*\n%TF.CreationDate,1*%\nX0Y0D02*\n",
    "b-F_Silkscreen.gto": "G04 date 1*\nX0Y0D03*\n",
    "b-PTH.drl": "M48\n; date 1\nT1C0.300\n",
    "b-job.gbrjob": json.dumps({"Header": {"CreationDate": "1"}, "FilesAttributes": []}),
}
VARIANT_FILES = [
    GerberFile("b-F_Cu.gtl", ("Copper", "L1", "Top"), "Positive", "F.Cu"),
    GerberFile("b-F_Silkscreen.gto", ("Legend", "Top"), "Positive", "F.Silkscreen"),
    GerberFile("b-PTH.drl", ("Plated", "1", "2", "PTH"), "", ""),
    GerberFile("b-job.gbrjob", (), "", ""),
]


@pytest.mark.parametrize(
    ("changes", "differ", "allowed"),
    [
        (
            {
                "b-F_Cu.gtl": "G04 date 2*\n%TF.CreationDate,2*%\nX0Y0D02*\n",
                "b-F_Silkscreen.gto": "G04 date 2*\nX0Y0D03*\n",
                "b-PTH.drl": "M48\n; date 2\nT1C0.300\n",
                "b-job.gbrjob": json.dumps(
                    {"Header": {"CreationDate": "2"}, "FilesAttributes": []}
                ),
            },
            [],
            True,
        ),
        ({"b-F_Silkscreen.gto": "X1Y1D03*\n"}, ["b-F_Silkscreen.gto"], True),
        ({"b-F_Cu.gtl": "X1Y1D02*\n"}, ["b-F_Cu.gtl"], False),
        ({"b-PTH.drl": "M48\nT1C0.400\n"}, ["b-PTH.drl"], False),
        (
            {"b-job.gbrjob": json.dumps({"Header": {}, "FilesAttributes": [1]})},
            ["b-job.gbrjob"],
            False,
        ),
        ({"b-B_Cu.gbl": "X0Y0D02*\n"}, ["b-B_Cu.gbl"], False),
    ],
)
def test_gerber_differences(
    tmp_path: Path, changes: dict[str, str], differ: list[str], allowed: bool
) -> None:
    """Less comments and dates; only the silkscreens (and User.Drawings)
    may differ; a file only one variant has differs."""
    first = gerber_directory(tmp_path / "a", VARIANT_GERBERS)
    other = gerber_directory(tmp_path / "b", {**VARIANT_GERBERS, **changes})
    found = gerber_differences(first, other, VARIANT_FILES)
    assert [f.path for f in found] == differ
    assert silkscreen_only(found) is allowed


def test_user_drawings_may_differ() -> None:
    assert silkscreen_only([GerberFile("b-User_Drawings.gbr", ("Other", "User"), "", "")])
    assert not silkscreen_only([GerberFile("b-F_Mask.gts", ("SolderMask", "Top"), "", "")])


# The fixture's two variants: lite has R2 DNP; full is the default.
VARIANTS = '["lite", "full"]'
LITE_BOM = FAKE_BOM.replace('"C25744","","","",""\n"R3"', '"C25744","","","","DNP"\n"R3"')
LITE_POS = "".join(line + "\n" for line in FAKE_POS.splitlines() if '"R2"' not in line)


def variant_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outputs: dict[str, dict[str, str]],
    variants: str = VARIANTS,
    args: tuple[str, ...] = (),
    where: str = "project",
) -> tuple[Path, list[list[str]]]:
    """The fixture released with the project's variants lite and full,
    the fab.toml's variants, and the fake's outputs changed.  The variants
    are in the .kicad_pro, as KiCad's GUI keeps them, or with where
    "board" in the board's top-level list, as pcbnew's AddVariant writes
    it."""
    if where == "board":
        pcb = FIXTURE["hw/b.kicad_pcb"].replace(
            "(kicad_pcb ",
            '(kicad_pcb (variants (variant (name "lite")) (variant (name "full")))\n',
        )
        monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    else:
        project = json.loads(FIXTURE["hw/b.kicad_pro"])
        project["schematic"] = {"variants": [{"name": "lite"}, {"name": "full"}]}
        monkeypatch.setitem(FIXTURE, "hw/b.kicad_pro", json.dumps(project))
    toml = FIXTURE["fab.toml"].replace(
        'profile = "jlcpcb"\n', f'profile = "jlcpcb"\nvariants = {variants}\n'
    )
    monkeypatch.setitem(FIXTURE, "fab.toml", toml)
    for key, value in outputs.items():
        monkeypatch.setitem(FAKE_OUTPUTS, key, value)
    return release(tmp_path, monkeypatch, args=args)


LITE_OUTPUTS = {
    "sch export bom @lite": {"": LITE_BOM},
    "pcb export pos @lite": {"": LITE_POS},
}


def variant(call: list[str], name: str) -> list[str]:
    """A kicad-cli call with --variant name before its -o, and the work
    files named for the variant."""
    at = call.index("-o")
    out = call[at + 1]
    if out.startswith("WORK/") and out.endswith(".csv"):
        out = out.removesuffix(".csv") + f"-{name}.csv"
    elif out.startswith("WORK/gerbers"):
        out = out.replace("WORK/gerbers", f"WORK/gerbers-{name}", 1)
    return [*call[:at], "--variant", name, "-o", out, *call[at + 2 :]]


def in_variant_directory(call: list[str], name: str) -> list[str]:
    """A call writing into the variant's Gerber directory, without --variant."""
    return [x.replace("WORK/gerbers/", f"WORK/gerbers-{name}/") for x in call]


def test_release_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Variants whose Gerbers are the same: one Gerber zip and the shared
    documents as without variants, and per variant its BOMs, CPL, ODB++,
    IPC-2581 and PDFs, each from kicad-cli with --variant."""

    # Each variant's Gerbers: the same drawing, other comments and dates.
    def dated(variant: str, second: int) -> dict[str, str]:
        job = json.loads(FAKE_GERBERS["b-job.gbrjob"])
        job["Header"] = {"CreationDate": f"2026-11-18T12:00:0{second}"}
        return {
            **{k: f"G04 {variant} {k}*\n{v}" for k, v in FAKE_GERBERS.items()},
            "b-job.gbrjob": json.dumps(job),
        }

    outputs = {
        **LITE_OUTPUTS,
        "pcb export gerbers @lite": dated("lite", 1),
        "pcb export gerbers @full": dated("full", 2),
    }
    root, calls = variant_release(tmp_path, monkeypatch, outputs)
    dest = root / "production/20261118-1"
    files = sorted(f.name for f in dest.iterdir())
    assert files == sorted(
        [
            "b-1-fab-notes.txt",
            "b-1-gerber-map.txt",
            "b-1-gerbers-jlcpcb.zip",
            "b-1-source.tgz",
            "b-1-stackup.txt",
            *(
                f"b-1-{v}-{f}"
                for v in ("lite", "full")
                for f in ("bom.csv", "ipc2581.zip", "jlcpcb-bom.csv", "jlcpcb-cpl.csv", "odb.tgz")
            ),
        ]
    )
    err = profile_quiet(capsys.readouterr().err)
    assert "warning" not in err
    assert "differ" not in err
    erc, drc, bom, fitted, pos, every, gerbers, drill, odb, ipc, sch_pdf, pcb_pdf = RELEASE_CALLS
    v = ("lite", "full")
    assert calls == [
        erc,
        drc,
        *(
            c
            for n in v
            for c in (variant(bom, n), variant(fitted, n), variant(pos, n), variant(every, n))
        ),
        *(c for n in v for c in (variant(gerbers, n), in_variant_directory(drill, n))),
        *(variant([x.replace("b-1-", f"b-1-{n}-") for x in c], n) for n in v for c in (odb, ipc)),
        *(
            variant([x.replace("WORK/pdf/b-", f"WORK/pdf/b-{n}-") for x in c], n)
            for n in v
            for c in (sch_pdf, pcb_pdf)
        ),
    ]
    for name in ("b-1-gerber-map.txt", "b-1-stackup.txt", "b-1-fab-notes.txt"):
        assert (dest / name).read_bytes() == RELEASE_TEXT[name].encode()
    for name in ("bom.csv", "jlcpcb-bom.csv", "jlcpcb-cpl.csv"):
        assert (dest / f"b-1-full-{name}").read_bytes() == RELEASE_TEXT[f"b-1-{name}"].encode()
    lite = (dest / "b-1-lite-bom.csv").read_text()
    assert '"R1,R2",10k' not in lite
    assert "R1,10k,Resistor_SMD:R_0402_1005Metric,1,," in lite
    assert "R2,10k,Resistor_SMD:R_0402_1005Metric,0,yes," in lite
    assert "\nR2," not in (dest / "b-1-lite-jlcpcb-cpl.csv").read_text()
    with tarfile.open(dest / "b-1-source.tgz") as t:
        assert sorted(m.name for m in t.getmembers() if "/pdf/" in m.name) == [
            "b-1-source/pdf/b-full-board.pdf",
            "b-1-source/pdf/b-full-schematic.pdf",
            "b-1-source/pdf/b-lite-board.pdf",
            "b-1-source/pdf/b-lite-schematic.pdf",
        ]
    assert git(root, "tag", "-n") == (
        "b-20261118-1    b 1: fab release production/20261118-1; variants lite, full"
    )


def test_release_variants_silkscreen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Variants whose silkscreens differ: a Gerber zip per variant, each with
    its own Gerbers, and no shared one; the map names each; stderr names
    the layer."""
    silk = {**FAKE_GERBERS, "b-F_Silkscreen.gto": "G04 F.Silkscreen*\nX1Y1D03*\n"}
    root, _ = variant_release(
        tmp_path, monkeypatch, {**LITE_OUTPUTS, "pcb export gerbers @full": silk}
    )
    dest = root / "production/20261118-1"
    zips = sorted(f.name for f in dest.glob("*.zip") if "gerbers" in f.name)
    assert zips == ["b-1-full-gerbers-jlcpcb.zip", "b-1-lite-gerbers-jlcpcb.zip"]
    title = "b 1: the files in each of b-1-lite-gerbers-jlcpcb.zip, b-1-full-gerbers-jlcpcb.zip"
    text = (dest / "b-1-gerber-map.txt").read_text()
    assert text == RELEASE_TEXT["b-1-gerber-map.txt"].replace(
        "b 1: the files in b-1-gerbers-jlcpcb.zip", title
    )
    for name, gerber in (("lite", FAKE_GERBERS), ("full", silk)):
        with zipfile.ZipFile(dest / f"b-1-{name}-gerbers-jlcpcb.zip") as z:
            assert z.read("b-F_Silkscreen.gto").decode() == gerber["b-F_Silkscreen.gto"]
            assert z.read("README.txt").decode() == text
            assert {"stackup.txt", "fab-notes.txt"} <= set(z.namelist())
    assert profile_quiet(capsys.readouterr().err) == (
        "variants lite, full: the Gerbers differ in F.Silkscreen: a Gerber zip per variant,"
        " each a bare-board order of its own\n"
    )


@pytest.mark.parametrize(
    ("name", "text", "what"),
    [
        ("b-F_Cu.gtl", "X1Y1D02*\n", "F.Cu"),
        ("b-F_Mask.gts", "X1Y1D03*\n", "F.Mask"),
        ("b-Edge_Cuts.gm1", "X1Y1D01*\n", "Edge.Cuts"),
    ],
)
def test_release_variants_refused_gerbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str, what: str
) -> None:
    """Variants whose copper, mask or outline differ: refused, naming the
    variant and the layer, before a --force replaces the release."""
    old = tmp_path / "repo/production/20261118-1/old.txt"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    outputs = {**LITE_OUTPUTS, "pcb export gerbers @full": {**FAKE_GERBERS, name: text}}
    with pytest.raises(
        SystemExit,
        match=rf"^variant full: its Gerbers differ from variant lite's in {re.escape(what)}:"
        " a variant cannot change copper, mask, paste, Edge.Cuts or the drill$",
    ):
        variant_release(tmp_path, monkeypatch, outputs, args=("--force",))
    assert sorted(p.name for p in old.parent.iterdir()) == ["old.txt"]
    assert git(tmp_path / "repo", "tag") == ""


@pytest.mark.parametrize(
    ("outputs", "error"),
    [
        (
            {"sch export bom @lite": {"": LITE_BOM}},
            "variant lite: R2: DNP in the schematic, fitted on the board",
        ),
        (
            {"pcb export pos @lite": {"": LITE_POS}},
            "variant lite: R2: fitted in the schematic, DNP on the board\n"
            "variant lite: in the BOM but not placed: R2",
        ),
        (
            {
                **LITE_OUTPUTS,
                "pcb export pos all @full": {
                    "": FAKE_POS_ALL.replace('"U2","NEW1"', '"U2","NEW2"')
                },
            },
            "variant full: U2: Value 'NEW1' in the schematic, 'NEW2' on the board",
        ),
        (
            {
                **LITE_OUTPUTS,
                "sch export bom @full": {
                    "": FAKE_BOM.replace("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Package_SO:SO-8")
                },
            },
            "variant full: U2: Footprint 'Package_SO:SO-8' in the schematic,"
            " 'SOIC-8_3.9x4.9mm_P1.27mm' on the board: a variant cannot change a footprint",
        ),
    ],
)
def test_release_variants_cross_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outputs: dict[str, dict[str, str]],
    error: str,
) -> None:
    """A variant's schematic and board that differ: refused, naming the
    variant and the part, after the exports, before anything is written."""
    with pytest.raises(SystemExit, match=f"^{re.escape(error)}$"):
        variant_release(tmp_path, monkeypatch, outputs)
    assert all(c[2] in ("bom", "pos") for c in kicad_calls(tmp_path)[2:])
    assert not (tmp_path / "repo/production").exists()


def test_release_variants_profile_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The profile's check of every variant, each refusal naming it: U2 in
    lite's BOM and not on its board."""
    lite = "".join(line + "\n" for line in LITE_POS.splitlines() if '"U2"' not in line)
    every = "".join(line + "\n" for line in FAKE_POS_ALL.splitlines() if '"U2"' not in line)
    outputs = {
        **LITE_OUTPUTS,
        "pcb export pos @lite": {"": lite},
        "pcb export pos all @lite": {"": every},
    }
    with pytest.raises(SystemExit, match=r"^variant lite: in the BOM but not placed: U2$"):
        variant_release(tmp_path, monkeypatch, outputs)
    assert not (tmp_path / "repo/production").exists()


@pytest.mark.parametrize("where", ["project", "board"])
def test_release_variant_the_project_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Before anything runs."""
    with pytest.raises(
        SystemExit,
        match=r"^b: variant 'proto' is not one of the project's \(its .kicad_pro, board or"
        r" schematic\)$",
    ):
        variant_release(tmp_path, monkeypatch, LITE_OUTPUTS, '["lite", "proto"]', where=where)
    assert kicad_calls(tmp_path) == []


def test_release_variants_from_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The variants in the board's top-level list, none in the .kicad_pro:
    the same files as from the .kicad_pro."""
    root, _ = variant_release(tmp_path, monkeypatch, LITE_OUTPUTS, where="board")
    assert (root / "production/20261118-1/b-1-lite-bom.csv").exists()
    assert "warning" not in profile_quiet(capsys.readouterr().err)


def test_release_variants_unlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A project with variants and none listed: the default built, as
    without variants, and a warning."""
    root, calls = variant_release(tmp_path, monkeypatch, LITE_OUTPUTS, "[]")
    assert calls == RELEASE_CALLS
    bom = root / "production/20261118-1/b-1-bom.csv"
    assert bom.read_bytes() == RELEASE_TEXT["b-1-bom.csv"].encode()
    assert profile_quiet(capsys.readouterr().err) == (
        "warning: the project has variants lite, full, and fab.toml lists none:"
        " the default variant is built\n"
    )


@pytest.mark.parametrize(
    ("change", "errors"),
    [
        (
            lambda pcb: pcb[: pcb.index("\n  (table ")] + ")\n",
            ["no stackup table (Place > Add Stackup Table) on User.Drawings"],
        ),
        (
            lambda pcb: pcb.replace('(table_cell "0.2 mm")', '(table_cell "0.25 mm")'),
            ["stackup table: dielectric 1: thickness '0.25 mm', not 0.2; out of date: replace it"],
        ),
        (
            lambda pcb: pcb.replace('(layer "Dwgs.User")', '(layer "Cmts.User")'),
            ["stackup table on Cmts.User, not User.Drawings"],
        ),
        (
            lambda pcb: pcb.replace(
                '(layer "In1.Cu" (type "copper") (thickness 0.0175))',
                '(layer "In1.Cu" (type "copper") (thickness 0.0175))\n'
                '      (layer "In2.Cu" (type "copper") (thickness 0.0175))',
            ),
            ["stackup table: 7 rows, the stackup 8 layers; out of date: replace it"],
        ),
    ],
)
def test_release_refuses_a_board_without_a_matching_stackup_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[str], str],
    errors: list[str],
) -> None:
    """kicad-style-check's stackup table rules, naming stackup-table as the
    fix, before anything is checked or written."""
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", change(FIXTURE["hw/b.kicad_pcb"]))
    with pytest.raises(SystemExit) as e:
        release(tmp_path, monkeypatch)
    message = str(e.value)
    for error in errors:
        assert f"b.kicad_pcb: {error}" in message
    assert message.endswith("\nwrite or refresh it with: stackup-table hw/b.kicad_pcb")
    assert kicad_calls(tmp_path) == []
    assert not (tmp_path / "repo/production").exists()


def test_drc_project(tmp_path: Path) -> None:
    """DRC's scratch copy: the project's files copied, its directories
    linked, the rules installed in the copy; the board's files unchanged."""
    root = tmp_path / "repo"
    board = root / "hw"
    (board / "lib").mkdir(parents=True)
    for name in ("b.kicad_pcb", "b.kicad_sch", "fp-lib-table"):
        (board / name).write_text(name)
    (board / "b.kicad_pro").write_text("{}")
    (board / "b.kicad_dru").write_text(f"(version 1)\n{OWN_RULE}")
    work = tmp_path / "work"
    work.mkdir()
    r = Release(root, load_config(FIXTURE["fab.toml"])["b"], work)
    rules = install(f"(version 1)\n{OWN_RULE}", {}, SECTION, board_setup(Jlcpcb.rules))
    (root / "lib").mkdir()
    pcb = r.drc_project(rules)
    scratch = work / "project/hw"
    assert pcb == scratch / "b.kicad_pcb"
    assert (scratch / "../lib").resolve() == (root / "lib").resolve()
    assert sorted(p.name for p in (work / "project").iterdir()) == ["hw", "lib"]
    assert not (scratch.parent / "hw").is_symlink()
    assert sorted(p.name for p in scratch.iterdir()) == sorted(p.name for p in board.iterdir())
    assert (scratch / "lib").is_symlink()
    assert (scratch / "lib").resolve() == (board / "lib").resolve()
    assert not (scratch / "b.kicad_pcb").is_symlink()
    assert (scratch / "b.kicad_dru").read_text() == f"(version 1)\n{SECTION}\n{OWN_RULE}"
    setup = json.loads((scratch / "b.kicad_pro").read_text())["board"]["design_settings"]
    assert setup["rules"]["min_clearance"] == 0.1
    assert (board / "b.kicad_dru").read_text() == f"(version 1)\n{OWN_RULE}"
    assert (board / "b.kicad_pro").read_text() == "{}"


def test_release_warns_of_rules_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A board's .kicad_dru without the current rules, a Board Setup below
    the profile's, and a copy of the profile's rules: each a warning, the
    release made with the current rules."""
    copy = '(rule "JLCPCB track width"\n\t(constraint track_width (min 0.1mm)))\n'
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_dru", f"(version 1)\n{copy}")
    project = json.loads(FIXTURE["hw/b.kicad_pro"])
    project["board"]["design_settings"]["rules"]["min_clearance"] = 0.05
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pro", json.dumps(project))
    root, calls = release(tmp_path, monkeypatch)
    assert calls == RELEASE_CALLS
    adopt = "run ee_tooling's adopt --yes to make KiCad's DRC the release's"
    assert profile_quiet(capsys.readouterr().err) == (
        f"warning: b.kicad_dru: ee_tooling's jlcpcb rules missing or out of date: {adopt}\n"
        "warning: b.kicad_pro: Board Setup below jlcpcb's minimums"
        f" (board.design_settings.rules.min_clearance: 0.05 -> 0.1): {adopt}\n"
        "warning: hw/b.kicad_dru: 1 rule below ee_tooling's end marker copies the profile's and"
        " wins over them:\n  JLCPCB track width\nremove this rule from below the end marker in"
        " hw/b.kicad_dru\n"
    )
    assert (root / "hw/b.kicad_dru").read_text() == f"(version 1)\n{copy}"


def test_release_adopted_has_no_rules_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release(tmp_path, monkeypatch)
    assert profile_quiet(capsys.readouterr().err) == ""


def test_release_refuses_bad_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_dru", f"(version 1)\n{SECTION}{SECTION}")
    with pytest.raises(SystemExit, match=r"^b: its rules: ee_tooling's markers are not one"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []


@pytest.mark.parametrize(
    ("key", "error"),
    [
        ("sch erc", "ERC is not clean:\nERC report\n"),
        ("pcb drc", "DRC is not clean, with jlcpcb's rules:\nDRC report\n"),
    ],
)
def test_release_stops_on_violations_with_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, error: str
) -> None:
    """The report in the message: the work directory it is in goes."""
    monkeypatch.setenv("FAKE_FAIL", key)
    with pytest.raises(SystemExit, match=f"^{re.escape(error)}$"):
        release(tmp_path, monkeypatch)
    assert not (tmp_path / "repo/production").exists()


@pytest.mark.parametrize(
    ("directory", "production"),
    [(".", "production"), ("hw", "hw/production"), ("hw", "production")],
)
def test_release_rebuilt_over_its_own_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str, production: str
) -> None:
    """A rebuild with --force over an uncommitted release: every board's
    production directory is not a source, in the board's directory or
    not; anything else uncommitted there still stops it."""
    files = {
        (k.replace("hw/", "", 1) if directory == "." else k): v
        for k, v in FIXTURE.items()
        if k != "fab.toml"
    }
    other = '[[board]]\nname = "other"\ndirectory = "o"\nproduction = "out"\nprofile = "jlcpcb"\n'
    toml = (
        FIXTURE["fab.toml"]
        .replace('directory = "hw"', f'directory = "{directory}"')
        .replace('production = "production"', f'production = "{production}"')
        + other
        + FIXTURE["fab.toml"].split("\n\n", 1)[1]
    )
    for k in list(FIXTURE):
        monkeypatch.delitem(FIXTURE, k)
    for k, v in {**files, "fab.toml": toml}.items():
        monkeypatch.setitem(FIXTURE, k, v)
    root, _ = release(tmp_path, monkeypatch)
    (root / "out").mkdir()
    (root / "out/x.txt").write_text("another board's release\n")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force"])
    main()
    assert (root / production / "20261118-1/b-1-bom.csv").exists()
    (root / directory / "stray.txt").write_text("x\n")
    with pytest.raises(SystemExit, match=r"uncommitted changes in the release's sources:\n.*stray"):
        main()


# A Git LFS pointer as git-lfs 3.7.1 writes it.
POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:27c22de2bca1a85418f3a0e1ea8e9917745bcb75960a9a8fe526a7c1a76f3c95\n"
    "size 10\n"
)


@pytest.mark.parametrize(
    ("text", "pointer"),
    [
        (POINTER, True),
        (POINTER.replace("git-lfs.github.com", "hawser.github.com"), True),
        (POINTER.rstrip("\n"), True),
        ("version https://git-lfs.github.com/spec/v1", True),
        ("", False),
        (POINTER.replace("v1", "v2"), False),
        (" " + POINTER, False),
        ("(kicad_pcb)\n", False),
        (POINTER + "x" * (1024 - len(POINTER) - 1), True),
        (POINTER + "x" * (1024 - len(POINTER)), False),
    ],
)
def test_lfs_pointer(tmp_path: Path, text: str, pointer: bool) -> None:
    """By its first line and its size alone; an empty file is not one."""
    f = tmp_path / "f"
    f.write_text(text)
    assert lfs_pointer(f) is pointer


def test_lfs_pointers(tmp_path: Path) -> None:
    """Files and directories walked, from the root, in order."""
    for name, text in {
        "hw/b.kicad_pcb": POINTER,
        "hw/b.kicad_sch": "(kicad_sch)\n",
        "hw/b.3dshapes/z.step": POINTER,
        "hw/b.3dshapes/a/m.wrl": POINTER,
        "hw/b.3dshapes/empty.step": "",
        "docs/d.pdf": "%PDF-1.4\n",
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    paths = [Path("hw/b.kicad_pcb"), Path("hw/b.kicad_sch"), Path("hw/b.3dshapes"), Path("docs")]
    assert lfs_pointers(tmp_path, paths) == [
        Path("hw/b.kicad_pcb"),
        Path("hw/b.3dshapes/a/m.wrl"),
        Path("hw/b.3dshapes/z.step"),
    ]
    overlapping = [Path("hw/b.3dshapes/z.step"), Path("hw/b.3dshapes"), Path("hw/b.kicad_pcb")]
    assert lfs_pointers(tmp_path, overlapping) == [
        Path("hw/b.3dshapes/z.step"),
        Path("hw/b.3dshapes/a/m.wrl"),
        Path("hw/b.kicad_pcb"),
    ]


@pytest.mark.parametrize(
    "args", [(), ("--force",), ("--allow-dirty",), ("--force", "--allow-dirty")]
)
def test_release_refuses_lfs_pointers_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    """A pointer among the board's files or its sources, as a clone without
    git-lfs leaves it, stops the release before anything is checked or
    written, naming each, whatever the options."""
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pro", POINTER)
    monkeypatch.setitem(FIXTURE, "docs/datasheets/u1.pdf", POINTER)
    old = tmp_path / "repo/production/20261118-1/old.txt"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    with pytest.raises(SystemExit) as e:
        release(tmp_path, monkeypatch, args=args)
    assert str(e.value) == (
        "Git LFS pointers among the release's inputs, their files never fetched"
        " (a clone without git-lfs):\n"
        "  hw/b.kicad_pro\n"
        "install git-lfs, then, from the repository's root:\n"
        "  git lfs install --local --skip-repo && git lfs pull"
    )
    assert kicad_calls(tmp_path) == []
    assert old.read_text() == "kept\n"
    assert git(tmp_path / "repo", "tag") == ""


def test_release_with_variants_refuses_lfs_pointers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With design variants, once, before any build."""
    monkeypatch.setitem(FIXTURE, "docs/u1.pdf", POINTER)
    with pytest.raises(SystemExit, match=r"^Git LFS pointers among .*\n  docs/u1.pdf\ninstall"):
        variant_release(tmp_path, monkeypatch, LITE_OUTPUTS)
    assert kicad_calls(tmp_path) == []


def test_release_refuses_a_tag_on_an_earlier_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production directory committed, then a rebuild: refused before
    anything is replaced, saying why and the choices; --retag moves it."""
    root, _ = release(tmp_path, monkeypatch)
    first = git(root, "rev-parse", "--short", "HEAD")
    git(root, "add", "production")
    git(root, "commit", "-q", "-m", "release")
    head = git(root, "rev-parse", "--short", "HEAD")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force"])
    with pytest.raises(SystemExit) as e:
        main()
    assert str(e.value) == tag_refused("b-20261118-1", first, head)
    assert f"HEAD is {head}: it has moved since" in str(e.value)
    assert "--retag" in str(e.value)
    assert git(root, "rev-parse", "--short", "b-20261118-1^{commit}") == first
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    main()
    assert git(root, "rev-parse", "--short", "b-20261118-1^{commit}") == head


def test_release_refuses_a_library_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before anything runs, naming each path as written."""
    monkeypatch.setitem(
        FIXTURE,
        "hw/fp-lib-table",
        '(fp_lib_table (lib (name "a")(uri "${KIPRJMOD}/../../a.pretty"))'
        ' (lib (name "s")(uri "${KICAD10_FOOTPRINT_DIR}/R.pretty")))\n',
    )
    with pytest.raises(SystemExit) as e:
        release(tmp_path, monkeypatch)
    assert str(e.value) == (
        "b: libraries outside the repository, or under a variable not KiCad's own (its library"
        " tables and 3D model paths, as written):\n  ${KIPRJMOD}/../../a.pretty\nput each"
        " library in the repository (a git submodule) and name it by a ${KIPRJMOD}-relative"
        " path (docs/kicad-style.md, Files)"
    )
    assert kicad_calls(tmp_path) == []


def library_submodule(tmp_path: Path, root: Path) -> Path:
    """A library repository added to root as the submodule libs/shared,
    the fixture board's fp-lib-table naming its x.pretty; committed."""
    lib = tmp_path / "lib"
    (lib / "x.pretty").mkdir(parents=True)
    (lib / "x.pretty/R.kicad_mod").write_text("(footprint R)\n")
    git(lib, "init", "-q")
    git(lib, "add", ".")
    git(lib, "commit", "-q", "-m", "lib")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "libs/shared")
    (root / "hw/fp-lib-table").write_text(
        '(fp_lib_table (lib (name "x")(uri "${KIPRJMOD}/../libs/shared/x.pretty")))\n'
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "library")
    return root / "libs/shared"


@pytest.mark.parametrize(
    "change",
    [
        lambda s: (s / "x.pretty/R.kicad_mod").write_text("(footprint R2)\n"),
        lambda s: (s / "untracked.txt").write_text("x\n"),
        lambda s: git(s, "commit", "-q", "--allow-empty", "-m", "new"),
        lambda s: (
            git(s.parent.parent, "config", "submodule.libs/shared.ignore", "dirty"),
            (s / "x.pretty/R.kicad_mod").write_text("(footprint R2)\n"),
        ),
        lambda s: (
            git(s.parent.parent, "config", "submodule.libs/shared.ignore", "all"),
            git(s, "commit", "-q", "--allow-empty", "-m", "new"),
        ),
    ],
)
def test_release_refuses_a_changed_library_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], object]
) -> None:
    """A library the board's tables name, in a submodule outside the
    board's directory and not in sources: its modified or untracked
    content, or a new commit in it, stops the release, whatever the
    submodule's ignore setting; --allow-dirty goes on."""
    root, _ = release(tmp_path, monkeypatch)
    submodule = library_submodule(tmp_path, root)
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    main()
    change(submodule)
    with pytest.raises(
        SystemExit, match=r"uncommitted changes in the release's sources:\n.*libs/shared"
    ):
        main()
    monkeypatch.setattr(
        sys, "argv", ["fab-release", "--date", "20261118", "--force", "--allow-dirty"]
    )
    main()


def test_release_refuses_an_lfs_pointer_in_a_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 3D model the board names, in a library outside the board's
    directory, never fetched."""
    pcb = FIXTURE["hw/b.kicad_pcb"].replace(
        "(setup", '(footprint "x:R" (model "${KIPRJMOD}/../libs/3d/R.step"))\n  (setup', 1
    )
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n"
    monkeypatch.setitem(FIXTURE, "libs/3d/R.step", pointer)
    with pytest.raises(SystemExit, match=r"Git LFS pointers among .*\n  libs/3d/R.step\n"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []


def test_release_warns_of_a_model_found_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A relative 3D model path that resolves nowhere: a warning naming
    the footprint and the path; the release goes on."""
    pcb = FIXTURE["hw/b.kicad_pcb"].replace(
        "(setup",
        '(footprint "x:R" (property "Reference" "R9") (model "3d/R.step"))\n  (setup',
        1,
    )
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    root, _ = release(tmp_path, monkeypatch)
    assert (root / "production/20261118-1/b-1-bom.csv").exists()
    assert profile_quiet(capsys.readouterr().err) == (
        "warning: 3D model not found, from the project or its library: R9 (x:R): 3d/R.step"
        " (library 'x' not in fp-lib-table)\n"
    )


@pytest.mark.parametrize(
    ("one", "two"),
    [("production", "production"), ("production", "production/jig"), ("./out", "out/")],
)
def test_config_refuses_shared_production(one: str, two: str) -> None:
    """Two boards' releases in one directory, or one in the other's."""
    a = BOARD.replace('production = "production"', f'production = "{one}"')
    b = BOARD.replace('name = "b"', 'name = "c"').replace(
        'production = "production"', f'production = "{two}"'
    )
    with pytest.raises(
        ValueError,
        match=rf"^\[\[board\]\] 'b' and 'c': production '{re.escape(str(Path(one)))}' and"
        rf" '{re.escape(str(Path(two)))}' are the same directory, or one holds the other$",
    ):
        load_config(a + b)


def test_config_separate_productions() -> None:
    a = BOARD.replace('production = "production"', 'production = "production/b"')
    b = BOARD.replace('name = "b"', 'name = "c"').replace(
        'production = "production"', 'production = "production/c"'
    )
    assert list(load_config(a + b)) == ["b", "c"]


def test_release_force_keeps_another_boards_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force does not remove a release directory holding a file not this
    board's."""
    old = tmp_path / "repo/production/20261118-1"
    old.mkdir(parents=True)
    (old / "jig-1-bom.csv").write_text("another board's\n")
    with pytest.raises(SystemExit, match=r"20261118-1 holds jig-1-bom.csv, not this board's"):
        release(tmp_path, monkeypatch, args=("--force",))
    assert (old / "jig-1-bom.csv").read_text() == "another board's\n"


@pytest.mark.parametrize(
    ("production", "error"),
    [
        ("/tmp/out", "production '/tmp/out' is outside the repository"),
        ("../out", "production '../out' is outside the repository"),
        ("hw/../../out", "production 'hw/../../out' is outside the repository"),
        (".", "production '.' holds 'hw', 'b''s directory"),
        ("hw", "production 'hw' holds 'hw', 'b''s directory"),
        ("docs", "production 'docs' holds 'docs/x', 'b''s source"),
    ],
)
def test_config_refuses_a_bad_production(production: str, error: str) -> None:
    """Outside the repository, or holding what the uncommitted-sources check
    must see."""
    text = BOARD.replace('production = "production"', f'production = "{production}"')
    with pytest.raises(ValueError, match=rf"^\[\[board\]\] 'b': {re.escape(error)}$"):
        load_config(f'{text}sources = ["docs/x"]\n')


def test_config_production_inside_the_directory() -> None:
    """The repository-root layout: production inside the board's directory."""
    text = BOARD.replace('directory = "hw"', 'directory = "."')
    assert load_config(text)["b"].production == Path("production")


def test_release_stops_when_git_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A git call that fails stops the release, with git's message, never
    read as clean."""
    real = subprocess.run

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if list(args[:2]) == ["git", "status"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad pathspec\n")
        return real(args, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit, match=r"^git status .* failed \(128\):\nfatal: bad pathspec\n$"):
        release(tmp_path, monkeypatch)
    assert not (tmp_path / "repo/production").exists()


@pytest.mark.parametrize("key", ["pcb export odb", "sch export pdf"])
def test_release_failing_export_keeps_the_old_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """An export failing after the checks, with --force: the old release
    as it was, no partial directory, the tag where it was."""
    root, _ = release(tmp_path, monkeypatch)
    dest = root / "production/20261118-1"
    before = {f.name: f.read_bytes() for f in dest.iterdir()}
    tagged = git(root, "rev-parse", "b-20261118-1^{commit}")
    git(root, "commit", "-q", "--allow-empty", "-m", "later")
    monkeypatch.setenv("FAKE_FAIL", key)
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    with pytest.raises(SystemExit, match=rf"^kicad-cli {key} "):
        main()
    assert {f.name: f.read_bytes() for f in dest.iterdir()} == before
    assert sorted(p.name for p in (root / "production").iterdir()) == ["20261118-1"]
    assert git(root, "rev-parse", "b-20261118-1^{commit}") == tagged


@pytest.mark.parametrize(
    ("rev", "error"),
    [
        ("Rev A", "tag 'b-20261118-Rev A' is not a git tag name"),
        ("1/2", "release directory '20261118-1/2' is not one path component"),
        ("1..2", "tag 'b-20261118-1..2' is not a git tag name"),
        ("1.lock", "tag 'b-20261118-1.lock' is not a git tag name"),
    ],
)
def test_release_refuses_a_bad_rev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rev: str, error: str
) -> None:
    """Before anything runs, naming the value and where it comes from."""
    monkeypatch.setitem(
        FIXTURE, "hw/b.kicad_pcb", FIXTURE["hw/b.kicad_pcb"].replace('(rev "1")', f'(rev "{rev}")')
    )
    with pytest.raises(
        SystemExit,
        match=rf"^{re.escape(error)} \(--date '20261118', the title block's rev"
        rf" {re.escape(repr(rev))}\)",
    ):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []
    assert not (tmp_path / "repo/production").exists()


def test_release_name_problem(tmp_path: Path) -> None:
    assert release_name_problem(tmp_path, "b", "20261118", "1.0") is None
    assert "not one path component" in (release_name_problem(tmp_path, "b", "a/b", "1") or "")


def test_drc_project_links_odd_entries(tmp_path: Path) -> None:
    """A broken link, a link loop, a FIFO and an unreadable file in the
    board's directory: linked, not read; KiCad's cache left out; the
    project's own files copied."""
    root = tmp_path / "repo"
    board = root / "hw"
    board.mkdir(parents=True)
    for name in ("b.kicad_pcb", "b.kicad_sch", "fp-info-cache"):
        (board / name).write_text(name)
    (board / "broken").symlink_to(board / "nothing")
    (board / "loop").symlink_to(board / "loop")
    os.mkfifo(board / "fifo")
    (board / "secret").write_text("x")
    (board / "secret").chmod(0)
    work = tmp_path / "work"
    work.mkdir()
    r = Release(root, load_config(FIXTURE["fab.toml"])["b"], work)
    r.drc_project(install(None, {}, SECTION, board_setup(Jlcpcb.rules)))
    scratch = work / "project/hw"
    assert sorted(p.name for p in scratch.iterdir()) == [
        "b.kicad_dru",
        "b.kicad_pcb",
        "b.kicad_pro",
        "b.kicad_sch",
        "broken",
        "fifo",
        "loop",
        "secret",
    ]
    for name in ("broken", "fifo", "loop", "secret"):
        assert (scratch / name).is_symlink()
    assert not (scratch / "b.kicad_sch").is_symlink()
    (board / "secret").chmod(0o600)


def test_drc_project_stops_on_an_unreadable_board(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "hw").mkdir(parents=True)
    (root / "hw/b.kicad_pcb").symlink_to(root / "hw/nothing")
    work = tmp_path / "work"
    work.mkdir()
    r = Release(root, load_config(FIXTURE["fab.toml"])["b"], work)
    with pytest.raises(SystemExit, match=r"^DRC's scratch copy: .*b.kicad_pcb: No such file"):
        r.drc_project(install(None, {}, SECTION, board_setup(Jlcpcb.rules)))


def test_source_tgz_leaves_out_git_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sources = ["."]: the source tgz holds the repository, but .git and
    every board's production directory (an earlier release, this one's
    own files)."""
    toml = FIXTURE["fab.toml"].replace('sources = ["docs"]', 'sources = ["."]')
    other = '[[board]]\nname = "o"\ndirectory = "o"\nproduction = "out"\nprofile = "jlcpcb"\n'
    monkeypatch.setitem(FIXTURE, "fab.toml", toml + other + FIXTURE["fab.toml"].split("\n\n", 1)[1])
    monkeypatch.setitem(FIXTURE, "out/o-1-bom.csv", "another board's release\n")
    root, _ = release(tmp_path, monkeypatch)
    (root / "production/20261117-1").mkdir()
    (root / "production/20261117-1/b-1-bom.csv").write_text("earlier\n")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force"])
    main()
    with tarfile.open(root / "production/20261118-1/b-1-source.tgz") as t:
        names = [m.name for m in t.getmembers()]
    assert "b-1-source/./docs/notes.txt" in names or "b-1-source/docs/notes.txt" in names
    assert not [n for n in names if "/.git" in n or "production" in n or "/out" in n]


def hierarchy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture's schematic in two levels: hw/sub/a.kicad_sch, and from
    it hw/sub/b.kicad_sch and shared/c.kicad_sch, outside the board's
    directory."""
    monkeypatch.setitem(
        FIXTURE, "hw/b.kicad_sch", '(kicad_sch (sheet (property "Sheetfile" "sub/a.kicad_sch")))\n'
    )
    monkeypatch.setitem(
        FIXTURE,
        "hw/sub/a.kicad_sch",
        '(kicad_sch (sheet (property "Sheetfile" "b.kicad_sch"))'
        ' (sheet (property "Sheetfile" "../../shared/c.kicad_sch")))\n',
    )
    monkeypatch.setitem(FIXTURE, "hw/sub/b.kicad_sch", "(kicad_sch)\n")
    monkeypatch.setitem(FIXTURE, "shared/c.kicad_sch", "(kicad_sch)\n")


def test_source_tgz_holds_every_sheet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hierarchy(monkeypatch)
    root, _ = release(tmp_path, monkeypatch)
    with tarfile.open(root / "production/20261118-1/b-1-source.tgz") as t:
        names = [m.name for m in t.getmembers()]
    for sheet in (
        "hw/b.kicad_sch",
        "hw/sub/a.kicad_sch",
        "hw/sub/b.kicad_sch",
        "shared/c.kicad_sch",
    ):
        assert names.count(f"b-1-source/{sheet}") == 1, sheet


def test_release_refuses_an_uncommitted_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sheet outside the board's directory, changed: uncommitted."""
    hierarchy(monkeypatch)
    root, _ = release(tmp_path, monkeypatch)
    (root / "shared/c.kicad_sch").write_text("(kicad_sch (changed))\n")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force"])
    with pytest.raises(SystemExit, match=r"uncommitted changes .*:\n ?M shared/c.kicad_sch"):
        main()


def test_output_clash() -> None:
    """Variants a and a-jlcpcb: a's JLCPCB BOM is a-jlcpcb's house BOM."""
    profile = Jlcpcb(CORRECTIONS)
    assert output_clash("b", "1", ["a", "a-jlcpcb"], False, profile) == (
        "variants a and a-jlcpcb would both write b-1-a-jlcpcb-bom.csv: rename one"
    )
    assert output_clash("b", "1", ["a", "a-x"], True, profile) is None
    assert output_clash("b", "1", [None], False, profile) is None


def test_release_refuses_variants_whose_files_clash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = json.loads(FIXTURE["hw/b.kicad_pro"])
    project["schematic"] = {"variants": [{"name": "a"}, {"name": "a-jlcpcb"}]}
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pro", json.dumps(project))
    toml = FIXTURE["fab.toml"].replace(
        'profile = "jlcpcb"\n', 'profile = "jlcpcb"\nvariants = ["a", "a-jlcpcb"]\n'
    )
    monkeypatch.setitem(FIXTURE, "fab.toml", toml)
    with pytest.raises(SystemExit, match=r"^variants a and a-jlcpcb would both write"):
        release(tmp_path, monkeypatch)
    assert not (tmp_path / "repo/production").exists()


def test_release_warnings_name_the_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The profile's warnings on stderr, each build's naming its variant."""
    variant_release(tmp_path, monkeypatch, LITE_OUTPUTS)
    out, err = capsys.readouterr()
    assert "warning" not in out
    for v in ("lite", "full"):
        assert f"warning: variant {v}: no LCSC part number for TP1,TP2\n" in err
        assert (
            f"warning: variant {v}: check a new part (C4) in JLCPCB's placement preview:"
            " no correction known\n"
        ) in err


def test_release_git_ignores_a_callers_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIT_DIR naming another repository: the release's git commands act on
    the one it runs in."""
    other, board = tmp_path / "other", tmp_path / "board"
    for d in (other, board):
        d.mkdir()
        git(d, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.chdir(board)
    assert repository_root() == board
    release = Release(board, load_config(CONFIG)["jig"], tmp_path / "work")
    assert release.run("git", "rev-parse", "--absolute-git-dir").stdout.strip() == str(
        board / ".git"
    )


# A board's text with each variant's variable, and the (layers ...) names
# KiCad 10 writes for it.
VARIANT_LAYERS = (
    '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (5 "F.SilkS" user "F.Silkscreen")'
    ' (17 "Dwgs.User" user "User.Drawings") (19 "Cmts.User" user "User.Comments")'
    ' (31 "F.Fab" user))'
)
VARIANT_TEXT = '(gr_text "rev ${VARIANT}" (at 1 1) (layer "F.SilkS") (uuid "t"))'
BOARD_LIST = '(variants (variant (name "lite")) (variant (name "full")))'


def variant_board(*items: str) -> Expression:
    return parse(f"(kicad_pcb {VARIANT_LAYERS} {' '.join(items)})")


SHOWN = ["F.Cu", "B.Cu", "F.Silkscreen", "User.Drawings", "F.Fab"]


@pytest.mark.parametrize(
    ("listed", "items", "warned"),
    [
        (("lite", "full"), (VARIANT_TEXT,), True),
        (("lite", "full"), (VARIANT_TEXT.replace("${VARIANT}", "${VARIANT_DESC}"),), True),
        (("lite", "full"), (VARIANT_TEXT, '(variants (variant (name "lite")))'), True),
        (("lite", "full"), (VARIANT_TEXT, BOARD_LIST), False),
        ((), (VARIANT_TEXT,), False),
        (("lite", "full"), (VARIANT_TEXT.replace("${VARIANT}", "A"),), False),
        (("lite", "full"), (VARIANT_TEXT.replace("F.SilkS", "Cmts.User"),), False),
        (
            ("lite", "full"),
            (
                '(footprint "R" (layer "F.Cu") (property "Value" "${VARIANT}" (at 0 0)'
                ' (layer "F.Fab") (uuid "p")))',
            ),
            True,
        ),
        (
            ("lite", "full"),
            (
                '(footprint "R" (layer "F.Cu") (property "Value" "${VARIANT}" (at 0 0)'
                ' (layer "F.Fab") (hide yes) (uuid "p")))',
            ),
            False,
        ),
        (
            ("lite", "full"),
            ('(table (layer "Dwgs.User") (cells (table_cell "${VARIANT}" (uuid "c"))))',),
            True,
        ),
    ],
)
def test_variant_text_warning(
    listed: tuple[str, ...], items: tuple[str, ...], warned: bool
) -> None:
    """Text with a variant's variable on a layer the release exports, and the
    board's own list lacking a listed variant: warned; otherwise not."""
    found = variant_text_warning(listed, variant_board(*items), SHOWN)
    assert (found is not None) is warned


def test_variant_text_warning_names_the_layers_texts_and_the_fix() -> None:
    pcb = variant_board(
        VARIANT_TEXT,
        VARIANT_TEXT.replace("F.SilkS", "Dwgs.User"),
        '(variants (variant (name "lite")))',
    )
    assert variant_text_warning(("lite", "full"), pcb, SHOWN) == (
        "text 'rev ${VARIANT}' on F.Silkscreen, User.Drawings will be the same on every"
        " variant's Gerbers: the board's own variants list lacks full (kicad-cli then gives the"
        " variables one value): add the variants to the board's list (pcbnew's AddVariant, then"
        " save)"
    )


def test_variant_text_default_layer_names() -> None:
    """A board with no (layers ...) names: KiCad's default names."""
    pcb = parse(f"(kicad_pcb {VARIANT_TEXT})")
    assert variant_text_warning(("lite",), pcb, SHOWN) is not None


@pytest.mark.parametrize(("where", "warned"), [("project", True), ("board", False)])
def test_release_warns_on_variant_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    where: str,
    warned: bool,
) -> None:
    """Variants in the .kicad_pro only and ${VARIANT} on the silkscreen:
    the release warns, and is made as without the text."""
    pcb = FIXTURE["hw/b.kicad_pcb"].replace("(kicad_pcb ", f"(kicad_pcb {VARIANT_TEXT}\n", 1)
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    root, _ = variant_release(tmp_path, monkeypatch, LITE_OUTPUTS, where=where)
    err = capsys.readouterr().err
    assert ("warning: b.kicad_pcb: text 'rev ${VARIANT}' on F.Silkscreen" in err) is warned
    assert (root / "production/20261118-1/b-1-lite-bom.csv").exists()


def test_release_refuses_a_board_without_a_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb = FIXTURE["hw/b.kicad_pcb"].replace('(rev "1")', "")
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    with pytest.raises(SystemExit, match=r"^no revision in b.kicad_pcb's title block$"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []


def test_release_refuses_an_existing_release_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "repo/production/20261118-1/b-1-bom.csv"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    with pytest.raises(
        SystemExit, match=r"production/20261118-1 exists; use --force to replace it$"
    ):
        release(tmp_path, monkeypatch)
    assert old.read_text() == "kept\n"
    assert kicad_calls(tmp_path) == []


def test_release_refuses_missing_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = FIXTURE["fab.toml"].replace('sources = ["docs"]', 'sources = ["docs", "gone"]')
    monkeypatch.setitem(FIXTURE, "fab.toml", toml)
    with pytest.raises(SystemExit, match=r"^fab.toml names files that are not there: gone$"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []


def test_release_stops_on_an_unreadable_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An input the pointer check cannot read: named, before anything runs."""
    monkeypatch.setitem(FIXTURE, "docs/secret.txt", "x\n")

    def unreadable(path: Path) -> bool:
        if path.name == "secret.txt":
            raise PermissionError(13, "Permission denied", str(path))
        return False

    monkeypatch.setattr(fab_release, "lfs_pointer", unreadable)
    with pytest.raises(SystemExit, match=r"docs/secret.txt: Permission denied$"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []


def test_core_imports_no_profile_module() -> None:
    """fab_release imports PROFILES and profile.py, never a profile's module;
    profile.py imports nothing from ee_tooling."""
    import ast

    def imports(module: str) -> set[str]:
        tree = ast.parse(Path(importlib.util.find_spec(module).origin or "").read_text())  # type: ignore[union-attr]
        return {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }

    core = imports("ee_tooling.fab_release")
    assert {m for m in core if m.startswith("ee_tooling.profiles")} == {
        "ee_tooling.profiles",
        "ee_tooling.profiles.profile",
    }
    assert not [m for m in imports("ee_tooling.profiles.profile") if m.startswith("ee_tooling")]


def test_source_tgz_holds_each_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source holding the project's files ("hw", or "."): each once."""
    toml = FIXTURE["fab.toml"].replace('sources = ["docs"]', 'sources = ["hw", "docs", "."]')
    monkeypatch.setitem(FIXTURE, "fab.toml", toml)
    root, _ = release(tmp_path, monkeypatch)
    with tarfile.open(root / "production/20261118-1/b-1-source.tgz") as t:
        files = [m.name for m in t.getmembers() if m.isfile()]
    assert len(files) == len(set(files))
    assert "b-1-source/hw/b.kicad_pcb" in files
    assert "b-1-source/fab.toml" in files


@pytest.mark.parametrize("name", ["hw/b.kicad_pcb", "hw/b.kicad_sch"])
def test_release_names_a_pointer_board_as_a_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The board or schematic a pointer: named as one, before it is parsed."""
    monkeypatch.setitem(FIXTURE, name, POINTER)
    with pytest.raises(SystemExit, match=rf"^Git LFS pointers among .*\n  {name}\n"):
        release(tmp_path, monkeypatch)


def test_pointer_check_skips_git_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sources = ["."]: an old release's archive and .git's files, pointers
    or not, are not the release's inputs."""
    toml = FIXTURE["fab.toml"].replace('sources = ["docs"]', 'sources = ["."]')
    monkeypatch.setitem(FIXTURE, "fab.toml", toml)
    monkeypatch.setitem(FIXTURE, "production/20261117-1/b-1-odb.tgz", POINTER)
    root, _ = release(tmp_path, monkeypatch)
    assert (root / "production/20261118-1/b-1-bom.csv").exists()


def test_pointer_message_by_repository() -> None:
    """The fetch commands for each repository the pointers are in."""
    modules = [Path("libs/shared/sub"), Path("libs/shared")]
    text = pointer_message(
        [Path("docs/a.pdf"), Path("libs/shared/x.step"), Path("libs/shared/sub/y.step")],
        modules,
    )
    assert text.endswith(
        "install git-lfs, then, from the repository's root:\n"
        "  git lfs install --local --skip-repo && git lfs pull\n"
        "  git -C libs/shared lfs install --local --skip-repo && git -C libs/shared lfs pull\n"
        "  git -C libs/shared/sub lfs install --local --skip-repo"
        " && git -C libs/shared/sub lfs pull"
    )


@pytest.mark.parametrize(
    ("sheet", "error"),
    [
        ("sub/gone.kicad_sch", "b: sheet sub/gone.kicad_sch, named by hw/b.kicad_sch: not found"),
        (
            "../../outside.kicad_sch",
            "outside.kicad_sch, named by hw/b.kicad_sch: outside the repository",
        ),
    ],
)
def test_release_refuses_a_sheet_missing_or_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sheet: str, error: str
) -> None:
    """kicad-cli would load either as an empty sheet, its parts gone from
    ERC, parity and the BOMs: refused before anything runs."""
    monkeypatch.setitem(
        FIXTURE, "hw/b.kicad_sch", f'(kicad_sch (sheet (property "Sheetfile" "{sheet}")))\n'
    )
    (tmp_path / "outside.kicad_sch").write_text("(kicad_sch)\n")
    with pytest.raises(SystemExit, match=re.escape(error) + "$"):
        release(tmp_path, monkeypatch)
    assert kicad_calls(tmp_path) == []


@pytest.mark.parametrize(
    ("rev", "variables", "found"),
    [
        ("1", {}, "1"),
        ("${REV}", {"REV": "2.1"}, "2.1"),
        ("A${N}", {"N": "${M}", "M": "3"}, "A3"),
    ],
)
def test_resolved_revision(rev: str, variables: dict[str, str], found: str) -> None:
    assert resolved_revision(rev, {"text_variables": variables}) == found


@pytest.mark.parametrize(
    ("rev", "variables"), [("${REVISION}", {}), ("${A}", {"A": "${A}"}), ("${X}", {"X": 1})]
)
def test_resolved_revision_refused(rev: str, variables: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="title block's rev"):
        resolved_revision(rev, {"text_variables": variables})


def test_release_resolves_a_rev_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rev a project text variable: the directory, files and tag take
    its value, as KiCad's title block shows it."""
    pcb = FIXTURE["hw/b.kicad_pcb"].replace('(rev "1")', '(rev "${REV}")')
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pcb", pcb)
    project = json.loads(FIXTURE["hw/b.kicad_pro"])
    project["text_variables"] = {"REV": "1"}
    monkeypatch.setitem(FIXTURE, "hw/b.kicad_pro", json.dumps(project))
    root, _ = release(tmp_path, monkeypatch)
    assert (root / "production/20261118-1/b-1-bom.csv").exists()
    assert git(root, "tag") == "b-20261118-1"


def test_release_cross_checks_the_default_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without variants too: a part fitted in the schematic and DNP on the
    board, refused before anything is written."""
    pos = "".join(line + "\n" for line in FAKE_POS.splitlines() if '"R2"' not in line)
    monkeypatch.setitem(FAKE_OUTPUTS, "pcb export pos", {"": pos})
    with pytest.raises(SystemExit, match=r"^R2: fitted in the schematic, DNP on the board\n"):
        release(tmp_path, monkeypatch)
    assert not (tmp_path / "repo/production").exists()


@pytest.mark.parametrize(
    ("footprint", "placed", "problems"),
    [
        ("lib:R_0402", True, []),
        (
            "other:R_0402",
            True,
            [
                "R1: Footprint 'other:R_0402' in the schematic, 'lib:R_0402' on the board:"
                " a variant cannot change a footprint"
            ],
        ),
        (
            "lib:R_0603",
            False,
            [
                "R1: Footprint 'lib:R_0603' in the schematic, 'lib:R_0402' on the board:"
                " a variant cannot change a footprint"
            ],
        ),
    ],
)
def test_cross_checks_footprint_library(footprint: str, placed: bool, problems: list[str]) -> None:
    """The board's LIBRARY:NAME, for a part placed or not (excluded from
    position files): another library's footprint of the same name too."""
    found = [placement("R1")] if placed else []
    assert (
        cross_checks([Part("R1", "10k", footprint, "")], found, found, {"R1": "lib:R_0402"})
        == problems
    )


def test_board_footprints() -> None:
    pcb = parse(
        '(kicad_pcb (footprint "lib:R" (property "Reference" "R1"))'
        ' (footprint "lib:C" (property "Value" "1u") (property "Reference" "C1")))'
    )
    assert board_footprints(pcb) == {"R1": "lib:R", "C1": "lib:C"}


def test_located_production_errors(tmp_path: Path) -> None:
    """Links followed: a production directory a link out of the
    repository, and two names of one directory, refused."""
    root = tmp_path / "repo"
    (root / "out/b").mkdir(parents=True)
    (root / "alias").symlink_to(root / "out")
    (root / "away").symlink_to(tmp_path)
    boards = load_config(
        BOARD.replace('production = "production"', 'production = "away/x"')
    ).values()
    assert located_production_errors(root, boards) == [
        "[[board]] 'b': production 'away/x' leads out of the repository (a link)"
    ]
    two = BOARD.replace('production = "production"', 'production = "out/b"') + BOARD.replace(
        'name = "b"', 'name = "c"'
    ).replace('production = "production"', 'production = "alias/b"')
    assert located_production_errors(root, load_config(two).values()) == [
        "[[board]] 'b' and 'c': production 'out/b' and 'out/b' are the same directory,"
        " or one holds the other"
    ]


def rebuild_refused(monkeypatch: pytest.MonkeyPatch, match: str) -> None:
    """A rebuild of the fixture's release (--force) refused as uncommitted,
    the status naming match."""
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force"])
    with pytest.raises(
        SystemExit, match=rf"uncommitted changes in the release's sources:\n(.*\n)*.*{match}"
    ):
        main()


def test_uncommitted_fab_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = release(tmp_path, monkeypatch)
    (root / "fab.toml").write_text((root / "fab.toml").read_text().replace("tg = 150", "tg = 170"))
    rebuild_refused(monkeypatch, "fab.toml")


def test_uncommitted_ee_tooling_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ee_tooling checkout the release runs from, a submodule of the
    board repository: a change in it (a parts.toml correction) refused."""
    root, _ = release(tmp_path, monkeypatch)
    submodule = library_submodule(tmp_path, root)
    monkeypatch.setattr(fab_release, "CHECKOUT", submodule)
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    main()
    (submodule / "parts.toml").write_text("[rotation]\n")
    rebuild_refused(monkeypatch, "libs/shared")


def test_uncommitted_source_in_a_submodule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source inside a submodule: its change, as the submodule's."""
    root, _ = release(tmp_path, monkeypatch)
    submodule = library_submodule(tmp_path, root)
    (root / "hw/fp-lib-table").write_text("(fp_lib_table)\n")
    (root / "fab.toml").write_text(
        (root / "fab.toml")
        .read_text()
        .replace('sources = ["docs"]', 'sources = ["libs/shared/x.pretty"]')
    )
    git(root, "commit", "-q", "-am", "source in a submodule")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    main()
    (submodule / "x.pretty/R.kicad_mod").write_text("(footprint R2)\n")
    rebuild_refused(monkeypatch, "libs/shared")


def test_uncommitted_through_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source that is a link inside the repository: its target's change
    refused; a link out of the repository refused as such."""
    monkeypatch.setitem(FIXTURE, "shared/notes.txt", "notes\n")
    root, _ = release(tmp_path, monkeypatch)
    (root / "docs/linked").symlink_to(root / "shared")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "link")
    (root / "fab.toml").write_text(
        (root / "fab.toml").read_text().replace('sources = ["docs"]', 'sources = ["docs/linked"]')
    )
    git(root, "commit", "-q", "-am", "linked source")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    main()
    (root / "shared/notes.txt").write_text("changed\n")
    rebuild_refused(monkeypatch, "shared/notes.txt")
    git(root, "checkout", "--", "shared")
    (root / "docs/linked").unlink()
    (root / "docs/linked").symlink_to(tmp_path)
    git(root, "commit", "-q", "-am", "out")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    with pytest.raises(SystemExit, match=r"^docs/linked leads out of the repository \(a link\)"):
        main()


def test_ignored_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file the release reads that git ignores: as untracked."""
    monkeypatch.setitem(FIXTURE, ".gitignore", "secret.txt\n")
    root, _ = release(tmp_path, monkeypatch)
    (root / "docs/secret.txt").write_text("not committed\n")
    rebuild_refused(monkeypatch, "!! docs/secret.txt")


def test_uncommitted_nested_submodule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A library submodule's own submodule, its .gitmodules ignore = all:
    a change in it refused."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "a.kicad_mod").write_text("(footprint A)\n")
    git(inner, "init", "-q")
    git(inner, "add", ".")
    git(inner, "commit", "-q", "-m", "inner")
    root, _ = release(tmp_path, monkeypatch)
    lib = tmp_path / "lib"
    (lib / "x.pretty").mkdir(parents=True)
    (lib / "x.pretty/R.kicad_mod").write_text("(footprint R)\n")
    git(lib, "init", "-q")
    git(lib, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "inner")
    git(lib, "config", "-f", ".gitmodules", "submodule.inner.ignore", "all")
    git(lib, "add", ".")
    git(lib, "commit", "-q", "-m", "lib")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "libs/shared")
    git(
        root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "-q",
        "--init",
        "--recursive",
    )
    (root / "hw/fp-lib-table").write_text(
        '(fp_lib_table (lib (name "x")(uri "${KIPRJMOD}/../libs/shared/x.pretty")))\n'
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "library")
    monkeypatch.setattr(sys, "argv", ["fab-release", "--date", "20261118", "--force", "--retag"])
    main()
    (root / "libs/shared/inner/a.kicad_mod").write_text("(footprint B)\n")
    rebuild_refused(monkeypatch, "libs/shared/inner")


def test_production_is_a_literal_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """production = "*": the check leaves out the directory named *, not
    everything."""
    toml = FIXTURE["fab.toml"].replace('production = "production"', 'production = "*"')
    monkeypatch.setitem(FIXTURE, "fab.toml", toml)
    root, _ = release(tmp_path, monkeypatch)
    assert (root / "*/20261118-1/b-1-bom.csv").exists()
    (root / "docs/notes.txt").write_text("changed\n")
    rebuild_refused(monkeypatch, "docs/notes.txt")
