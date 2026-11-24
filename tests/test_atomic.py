import io
import os
from pathlib import Path

import pytest

from ee_tooling.atomic import LEFTOVER, write_bytes


def test_new_and_replaced(tmp_path: Path) -> None:
    target = tmp_path / "f"
    write_bytes(target, b"one\n")
    target.chmod(0o640)
    write_bytes(target, b"two\n")
    assert target.read_bytes() == b"two\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_symbolic_link_written_through(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"old\n")
    link = tmp_path / "link"
    link.symlink_to("real")
    write_bytes(link, b"new\n")
    assert link.is_symlink()
    assert real.read_bytes() == b"new\n"


def test_hard_link_written_in_place(tmp_path: Path) -> None:
    """Every name keeps sharing the file."""
    first = tmp_path / "CLAUDE.md"
    first.write_bytes(b"old\n")
    second = tmp_path / "AGENTS.md"
    os.link(first, second)
    write_bytes(second, b"new, longer\n")
    assert first.read_bytes() == second.read_bytes() == b"new, longer\n"
    assert second.stat().st_nlink == 2
    write_bytes(second, b"short\n")
    assert first.read_bytes() == b"short\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AGENTS.md", "CLAUDE.md"]


def test_failed_write_leaves_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"old\n")

    def full(*_: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", full)
    with pytest.raises(OSError):
        write_bytes(target, b"new\n")
    assert target.read_bytes() == b"old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_failed_write_in_place_keeps_the_new_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hard-linked file whose write in place fails midway: the scratch
    copy, the only whole new text, is kept and named."""
    first = tmp_path / "CLAUDE.md"
    first.write_bytes(b"old text\n")
    second = tmp_path / "AGENTS.md"
    os.link(first, second)

    def full(self: Path, *_: object) -> None:
        with io.FileIO(self, "r+") as f:
            f.write(b"ne")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "open", full)
    with pytest.raises(OSError) as raised:
        write_bytes(second, b"new\n")
    monkeypatch.undo()
    scratch = tmp_path / f".AGENTS.md.{os.getpid()}.ee_tooling-tmp"
    assert scratch.read_bytes() == b"new\n"
    assert raised.value.filename == str(second)
    assert raised.value.strerror == (
        f"No space left on device; written in place, maybe in part: its whole new text is in"
        f" {scratch}"
    )
    assert first.read_bytes() == b"ned text\n"
    assert LEFTOVER.match(scratch.name)


def test_failed_scratch_write_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that fails before the hard-linked file is touched leaves no
    scratch file."""
    first = tmp_path / "a"
    first.write_bytes(b"old\n")
    os.link(first, tmp_path / "b")

    def gone(*_: object) -> bool:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(Path, "exists", gone)
    with pytest.raises(OSError):
        write_bytes(first, b"new\n")
    monkeypatch.undo()
    assert first.read_bytes() == b"old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b"]
