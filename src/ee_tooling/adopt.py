"""Put ee_tooling in place in a board repository (docs/adopting.md).

Runs from the board repository's ee_tooling submodule (its `adopt`
script), whatever the directory.  Without --yes it prints what it would
do; with --yes it does it; with --check it fails if a file it manages
differs from what this ee_tooling would write, or if a file of a type Git
LFS tracks is a plain blob at HEAD.  Every mode needs git-lfs.  It never
overwrites a file the project owns once written, but to migrate one
ee_tooling renamed (jlcpcb.toml, the release's skill).
"""

import argparse
import codecs
import fnmatch
import importlib.resources
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ee_tooling.atomic import LEFTOVER, write_text
from ee_tooling.fab_release import (
    CONFIG,
    DRU,
    FAB,
    GONE_KEYS,
    LFS_POINTER_SIZE,
    LFS_VERSIONS,
    string,
    strings,
)
from ee_tooling.git_environment import environment
from ee_tooling.kicad_pcbnew import SYSTEM_PYTHON, unsupported
from ee_tooling.kicad_rules import (
    ERC_SETTINGS,
    Dru,
    board_setup,
    copied_message,
    dropped,
    erc_set,
    install,
    installed,
    raised,
    resource,
    retired_message,
)
from ee_tooling.profiles import PROFILES
from ee_tooling.profiles.jlcpcb import Jlcpcb
from ee_tooling.project_libraries import libraries
from ee_tooling.sexpr import ParseError, parse

# This ee_tooling's checkout: src/ee_tooling/adopt.py's grandparent.
CHECKOUT = Path(__file__).resolve().parents[2]
HOOKS_PATH = ".githooks"
AGENTS = Path("AGENTS.md")
AGENTS_BEGIN = "<!-- ee_tooling: begin; written by ee-tooling-adopt -->"
AGENTS_END = "<!-- ee_tooling: end -->"
# .gitattributes's section, first in the file: Git LFS's patterns.
GITATTRIBUTES = Path(".gitattributes")
GITATTRIBUTES_BEGIN = "# ee_tooling: begin; written by ee-tooling-adopt"
GITATTRIBUTES_END = "# ee_tooling: end; the project's own lines follow and win"
# The files adopt writes a section of, the rest of each the project's.
SECTION_FILES = (AGENTS, GITATTRIBUTES)
# The hook that uploads Git LFS objects; one without WRITTEN_BY_ADOPT in it
# is the project's own, which adopt replaces, saying so.
PRE_PUSH = Path(HOOKS_PATH, "pre-push")
WRITTEN_BY_ADOPT = "(ee_tooling's docs/adopting.md)"
# git-lfs's filters for this clone only, in .git/config, and no hooks: the
# managed pre-push is git-lfs's.
LFS_INSTALL = ("git", "lfs", "install", "--local", "--skip-repo")
LFS_PULL = ("git", "lfs", "pull")
# --check's label for the files of a type Git LFS tracks stored as plain
# blobs at HEAD, and how to store them through Git LFS.
NOT_IN_LFS = "not stored through Git LFS"
RENORMALIZE = (
    "with git-lfs's filters set ({adopt} --yes sets them), run git add -A && git add"
    " --renormalize . and commit"
)
# What a hand-adopted AGENTS.md has: the house style's import line.
STYLE = "docs/kicad-style.md"
# The profile of each board adopt finds.
PROFILE = Jlcpcb.name
# The release's skill in a board repository, from the root.
SKILL = Path(".claude/skills/fab-release/SKILL.md")
# Where adopt puts a lone board's releases, from the root.
PRODUCTION = Path("production")
# What ee_tooling renamed, in a board repository: the release's old config
# and its old [[board]] keys fab.toml has, the old skill's directory, and the names in
# it, old to new.
OLD_CONFIG = Path("jlcpcb.toml")
OLD_KEYS = ("name", "directory", "production", "sources")
OLD_SKILL = Path(".claude/skills/jlcpcb-release")
RENAMED = (("jlcpcb-release", "fab-release"), (str(OLD_CONFIG), CONFIG))
# A line of fab.toml that starts a [[board]], and one that starts its
# [board.fab].
BOARD_HEADER = re.compile(r"^[ \t]*\[\[[ \t]*board[ \t]*\]\][ \t]*(#.*)?$", re.M)
# A TOML one-line string, basic or literal.
TOML_STRING = re.compile(r""""(?:[^"\\]|\\.)*"|'[^']*'""")
FAB_HEADER = re.compile(rf"^[ \t]*\[[ \t]*board[ \t]*\.[ \t]*{FAB}[ \t]*\][ \t]*(#.*)?$", re.M)


# A UTF-8 byte order mark, as text.
BOM = "\ufeff"


def read(path: Path) -> str:
    """A file's text, without a leading UTF-8 byte order mark, its line
    endings \n (write restores both); ValueError naming it if it is not
    UTF-8."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"{path}: not UTF-8 (byte {e.start}: {e.reason})") from e


class Owner(Enum):
    """Who keeps a file up to date once it is written."""

    MANAGED = "managed"  # ee-tooling-adopt: a rerun rewrites it, --check compares it
    PROJECT = "project"  # the board repository: written only if missing


@dataclass(frozen=True)
class File:
    """A file to write, from the repository root."""

    path: Path
    text: str
    owner: Owner
    executable: bool = False

    def action(self, root: Path) -> str | None:
        """create, update or make executable, or None when nothing is to be
        done."""
        target = root / self.path
        if not target.exists():
            return "create"
        if self.owner is Owner.MANAGED and read(target) != self.text:
            return "update"
        if self.owner is Owner.MANAGED and self.executable and not executable(root, self.path):
            return "make executable"
        return None


@dataclass(frozen=True)
class Command:
    """A command to run, in a directory from the repository root."""

    args: tuple[str, ...]
    directory: Path = Path()
    writes: tuple[Path, ...] = ()  # what it writes or clears, from the root: no link allowed


@dataclass(frozen=True)
class Remove:
    """A project file ee_tooling does not read, from the repository root,
    removed unless git does not hold it as it is."""

    path: Path
    why: str  # what replaced it
    kept: str | None = None  # why it is kept instead: git would not have it back


@dataclass(frozen=True)
class Rename:
    """A project directory ee_tooling renamed, from the repository root,
    with the names ee_tooling renamed renamed in its Markdown files."""

    source: Path
    target: Path
    names: tuple[tuple[str, str], ...]  # old, new


@dataclass(frozen=True)
class Edit:
    """A project file ee_tooling adds to, its own text kept, from the
    repository root."""

    path: Path
    text: str  # the file, edited
    why: str  # what was added


Step = File | Command | Remove | Rename | Edit


@dataclass(frozen=True)
class Layout:
    """Where ee_tooling and the hardware tools' environment are."""

    root: Path
    submodule: Path  # from the root

    @property
    def hardware(self) -> Path:
        """The hardware tools' uv project, the submodule's parent."""
        return self.submodule.parent

    def fill(self, template: str) -> str:
        text = importlib.resources.files(__package__).joinpath("templates", template).read_text()
        return (
            text.replace("@EE_TOOLING@", self.submodule.as_posix())
            .replace("@HARDWARE@", self.hardware.as_posix())
            .replace("@NAME@", package_name(self.root.name))
        )


# A valid Python package name (PEP 508), which uv requires of a project's;
# and what package_name makes of the rest.
PACKAGE_NAME = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.I)
NOT_IN_A_PACKAGE_NAME = re.compile(r"[^A-Za-z0-9]+")
# The name when nothing of the directory's is left.
DEFAULT_PACKAGE_NAME = "board"


def package_name(directory: str) -> str:
    """A package name from the repository's directory name: each run of
    other than ASCII letters and digits a hyphen, lowercased."""
    name = NOT_IN_A_PACKAGE_NAME.sub("-", directory).strip("-").lower()
    return name or DEFAULT_PACKAGE_NAME


def git(directory: Path, *args: str) -> str:
    """git's output, or "" if it fails (a missing key, no superproject)."""
    r = subprocess.run(
        ["git", *args],
        cwd=directory,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip()


# A tracked file's mode in git's index when it is executable.
EXECUTABLE_MODE = "100755"


def executable(root: Path, path: Path) -> bool:
    """Whether git runs the file, from root, as a hook, and every clone
    would: executable in the work tree (git runs a hook only so, whatever
    core.fileMode says), and, when git tracks it, in the index (what a
    clone checks out)."""
    if not (root / path).stat().st_mode & 0o111:
        return False
    staged = git(root, "ls-files", "-s", "--", path.as_posix())
    return not staged or staged.split()[0] == EXECUTABLE_MODE


def lfs_missing(submodule: Path) -> str | None:
    """Why git-lfs cannot run here, or None if it can: git's error's first
    line, and the doc's path from the board repository's root."""
    r = subprocess.run(
        ["git", "lfs", "version"], env=environment(), capture_output=True, text=True, check=False
    )
    if r.returncode == 0:
        return None
    error = next(iter(r.stderr.strip().splitlines()), str(r.returncode))
    return (
        f"git-lfs is missing (git lfs version: {error}); install the distribution's git-lfs"
        f" package ({submodule.as_posix()}/docs/adopting.md)"
    )


def git_lfs(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """git lfs args, in root, leaving the repository's hooks and config as
    they are.  git-lfs sets lfs.repositoryformatversion in .git/config when
    it is unset, through `git config`, which reads and writes the file
    GIT_CONFIG names instead (git-config(1)): here a scratch copy of the
    repository's config, so git-lfs reads its settings and its write is
    dropped.  With no pre-push in core.hooksPath, git-lfs's fsck installs its
    four hooks there (.githooks, before --yes writes the managed one): here
    a scratch directory."""
    with tempfile.TemporaryDirectory() as scratch:
        config = Path(scratch, "config")
        local = git(root, "rev-parse", "--git-path", "config")
        config.write_bytes((root / local).read_bytes() if local else b"")
        # git-lfs reads core.hooksPath from that file, not from -c.
        git(root, "config", "--file", str(config), "core.hooksPath", str(Path(scratch, "hooks")))
        return subprocess.run(
            ["git", "lfs", *args],
            cwd=root,
            env={**environment(), "GIT_CONFIG": str(config)},
            capture_output=True,
            text=True,
            check=False,
        )


def lfs_unfetched(root: Path) -> bool:
    """Whether a file Git LFS tracks at HEAD is a pointer in the work tree
    (git lfs ls-files marks it -, a fetched one *); False without git-lfs
    (main refuses to run then).  ValueError with git-lfs's error if
    ls-files fails otherwise."""
    r = git_lfs(root, "ls-files")
    if r.returncode != 0:
        if shutil.which("git-lfs") is None:
            return False
        error = r.stderr.strip() or r.stdout.strip()
        raise ValueError(f"git lfs ls-files failed ({r.returncode}): {error}")
    return any(line.split(" ", 2)[1:2] == ["-"] for line in r.stdout.splitlines())


# How git lfs fsck --pointers starts each line naming a file whose blob is
# not a canonical pointer (a plain blob, a pointer written otherwise).
FSCK_FINDING = "pointer: "


def fsck_message(report: str, submodule: Path) -> str:
    """--check's message for plain_blobs's report: the files and the fix
    when each line names a file, or that fsck itself failed."""
    if all(line.startswith(FSCK_FINDING) for line in report.splitlines()):
        fix = RENORMALIZE.format(adopt=adopt_command(submodule))
        return f"{report}\n{NOT_IN_LFS}: the files above; {fix}"
    return (
        f"{report}\ngit lfs fsck --pointers HEAD failed (its error is above): fix that, then rerun"
    )


def plain_blobs(root: Path) -> str | None:
    """git lfs fsck --pointers's report of the files of a type Git LFS
    tracks stored at HEAD as plain blobs, or None if there are none or no
    HEAD."""
    if not git(root, "rev-parse", "--verify", "-q", "HEAD"):
        return None
    r = git_lfs(root, "fsck", "--pointers", "HEAD")
    return None if r.returncode == 0 else f"{r.stdout}{r.stderr}".strip()


def locate(checkout: Path) -> Layout:
    """The board repository that has checkout as its submodule."""
    superproject = git(checkout, "rev-parse", "--show-superproject-working-tree")
    if not superproject:
        raise ValueError(f"{checkout} is not a submodule of a board repository")
    root = Path(superproject)
    return Layout(root, checkout.relative_to(root))


def toml_string(text: str) -> str:
    """text as a TOML basic string: JSON's escapes are TOML's, but a
    surrogate pair's (a character beyond the BMP, which TOML takes
    as it is)."""
    return json.dumps(text, ensure_ascii=False)


def toml_value(value: object) -> str:
    """A string, number or boolean, as TOML writes it."""
    match value:
        case bool():
            return "true" if value else "false"
        case int() | float():
            return str(value)
        case str():
            return toml_string(value)
    raise TypeError(f"{value!r} is not a string, number or boolean")


def fab_lines(defaults: dict[str, Any], keys: Iterable[str]) -> list[str]:
    """[board.fab]'s keys, each with its house value."""
    return [f"{k} = {toml_value(defaults[k])}" for k in keys]


def board_lines(name: str, directory: str, production: str, sources: list[str]) -> list[str]:
    """A [[board]] of fab.toml, with adopt's profile and its house
    [board.fab] values."""
    defaults = PROFILES[PROFILE]().fab_defaults
    return [
        "",
        "[[board]]",
        f"name = {toml_string(name)}",
        f"directory = {toml_string(directory)}",
        f"production = {toml_string(production)}",
        f"sources = [{', '.join(toml_string(s) for s in sources)}]",
        f"profile = {toml_string(PROFILE)}",
        "",
        f"[board.{FAB}]",
        *fab_lines(defaults, defaults),
    ]


def fab_added(text: str) -> tuple[str, list[str]] | None:
    """fab.toml's text with the [board.fab] keys each board lacks added,
    with its profile's house values, its comments kept, and what was added;
    None if none lacks one.  A board whose profile is not known is left to
    fab-release.  ValueError if the text cannot be edited so: a fab given
    other than as a [board.fab] table, or a [[board]] not found by its
    header."""
    config = tomllib.loads(text)
    boards = config.get("board", [])
    if not isinstance(boards, list) or not all(isinstance(b, dict) for b in boards):
        return None
    # Each board's missing keys, and its house values.
    missing: list[tuple[list[str], dict[str, Any]]] = []
    for b in boards:
        found = b.get(FAB, {})
        if not isinstance(found, dict):
            raise ValueError(f"[[board]] {b.get('name')!r}: {FAB!r} is not a table")
        defaults = PROFILES[b["profile"]]().fab_defaults if b.get("profile") in PROFILES else {}
        missing.append(([k for k in defaults if k not in found], defaults))
    if not any(keys for keys, _ in missing):
        return None
    headers = list(BOARD_HEADER.finditer(text))
    if len(headers) != len(boards):
        raise ValueError(f"{len(boards)} boards, {len(headers)} [[board]] headers")
    ends = [h.start() for h in headers[1:]] + [len(text)]
    added = []
    for b, header, end, (keys, defaults) in reversed(
        list(zip(boards, headers, ends, missing, strict=True))
    ):
        if not keys:
            continue
        lines = "\n".join(fab_lines(defaults, keys))
        table = FAB_HEADER.search(text, header.end(), end)
        if table is not None:
            text = f"{text[: table.end()]}\n{lines}{text[table.end() :]}"
        elif FAB in b:
            raise ValueError(f"[[board]] {b.get('name')!r}: {FAB!r} is not a [board.{FAB}] table")
        else:
            at = header.start() + len(text[header.start() : end].rstrip())
            text = f"{text[:at]}\n\n[board.{FAB}]\n{lines}{text[at:]}"
        added.append(f"[[board]] {b.get('name')!r}: {', '.join(keys)}")
    after = tomllib.loads(text).get("board", [])
    if any(
        k not in b.get(FAB, {}) for b, (keys, _) in zip(after, missing, strict=True) for k in keys
    ):
        raise ValueError("the keys added are not where they belong")
    return text, list(reversed(added))


def productions(layout: Layout) -> list[Path]:
    """The production directories, from the root: adopt's, and each that
    jlcpcb.toml or fab.toml names."""
    out = [PRODUCTION]
    for config in (OLD_CONFIG, Path(CONFIG)):
        path = layout.root / config
        if not path.exists():
            continue
        try:
            boards = tomllib.loads(read(path)).get("board", [])
        except (tomllib.TOMLDecodeError, ValueError) as e:
            print(f"{config}: {e}; its production directories not skipped", file=sys.stderr)
            continue
        out += [
            Path(b["production"])
            for b in boards
            if isinstance(b, dict) and isinstance(b.get("production"), str)
        ]
    return out


def projects(layout: Layout) -> list[Path]:
    """The KiCad projects (*.kicad_pro) under the hardware directory, but
    in the submodule, a production directory, a hidden directory (.git,
    .venv, .claude's worktrees) or another repository (a directory with a
    .git: a library's submodule, a clone)."""
    root = layout.root
    skip = {root / layout.submodule, *(root / p for p in productions(layout))}
    found = []
    for here, directories, names in (root / layout.hardware).walk():
        directories[:] = [
            d
            for d in directories
            if not d.startswith(".")
            and here / d not in skip
            and not os.path.lexists(here / d / ".git")
        ]
        found += [here / n for n in names if n.endswith(".kicad_pro")]
    return sorted(found)


def fab_config(layout: Layout) -> str | None:
    """A starting fab.toml, one [[board]] per KiCad project under the
    hardware directory, or None if there is none.  ValueError naming two
    projects of one name, which fab.toml cannot tell apart."""
    found = projects(layout)
    if not found:
        return None
    by_name: dict[str, list[Path]] = {}
    for project in found:
        by_name.setdefault(project.stem, []).append(project.relative_to(layout.root))
    for name, paths in by_name.items():
        if len(paths) > 1:
            raise ValueError(
                f"KiCad projects of one name, {name!r}: {', '.join(p.as_posix() for p in paths)};"
                f" {CONFIG} names a board by its project: write {CONFIG} by hand, or rename one"
            )
    lines = [
        "# The boards fab-release builds; the first is the default.  Paths are",
        "# from the repository root.  Written by ee-tooling-adopt: check each",
        "# board's production directory and [board.fab], and add its sources.",
    ]
    for project in found:
        # One board owns production/; several each get their own directory.
        production = PRODUCTION if len(found) == 1 else PRODUCTION / project.stem
        lines += board_lines(
            project.stem,
            project.parent.relative_to(layout.root).as_posix(),
            production.as_posix(),
            [],
        )
    return "\n".join(lines) + "\n"


# The comment lines an earlier adopt wrote at the top of jlcpcb.toml, not
# the project's.
OLD_HEADER = frozenset(
    {
        "# The boards jlcpcb-release builds; the first is the default.  Paths are",
        "# from the repository root.  Written by ee-tooling-adopt: check each",
        "# board's production directory and add its sources.",
    }
)


def has_comment(text: str) -> bool:
    """Whether TOML text has a comment: a # outside a one-line string."""
    return any("#" in TOML_STRING.sub("", line) for line in text.splitlines())


def migrated_config(text: str) -> str:
    """fab.toml from jlcpcb.toml's text: its boards, each with adopt's
    profile.  Its comments are not carried over, nor its keys fab.toml does
    not have, each named on stderr.  ValueError naming a bad key."""
    old = tomllib.loads(text)
    lines = [
        "# The boards fab-release builds; the first is the default.  Paths are",
        f"# from the repository root.  Migrated from {OLD_CONFIG} by ee-tooling-adopt:",
        "# check each board's [board.fab].",
    ]
    own = "".join(
        line for line in text.splitlines(keepends=True) if line.rstrip() not in OLD_HEADER
    )
    if has_comment(own):
        print(
            f"{OLD_CONFIG}: its comments not carried over to {CONFIG}; they stay in git's"
            f" history (git show HEAD:{OLD_CONFIG})",
            file=sys.stderr,
        )
    for key in sorted(set(old) - {"board"}):
        print(f"{OLD_CONFIG}: {key!r} not carried over to {CONFIG}", file=sys.stderr)
    boards = old.get("board", [])
    if not isinstance(boards, list) or not all(isinstance(b, dict) for b in boards):
        raise ValueError("no [[board]]")
    for number, table in enumerate(boards, 1):
        where = f"[[board]] {number}"
        name = string(where, table, "name", None)
        where = f"[[board]] {name!r}"
        for key in sorted(set(table) - set(OLD_KEYS)):
            why = f": {GONE_KEYS[key]}" if key in GONE_KEYS else ""
            print(
                f"{OLD_CONFIG}: {where}: {key!r} not carried over to {CONFIG}{why}", file=sys.stderr
            )
        lines += board_lines(
            name,
            string(where, table, "directory", None),
            string(where, table, "production", None),
            strings(where, table, "sources", []),
        )
    return "\n".join(lines) + "\n"


def unsaved(root: Path, path: Path) -> str | None:
    """Why git could not give path back if it were removed (untracked, or
    with uncommitted changes), or None."""
    if not git(root, "ls-files", "--", path.as_posix()):
        return "not committed"
    if git(root, "status", "--porcelain", "--", path.as_posix()):
        return "with uncommitted changes"
    return None


# The project files the release needs: --check names one missing.
RELEASE_NEEDS = (Path(CONFIG),)
# Why a scratch file of an interrupted write is removed.
LEFTOVER_WHY = "left by a write that was interrupted"
# Directories not searched for them: git's, the venv's.
NOT_SEARCHED = {".git", ".venv"}


def leftovers(root: Path) -> list[Path]:
    """The scratch files an interrupted write left (atomic.LEFTOVER), from
    root; not in another repository (a directory with a .git)."""
    found = []
    for here, directories, names in root.walk():
        directories[:] = [
            d
            for d in directories
            if d not in NOT_SEARCHED and not os.path.lexists(here / d / ".git")
        ]
        found += [(here / n).relative_to(root) for n in names if LEFTOVER.match(n)]
    return sorted(found)


def migrations(layout: Layout) -> list[Remove | Rename | Edit]:
    """What moves the project files ee_tooling renamed to their new names
    (a file's new text is among files()), and adds the keys fab.toml
    lacks.  ValueError naming what cannot be done."""
    root = layout.root
    out: list[Remove | Rename | Edit] = []
    if (root / CONFIG).exists():
        try:
            edited = fab_added(read(root / CONFIG))
        except ValueError as e:  # tomllib.TOMLDecodeError among them
            raise ValueError(f"{CONFIG}: {e}; add each [board.{FAB}] key by hand") from e
        if edited is not None:
            text, added = edited
            out.append(Edit(Path(CONFIG), text, f"[board.{FAB}] keys added: {'; '.join(added)}"))
    for old, new in ((OLD_CONFIG, Path(CONFIG)), (OLD_SKILL, SKILL.parent)):
        if not (root / old).exists():
            continue
        if (root / new).exists():
            print(f"{old} and {new}: {old} is not read; remove it", file=sys.stderr)
        elif old == OLD_CONFIG:
            out.append(Remove(old, f"migrated to {new}", unsaved(root, old)))
        else:
            out.append(Rename(old, new, RENAMED))
    return out


def project_json(project: dict[str, Any]) -> str:
    """A .kicad_pro's text, indented as KiCad writes it."""
    return json.dumps(project, indent=2, ensure_ascii=False) + "\n"


# KiCad's lock file for a file it has open: ~NAME.EXT.lck beside it
# (libkicommon's "~*.lck"), for the project, the board and the schematic.
LOCK = "~{}.lck"
LOCKED_SUFFIXES = (".kicad_pro", ".kicad_pcb", ".kicad_sch")


def open_in_kicad(root: Path, directory: Path, name: str) -> Path | None:
    """The first lock file KiCad keeps for project name's files in
    directory, from root, or None."""
    for suffix in LOCKED_SUFFIXES:
        lock = directory / LOCK.format(f"{name}{suffix}")
        if (root / lock).exists():
            return lock
    return None


def foreign_directory(root: Path, directory: Path) -> str | None:
    """Why directory, from root, is not the repository's own: outside it, or
    in another repository (a directory with a .git, a submodule); or
    None."""
    top = root.resolve()
    target = (root / directory).resolve()
    if not target.is_relative_to(top):
        return "is outside the repository"
    inside = target.relative_to(top)
    for d in [inside, *inside.parents]:
        if d != Path() and os.path.lexists(top / d / ".git"):
            return f"is in another repository ({d.as_posix()})"
    return None


def rules_edits(root: Path, config: str | None) -> list[Edit]:
    """What installs each board's profile's rules in its .kicad_dru, raises
    its Board Setup in its .kicad_pro and sets its ERC severities (kept
    where stricter) and connection grid there, by fab.toml's text (config),
    each checked once edited.  A board whose profile is not known, or whose
    .kicad_pro is not there, is left, said on stderr.  ValueError naming
    what cannot be done."""
    if config is None:
        return []
    try:
        boards = tomllib.loads(config).get("board", [])
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{CONFIG}: {e}") from e
    out = []
    for b in boards if isinstance(boards, list) else []:
        if not isinstance(b, dict) or b.get("profile") not in PROFILES:
            continue
        name, directory = b.get("name"), b.get("directory")
        if not isinstance(name, str) or not isinstance(directory, str):
            continue
        if name in (".", "..", "") or "/" in name or "\\" in name:
            raise ValueError(
                f"{CONFIG}: [[board]] {name!r}: a board's name is its project's file name, one"
                " path component: adopt edits no file by it"
            )
        if (why := foreign_directory(root, Path(directory))) is not None:
            raise ValueError(
                f"{CONFIG}: [[board]] {name!r}: directory {directory!r} {why}: adopt edits no"
                " file there"
            )
        for destination in (f"{name}.kicad_pro", f"{name}{DRU}"):
            where = (root / directory / destination).resolve().parent
            if (why := foreign_directory(root, where)) is not None:
                raise ValueError(
                    f"{CONFIG}: [[board]] {name!r}: {Path(directory, destination).as_posix()}"
                    f" {why}: adopt edits no file there"
                )
        profile = PROFILES[b["profile"]]()
        try:
            options = profile.options(b.get(profile.name, {}))
        except ValueError as e:
            raise ValueError(f"{CONFIG}: [[board]] {name!r}: [board.{profile.name}]: {e}") from e
        project, dru = Path(directory, f"{name}.kicad_pro"), Path(directory, f"{name}{DRU}")
        if not (root / project).exists():
            print(f"{project.as_posix()} is not there: rules not installed", file=sys.stderr)
            continue
        section = installed(profile.name, profile.rules, options)
        least = board_setup(profile.rules)
        house = json.loads(resource(ERC_SETTINGS))
        by_hand = "install the rules by hand"
        try:
            settings = json.loads(read(root / project))
        except ValueError as e:
            raise ValueError(f"{project.as_posix()}: {e}; {by_hand}") from e
        existing = read(root / dru) if (root / dru).exists() else None
        try:
            found = install(existing, settings, section, least)
        except ValueError as e:
            raise ValueError(f"{dru.as_posix()}: {e}; {by_hand}") from e
        settings, erc = erc_set(found.project, house)
        if (
            Dru.split(found.dru).section != found.section
            or raised(settings, least)[1]
            or erc_set(settings, house)[1]
        ):
            raise ValueError(f"[[board]] {name!r}: the rules are not where they belong; {by_hand}")
        if found.copied:
            print(copied_message(dru.as_posix(), found.copied), file=sys.stderr)
        if found.retired:
            print(retired_message(dru.as_posix(), found.retired), file=sys.stderr)
        close = "close the board in KiCad first"
        count, names = dropped(Dru.split(existing).section, found.section)
        if found.stale and count:
            rules = f"; rules: {', '.join(names)}" if names else ""
            print(
                f"{dru.as_posix()}: {count} lines between ee_tooling's markers are not"
                f" ee_tooling's and go{rules}",
                file=sys.stderr,
            )
        if found.stale:
            out.append(
                Edit(dru, found.dru, f"ee_tooling's {profile.name} rules installed; {close}")
            )
        what = []
        if found.raised:
            what.append(
                f"Board Setup raised to {profile.name}'s minimums: {'; '.join(found.raised)}"
            )
        if erc:
            what.append(f"ERC severities and connection grid set: {'; '.join(erc)}")
        if (found.stale or what) and (lock := open_in_kicad(root, Path(directory), name)):
            raise ValueError(
                f"{lock.as_posix()}: KiCad has {name} open; close it, then rerun (adopt edits"
                f" {dru.name} and {project.name}, which KiCad would overwrite)"
            )
        if what:
            out.append(Edit(project, project_json(settings), f"{'; '.join(what)}; {close}"))
    return out


@dataclass(frozen=True)
class Markers:
    """The lines around ee_tooling's section of a file the project shares,
    and where a file without the section gets it."""

    begin: str
    end: str
    first: bool  # prepended (the project's lines after it win), else appended


AGENTS_MARKERS = Markers(AGENTS_BEGIN, AGENTS_END, first=False)
GITATTRIBUTES_MARKERS = Markers(GITATTRIBUTES_BEGIN, GITATTRIBUTES_END, first=True)


def marked_span(text: str, markers: Markers) -> tuple[int, int] | None:
    """Where the section is in text, from its begin line's start to its end
    line's end, or None if neither marker is a line of it (a marker within
    a line is the project's text).  ValueError unless the markers are one
    begin line, then one end line."""

    def lines(marker: str) -> list[re.Match[str]]:
        return list(re.finditer(rf"^{re.escape(marker)}[ \t]*$", text, re.M))

    begins, ends = lines(markers.begin), lines(markers.end)
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].start():
        raise ValueError("ee_tooling's markers are not one begin line, then one end line")
    return begins[0].start(), ends[0].end()


def marked_text(existing: str | None, block: str, markers: Markers) -> str:
    """A file with ee_tooling's section, block between the markers: the
    section replaced in place, or added where markers says; the rest of the
    file kept byte for byte (but a file without the section, appended to,
    gets one blank line before it).  ValueError, from marked_span, if the
    markers are not where they belong."""
    section = f"{markers.begin}\n{block.rstrip()}\n{markers.end}\n"
    if existing is None:
        return section
    span = marked_span(existing, markers)
    if span is not None:
        begin, end = span
        after = existing[end:]
        # The end line's own line ending is the section's.
        return existing[:begin] + section + after.removeprefix("\r\n").removeprefix("\n")
    if markers.first:
        return section + existing
    return existing.rstrip("\n") + "\n\n" + section


def agents_text(existing: str | None, block: str) -> str | None:
    """AGENTS.md with ee_tooling's section, or None if it was adopted by
    hand (the house style imported outside the markers).  ValueError, from
    marked_span."""
    if existing is not None and STYLE in existing and marked_span(existing, AGENTS_MARKERS) is None:
        return None
    return marked_text(existing, block, AGENTS_MARKERS)


def warn_replaced_hook(root: Path) -> None:
    """Say loudly, with its text, that the project's own pre-push is
    replaced by ee_tooling's."""
    hook = root / PRE_PUSH
    if not hook.exists():
        return
    text = hook.read_text(errors="replace")
    if WRITTEN_BY_ADOPT in text or git_lfs_stock_hook(hook):
        return
    print(
        f"WARNING: {PRE_PUSH} is the project's own, not ee_tooling's: --yes replaces it with"
        f" ee_tooling's, which uploads Git LFS objects ({HOOKS_PATH} is ee_tooling's); move"
        f" what it does elsewhere.  Its text:\n{text}",
        file=sys.stderr,
    )


# A file git keeps in a hooks directory that is not a hook.
SAMPLE_SUFFIX = ".sample"


# git-lfs's own hook, as git-lfs installs it (3.x): its hook's name is the
# git lfs subcommand it runs.
LFS_STOCK_HOOK = re.compile(
    r"^#!/bin/sh\ncommand -v git-lfs >/dev/null 2>&1 \|\| \{ printf >&2 .*; exit 2; \}\n"
    r'git lfs (?P<name>[a-z-]+) "\$@"\n?$'
)


def git_lfs_stock_hook(hook: Path) -> bool:
    """Whether hook is git-lfs's own, as installed at a clone: one that
    need not run from .githooks (the post-* serve file locking only, the
    managed pre-push replaces pre-push)."""
    try:
        text = hook.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    found = LFS_STOCK_HOOK.match(text)
    return found is not None and found.group("name") == hook.name


def warn_disabled_hooks(root: Path) -> None:
    """Say loudly, naming each, which hooks stop running when core.hooksPath
    becomes .githooks: those in the hooks directory git uses now (.git/hooks,
    or another core.hooksPath such as .husky), when that is not .githooks."""
    current = git(root, "rev-parse", "--git-path", "hooks")
    if not current:
        return
    directory = (root / current).resolve()
    if directory == (root / HOOKS_PATH).resolve() or not directory.is_dir():
        return
    hooks = sorted(
        f.name
        for f in directory.iterdir()
        if f.is_file()
        and not f.name.endswith(SAMPLE_SUFFIX)
        and os.access(f, os.X_OK)
        and not git_lfs_stock_hook(f)
    )
    if not hooks:
        return
    where = (
        directory.relative_to(root.resolve())
        if directory.is_relative_to(root.resolve())
        else directory
    )
    print(
        f"WARNING: core.hooksPath is to be {HOOKS_PATH} (--yes), so these hooks git runs"
        f" now from {where.as_posix()} stop running: {', '.join(hooks)}",
        file=sys.stderr,
    )


def uses_ee_tooling(pyproject: Path) -> bool:
    """Whether the hardware tools' project has ee_tooling as a source; said
    on stderr if its name is not a package name uv accepts (the project's
    file, never rewritten: rename it there)."""
    if not pyproject.exists():
        return False
    project = tomllib.loads(read(pyproject))
    name = project.get("project", {}).get("name")
    if isinstance(name, str) and not PACKAGE_NAME.match(name):
        print(
            f"{pyproject}: [project] name {name!r} is not a package name uv accepts:"
            f" rename it, say to {package_name(name)!r}",
            file=sys.stderr,
        )
    sources = project.get("tool", {}).get("uv", {}).get("sources", {})
    return "ee-tooling" in sources


def system_site_packages(venv: Path) -> bool:
    config = venv / "pyvenv.cfg"
    return config.exists() and "include-system-site-packages = true" in config.read_text()


# The characters a submodule's path may have, which the hooks and the CI
# workflow quote safely (a space in double quotes): not $, `, ", ', ;, a
# newline, a backslash, which sh or YAML would take for code.
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/ +-]+$")


def adopt_command(submodule: Path) -> str:
    """The submodule's adopt, as a shell command."""
    return shlex.quote(f"{submodule.as_posix()}/adopt")


def files(layout: Layout) -> list[File]:
    """The files ee_tooling puts in a board repository.  ValueError if the
    submodule's path has a character the templates cannot carry safely."""
    if not SAFE_PATH.match(layout.submodule.as_posix()):
        raise ValueError(
            f"the submodule's path {layout.submodule.as_posix()!r} has a character the hooks and"
            " the CI workflow cannot carry safely (they take letters, digits, a space and"
            " . _ / + -): move the submodule (git mv), then rerun; nothing written"
        )
    root = layout.root
    out = [
        File(Path(HOOKS_PATH, "commit-msg"), layout.fill("commit-msg"), Owner.MANAGED, True),
        File(PRE_PUSH, layout.fill("pre-push"), Owner.MANAGED, True),
        File(
            Path(".github/workflows/ee-tooling.yml"), layout.fill("ee-tooling.yml"), Owner.MANAGED
        ),
        File(
            layout.hardware / "pyproject.toml",
            layout.fill("hardware-pyproject.toml"),
            Owner.PROJECT,
        ),
    ]
    warn_replaced_hook(root)
    warn_disabled_hooks(root)
    attributes = root / GITATTRIBUTES
    existing = read(attributes) if attributes.exists() else None
    try:
        attributes_text = marked_text(existing, layout.fill("gitattributes"), GITATTRIBUTES_MARKERS)
    except ValueError as e:
        raise ValueError(f"{GITATTRIBUTES}: {e}; nothing written: fix them by hand") from e
    out.append(File(GITATTRIBUTES, attributes_text, Owner.MANAGED))
    agents = root / AGENTS
    agents_existing = read(agents) if agents.exists() else None
    try:
        text = agents_text(agents_existing, layout.fill("agents.md"))
    except ValueError as e:
        raise ValueError(f"{AGENTS}: {e}; nothing written: fix them by hand") from e
    if text is None:
        print(f"{AGENTS}: adopted by hand; left as it is", file=sys.stderr)
    else:
        out.append(File(AGENTS, text, Owner.MANAGED))
    # A skill to be renamed (migrations()) is not written anew.
    if not (root / OLD_SKILL).exists() or (root / SKILL.parent).exists():
        out.append(File(SKILL, layout.fill("fab-release-skill.md"), Owner.PROJECT))
    if (root / CONFIG).exists():
        return out
    if (root / OLD_CONFIG).exists():
        try:
            config = migrated_config(read(root / OLD_CONFIG))
        except (tomllib.TOMLDecodeError, ValueError) as e:
            raise ValueError(f"{OLD_CONFIG}: {e}; fix it, or write {CONFIG} by hand") from e
        out.append(File(Path(CONFIG), config, Owner.PROJECT))
        return out
    new = fab_config(layout)
    if new is None:
        print(f"no KiCad project under {layout.hardware}; no {CONFIG}", file=sys.stderr)
    else:
        out.append(File(Path(CONFIG), new, Owner.PROJECT))
    return out


def link_on_the_way(root: Path, path: Path) -> Path | None:
    """The first of path's components, from root, that is a symbolic link,
    or None."""
    here = root
    for part in path.parts:
        here = here / part
        if here.is_symlink():
            return here.relative_to(root)
    return None


def linked_within(root: Path, path: Path) -> Path | None:
    """The file path, from root, links to, if a write may go through to it:
    a regular file inside the repository, or one not there yet in a
    directory inside it (a board's directory reached through a link, its
    .kicad_dru to be made), not in another repository (a directory with a
    .git on the way); else None."""
    target = (root / path).resolve()
    top = root.resolve()
    there = target.is_file() or (not target.exists() and target.parent.is_dir())
    if not there or not target.is_relative_to(top):
        return None
    for directory in target.relative_to(top).parents:
        if directory != Path() and os.path.lexists(top / directory / ".git"):
            return None
    return target.relative_to(top)


def through_link(root: Path, step: Step) -> Path | None:
    """Where a write of step goes through a symbolic link, if it may: a
    file adopt edits keeping the project's text in it (a section's file,
    AGENTS.md as a link to CLAUDE.md, say, or an Edit) linked to a file
    within the repository; None if step's path is not a link."""
    if not isinstance(step, File | Edit) or link_on_the_way(root, step.path) is None:
        return None
    if isinstance(step, File) and step.path not in SECTION_FILES:
        return None
    return linked_within(root, step.path)


def refuse_links(root: Path, steps: Sequence[Step]) -> None:
    """ValueError naming a symbolic link a step would write, edit, rename or
    remove through (the link's target, perhaps outside the repository, is
    not adopt's to change): a file's path, a renamed directory, or a
    Markdown file in it that is renamed in.  A file with ee_tooling's
    section, or one adopt edits, linked to a file within the repository is
    written there (through_link), said on stderr."""
    for step in steps:
        match step:
            case File() | Edit() if (target := through_link(root, step)) is not None:
                print(
                    f"{step.path.as_posix()} links to {target.as_posix()}: written there",
                    file=sys.stderr,
                )
                paths = []
            case File() | Edit() | Remove():
                paths = [step.path]
            case Rename():
                source = root / step.source
                inside = [] if source.is_symlink() else sorted(source.rglob("*.md"))
                paths = [step.source, step.target, *(p.relative_to(root) for p in inside)]
            case Command():
                paths = list(step.writes)
        for p in paths:
            link = link_on_the_way(root, p)
            if link is not None:
                raise ValueError(
                    f"{link.as_posix()} is a symbolic link (to {os.readlink(root / link)}):"
                    f" adopt changes no file through a link; nothing done: replace it with"
                    f" what it links to, or remove it"
                )


# How many of the files plain_at_head finds are named.
PLAIN_SHOWN = 5
# A file's mode in git that is not a blob of its own: a submodule, a link.
NOT_A_FILE_MODES = ("160000", "120000")


def plain_at_head(root: Path) -> list[str]:
    """The files at HEAD of a type the managed .gitattributes section
    tracks through Git LFS, stored as plain blobs, not pointers: those that
    --check fails on once .gitattributes is committed.  A type the project
    makes plain (-filter in its own lines, git check-attr) is left out."""
    if not git(root, "rev-parse", "--verify", "-q", "HEAD"):
        return []
    template = importlib.resources.files(__package__).joinpath("templates", "gitattributes")
    patterns = [line.split()[0] for line in template.read_text().splitlines() if line.strip()]
    found = []
    for entry in git(root, "ls-tree", "-r", "-l", "-z", "HEAD").split("\0"):
        if "\t" not in entry:
            continue
        info, path = entry.split("\t", 1)
        mode, _, oid, size = info.split()
        name = path.rsplit("/", 1)[-1]
        if mode in NOT_A_FILE_MODES or not any(fnmatch.fnmatchcase(name, p) for p in patterns):
            continue
        if int(size) < LFS_POINTER_SIZE:
            first = git(root, "cat-file", "blob", oid).split("\n", 1)[0]
            if first.encode() in LFS_VERSIONS or int(size) == 0:
                continue
        found.append(path)
    if not found:
        return []
    attributes = git(root, "check-attr", "filter", "--", *found)
    plain = {
        line.rsplit(": filter: ", 1)[0]
        for line in attributes.splitlines()
        if line.endswith((": filter: unset", ": filter: set"))
    }
    return [p for p in found if p not in plain]


def warn_plain_at_head(root: Path, submodule: Path) -> None:
    """Say, before the commit that would show it, which files at HEAD
    --check will fail on as plain blobs, and the fix to make with that
    commit."""
    found = plain_at_head(root)
    if not found:
        return
    shown = ", ".join(found[:PLAIN_SHOWN]) + (", ..." if len(found) > PLAIN_SHOWN else "")
    print(
        f"{len(found)} files at HEAD of types Git LFS tracks are plain blobs ({shown}):"
        f" once .gitattributes is committed, {adopt_command(submodule)} --check fails on"
        " them.  Commit them as pointers with adopt's files: after --yes, git add -A &&"
        " git add --renormalize . and commit",
        file=sys.stderr,
    )


def warn_unfetched_submodules(layout: Layout) -> None:
    """Name each submodule (a library, but ee_tooling) whose files Git LFS
    tracks are pointers, with the commands that fetch them: adopt sets up
    the board repository's clone alone, and changes no other repository's
    config."""
    root = layout.root
    found = git(root, "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    for line in found.splitlines():
        path = Path(line.split(" ", 1)[1])
        if path == layout.submodule or not os.path.lexists(root / path / ".git"):
            continue
        listed = git_lfs(root / path, "ls-files").stdout.splitlines()
        count = sum(1 for entry in listed if entry.split(" ", 2)[1:2] == ["-"])
        if count:
            where = shlex.quote(path.as_posix())
            print(
                f"{path.as_posix()}: {count} files Git LFS tracks are pointers, not fetched"
                f" (adopt sets up this clone only): git -C {where} lfs install --local"
                f" --skip-repo && git -C {where} lfs pull",
                file=sys.stderr,
            )


def warn_outside_libraries(layout: Layout, config: str | None) -> None:
    """Warn, for each board in fab.toml's text (config), of each library its
    tables and 3D model paths name outside the repository, which
    fab-release refuses (project_libraries.libraries), pointing at the
    docs' recipe; a board or table that cannot be read is said so."""
    if config is None:
        return
    try:
        boards = tomllib.loads(config).get("board", [])
    except tomllib.TOMLDecodeError:
        return
    root = layout.root
    for b in boards if isinstance(boards, list) else []:
        if not isinstance(b, dict):
            continue
        name, directory = b.get("name"), b.get("directory")
        if not isinstance(name, str) or not isinstance(directory, str):
            continue
        pcb = root / directory / f"{name}.kicad_pcb"
        if not pcb.is_file():
            continue
        try:
            found = libraries(root, Path(directory), parse(read(pcb)))
        except (ValueError, ParseError, OSError) as e:
            print(f"warning: {name}: its libraries not checked: {e}", file=sys.stderr)
            continue
        if found.outside:
            print(
                f"warning: [[board]] {name!r}: libraries outside the repository, which"
                f" fab-release refuses: {', '.join(found.outside)}; move each in"
                f" ({layout.submodule.as_posix()}/docs/adopting.md, Moving a library into the"
                " repository)",
                file=sys.stderr,
            )


def plan(layout: Layout) -> list[Step]:
    """What is to be done, in order."""
    root, submodule, hardware = layout.root, layout.submodule, layout.hardware
    planned = files(layout)
    warn_plain_at_head(root, submodule)
    warn_unfetched_submodules(layout)
    warn_outside_libraries(layout, planned_config(root, planned))
    steps: list[Step] = [f for f in planned if f.action(root)]
    steps += migrations(layout)
    steps += rules_edits(root, planned_config(root, planned))
    steps += [Remove(p, LEFTOVER_WHY) for p in leftovers(root)]
    # git's and git-lfs's set-up first: .gitattributes routes files to Git
    # LFS already, and a uv step that fails must not leave them unset.
    if git(root, "config", "core.hooksPath") != HOOKS_PATH:
        steps.append(Command(("git", "config", "core.hooksPath", HOOKS_PATH)))
    if not git(root, "config", "filter.lfs.process"):
        steps.append(Command(LFS_INSTALL))
    if lfs_unfetched(root):
        steps.append(Command(LFS_PULL))
    venv = root / hardware / ".venv"
    reason = unsupported()
    if reason is not None:
        print(
            f"{reason}: no venv on the system Python, so stitch and placement-score"
            " cannot run here (docs/stitch.md, Platforms)",
            file=sys.stderr,
        )
    elif not system_site_packages(venv):
        clear = ("--clear",) if venv.exists() else ()
        steps.append(
            Command(
                ("uv", "venv", *clear, "--system-site-packages", "--python", SYSTEM_PYTHON),
                hardware,
                (hardware / ".venv",),
            )
        )
    if not uses_ee_tooling(root / hardware / "pyproject.toml"):
        editable = f"./{submodule.relative_to(hardware).as_posix()}"
        steps.append(
            Command(
                ("uv", "add", "--dev", "--editable", "--no-workspace", editable),
                hardware,
                tuple(hardware / n for n in ("pyproject.toml", "uv.lock", ".venv")),
            )
        )
    refuse_links(root, steps)
    return steps


def describe(step: Step, root: Path) -> str:
    match step:
        case File():
            return f"{step.action(root)} {step.path.as_posix()} ({step.owner.value})"
        case Command():
            where = f" (in {step.directory.as_posix()})" if step.directory != Path() else ""
            return f"run {' '.join(step.args)}{where}"
        case Remove():
            if step.kept is not None:
                return (
                    f"keep {step.path.as_posix()} (project; {step.why}; {step.kept}:"
                    " remove it yourself once the new file is checked)"
                )
            return f"remove {step.path.as_posix()} (project; {step.why})"
        case Edit():
            verb = "update" if (root / step.path).exists() else "create"
            return f"{verb} {step.path.as_posix()} (project; {step.why})"
        case Rename():
            names = ", ".join(f"{old} -> {new}" for old, new in step.names)
            return (
                f"move {step.source.as_posix()}/ to {step.target.as_posix()}/"
                f" (project; {names} in its .md files)"
            )


def in_style(target: Path, text: str) -> str:
    """text, with \n line endings and no byte order mark, as target has
    its own: its first line's line ending (\r\n in a checkout that
    converts them) and a leading UTF-8 byte order mark, if it has one; text
    as it is for a file not there yet."""
    if not target.exists():
        return text
    raw = target.read_bytes()
    first = raw.find(b"\n")
    if first > 0 and raw[first - 1 : first] == b"\r":
        text = text.replace("\n", "\r\n")
    return BOM + text if raw.startswith(codecs.BOM_UTF8) else text


def write(target: Path, text: str) -> None:
    """target's text replaced whole or not at all (atomic.write_text), in
    its own line endings and byte order mark (in_style)."""
    write_text(target, in_style(target, text))


def writable(path: Path) -> bool:
    """Whether path, or the nearest directory of it that exists, can be
    written."""
    while not path.exists():
        path = path.parent
    return os.access(path, os.W_OK)


def refuse_unwritable(root: Path, steps: Sequence[Step]) -> None:
    """ValueError naming a file or directory a step must write that cannot
    be: before any step is done."""
    for step in steps:
        match step:
            case File() | Edit():
                # Where the write lands: a link's target, and its directory.
                landing = (root / step.path).resolve()
                paths = [landing, landing.parent]
            case Remove():
                paths = [step.path.parent] if step.kept is None else []
            case Rename():
                paths = [step.source.parent, step.target.parent, step.source]
            case Command():
                paths = []
        for p in paths:
            if not writable(root / p):
                shown = p.relative_to(root.resolve()) if p.is_relative_to(root.resolve()) else p
                raise ValueError(f"{shown.as_posix()} cannot be written: nothing done")


def apply(root: Path, steps: Sequence[Step], run: Callable[[Sequence[str], Path], None]) -> None:
    """The steps, in order; ValueError naming what cannot be written, before
    any is done."""
    refuse_unwritable(root, steps)
    for step in steps:
        print(describe(step, root), flush=True)
        match step:
            case File():
                target = root / (through_link(root, step) or step.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                write(target, step.text)
                if step.executable:
                    target.chmod(0o755)
                    # The mode git tracks, for every clone.
                    if not executable(root, step.path):
                        run(("git", "update-index", "--chmod=+x", "--", step.path.as_posix()), root)
            case Command():
                run(step.args, root / step.directory)
            case Remove():
                if step.kept is None:
                    (root / step.path).unlink()
            case Edit():
                write(root / (through_link(root, step) or step.path), step.text)
            case Rename():
                # Its files first, then the directory: a run stopped between
                # leaves the old directory, which the next run renames.
                for f in sorted((root / step.source).rglob("*.md")):
                    try:
                        text = read(f)
                    except ValueError as e:
                        print(f"{e}: left as it is", file=sys.stderr)
                        continue
                    for old, new in step.names:
                        text = text.replace(old, new)
                    write(f, text)
                (root / step.source).rename(root / step.target)


def run_command(args: Sequence[str], directory: Path) -> None:
    # Not in the venv adopt runs in (its submodule's), but the directory's own;
    # git's repository the directory's too.
    subprocess.run(
        args,
        cwd=directory,
        env={k: v for k, v in environment().items() if k != "VIRTUAL_ENV"},
        check=True,
    )


def read_config(root: Path) -> str | None:
    """fab.toml's text, or None if it is not there."""
    path = root / CONFIG
    return read(path) if path.exists() else None


def pending(layout: Layout) -> list[tuple[str, Path]]:
    """What --check fails on, each with what it awaits: the managed files
    not there ("missing") or that differ from what would be written ("out
    of date"), the project
    files still to be migrated ("to be migrated"), the project files
    whose rules or settings are still to be installed ("to be updated"),
    and the scratch files of an interrupted write ("left over")."""
    root = layout.root
    planned = files(layout)
    # A managed file's action: "create" (not there) or "update".
    managed = [(f, f.action(root)) for f in planned if f.owner is Owner.MANAGED]
    migrating = migrations(layout)
    edits = rules_edits(root, planned_config(root, planned))
    warn_outside_libraries(layout, planned_config(root, planned))
    refuse_links(root, [*(f for f, a in managed if a), *migrating, *edits])
    renamed = [s.source if isinstance(s, Rename) else s.path for s in migrating]
    rules = [s.path for s in edits]
    # A project file the release needs, which --yes would create.
    needed = [
        f.path
        for f in planned
        if f.owner is Owner.PROJECT and f.path in RELEASE_NEEDS and f.action(root) == "create"
    ]
    return [
        *(("missing" if a == "create" else "out of date", f.path) for f, a in managed if a),
        *(("missing", p) for p in needed),
        *(("to be migrated", p) for p in renamed),
        *(("to be updated", p) for p in rules),
        *(("left over", p) for p in leftovers(root)),
    ]


def planned_config(root: Path, planned: Iterable[File]) -> str | None:
    """fab.toml's text as --yes leaves it: the one planned (written or
    migrated) if any, else the one there, else None."""
    new = next((f.text for f in planned if f.path == Path(CONFIG) and f.action(root)), None)
    return new if new is not None else read_config(root)


def stale(layout: Layout) -> list[Path]:
    """The paths pending() names."""
    return [p for _, p in pending(layout)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--yes", action="store_true", help="do it, rather than print it")
    mode.add_argument("--check", action="store_true", help="fail if a managed file is stale")
    a = ap.parse_args()

    try:
        layout = locate(CHECKOUT)
        if (why := lfs_missing(layout.submodule)) is not None:
            sys.exit(why)
        if a.check:
            out_of_date = pending(layout)
        else:
            steps = plan(layout)
    except ValueError as e:
        sys.exit(str(e))
    except OSError as e:
        sys.exit(f"{e.filename}: {e.strerror}; nothing done")
    if a.check:
        for what, p in out_of_date:
            print(
                f"{what}: {p.as_posix()}; run {adopt_command(layout.submodule)} --yes",
                file=sys.stderr,
            )
        # git-lfs's own report, apart from pending()'s paths: it names the files.
        report = plain_blobs(layout.root)
        if report is not None:
            print(fsck_message(report, layout.submodule), file=sys.stderr)
        sys.exit(1 if out_of_date or report is not None else 0)
    if not steps:
        print("nothing to do")
    elif a.yes:
        try:
            apply(layout.root, steps, run_command)
        except ValueError as e:
            sys.exit(str(e))
        except OSError as e:
            sys.exit(f"{e.filename}: {e.strerror}; the steps above were done")
        except subprocess.CalledProcessError as e:
            why = ""
            if tuple(e.cmd) == LFS_PULL:
                why = (
                    "; the Git LFS files stay pointers until it runs: rerun"
                    f" {adopt_command(layout.submodule)} --yes with the remote in reach"
                )
            sys.exit(f"{' '.join(e.cmd)} failed ({e.returncode}){why}")
    else:
        for step in steps:
            print(describe(step, layout.root))
        print("rerun with --yes to do it")
