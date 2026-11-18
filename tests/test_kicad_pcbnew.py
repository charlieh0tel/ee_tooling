import tomllib
from pathlib import Path

import pytest

from ee_tooling import kicad_pcbnew

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def fake_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> None:
    """Make SYSTEM_PYTHON a shell script."""
    python = tmp_path / "python3"
    python.write_text(f"#!/bin/sh\n{script}\n")
    python.chmod(0o755)
    monkeypatch.setattr(kicad_pcbnew, "SYSTEM_PYTHON", str(python))


def test_min_python_is_requires_python() -> None:
    requires = tomllib.loads(PYPROJECT.read_text())["project"]["requires-python"]
    assert requires == f">={kicad_pcbnew.version(kicad_pcbnew.MIN_PYTHON)}"


def test_system_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_python(monkeypatch, tmp_path, "echo 3 10")
    assert kicad_pcbnew.system_python() == (3, 10)


def test_system_python_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_python(monkeypatch, tmp_path, "exit 1")
    assert kicad_pcbnew.system_python() is None
    assert "python3" in capsys.readouterr().err


def test_missing_too_old(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_python(monkeypatch, tmp_path, "echo 3 10")
    message = kicad_pcbnew.missing()
    assert "is 3.10, and ee_tooling needs 3.12 or later" in message
    assert "cannot run here" in message


def test_missing_new_enough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_python(monkeypatch, tmp_path, "echo 3 12")
    assert "make the venv with uv venv --system-site-packages" in kicad_pcbnew.missing()


def test_missing_no_system_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(kicad_pcbnew, "SYSTEM_PYTHON", str(tmp_path / "none"))
    assert "there is no" in kicad_pcbnew.missing()
    assert "none" in capsys.readouterr().err
