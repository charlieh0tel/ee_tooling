# Releasing

A release is a tag; nothing is published to a registry.  A board
repository takes ee_tooling as a git submodule following `main` or a
release line (`docs/adopting.md`); a release names a version of its
line.  `main` is the current line.  Each earlier line has a branch,
`release/0.1` and `release/0.2`, that takes fixes only.

## Versions and tags

- Semantic versioning from 0.1.0, one version for the repository:
  `version` in `pyproject.toml`, and in each Rust crate once there are
  any.  Before 1.0, a breaking change bumps the minor version; anything
  else the patch.
- A breaking change is one a board repository must act on: a command,
  option, `fab.toml` key, Python or Rust API, skill or output file
  that is removed, renamed or changed in meaning.  A change of the
  rules (a new rule set) is breaking for a board that follows the
  current set, not for one that pins its rules (`docs/kicad-rules.md`,
  Pinning a board's rules).
- A release is an annotated tag `vX.Y.Z` on a commit of `main` that
  has passed every check.  Its notes, breaking changes first, are its
  GitHub release's.

## Cutting a release

1. Pick the version from the changes since the last release (`git log
   vX.Y.Z..main`).
2. Bump `version` in `pyproject.toml` (and the crates'); `uv lock`
   updates `uv.lock`.
3. Draft the release notes: what changed for a board repository,
   breaking changes first, each listed precisely.
4. Run every check: `make hook-test`, `uv run ruff format --check`,
   `uv run ruff check`, `make mypy`, `uv run pytest`, and, once there
   is Rust, `cargo fmt --check`, `cargo clippy`, `cargo test`.
5. Commit `pyproject.toml` and `uv.lock` as "Release X.Y.Z" and merge
   it to `main`.
6. Wait for CI to pass on the merged commit, then tag it `vX.Y.Z`,
   annotated, and push the tag.
7. Make a GitHub release from the tag with the notes.

A broken release is fixed in the next patch version; a tag is never
moved or reused.

## Rule sets

A rule set is the rules files as a release shipped them, in
`src/ee_tooling/rules/VERSION/` (`docs/kicad-rules.md`); a board pins
one by its version.  A released set's files never change, nor what
they install: `tests/test_rule_sets.py` holds each set's output for two
boards byte for byte, in `adopt` and in `fab-release`'s DRC.

A release that changes the rules:

1. Copies the newest set's directory to `src/ee_tooling/rules/X.Y.Z/`,
   X.Y.Z the version of the release that will ship it (renamed before
   the release if that version changes), and makes the changes there,
   never in an older set.  A profile's file names are the same in every set, as is
   each section a profile's option leaves out.
2. Updates `docs/kicad-rules.md` to the new set.
3. Writes the new set's expected output:
   `EE_TOOLING_WRITE_GOLDEN=1 uv run pytest tests/test_rule_sets.py`
   writes the files missing in `tests/rule_sets/X.Y.Z/`, never one that
   is there; read them, and commit them with the set.
4. Lists the rules' changes in the release's notes, as breaking for a
   board that follows the current set.

A change of the code that applies the rules (`kicad_rules.py`: the
markers, the Board Setup raise, a minimum raised to the board's Board
Setup value, the ERC settings) must leave every set's expected output
as it is: a change that cannot is breaking for every board, pinned or
not, and needs the owner's decision.
