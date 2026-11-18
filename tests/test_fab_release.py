import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from test_profiles_jlcpcb import CORRECTIONS

from ee_tooling.fab_release import (
    Board,
    Release,
    TagPlan,
    load_config,
    main,
    profile_options,
    revision,
    tag_plan,
)
from ee_tooling.profiles.jlcpcb import Corrections, Jlcpcb, Options
from ee_tooling.profiles.profile import GerberOptions

CONFIG = """
[[board]]
name = "main"
directory = "hardware/main"
production = "production"
sources = ["hardware/main/main.kicad_sym", "docs"]
profile = "jlcpcb"

[[board]]
name = "jig"
directory = "hardware/jig"
production = "production/jig"
profile = "jlcpcb"
"""


def test_config_boards_in_order() -> None:
    boards = load_config(CONFIG)
    assert list(boards) == ["main", "jig"]
    main = boards["main"]
    assert main.pcb == Path("hardware/main/main.kicad_pcb")
    assert main.sch == Path("hardware/main/main.kicad_sch")
    assert boards["jig"].sources == ()
    assert main.profile == "jlcpcb"


# A board with every key, for the config's error tests.
BOARD = """\
[[board]]
name = "b"
directory = "hw"
production = "production"
profile = "jlcpcb"
"""


def test_config_without_boards() -> None:
    with pytest.raises(ValueError, match="no \\[\\[board\\]\\]"):
        load_config("")


@pytest.mark.parametrize("key", ["name", "directory", "production", "profile"])
def test_config_missing_key(key: str) -> None:
    text = "".join(line + "\n" for line in BOARD.splitlines() if not line.startswith(key))
    with pytest.raises(ValueError, match=f"no '{key}'$"):
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


def test_revision() -> None:
    pcb = '(kicad_pcb (title_block (title "x") (date "d") (rev "1.2.3")) (net 0 ""))'
    assert revision(pcb) == "1.2.3"
    assert revision('(kicad_pcb (rev "9"))') is None


def test_board_is_frozen() -> None:
    b = Board("x", Path("d"), Path("p"), (), "jlcpcb")
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
    b = Board("b", Path("hw"), Path("production"), (), "jlcpcb")
    work = tmp_path / str(options)
    work.mkdir()
    Release(tmp_path, b, work, ("F.Cu", "In1.Cu", "B.Cu")).gerbers(work / "b.zip", "b 1", options)
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
    r.tag(name, "jig 1: JLCPCB release production/20261102-1", move=False)
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
# its subcommand, as name to contents, "" naming the -o file itself.
FAKE_KICAD_CLI = """
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["FAKE_KICAD_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
key = " ".join(args[:3] if args[1] == "export" else args[:2])
out = pathlib.Path(args[args.index("-o") + 1])
for name, text in json.loads(os.environ["FAKE_OUTPUTS"])[key].items():
    (out / name if name else out).write_text(text)
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
                {"Path": "b-Edge_Cuts.gm1", "FileFunction": "Profile,NP"},
            ],
            "DesignRules": [{"Layers": "Outer", "PadToPad": 0.2, "MinLineWidth": 0.15}],
        }
    ),
    "b-F_Cu.gtl": "G04 F.Cu*\n",
    "b-In1_Cu.g1": "G04 In1.Cu*\n",
    "b-B_Cu.gbl": "G04 B.Cu*\n",
    "b-F_Mask.gts": "G04 F.Mask*\n",
    "b-B_Mask.gbs": "G04 B.Mask*\n",
    "b-F_Silkscreen.gto": "G04 F.Silkscreen*\n",
    "b-Edge_Cuts.gm1": "G04 Edge.Cuts*\n",
}
FAKE_DRILL = {
    "b-PTH.drl": "M48\n; #@! TF.FileFunction,Plated,1,3,PTH\nMETRIC\nT1C0.300\n%\n",
    "b-NPTH.drl": "M48\n; #@! TF.FileFunction,NonPlated,1,3,NPTH\nMETRIC\nT1C3.200\n%\n",
}
FAKE_BOM = """\
"Reference","Value","Footprint","LCSC"
"C1","100n","Capacitor_SMD:C_0402_1005Metric","C1525"
"C2","100n","Capacitor_SMD:C_0402_1005Metric","C1525"
"C3","100n","Capacitor_SMD:C_0402_1005Metric","C307331"
"C10","100n","Capacitor_SMD:C_0402_1005Metric","C1525"
"D1","LED","LED_SMD:LED_0603_1608Metric","C2"
"J1","Conn_01x02","Connector_JST:JST_SH_SM02B","C3"
"R1","10k","Resistor_SMD:R_0402_1005Metric","C25744"
"R2","10k","Resistor_SMD:R_0402_1005Metric","C25744"
"TP1","TP","TestPoint:TestPoint_Pad_D1.0mm",""
"TP2","TP","TestPoint:TestPoint_Pad_D1.0mm",""
"U1","TPS1","Package_TO_SOT_SMD:SOT-23-5","C1"
"U2","NEW1","Package_SO:SOIC-8_3.9x4.9mm_P1.27mm","C4"
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
FAKE_OUTPUTS = {
    "sch erc": {"": "ERC report\n"},
    "pcb drc": {"": "DRC report\n"},
    "pcb export gerbers": FAKE_GERBERS,
    "pcb export drill": FAKE_DRILL,
    "pcb export odb": {"": "ODB++ job\n"},
    "pcb export ipc2581": {"": "IPC-2581 zip\n"},
    "sch export bom": {"": FAKE_BOM},
    "pcb export pos": {"": FAKE_POS},
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
            "net_settings": {
                "classes": [
                    {"name": "Default", "track_width": 0.2},
                    {"name": "90Z_DIFF_USB", "diff_pair_width": 0.2, "diff_pair_gap": 0.15},
                ]
            }
        }
    ),
    "hw/fp-lib-table": "(fp_lib_table)\n",
    "docs/notes.txt": "notes\n",
}


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
    ["sch", "erc", "--exit-code-violations", "-o", "WORK/erc.rpt", "ROOT/hw/b.kicad_sch"],
    [
        "pcb",
        "drc",
        "--schematic-parity",
        "--exit-code-violations",
        "-o",
        "WORK/drc.rpt",
        "ROOT/hw/b.kicad_pcb",
    ],
    [
        "sch",
        "export",
        "bom",
        "--exclude-dnp",
        "--fields",
        "Reference,Value,Footprint,LCSC",
        "-o",
        "WORK/bom.csv",
        "ROOT/hw/b.kicad_sch",
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
        "ROOT/hw/b.kicad_pcb",
    ],
    [
        "pcb",
        "export",
        "gerbers",
        "--layers",
        "F.Cu,In1.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts",
        "--no-x2",
        "--subtract-soldermask",
        "--check-zones",
        "-o",
        "WORK/gerbers",
        "ROOT/hw/b.kicad_pcb",
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
        "ROOT/hw/b.kicad_pcb",
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
        "ROOT/production/20261118-1/b-1-odb.tgz",
        "ROOT/hw/b.kicad_pcb",
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
        "ROOT/production/20261118-1/b-1-ipc2581.zip",
        "ROOT/hw/b.kicad_pcb",
    ],
    ["sch", "export", "pdf", "-o", "WORK/pdf/b-schematic.pdf", "ROOT/hw/b.kicad_sch"],
    [
        "pcb",
        "export",
        "pdf",
        "--mode-multipage",
        "--layers",
        "F.Cu,In1.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Fab,B.Fab",
        "--common-layers",
        "Edge.Cuts",
        "-o",
        "WORK/pdf/b-board.pdf",
        "ROOT/hw/b.kicad_pcb",
    ],
]

# The fixture's release: each text file, by name.
RELEASE_TEXT = {
    "b-1-bom.csv": """\
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
    "b-1-cpl.csv": """\
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
b 1: the files in b-1-jlcpcb.zip

Gerber RS-274X, mm, 4.6 format; Excellon drill, mm, decimal; finished hole sizes.
Copper layers L1 (top) to Ln (bottom).

File                Layer         Function                            Side    Polarity
------------------  ------------  ----------------------------------  ------  --------
b-F_Cu.gtl          F.Cu          copper L1                           top     positive
b-In1_Cu.g1         In1.Cu        copper L2                           inner   positive
b-B_Cu.gbl          B.Cu          copper L3                           bottom  positive
b-F_Mask.gts        F.Mask        solder mask                         top     negative
b-B_Mask.gbs        B.Mask        solder mask                         bottom  negative
b-F_Silkscreen.gto  F.Silkscreen  silkscreen                          top     positive
b-Edge_Cuts.gm1     Edge.Cuts     board outline                       -       -
b-NPTH.drl          -             drill, non-plated (NPTH), L1 to L3  -       -
b-PTH.drl           -             drill, plated (PTH), L1 to L3       -       -
b-job.gbrjob        -             Gerber job file (JSON)              -       -
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

1.  STANDARD: build and inspect to IPC-6012 and IPC-A-600, class 2 (default; confirm).
2.  BASE MATERIAL: FR4; Tg [TBD: Tg, deg C].
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
12. ELECTRICAL TEST: [TBD: electrical test, e.g. 100% netlist test per IPC-9252].
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
    assert capsys.readouterr().out == (
        "warning: no LCSC part number for TP1,TP2\n"
        "check a new part (C4) in JLCPCB's placement preview: no correction known\n"
        "tagged b-20261118-1; push it with: git push origin refs/tags/b-20261118-1\n"
        f"production/20261118-1 from {commit}:\n" + "".join(f"  {f}\n" for f in files)
    )
    assert git(root, "tag", "-n") == "b-20261118-1    b 1: JLCPCB release production/20261118-1"
    assert calls == RELEASE_CALLS
    assert files == [
        "b-1-bom.csv",
        "b-1-cpl.csv",
        "b-1-fab-notes.txt",
        "b-1-gerber-map.txt",
        "b-1-ipc2581.zip",
        "b-1-jlcpcb.zip",
        "b-1-odb.tgz",
        "b-1-source.tgz",
        "b-1-stackup.txt",
    ]
    for name, text in RELEASE_TEXT.items():
        assert (dest / name).read_bytes() == text.encode(), name
    assert (dest / "b-1-odb.tgz").read_text() == "ODB++ job\n"
    assert (dest / "b-1-ipc2581.zip").read_text() == "IPC-2581 zip\n"
    with zipfile.ZipFile(dest / "b-1-jlcpcb.zip") as z:
        assert {i.filename: z.read(i).decode() for i in z.infolist()} == {
            **dict(sorted({**FAKE_GERBERS, **FAKE_DRILL}.items())),
            "README.txt": RELEASE_TEXT["b-1-gerber-map.txt"],
        }
    with tarfile.open(dest / "b-1-source.tgz") as t:
        tgz = {m.name: t.extractfile(m) for m in t.getmembers() if m.isfile()}
        assert [m.name for m in t.getmembers()] == [
            "b-1-source/hw/b.kicad_pcb",
            "b-1-source/hw/b.kicad_sch",
            "b-1-source/hw/b.kicad_pro",
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
        raise tomllib.TOMLDecodeError("bad parts.toml")

    old = tmp_path / "repo/production/20261118-1/old.txt"
    old.parent.mkdir(parents=True)
    old.write_text("kept\n")
    with pytest.raises(tomllib.TOMLDecodeError):
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
    assert kicad_calls(tmp_path) == RELEASE_CALLS[:4]
    assert old.read_text() == "kept\n"
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
