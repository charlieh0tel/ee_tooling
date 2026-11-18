# Design: Git LFS

A proposal, for review before any code.  It puts Git LFS in place in
board and library repositories through `adopt`.  Proposed: a managed
section at the top of `.gitattributes` tracking `*.zip`, `*.tgz`,
`*.pdf` and STEP files, with every KiCad text format plain; a managed
`.githooks/pre-push`; `adopt` requiring git-lfs and setting its filters
per clone; `adopt --check` failing when git-lfs is missing or a file of
a tracked type is a plain blob at HEAD (`git lfs fsck --pointers`);
the release refusing an unfetched pointer among its inputs; no
`lfs: true` on the CI template's checkouts, since neither job reads a
tracked file's contents; no history rewriting by any tool.  The
release's refusal is additive and lands any time; the rest is breaking
and lands separately from the fab-release flag day (0.4.0).  Open:
VRML and images; whether library repositories run `adopt`; whether the
breaking part lands before or after 0.4.0; whether the host's quotas
suit committing every release's archives.

## Facts this rests on

From the code at `c36c726`:

- `adopt` (`src/ee_tooling/adopt.py`, `docs/adopting.md`) writes
  managed files (rewritten by `--yes`, compared by `--check`) and
  project files (written only if missing, never overwritten).
  Managed: `.githooks/commit-msg`, `.github/workflows/ee-tooling.yml`,
  and `AGENTS.md`'s section.  Project: `hardware/pyproject.toml`,
  `jlcpcb.toml`, `.claude/skills/jlcpcb-release/SKILL.md`.
- `AGENTS.md`'s section lies between `<!-- ee_tooling: begin; written
  by ee-tooling-adopt -->` and `<!-- ee_tooling: end -->`; the rest of
  the file is the project's.  A missing section is appended; a file
  that imports the house style outside the markers is left alone.
  `--check` compares the whole file with the file `--yes` would write.
- `adopt` runs commands only when needed: `uv venv`, `uv add`,
  `git config core.hooksPath .githooks` (once per clone).
- `adopt` finds the repository as its submodule's superproject: any
  repository with ee_tooling as a submodule can run it.  With no
  `*.kicad_pro` it writes no `jlcpcb.toml`, saying so.
- The release (`src/ee_tooling/jlcpcb_release.py`) reads the board's
  `.kicad_pcb` itself (revision, parse) before any check, and runs
  kicad-cli on the `.kicad_sch` and `.kicad_pcb`.  Its committed-sources
  check is `git status --porcelain` over the board's directory and
  `sources`.  The source tgz holds the project's `.kicad_pcb`,
  `.kicad_sch`, `.kicad_pro`, `.kicad_dru`, `.pretty`, `.3dshapes`,
  `fp-lib-table` and `sym-lib-table` that exist, walked recursively, then
  `sources`, then the PDFs it exports.  None of the kicad-cli exports it
  runs reads a 3D model; `design-review`'s renders
  (`review_evidence.py`, `kicad-cli pcb render`) do.
- It writes `production/YYYYMMDD-REV/`: `NAME-REV-jlcpcb.zip`,
  `-odb.tgz`, `-ipc2581.zip`, `-source.tgz` (binary), and the
  `.txt` and `.csv` files (text).  The skill says to commit the
  production directory after the order.
- The CI template's two jobs check out with `submodules: true`;
  `attribution` reads commit messages and identities (`fetch-depth: 0`),
  `adopted` runs `adopt --check`.  Neither reads a file's contents.
- ee_tooling itself tracks no binary file: a board repository's clone of
  the submodule is unchanged by this design.
- `docs/releasing.md`: a breaking change is one a board repository must
  act on.

Checked locally with git 2.53.0 and git-lfs 3.7.1, in scratch
repositories with `file://` remotes:

- A pointer is `version https://git-lfs.github.com/spec/v1`, `oid
  sha256:<64 hex>`, `size <bytes>`, each line ending `\n`, 129 bytes for
  the files tried.  The spec (`/usr/share/doc/git-lfs/spec.md`): keys
  sorted after `version`; under 1024 bytes; an empty file is its own
  pointer; the pre-release version URL `https://hawser.github.com/spec/v1`
  is still read.
- With smudge skipped (`GIT_LFS_SKIP_SMUDGE=1`), or with no `lfs` filter
  in any config, a checkout leaves pointers in the work tree and
  `git status` is clean.  `git lfs ls-files` marks such a file `-`, a
  present one `*`.
- With no `lfs` filter in any config, `git add` of a tracked type stores
  the plain blob, silently.
- With the filters set, a plain blob of a tracked type at HEAD shows as
  modified (` M`) in `git status`, permanently, and `git clone` warns
  "Encountered 1 file that should have been a pointer".
- `git lfs fsck --pointers [REV]` exits 1 naming each such file
  ("should have been a pointer but was not"), 0 otherwise.  It examines
  HEAD's tree (or REV's) and needs no LFS object fetched.
- `git add --renormalize .` and a commit turn the plain blob into a
  pointer; fsck then passes.  `git lfs migrate import --no-rewrite`
  refuses, the work tree being dirty (the ` M` above).
- `git lfs fsck --pointers HEAD~1` on a commit before `.gitattributes`
  existed passes.
- Bracket patterns (`*.[sS][tT][eE][pP]`) match in both git and git-lfs.
- `git lfs install --local --skip-repo` sets the four `filter.lfs.*`
  keys in `.git/config` and writes no hook.  Local filters do not reach
  a submodule: its files stay pointers.  With global filters,
  `git clone --recurse-submodules` smudges a submodule's files.
- `git lfs install`, `git lfs track`, `fetch`, `ls-files` and
  `fsck` install `pre-push`, `post-checkout`, `post-commit` and
  `post-merge` into `core.hooksPath` when absent: in a board repository,
  four untracked files in `.githooks/`.  When a `pre-push` that is not
  git-lfs's own is there, `git lfs track` installs none of them and
  succeeds; `git lfs install` fails ("Hook already exists").
- The `post-*` hooks only make lockable files read-only
  (`git lfs post-checkout --help`); `pre-push` uploads the objects.
  git-lfs's hook exits 2 when git-lfs is not on the path.
- `git lfs` without git-lfs: "not a git command", exit 1 (as any
  unknown git command).
- A later `.gitattributes` line overrides an earlier one, per attribute
  (`gitattributes(5)`).

Not verified:

- Whether GitHub's `ubuntu-latest` runner image has git-lfs.
- What `actions/checkout` does without `lfs: true` (pointers, assumed),
  and whether `lfs: true` fetches a submodule's objects.
- The host's LFS storage and bandwidth allowances, whether CI and forks
  count against them, and whether stored objects can be deleted short
  of deleting the repository.
- What KiCad does with a pointer where a STEP model should be (assumed:
  the model is missing from the 3D view and renders, without an error).
- What `git lfs migrate import --everything` does to annotated tags.

## Patterns

The managed section, at the top of `.gitattributes`:

```
# ee_tooling: begin; written by ee-tooling-adopt
*.zip filter=lfs diff=lfs merge=lfs -text
*.tgz filter=lfs diff=lfs merge=lfs -text
*.pdf filter=lfs diff=lfs merge=lfs -text
*.[sS][tT][eE][pP] filter=lfs diff=lfs merge=lfs -text
*.[sS][tT][pP] filter=lfs diff=lfs merge=lfs -text
# ee_tooling: end; the project's own lines follow and win
```

- The lines are `git lfs track`'s own form, so `git lfs track` lists
  them.
- `*.zip`, `*.tgz`: the release's Gerber zip, ODB++, IPC-2581 and
  source tgz.  `*.pdf`: datasheets and exported drawings.
- STEP is text (ISO 10303-21) but large, unreviewed as a diff, and never
  merged by hand: tracked, both extensions, any case (makers ship
  `.STEP`).
- Plain: every KiCad file (`.kicad_pcb`, `.kicad_sch`, `.kicad_pro`,
  `.kicad_dru`, `.kicad_mod`, `.kicad_sym`, `.kicad_wks`), the
  release's `.txt` and `.csv`, `.toml`, `.md`.  They are reviewed as
  diffs, merged, read by `kicad-style-check` and the release, and
  small.
- Open: VRML (`.wrl`), text but large, beside STEP in many libraries;
  images (`.png`, `.jpg`) in docs.  Proposed: not tracked until a
  repository needs them, then added to the section for all.

## .gitattributes

- `.gitattributes` becomes a managed section, as `AGENTS.md`'s: `adopt`
  writes the section, the rest of the file is the project's.
- The section goes first, so the project's own lines, after it, win per
  attribute: `*.pdf -filter -diff -merge text` keeps one repository's
  PDFs plain.  A `.gitattributes` in a subdirectory is the project's and
  also wins.
- A file without the section gets it prepended; one with it gets it
  replaced in place.  Lines `git lfs track` appended are the project's.
- `--check` compares the whole file, as for `AGENTS.md`.
- The marked-section code (`agents_text`) becomes one function for both
  files, taking the markers and where a new section goes.

## The hook

- `.githooks/pre-push`, managed, as `.githooks/commit-msg`: git-lfs's
  own `pre-push` body under ee_tooling's header.  Without it, with
  `core.hooksPath` set to `.githooks`, a push sends pointers without
  their objects and every other clone gets pointers it cannot fetch.
- Its text differs from git-lfs's, so git-lfs installs none of its hooks
  into `.githooks` (checked with 3.7.1): no untracked `post-*` files.
  The `post-*` hooks serve file locking, which is not used.
- A push from a machine without git-lfs is refused by the hook, saying
  so.
- A board repository's own `pre-push` is replaced: `.githooks` is
  ee_tooling's.

## adopt

`adopt` (any mode) refuses to run when `git lfs version` fails, naming
the distribution's `git-lfs` package.  It installs nothing itself.

`adopt --yes`, each only when needed:

- writes the `.gitattributes` section and `.githooks/pre-push`;
- `git lfs install --local --skip-repo`, when `git config
  filter.lfs.process` is unset: the filters for this clone, in
  `.git/config`, not `~/.gitconfig`; no hooks (the managed one is
  there);
- `git lfs pull`, after installing the filters: the clone's pointers
  become files.

It never runs `git lfs migrate`, never renormalizes and never commits.

`adopt --check` fails, each with its own message:

- a managed file is stale (as now; now including `.gitattributes` and
  `pre-push`);
- `git lfs version` fails: git-lfs is missing;
- `git lfs fsck --pointers HEAD` fails: its output, then the fix,
  `git add --renormalize .` and a commit.

`--check` examines HEAD's tree only: history before the section, and
uncommitted files, pass.  It needs no LFS object and no filter
configured, so CI checks out without `lfs: true`.  The detection is
git-lfs's own (`fsck --pointers`) rather than `git ls-files` with
`git check-attr filter` and a parse of each blob: `--check` requires
git-lfs anyway, and the pattern matching is then git-lfs's.

## Migration of a repository with binary history

- The tool does: fail `--check` on a plain blob at HEAD; print
  `git add --renormalize .`.  The commit that follows stores pointers
  from then on; old commits keep their blobs and the repository does
  not shrink.
- The tool does not: rewrite history.  `git lfs migrate import
  --everything` changes every commit's hash: the release tags
  (`NAME-YYYYMMDD-REV`) and every superproject's submodule pointer to a
  library repository then name commits that are gone from the branch,
  and every clone must be replaced.  A repository that must shrink does
  it by hand, by its owner's decision, outside ee_tooling.
- `docs/adopting.md` gains a section: install git-lfs, rerun
  `adopt --yes`, renormalize, commit, push.

## The release

- Before reading the `.kicad_pcb`, the release reads every regular file
  among its inputs: the board's `.kicad_pcb`, `.kicad_sch`, `.kicad_pro`
  and `.kicad_dru`, and every file under the source tgz's paths
  (`Board.source_paths`, directories walked).  A file under 1024 bytes
  whose first line is `version https://git-lfs.github.com/spec/v1` or
  `version https://hawser.github.com/spec/v1` is an unfetched pointer.
- One or more: the release stops before anything is written, naming
  each file and `git lfs pull`.  Neither `--allow-dirty` nor `--force`
  passes it.
- Content only: it needs neither git-lfs nor the attributes, so it
  catches a clone made without git-lfs.  An empty file is not a
  pointer.
- A plain blob of a tracked type among the sources is already refused
  by the committed-sources check (it shows ` M`), with the uncommitted
  changes message; `adopt --check` names the cause.
- The release's outputs become LFS objects when committed, if the
  filters are set; if not, they are plain blobs and `--check` fails in
  CI.
- After fab-release's split, the check is in the core, `fab_release.py`.

## CI

- The template's checkouts stay as they are: `submodules: true`, no
  `lfs: true`.  `attribution` reads commits; `adopted` runs
  `adopt --check`, which needs git-lfs but no object.  `lfs: true`
  would spend the host's bandwidth allowance on every push for nothing.
- If the runner image lacks git-lfs (not verified), the `adopted` job
  gains a step installing it before `adopt --check`.
- `docs/adopting.md`'s "What is left to do": any other CI job that reads
  a tracked file (a release, a render) needs `lfs: true` on its
  checkout, as one running the tools needs `submodules: true`; a library
  submodule's objects may need their own fetch (not verified).
- ee_tooling's own CI: the tests that need git-lfs skip without it; one
  job fails if they are skipped, as the `kicad` job does for kicad-cli.

## Cost

- A new required program on every machine that clones, commits, pushes,
  or runs `adopt`, and in CI.  Without it: a checkout gives pointers,
  silently; a commit of a tracked type stores a plain blob, silently; a
  push is refused by the hook.
- Host storage: each release's archives, each version of a STEP or PDF,
  kept for good; not freed by deleting the file.  Bandwidth: each clone
  and each fetch of a new revision downloads HEAD's objects.  The
  allowances are the host's and the account's (not verified).
- A submodule consumer: a library repository in LFS, as a board
  repository's submodule, gives its STEP files only on a machine with
  global filters (`git lfs install`, the user's own step).  `adopt`'s
  `--local` filters do not reach it.  A board repository's clone of
  ee_tooling is unchanged.

## Breaking or not

Breaking under `docs/releasing.md`: a board repository must act.

1. `adopt` and `adopt --check` require git-lfs.
2. `adopt --check` fails on a plain blob of a tracked type at HEAD: a
   repository with committed archives, PDFs or STEP files must
   renormalize and commit.
3. `adopt --yes` writes `.gitattributes`'s section and
   `.githooks/pre-push`; a board repository's own `pre-push` is
   replaced.
4. A push without git-lfs is refused.

Not breaking: the release's pointer refusal, which refuses only a
release that is already wrong (a pointer in the source tgz).

Relation to the fab-release flag day: separate, not the same merge.
The two touch different things (fab-release: the release's config,
names and checks; this: `adopt`, the hook, `.gitattributes`), each is a
migration of its own, and joining them makes the 0.4.0 merge larger and
its migration day longer.  The pointer refusal lands any time, in
`jlcpcb_release.py` or, after the split, `fab_release.py`.  Open:
whether the breaking part is released before 0.4.0 (as 0.3.0) or after
it (0.5.0).

## Order of work

Refactor first, with no change in behavior; each commit passes every
check, with its docs.

1. `adopt`: one marked-section function for `AGENTS.md` and, later,
   `.gitattributes`; same files.

Additive, any time:

2. The release refuses an unfetched pointer; tests with pointer files
   written by the test, no git-lfs needed; the skill says so.

Then one PR, breaking, merged as one:

3. The `.gitattributes` template and section; `adopt` writes it.
4. `.githooks/pre-push`, managed.
5. `adopt` requires git-lfs; installs the local filters and pulls.
6. `--check`: git-lfs present; `git lfs fsck --pointers HEAD`.  Tests
   in scratch repositories with `file://` remotes, skipped without
   git-lfs; ee_tooling's CI fails if they are skipped.
7. The CI template, if the runner lacks git-lfs; `docs/adopting.md`
   (the table, what is left to do, the migration section).
8. Release; tag after CI.

## Adversarial review

- A machine without the filters commits plain blobs with no error; the
  first warning is `adopt --check` in CI, after the push.  The fix is a
  commit, but the blob stays in the history.
- A clone without git-lfs gets pointers and no error; KiCad, the
  release and `design-review` then read pointers.  The release refuses;
  KiCad's 3D view and `design-review`'s renders lose the models without
  an error (not verified).  A later, additive check in `design-review`
  could refuse as the release does.
- Committing every release's archives to LFS spends storage for good.
  The archives are reproducible from the tagged commit, except for
  kicad-cli's version; attaching them to a host release on the tag the
  release already makes would keep them out of the repository.  Not
  proposed; not foreclosed.  Open: whether the host's quotas suit
  committing them.
- `--check` on HEAD only: history keeps every plain blob, and a clone
  stays as large as it was.  Shrinking it is a history rewrite the tool
  refuses to make.
- Library repositories: `adopt` puts the section in place only where
  ee_tooling is a submodule, and brings `hardware/pyproject.toml`, a
  venv and the release skill with it.  Open: whether a library
  repository runs `adopt`, or takes the section from a documented list
  by hand (then unchecked).
- Global filters are the user's: `adopt` does not write `~/.gitconfig`,
  so a library submodule in LFS needs the user's own `git lfs install`.
- Replacing a board repository's own `pre-push` loses it; the hook is
  a place for ee_tooling's checks only.
- git-lfs's behavior with a foreign `pre-push` (installing no hooks) is
  observed, not documented; a later git-lfs may write the `post-*`
  hooks into `.githooks` as untracked files.
- `fsck --pointers` also fails on a non-canonical pointer; the
  renormalize fix covers it.
- Patterns are global: a repository that wants a `.zip` plain overrides
  it after the section; one that wants another type tracked adds it
  after the section, or asks for it in ee_tooling.
- Foreclosed: nothing kept plain now is hard to track later (a new
  pattern and a renormalize); a tracked type made plain again needs
  `git lfs migrate export` or a renormalize the other way.
