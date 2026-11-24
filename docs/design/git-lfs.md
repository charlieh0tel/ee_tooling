# Design: Git LFS

Git LFS in board repositories, put in place by `adopt`, in ee_tooling
0.4.0 with `fab-release` (`docs/design/fab-release.md`): a managed
section at the top of `.gitattributes` tracking archives (`.zip`,
`.tgz`), PDFs, STEP and VRML files, each in any case, with every KiCad
text format plain; a managed `.githooks/pre-push`; `adopt` requiring
git-lfs and setting its filters per clone; `adopt --check` failing when
git-lfs is missing or a file of a tracked type is a plain blob at HEAD
(`git lfs fsck --pointers`); the release refusing an unfetched pointer
among its inputs; no `lfs: true` on the CI template's checkouts, since
neither job reads a tracked file's contents; no history rewriting by
any tool.

Decisions: release archives stay committed under the production
directory, through LFS; `*.wrl` tracked, images not; every pattern in
any case; no opt-out: a board repository's host must support Git LFS.
Deferred: library repositories under `adopt` (they copy the section by
hand); a check of 3D models outside the release's inputs.

## Facts this rests on

- ee_tooling itself tracks no binary file: a board repository's clone of
  the submodule is the same with or without git-lfs.
- The CI template's two jobs check out with `submodules: true`;
  `attribution` reads commit messages and identities (`fetch-depth: 0`),
  `adopted` runs `adopt --check`.  Neither reads a file's contents.
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
- `git lfs install`, `git lfs track` and `fsck` install `pre-push`,
  `post-checkout`, `post-commit` and `post-merge` into `core.hooksPath`
  when absent (`ls-files` does not; `fetch` with no remote does not): in a board repository,
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

Checked locally with the same versions (scratch
repositories with the global and system git config left out, and the
tests in `tests/test_adopt.py`, `tests/test_fab_release.py`):

- `git lfs fsck --pointers HEAD` needs no filter configured; it exits 2
  ("Git can't resolve ref") in a repository with no commit; it does not
  look into a submodule's files; a subdirectory's `.gitattributes`
  making a type plain is honored.
- With ee_tooling's `pre-push` in `.githooks`, `git lfs install --local
  --skip-repo`, `ls-files`, `track`, `pull` and `fsck` write no hook
  there.  With no `pre-push` there, `fsck` writes git-lfs's four hooks
  (a first `adopt --check`, before `--yes`); `version`, `ls-files`,
  `pull` and `install --local --skip-repo` write none.  With
  `git -c core.hooksPath=DIR`, `fsck` writes them into DIR, so `--check`
  runs it with a scratch directory there.
- `git lfs ls-files`, `fsck`, `pull` and `install --local --skip-repo`
  set `lfs.repositoryformatversion = 0` in `.git/config` when it is unset
  (`version` does not); `-c lfs.repositoryformatversion=0`, or the key
  in the global config, does not stop it.  It is written through `git
  config`: with `GIT_CONFIG` naming another file (git-config(1)) that
  file takes the write, and git-lfs reads its settings, `core.hooksPath`
  among them, from it rather than from `-c`.
- `git add --renormalize .` stages every tracked file's changes, not
  only the renormalized ones; untracked files are left.  With the
  filters set, `git add` of a plain blob shown ` M` stores it as a
  pointer too.
- `git lfs pull` exits 2 with no remote, even with nothing to fetch,
  and with the remote out of reach (`file://` gone, an unresolvable
  `https://` host).  With no filter configured it exits 0 and leaves the
  pointers ("Skipping object checkout, Git LFS is not installed").
- A push through ee_tooling's `pre-push` uploads the objects; without
  git-lfs on the path the hook refuses it; to an LFS endpoint answering
  501 git-lfs says "batch response: Not Implemented" and the push fails.
- `git lfs migrate import --everything` rewrites the commits annotated
  tags point at (3.7.1).

From the hosts' documentation and responses:

- GitHub's `ubuntu-24.04` runner image lists Git LFS 3.8.1
  (actions/runner-images, `images/ubuntu/Ubuntu2404-Readme.md`).
- `actions/checkout`'s `lfs` is false by default: Git LFS files are not
  downloaded.
- GitHub: Free and Pro include 10 GiB of LFS storage and 10 GiB a month
  of bandwidth, Team and Enterprise 250 GiB each, metered beyond;
  Actions' downloads count against bandwidth; stored objects are purged
  only by deleting the repository or through support.
- A Gitea-based host: its LFS batch API answers for a repository, and its
  repository settings have an LFS page.

Not verified:

- Whether the `ubuntu-latest` image, moving to Ubuntu 26.04, has
  git-lfs (the template installs it where it is missing).
- Whether `lfs: true` with `submodules: true` fetches a submodule's
  objects.
- An actual upload to the Gitea-based host.
- What KiCad does with a pointer where a STEP model should be (assumed:
  the model is missing from the 3D view and renders, without an error).

## Patterns

The managed section, at the top of `.gitattributes`:

```
# ee_tooling: begin; written by ee-tooling-adopt
*.[zZ][iI][pP] filter=lfs diff=lfs merge=lfs -text
*.[tT][gG][zZ] filter=lfs diff=lfs merge=lfs -text
*.[pP][dD][fF] filter=lfs diff=lfs merge=lfs -text
*.[sS][tT][eE][pP] filter=lfs diff=lfs merge=lfs -text
*.[sS][tT][pP] filter=lfs diff=lfs merge=lfs -text
*.[wW][rR][lL] filter=lfs diff=lfs merge=lfs -text
.githooks/* text eol=lf
# ee_tooling: end; the project's own lines follow and win
```

- The Git LFS lines are `git lfs track`'s own form, so `git lfs track`
  lists them.
- `.githooks/* text eol=lf`: the hooks are checked out with LF line
  endings, whatever `core.autocrlf` says; the kernel cannot run a
  script whose `#!/bin/sh` line ends in CR.
- The section's lines are `src/ee_tooling/templates/gitattributes`;
  `adopt` adds the markers.
- Every pattern matches any case: makers ship `.PDF` and `.STEP`.
- `.zip`, `.tgz`: the release's Gerber zip, ODB++, IPC-2581 and
  source tgz, which stay committed under the production directory.
  `.pdf`: datasheets and exported drawings.
- STEP is text (ISO 10303-21) but large, unreviewed as a diff, and never
  merged by hand: tracked, both extensions, any case (makers ship
  `.STEP`).
- VRML (`.wrl`), KiCad's other 3D model format, likewise: text, large,
  beside STEP in many libraries; any case.
- Plain: every KiCad file (`.kicad_pcb`, `.kicad_sch`, `.kicad_pro`,
  `.kicad_dru`, `.kicad_mod`, `.kicad_sym`, `.kicad_wks`), the
  release's `.txt` and `.csv`, `.toml`, `.md`.  They are reviewed as
  diffs, merged, read by `kicad-style-check` and the release, and
  small.
- Not tracked: images (`.png`, `.jpg`).  A repository that wants them
  in LFS adds its own lines after the section.

## .gitattributes

- `.gitattributes` has a managed section, as `AGENTS.md` has: `adopt`
  writes the section, the rest of the file is the project's.
- The section goes first, so the project's own lines, after it, win per
  attribute: `*.pdf -filter -diff -merge text` keeps one repository's
  PDFs plain.  A `.gitattributes` in a subdirectory is the project's and
  also wins.
- A file without the section gets it prepended; one with it gets it
  replaced in place.  Lines `git lfs track` appended are the project's.
- `--check` compares the whole file, as for `AGENTS.md`.
- One function (`marked_text`) writes both files' sections, given the
  markers and where a new section goes (`Markers`).  Markers other than
  one begin line, then one end line stop `adopt`, naming the file.

## The hook

- `.githooks/pre-push`, managed, as `.githooks/commit-msg`: it runs
  `git lfs pre-push` with the push's arguments and refs, as git-lfs's
  own hook does, under ee_tooling's header and with its own messages.  Without it, with
  `core.hooksPath` set to `.githooks`, a push sends pointers without
  their objects and every other clone gets pointers it cannot fetch.
- Its text differs from git-lfs's, so git-lfs installs none of its hooks
  into `.githooks` (checked with 3.7.1): no untracked `post-*` files.
  The `post-*` hooks serve file locking, which is not used.
- A push from a machine without git-lfs is refused by the hook (exit 2),
  saying so.  When `git lfs pre-push` fails (a remote without Git LFS,
  or out of reach), the hook says so after git-lfs's error.
- A board repository's own `pre-push` is replaced: `.githooks` is
  ee_tooling's.  One without ee_tooling's header is named on stderr by
  every mode of `adopt`, with its text, before `--yes` replaces it.

## adopt

`adopt` (any mode) refuses to run when `git lfs version` fails, naming
the distribution's `git-lfs` package.  It installs nothing itself.

`adopt --yes`, each only when needed:

- writes the `.gitattributes` section and `.githooks/pre-push`;
- `git lfs install --local --skip-repo`, when `git config
  filter.lfs.process` is unset: the filters for this clone, in
  `.git/config`, not `~/.gitconfig`; no hooks (the managed one is
  there);
- `git lfs pull`, after the filters, when `git lfs ls-files` marks a file `-` (a
  pointer in the work tree): the clone's pointers become files.  Not
  otherwise: it fails with no remote even with nothing to fetch.  When
  it fails, `adopt` says the files stay pointers until it runs.

It never runs `git lfs migrate`, never renormalizes and never commits.

`adopt --check` fails, each with its own message:

- a managed file is stale, `.gitattributes` and `pre-push` among them;
- `git lfs version` fails: git-lfs is missing;
- `git lfs fsck --pointers HEAD` fails: its output, apart from the
  other failures' labeled paths, then "not stored through Git LFS: the
  files above" and the fix: with the filters set,
  `git add --renormalize .` in a clean work tree, and a commit.  A
  repository with no commit is not examined.

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
- `docs/adopting.md` (Git LFS; Upgrading a board repository) gives the
  steps: install git-lfs, rerun `adopt --yes`, renormalize, commit,
  push.

## The release

- Before it reads the board's `.kicad_pcb`, `.kicad_sch` and
  `.kicad_pro`, the release reads each of them for a pointer; then,
  once it has the sheets and libraries they name, every regular file of
  the source tgz's paths (`Board.source_paths`: the project's files,
  its sheet files, `sources`) and of each library path inside the
  repository (`project_libraries.py`), directories walked, `.git` and
  every production directory left out.  A file under 1024 bytes whose
  first line is `version https://git-lfs.github.com/spec/v1` or
  `version https://hawser.github.com/spec/v1` is an unfetched pointer.
- One or more: the release stops before anything is written, naming
  each file and the fix, `git lfs install --local --skip-repo` and
  `git lfs pull` (a pull without the filters leaves the pointers), with
  `git -C SUBMODULE` for a pointer in a submodule.
  Neither `--allow-dirty` nor `--force` passes it.  It runs once, before
  every variant's build.
- Content only: it needs neither git-lfs nor the attributes, so it
  catches a clone made without git-lfs.  An empty file is not a
  pointer.
- A plain blob of a tracked type among the sources is already refused
  by the committed-sources check (it shows ` M`), with the uncommitted
  changes message; `adopt --check` names the cause.
- The release's outputs become LFS objects when committed, if the
  filters are set; if not, they are plain blobs and `--check` fails in
  CI.
- The check is in fab-release's core, `fab_release.py`.

## CI

- The template's checkouts stay as they are: `submodules: true`, no
  `lfs: true`.  `attribution` reads commits; `adopted` runs
  `adopt --check`, which needs git-lfs but no object.  `lfs: true`
  would spend the host's bandwidth allowance on every push for nothing.
- The `adopted` job runs `git lfs version` and installs the
  distribution's `git-lfs` package only when that fails, before
  `adopt --check`: `ubuntu-24.04` has it, and `ubuntu-latest` is moving
  to a newer image.
- `docs/adopting.md`'s "What is left to do": any other CI job that reads
  a tracked file (a release, a render) needs `lfs: true` on its
  checkout, as one running the tools needs `submodules: true`; a library
  submodule's objects may need their own fetch (not verified).
- ee_tooling's own CI: the tests that need git-lfs skip without it
  ("no git-lfs on the path"); the `kicad` job (`ubuntu-24.04`) fails if
  they are skipped, as it does for kicad-cli.  The tests leave the
  global and system git config out (`GIT_CONFIG_GLOBAL`,
  `GIT_CONFIG_NOSYSTEM`).

## Cost

- A new required program on every machine that clones, commits, pushes,
  or runs `adopt`, and in CI.  Without it: a checkout gives pointers,
  silently; a commit of a tracked type stores a plain blob, silently; a
  push is refused by the hook.
- Host storage: each release's archives, each version of a STEP or PDF,
  kept for good; not freed by deleting the file.  Bandwidth: each clone
  and each fetch of a new revision downloads HEAD's objects.  The
  allowances are the host's and the account's (above).  Not verified:
  a board repository's archives per release against them.
- A submodule consumer: a library repository in LFS, as a board
  repository's submodule, gives its STEP files only on a machine with
  global filters (`git lfs install`, the user's own step).  `adopt`'s
  `--local` filters do not reach it.  A board repository's clone of
  ee_tooling is unchanged.

## Library repositories

Deferred: `adopt` does not serve a library repository.  A library
repository copies the lines of `src/ee_tooling/templates/gitattributes`
into its own `.gitattributes` by hand; nothing checks them there.

## Migration

Part of the fab-release flag day, released in 0.4.0.  A board
repository migrating to 0.4.0 also installs git-lfs on each machine,
reruns `adopt --yes`, and commits with `git add --renormalize .`.

Breaking changes, under `docs/releasing.md` (a board repository must
act), items 34 to 39 of fab-release's list (`docs/design/fab-release.md`,
Migration, Git LFS):

1. `adopt` and `adopt --check` require git-lfs.
2. `adopt --check` fails on a plain blob of a tracked type at HEAD: a
   repository with committed archives, PDFs, STEP or VRML files must
   renormalize and commit.
3. `adopt --yes` writes `.gitattributes`'s section and
   `.githooks/pre-push`; a board repository's own `pre-push` is
   replaced.
4. A push without git-lfs is refused.
5. The release refuses a board whose inputs hold an unfetched LFS
   pointer.
6. A board repository's host must support Git LFS: a push to one that
   does not is refused.

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
  chosen: they stay committed under the production directory.  Not
  foreclosed.
- The flag day grows: each board repository's 0.4.0 migration adds
  git-lfs on every machine and a renormalize commit to fab-release's
  day of work.
- `--check` on HEAD only: history keeps every plain blob, and a clone
  stays as large as it was.  Shrinking it is a history rewrite the tool
  refuses to make.
- Library repositories copy the section by hand: it goes stale there
  when the section changes, and nothing checks it.
- Global filters are the user's: `adopt` does not write `~/.gitconfig`,
  so a library submodule in LFS needs the user's own `git lfs install`.
- Replacing a board repository's own `pre-push` loses what it did;
  `adopt` prints its text first, and the hook is a place for
  ee_tooling's checks only.
- No opt-out: a board repository on a host without Git LFS cannot push
  once adopted.
- git-lfs's behavior with a foreign `pre-push` (installing no hooks) is
  observed, not documented; a later git-lfs may write the `post-*`
  hooks into `.githooks` as untracked files.
- `fsck --pointers` also fails on a non-canonical pointer; the
  renormalize fix covers it.
- Patterns are global: a repository that wants a `.zip` plain overrides
  it after the section; one that wants another type tracked adds it
  after the section, or asks for it in ee_tooling.
- Foreclosed: nothing kept plain is hard to track later (a new
  pattern and a renormalize); a tracked type made plain again needs
  `git lfs migrate export` or a renormalize the other way.
