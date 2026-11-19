import os
from pathlib import Path

import pytest

from ee_tooling.atomic import write_bytes


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
