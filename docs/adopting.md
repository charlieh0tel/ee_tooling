# Adopting ee_tooling in a board repository

A board repository takes ee_tooling as a git submodule at
`hardware/ee_tooling`, following ee_tooling's `main`, with a
`hardware/` directory holding the KiCad projects and the hardware
tools' uv project.  `ee-tooling-adopt` puts it in place.

## Running it

The first time, from anywhere in the repository:

```
uvx --from git+https://github.com/charlieh0tel/ee_tooling ee-tooling-adopt
uvx --from git+https://github.com/charlieh0tel/ee_tooling ee-tooling-adopt --yes
```

The first prints what it would do; `--yes` does it.  After that, from
`hardware/`, `uv run ee-tooling-adopt` (and `--yes`) brings the files
it manages up to date, and `--check` fails if one is stale.  `--path`
puts the submodule elsewhere (default `hardware/ee_tooling`); its
parent is the hardware tools' uv project.

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
| `jlcpcb.toml` | project | one `[[board]]` per `*.kicad_pro` under `hardware/` |
| `.claude/skills/jlcpcb-release/SKILL.md` | project | the boards' specifics, pointing at ee_tooling's skill |

The `AGENTS.md` section imports `docs/kicad-style.md`, says how the
submodule and venv are kept, and says: before writing new tooling in
the board repository (a script, a checker, a skill, a style rule),
consider whether it could be made generic and generally useful; if so,
ask whether it should be made in ee_tooling instead.  An `AGENTS.md`
that already imports the house style outside the markers was adopted
by hand and is left alone.

What it runs, each only when needed:

- `git submodule add -b main` (or `git submodule update --init` in a
  clone that has the submodule registered);
- `uv venv --system-site-packages --python /usr/bin/python3` in
  `hardware/`, with `--clear` for a venv without system packages:
  uv has no project setting for it, and KiCad's `pcbnew` is only in
  the system Python.  A venv uv remakes itself (after `.venv` is
  deleted) has no `pcbnew`; rerun `ee-tooling-adopt --yes`;
- `uv add --dev --editable --no-workspace ./ee_tooling` in `hardware/`;
- `git config core.hooksPath .githooks`, once per clone.

## What is left to do

- `jlcpcb.toml`: each board's production directory and `sources`, and
  its copper layers if the board file was missing
  (`skills/jlcpcb-release/SKILL.md` gives the keys).
- The skill's ordering checklist and part corrections.
- Any other CI job that runs ee_tooling's tools needs `submodules: true`
  on its checkout.
- The attribution job fails on any commit already in the history that
  credits an AI assistant.
- Commit the result.

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
cd hardware && uv run ee-tooling-adopt --yes
```

and commit the new pointer with any files it rewrote.
