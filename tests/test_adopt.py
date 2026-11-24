import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
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
    package_name,
    pending,
    plain_blobs,
    plan,
    projects,
    stale,
)
from ee_tooling.fab_release import load_config
from ee_tooling.kicad_rules import current, erc_set, erc_settings, installed
from ee_tooling.profiles.jlcpcb import Jlcpcb, Options

SUBMODULE = Path("hardware/ee_tooling")


@pytest.fixture(autouse=True)
def supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system Python that can run pcbnew, whatever this machine's is."""
    monkeypatch.setattr(adopt, "unsupported", lambda: None)


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
    new = f"# Agents\n\n{AGENTS_BEGIN}\nnew\n{AGENTS_END}\n\n## After\n"
    assert agents_text(old, "new\n") == new
    assert agents_text(new, "new\n") == new


def test_agents_adopted_by_hand_left_alone() -> None:
    assert agents_text("@hardware/ee_tooling/docs/kicad-style.md\n", "new\n") is None


def test_agents_section_with_the_style_replaced() -> None:
    """The house style imported inside the markers is ee_tooling's section."""
    old = f"{AGENTS_BEGIN}\n@x/docs/kicad-style.md\n{AGENTS_END}\n"
    assert agents_text(old, "new\n") == f"{AGENTS_BEGIN}\nnew\n{AGENTS_END}\n"


def test_agents_marker_quoted_in_prose_is_the_projects() -> None:
    """A marker within a line is not one: the real section is replaced, the
    lines before it kept."""
    old = (
        f"# Agents\n\nadopt's section starts at `{AGENTS_BEGIN}`.\n\nOwn rules.\n\n"
        f"{AGENTS_BEGIN}\nold\n{AGENTS_END}\n"
    )
    text = agents_text(old, "new\n")
    assert text == old.replace("\nold\n", "\nnew\n")
    assert agents_text(text, "new\n") == text


@pytest.mark.parametrize(
    "old",
    [
        f"{AGENTS_END}\nown\n{AGENTS_BEGIN}\n",
        f"own\n{AGENTS_BEGIN}\nmine\n",
        f"own\n{AGENTS_BEGIN}\nold\n{AGENTS_END}\nmine\n{AGENTS_END}\n",
        f"{AGENTS_BEGIN}\na\n{AGENTS_END}\n{AGENTS_BEGIN}\nb\n{AGENTS_END}\n",
        f"own\n{AGENTS_END}\n",
    ],
)
def test_agents_markers_out_of_place_refused(old: str) -> None:
    """An end before the begin, a begin with no end, two of either: refused,
    rather than a section added at every run or the project's lines lost."""
    with pytest.raises(ValueError, match=r"^ee_tooling's markers are not one begin line, then one"):
        agents_text(old, "new\n")


@pytest.mark.parametrize(
    ("name", "old"),
    [
        ("AGENTS.md", f"own\n{AGENTS_BEGIN}\nmine\n"),
        (
            ".gitattributes",
            "*.png -text\n# ee_tooling: end; the project's own lines follow and win\n",
        ),
    ],
)
def test_adopt_refuses_misplaced_markers(tmp_path: Path, name: str, old: str) -> None:
    """Naming the file, before anything is written."""
    layout = repository(tmp_path, "main")
    (tmp_path / name).write_text(old)
    for run in (plan, stale):
        with pytest.raises(
            ValueError, match=rf"^{re.escape(name)}: ee_tooling's markers .*by hand$"
        ):
            run(layout)
    assert (tmp_path / name).read_text() == old
    assert not (tmp_path / ".githooks").exists()


# A section that goes first, as the project's lines after it win.
FIRST = Markers("# begin", "# end", first=True)


@pytest.mark.parametrize(
    ("existing", "text"),
    [
        (None, "# begin\nnew\n# end\n"),
        ("own\n", "# begin\nnew\n# end\nown\n"),
        ("\n\nown\n", "# begin\nnew\n# end\n\n\nown\n"),
        ("# begin\nold\n# end\nown\n", "# begin\nnew\n# end\nown\n"),
        ("mine\n# begin\nold\n# end\n\nown\n", "mine\n# begin\nnew\n# end\n\nown\n"),
        ("# begin\nold\n# end\n\n\n", "# begin\nnew\n# end\n\n\n"),
        ("# begin\nold\n# end", "# begin\nnew\n# end\n"),
        ("own # begin\n", "# begin\nnew\n# end\nown # begin\n"),
        ("# begin \nold\n# end\t\nown\n", "# begin\nnew\n# end\nown\n"),
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
        ("git", "config", "core.hooksPath", ".githooks"),
        LFS_INSTALL,
        ("uv", "venv", "--system-site-packages", "--python", "/usr/bin/python3"),
        ("uv", "add", "--dev", "--editable", "--no-workspace", "./ee_tooling"),
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
        'HOOK="ee_tooling/.githooks/commit-msg"\n'
        in (tmp_path / ".githooks/commit-msg").read_text()
    )
    workflow = (tmp_path / ".github/workflows/ee-tooling.yml").read_text()
    assert "run: '\"ee_tooling/adopt\" --check'" in workflow
    assert "@ee_tooling/docs/kicad-style.md" in (tmp_path / "AGENTS.md").read_text()
    assert "(ee_tooling/docs/adopting.md)" in (tmp_path / ".githooks/pre-push").read_text()
    assert stale(layout) == []


def test_plan_fresh_repository(tmp_path: Path) -> None:
    steps = plan(repository(tmp_path, "main"))
    assert commands(steps) == [
        ("git", "config", "core.hooksPath", ".githooks"),
        LFS_INSTALL,
        ("uv", "venv", "--system-site-packages", "--python", "/usr/bin/python3"),
        ("uv", "add", "--dev", "--editable", "--no-workspace", "./ee_tooling"),
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
    assert 'HOOK="hardware/ee_tooling/.githooks/commit-msg"\n' in hook.read_text()
    assert "@" not in hook.read_text().replace('"$@"', "")
    workflow = (tmp_path / ".github/workflows/ee-tooling.yml").read_text()
    assert 'sh "hardware/ee_tooling/.githooks/commit-msg" --messages' in workflow
    assert "run: '\"hardware/ee_tooling/adopt\" --check'" in workflow
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
        f'name = "{package_name(tmp_path.name)}-hardware"'
        in (tmp_path / "hardware/pyproject.toml").read_text()
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


@pytest.mark.parametrize(
    "spoiled",
    [lambda b: b.replace(b"\n", b"\r\n"), lambda b: b"\xef\xbb\xbf" + b],
    ids=["crlf", "bom"],
)
def test_managed_hook_byte_for_byte(tmp_path: Path, spoiled: Callable[[bytes], bytes]) -> None:
    """A hook with CRLF line endings or a byte order mark, which the kernel
    cannot run (#!/bin/sh followed by a CR), is stale, and rewritten with
    LF and no mark."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    for name in ("commit-msg", "pre-push"):
        hook = tmp_path / ".githooks" / name
        good = hook.read_bytes()
        hook.write_bytes(spoiled(good))
        assert stale(layout) == [Path(".githooks", name)]
        apply(tmp_path, plan(layout), lambda *_: None)
        assert hook.read_bytes() == good
        assert good.startswith(b"#!/bin/sh\n") and b"\r" not in good
        assert stale(layout) == []


def test_hooks_checked_out_with_lf(tmp_path: Path) -> None:
    """.gitattributes's section has git check the hooks out with LF."""
    ran(tmp_path)
    attributes = subprocess.run(
        ["git", "check-attr", "eol", "--", ".githooks/commit-msg", ".githooks/pre-push"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert attributes.splitlines() == [
        ".githooks/commit-msg: eol: lf",
        ".githooks/pre-push: eol: lf",
    ]


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
.githooks/* text eol=lf
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
    commit(tmp_path, "adopted", "jlcpcb.toml")
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
    assert (
        err.count(
            "jlcpcb.toml: its comments not carried over to fab.toml; they stay in git's history"
            " (git show HEAD:jlcpcb.toml)\n"
        )
        == 1
    )
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
    commit(tmp_path, "jlcpcb.toml", "jlcpcb.toml")
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
    "board": {"design_settings": {"rules": {"min_clearance": 0.05, "min_hole_clearance": 0.25}}},
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
ERC_CHANGES = erc_set(LOW_SETUP, erc_settings(current()))[1]
SECTION = installed("jlcpcb", Jlcpcb.rules, Options(), current())


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
            " board.design_settings.rules.min_hole_to_hole: none -> 0.2;"
            " board.design_settings.rules.min_microvia_drill: none -> 0.15;"
            " board.design_settings.rules.min_through_hole_diameter: none -> 0.15;"
            " board.design_settings.rules.min_track_width: none -> 0.1;"
            " board.design_settings.rules.min_via_annular_width: none -> 0.05;"
            " board.design_settings.rules.min_via_diameter: none -> 0.25;"
            f" ERC severities and connection grid set: {'; '.join(ERC_CHANGES)};"
            " close the board in KiCad first",
        ),
    ]
    apply(tmp_path, edits, lambda *_: None)
    assert (board / "main.kicad_dru").read_text() == f"(version 1)\n{SECTION}\n{OWN_RULE}"
    project = json.loads((board / "main.kicad_pro").read_text())
    assert project["board"]["design_settings"]["rules"] == {
        "min_clearance": 0.1,
        "min_copper_edge_clearance": 0.2,
        "min_hole_clearance": 0.25,
        "min_hole_to_hole": 0.2,
        "min_microvia_drill": 0.15,
        "min_through_hole_diameter": 0.15,
        "min_track_width": 0.1,
        "min_via_annular_width": 0.05,
        "min_via_diameter": 0.25,
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
    old = SECTION.replace("(min 0.18mm)", "(min 0.05mm)", 1)
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
    copy = '(rule "JLCPCB annular ring"\n\t(constraint annular_width (min 0.18mm)))\n'
    dru.write_text(f"(version 1)\n{copy}")
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert dru.read_text().endswith(f"\n{copy}")
    assert (
        "hardware/main/main.kicad_dru: 1 rule below ee_tooling's end marker copies the"
        " profile's and wins over them:\n  JLCPCB annular ring\n"
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
    assert f"missing: .githooks/commit-msg{run}" in err
    assert "out of date: .githooks/commit-msg" not in err
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
    why = lfs_missing(SUBMODULE)
    assert why == (
        "git-lfs is missing (git lfs version: git: 'lfs' is not a git command. See 'git"
        " --help'.); install the distribution's git-lfs package"
        " (hardware/ee_tooling/docs/adopting.md)"
    )


@pytest.mark.parametrize("mode", [[], ["--yes"], ["--check"]])
def test_every_mode_refused_without_git_lfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: list[str]
) -> None:
    """Before anything but finding the repository."""
    monkeypatch.setattr(adopt, "lfs_missing", lambda _: "git-lfs is missing")
    monkeypatch.setattr(adopt, "locate", lambda _: Layout(tmp_path, SUBMODULE))
    monkeypatch.setattr(adopt, "plan", lambda _: pytest.fail("planned"))
    monkeypatch.setattr(adopt, "pending", lambda _: pytest.fail("checked"))
    monkeypatch.setattr(sys, "argv", ["ee-tooling-adopt", *mode])
    with pytest.raises(SystemExit, match=r"^git-lfs is missing$"):
        adopt.main()


@needs_lfs
def test_lfs_found() -> None:
    assert lfs_missing(SUBMODULE) is None


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
    assert "missing: .gitattributes; run hardware/ee_tooling/adopt --yes\n" in err
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
        "not stored through Git LFS: the files above; with git-lfs's filters set"
        " (hardware/ee_tooling/adopt --yes sets them), run git add -A && git add --renormalize ."
        " and commit\n"
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
def test_check_installs_no_hook(tmp_path: Path) -> None:
    """--check before --yes: git lfs fsck, with no pre-push in .githooks,
    writes none of git-lfs's hooks there."""
    layout = repository(tmp_path, "main")
    git("-C", str(tmp_path), "config", "core.hooksPath", ".githooks")
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks/commit-msg").write_text("#!/bin/sh\n")
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES)
    (tmp_path / "a.zip").write_text("PK")
    commit(tmp_path, "board", ".")
    report = plain_blobs(layout.root)
    assert report is not None
    assert '"a.zip"' in report
    assert sorted(p.name for p in (tmp_path / ".githooks").iterdir()) == ["commit-msg"]


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
    assert [c for c in commands(plan(layout)) if c[0] == "git"][-2:] == [LFS_INSTALL, LFS_PULL]
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
        "git lfs pull failed (2); the Git LFS files stay pointers until it runs: rerun"
        " hardware/ee_tooling/adopt --yes with the remote in reach"
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


def test_check_agrees_with_the_plan_before_migration(tmp_path: Path) -> None:
    """jlcpcb.toml and no fab.toml: --check names the boards' rules files
    the dry run would update, from the fab.toml it would write."""
    layout = adopted(tmp_path, "main")
    edits = {s.path for s in plan(layout) if isinstance(s, Edit)}
    assert edits == {Path("hardware/main/main.kicad_dru"), Path("hardware/main/main.kicad_pro")}
    assert {p for what, p in pending(layout) if what == "to be updated"} == edits


def test_git_ignores_a_callers_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GIT_DIR and a relative GIT_INDEX_FILE, as a hook or `git rebase -x`
    sets them: adopt's git commands act on the repository they run in."""
    other = tmp_path / "other"
    git("init", "-q", str(other))
    before = (other / ".git/config").read_text()
    board = tmp_path / "board"
    git("init", "-q", str(board))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")
    assert adopt.git(board, "rev-parse", "--show-toplevel") == str(board)
    adopt.run_command(("git", "config", "core.hooksPath", ".githooks"), board)
    assert adopt.git(board, "config", "core.hooksPath") == ".githooks"
    assert (other / ".git/config").read_text() == before


def link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def refused_through(layout: Layout, name: str) -> None:
    """plan and --check refuse, naming the link name."""
    for run in (plan, stale):
        with pytest.raises(
            ValueError, match=rf"^{re.escape(name)} is a symbolic link \(to .*\): adopt changes no"
        ):
            run(layout)


def test_managed_file_through_a_link_refused(tmp_path: Path) -> None:
    """A managed hook linked to the project's own script: the script kept."""
    layout = repository(tmp_path, "main")
    script = tmp_path / "scripts/pre-push.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nmake lint\n")
    link(tmp_path / ".githooks/pre-push", Path("../scripts/pre-push.sh"))
    refused_through(layout, ".githooks/pre-push")
    assert script.read_text() == "#!/bin/sh\nmake lint\n"


def test_file_in_a_linked_directory_refused(tmp_path: Path) -> None:
    """A project file in a directory linked outside the repository."""
    outside = tmp_path / "outside"
    outside.mkdir()
    layout = repository(tmp_path / "board", "main")
    link(tmp_path / "board/.claude", outside)
    with pytest.raises(ValueError, match=r"^\.claude is a symbolic link"):
        plan(layout)
    assert list(outside.iterdir()) == []


def test_edit_through_a_link_outside_refused(tmp_path: Path) -> None:
    outside = tmp_path / "shared.toml"
    outside.write_text(NO_FAB)
    layout = repository(tmp_path / "board", "main")
    link(tmp_path / "board/fab.toml", outside)
    refused_through(layout, "fab.toml")
    assert outside.read_text() == NO_FAB


def test_edit_through_a_link_inside_written_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A .kicad_dru linked to a file in the repository: written there, the
    link kept, said so."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    (tmp_path / "rules.kicad_dru").write_text(dru.read_text())
    dru.unlink()
    link(dru, Path("../../rules.kicad_dru"))
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert dru.is_symlink()
    assert "ee_tooling: jlcpcb rules" in (tmp_path / "rules.kicad_dru").read_text()
    assert (
        "hardware/main/main.kicad_dru links to rules.kicad_dru: written there"
        in capsys.readouterr().err
    )
    assert Path("hardware/main/main.kicad_dru") not in stale(layout)


def test_agents_linked_to_claude(tmp_path: Path) -> None:
    """AGENTS.md a link to CLAUDE.md: the section written in CLAUDE.md, the
    link kept."""
    layout = repository(tmp_path, "main")
    (tmp_path / "CLAUDE.md").write_text("# Mine\n")
    link(tmp_path / "AGENTS.md", Path("CLAUDE.md"))
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    assert (tmp_path / "AGENTS.md").is_symlink()
    text = (tmp_path / "CLAUDE.md").read_text()
    assert text.startswith("# Mine\n\n<!-- ee_tooling: begin")
    assert Path("AGENTS.md") not in stale(layout)


def test_link_into_another_repository_refused(tmp_path: Path) -> None:
    """A section's file linked into a submodule: not adopt's to change."""
    layout = repository(tmp_path, "main")
    library = tmp_path / "lib"
    library.mkdir()
    (library / ".git").write_text("gitdir: x\n")
    (library / "AGENTS.md").write_text("# Library\n")
    link(tmp_path / "AGENTS.md", Path("lib/AGENTS.md"))
    refused_through(layout, "AGENTS.md")
    assert (library / "AGENTS.md").read_text() == "# Library\n"


def test_remove_through_a_link_refused(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "old.toml").write_text(OLD_CONFIG)
    link(tmp_path / "jlcpcb.toml", Path("old.toml"))
    with pytest.raises(ValueError, match=r"^jlcpcb.toml is a symbolic link"):
        plan(layout)
    assert (tmp_path / "jlcpcb.toml").is_symlink()


@pytest.mark.parametrize("linked", ["directory", "md"])
def test_rename_through_a_link_refused(tmp_path: Path, linked: str) -> None:
    """The old skill's directory, or a Markdown file in it, linked outside
    the repository: its .md files there are not rewritten."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("jlcpcb-release\n")
    layout = adopted(tmp_path / "board", "main")
    skill = tmp_path / "board/.claude/skills/jlcpcb-release"
    if linked == "directory":
        for f in skill.iterdir():
            f.unlink()
        skill.rmdir()
        link(skill, outside)
        name = ".claude/skills/jlcpcb-release"
    else:
        (skill / "SKILL.md").unlink()
        link(skill / "SKILL.md", outside / "SKILL.md")
        name = ".claude/skills/jlcpcb-release/SKILL.md"
    with pytest.raises(ValueError, match=rf"^{re.escape(name)} is a symbolic link"):
        plan(layout)
    assert (outside / "SKILL.md").read_text() == "jlcpcb-release\n"


def test_projects_skip_other_repositories(tmp_path: Path) -> None:
    """A library's submodule (a .git file) or a clone (a .git directory)
    under the hardware directory: its projects are not boards."""
    layout = repository(tmp_path, "main")
    for name, git_entry in (("lib", "file"), ("clone", "directory")):
        demo = tmp_path / "hardware" / name / "demo"
        demo.mkdir(parents=True)
        (demo / "demo.kicad_pro").write_text("{}")
        entry = tmp_path / "hardware" / name / ".git"
        if git_entry == "file":
            entry.write_text("gitdir: ../../.git/modules/lib\n")
        else:
            entry.mkdir()
    assert projects(layout) == [tmp_path / "hardware/main/main.kicad_pro"]
    config = fab_config(layout)
    assert config is not None
    assert "demo" not in config


@pytest.mark.parametrize(
    ("state", "kept"),
    [("untracked", "not committed"), ("modified", "with uncommitted changes")],
)
def test_migration_keeps_a_config_git_would_lose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str, kept: str
) -> None:
    """jlcpcb.toml not committed as it is: fab.toml written from it, it
    kept and said so; --check fails until then, and passes after, naming it
    on stderr."""
    layout = repository(tmp_path, "main")
    old = tmp_path / "jlcpcb.toml"
    old.write_text(OLD_CONFIG)
    if state == "modified":
        commit(tmp_path, "jlcpcb.toml", "jlcpcb.toml")
        old.write_text(OLD_CONFIG + "# mine\n")
    text = old.read_text()
    assert Path("jlcpcb.toml") in stale(layout)
    steps = plan(layout)
    assert Remove(Path("jlcpcb.toml"), "migrated to fab.toml", kept) in steps
    apply(tmp_path, steps, lambda *_: None)
    out = capsys.readouterr().out
    assert (
        f"keep jlcpcb.toml (project; migrated to fab.toml; {kept}: remove it yourself once the"
        " new file is checked)\n"
    ) in out
    assert old.read_text() == text
    assert load_config((tmp_path / "fab.toml").read_text())["main"].sources == (Path("docs"),)
    assert stale(layout) == []
    assert "jlcpcb.toml and fab.toml: jlcpcb.toml is not read; remove it" in capsys.readouterr().err


def test_managed_hook_not_executable(tmp_path: Path) -> None:
    """A managed hook without its executable bit, which git skips: --check
    fails, --yes sets it, then nothing is to be done."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    hook = tmp_path / ".githooks/pre-push"
    hook.chmod(0o644)
    assert stale(layout) == [Path(".githooks/pre-push")]
    steps = [s for s in plan(layout) if isinstance(s, File)]
    assert [describe(s, tmp_path) for s in steps] == [
        "make executable .githooks/pre-push (managed)"
    ]
    apply(tmp_path, steps, run_git_only)
    assert hook.stat().st_mode & 0o111
    assert stale(layout) == []


@pytest.mark.parametrize("file_mode", ["true", "false"])
def test_managed_hook_not_executable_in_git(tmp_path: Path, file_mode: str) -> None:
    """The hook committed 100644, executable in the work tree: a clone's is
    not; --check fails, --yes stages the mode, whatever core.fileMode."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    git("-C", str(tmp_path), "config", "core.fileMode", file_mode)
    commit(tmp_path, "hooks", ".githooks/pre-push")
    git("-C", str(tmp_path), "update-index", "--chmod=-x", ".githooks/pre-push")
    git("-C", str(tmp_path), "commit", "-q", "--no-verify", "--allow-empty", "-m", "100644")
    (tmp_path / ".githooks/pre-push").chmod(0o755)
    assert stale(layout) == [Path(".githooks/pre-push")]
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], run_git_only)
    staged = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "-s", ".githooks/pre-push"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert staged.startswith("100755 ")
    assert stale(layout) == []


def test_hook_not_executable_in_the_work_tree_with_file_mode_false(tmp_path: Path) -> None:
    """core.fileMode false and the hook tracked 100755: git still runs it
    only if the work tree's bit is set, so --check fails without it."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    git("-C", str(tmp_path), "config", "core.fileMode", "false")
    git("-C", str(tmp_path), "add", "--chmod=+x", ".githooks/pre-push")
    commit(tmp_path, "hooks", ".githooks/pre-push")
    hook = tmp_path / ".githooks/pre-push"
    hook.chmod(0o644)
    assert stale(layout) == [Path(".githooks/pre-push")]
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], run_git_only)
    assert hook.stat().st_mode & 0o111
    assert stale(layout) == []


@needs_lfs
def test_dry_run_and_check_leave_git_config(tmp_path: Path) -> None:
    """git lfs ls-files (the plan) and git lfs fsck (--check) would set
    lfs.repositoryformatversion in .git/config: neither does here, and
    they still read the repository's settings."""
    layout = repository(tmp_path, "main")
    git("-C", str(tmp_path), "config", "core.hooksPath", ".githooks")
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES)
    (tmp_path / "a.zip").write_text("PK")
    commit(tmp_path, "board", ".")
    config = (tmp_path / ".git/config").read_bytes()
    plan(layout)
    stale(layout)
    report = plain_blobs(tmp_path)
    assert report is not None
    assert '"a.zip"' in report
    assert (tmp_path / ".git/config").read_bytes() == config
    assert not (tmp_path / ".githooks").exists()


@pytest.mark.parametrize(
    "name", ["AGENTS.md", ".gitattributes", "fab.toml", "hardware/main/main.kicad_pro"]
)
def test_file_not_utf8_named(tmp_path: Path, name: str) -> None:
    layout = repository(tmp_path, "main")
    if name == "hardware/main/main.kicad_pro":
        (tmp_path / "fab.toml").write_text(fab_config(layout) or "")
    (tmp_path / name).write_bytes(b"caf\xe9\n")
    for run in (plan, stale):
        with pytest.raises(ValueError, match=rf"{re.escape(name)}: not UTF-8 \(byte 3: "):
            run(layout)


def test_rename_leaves_a_markdown_file_not_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = adopted(tmp_path, "main")
    (tmp_path / ".claude/skills/jlcpcb-release/old.md").write_bytes(b"jlcpcb-release caf\xe9\n")
    apply(tmp_path, plan(layout), lambda *_: None)
    moved = tmp_path / ".claude/skills/fab-release"
    assert (moved / "old.md").read_bytes() == b"jlcpcb-release caf\xe9\n"
    assert (moved / "SKILL.md").read_text().startswith("---\nname: fab-release\n")
    assert "old.md: not UTF-8 (byte 18: invalid continuation byte): left as it is" in (
        capsys.readouterr().err
    )


def test_unwritable_refused_before_anything(tmp_path: Path) -> None:
    """A read-only .kicad_pro: --yes stops naming it, nothing done."""
    layout = rules_repository(tmp_path)
    project = tmp_path / "hardware/main/main.kicad_pro"
    before = project.read_text()
    project.chmod(0o444)
    (tmp_path / ".githooks/pre-push").unlink()
    steps = plan(layout)
    with pytest.raises(
        ValueError, match=r"^hardware/main/main.kicad_pro cannot be written: nothing done$"
    ):
        apply(tmp_path, steps, lambda *_: pytest.fail("ran"))
    assert project.read_text() == before
    assert not (tmp_path / ".githooks/pre-push").exists()


def test_write_whole_or_not_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Interrupted before the rename: the file as it was, no scratch file
    left; done: the new text, the file's mode kept."""
    target = tmp_path / "b.kicad_pro"
    target.write_text("old\n")
    target.chmod(0o640)

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as m:
        m.setattr(os, "replace", interrupted)
        with pytest.raises(KeyboardInterrupt):
            adopt.write(target, "new\n")
    assert target.read_text() == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["b.kicad_pro"]
    adopt.write(target, "new\n")
    assert target.read_text() == "new\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["b.kicad_pro"]


def test_fsck_message() -> None:
    """Files named: the fix; fsck's own failure: said as such."""
    found = (
        'pointer: unexpectedGitObject: "a.zip" (treeish 1234) should have been a pointer but'
        " was not"
    )
    assert adopt.fsck_message(found, SUBMODULE).endswith(
        "\nnot stored through Git LFS: the files above; with git-lfs's filters set"
        " (hardware/ee_tooling/adopt --yes sets them), run git add -A && git add --renormalize ."
        " and commit"
    )
    failed = "fatal: bad object HEAD"
    assert adopt.fsck_message(failed, SUBMODULE) == (
        "fatal: bad object HEAD\ngit lfs fsck --pointers HEAD failed (its error is above): fix"
        " that, then rerun"
    )
    assert "renormalize" not in adopt.fsck_message(f"{found}\n{failed}", SUBMODULE)


@pytest.mark.parametrize(
    ("directory", "name"),
    [
        ("widget", "widget"),
        ("My Board", "my-board"),
        ("caf\u00e9 board", "caf-board"),
        ("__x__", "x"),
        ("\u00e9\u00e9", "board"),
        ("a.b_c", "a-b-c"),
    ],
)
def test_package_name(directory: str, name: str) -> None:
    """A clone's directory name made a package name uv accepts."""
    assert package_name(directory) == name
    assert adopt.PACKAGE_NAME.match(f"{name}-hardware")


def test_pyproject_with_a_bad_name_said(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The project's own file, never rewritten: named, with a name to use."""
    layout = repository(tmp_path, "main")
    pyproject = tmp_path / "hardware/pyproject.toml"
    pyproject.parent.mkdir(exist_ok=True)
    pyproject.write_text('[project]\nname = "My Board-hardware"\n')
    plan(layout)
    assert (
        "[project] name 'My Board-hardware' is not a package name uv accepts: rename it, say to"
        " 'my-board-hardware'" in capsys.readouterr().err
    )


def test_git_set_up_before_uv(tmp_path: Path) -> None:
    """A uv step that fails leaves hooksPath and the LFS filters set."""
    layout = repository(tmp_path / "My Board", "main")
    ran_commands: list[tuple[str, ...]] = []

    def run(args: Sequence[str], directory: Path) -> None:
        ran_commands.append(tuple(args))
        if args[0] == "uv":
            raise subprocess.CalledProcessError(1, list(args))
        run_git_only(args, directory)

    with pytest.raises(subprocess.CalledProcessError):
        apply(layout.root, plan(layout), run)
    assert ran_commands[:2] == [("git", "config", "core.hooksPath", ".githooks"), LFS_INSTALL]
    assert adopt.git(layout.root, "config", "core.hooksPath") == ".githooks"
    assert 'name = "my-board-hardware"' in (layout.root / "hardware/pyproject.toml").read_text()


def test_skill_move_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopped while rewriting the old skill's files: the old directory is
    still there, so --check fails and a rerun finishes the move."""
    layout = adopted(tmp_path, "main")
    old = tmp_path / ".claude/skills/jlcpcb-release"
    (old / "more.md").write_text("jlcpcb-release\n")
    real = adopt.write

    def stopped(target: Path, text: str) -> None:
        if target.name == "more.md":
            raise KeyboardInterrupt
        real(target, text)

    with monkeypatch.context() as m:
        m.setattr(adopt, "write", stopped)
        with pytest.raises(KeyboardInterrupt):
            apply(tmp_path, plan(layout), lambda *_: None)
    assert old.is_dir()
    assert Path(".claude/skills/jlcpcb-release") in stale(layout)
    apply(tmp_path, plan(layout), lambda *_: None)
    new = tmp_path / ".claude/skills/fab-release"
    assert not old.exists()
    assert (new / "more.md").read_text() == "fab-release\n"
    assert "fab-release" in (new / "SKILL.md").read_text()
    assert stale(layout) == []


def test_leftover_scratch_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A scratch file an interrupted write left: --check names it, --yes
    removes it; one in another repository is left."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    left = tmp_path / "hardware/main/.main.kicad_pro.4242.ee_tooling-tmp"
    left.write_text("{")
    other = tmp_path / "hardware/lib/.x.kicad_pro.1.ee_tooling-tmp"
    other.parent.mkdir()
    (other.parent / ".git").write_text("gitdir: x\n")
    other.write_text("{")
    assert ("left over", Path("hardware/main/.main.kicad_pro.4242.ee_tooling-tmp")) in pending(
        layout
    )
    steps = [s for s in plan(layout) if isinstance(s, Remove)]
    assert steps == [Remove(left.relative_to(tmp_path), "left by a write that was interrupted")]
    apply(tmp_path, steps, lambda *_: None)
    assert not left.exists() and other.exists()
    assert stale(layout) == []


def test_leftover_of_a_hard_linked_file_kept(tmp_path: Path) -> None:
    """The scratch file of a hard-linked file written in place may hold the
    only whole new text: --check names it as such, --yes keeps it."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    os.link(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md")
    left = tmp_path / ".AGENTS.md.4242.ee_tooling-tmp"
    left.write_text("new\n")
    assert pending(layout) == [(adopt.LEFTOVER_KEPT, Path(".AGENTS.md.4242.ee_tooling-tmp"))]
    steps = plan(layout)
    assert steps[0] == Remove(
        Path(left.name),
        "left by a write that was interrupted",
        "it may be the only whole copy of AGENTS.md's new text, which has other hard links and"
        " is written in place",
    )
    assert describe(steps[0], tmp_path).endswith("remove it yourself once the new file is checked)")
    apply(tmp_path, [steps[0]], lambda *_: None)
    assert left.read_text() == "new\n"


def test_blank_line_after_the_section_kept(tmp_path: Path) -> None:
    """A blank line the project put after the end marker: --check passes and
    --yes leaves it."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    for name in ("AGENTS.md", ".gitattributes"):
        path = tmp_path / name
        path.write_text(path.read_text() + "\n# the project's own\n")
    assert stale(layout) == []
    before = {n: (tmp_path / n).read_text() for n in ("AGENTS.md", ".gitattributes")}
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    assert {n: (tmp_path / n).read_text() for n in before} == before


@pytest.mark.parametrize(
    ("directory", "why"),
    [
        ("../other", "is outside the repository"),
        ("hardware/lib/demo", "is in another repository (hardware/lib)"),
        ("hardware/lib", "is in another repository (hardware/lib)"),
    ],
)
def test_board_directory_not_the_repositorys_refused(
    tmp_path: Path, directory: str, why: str
) -> None:
    """fab.toml naming a directory outside the repository or in another
    one: refused before any edit is planned, nothing written there."""
    layout = repository(tmp_path / "board", "main")
    for d in (tmp_path / "other", tmp_path / "board/hardware/lib/demo"):
        d.mkdir(parents=True)
        (d / "main.kicad_pro").write_text("{}")
    (tmp_path / "board/hardware/lib/.git").write_text("gitdir: x\n")
    config = fab_config(layout) or ""
    (tmp_path / "board/fab.toml").write_text(
        config.replace('directory = "hardware/main"', f'directory = "{directory}"')
    )
    for run in (plan, stale):
        with pytest.raises(
            ValueError,
            match=rf"^fab.toml: \[\[board\]\] 'main': directory '{re.escape(directory)}'"
            rf" {re.escape(why)}: adopt edits no file there$",
        ):
            run(layout)
    assert not (tmp_path / "other/main.kicad_dru").exists()
    assert not (tmp_path / "board/hardware/lib/demo/main.kicad_dru").exists()


def test_venv_through_a_link_refused(tmp_path: Path) -> None:
    """uv venv --clear would empty a linked .venv's target: refused, the
    target kept."""
    layout = repository(tmp_path / "board", "main")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    link(tmp_path / "board/hardware/.venv", target)
    with pytest.raises(ValueError, match=r"^hardware/\.venv is a symbolic link"):
        plan(layout)
    assert (target / "pyvenv.cfg").exists()


def hook(directory: Path, name: str, executable: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("#!/bin/sh\n")
    (directory / name).chmod(0o755 if executable else 0o644)


@pytest.mark.parametrize("hooks_path", [None, ".husky"])
def test_hooks_that_stop_running_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], hooks_path: str | None
) -> None:
    """The hooks git runs now, from .git/hooks or another core.hooksPath:
    each named, in every mode, as one that stops running."""
    layout = repository(tmp_path, "main")
    if hooks_path is None:
        directory = tmp_path / ".git/hooks"
    else:
        directory = tmp_path / hooks_path
        git("-C", str(tmp_path), "config", "core.hooksPath", hooks_path)
    hook(directory, "pre-commit")
    hook(directory, "post-merge")
    hook(directory, "pre-push.sample")
    hook(directory, "notes", executable=False)
    where = ".git/hooks" if hooks_path is None else hooks_path
    warning = (
        "WARNING: core.hooksPath is to be .githooks (--yes), so these hooks git runs now from"
        f" {where} stop running: post-merge, pre-commit\n"
    )
    for run in (plan, stale):
        capsys.readouterr()
        run(layout)
        assert warning in capsys.readouterr().err


def test_no_warning_once_hooks_path_is_ours(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = repository(tmp_path, "main")
    git("-C", str(tmp_path), "config", "core.hooksPath", ".githooks")
    hook(tmp_path / ".git/hooks", "pre-commit")
    capsys.readouterr()
    plan(layout)
    assert "stop running" not in capsys.readouterr().err


def test_lines_in_the_section_not_ours_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rule the project put between the markers: --yes says it goes,
    with the count of lines and the rule's name."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    text = dru.read_text()
    inside = '(rule "Mine inside"\n\t(constraint track_width (min 0.3mm)))\n'
    dru.write_text(text.replace("# --- ee_tooling: end", f"{inside}# --- ee_tooling: end", 1))
    capsys.readouterr()
    plan(layout)
    assert (
        "hardware/main/main.kicad_dru: 2 lines between ee_tooling's markers are not"
        " ee_tooling's and go; rules: Mine inside\n"
    ) in capsys.readouterr().err


def test_bom_and_crlf_kept(tmp_path: Path) -> None:
    """.gitattributes with a byte order mark and CRLF line endings, a CRLF
    fab.toml and a .kicad_pro with a byte order mark: each written in its
    own style, the mark first; then nothing to do."""
    layout = rules_repository(tmp_path)
    attributes = tmp_path / ".gitattributes"
    attributes.write_bytes(b"\xef\xbb\xbf*.png -text\r\n")
    (tmp_path / "fab.toml").write_bytes(NO_FAB.replace("\n", "\r\n").encode())
    project = tmp_path / "hardware/main/main.kicad_pro"
    project.write_bytes(b"\xef\xbb\xbf" + project.read_bytes())
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File | Edit)], lambda *_: None)
    raw = attributes.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf# ee_tooling: begin")
    assert raw.endswith(
        b"# ee_tooling: end; the project's own lines follow and win\r\n*.png -text\r\n"
    )
    assert b"\n" not in raw.replace(b"\r\n", b"")
    fab = (tmp_path / "fab.toml").read_bytes()
    assert b"ipc_class = 2\r\n" in fab and b"\n" not in fab.replace(b"\r\n", b"")
    assert project.read_bytes().startswith(b"\xef\xbb\xbf{")
    assert json.loads(project.read_text(encoding="utf-8-sig"))["schematic"]
    assert stale(layout) == []


def test_submodule_path_with_a_space(tmp_path: Path) -> None:
    """The hooks and CI quote the submodule's path: one with a space works."""
    root = tmp_path / "board"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    layout = Layout(root, Path("hard ware/ee tooling"))
    apply(root, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    hook = root / "hard ware/ee tooling/.githooks/commit-msg"
    message = tmp_path / "message"
    message.write_text("x\n")
    missing = subprocess.run(
        ["sh", ".githooks/commit-msg", str(message)], cwd=root, capture_output=True, text=True
    )
    assert missing.returncode == 1
    assert missing.stderr == (
        "commit refused: hard ware/ee tooling/.githooks/commit-msg is missing; run git"
        " submodule update --init\n"
    )
    hook.parent.mkdir(parents=True)
    hook.write_text('echo "ran $1"\n')
    ran_hook = subprocess.run(
        ["sh", ".githooks/commit-msg", str(message)], cwd=root, capture_output=True, text=True
    )
    assert ran_hook.stdout == f"ran {message}\n"
    workflow = (root / ".github/workflows/ee-tooling.yml").read_text()
    run = next(line for line in workflow.splitlines() if 'adopt" --check' in line)
    assert shlex.split(run.split("run: ", 1)[1].strip("'")) == [
        "hard ware/ee tooling/adopt",
        "--check",
    ]
    for line in workflow.splitlines():
        if "commit-msg --" in line:
            assert shlex.split(line.strip().lstrip("| "))[:2] == [
                "sh",
                "hard ware/ee tooling/.githooks/commit-msg",
            ]


@pytest.mark.parametrize("name", ["widget", "caf\u00e9", "board \U0001f680", 'q"uote\\'])
def test_toml_string(name: str) -> None:
    """A project's name in fab.toml, whatever its characters."""
    assert tomllib.loads(f"x = {adopt.toml_string(name)}\n") == {"x": name}


def test_fab_config_with_a_name_beyond_the_bmp(tmp_path: Path) -> None:
    layout = repository(tmp_path, "rocket\U0001f680")
    config = fab_config(layout)
    assert config is not None
    assert list(load_config(config)) == ["rocket\U0001f680"]


@pytest.mark.parametrize("mode", [[], ["--check"]])
@pytest.mark.parametrize("name", ["AGENTS.md", "fab.toml", ".gitattributes"])
def test_directory_where_a_file_belongs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: list[str],
    name: str,
) -> None:
    """Named, not a traceback."""
    layout = repository(tmp_path, "main")
    (tmp_path / name).mkdir()
    monkeypatch.setattr(adopt, "lfs_missing", lambda _: None)
    code, _ = adopt_main(layout, monkeypatch, capsys, *mode)
    assert code == f"{tmp_path / name}: Is a directory; nothing done"


@pytest.mark.parametrize("suffix", [".kicad_pro", ".kicad_pcb", ".kicad_sch"])
def test_refused_while_kicad_has_the_board_open(tmp_path: Path, suffix: str) -> None:
    """KiCad's lock file beside a file it has open: no edit planned, the
    lock named; none once the rules are in."""
    layout = rules_repository(tmp_path)
    lock = tmp_path / f"hardware/main/~main{suffix}.lck"
    lock.write_text("{}")
    for run in (plan, stale):
        with pytest.raises(
            ValueError,
            match=rf"^hardware/main/~main{re.escape(suffix)}\.lck: KiCad has main's main.kicad_pro,"
            r" main.kicad_dru open, which it would overwrite; close it, then rerun\.  KiCad"
            r" leaves this lock file behind when it crashes: remove it when KiCad is not"
            r" running$",
        ):
            run(layout)
    lock.unlink()
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    lock.write_text("{}")
    assert Path("hardware/main/main.kicad_pro") not in stale(layout)


def test_check_names_a_missing_fab_toml(tmp_path: Path) -> None:
    """fab.toml gone, which --yes would write again and the release needs:
    --check fails, naming it; with no KiCad project there is none to want."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    (tmp_path / "fab.toml").unlink()
    assert ("missing", Path("fab.toml")) in pending(layout)
    empty = repository(tmp_path / "empty")
    apply(empty.root, [s for s in plan(empty) if isinstance(s, File)], lambda *_: None)
    assert Path("fab.toml") not in stale(empty)


def test_two_projects_of_one_name_refused(tmp_path: Path) -> None:
    """hardware/a/main and hardware/b/main: refused before fab.toml is
    written, naming both."""
    layout = repository(tmp_path)
    for d in ("a", "b"):
        (tmp_path / "hardware" / d).mkdir(parents=True)
        (tmp_path / "hardware" / d / "main.kicad_pro").write_text("{}")
    with pytest.raises(
        ValueError,
        match=r"^KiCad projects of one name, 'main': hardware/a/main.kicad_pro,"
        r" hardware/b/main.kicad_pro; fab.toml names a board by its project",
    ):
        plan(layout)
    assert not (tmp_path / "fab.toml").exists()


def test_lfs_ls_files_failing_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git lfs ls-files failing (not git-lfs missing): its error, not
    "nothing to fetch"."""
    layout = repository(tmp_path, "main")
    monkeypatch.setattr(
        adopt,
        "git_lfs",
        lambda *_: subprocess.CompletedProcess([], 2, "", "Error: bad index\n"),
    )
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/git-lfs")
    with pytest.raises(ValueError, match=r"^git lfs ls-files failed \(2\): Error: bad index$"):
        plan(layout)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert not adopt.lfs_unfetched(tmp_path)


def test_old_generated_header_not_taken_for_comments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """jlcpcb.toml whose only comments are the header an earlier adopt
    wrote: no note about comments."""
    header = (
        "# The boards jlcpcb-release builds; the first is the default.  Paths are\n"
        "# from the repository root.  Written by ee-tooling-adopt: check each\n"
        "# board's production directory and add its sources.\n"
    )
    adopt.migrated_config(header + OLD_CONFIG.split("\n", 1)[1].replace("colour", "x"))
    assert "comments" not in capsys.readouterr().err


# A Git LFS pointer's text.
POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:27c22de2bca1a85418f3a0e1ea8e9917745bcb75960a9a8fe526a7c1a76f3c95\n"
    "size 10\n"
)


def test_plain_blobs_at_head_said_before_the_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Archives and PDFs committed as plain blobs: the dry run says so, with
    the fix, before .gitattributes is committed; a pointer, a type the
    project keeps plain and an untracked type are not named."""
    layout = repository(tmp_path, "main")
    for name, text in {
        "production/1/b.zip": "PK zip",
        "docs/U1.PDF": "%PDF",
        "docs/plain.pdf": "%PDF",
        "lfs.zip": POINTER,
        "photo.png": "png",
        ".gitattributes": "*.pdf -filter\n",
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    commit(tmp_path, "board", ".")
    assert adopt.plain_at_head(tmp_path) == ["docs/U1.PDF", "production/1/b.zip"]
    capsys.readouterr()
    plan(layout)
    assert (
        "2 files at HEAD of types Git LFS tracks are plain blobs (docs/U1.PDF,"
        " production/1/b.zip): once .gitattributes is committed,"
        " hardware/ee_tooling/adopt --check fails on them.  Commit them as pointers with"
        " adopt's files: after --yes, git add -A && git add --renormalize . and commit\n"
    ) in capsys.readouterr().err


def test_no_plain_blobs_no_word(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    layout = repository(tmp_path, "main")
    plan(layout)
    assert "plain blobs" not in capsys.readouterr().err


@needs_lfs
def test_library_submodule_pointers_named_with_the_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A library submodule whose STEP files are pointers: named, with the
    commands that fetch them, which do."""
    library = lfs_clone(tmp_path / "library")
    origin = (tmp_path / "library/origin.git").as_uri()
    layout = repository(tmp_path / "board", "main")
    git("-C", str(layout.root), "submodule", "add", "-q", "-b", "main", origin, "hardware/lib")
    lib = layout.root / "hardware/lib"
    assert (lib / "hardware/main/u1.step").read_text().startswith("version https://git-lfs")
    capsys.readouterr()
    plan(layout)
    err = capsys.readouterr().err
    command = "git -C hardware/lib lfs install --local --skip-repo && git -C hardware/lib lfs pull"
    assert (
        "hardware/lib: 1 files Git LFS tracks are pointers, not fetched (adopt sets up this"
        f" clone only): {command}\n" in err
    )
    subprocess.run(command, shell=True, cwd=layout.root, check=True, capture_output=True)
    assert (lib / "hardware/main/u1.step").read_text() == "ISO-10303-21;"
    capsys.readouterr()
    plan(layout)
    assert "pointers, not fetched" not in capsys.readouterr().err
    assert library.root.exists()


def test_retired_rules_below_the_marker_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    dru.write_text(
        dru.read_text() + '(rule "JLCPCB via diameter"\n\t(constraint via_diameter (min 0.25mm)))\n'
    )
    capsys.readouterr()
    plan(layout)
    assert (
        "hardware/main/main.kicad_dru: 1 rule outside ee_tooling's markers is retired from"
        " ee_tooling's rules and still in force:\n  JLCPCB via diameter\n"
    ) in capsys.readouterr().err


def test_section_value_change_not_said_to_drop_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A section ee_tooling changed (a value, a retired rule): replaced
    without a word about lines that go."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    old = dru.read_text().replace("(min 0.18mm)", "(min 0.15mm)", 1)
    retired = '(rule "JLCPCB track width"\n\t(constraint track_width (min 0.1mm)))\n'
    dru.write_text(old.replace("# --- ee_tooling: end", f"{retired}# --- ee_tooling: end", 1))
    capsys.readouterr()
    assert Path("hardware/main/main.kicad_dru") in stale(layout)
    assert "between ee_tooling's markers" not in capsys.readouterr().err


def test_agents_hard_linked_to_claude(tmp_path: Path) -> None:
    """AGENTS.md a hard link of CLAUDE.md: both names get the section."""
    layout = repository(tmp_path, "main")
    (tmp_path / "CLAUDE.md").write_text("# Mine\n")
    os.link(tmp_path / "CLAUDE.md", tmp_path / "AGENTS.md")
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    assert (tmp_path / "CLAUDE.md").read_text() == (tmp_path / "AGENTS.md").read_text()
    assert "ee_tooling: begin" in (tmp_path / "CLAUDE.md").read_text()


def test_linked_file_in_a_read_only_directory_refused_first(tmp_path: Path) -> None:
    """AGENTS.md a link to docs/CLAUDE.md, docs read-only: named before
    anything is done."""
    layout = repository(tmp_path, "main")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CLAUDE.md").write_text("# Mine\n")
    link(tmp_path / "AGENTS.md", Path("docs/CLAUDE.md"))
    docs.chmod(0o555)
    try:
        steps = plan(layout)
        with pytest.raises(ValueError, match=r"^docs cannot be written: nothing done$"):
            apply(tmp_path, steps, lambda *_: pytest.fail("ran"))
        assert not (tmp_path / ".githooks").exists()
    finally:
        docs.chmod(0o755)


@pytest.mark.parametrize(
    "path",
    [
        "hw $(touch PWNED)/ee_tooling",
        "hw `touch PWNED`/ee_tooling",
        'hw "x"/ee_tooling',
        "hw 'x'/ee_tooling",
        "hw;touch PWNED/ee_tooling",
        "hw\ntouch PWNED/ee_tooling",
        "hw\\x/ee_tooling",
    ],
)
def test_submodule_path_with_shell_characters_refused(tmp_path: Path, path: str) -> None:
    """A path the hooks or the workflow would run as code: refused before
    any template is written, in every mode; nothing runs."""
    root = tmp_path / "board"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    layout = Layout(root, Path(path))
    for run in (plan, stale):
        with pytest.raises(ValueError, match=r"^the submodule's path .* cannot carry safely"):
            run(layout)
    assert not (root / ".githooks").exists()
    assert not (root / ".github").exists()
    assert not list(tmp_path.rglob("PWNED"))


def test_commands_in_messages_quoted(tmp_path: Path) -> None:
    """A submodule path with a space: the adopt command a message gives is
    one shell word."""
    assert adopt.adopt_command(Path("hard ware/ee tooling")) == "'hard ware/ee tooling/adopt'"
    assert adopt.adopt_command(SUBMODULE) == "hardware/ee_tooling/adopt"


@pytest.mark.parametrize("name", ["../sibling/main", "..", ".", "a/b", "a\\b"])
def test_board_name_not_one_path_component_refused(tmp_path: Path, name: str) -> None:
    """A name that would build a path elsewhere: refused, nothing planned
    there."""
    layout = repository(tmp_path / "board", "main")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "main.kicad_pro").write_text("{}")
    config = (fab_config(layout) or "").replace('name = "main"', f"name = {json.dumps(name)}")
    config = config.replace('directory = "hardware/main"', 'directory = "."')
    (tmp_path / "board/fab.toml").write_text(config)
    for run in (plan, stale):
        with pytest.raises(
            ValueError, match=r"a board's name is its project's file name, one path"
        ):
            run(layout)
    assert (sibling / "main.kicad_pro").read_text() == "{}"


def test_board_file_linked_out_of_the_repository_refused(tmp_path: Path) -> None:
    """The board's .kicad_pro a link to another repository's: refused."""
    layout = repository(tmp_path / "board", "main")
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.kicad_pro").write_text("{}")
    project = tmp_path / "board/hardware/main/main.kicad_pro"
    project.unlink()
    project.symlink_to(other / "x.kicad_pro")
    (tmp_path / "board/fab.toml").write_text(fab_config(layout) or "")
    with pytest.raises(
        ValueError, match=r"hardware/main/main.kicad_pro is outside the repository: adopt edits"
    ):
        plan(layout)


@needs_lfs
def test_documented_upgrade_of_a_v0_2_2_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """jlcpcb.toml, the old skill and a plain zip committed, as a v0.2.2
    board repository has them: adopt --yes, then the documented
    `git add -A && git add --renormalize .` and a commit; --check passes."""
    layout = adopted(tmp_path, "main")
    (tmp_path / "production/1").mkdir(parents=True)
    (tmp_path / "production/1/b.zip").write_text("PK zip")
    commit(tmp_path, "v0.2.2 board", ".")
    assert adopt_main(layout, monkeypatch, capsys, "--yes")[0] is None
    assert not (tmp_path / "jlcpcb.toml").exists()
    alone = subprocess.run(
        ["git", "add", "--renormalize", "."], cwd=tmp_path, capture_output=True, text=True
    )
    assert alone.returncode == 128 and "unable to stat" in alone.stderr
    subprocess.run("git add -A && git add --renormalize .", shell=True, cwd=tmp_path, check=True)
    git("-C", str(tmp_path), "commit", "-q", "--no-verify", "-m", "ee_tooling 0.4.0")
    code, err = adopt_main(layout, monkeypatch, capsys, "--check")
    assert code == 0, err


# A hook as git-lfs 3.7.1 installs it, by its name.
LFS_HOOK = (
    "#!/bin/sh\n"
    'command -v git-lfs >/dev/null 2>&1 || { printf >&2 "\\n%s\\n\\n" "This repository is'
    " configured for Git LFS but 'git-lfs' was not found on your path. If you no longer wish to"
    " use Git LFS, remove this hook by deleting the '{name}' file in the hooks directory (set"
    " by 'core.hookspath'; usually '.git/hooks').\"; exit 2; }}\n"
    'git lfs {name} "$@"\n'
)


def test_git_lfs_stock_hooks_not_warned_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh clone's .git/hooks, git-lfs's own: no warning; a hook of
    the project's beside them still named."""
    layout = repository(tmp_path, "main")
    hooks = tmp_path / ".git/hooks"
    for name in ("pre-push", "post-checkout", "post-commit", "post-merge"):
        (hooks / name).write_text(LFS_HOOK.replace("{name}", name).replace("}}", "}"))
        (hooks / name).chmod(0o755)
    capsys.readouterr()
    plan(layout)
    assert "stop running" not in capsys.readouterr().err
    (hooks / "post-merge").write_text(
        LFS_HOOK.replace("{name}", "post-checkout").replace("}}", "}")
    )
    hook(hooks, "pre-commit")
    plan(layout)
    assert "stop running: post-merge, pre-commit\n" in capsys.readouterr().err


def test_git_lfs_stock_pre_push_replaced_quietly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """.githooks/pre-push as git-lfs installs it: not the project's own."""
    layout = repository(tmp_path, "main")
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks/pre-push").write_text(
        LFS_HOOK.replace("{name}", "pre-push").replace("}}", "}")
    )
    capsys.readouterr()
    plan(layout)
    assert "WARNING" not in capsys.readouterr().err


def test_library_outside_the_repository_warned_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A board's fp-lib-table naming a library beside the repository: the
    dry run and --check warn, pointing at the recipe; --check still passes
    on it."""
    ran(tmp_path)
    layout = Layout(tmp_path, SUBMODULE)
    (tmp_path / "hardware/main/fp-lib-table").write_text(
        '(fp_lib_table (lib (name "a")(type "KiCad")(uri "${KIPRJMOD}/../../../a.pretty")))\n'
    )
    warning = (
        "warning: [[board]] 'main': libraries outside the repository, which fab-release"
        " refuses: ${KIPRJMOD}/../../../a.pretty; move each in"
        " (hardware/ee_tooling/docs/adopting.md, Moving a library into the repository)\n"
    )
    capsys.readouterr()
    plan(layout)
    assert warning in capsys.readouterr().err
    assert Path("hardware/main/fp-lib-table") not in stale(layout)
    assert warning in capsys.readouterr().err


def test_board_directory_through_a_link_inside(tmp_path: Path) -> None:
    """hardware/main a link to boards/main, no .kicad_dru yet: the rules
    written there, the same on the first run as on the next."""
    layout = rules_repository(tmp_path)
    board = tmp_path / "hardware/main"
    (tmp_path / "boards").mkdir()
    board.rename(tmp_path / "boards/main")
    board.symlink_to(Path("../boards/main"))
    (tmp_path / "boards/main/main.kicad_dru").unlink()
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert "ee_tooling: jlcpcb rules" in (tmp_path / "boards/main/main.kicad_dru").read_text()
    assert board.is_symlink()
    assert [s for s in plan(layout) if isinstance(s, Edit)] == []


def test_small_binary_blob_at_head(tmp_path: Path) -> None:
    """A committed .tgz under 1024 bytes, not UTF-8: read as bytes, named
    as a plain blob, no error."""
    repository(tmp_path, "main")
    (tmp_path / "a.tgz").write_bytes(b"\x1f\x8b\x08\x00\xff\xfe\x00binary")
    commit(tmp_path, "board", ".")
    assert adopt.plain_at_head(tmp_path) == ["a.tgz"]


@needs_lfs
def test_library_submodule_with_a_space_in_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """hardware/my lib: found, named, its command quoted."""
    lfs_clone(tmp_path / "library")
    origin = (tmp_path / "library/origin.git").as_uri()
    layout = repository(tmp_path / "board", "main")
    git("-C", str(layout.root), "submodule", "add", "-q", "-b", "main", origin, "hardware/my lib")
    capsys.readouterr()
    plan(layout)
    assert (
        "hardware/my lib: 1 files Git LFS tracks are pointers, not fetched (adopt sets up this"
        " clone only): git -C 'hardware/my lib' lfs install --local --skip-repo && git -C"
        " 'hardware/my lib' lfs pull\n"
    ) in capsys.readouterr().err


def test_leftover_in_the_old_skill_removed_before_the_move(tmp_path: Path) -> None:
    """A kill during the skill's move left a scratch file in the old
    directory: one --yes removes it, then moves the directory."""
    layout = adopted(tmp_path, "main")
    left = tmp_path / ".claude/skills/jlcpcb-release/.SKILL.md.4242.ee_tooling-tmp"
    left.write_text("partial")
    apply(tmp_path, plan(layout), lambda *_: None)
    new = tmp_path / ".claude/skills/fab-release"
    assert new.is_dir() and not (tmp_path / ".claude/skills/jlcpcb-release").exists()
    assert not (new / left.name).exists()
    assert stale(layout) == []


def test_in_style_keeps_each_line_ending(tmp_path: Path) -> None:
    """Each line the file had keeps its own ending; a new one takes the
    file's more common; a lone CR is left; the mark stays first."""
    target = tmp_path / "f"
    target.write_bytes(b"\xef\xbb\xbfa\r\nb\nc\r\nd\re\r\n")
    text = adopt.read(target)
    assert text == "a\nb\nc\nd\re\n"
    out = adopt.in_style(target, "new\na\nb\nc\nd\re\n")
    assert out.encode() == b"\xef\xbb\xbfnew\r\na\r\nb\nc\r\nd\re\r\n"
    target.write_bytes(b"x\ny\n")
    assert adopt.in_style(target, "x\nz\ny\n") == "x\nz\ny\n"
    target.write_bytes(b"")
    assert adopt.in_style(target, "s\n") == "s\n"


def test_empty_agents_gets_just_the_section(tmp_path: Path) -> None:
    layout = repository(tmp_path, "main")
    (tmp_path / "AGENTS.md").write_text("")
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    assert (tmp_path / "AGENTS.md").read_text().startswith(AGENTS_BEGIN)


def test_mixed_line_endings_kept(tmp_path: Path) -> None:
    """.gitattributes with mixed endings and a lone CR in a line: the
    project's lines as they were, byte for byte; then nothing to do."""
    layout = repository(tmp_path, "main")
    own = b"*.png -text\r\n*.jpg -text\n# a\rb\r\n"
    (tmp_path / ".gitattributes").write_bytes(own)
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, File)], lambda *_: None)
    raw = (tmp_path / ".gitattributes").read_bytes()
    assert raw.endswith(b"follow and win\r\n" + own)
    assert Path(".gitattributes") not in stale(layout)


# The first rule set's section, which a board pinned at it has.
FIRST_SECTION = installed("jlcpcb", Jlcpcb.rules, Options(), "0.4.0")


def pin(root: Path, version: object) -> None:
    """fab.toml's board pinned at version (a TOML value), or unpinned
    (None)."""
    config = root / "fab.toml"
    text = re.sub(r"^rules = .*\n", "", config.read_text(), flags=re.M)
    if version is not None:
        line = f"rules = {json.dumps(version)}\n"
        text = text.replace('profile = "jlcpcb"\n', f'profile = "jlcpcb"\n{line}', 1)
    config.write_text(text)


def setup_values(root: Path) -> tuple[float, str]:
    """The board's Board Setup clearance and field_name_whitespace's
    severity: each set's are different."""
    project = json.loads((root / "hardware/main/main.kicad_pro").read_text())
    return (
        project["board"]["design_settings"]["rules"]["min_clearance"],
        project["erc"]["rule_severities"]["field_name_whitespace"],
    )


def test_pinned_board_keeps_its_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], newer_set: str
) -> None:
    """A board pinned at an older set: --yes installs that set's section,
    Board Setup and ERC values; --check then passes with a newer set
    current, saying the pin; a section changed by hand is to be updated."""
    layout = rules_repository(tmp_path)
    pin(tmp_path, "0.4.0")
    dru = tmp_path / "hardware/main/main.kicad_dru"
    said = (
        f"main: ee_tooling's rules pinned at 0.4.0 by fab.toml; the current rules are {newer_set}"
    )
    capsys.readouterr()
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert said in capsys.readouterr().err.splitlines()
    assert dru.read_text() == f"(version 1)\n{FIRST_SECTION}\n{OWN_RULE}"
    assert setup_values(tmp_path) == (0.1, "warning")
    assert stale(layout) == []
    assert said in capsys.readouterr().err.splitlines()
    dru.write_text(dru.read_text().replace("(min 0.18mm)", "(min 0.05mm)", 1))
    assert stale(layout) == [Path("hardware/main/main.kicad_dru")]


def test_pin_switched(tmp_path: Path, capsys: pytest.CaptureFixture[str], newer_set: str) -> None:
    """Unpinned, a board pinned at an older set is to be updated to the
    current one, its section naming it; pinned back, only its section goes
    back: Board Setup is not lowered nor an ERC severity made laxer, and
    no rule of either set is taken for one put there by hand."""
    layout = rules_repository(tmp_path)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    pin(tmp_path, "0.4.0")
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    pin(tmp_path, None)
    assert stale(layout) == [
        Path("hardware/main/main.kicad_dru"),
        Path("hardware/main/main.kicad_pro"),
    ]
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    newer = installed("jlcpcb", Jlcpcb.rules, Options(), newer_set)
    assert newer.splitlines()[1] == f"# ee_tooling rule set {newer_set}"
    assert dru.read_text() == f"(version 1)\n{newer}\n{OWN_RULE}"
    assert setup_values(tmp_path) == (0.12, "error")
    assert stale(layout) == []
    pin(tmp_path, "0.4.0")
    assert stale(layout) == [Path("hardware/main/main.kicad_dru")]
    capsys.readouterr()
    apply(tmp_path, [s for s in plan(layout) if isinstance(s, Edit)], lambda *_: None)
    assert "not ee_tooling's" not in capsys.readouterr().err
    assert dru.read_text() == f"(version 1)\n{FIRST_SECTION}\n{OWN_RULE}"
    assert setup_values(tmp_path) == (0.12, "error")
    assert stale(layout) == []


def test_pinned_at_the_current_set_not_said(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], newer_set: str
) -> None:
    layout = rules_repository(tmp_path)
    pin(tmp_path, newer_set)
    capsys.readouterr()
    plan(layout)
    assert "pinned" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "error"),
    [("0.3.0", r"rules '0\.3\.0' is not one of 0\.4\.0, 99\.0\.0"), (4, "'rules' is not a string")],
)
def test_pin_not_a_set(tmp_path: Path, newer_set: str, value: object, error: str) -> None:
    layout = rules_repository(tmp_path)
    pin(tmp_path, value)
    with pytest.raises(ValueError, match=rf"^fab.toml: \[\[board\]\] 'main': {error}$"):
        stale(layout)


@pytest.mark.parametrize(
    ("version", "kind"),
    [
        ("0.4.0", "below ee_tooling's end marker copies"),
        (None, "outside ee_tooling's markers is retired"),
    ],
)
def test_retired_by_the_pinned_set(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    newer_set: str,
    version: str | None,
    kind: str,
) -> None:
    """A rule the newer set retired, below the end marker: a copy of the
    section's for a board pinned at a set that has it, retired for one on
    the newer set."""
    layout = rules_repository(tmp_path)
    pin(tmp_path, version)
    dru = tmp_path / "hardware/main/main.kicad_dru"
    dru.write_text(dru.read_text() + '(rule "JLCPCB BGA pad size"\n\t(constraint assertion "1"))\n')
    capsys.readouterr()
    plan(layout)
    assert f"1 rule {kind}" in capsys.readouterr().err
