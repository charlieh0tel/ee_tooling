import argparse
import json
import shutil
from pathlib import Path

import pytest
from test_review_board import BOARD
from test_review_erc import REPORT as ERC
from test_review_findings import GOOD
from test_review_netlist import NETLIST
from test_review_pairs import BOARD as PAIRS_BOARD
from test_review_punch import PUNCH
from test_review_punch import REPORT as PUNCH_REPORT

from ee_tooling.review import main, pair


def run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, str, str]:
    """design-review with args: its exit status, stdout and stderr."""
    monkeypatch.setattr("sys.argv", ["design-review", *args])
    try:
        main()
        status = 0
    except SystemExit as e:
        status = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return status, out, err


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_pair() -> None:
    assert pair("CAN_H,CAN_L") == ("CAN_H", "CAN_L")


@pytest.mark.parametrize("arg", ["CAN_H", "CAN_H,", ",CAN_L", "A,B,C"])
def test_pair_malformed(arg: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="a pair is NET_P,NET_N"):
        pair(arg)


def test_pairs_malformed_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pcb = write(tmp_path / "b.kicad_pcb", PAIRS_BOARD)
    status, _, err = run(monkeypatch, capsys, "pairs", str(pcb), "CAN_H")
    assert status == 2 and "a pair is NET_P,NET_N" in err


def test_no_command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    status, _, err = run(monkeypatch, capsys)
    assert status == 2 and "required: command" in err


def test_netlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    net = write(tmp_path / "w.net", NETLIST)
    out = tmp_path / "new" / "dir"
    status, stdout, _ = run(monkeypatch, capsys, "netlist", str(net), str(out))
    assert status == 0 and stdout == f"3 components, 2 nets -> {out}\n"
    assert (out / "nets.txt").is_file() and (out / "components.txt").is_file()


def test_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pcb = write(tmp_path / "b.kicad_pcb", BOARD)
    status, stdout, _ = run(monkeypatch, capsys, "board", str(pcb))
    assert status == 0 and stdout.startswith("# Board summary: b.kicad_pcb\n")


def test_erc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    erc = write(tmp_path / "erc.json", json.dumps(ERC))
    status, stdout, _ = run(monkeypatch, capsys, "erc", str(erc))
    assert status == 0 and stdout.startswith("# ERC summary: erc.json\n")


def test_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pcb = write(tmp_path / "b.kicad_pcb", PAIRS_BOARD)
    status, stdout, _ = run(monkeypatch, capsys, "pairs", str(pcb), "CAN_H,CAN_L")
    assert status == 0 and "## CAN_H / CAN_L" in stdout


def test_findings_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    table = write(tmp_path / "r.md", GOOD)
    status, stdout, err = run(monkeypatch, capsys, "findings", str(table))
    assert (status, stdout, err) == (0, "1 tables, 2 rows, 0 problems\n", "")


def test_findings_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    table = write(tmp_path / "r.md", "prose only\n")
    status, stdout, err = run(monkeypatch, capsys, "findings", str(table))
    assert status == 1 and stdout == "0 tables, 0 rows, 1 problems\n"
    assert err.startswith(f"{table}:0: no findings table")


def test_punch_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = write(tmp_path / "report.md", PUNCH_REPORT)
    punch = write(tmp_path / "punch.md", PUNCH)
    status, stdout, err = run(monkeypatch, capsys, "punch-list", str(report), str(punch))
    assert status == 1 and stdout == "4 problems\n"
    assert f"{punch}:6: S-9 is not a finding of the report" in err
    assert f"{report}:8: S-2 (Major) is not on the punch list" in err


def test_punch_list_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = write(tmp_path / "report.md", PUNCH_REPORT)
    punch = write(tmp_path / "punch.md", "(S-1) (S-2) (S-3), question 2\n")
    status, stdout, err = run(monkeypatch, capsys, "punch-list", str(report), str(punch))
    assert (status, stdout, err) == (0, "0 problems\n", "")


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="poppler not on the path")
def test_pages_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, _, err = run(monkeypatch, capsys, "pages", str(tmp_path / "none.pdf"))
    assert status == 1 and err.startswith("design-review pages: pdfinfo ")


def test_evidence_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "W.kicad_pcb", '(kicad_pcb (layers (0 "F.Cu" signal)))')
    out = tmp_path / "out"
    status, stdout, _ = run(
        monkeypatch, capsys, "evidence", str(tmp_path), "W", str(out), "--dry-run"
    )
    assert status == 0 and stdout.startswith("kicad-cli sch erc ")
    assert not out.exists()


def test_evidence_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed export makes the exit status 1."""
    write(tmp_path / "W.kicad_pcb", '(kicad_pcb (layers (0 "F.Cu" signal)))')
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    status, _, err = run(monkeypatch, capsys, "evidence", str(tmp_path), "W", str(tmp_path / "o"))
    assert status == 1 and "not on the path" in err
