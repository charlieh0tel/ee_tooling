import os
import subprocess
import sys
from pathlib import Path

# A test that runs git init, git commit and git tag in its own directory.
GIT_TEST = "tests/test_fab_release.py::test_tag"
ROOT = Path(__file__).resolve().parents[1]


def scratch(path: Path) -> str:
    """A repository at path; its config."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return (path / ".git/config").read_text()


def run_with_git_dir(tmp_path: Path, git_dir: Path, *options: str) -> int:
    """GIT_TEST in a pytest of its own, GIT_DIR naming git_dir, as a hook or
    `git rebase -x` would set it."""
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "inner"),
            *options,
            GIT_TEST,
        ],
        cwd=ROOT,
        env={**os.environ, "GIT_DIR": str(git_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode


def refs(git_dir: Path) -> str:
    """The repository's commits' subjects and its tags."""
    return subprocess.run(
        ["git", "--git-dir", str(git_dir), "log", "--all", "--format=%s %D"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def test_suite_leaves_a_named_repository_alone(tmp_path: Path) -> None:
    """With GIT_DIR naming a repository, the suite's git commands act on
    their own repositories, not it."""
    repository = tmp_path / "named"
    config = scratch(repository)
    assert run_with_git_dir(tmp_path, repository / ".git") == 0
    assert (repository / ".git/config").read_text() == config
    assert refs(repository / ".git") == ""


def test_without_the_fixture_the_named_repository_is_changed(tmp_path: Path) -> None:
    """What the fixture prevents: the same test without conftest.py commits
    in the named repository."""
    repository = tmp_path / "named"
    scratch(repository)
    run_with_git_dir(tmp_path, repository / ".git", "--noconftest")
    assert refs(repository / ".git").startswith("one ")
