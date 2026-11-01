import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from ee_tooling.adopt import (
    AGENTS_BEGIN,
    AGENTS_END,
    URL,
    Command,
    File,
    Layout,
    Owner,
    agents_text,
    apply,
    copper_layers,
    jlcpcb_config,
    plan,
    stale,
)

SUBMODULE = Path("hardware/ee_tooling")

PCB = """(kicad_pcb
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(4 "In1.Cu" signal)
\t\t(6 "In2.Cu" signal)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(25 "Edge.Cuts" user)
\t)
\t(net 0 "")
)
"""


def repository(tmp_path: Path, *projects: str) -> Layout:
    """A git repository with a KiCad project per name under hardware/."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in projects:
        d = tmp_path / "hardware" / name
        d.mkdir(parents=True)
        (d / f"{name}.kicad_pro").write_text("{}")
        (d / f"{name}.kicad_pcb").write_text(PCB)
    return Layout(tmp_path, SUBMODULE)


def commands(steps: Sequence[File | Command]) -> list[tuple[str, ...]]:
    return [s.args for s in steps if isinstance(s, Command)]


def written(steps: Sequence[File | Command]) -> dict[str, Owner]:
    return {s.path.as_posix(): s.owner for s in steps if isinstance(s, File)}


def test_copper_layers_top_to_bottom() -> None:
    assert copper_layers(PCB) == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


def test_copper_layers_none() -> None:
    assert copper_layers('(kicad_pcb (net 0 ""))') == []


def test_agents_new() -> None:
    text = agents_text(None, "## ee_tooling\n")
    assert text == f"{AGENTS_BEGIN}\n## ee_tooling\n{AGENTS_END}\n"


def test_agents_appended_to_own_text() -> None:
    text = agents_text("# Agents\n\nOwn rules.\n", "## ee_tooling\n")
    assert text == f"# Agents\n\nOwn rules.\n\n{AGENTS_BEGIN}\n## ee_tooling\n{AGENTS_END}\n"


def test_agents_section_replaced_in_place() -> None:
    old = f"# Agents\n\n{AGENTS_BEGIN}\nold\n{AGENTS_END}\n\n## After\n"
    assert agents_text(old, "new\n") == f"# Agents\n\n{AGENTS_BEGIN}\nnew\n{AGENTS_END}\n## After\n"


def test_agents_adopted_by_hand_left_alone() -> None:
    assert agents_text("@hardware/ee_tooling/docs/kicad-style.md\n", "new\n") is None


def test_jlcpcb_config(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main", "jig")
    (tmp_path / "hardware/ee_tooling/x").mkdir(parents=True)
    (tmp_path / "hardware/ee_tooling/x/x.kicad_pro").write_text("{}")
    config = jlcpcb_config(layout)
    assert config is not None
    assert '[[board]]\nname = "jig"\ndirectory = "hardware/jig"\n' in config
    assert 'copper = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]\nproduction = "production"\n' in config
    assert 'production = "production/main"' in config
    assert '"x"' not in config


def test_jlcpcb_config_no_project(tmp_path: Path) -> None:
    assert jlcpcb_config(repository(tmp_path)) is None


def test_plan_fresh_repository(tmp_path: Path) -> None:
    steps = plan(repository(tmp_path, "main"), URL)
    assert commands(steps) == [
        ("git", "submodule", "add", "-b", "main", URL, "hardware/ee_tooling"),
        ("uv", "venv", "--system-site-packages", "--python", "/usr/bin/python3"),
        ("uv", "add", "--dev", "--editable", "--no-workspace", "./ee_tooling"),
        ("git", "config", "core.hooksPath", ".githooks"),
    ]
    assert written(steps) == {
        ".githooks/commit-msg": Owner.MANAGED,
        ".github/workflows/ee-tooling.yml": Owner.MANAGED,
        ".claude/skills/jlcpcb-release/SKILL.md": Owner.PROJECT,
        "hardware/pyproject.toml": Owner.PROJECT,
        "AGENTS.md": Owner.MANAGED,
        "jlcpcb.toml": Owner.PROJECT,
    }


def ran(tmp_path: Path) -> list[tuple[str, ...]]:
    """Adopt with every command recorded, not run; returns the commands."""
    layout = repository(tmp_path, "main")
    calls: list[tuple[str, ...]] = []
    apply(tmp_path, plan(layout, URL), lambda args, _: calls.append(tuple(args)))
    return calls


def test_apply_writes_files(tmp_path: Path) -> None:
    ran(tmp_path)
    hook = tmp_path / ".githooks/commit-msg"
    assert hook.stat().st_mode & 0o111
    assert "HOOK=hardware/ee_tooling/.githooks/commit-msg\n" in hook.read_text()
    assert "@" not in hook.read_text().replace('"$@"', "")
    workflow = (tmp_path / ".github/workflows/ee-tooling.yml").read_text()
    assert "sh hardware/ee_tooling/.githooks/commit-msg --messages" in workflow
    assert "uv run --directory hardware ee-tooling-adopt --check" in workflow
    agents = (tmp_path / "AGENTS.md").read_text()
    assert "\n@hardware/ee_tooling/docs/kicad-style.md\n" in agents
    assert "git submodule update --remote hardware/ee_tooling" in agents
    assert (
        f'name = "{tmp_path.name}-hardware"' in (tmp_path / "hardware/pyproject.toml").read_text()
    )


def test_project_files_not_overwritten(tmp_path: Path) -> None:
    ran(tmp_path)
    (tmp_path / "jlcpcb.toml").write_text("# edited\n")
    steps = plan(Layout(tmp_path, SUBMODULE), URL)
    assert "jlcpcb.toml" not in written(steps)


def test_managed_files_updated(tmp_path: Path) -> None:
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    assert stale(layout) == []
    (tmp_path / ".githooks/commit-msg").write_text("#!/bin/sh\n")
    (tmp_path / "AGENTS.md").write_text("# Mine\n\n" + (tmp_path / "AGENTS.md").read_text())
    assert stale(layout) == [Path(".githooks/commit-msg")]
    assert written(plan(layout, URL)) == {".githooks/commit-msg": Owner.MANAGED}


def test_rerun_has_nothing_to_do(tmp_path: Path) -> None:
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    (tmp_path / "hardware/ee_tooling/.git").mkdir(parents=True)
    (tmp_path / "hardware/.venv").mkdir()
    (tmp_path / "hardware/.venv/pyvenv.cfg").write_text("include-system-site-packages = true\n")
    pyproject = tmp_path / "hardware/pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + '\n[tool.uv.sources]\nee-tooling = { path = "ee_tooling" }\n'
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "core.hooksPath", ".githooks"], check=True
    )
    assert plan(layout, URL) == []


def test_venv_without_system_packages_remade(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "hardware/.venv").mkdir(parents=True)
    (tmp_path / "hardware/.venv/pyvenv.cfg").write_text("include-system-site-packages = false\n")
    assert (
        "uv",
        "venv",
        "--clear",
        "--system-site-packages",
        "--python",
        "/usr/bin/python3",
    ) in commands(plan(layout, URL))


@pytest.mark.parametrize("registered", [True, False])
def test_submodule_init_or_add(tmp_path: Path, registered: bool) -> None:
    layout = repository(tmp_path, "main")
    if registered:
        (tmp_path / ".gitmodules").write_text(
            f'[submodule "hardware/ee_tooling"]\n\tpath = hardware/ee_tooling\n\turl = {URL}\n'
        )
    first = commands(plan(layout, URL))[0]
    if registered:
        assert first == ("git", "submodule", "update", "--init", "hardware/ee_tooling")
    else:
        assert first[:3] == ("git", "submodule", "add")
