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
to date; `--check` fails if one is missing ("missing: PATH") or stale
("out of date: PATH"), if `fab.toml`, which the release needs, is
missing while a KiCad project is there to write it from ("missing:
fab.toml"), if a file below is still to be migrated ("to be migrated:
PATH"), if a board's rules or settings are still to be installed ("to
be updated: PATH"), if an interrupted write left a scratch file ("left
over: PATH"), or if a file of a type Git LFS tracks is a plain blob at
HEAD (git-lfs's report, then "not stored through Git LFS": below).  A fresh
clone needs `git submodule update --init` first.  Run from a checkout
that is not a submodule, it stops: "PATH is not a submodule of a board
repository".

Every mode needs git-lfs (the distribution's `git-lfs` package): with
`git lfs version` failing, `adopt` stops before anything else, saying
so with git's first line and this doc's path from the repository's
root.  It installs nothing itself.

Its git commands run in the board repository (or the submodule) by
directory: the variables naming a repository (`git rev-parse
--local-env-vars`: `GIT_DIR`, `GIT_INDEX_FILE`, ...), which a hook or
`git rebase -x` may set for another repository or relative to another
directory, are removed from their environment.  `fab-release` does the
same.

The submodule's parent need not be `hardware/`.  With the submodule at
`ee_tooling/` and the KiCad project at the repository root, the hardware
tools' uv project is the root: `pyproject.toml`, `.venv` and the
`./ee_tooling` editable source are there, and `fab.toml` has
`directory = "."`.  (`tests/test_adopt.py`, `test_root_layout`.)

## What it writes

The templates are in `src/ee_tooling/templates/`.  A managed file is
rewritten by every run with `--yes`; a project file is written only if
missing, then the project's.  Each board's `.kicad_dru` and `.kicad_pro`
are the project's, and `--yes` edits them in place, keeping the rest:
the dry run and `--yes` name such an edit "update PATH (project;
...)", and `--check` "to be updated: PATH".

| Path | Owner | What |
|---|---|---|
| `.githooks/commit-msg` | managed | runs ee_tooling's commit-msg hook from the submodule |
| `.githooks/pre-push` | managed | git-lfs's `pre-push`: uploads the pushed commits' Git LFS objects |
| `.gitattributes` | managed section | first in the file, between `ee_tooling: begin` and `end` markers: the Git LFS patterns (below) |
| `.github/workflows/ee-tooling.yml` | managed | CI: the hook's checks over the whole history, and `--check` (git-lfs installed first where the runner lacks it) |
| `AGENTS.md` | managed section | between `ee_tooling: begin` and `end` markers; the rest of the file is left as it is |
| `hardware/pyproject.toml` | project | the hardware tools' uv project, named `<directory>-hardware`, the repository's directory name with each run of other than ASCII letters and digits a hyphen, lowercased (a package name uv accepts); an existing one whose name uv refuses is named on stderr: rename it there |
| `fab.toml` | project | one `[[board]]` per `*.kicad_pro` under `hardware/` (its name the project's, whatever its characters), profile `jlcpcb`, with the profile's house `[board.fab]` values |
| `.claude/skills/fab-release/SKILL.md` | project | the boards' specifics, pointing at ee_tooling's skill |
| each board's `NAME.kicad_dru` | project, with ee_tooling's section | its profile's rules between `ee_tooling` markers, the board's own rules after them (`docs/kicad-rules.md`); a new file if it has none |
| each board's `NAME.kicad_pro` | project | Board Setup's design rule minimums raised to the profile's, never lowered; the ERC severities set from `erc_severities.json`, each kept where the board's is stricter, and the connection grid (`docs/kicad-rules.md`) |

`adopt` changes a file through a symbolic link only where it keeps the
project's text in it: `AGENTS.md` and `.gitattributes` (its sections)
and the files it edits (`fab.toml`, a board's `.kicad_dru` and
`.kicad_pro`), each linked to a regular file inside the repository and
not in another repository (a submodule, a clone): it writes that file,
keeps the link, and says so on stderr (`AGENTS.md` as a link to
`CLAUDE.md`, say).  Any other link (a managed hook or the workflow,
which `adopt` writes whole, a file it creates, removes or renames, a
link leaving the repository or into another repository, a linked
`hardware/.venv`, `pyproject.toml` or `uv.lock` the uv steps would
write or clear) stops it,
naming the link, before anything is done (`--check` too): replace the
link with what it links to, or remove it.

A file `adopt` reads that cannot be read (a directory where the file
belongs, say) stops it, naming it, before anything is done.  A file `adopt` reads that is not UTF-8 stops it, naming the file,
before anything is done; a Markdown file in a skill it moves is left as
it is, named on stderr.

A file `adopt` edits or rewrites keeps its own line endings (its first
line's: `\r\n` in a checkout that converts them) and a leading UTF-8
byte order mark, which stays first (before `.gitattributes`'s section,
say); a new file is written with `\n` and no mark.

`--yes` checks that it can write every file it writes (a read-only
`.kicad_pro`, say) before it does anything, and stops naming the first
it cannot.  It writes each file beside it and renames it over the old,
the old file's mode kept, so an interrupted run leaves each file whole,
old or new.  A file with other hard links (`AGENTS.md` a hard link of
`CLAUDE.md`, say) is written in place instead, after a scratch copy,
so every name keeps sharing it; a link adopt writes through is written
at its target, and the target's directory must be writable too, which
`--yes` checks first.  The scratch file such a run may leave beside one
(`.NAME.PID.ee_tooling-tmp`) is named by `--check` ("left over") and
removed by `--yes`.

A board whose `directory` in `fab.toml` resolves outside the
repository, or into another repository (a directory with a `.git` on the
way: a library's submodule, a clone), stops `adopt` (every mode),
naming it, before any edit is planned: it edits no `.kicad_dru` or
`.kicad_pro` there.  So does a board whose `name` is not one path
component (a `/` or `\` in it, or `.` or `..`), since the files are named
by it, and one whose `.kicad_pro` or `.kicad_dru` resolves (through a
link) outside the repository or into another repository.

Close the boards in KiCad before `--yes`: KiCad does not reload a
`.kicad_dru` or `.kicad_pro` changed under it, and saving from KiCad
overwrites the change.  While KiCad's lock file for the board's project,
board or schematic (`~NAME.kicad_pro.lck`, `~NAME.kicad_pcb.lck`,
`~NAME.kicad_sch.lck` beside it) is there and an edit is due, `adopt`
stops, naming it.  `--check` fails while a board's rules section
is missing or out of date, its Board Setup is below the profile's
minimums, or its ERC severities or connection grid differ as above.  A
board whose profile is not known, or whose `.kicad_pro` is not there,
is left, said on stderr.  A board whose `[board.<profile>]` its
profile refuses (an unknown key, a value not `true` or `false`) stops
`adopt`, naming it.  A `.kicad_pro` that is not JSON, or a `.kicad_dru`
whose markers are out of order (below), stops it with "install the
rules by hand"; so does an edit that does not read back as written
("the rules are not where they belong").  The boards are `fab.toml`'s,
or, before it is written or migrated, those it will be written with:
the dry run, `--yes` and `--check` name the same files.

A board whose `.kicad_dru` is a hand-made copy of ee_tooling's rules
file, its own rules after it, keeps that copy below the end marker
after `--yes`: those rules, named as the section's, win over it.
`adopt` and `fab-release` say so on stderr, with the count, each rule's
name and the file; `--check` does not fail on it.  Delete those rules
from below the end marker, keeping the board's own.

It finds the KiCad projects (`*.kicad_pro`) under `hardware/`, skipping
the submodule, every directory whose name starts with `.` (`.git`,
`.venv`, `.claude/`'s worktrees), every production directory
(`production/`, and each `production` that `jlcpcb.toml` or `fab.toml`
names) and every other repository (a directory holding a `.git` file or
directory: a library's submodule, a clone), so a project file in a
release, a worktree or a library is not taken for a board, and `adopt`
edits no other repository's `.kicad_dru` or `.kicad_pro`.  Two projects
of one name in different directories (`hardware/a/main.kicad_pro` and
`hardware/b/main.kicad_pro`) stop it before `fab.toml` is written,
naming both: `fab.toml` names a board by its project, so write it by
hand or rename one.

## Migration

A repository adopted before `fab.toml` is migrated by `--yes`, the one
time `adopt` changes files the project owns:

- `jlcpcb.toml`, with no `fab.toml`: `fab.toml` is written from its
  boards, each key kept and `profile = "jlcpcb"` added, and
  `jlcpcb.toml` is removed if git holds it as it is (committed, with no
  uncommitted change).  Otherwise it is kept, and `--yes` says so: its
  comments and edits would be lost for good.  `--check` names it ("to be
  migrated") until `fab.toml` is written, then passes, saying on stderr
  that it is not read: remove it yourself once `fab.toml` is checked.  Each board gets the profile's house
  `[board.fab]` values.  `copper` is dropped: the copper layers are
  the stackup's.  Its comments are not carried over, said once on
  stderr if it has any but the header an earlier `adopt` wrote: they
  stay in git's history (`git show HEAD:jlcpcb.toml`); nor is a key `fab.toml` does not have, each
  named on stderr.  A `jlcpcb.toml`
  that does not parse, or lacks a key, stops `adopt`, naming it.
- `.claude/skills/jlcpcb-release/`, with no `.claude/skills/fab-release/`:
  moved there, and `jlcpcb-release` and `jlcpcb.toml` in its `.md` files
  renamed `fab-release` and `fab.toml`: the files first, then the
  directory, so a run stopped between leaves the old directory, which
  `--check` names and the next `--yes` moves.
- With both the old and the new there, the old is left as it is, named
  on stderr.
- `fab.toml` with a board lacking a `[board.fab]` key: each key it
  lacks is added, with its profile's house value, under the board's
  `[board.fab]` (added after the board's other lines if it has none);
  the rest of the file, comments too, is kept.  `--check` fails until
  then.  A `fab` given otherwise (an inline table, dotted keys), or a
  `fab.toml` whose `[[board]]` header lines are not one per board ("N
  boards, M [[board]] headers"), stops `adopt`, naming it: add the keys
  by hand.

The `AGENTS.md` section imports `docs/kicad-style.md`, says how the
submodule and venv are kept, and says: before writing new tooling in
the board repository (a script, a checker, a skill, a style rule),
consider whether it could be made generic and generally useful; if so,
ask whether it should be made in ee_tooling instead.  An `AGENTS.md`
that already imports the house style outside the markers was adopted
by hand and is left alone.

In `AGENTS.md` and `.gitattributes` a marker is a whole line (a marker
quoted within a line is the project's text).  With no marker line the
section is added; with one begin line, then one end line, the lines
between them are replaced, and everything before the begin line and
after the end line is kept byte for byte (a blank line after the
section too).  Any other arrangement (an end before the
begin, a begin with no end, two of either) stops `adopt`, naming the
file, before anything is written: fix the markers by hand.  A board's
`.kicad_dru` markers (`docs/kicad-rules.md`) are held to the same, and
stop `fab-release` too.

What it runs, each only when needed, in this order (git's set-up
first: `.gitattributes` routes files to Git LFS already, and a uv step
that fails must not leave the hooks or the filters unset):

- `git config core.hooksPath .githooks`, once per clone;
- `git lfs install --local --skip-repo`, when `git config
  filter.lfs.process` is unset: git-lfs's filters for this clone, in
  `.git/config`, never `~/.gitconfig`, and no hooks (the managed
  `pre-push` is git-lfs's);
- `git lfs pull`, when a file Git LFS tracks is a pointer in the work
  tree (`git lfs ls-files` marks it `-`): the clone's pointers become
  files.  With no remote, or the remote out of reach, it fails, and
  `adopt` says the files stay pointers until it runs.  When `git lfs
  ls-files` itself fails, `adopt` stops with its error;
- `uv venv --system-site-packages --python /usr/bin/python3` in
  `hardware/`, with `--clear` for a venv without system packages:
  uv has no project setting for it, and KiCad's `pcbnew` is only in
  the system Python.  A venv uv remakes itself (after `.venv` is
  deleted) has no `pcbnew`; rerun `adopt --yes`.  The system Python
  must be 3.12 or later (`docs/stitch.md`, Platforms); where it is
  older, or missing, `adopt` says so and makes no venv on it, and
  `uv add` makes one on a Python of uv's;
- `uv add --dev --editable --no-workspace ./ee_tooling` in `hardware/`.

It never runs `git lfs migrate`, never renormalizes and never commits.

## What is left to do

- After a migration, check `fab.toml` and the moved skill, and look for
  `jlcpcb-release` and `jlcpcb.toml` elsewhere in the repository.
- `fab.toml`: each board's production directory, `sources`, profile and
  `[board.fab]` values (`skills/fab-release/SKILL.md` gives the keys).
- Each library a board reads (footprints, symbols, 3D models) other than
  KiCad's own inside the repository, normally as a git submodule, and
  the project's library tables and 3D model paths naming it by a
  `${KIPRJMOD}`-relative path (`docs/kicad-style.md`, Files).  A clone
  beside the repository is not supported.  Add a library to `sources`
  for the source tgz to hold it.
- Each board's stackup, in Board Setup, Physical Stackup: the release
  takes the copper layers from it, and refuses a board without one; and
  its stackup table on User.Drawings, which `stackup-table` writes: the
  release refuses a board without one matching the stackup.
- The skill's ordering checklist and part corrections.
- Any other CI job that runs ee_tooling's tools needs `submodules: true`
  on its checkout, and one that reads a file Git LFS tracks (a release,
  a render) needs `lfs: true`.  `actions/checkout`'s `lfs` is false by
  default: the checkout holds pointers.  Whether `lfs: true` fetches a
  submodule's Git LFS objects is not documented.
- git-lfs on each machine that clones, commits, pushes or runs `adopt`.
  Without it a clone holds pointers, silently; a commit of a tracked type
  stores a plain blob, silently (`--check` fails on it in CI); a push is
  refused by the hook.
- A library repository in Git LFS, as a submodule, gives its files only
  on a machine whose own git config sets git-lfs's filters (`git lfs
  install --skip-repo`, the user's step, or the distribution's package
  setting them system-wide): `adopt`'s filters are this clone's alone.
  The dry run and `--yes` name each submodule (but ee_tooling) whose
  tracked files are pointers, with the commands that fetch them, `git -C
  SUBMODULE lfs install --local --skip-repo && git -C SUBMODULE lfs
  pull`; `adopt` does not run them, as it changes no other repository's
  config.
- The remote must support Git LFS: a push to one that does not is
  refused by the hook, after git-lfs's error.
- The attribution job fails on any commit already in the history that
  credits an AI assistant.
- Commit the result, `hardware/uv.lock` with it.

## Git LFS

`.gitattributes`'s section stores these through Git LFS, each in any
case (`*.[pP][dD][fF]`), in any directory:

- `.zip`, `.tgz`: the release's archives, committed under the
  production directory;
- `.pdf`: datasheets and exported drawings;
- `.step`, `.stp`, `.wrl`: 3D models, text but large and never
  reviewed as a diff.

Every KiCad file, the release's `.txt` and `.csv`, `.toml` and `.md`
stay plain, as do images.  The lines are `git lfs track`'s own form.
The section goes first, so the project's own lines after it win, per
attribute: `*.pdf -filter -diff -merge text` keeps a repository's PDFs
plain, `*.png filter=lfs diff=lfs merge=lfs -text` adds a type.  A
`.gitattributes` in a subdirectory is the project's and also wins.

`--check` runs `git lfs fsck --pointers HEAD`: a file of a tracked
type stored at HEAD as a plain blob, not a pointer, fails it, named in
git-lfs's words ("should have been a pointer but was not"), with the
fix: with git-lfs's filters set, `git add -A && git add --renormalize
.` and a commit (`git add -A` first: after `--yes` moved or removed a
file, `--renormalize` alone fails, "unable to stat"; together they
stage every change in the work tree).  When
fsck fails otherwise (its output naming no file: a damaged repository,
say), `--check` says that fsck failed, with its error, and no fix.  It
examines HEAD's tree alone: history and uncommitted files pass, and it
needs no Git LFS object fetched and no filter set, so CI checks out
without `lfs: true`.  A repository with no commit passes.  The hooks
`git lfs fsck` installs where there is no `pre-push` go to a scratch
directory, not `.githooks`.  Without `--yes`, `adopt` changes nothing in
`.git/config` either: git-lfs's `ls-files` and `fsck` set
`lfs.repositoryformatversion` there when it is unset, and `adopt` runs
them with `GIT_CONFIG` naming a scratch copy of it, which takes the
write.  `--yes`'s `git lfs install` and `git lfs pull` set it.

A repository whose files of these types were committed before the
section, or from a machine without git-lfs's filters, holds them as
plain blobs, and `--check` fails until they are stored through Git
LFS.  It passes right after `--yes` (HEAD's `.gitattributes` has no
section yet) and fails once `.gitattributes` is committed, so the
renormalize goes in the same commit as `adopt`'s files.  The dry run and
`--yes` name such files at HEAD, with the fix, before that commit.  The
first two steps on each machine, the third once:

1. Install git-lfs (the distribution's `git-lfs` package).
2. `hardware/ee_tooling/adopt --yes`: the section, the hook, the
   filters for this clone, and `git lfs pull`.
3. `git add -A && git add --renormalize .`, then commit `adopt`'s files
   and the renormalized ones together, and push.

The commit stores pointers from then on; older commits keep their
blobs, and the repository does not shrink.  Shrinking it rewrites
history (`git lfs migrate import --everything` gives every commit a new
hash, the commits the release tags name among them, and every
superproject's submodule pointer to the repository then names a commit
gone from its branch): no ee_tooling tool does it.  Each version of a
tracked file is kept by the host, counted against its Git LFS storage;
each clone and CI checkout with `lfs: true` downloads, counted against
its bandwidth.

The CI template's `adopted` job runs `git lfs version` and installs
the distribution's `git-lfs` package only when that fails: GitHub's
`ubuntu-24.04` image lists Git LFS among its tools (actions/runner-images,
`images/ubuntu/Ubuntu2404-Readme.md`), and `ubuntu-latest` moves to newer
images.

`adopt` does not serve a library repository (footprints, symbols, 3D
models; deferred: `docs/design/fab-release.md`, Later): it copies the
lines of `src/ee_tooling/templates/gitattributes` into its own
`.gitattributes` by hand, and nothing checks them there.

## Hooks

Git runs a hook from the top of the work tree, whatever the directory
of the commit, so the wrapper's path is from there.  Before
`git submodule update --init` the hook is missing, and the wrapper
refuses every commit, saying so.  `make hook-test` in ee_tooling tests
the wrapper in a scratch repository.  The hooks and the CI workflow quote the
submodule's path, so one with a space works.  A submodule path with any
other character than letters, digits, a space and `. _ / + -` (a `$`, a
backquote, a quote, a `;`, a newline, a backslash), which a hook or the
workflow would run as code, stops `adopt` in every mode before any file
is written: move the submodule (`git mv`).  The commands `adopt`'s
messages give are quoted for the shell.

`.githooks/pre-push` runs `git lfs pre-push`, which uploads the pushed
commits' Git LFS objects; without it a push sends pointers whose files
no other clone can fetch.  It refuses a push from a machine without
git-lfs, and says so when the upload fails (a remote without Git LFS,
or out of reach).  `.githooks` is ee_tooling's: a repository's own
`pre-push` is replaced.  `adopt`, in every mode, names one it did not
write on stderr, with its text, before `--yes` replaces it.

Git skips a hook that is not executable, silently: a `pre-push`
committed without its executable bit lets pushes send pointers without
their objects.  So a managed hook is stale unless it is executable in
the work tree (git runs a hook only so, whatever `core.fileMode` says)
and, when tracked, in the index (what every clone checks out): `--check`
fails on it, and `--yes` sets the work tree's bit and stages the index's
(`git update-index --chmod=+x`) for the next commit.

`--yes` sets `core.hooksPath` to `.githooks`, so the hooks git runs now
from elsewhere (`.git/hooks`, or another `core.hooksPath` such as
`.husky`) stop running.  Every mode names each of them (an executable
file, not a `.sample`) on stderr, in a `WARNING:` line, while
`core.hooksPath` is not `.githooks` yet.

## Updating

A change to ee_tooling is made and pushed in ee_tooling.  Then, in the
board repository, with KiCad closed:

```
git submodule update --remote hardware/ee_tooling
hardware/ee_tooling/adopt --yes
```

and commit the new pointer with any files it rewrote, any files it
named as plain blobs renormalized (`git add -A && git add --renormalize
.`, Git LFS above), and `hardware/uv.lock` if it changed, in one
commit.  A bump
across breaking changes takes the steps of "Upgrading a board
repository" below.  `hardware/uv.lock` records the
submodule's version and dependencies, so a bump that changes either
changes it: the first `uv run` in `hardware/` after the bump rewrites
it (`uv lock --check` there says whether it is current).  `adopt --yes`
does not: its `uv add` runs only when `hardware/pyproject.toml` lacks
ee_tooling.  `fab-release` refuses an uncommitted `uv.lock` in a board's
directory or `sources` (the repository-root layout, `directory = "."`).

## Upgrading a board repository

After a bump across breaking changes (the release's notes list them,
`docs/releasing.md`), in this order, on one machine.  With the KiCad
project at the repository root, `ee_tooling/` stands for
`hardware/ee_tooling/` and the root for `hardware/`.

1. Close KiCad: `adopt` edits the boards' `.kicad_pro` and `.kicad_dru`,
   which KiCad would overwrite, and stops while KiCad's lock files are
   there.
2. Install git-lfs (the distribution's `git-lfs` package), on every
   machine that clones, commits or pushes.
3. `git submodule update --remote hardware/ee_tooling`, then
   `hardware/ee_tooling/adopt` (the dry run): read what it would do and
   its warnings (hooks that stop running, a `pre-push` replaced, plain
   blobs at HEAD, library submodules' pointers, lines dropped from the
   rules section).
4. `hardware/ee_tooling/adopt --yes`.
5. `git add -A && git add --renormalize .` (stages everything `--yes`
   changed, moved or removed, and stores the files step 3 or 4 named as
   plain blobs as pointers), and run the commands it gave for each
   library submodule's pointers.
6. In `hardware/`: `uv lock` (or any `uv run`), so `hardware/uv.lock`
   records the new ee_tooling.
7. Remove any hand copy of the profile's rules below the end marker of
   each board's `.kicad_dru` (`adopt` names them); they win over the
   section's.
8. Open each board in KiCad: refill the zones and run DRC and ERC.
   Board Setup is raised to the profile's minimums and its rules apply,
   and the release fails on a warning as on an error
   (`--exit-code-violations`).  Fix what they find; draw or refresh the
   stackup table (`stackup-table`).
9. Commit it all in one commit (the submodule pointer, `adopt`'s files,
   the renormalized files, `uv.lock`, the boards), and push: the
   `pre-push` hook uploads the Git LFS objects.

`adopt --check` passing means the files `adopt` manages are current and
nothing at HEAD is a plain blob of a tracked type; it does not mean a
release will succeed: `fab-release` checks ERC, DRC, the stackup table,
the libraries and the committed sources besides.

### Moving a library into the repository

`fab-release` refuses a library outside the repository.  To move one in,
as a submodule:

1. Give the library a repository of its own (`git init`, or use the
   one it has); if it holds 3D models or PDFs, copy the lines of
   `hardware/ee_tooling/src/ee_tooling/templates/gitattributes` into its
   `.gitattributes`, commit, and push it to its host.
2. In the board repository: `git submodule add URL hardware/LIB`.
3. Repoint the board's `fp-lib-table` and `sym-lib-table`, and its 3D
   model paths, at it by a `${KIPRJMOD}`-relative path
   (`${KIPRJMOD}/../LIB/...`; `docs/kicad-style.md`, Files).
4. List it in the board's `sources` in `fab.toml` if the source tgz is
   to hold it.
5. Commit, and check with `fab-release` as above.
