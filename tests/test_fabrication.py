import json
import zipfile
from pathlib import Path

import pytest

from ee_tooling.fabrication import (
    GerberFile,
    Impedance,
    Minimums,
    Stackup,
    board_thickness,
    gerber_files,
    gerber_map,
    impedances,
    minimums,
    notes,
    ounces,
    stackup,
    stackup_document,
    table,
)
from ee_tooling.jlcpcb_release import Board, Release
from ee_tooling.sexpr import parse

LAYERS = ["F.Cu", "In1.Cu", "B.Cu", "F.Mask", "F.Silkscreen", "Edge.Cuts"]


def attribute(path: str, function: str, polarity: str = "Positive") -> dict[str, str]:
    return {"Path": path, "FileFunction": function, "FilePolarity": polarity}


def write_gerbers(directory: Path) -> None:
    job = {
        "FilesAttributes": [
            attribute("b-F_Cu.gtl", "Copper,L1,Top"),
            attribute("b-In1_Cu.g1", "Copper,L2,Inr"),
            attribute("b-B_Cu.gbl", "Copper,L3,Bot"),
            attribute("b-F_Mask.gts", "SolderMask,Top", "Negative"),
            attribute("b-F_Silkscreen.gto", "Legend,Top"),
            attribute("b-Edge_Cuts.gm1", "Profile"),
        ],
        "DesignRules": [
            {"Layers": "Outer", "PadToPad": 0.2, "TrackToTrack": 0.15, "MinLineWidth": 0.127},
            {"Layers": "Inner", "PadToPad": 0.0, "TrackToRegion": 0.1, "MinLineWidth": 0.15},
        ],
    }
    (directory / "b-job.gbrjob").write_text(json.dumps(job))
    (directory / "b-PTH.drl").write_text(
        "M48\n; #@! TF.FileFunction,Plated,1,3,PTH\nMETRIC\nT1C0.300\nT2C0.250\n%\nT1\nX1Y1\n"
    )
    (directory / "b-NPTH.drl").write_text(
        "M48\n; #@! TF.FileFunction,NonPlated,1,3,NPTH\nT1C3.200\n"
    )


def test_table() -> None:
    assert table(("A", "Bb"), [("xyz", ""), ("1", "2")]) == "A    Bb\n---  --\nxyz  -\n1    2\n"


def test_gerber_files(tmp_path: Path) -> None:
    write_gerbers(tmp_path)
    files = gerber_files(tmp_path, "b", LAYERS)
    assert [(f.path, f.layer, f.description, f.side, f.polarity) for f in files] == [
        ("b-F_Cu.gtl", "F.Cu", "copper L1", "top", "Positive"),
        ("b-In1_Cu.g1", "In1.Cu", "copper L2", "inner", "Positive"),
        ("b-B_Cu.gbl", "B.Cu", "copper L3", "bottom", "Positive"),
        ("b-F_Mask.gts", "F.Mask", "solder mask", "top", "Negative"),
        ("b-F_Silkscreen.gto", "F.Silkscreen", "silkscreen", "top", "Positive"),
        ("b-Edge_Cuts.gm1", "Edge.Cuts", "board outline", "", "Positive"),
        ("b-NPTH.drl", "", "drill, non-plated (NPTH), L1 to L3", "", ""),
        ("b-PTH.drl", "", "drill, plated (PTH), L1 to L3", "", ""),
        ("b-job.gbrjob", "", "Gerber job file (JSON)", "", ""),
    ]


def test_gerber_files_without_job(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="0 Gerber job files"):
        gerber_files(tmp_path, "b", LAYERS)


def test_gerber_files_drill_without_function(tmp_path: Path) -> None:
    write_gerbers(tmp_path)
    (tmp_path / "b-PTH.drl").write_text("M48\nMETRIC\n")
    with pytest.raises(ValueError, match=r"no TF\.FileFunction"):
        gerber_files(tmp_path, "b", LAYERS)


def test_gerber_map() -> None:
    text = gerber_map(
        "b 1.0: the files in b-1.0-jlcpcb.zip",
        [GerberFile("b-F_Mask.gts", ("SolderMask", "Top"), "Negative", "F.Mask")],
    )
    assert text.startswith("b 1.0: the files in b-1.0-jlcpcb.zip\n\n")
    assert text.endswith("b-F_Mask.gts  F.Mask  solder mask  top   negative\n")


def test_gerbers_zip_holds_the_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def kicad(self: Release, *args: str) -> None:
        if args[2] == "gerbers":
            write_gerbers(Path(args[args.index("-o") + 1]))

    monkeypatch.setattr(Release, "kicad", kicad)
    work = tmp_path / "work"
    work.mkdir()
    b = Board("b", Path("hw"), ("F.Cu", "In1.Cu", "B.Cu"), Path("production"), ())
    out = tmp_path / "b-1-jlcpcb.zip"
    files = Release(tmp_path, b, work).gerbers(out, "b 1")
    with zipfile.ZipFile(out) as z:
        assert z.read("README.txt").decode() == gerber_map("b 1", files)
        assert "b-PTH.drl" in z.namelist()


PCB = """(kicad_pcb (general (thickness 1.6))
  (setup
    (stackup
      (layer "F.SilkS" (type "Top Silk Screen") (color "White"))
      (layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (material "2116")
        (epsilon_r 4.4) (loss_tangent 0.02) addsublayer
        (thickness 0.05) (material "1080") (epsilon_r 4.2) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.0152 locked))
      (layer "dielectric 2" (type "core") (thickness 1.2) (material "FR4") (epsilon_r 4.6))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (copper_finish "ENIG")
      (dielectric_constraints yes)
      (castellated_pads yes))
    (pad_to_mask_clearance 0)))"""

PROJECT = {
    "net_settings": {
        "classes": [
            {"name": "Default", "track_width": 0.2},
            {"name": "90Z_DIFF", "track_width": 0.2, "diff_pair_width": 0.3, "diff_pair_gap": 0.15},
            {"name": "50Z_SE", "track_width": 0.36},
            {"name": "USB_90Z", "track_width": 0.3, "diff_pair_width": 0.3, "diff_pair_gap": 0.2},
            {"name": "Power", "track_width": 0.4},
        ]
    }
}

FILES = [
    GerberFile("b-F_Cu.gtl", ("Copper", "L1", "Top"), "Positive", "F.Cu"),
    GerberFile("b-In1_Cu.g1", ("Copper", "L2", "Inr"), "Positive", "In1.Cu"),
    GerberFile("b-B_Cu.gbl", ("Copper", "L3", "Bot"), "Positive", "B.Cu"),
    GerberFile("b-F_Mask.gts", ("SolderMask", "Top"), "Negative", "F.Mask"),
    GerberFile("b-F_Silkscreen.gto", ("Legend", "Top"), "Positive", "F.Silkscreen"),
]


def test_stackup_sublayers_and_settings() -> None:
    s = stackup(parse(PCB))
    assert s is not None
    assert [(x.name, x.thickness, x.material) for x in s.layers[3:5]] == [
        ("dielectric 1", 0.1, "2116"),
        ("dielectric 1", 0.05, "1080"),
    ]
    assert s.layers[4].type == "prepreg"
    assert s.layers[5].thickness == 0.0152
    assert s.thickness == pytest.approx(1.4452)
    assert s.settings == {
        "copper_finish": "ENIG",
        "dielectric_constraints": "yes",
        "castellated_pads": "yes",
    }


def test_no_stackup() -> None:
    pcb = parse("(kicad_pcb (general (thickness 1.6)) (setup (pad_to_mask_clearance 0)))")
    assert stackup(pcb) is None
    assert board_thickness(pcb) == 1.6
    text = stackup_document("b 1: stackup", None, 1.6, FILES, [])
    assert "The board has no stackup" in text
    assert "Board thickness setting: 1.6 mm." in text
    assert "Impedance control: none" in text


def test_impedances() -> None:
    assert impedances(PROJECT) == [
        Impedance("90Z_DIFF", "90", "differential", 0.2, 0.3, 0.15),
        Impedance("50Z_SE", "50", "single-ended", 0.36, None, None),
        Impedance("USB_90Z", "90", "", 0.3, 0.3, 0.2),
    ]


def test_stackup_document() -> None:
    text = stackup_document("b 1: stackup", stackup(parse(PCB)), 1.6, FILES, impedances(PROJECT))
    lines = text.splitlines()
    assert lines[0] == "b 1: stackup"
    row = {line.split()[1]: line for line in lines if line.startswith("L")}
    assert row["F.Cu"].endswith("b-F_Cu.gtl")
    assert row["In1.Cu"].endswith("b-In1_Cu.g1")
    assert "b-F_Mask.gts" in next(line for line in lines if "F.Mask" in line)
    assert "b-F_Silkscreen.gto" in next(line for line in lines if "F.SilkS" in line)
    assert "Total thickness: 1.4452 mm (board thickness setting: 1.6 mm)." in text
    assert "Copper finish: ENIG" in text
    assert "Castellated pads: yes" in text
    assert "JLCPCB's impedance calculator" in text
    usb = next(line for line in lines if line.startswith("USB_90Z"))
    assert "[TBD: name not <ohms>Z_DIFF[_<INTERFACE>] or <ohms>Z_SE]" in usb
    se = next(line for line in lines if line.startswith("50Z_SE"))
    assert se.split()[1:] == ["50", "single-ended", "0.36", "-", "-"]


def test_release_fabrication_warns_without_stackup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    b = Board("b", Path("hw"), ("F.Cu", "B.Cu"), Path("production"), ())
    (tmp_path / "hw").mkdir()
    (tmp_path / "hw/b.kicad_pro").write_text(json.dumps(PROJECT))
    (tmp_path / "gerbers").mkdir()
    write_gerbers(tmp_path / "gerbers")
    pcb = parse("(kicad_pcb (general (thickness 1.6)))")
    Release(tmp_path, b, tmp_path).fabrication(tmp_path, "b-1", "b 1", pcb, FILES)
    assert "b.kicad_pcb has no stackup" in capsys.readouterr().out
    assert (tmp_path / "b-1-stackup.txt").read_text() == stackup_document(
        "b 1: stackup", None, 1.6, FILES, impedances(PROJECT)
    )
    assert (tmp_path / "b-1-fab-notes.txt").read_text() == notes(
        "b 1: fabrication notes",
        None,
        1.6,
        FILES,
        impedances(PROJECT),
        Minimums(0.127, 0.1, 0.25),
        "b-1-stackup.txt",
        "b-1-gerber-map.txt",
    )


def test_minimums(tmp_path: Path) -> None:
    write_gerbers(tmp_path)
    assert minimums(tmp_path) == Minimums(0.127, 0.1, 0.25)
    assert minimums(tmp_path / "missing") == Minimums(None, None, None)


@pytest.mark.parametrize(
    ("thickness", "text"),
    [(0.035, "35 um (1 oz)"), (0.0152, "15.2 um (0.5 oz)"), (0.07, "70 um (2 oz)")],
)
def test_ounces(thickness: float, text: str) -> None:
    assert ounces(thickness) == text


def notes_lines(found: Stackup | None, impedance: list[Impedance]) -> dict[str, str]:
    """The notes' items, by their heading."""
    text = notes("b 1: notes", found, 1.6, FILES, impedance, Minimums(0.2, 0.15, None), "s", "m")
    assert text.startswith("b 1: notes\nUnless otherwise specified.\n\n1.  STANDARD:")
    items = [line.split(None, 1)[1] for line in text.splitlines()[3:]]
    return dict(item.split(": ", 1) for item in items)


def test_notes_from_stackup() -> None:
    n = notes_lines(stackup(parse(PCB)), impedances(PROJECT))
    assert n["STANDARD"].startswith("build and inspect to IPC-6012 and IPC-A-600, class 2")
    assert n["BASE MATERIAL"] == "2116, 1080, FR4; Tg [TBD: Tg, deg C]."
    assert n["LAYERS"] == "3 copper, L1 (top) to L3 (bottom), per m."
    assert n["THICKNESS"] == "finished board 1.4452 mm, per s."
    assert n["COPPER"] == (
        "finished weights L1 35 um (1 oz), L2 15.2 um (0.5 oz), L3 35 um (1 oz)."
    )
    assert n["FINISH"] == "ENIG."
    assert n["SOLDER MASK"].endswith("over bare copper; top, green.")
    assert n["SILKSCREEN"].startswith("top, white;")
    assert n["IMPEDANCE CONTROL"].startswith("yes, per s;")
    assert n["MINIMUM FEATURES"] == ("track 0.2 mm, space 0.15 mm, finished drill [TBD: drill].")
    assert n["ELECTRICAL TEST"].startswith("[TBD: electrical test")


def test_notes_without_stackup() -> None:
    n = notes_lines(None, [])
    assert n["BASE MATERIAL"].startswith("[TBD: base material")
    assert n["THICKNESS"] == "finished board 1.6 mm (KiCad's board thickness setting; no stackup)."
    assert n["COPPER"].startswith("finished weights [TBD: copper weights")
    assert n["FINISH"] == "[TBD: surface finish]."
    assert n["SOLDER MASK"].endswith("; top, [TBD: color].")
    assert n["IMPEDANCE CONTROL"] == "no."


def test_notes_colors_by_side() -> None:
    found = stackup(
        parse(
            '(kicad_pcb (setup (stackup (layer "F.Mask" (type "Top Solder Mask") (color "Green"))'
            ' (layer "F.Cu" (type "copper") (thickness 0.035))'
            ' (layer "B.Mask" (type "Bottom Solder Mask") (color "Black")))))'
        )
    )
    files = [*FILES, GerberFile("b-B_Mask.gbs", ("SolderMask", "Bot"), "Negative", "B.Mask")]
    text = notes("t", found, None, files, [], Minimums(None, None, None), "s", "m")
    assert "over bare copper; top green; bottom black." in text
