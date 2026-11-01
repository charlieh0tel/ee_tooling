# Releasing

A release is a tag; nothing is published to a registry.  A board
repository takes ee_tooling as a git submodule following `main`
(`docs/adopting.md`); a release names a version of `main`.

## Versions and tags

- Semantic versioning from 0.1.0, one version for the repository:
  `version` in `pyproject.toml`, and in each Rust crate once there are
  any.  Before 1.0, a breaking change bumps the minor version; anything
  else the patch.
- A breaking change is one a board repository must act on: a command,
  option, `jlcpcb.toml` key, Python or Rust API, skill or output file
  that is removed, renamed or changed in meaning.
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
   `uv run ruff check`, `uv run mypy`, `uv run pytest`, and, once there
   is Rust, `cargo fmt --check`, `cargo clippy`, `cargo test`.
5. Commit `pyproject.toml` and `uv.lock` as "Release X.Y.Z" and merge
   it to `main`.
6. Wait for CI to pass on the merged commit, then tag it `vX.Y.Z`,
   annotated, and push the tag.
7. Make a GitHub release from the tag with the notes.

A broken release is fixed in the next patch version; a tag is never
moved or reused.
