import shlex
import shutil
from pathlib import Path

import pytest

from ee_tooling import review_evidence
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
    assert pngs[-1][-1] == "/out/png/B.Mask_B.Paste"
    assert "/out/pcb/B.Paste.svg" in [c[-2] for c in svgs]


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


def test_export_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tool that hangs is stopped and reported; the rest run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("kicad-cli", "pdftoppm"):
        (bin_dir / tool).write_text("#!/bin/sh\nexec sleep 5\n", encoding="utf-8")
        (bin_dir / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(review_evidence, "EXPORT_TIMEOUT", 0.05)
    (tmp_path / "W.kicad_pcb").write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal)))', encoding="utf-8"
    )
    out = tmp_path / "out"
    failed = export(Project(tmp_path, "W"), out)
    assert len(failed) == len(commands(Project(tmp_path, "W"), out, ["F.Cu"]))
    assert "failed: over 0.05 s: kicad-cli sch erc" in capsys.readouterr().err


# A two-layer board with one track, and an empty schematic, that kicad-cli
# 10 reads.
E2E_SCHEMATIC = """(kicad_sch (version 20250114) (generator "eeschema") (generator_version "10.0")
  (uuid "4a0b7c2e-1111-4222-8333-944455556666") (paper "A4")
  (title_block (title "Widget"))
  (lib_symbols))
"""
E2E_BOARD = """(kicad_pcb (version 20260206) (generator "pcbnew") (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)
    (1 "F.Mask" user) (3 "B.Mask" user) (13 "F.Paste" user) (15 "B.Paste" user)
    (5 "F.SilkS" user) (7 "B.SilkS" user) (35 "F.Fab" user) (37 "B.Fab" user))
  (net 0 "") (net 1 "GND")
  (gr_rect (start 0 0) (end 40 30) (stroke (width 0.05) (type solid)) (layer "Edge.Cuts"))
  (segment (start 5 15) (end 35 15) (width 0.25) (layer "F.Cu") (net 1)))
"""


@pytest.mark.skipif(
    shutil.which("kicad-cli") is None or shutil.which("pdftoppm") is None,
    reason="no kicad-cli (KiCad 10) or poppler on the path",
)
def test_export_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every export runs and every file lands; small renders and PNGs keep
    it quick."""
    monkeypatch.setattr(review_evidence, "RENDER_SIZE", ("120", "80"))
    monkeypatch.setattr(review_evidence, "PNG_DPI", "20")
    (tmp_path / "W.kicad_sch").write_text(E2E_SCHEMATIC, encoding="utf-8")
    (tmp_path / "W.kicad_pcb").write_text(E2E_BOARD, encoding="utf-8")
    (tmp_path / "W.kicad_pro").write_text('{"meta": {"filename": "W.kicad_pro"}}', encoding="utf-8")
    out = tmp_path / "out"
    assert export(Project(tmp_path, "W"), out) == []
    copper = ["F.Cu", "B.Cu"]
    expected = [
        "erc.json", "netlist.net", "bom.csv", "drc.json", "drc-parity.json",
        "sch/W.pdf", "pcb/positions.csv", "png/render-top.png", "png/render-bottom.png",
        *SUMMARIZED,
        *(f"pcb/{layer}.svg" for layer in (*copper, *PLOT_LAYERS)),
        *(f"png/{'_'.join(group)}.png" for group in (*((c,) for c in copper), *PNG_GROUPS)),
    ]  # fmt: skip
    assert [f for f in expected if not (out / f).is_file()] == []
    assert list((out / "pcb" / "drill").iterdir())
    assert "segments: 1; length per layer (mm): F.Cu 30" in (out / "board.md").read_text()
    assert (out / "erc.md").read_text().startswith("# ERC summary: erc.json")


def test_export_failure_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tool that exits non-zero is reported with its stderr; the rest run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("kicad-cli", "pdftoppm"):
        (bin_dir / tool).write_text("#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8")
        (bin_dir / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    (tmp_path / "W.kicad_pcb").write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal)))', encoding="utf-8"
    )
    out = tmp_path / "out"
    failed = export(Project(tmp_path, "W"), out)
    assert len(failed) == len(commands(Project(tmp_path, "W"), out, ["F.Cu"]))
    err = capsys.readouterr().err
    assert "failed: kicad-cli sch erc" in err and "\nbroken\n" in err
