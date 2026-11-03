from pathlib import Path

import pytest

from ee_tooling.review_evidence import PLOT_LAYERS, PNG_GROUPS, Project, commands, export

COPPER = ["F.Cu", "In1.Cu", "B.Cu"]


def test_commands() -> None:
    p = Project(Path("/b"), "Widget")
    found = commands(p, Path("/out"), COPPER)
    assert found[0][:3] == ["kicad-cli", "sch", "erc"] and found[0][-1] == "/b/Widget.kicad_sch"
    assert all(c[0] in ("kicad-cli", "pdftoppm") for c in found)
    svgs = [c for c in found if c[:4] == ["kicad-cli", "pcb", "export", "svg"]]
    assert len(svgs) == len(COPPER) + len(PLOT_LAYERS)
    assert svgs[1][-2] == "/out/pcb/In1.Cu.svg"
    pngs = [c for c in found if c[0] == "pdftoppm"]
    assert len(pngs) == len(COPPER) + len(PNG_GROUPS)
    assert pngs[1][-1] == "/out/png/In1.Cu"
    assert pngs[-1][-1] == "/out/png/F.Mask_F.Paste"


def test_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "W.kicad_pcb").write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal) (1 "In1.Cu" power) (31 "B.Cu" signal)))',
        encoding="utf-8",
    )
    assert export(Project(tmp_path, "W"), tmp_path / "out", dry_run=True) == []
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(commands(Project(tmp_path, "W"), tmp_path / "out", COPPER))
    board = str(tmp_path / "W.kicad_pcb")
    assert any(line.endswith(f"/out/pcb/In1.Cu.svg {board}") for line in lines)
    assert not (tmp_path / "out").exists()


def test_tools_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No kicad-cli or pdftoppm: every command fails, the summaries without
    a source are skipped, and the board summary is still written."""
    (tmp_path / "W.kicad_pcb").write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal) (31 "B.Cu" signal)))', encoding="utf-8"
    )
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = tmp_path / "out"
    failed = export(Project(tmp_path, "W"), out)
    assert len(failed) == len(commands(Project(tmp_path, "W"), out, ["F.Cu", "B.Cu"]))
    err = capsys.readouterr().err
    assert "failed: kicad-cli not on the path: kicad-cli sch erc" in err
    assert "skipped: nets.txt and components.txt, without" in err
    assert "skipped: erc.md, without" in err
    assert (out / "board.md").is_file() and not (out / "erc.md").exists()
