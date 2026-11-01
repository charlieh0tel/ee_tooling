---
name: jlcpcb-release
description: Build the JLCPCB release package for a board (Gerber/drill zip, BOM, placement, source tgz) under its production directory, and walk the order checklist. Use when the user asks to release, package, or order a board from JLCPCB.
---

# JLCPCB release

Read `@EE_TOOLING@/skills/jlcpcb-release/SKILL.md` first: it says how
`jlcpcb-release` works, what it writes and how to fix rotations.  This
file adds the boards' specifics.  From `@HARDWARE@/`:

```
uv run jlcpcb-release            # the first board in jlcpcb.toml
uv run jlcpcb-release NAME       # another
```

## Ordering checklist

Each board's layers, thickness, finish, stackup, stencil and assembly
notes.

## Rotations and offsets

Corrections found in JLCPCB's preview, dated, and those not yet
confirmed.  They go in `@EE_TOOLING@/src/ee_tooling/parts.toml`.
