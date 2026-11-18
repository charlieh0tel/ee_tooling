"""The environment of the git commands ee_tooling runs."""

import functools
import os
import subprocess


@functools.cache
def local_variables() -> tuple[str, ...]:
    """git's variables that name a repository, its work tree, index or
    objects (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...):
    `git rev-parse --local-env-vars`."""
    r = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
    )
    return tuple(r.stdout.split())


def environment() -> dict[str, str]:
    """os.environ without git's local variables: each git command ee_tooling
    runs finds its repository from the directory it runs in, never from a
    caller's GIT_DIR or GIT_INDEX_FILE (a hook's, `git rebase -x`'s), which
    may be relative to another directory or name another repository."""
    drop = set(local_variables())
    return {k: v for k, v in os.environ.items() if k not in drop}
