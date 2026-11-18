import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from ee_tooling import adopt
from ee_tooling.adopt import (
    AGENTS_BEGIN,
    AGENTS_END,
    Command,
    File,
    Layout,
    Owner,
    Remove,
    Rename,
    Step,
    agents_text,
    apply,
    fab_config,
    locate,
    plan,
    projects,
    stale,
)
from ee_tooling.fab_release import load_config

SUBMODULE = Path("hardware/ee_tooling")


@pytest.fixture(autouse=True)
def supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system Python that can run pcbnew, whatever this machine's is."""
    monkeypatch.setattr(adopt, "unsupported", lambda: None)


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


def commands(steps: Sequence[Step]) -> list[tuple[str, ...]]:
    return [s.args for s in steps if isinstance(s, Command)]


def written(steps: Sequence[Step]) -> dict[str, Owner]:
    return {s.path.as_posix(): s.owner for s in steps if isinstance(s, File)}


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


def test_fab_config(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main", "jig")
    (tmp_path / "hardware/ee_tooling/x").mkdir(parents=True)
    (tmp_path / "hardware/ee_tooling/x/x.kicad_pro").write_text("{}")
    config = fab_config(layout)
    assert config is not None
    assert '[[board]]\nname = "jig"\ndirectory = "hardware/jig"\n' in config
    assert 'directory = "hardware/jig"\nproduction = "production/jig"\n' in config
    assert "copper" not in config
    assert 'production = "production/main"' in config
    assert config.count('profile = "jlcpcb"\n') == 2
    assert list(load_config(config)) == ["jig", "main"]
    assert '"x"' not in config


def test_fab_config_one_board(tmp_path: Path) -> None:
    config = fab_config(repository(tmp_path, "main"))
    assert config is not None
    assert 'production = "production"\n' in config


def test_fab_config_no_project(tmp_path: Path) -> None:
    assert fab_config(repository(tmp_path)) is None


def test_root_layout(tmp_path: Path) -> None:
    """The submodule at ee_tooling/ and the KiCad project at the repository root."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "main.kicad_pro").write_text("{}")
    (tmp_path / "main.kicad_pcb").write_text(PCB)
    (tmp_path / "ee_tooling/x").mkdir(parents=True)
    (tmp_path / "ee_tooling/x/x.kicad_pro").write_text("{}")
    layout = Layout(tmp_path, Path("ee_tooling"))
    config = fab_config(layout)
    assert config is not None
    assert '[[board]]\nname = "main"\ndirectory = "."\n' in config
    assert '"x"' not in config
    steps = plan(layout)
    assert commands(steps) == [
        ("uv", "venv", "--system-site-packages", "--python", "/usr/bin/python3"),
        ("uv", "add", "--dev", "--editable", "--no-workspace", "./ee_tooling"),
        ("git", "config", "core.hooksPath", ".githooks"),
    ]
    assert [s.directory for s in steps if isinstance(s, Command)] == [Path()] * 3
    assert written(steps) == {
        ".githooks/commit-msg": Owner.MANAGED,
        ".github/workflows/ee-tooling.yml": Owner.MANAGED,
        ".claude/skills/fab-release/SKILL.md": Owner.PROJECT,
        "pyproject.toml": Owner.PROJECT,
        "AGENTS.md": Owner.MANAGED,
        "fab.toml": Owner.PROJECT,
    }
    apply(tmp_path, steps, lambda *_: None)
    assert (
        "HOOK=ee_tooling/.githooks/commit-msg\n" in (tmp_path / ".githooks/commit-msg").read_text()
    )
    workflow = (tmp_path / ".github/workflows/ee-tooling.yml").read_text()
    assert "run: ee_tooling/adopt --check" in workflow
    assert "@ee_tooling/docs/kicad-style.md" in (tmp_path / "AGENTS.md").read_text()
    assert stale(layout) == []


def test_plan_fresh_repository(tmp_path: Path) -> None:
    steps = plan(repository(tmp_path, "main"))
    assert commands(steps) == [
        ("uv", "venv", "--system-site-packages", "--python", "/usr/bin/python3"),
        ("uv", "add", "--dev", "--editable", "--no-workspace", "./ee_tooling"),
        ("git", "config", "core.hooksPath", ".githooks"),
    ]
    assert written(steps) == {
        ".githooks/commit-msg": Owner.MANAGED,
        ".github/workflows/ee-tooling.yml": Owner.MANAGED,
        ".claude/skills/fab-release/SKILL.md": Owner.PROJECT,
        "hardware/pyproject.toml": Owner.PROJECT,
        "AGENTS.md": Owner.MANAGED,
        "fab.toml": Owner.PROJECT,
    }


def ran(tmp_path: Path) -> list[tuple[str, ...]]:
    """Adopt with every command recorded, not run; returns the commands."""
    layout = repository(tmp_path, "main")
    calls: list[tuple[str, ...]] = []
    apply(tmp_path, plan(layout), lambda args, _: calls.append(tuple(args)))
    return calls


def test_apply_writes_files(tmp_path: Path) -> None:
    ran(tmp_path)
    hook = tmp_path / ".githooks/commit-msg"
    assert hook.stat().st_mode & 0o111
    assert "HOOK=hardware/ee_tooling/.githooks/commit-msg\n" in hook.read_text()
    assert "@" not in hook.read_text().replace('"$@"', "")
    workflow = (tmp_path / ".github/workflows/ee-tooling.yml").read_text()
    assert "sh hardware/ee_tooling/.githooks/commit-msg --messages" in workflow
    assert "run: hardware/ee_tooling/adopt --check" in workflow
    agents = (tmp_path / "AGENTS.md").read_text()
    assert "\n@hardware/ee_tooling/docs/kicad-style.md\n" in agents
    assert "git submodule update --remote hardware/ee_tooling" in agents
    assert "`hardware/ee_tooling/adopt --yes`" in agents
    assert (
        f'name = "{tmp_path.name}-hardware"' in (tmp_path / "hardware/pyproject.toml").read_text()
    )


def test_project_files_not_overwritten(tmp_path: Path) -> None:
    ran(tmp_path)
    (tmp_path / "fab.toml").write_text("# edited\n")
    steps = plan(Layout(tmp_path, SUBMODULE))
    assert "fab.toml" not in written(steps)


def test_managed_files_updated(tmp_path: Path) -> None:
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    assert stale(layout) == []
    (tmp_path / ".githooks/commit-msg").write_text("#!/bin/sh\n")
    (tmp_path / "AGENTS.md").write_text("# Mine\n\n" + (tmp_path / "AGENTS.md").read_text())
    assert stale(layout) == [Path(".githooks/commit-msg")]
    assert written(plan(layout)) == {".githooks/commit-msg": Owner.MANAGED}


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
    assert plan(layout) == []


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
    ) in commands(plan(layout))


def test_venv_not_on_an_old_system_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(adopt, "unsupported", lambda: "/usr/bin/python3 is 3.10")
    assert not [c for c in commands(plan(repository(tmp_path, "main"))) if c[:2] == ("uv", "venv")]
    err = capsys.readouterr().err
    assert "/usr/bin/python3 is 3.10: no venv on the system Python" in err
    assert "stitch and placement-score cannot run here" in err


# A git identity for the test's commits: CI's runner has none.
IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.com")


def git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *IDENTITY, *args],
        check=True,
        capture_output=True,
    )


def test_locate_from_submodule(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    git("init", "-q", "-b", "main", str(upstream))
    git("-C", str(upstream), "commit", "-q", "--allow-empty", "-m", "x")
    board = tmp_path / "board"
    git("init", "-q", str(board))
    git(
        "-C",
        str(board),
        "submodule",
        "add",
        "-q",
        "-b",
        "main",
        str(upstream),
        "hardware/ee_tooling",
    )
    layout = locate(board / "hardware/ee_tooling")
    assert layout.root == board.resolve()
    assert layout.submodule == Path("hardware/ee_tooling")
    assert layout.hardware == Path("hardware")


def test_locate_not_a_submodule(tmp_path: Path) -> None:
    git("init", "-q", str(tmp_path))
    with pytest.raises(ValueError, match="not a submodule"):
        locate(tmp_path)


# A jlcpcb.toml as jlcpcb-release read it, with a comment and a key
# fab.toml does not have.
OLD_CONFIG = """\
# my boards
[[board]]
name = "main"
directory = "hardware/main"
copper = ["F.Cu", "B.Cu"]
production = "production/main"
sources = ["docs"]
colour = "red"

[[board]]
name = "jig"
directory = "hardware/jig"
copper = ["F.Cu", "B.Cu"]
production = "production/jig"
"""


def adopted(tmp_path: Path, *projects: str) -> Layout:
    """A repository adopted before fab.toml: jlcpcb.toml and the old skill."""
    layout = repository(tmp_path, *projects)
    (tmp_path / "jlcpcb.toml").write_text(OLD_CONFIG)
    skill = tmp_path / ".claude/skills/jlcpcb-release"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: jlcpcb-release\n---\nuv run jlcpcb-release; see jlcpcb.toml\n"
    )
    (skill / "notes.txt").write_text("jlcpcb-release\n")
    return layout


def test_migrate_several_boards(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """jlcpcb.toml becomes fab.toml, its boards and keys kept, each with
    profile jlcpcb; its comments and unknown keys are not carried over, each
    key named; the old skill moves, its names renamed in its .md files."""
    layout = adopted(tmp_path, "main", "jig")
    steps = plan(layout)
    assert written(steps)["fab.toml"] is Owner.PROJECT
    assert ".claude/skills/fab-release/SKILL.md" not in written(steps)
    assert [s for s in steps if isinstance(s, Remove | Rename)] == [
        Remove(Path("jlcpcb.toml"), "migrated to fab.toml"),
        Rename(
            Path(".claude/skills/jlcpcb-release"),
            Path(".claude/skills/fab-release"),
            (("jlcpcb-release", "fab-release"), ("jlcpcb.toml", "fab.toml")),
        ),
    ]
    err = capsys.readouterr().err
    assert "'colour' not carried over to fab.toml\n" in err
    assert (
        "[[board]] 'jig': 'copper' not carried over to fab.toml: the copper layers come from"
        " the board's stackup\n"
    ) in err
    assert {Path("jlcpcb.toml"), Path(".claude/skills/jlcpcb-release")} <= set(stale(layout))
    apply(tmp_path, steps, lambda *_: None)
    assert not (tmp_path / "jlcpcb.toml").exists()
    text = (tmp_path / "fab.toml").read_text()
    assert "# my boards" not in text
    assert "colour" not in text
    boards = load_config(text)
    assert list(boards) == ["main", "jig"]
    assert boards["main"].sources == (Path("docs"),)
    assert boards["main"].production == Path("production/main")
    assert {b.profile for b in boards.values()} == {"jlcpcb"}
    skill = tmp_path / ".claude/skills/fab-release"
    assert not (tmp_path / ".claude/skills/jlcpcb-release").exists()
    assert (skill / "SKILL.md").read_text() == (
        "---\nname: fab-release\n---\nuv run fab-release; see fab.toml\n"
    )
    assert (skill / "notes.txt").read_text() == "jlcpcb-release\n"
    assert stale(layout) == []
    assert [s for s in plan(layout) if not isinstance(s, Command)] == []


def test_migrate_one_board(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "jlcpcb.toml").write_text(OLD_CONFIG.split("\n\n")[0] + "\n")
    apply(tmp_path, plan(layout), lambda *_: None)
    boards = load_config((tmp_path / "fab.toml").read_text())
    assert list(boards) == ["main"]
    assert boards["main"].directory == Path("hardware/main")
    assert (tmp_path / ".claude/skills/fab-release/SKILL.md").exists()


def test_migrate_root_layout(tmp_path: Path) -> None:
    """The KiCad project at the root, directory "."."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "main.kicad_pro").write_text("{}")
    (tmp_path / "main.kicad_pcb").write_text(PCB)
    (tmp_path / "jlcpcb.toml").write_text(
        '[[board]]\nname = "main"\ndirectory = "."\ncopper = ["F.Cu", "B.Cu"]\n'
        'production = "production"\n'
    )
    layout = Layout(tmp_path, Path("ee_tooling"))
    apply(tmp_path, plan(layout), lambda *_: None)
    assert load_config((tmp_path / "fab.toml").read_text())["main"].directory == Path(".")
    assert not (tmp_path / "jlcpcb.toml").exists()


def test_already_migrated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """fab.toml and the new skill there: nothing migrated, an old file left
    beside them named on stderr, and --check passes."""
    layout = adopted(tmp_path, "main")
    apply(tmp_path, plan(layout), lambda *_: None)
    capsys.readouterr()
    (tmp_path / "jlcpcb.toml").write_text(OLD_CONFIG)
    steps = plan(layout)
    assert [s for s in steps if not isinstance(s, Command)] == []
    assert "jlcpcb.toml and fab.toml: jlcpcb.toml is not read" in capsys.readouterr().err
    assert stale(layout) == []


def test_neither_config(tmp_path: Path) -> None:
    """No jlcpcb.toml and no fab.toml: fab.toml is written from the
    projects, nothing removed."""
    steps = plan(repository(tmp_path, "main"))
    assert written(steps)["fab.toml"] is Owner.PROJECT
    assert not [s for s in steps if isinstance(s, Remove | Rename)]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[[board\n", "jlcpcb.toml: .*fix it, or write fab.toml by hand"),
        ('[[board]]\nname = "x"\n', r"jlcpcb.toml: \[\[board\]\] 'x': no 'directory'"),
        ("board = 1\n", r"jlcpcb.toml: no \[\[board\]\]"),
    ],
)
def test_migrate_refuses_a_bad_config(tmp_path: Path, text: str, error: str) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "jlcpcb.toml").write_text(text)
    with pytest.raises(ValueError, match=error):
        plan(layout)


def test_projects_skip_production_and_hidden(tmp_path: Path) -> None:
    """A project in a production directory (adopt's, or one a config
    names), or a hidden directory, is not a board."""
    layout = repository(tmp_path, "main")
    copies = [
        "hardware/.claude/worktrees/w/hardware/main",
        "hardware/main/.history/main",
        "production/20261118-1/main",
        "hardware/releases/x/main",
    ]
    for c in copies:
        (tmp_path / c).mkdir(parents=True)
        (tmp_path / c / "main.kicad_pro").write_text("{}")
    assert projects(layout) == [
        tmp_path / "hardware/main/main.kicad_pro",
        tmp_path / "hardware/releases/x/main/main.kicad_pro",
    ]
    (tmp_path / "jlcpcb.toml").write_text(
        '[[board]]\nname = "main"\nproduction = "hardware/releases"\n'
    )
    assert projects(layout) == [tmp_path / "hardware/main/main.kicad_pro"]
