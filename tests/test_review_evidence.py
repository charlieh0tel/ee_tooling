import shlex
from pathlib import Path

import pytest

from ee_tooling.review_evidence import (
    PLOT_LAYERS,
    PNG_GROUPS,
    SUMMARIZED,
    Project,
    commands,
    export,
)

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
    a source are skipped, an earlier run's files are gone, and the board
    summary is still written."""
    (tmp_path / "W.kicad_pcb").write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal) (31 "B.Cu" signal)))', encoding="utf-8"
    )
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = tmp_path / "out"
    out.mkdir()
    for stale in SUMMARIZED:
        (out / stale).write_text("from an earlier run", encoding="utf-8")
    failed = export(Project(tmp_path, "W"), out)
    assert len(failed) == len(commands(Project(tmp_path, "W"), out, ["F.Cu", "B.Cu"]))
    err = capsys.readouterr().err
    assert "failed: kicad-cli not on the path: kicad-cli sch erc" in err
    assert "skipped: nets.txt and components.txt, without" in err
    assert "skipped: erc.md, without" in err
    assert (out / "board.md").read_text(encoding="utf-8") != "from an earlier run"
    assert not any((out / stale).exists() for stale in SUMMARIZED if stale != "board.md")


def test_board_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No board file: said on stderr, board.md failed, the rest still run."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = tmp_path / "out"
    failed = export(Project(tmp_path, "W"), out)
    err = capsys.readouterr().err
    assert "skipped: the copper layers' exports, unreadable" in err
    assert "failed: board.md, from" in err
    assert len(failed) == len(commands(Project(tmp_path, "W"), out, [])) + 1
    assert not (out / "board.md").exists()


def test_board_unreadable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "W.kicad_pcb").write_text("(kicad_pcb (layers", encoding="utf-8")
    assert export(Project(tmp_path, "W"), tmp_path / "out", dry_run=True) == []
    assert "unreadable" in capsys.readouterr().err


def test_dry_run_quoted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Each line pastes into a shell: a path with a space and ${QUANTITY}
    are quoted."""
    project = tmp_path / "my proj"
    project.mkdir()
    (project / "W.kicad_pcb").write_text('(kicad_pcb (layers (0 "F.Cu" signal)))', encoding="utf-8")
    export(Project(project, "W"), tmp_path / "out", dry_run=True)
    lines = capsys.readouterr().out.splitlines()
    assert [shlex.split(line) for line in lines] == commands(
        Project(project, "W"), tmp_path / "out", ["F.Cu"]
    )
    assert any("'Reference,Value,Footprint,MPN,Manufacturer,${QUANTITY},DNP'" in x for x in lines)
