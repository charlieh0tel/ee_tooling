## ee_tooling

@@EE_TOOLING@/docs/kicad-style.md

- Read `@EE_TOOLING@/docs/kicad-style.md` (schematic drawing, board
  layout, silkscreen) before touching a board.
- `@EE_TOOLING@` is the ee_tooling submodule, following its `main`:
  its tools, house style and agent skills.  Change it there and push
  there, then `git submodule update --remote @EE_TOOLING@`,
  `@EE_TOOLING@/adopt --yes`, and commit the new pointer here.
- `@HARDWARE@/`'s venv is made with `uv venv --system-site-packages
  --python /usr/bin/python3`, for KiCad's pcbnew.
- Before writing new tooling here (a script, a checker, a skill, a
  style rule), consider whether it could be made generic and generally
  useful.  If so, ask whether it should be made in ee_tooling instead,
  then taken here.
- `@EE_TOOLING@/adopt` wrote this section, `.githooks/commit-msg`,
  `.githooks/pre-push`, `.github/workflows/ee-tooling.yml` and the
  section at the top of `.gitattributes`; rerun it with `--yes` rather
  than editing them.
- Archives (`.zip`, `.tgz`), PDFs, STEP and VRML files are stored
  through Git LFS (`.gitattributes`): each machine needs git-lfs.
