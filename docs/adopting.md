# Adopting ee_tooling in a board repository

A board repository takes ee_tooling as a git submodule, following
ee_tooling's `main`, in the directory that holds the KiCad projects
and the hardware tools' uv project (`hardware/` here).  The
submodule's `adopt` script puts the rest in place.

## Running it

From the board repository's root:

```
git submodule add -b main https://github.com/charlieh0tel/ee_tooling.git hardware/ee_tooling
hardware/ee_tooling/adopt          # prints what it would do
hardware/ee_tooling/adopt --yes    # does it
```

`adopt` runs `ee-tooling-adopt` in the submodule's own uv project
(`hardware/ee_tooling/.venv`, ignored by ee_tooling's `.gitignore`).
It finds the board repository as the submodule's superproject, so it
runs from any directory, and the submodule's parent is the hardware
tools' uv project.  Rerun with `--yes` to bring the files it manages up
to date; `--check` fails if one is stale, or if a file below is still to
be migrated.  A fresh clone needs `git submodule update --init` first.

The submodule's parent need not be `hardware/`.  With the submodule at
`ee_tooling/` and the KiCad project at the repository root, the hardware
tools' uv project is the root: `pyproject.toml`, `.venv` and the
`./ee_tooling` editable source are there, and `fab.toml` has
`directory = "."`.  (`tests/test_adopt.py`, `test_root_layout`.)

## What it writes

The templates are in `src/ee_tooling/templates/`.  A managed file is
rewritten by every run with `--yes`; a project file is written only if
missing, then the project's.

| Path | Owner | What |
|---|---|---|
| `.githooks/commit-msg` | managed | runs ee_tooling's commit-msg hook from the submodule |
| `.github/workflows/ee-tooling.yml` | managed | CI: the hook's checks over the whole history, and `--check` |
| `AGENTS.md` | managed section | between `ee_tooling: begin` and `end` markers; the rest of the file is left as it is |
| `hardware/pyproject.toml` | project | the hardware tools' uv project |
| `fab.toml` | project | one `[[board]]` per `*.kicad_pro` under `hardware/`, profile `jlcpcb`, with the profile's house `[board.fab]` values |
| `.claude/skills/fab-release/SKILL.md` | project | the boards' specifics, pointing at ee_tooling's skill |
| each board's `NAME.kicad_dru` | managed section | its profile's rules between `ee_tooling` markers, the board's own rules after them (`docs/kicad-rules.md`); a new file if it has none |
| each board's `NAME.kicad_pro` | project | Board Setup's design rule minimums raised to the profile's, never lowered; the ERC severities set from `erc_severities.json`, each kept where the board's is stricter, and the connection grid (`docs/kicad-rules.md`) |

Close the boards in KiCad before `--yes`: KiCad does not reload a
`.kicad_dru` or `.kicad_pro` changed under it, and saving from KiCad
overwrites the change.  `--check` fails while a board's rules section
is missing or out of date, its Board Setup is below the profile's
minimums, or its ERC severities or connection grid differ as above.  A board whose profile is not known, or whose `.kicad_pro`
is not there, is left, said on stderr.  The boards are `fab.toml`'s,
or, on the run that writes it, those it is written with.

It finds the KiCad projects (`*.kicad_pro`) under `hardware/`, skipping
the submodule, every directory whose name starts with `.` (`.git`,
`.venv`, `.claude/`'s worktrees) and every production directory
(`production/`, and each `production` that `jlcpcb.toml` or `fab.toml`
names), so a project file in a release or a worktree is not taken for a
board.

## Migration

A repository adopted before `fab.toml` is migrated by `--yes`, the one
time `adopt` changes files the project owns:

- `jlcpcb.toml`, with no `fab.toml`: `fab.toml` is written from its
  boards, each key kept and `profile = "jlcpcb"` added, and
  `jlcpcb.toml` is removed.  Each board gets the profile's house
  `[board.fab]` values.  `copper` is dropped: the copper layers are
  the stackup's.  Its comments are not carried over, nor a
  key `fab.toml` does not have, each named on stderr.  A `jlcpcb.toml`
  that does not parse, or lacks a key, stops `adopt`, naming it.
- `.claude/skills/jlcpcb-release/`, with no `.claude/skills/fab-release/`:
  moved there, and `jlcpcb-release` and `jlcpcb.toml` in its `.md` files
  renamed `fab-release` and `fab.toml`.
- With both the old and the new there, the old is left as it is, named
  on stderr.
- `fab.toml` with a board lacking a `[board.fab]` key: each key it
  lacks is added, with its profile's house value, under the board's
  `[board.fab]` (added after the board's other lines if it has none);
  the rest of the file, comments too, is kept.  `--check` fails until
  then.  A `fab` given otherwise (an inline table, dotted keys) stops
  `adopt`, naming it: add the keys by hand.

The `AGENTS.md` section imports `docs/kicad-style.md`, says how the
submodule and venv are kept, and says: before writing new tooling in
the board repository (a script, a checker, a skill, a style rule),
consider whether it could be made generic and generally useful; if so,
ask whether it should be made in ee_tooling instead.  An `AGENTS.md`
that already imports the house style outside the markers was adopted
by hand and is left alone.

What it runs, each only when needed:

- `uv venv --system-site-packages --python /usr/bin/python3` in
  `hardware/`, with `--clear` for a venv without system packages:
  uv has no project setting for it, and KiCad's `pcbnew` is only in
  the system Python.  A venv uv remakes itself (after `.venv` is
  deleted) has no `pcbnew`; rerun `adopt --yes`.  The system Python
  must be 3.12 or later (`docs/stitch.md`, Platforms); where it is
  older, or missing, `adopt` says so and makes no venv on it, and
  `uv add` makes one on a Python of uv's;
- `uv add --dev --editable --no-workspace ./ee_tooling` in `hardware/`;
- `git config core.hooksPath .githooks`, once per clone.

## What is left to do

- After a migration, check `fab.toml` and the moved skill, and look for
  `jlcpcb-release` and `jlcpcb.toml` elsewhere in the repository.
- `fab.toml`: each board's production directory, `sources`, profile and
  `[board.fab]` values (`skills/fab-release/SKILL.md` gives the keys).
- Each board's stackup, in Board Setup, Physical Stackup: the release
  takes the copper layers from it, and refuses a board without one; and
  its stackup table on User.Drawings, which `stackup-table` writes: the
  release refuses a board without one matching the stackup.
- The skill's ordering checklist and part corrections.
- Any other CI job that runs ee_tooling's tools needs `submodules: true`
  on its checkout.
- The attribution job fails on any commit already in the history that
  credits an AI assistant.
- Commit the result, `hardware/uv.lock` with it.

## Hook

Git runs a hook from the top of the work tree, whatever the directory
of the commit, so the wrapper's path is from there.  Before
`git submodule update --init` the hook is missing, and the wrapper
refuses every commit, saying so.  `make hook-test` in ee_tooling tests
the wrapper in a scratch repository.

## Updating

A change to ee_tooling is made and pushed in ee_tooling.  Then, in the
board repository:

```
git submodule update --remote hardware/ee_tooling
hardware/ee_tooling/adopt --yes
```

and commit the new pointer with any files it rewrote, and with
`hardware/uv.lock` if it changed.  `hardware/uv.lock` records the
submodule's version and dependencies, so a bump that changes either
changes it: the first `uv run` in `hardware/` after the bump rewrites
it (`uv lock --check` there says whether it is current).  `adopt --yes`
does not: its `uv add` runs only when `hardware/pyproject.toml` lacks
ee_tooling.  `fab-release` refuses an uncommitted `uv.lock` in a board's
directory or `sources` (the repository-root layout, `directory = "."`).
