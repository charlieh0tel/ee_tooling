"""Every test's git: its own repositories only."""

import subprocess
from pathlib import Path

import pytest

# git's variables that name a repository, its work tree, index or objects
# (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...), set by a hook or by
# `git rebase -x` running the suite: with one of them set, a test's
# `git init` or `git config` would act on that repository.
LOCAL_VARIABLES = subprocess.run(
    ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
).stdout.split()


@pytest.fixture(autouse=True)
def git_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No repository named by the environment; an empty global git config
    and no system one, so neither the machine's nor the user's (git-lfs's
    filters among them) is read and none is written; no repository found
    above pytest's temporary directory.  Returns the global config's
    path."""
    for name in LOCAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path_factory.mktemp("home") / "gitconfig"
    path.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # With no configuration git has no identity to commit or tag with.
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path_factory.getbasetemp()))
    return path
