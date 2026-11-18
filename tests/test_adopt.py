import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from ee_tooling import adopt
from ee_tooling.adopt import (
    AGENTS_BEGIN,
    AGENTS_END,
    AGENTS_MARKERS,
    LFS_INSTALL,
    LFS_PULL,
    Command,
    Edit,
    File,
    Layout,
    Markers,
    Owner,
    Remove,
    Rename,
    Step,
    agents_text,
    apply,
    describe,
    fab_added,
    fab_config,
    has_comment,
    lfs_missing,
    lfs_unfetched,
    locate,
    marked_text,
    plain_blobs,
    plan,
    projects,
    stale,
)
from ee_tooling.fab_release import load_config
from ee_tooling.kicad_rules import ERC_SETTINGS, erc_set, installed, resource
from ee_tooling.profiles.jlcpcb import Jlcpcb, Options

SUBMODULE = Path("hardware/ee_tooling")


@pytest.fixture(autouse=True)
def supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system Python that can run pcbnew, whatever this machine's is."""
    monkeypatch.setattr(adopt, "unsupported", lambda: None)


@pytest.fixture(autouse=True)
def git_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty global git config, and no system one: neither the machine's
    nor the user's (git-lfs's filters among them) is read, and none is
    written.  Returns the global config's path."""
    path = tmp_path_factory.mktemp("home") / "gitconfig"
    path.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return path


# The tests that run git-lfs; CI's kicad job has it and fails if they skip.
needs_lfs = pytest.mark.skipif(shutil.which("git-lfs") is None, reason="no git-lfs on the path")


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


def test_agents_section_with_the_style_replaced() -> None:
    """The house style imported inside the markers is ee_tooling's section."""
    old = f"{AGENTS_BEGIN}\n@x/docs/kicad-style.md\n{AGENTS_END}\n"
    assert agents_text(old, "new\n") == f"{AGENTS_BEGIN}\nnew\n{AGENTS_END}\n"


def test_agents_markers_out_of_order_appended() -> None:
    old = f"{AGENTS_END}\nown\n{AGENTS_BEGIN}\n"
    assert agents_text(old, "new\n") == f"{old}\n{AGENTS_BEGIN}\nnew\n{AGENTS_END}\n"


# A section that goes first, as the project's lines after it win.
FIRST = Markers("# begin", "# end", first=True)


@pytest.mark.parametrize(
    ("existing", "text"),
    [
        (None, "# begin\nnew\n# end\n"),
        ("own\n", "# begin\nnew\n# end\nown\n"),
        ("\n\nown\n", "# begin\nnew\n# end\nown\n"),
        ("# begin\nold\n# end\nown\n", "# begin\nnew\n# end\nown\n"),
        ("mine\n# begin\nold\n# end\n\nown\n", "mine\n# begin\nnew\n# end\nown\n"),
        ("# end\nown\n", "# begin\nnew\n# end\n# end\nown\n"),
    ],
)
def test_marked_text_first(existing: str | None, text: str) -> None:
    """Prepended to a file without the section, replaced in place in one
    with it; a rerun changes nothing."""
    assert marked_text(existing, "new\n", FIRST) == text
    assert marked_text(text, "new\n", FIRST) == text


@pytest.mark.parametrize(
    "existing", [None, "own\n", f"# Agents\n\n{AGENTS_BEGIN}\nold\n{AGENTS_END}\n"]
)
def test_marked_text_rerun_changes_nothing(existing: str | None) -> None:
    text = marked_text(existing, "new\n", AGENTS_MARKERS)
    assert marked_text(text, "new\n", AGENTS_MARKERS) == text


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
        LFS_INSTALL,
    ]
    assert [s.directory for s in steps if isinstance(s, Command)] == [Path()] * 4
    assert written(steps) == {
        ".githooks/commit-msg": Owner.MANAGED,
        ".githooks/pre-push": Owner.MANAGED,
        ".gitattributes": Owner.MANAGED,
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
    assert "(ee_tooling/docs/adopting.md)" in (tmp_path / ".githooks/pre-push").read_text()
    assert stale(layout) == []


def test_plan_fresh_repository(tmp_path: Path) -> None:
    steps = plan(repository(tmp_path, "main"))
    assert commands(steps) == [
        ("uv", "venv", "--system-site-packages", "--python", "/usr/bin/python3"),
        ("uv", "add", "--dev", "--editable", "--no-workspace", "./ee_tooling"),
        ("git", "config", "core.hooksPath", ".githooks"),
        LFS_INSTALL,
    ]
    assert written(steps) == {
        ".githooks/commit-msg": Owner.MANAGED,
        ".githooks/pre-push": Owner.MANAGED,
        ".gitattributes": Owner.MANAGED,
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
    assert (
        "git lfs version\n          || { sudo apt-get update && sudo apt-get install -y"
        " --no-install-recommends git-lfs; }\n" in workflow
    )
    assert "        lfs: true" not in workflow
    agents = (tmp_path / "AGENTS.md").read_text()
    assert "\n@hardware/ee_tooling/docs/kicad-style.md\n" in agents
    assert "git submodule update --remote hardware/ee_tooling" in agents
    assert "`hardware/ee_tooling/adopt --yes`" in agents
    hook = tmp_path / ".githooks/pre-push"
    assert hook.stat().st_mode & 0o111
    assert "@" not in hook.read_text().replace('"$@"', "")
    assert "(hardware/ee_tooling/docs/adopting.md)" in hook.read_text()
    assert (tmp_path / ".gitattributes").read_text() == GITATTRIBUTES
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
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "filter.lfs.process", "git-lfs filter-process"],
        check=True,
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


# The .gitattributes adopt writes in a repository without one.
GITATTRIBUTES = """\
# ee_tooling: begin; written by ee-tooling-adopt
*.[zZ][iI][pP] filter=lfs diff=lfs merge=lfs -text
*.[tT][gG][zZ] filter=lfs diff=lfs merge=lfs -text
*.[pP][dD][fF] filter=lfs diff=lfs merge=lfs -text
*.[sS][tT][eE][pP] filter=lfs diff=lfs merge=lfs -text
*.[sS][tT][pP] filter=lfs diff=lfs merge=lfs -text
*.[wW][rR][lL] filter=lfs diff=lfs merge=lfs -text
# ee_tooling: end; the project's own lines follow and win
"""


def test_gitattributes_section_first(tmp_path: Path) -> None:
    """The section goes before the project's own lines, which are kept and
    win; a section changed by hand is stale and rewritten in place."""
    layout = repository(tmp_path, "main")
    own = "*.pdf -filter -diff -merge text\n*.png filter=lfs diff=lfs merge=lfs -text\n"
    (tmp_path / ".gitattributes").write_text(own)
    apply(tmp_path, plan(layout), lambda *_: None)
    assert (tmp_path / ".gitattributes").read_text() == GITATTRIBUTES + own
    assert stale(layout) == []
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES.replace("*.[tT][gG][zZ]", "*.tgz") + own)
    assert stale(layout) == [Path(".gitattributes")]
    apply(tmp_path, plan(layout), lambda *_: None)
    assert (tmp_path / ".gitattributes").read_text() == GITATTRIBUTES + own


@pytest.mark.parametrize(
    "name", ["a.zip", "A.ZIP", "s.tgz", "d.pdf", "D.Pdf", "m.step", "M.STEP", "m.stp", "m.WRL"]
)
def test_gitattributes_tracked(tmp_path: Path, name: str) -> None:
    """git's own reading of the section: each type, in any case, in any
    directory, through Git LFS."""
    git("init", "-q", str(tmp_path))
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES)
    for path in (name, f"production/20261118-1/{name}"):
        out = subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "check-attr",
                "filter",
                "diff",
                "merge",
                "text",
                "--",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert (
            out
            == f"{path}: filter: lfs\n{path}: diff: lfs\n{path}: merge: lfs\n{path}: text: unset\n"
        )


@pytest.mark.parametrize(
    "name",
    [
        "b.kicad_pcb",
        "b.kicad_sch",
        "b.kicad_pro",
        "b.kicad_dru",
        "f.kicad_mod",
        "s.kicad_sym",
        "w.kicad_wks",
        "b-1-bom.csv",
        "b-1-stackup.txt",
        "fab.toml",
        "README.md",
        "photo.png",
        "a.zip.txt",
    ],
)
def test_gitattributes_plain(tmp_path: Path, name: str) -> None:
    git("init", "-q", str(tmp_path))
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES)
    out = subprocess.run(
        ["git", "-C", str(tmp_path), "check-attr", "filter", name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out == f"{name}: filter: unspecified\n"


def test_project_pre_push_replaced_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-push adopt did not write is named on stderr with its text, by
    every mode, and --yes replaces it."""
    layout = repository(tmp_path, "main")
    (tmp_path / ".githooks").mkdir()
    own = "#!/bin/sh\nmake lint\n"
    (tmp_path / ".githooks/pre-push").write_text(own)
    assert Path(".githooks/pre-push") in stale(layout)
    warning = (
        "WARNING: .githooks/pre-push is the project's own, not ee_tooling's: --yes replaces it"
        " with ee_tooling's, which uploads Git LFS objects (.githooks is ee_tooling's); move what"
        f" it does elsewhere.  Its text:\n{own}\n"
    )
    assert capsys.readouterr().err == warning
    steps = plan(layout)
    assert written(steps)[".githooks/pre-push"] is Owner.MANAGED
    assert capsys.readouterr().err == warning
    apply(tmp_path, steps, lambda *_: None)
    assert "git lfs pre-push" in (tmp_path / ".githooks/pre-push").read_text()
    assert stale(layout) == []
    assert capsys.readouterr().err == ""


def test_stale_pre_push_of_ours_replaced_quietly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    hook = tmp_path / ".githooks/pre-push"
    hook.write_text(hook.read_text().replace("exit 2", "exit 3"))
    capsys.readouterr()
    assert stale(layout) == [Path(".githooks/pre-push")]
    assert "WARNING" not in capsys.readouterr().err


def run_pre_push(tmp_path: Path, path: str) -> subprocess.CompletedProcess[str]:
    """The pre-push adopt writes, run with PATH as path, the push's refs on
    its stdin."""
    ran(tmp_path)
    return subprocess.run(
        ["/bin/sh", str(tmp_path / ".githooks/pre-push"), "origin", "file:///x"],
        input="refs/heads/main 1 refs/heads/main 0\n",
        env={"PATH": path},
        capture_output=True,
        text=True,
        check=False,
    )


def test_pre_push_refused_without_git_lfs(tmp_path: Path) -> None:
    empty = tmp_path / "bin"
    empty.mkdir()
    r = run_pre_push(tmp_path, str(empty))
    assert r.returncode == 2
    assert r.stderr == (
        "push refused: git-lfs is not installed; install the distribution's git-lfs package"
        " (hardware/ee_tooling/docs/adopting.md)\n"
    )


def fake_bin(tmp_path: Path, git_status: int) -> str:
    """A PATH with a git-lfs and a git that logs its arguments and its
    stdin to git.log and exits git_status: nothing else, so the shell's
    builtins only."""
    directory = tmp_path / "fake-bin"
    directory.mkdir()
    (directory / "git-lfs").write_text("#!/bin/sh\n")
    log = tmp_path / "git.log"
    (directory / "git").write_text(
        f'#!/bin/sh\necho "$@" >>{log}\nwhile read -r line; do echo "$line"; done >>{log}\n'
        f"exit {git_status}\n"
    )
    for f in directory.iterdir():
        f.chmod(0o755)
    return str(directory)


def test_pre_push_runs_git_lfs(tmp_path: Path) -> None:
    """git lfs pre-push, with the hook's arguments and the push's refs."""
    r = run_pre_push(tmp_path, fake_bin(tmp_path, 0))
    assert (r.returncode, r.stderr) == (0, "")
    assert (tmp_path / "git.log").read_text() == (
        "lfs pre-push origin file:///x\nrefs/heads/main 1 refs/heads/main 0\n"
    )


def test_pre_push_refused_when_the_upload_fails(tmp_path: Path) -> None:
    """A remote without Git LFS, or out of reach: git-lfs's error, then why."""
    r = run_pre_push(tmp_path, fake_bin(tmp_path, 2))
    assert r.returncode == 2
    assert r.stderr == (
        "push refused: git lfs pre-push could not upload the Git LFS objects (its error is"
        " above): the remote must support Git LFS and be reachable"
        " (hardware/ee_tooling/docs/adopting.md)\n"
    )


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
    assert err.count("jlcpcb.toml: its comments not carried over to fab.toml\n") == 1
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


# A fab.toml from before [board.fab]: comments, a board without it and
# one with part of it.
NO_FAB = """\
# my boards
[[board]]
name = "main"  # the main board
directory = "hardware/main"
production = "production/main"
profile = "jlcpcb"

[board.jlcpcb]
require_lcsc = true

[[board]]
name = "jig"
directory = "hardware/jig"
production = "production/jig"
profile = "jlcpcb"

[board.fab]  # ours
tg = 170
"""


def test_fab_added_keeps_the_text() -> None:
    """The keys each board lacks, with the house values, where they belong;
    every other line kept."""
    edited = fab_added(NO_FAB)
    assert edited is not None
    text, added = edited
    assert added == [
        "[[board]] 'main': ipc_class, base_material, tg, electrical_test",
        "[[board]] 'jig': ipc_class, base_material, electrical_test",
    ]
    assert text == NO_FAB.replace(
        "require_lcsc = true\n",
        'require_lcsc = true\n\n[board.fab]\nipc_class = 2\nbase_material = "FR-4"\ntg = 130\n'
        'electrical_test = "100% netlist test per IPC-9252"\n',
    ).replace(
        "[board.fab]  # ours\n",
        '[board.fab]  # ours\nipc_class = 2\nbase_material = "FR-4"\n'
        'electrical_test = "100% netlist test per IPC-9252"\n',
    )
    boards = load_config(text)
    assert boards["jig"].fab.tg == 170
    assert boards["main"].options == {"require_lcsc": True}
    assert fab_added(text) is None


def test_fab_added_at_the_end() -> None:
    text, _ = fab_added('[[board]]\nname = "b"\nprofile = "jlcpcb"\n\n\n') or ("", [])
    assert text.endswith(
        '\n\n[board.fab]\nipc_class = 2\nbase_material = "FR-4"\ntg = 130\n'
        'electrical_test = "100% netlist test per IPC-9252"\n\n\n'
    )


def test_fab_added_leaves_an_unknown_profile() -> None:
    assert fab_added('[[board]]\nname = "b"\nprofile = "pcbway"\n') is None


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (
            '[[board]]\nname = "b"\nprofile = "jlcpcb"\nfab = { tg = 150 }\n',
            r"not a \[board.fab\]",
        ),
        ('[[board]]\nname = "b"\nprofile = "jlcpcb"\nfab.tg = 150\n', r"not a \[board.fab\]"),
        ('board = [{ name = "b", profile = "jlcpcb" }]\n', r"1 boards, 0 \[\[board\]\] headers"),
        ('[[board]]\nname = "b"\nprofile = "jlcpcb"\nfab = 1\n', "'fab' is not a table"),
    ],
)
def test_fab_added_refused(text: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        fab_added(text)


def test_adopt_adds_fab_keys(tmp_path: Path) -> None:
    """--check fails on a missing [board.fab] key; --yes adds it; then
    there is nothing to do."""
    layout = repository(tmp_path, "main")
    (tmp_path / "fab.toml").write_text(NO_FAB)
    assert Path("fab.toml") in stale(layout)
    steps = [s for s in plan(layout) if isinstance(s, Edit) and s.path == Path("fab.toml")]
    assert [(s.path, s.why) for s in steps] == [
        (
            Path("fab.toml"),
            "[board.fab] keys added: [[board]] 'main': ipc_class, base_material, tg,"
            " electrical_test; [[board]] 'jig': ipc_class, base_material, electrical_test",
        )
    ]
    apply(tmp_path, steps, lambda *_: None)
    assert "# the main board" in (tmp_path / "fab.toml").read_text()
    assert Path("fab.toml") not in stale(layout)
    assert not [s for s in plan(layout) if isinstance(s, Edit) and s.path == Path("fab.toml")]


def test_adopt_stops_on_fab_it_cannot_add(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "fab.toml").write_text('[[board]]\nname = "b"\nprofile = "jlcpcb"\nfab.tg = 1\n')
    with pytest.raises(ValueError, match=r"^fab.toml: .*; add each \[board.fab\] key by hand$"):
        stale(layout)


# A board's own custom rule, and its Board Setup: one value above
# JLCPCB's minimum, one below.
OWN_RULE = '(rule "Mine"\n\t(constraint track_width (min 0.2mm)))\n'
LOW_SETUP = {
    "board": {"design_settings": {"rules": {"min_clearance": 0.05, "min_hole_clearance": 0.3}}},
    "erc": {
        "rule_severities": {
            "four_way_junction": "error",
            "endpoint_off_grid": "ignore",
            "my_own_check": "ignore",
        }
    },
    "meta": {"filename": "main.kicad_pro"},
    "schematic": {"connection_grid_size": 25.0},
}
# What the house ERC settings change in LOW_SETUP: each severity it lacks or
# has laxer, and the grid; not four_way_junction, stricter there.
ERC_CHANGES = erc_set(LOW_SETUP, json.loads(resource(ERC_SETTINGS)))[1]
SECTION = installed("jlcpcb", Jlcpcb.rules, Options())


def rules_repository(tmp_path: Path) -> Layout:
    """An adopted repository with one board, its rules not installed."""
    layout = repository(tmp_path, "main")
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    board = tmp_path / "hardware/main"
    (board / "main.kicad_pro").write_text(json.dumps(LOW_SETUP, indent=2) + "\n")
    (board / "main.kicad_dru").write_text(f"(version 1)\n\n{OWN_RULE}")
    return layout


def test_rules_installed(tmp_path: Path) -> None:
    """--check fails while the rules are not installed; --yes installs them
    before the board's own and raises Board Setup, never lowering a value;
    then there is nothing to do."""
    layout = rules_repository(tmp_path)
    board = tmp_path / "hardware/main"
    assert {Path("hardware/main/main.kicad_dru"), Path("hardware/main/main.kicad_pro")} <= set(
        stale(layout)
    )
    edits = [s for s in plan(layout) if isinstance(s, Edit)]
    assert [(s.path.as_posix(), s.why) for s in edits] == [
        (
            "hardware/main/main.kicad_dru",
            "ee_tooling's jlcpcb rules installed; close the board in KiCad first",
        ),
        (
            "hardware/main/main.kicad_pro",
            "Board Setup raised to jlcpcb's minimums:"
            " board.design_settings.rules.min_clearance: 0.05 -> 0.1;"
            " board.design_settings.rules.min_copper_edge_clearance: none -> 0.2;"
            f" ERC severities and connection grid set ({len(ERC_CHANGES)} values);"
            " close the board in KiCad first",
        ),
    ]
    apply(tmp_path, edits, lambda *_: None)
    assert (board / "main.kicad_dru").read_text() == f"(version 1)\n{SECTION}\n{OWN_RULE}"
    project = json.loads((board / "main.kicad_pro").read_text())
    assert project["board"]["design_settings"]["rules"] == {
        "min_clearance": 0.1,
        "min_hole_clearance": 0.3,
        "min_copper_edge_clearance": 0.2,
    }
    assert project["meta"] == LOW_SETUP["meta"]
    severities = project["erc"]["rule_severities"]
    assert severities["four_way_junction"] == "error"
    assert severities["endpoint_off_grid"] == "error"
    assert severities["field_name_whitespace"] == "warning"
    assert severities["my_own_check"] == "ignore"
    assert project["schematic"]["connection_grid_size"] == 50.0
    assert stale(layout) == []
    assert not [s for s in plan(layout) if isinstance(s, Edit)]


def test_rules_stale_section_replaced(tmp_path: Path) -> None:
    """An old section is replaced in place; the board's own rules kept."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    old = SECTION.replace("(min 0.1mm)", "(min 0.05mm)", 1)
    dru.write_text(f"(version 1)\n{old}\n{OWN_RULE}")
    assert Path("hardware/main/main.kicad_dru") in stale(layout)
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert dru.read_text() == f"(version 1)\n{SECTION}\n{OWN_RULE}"


def test_rules_new_dru(tmp_path: Path) -> None:
    """A board with no .kicad_dru gets one of the profile's rules."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    dru.unlink()
    edits = [s for s in plan(layout) if isinstance(s, Edit)]
    assert describe(edits[0], tmp_path).startswith("create hardware/main/main.kicad_dru (project;")
    assert describe(edits[1], tmp_path).startswith("update hardware/main/main.kicad_pro (project;")
    apply(tmp_path, edits, lambda *_: None)
    assert dru.read_text() == f"(version 1)\n{SECTION}"


def test_rules_in_one_run(tmp_path: Path) -> None:
    """A fresh repository: fab.toml written and the rules installed by the
    same --yes."""
    layout = repository(tmp_path, "main")
    apply(tmp_path, plan(layout), lambda *_: None)
    assert (tmp_path / "hardware/main/main.kicad_dru").read_text() == f"(version 1)\n{SECTION}"
    assert stale(layout) == []


def test_rules_copied_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A copy of the profile's rules among the board's own: said on stderr,
    left."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    copy = '(rule "JLCPCB track width"\n\t(constraint track_width (min 0.1mm)))\n'
    dru.write_text(f"(version 1)\n{copy}")
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert dru.read_text().endswith(f"\n{copy}")
    assert (
        "hardware/main/main.kicad_dru: rules JLCPCB track width after ee_tooling's copy them"
        " and win over them: remove them\n"
    ) in capsys.readouterr().err


@pytest.mark.parametrize(
    ("path", "text", "error"),
    [
        (
            "main.kicad_dru",
            f"(version 1)\n{SECTION}{SECTION}",
            "ee_tooling's markers are not one begin line, then one end line",
        ),
        ("main.kicad_pro", "{", "Expecting property name"),
    ],
)
def test_rules_refused(tmp_path: Path, path: str, text: str, error: str) -> None:
    layout = rules_repository(tmp_path)
    (tmp_path / "hardware/main" / path).write_text(text)
    with pytest.raises(
        ValueError, match=rf"^hardware/main/{path}: {error}.*; install the rules by hand$"
    ):
        plan(layout)


def test_rules_left_without_a_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    layout = rules_repository(tmp_path)
    (tmp_path / "hardware/main/main.kicad_pro").unlink()
    assert not [s for s in plan(layout) if isinstance(s, Edit)]
    assert (
        "hardware/main/main.kicad_pro is not there: rules not installed" in capsys.readouterr().err
    )


def test_check_says_what_each_awaits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--check: a managed file out of date, a file to be migrated, a board's
    rules to be updated, each said as such; it fails."""
    layout = adopted(tmp_path, "main")
    (tmp_path / "AGENTS.md").write_text("# Mine\n")
    monkeypatch.setattr(adopt, "locate", lambda _: layout)
    monkeypatch.setattr(sys, "argv", ["adopt", "--check"])
    with pytest.raises(SystemExit) as e:
        adopt.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    run = "; run hardware/ee_tooling/adopt --yes\n"
    assert f"out of date: AGENTS.md{run}" in err
    assert f"to be migrated: jlcpcb.toml{run}" in err
    assert f"to be migrated: .claude/skills/jlcpcb-release{run}" in err
    apply(tmp_path, [s for s in plan(layout) if not isinstance(s, Edit)], lambda *_: None)
    with pytest.raises(SystemExit):
        adopt.main()
    err = capsys.readouterr().err
    assert f"to be updated: hardware/main/main.kicad_dru{run}" in err
    assert f"to be updated: hardware/main/main.kicad_pro{run}" in err
    assert "out of date" not in err


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("# mine\n", True),
        ('name = "b"  # the board\n', True),
        ('name = "a#b"\n', False),
        ("name = 'a#b'\n", False),
        ('name = "a\\"#b"\n', False),
        ('[[board]]\nname = "b"\n', False),
    ],
)
def test_has_comment(text: str, found: bool) -> None:
    assert has_comment(text) is found


def test_migrate_without_comments_says_nothing_of_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "jlcpcb.toml").write_text(OLD_CONFIG.replace("# my boards\n", ""))
    plan(layout)
    assert "comments" not in capsys.readouterr().err


def test_lfs_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git on the path, git-lfs not: git's own error, and the fix."""
    git_only = tmp_path / "bin"
    git_only.mkdir()
    (git_only / "git").symlink_to(shutil.which("git") or "git")
    monkeypatch.setenv("PATH", str(git_only))
    why = lfs_missing()
    assert why is not None
    assert why.startswith("git-lfs is missing (git lfs version: git: 'lfs' is not a git command.")
    assert why.endswith("); install the distribution's git-lfs package (docs/adopting.md)")


@pytest.mark.parametrize("mode", [[], ["--yes"], ["--check"]])
def test_every_mode_refused_without_git_lfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: list[str]
) -> None:
    """Before the repository is looked at."""
    monkeypatch.setattr(adopt, "lfs_missing", lambda: "git-lfs is missing")
    monkeypatch.setattr(adopt, "locate", lambda _: pytest.fail("located"))
    monkeypatch.setattr(sys, "argv", ["ee-tooling-adopt", *mode])
    with pytest.raises(SystemExit, match=r"^git-lfs is missing$"):
        adopt.main()


@needs_lfs
def test_lfs_found() -> None:
    assert lfs_missing() is None


def commit(root: Path, message: str, *add: str) -> None:
    """A commit of add, past the hooks (the submodule's commit-msg hook is
    not there)."""
    git("-C", str(root), "add", *add)
    git("-C", str(root), "commit", "-q", "--no-verify", "-m", message)


def run_git_only(args: Sequence[str], directory: Path) -> None:
    """adopt's run_command for git's commands; uv's are not run."""
    if args[0] == "git":
        subprocess.run(args, cwd=directory, check=True, capture_output=True)


def adopt_main(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *mode: str,
) -> tuple[object, str]:
    """adopt in layout's repository with mode; its exit code (sys.exit's
    argument) and stderr."""
    monkeypatch.setattr(adopt, "locate", lambda _: layout)
    monkeypatch.setattr(adopt, "run_command", run_git_only)
    monkeypatch.setattr(sys, "argv", ["ee-tooling-adopt", *mode])
    capsys.readouterr()
    code: object = None
    try:
        adopt.main()
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().err


# Files of the types Git LFS tracks, and one it does not, by path.
BINARIES = {
    "production/20261118-1/main-1-gerbers-jlcpcb.zip": "PK\x03\x04 zip",
    "docs/U1.PDF": "%PDF-1.4 datasheet",
    "hardware/main/main.3dshapes/u1.step": "ISO-10303-21;",
}


@needs_lfs
def test_lfs_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    git_config: Path,
) -> None:
    """A repository with archives, a PDF and a STEP file committed as plain
    blobs: --check fails, --yes writes the section and sets the filters in
    .git/config alone, --check then names each file and the fix, which
    makes it pass."""
    layout = repository(tmp_path, "main")
    for name, text in BINARIES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    commit(tmp_path, "board", ".")
    status, err = adopt_main(layout, monkeypatch, capsys, "--check")
    assert status == 1
    assert "out of date: .gitattributes; run hardware/ee_tooling/adopt --yes\n" in err
    assert "Git LFS" not in err
    assert adopt_main(layout, monkeypatch, capsys, "--yes")[0] is None
    assert git_config.read_text() == ""
    local = subprocess.run(
        ["git", "-C", str(tmp_path), "config", "--local", "filter.lfs.process"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert local.stdout == "git-lfs filter-process\n"
    assert sorted(p.name for p in (tmp_path / ".githooks").iterdir()) == ["commit-msg", "pre-push"]
    adopted = (".gitattributes", ".githooks", ".github", ".claude", "AGENTS.md", "fab.toml")
    board = (
        "hardware/pyproject.toml",
        "hardware/main/main.kicad_pro",
        "hardware/main/main.kicad_dru",
    )
    commit(tmp_path, "adopt", *adopted, *board)
    porcelain = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert sorted(porcelain.splitlines()) == sorted(f" M {name}" for name in BINARIES)
    status, err = adopt_main(layout, monkeypatch, capsys, "--check")
    assert status == 1
    assert "out of date" not in err
    for name in BINARIES:
        assert f'"{name}"' in err
        assert "should have been a pointer but was not" in err
    assert "main.kicad_pcb" not in err
    assert err.endswith(
        "not stored through Git LFS: the files above; with git-lfs's filters set (adopt --yes"
        " sets them), run git add --renormalize . in a clean work tree, and commit\n"
    )
    git("-C", str(tmp_path), "add", "--renormalize", ".")
    commit(tmp_path, "renormalize")
    assert adopt_main(layout, monkeypatch, capsys, "--check") == (0, "")
    for name, text in BINARIES.items():
        blob = subprocess.run(
            ["git", "-C", str(tmp_path), "show", f"HEAD:{name}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert blob.startswith("version https://git-lfs.github.com/spec/v1\n")
        assert (tmp_path / name).read_text() == text
    pcb = tmp_path / "hardware/main/main.kicad_pcb"
    assert pcb.read_text() == PCB
    assert sorted(p.name for p in (tmp_path / ".githooks").iterdir()) == ["commit-msg", "pre-push"]
    assert git_config.read_text() == ""


@needs_lfs
def test_plain_blobs_none_before_a_commit(tmp_path: Path) -> None:
    git("init", "-q", str(tmp_path))
    assert plain_blobs(tmp_path) is None


def lfs_clone(tmp_path: Path) -> Layout:
    """A clone, made without git-lfs's filters, of a repository whose
    STEP file is in Git LFS at a file:// remote."""
    origin, source, clone = tmp_path / "origin.git", tmp_path / "source", tmp_path / "clone"
    git("init", "-q", "--bare", str(origin))
    repository(source, "main")
    git("-C", str(source), "config", "--local", "filter.lfs.process", "git-lfs filter-process")
    git("-C", str(source), "config", "--local", "filter.lfs.required", "true")
    (source / ".gitattributes").write_text(GITATTRIBUTES)
    (source / "hardware/main/u1.step").write_text("ISO-10303-21;")
    commit(source, "board", ".")
    git("-C", str(source), "remote", "add", "origin", origin.as_uri())
    git("-C", str(source), "push", "-q", "origin", "HEAD:main")
    git("-C", str(source), "lfs", "push", "--all", "origin")
    git("clone", "-q", "-b", "main", origin.as_uri(), str(clone))
    return Layout(clone, SUBMODULE)


@needs_lfs
def test_lfs_clone_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clone's pointers: --yes sets the filters, then pulls; no git-lfs
    hook lands in .githooks."""
    layout = lfs_clone(tmp_path)
    step = layout.root / "hardware/main/u1.step"
    assert step.read_text().startswith("version https://git-lfs.github.com/spec/v1\n")
    assert lfs_unfetched(layout.root)
    assert commands(plan(layout))[-2:] == [LFS_INSTALL, LFS_PULL]
    assert adopt_main(layout, monkeypatch, capsys, "--yes")[0] is None
    assert step.read_text() == "ISO-10303-21;"
    assert not lfs_unfetched(layout.root)
    assert LFS_PULL not in commands(plan(layout))
    assert LFS_INSTALL not in commands(plan(layout))
    assert sorted(p.name for p in (layout.root / ".githooks").iterdir()) == [
        "commit-msg",
        "pre-push",
    ]


@needs_lfs
def test_lfs_pull_out_of_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The remote gone: the files adopt wrote stay, and adopt says why the
    pointers do."""
    layout = lfs_clone(tmp_path)
    git("-C", str(layout.root), "remote", "set-url", "origin", (tmp_path / "gone.git").as_uri())
    assert adopt_main(layout, monkeypatch, capsys, "--yes")[0] == (
        "git lfs pull failed (2); the Git LFS files stay pointers until it runs: rerun adopt"
        " --yes with the remote in reach"
    )
    assert (layout.root / ".githooks/pre-push").exists()


@needs_lfs
def test_no_pull_with_nothing_to_fetch(tmp_path: Path) -> None:
    """git lfs pull fails with no remote, even with nothing to fetch: not
    run then."""
    layout = repository(tmp_path, "main")
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES)
    commit(tmp_path, "board", ".")
    assert not lfs_unfetched(tmp_path)
    assert LFS_PULL not in commands(plan(layout))
