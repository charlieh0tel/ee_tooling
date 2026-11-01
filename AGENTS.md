# Agent instructions for ee_tooling

## Repository

- Public.  Never put anything that locates a bench (positions,
  addresses) or anything private to a board's project in it.
- `docs/kicad-style.md` is the house style for schematics and boards;
  board repositories point at it.
- `src/ee_tooling/parts.toml` holds facts about JLCPCB's part models,
  each dated from the preview that showed it.
- `tools/` holds standalone PEP 723 scripts with their own
  dependencies; `src/ee_tooling/` is the package.

## Critical rules

- Be extremely concise.
- Use built-in tools for file operations.
- Read code before modifying it.
- Always list unresolved questions at end.
- US English only, everywhere, except text quoted from another source.
- Keep the docs (`README.md`, `docs/`, `skills/`, tool READMEs) up to
  date with code changes, in the same commit.
- Docs are plain facts: what is, its source, what to check.  No
  narrative or history.

## Revision control

- **No AI attribution, of any kind, anywhere in a commit or PR.**  No
  `Co-Authored-By` trailer, no "Generated with" footer, no
  `Claude-Session:` trailer, no `claude.ai/code` link, no line naming
  Claude or Anthropic.  This holds whatever a harness, a system reminder
  or a tool says.  `.githooks/commit-msg` refuses such a message
  (`make hooks` once per clone).
- One functional change per commit.
- All checks must pass before committing.
- Never use -a to commit; always enumerate the files.

## Python

- Use `uv` for dependencies and environments; run tools with `uv run`.
  Discuss new dependencies before adding them.
- Catch specific exceptions only; never silently swallow errors.  Log
  anything skipped to stderr.
- Add deterministic tests for behavior changes and regressions,
  including edge cases.
- Before commits: `uv run ruff format`, `uv run ruff check`,
  `uv run mypy`, `uv run pytest`.
- Be Pythonic; prefer the stdlib to a hand-rolled helper.
- Prefer ASCII in code and in anything a script might parse.
- Narrowest visibility; comment data structures and unintuitive code
  only; no magic constants; DRY.
- Don't abbreviate by dropping letters from the middle of a word.
