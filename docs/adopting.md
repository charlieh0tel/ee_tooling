# Adopting ee_tooling in a board repository

A board repository takes ee_tooling as a git submodule at
`hardware/ee_tooling`, pinned to a release or a commit
(`docs/releasing.md`).  The paths below assume that location and a
`hardware/` directory holding the KiCad projects.  oncore-emulator's
commit a8d13f5 is a worked example.

## Submodule

```
git submodule add https://github.com/charlieh0tel/ee_tooling.git hardware/ee_tooling
git -C hardware/ee_tooling checkout <vX.Y.Z or commit>
git add .gitmodules hardware/ee_tooling
```

To move the pin, check out another release or commit in
`hardware/ee_tooling` and commit the new pointer.  A change to
ee_tooling is made and committed in ee_tooling, then the pointer moved.
A fresh clone needs `git submodule update --init`.

## hardware/pyproject.toml

ee_tooling is an editable path dependency of the hardware tools'
environment:

```toml
[project]
name = "<board>-hardware"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["ee-tooling"]

[tool.uv.sources]
ee-tooling = { path = "ee_tooling", editable = true }
```

`uv lock`, then `uv run jlcpcb-release` from `hardware/`.  Scripts that
import KiCad's `pcbnew` need it from the system Python, so the venv is
made with:

```
uv venv --system-site-packages --python /usr/bin/python3
```

## jlcpcb.toml

At the repository root, one `[[board]]` per board.
`skills/jlcpcb-release/SKILL.md` gives its keys.

## AGENTS.md

Import the house style, so an agent reads it:

```
@hardware/ee_tooling/docs/kicad-style.md
```

State there that `hardware/ee_tooling` is the submodule, changed in
ee_tooling and then pinned here, and how the `hardware/` venv is made.

State there too: before writing new tooling in the board repository (a
script, a checker, a skill, a style rule), consider whether it could be
made generic and generally useful.  If so, ask whether it should be
made in ee_tooling instead, then pinned here.

## Project skill

`.claude/skills/jlcpcb-release/SKILL.md` holds only the boards'
specifics: which board names `jlcpcb.toml` has, the ordering checklist,
the corrections found in JLCPCB's preview.  It says to read
`hardware/ee_tooling/skills/jlcpcb-release/SKILL.md` first for how the
tool works.

## Commit-msg hook

ee_tooling's `.githooks/commit-msg` refuses a commit that credits an AI
assistant, by message or by author or committer.  The board repository
runs it from the submodule, not a copy:

```
mkdir -p .githooks
cp hardware/ee_tooling/scripts/consumer-commit-msg .githooks/commit-msg
git config core.hooksPath .githooks       # once per clone
```

`EE_TOOLING` in the wrapper is the submodule's path.  Git runs the hook
from the top of the work tree, whatever the directory of the commit.
Before `git submodule update --init` the hook is missing, and the
wrapper refuses every commit, saying so.  `make hook-test` in ee_tooling
tests the wrapper.

## CI

The checkout needs the submodule.  The attribution job runs the hook's
checks over the whole history, so a commit made in a clone without the
hook still fails CI; every commit already in the history must pass:

```yaml
  attribution:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          submodules: true
      - run: >-
          git log --format=%B HEAD
          | sh hardware/ee_tooling/.githooks/commit-msg --messages /dev/stdin
      - run: >-
          git log --format='%an <%ae>%n%cn <%ce>' HEAD
          | sh hardware/ee_tooling/.githooks/commit-msg --identities /dev/stdin
```

Any other job that runs ee_tooling's tools needs `submodules: true` on
its checkout too.
