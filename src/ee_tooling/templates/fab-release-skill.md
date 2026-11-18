---
name: fab-release
description: Build the release package for a board (Gerber/drill zip, BOM, placement, source tgz) under its production directory, and walk the order checklist. Use when the user asks to release, package, or order a board.
---

# Fab release

Read `@EE_TOOLING@/skills/fab-release/SKILL.md` first, and its
profile's file beside it (`jlcpcb.md`): they say how `fab-release`
works, what it writes and how to fix rotations.  This file adds the
boards' specifics.  From `@HARDWARE@/`:

```
uv run fab-release            # the first board in fab.toml
uv run fab-release NAME       # another
```

## Ordering checklist

Each board's layers, thickness, finish, stackup, stencil and assembly
notes.

## Rotations and offsets

Corrections found in JLCPCB's preview, dated, and those not yet
confirmed.  They go in `@EE_TOOLING@/src/ee_tooling/parts.toml`.
