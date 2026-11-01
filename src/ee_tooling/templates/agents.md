## ee_tooling

@@EE_TOOLING@/docs/kicad-style.md

- Read `@EE_TOOLING@/docs/kicad-style.md` (schematic drawing, board
  layout, silkscreen) before touching a board.
- `@EE_TOOLING@` is the ee_tooling submodule, following its `main`:
  its tools, house style and agent skills.  Change it there and push
  there, then `git submodule update --remote @EE_TOOLING@` and commit
  the new pointer here.
- `@HARDWARE@/`'s venv is made with `uv venv --system-site-packages
  --python /usr/bin/python3`, for KiCad's pcbnew.
- Before writing new tooling here (a script, a checker, a skill, a
  style rule), consider whether it could be made generic and generally
  useful.  If so, ask whether it should be made in ee_tooling instead,
  then taken here.
- `ee-tooling-adopt` wrote this section, `.githooks/commit-msg` and
  `.github/workflows/ee-tooling.yml`; rerun it (`uv run
  ee-tooling-adopt` in `@HARDWARE@/`) rather than editing them.
