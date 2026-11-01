import json
import zipfile
from pathlib import Path

import pytest

from ee_tooling.fabrication import GerberFile, gerber_files, gerber_map, table
from ee_tooling.jlcpcb_release import Board, Release

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
        ]
    }
    (directory / "b-job.gbrjob").write_text(json.dumps(job))
    (directory / "b-PTH.drl").write_text("M48\n; #@! TF.FileFunction,Plated,1,3,PTH\nMETRIC\n")
    (directory / "b-NPTH.drl").write_text("M48\n; #@! TF.FileFunction,NonPlated,1,3,NPTH\n")


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
    text = Release(tmp_path, b, work).gerbers(out, "b 1")
    assert text.startswith("b 1\n\n")
    with zipfile.ZipFile(out) as z:
        assert z.read("README.txt").decode() == text
        assert "b-PTH.drl" in z.namelist()
