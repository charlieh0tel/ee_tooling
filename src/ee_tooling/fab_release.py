"""Build a board's release package for its fab.

Reads fab.toml at the root of the git repository it is run in, and writes
the board's production directory, YYYYMMDD-REV/, with:
  NAME-REV-gerbers-jlcpcb.zip  Gerbers (User.Drawings too) and drill files,
                        for the PCB order, README.txt, the Gerber map, and
                        stackup.txt and fab-notes.txt (NAME-REV-gerbers.zip
                        for a profile with kicad-cli's own Gerber options)
  NAME-REV-gerber-map.txt  what each file in the Gerber zip is
  NAME-REV-stackup.txt  the board's stackup, each layer's Gerber, and its
                        impedance requirements
  NAME-REV-fab-notes.txt  fabrication notes, filled from the board and its
                        [board.fab], [TBD: ...] where neither says
  NAME-REV-odb.tgz      ODB++ job, for the record and ODB++ tools
  NAME-REV-ipc2581.zip  IPC-2581 revision C, for the record and IPC-2581 tools
  NAME-REV-bom.csv      the house BOM: every part, DNP parts at Qty 0
  NAME-REV-jlcpcb-bom.csv, NAME-REV-jlcpcb-cpl.csv  the profile's assembly
                        BOM and placement
  NAME-REV-source.tgz   the design files, PDFs and docs it was made from

With variants (KiCad 10 design variants), the BOMs, placement, ODB++ and
IPC-2581 are per variant, NAME-REV-VARIANT-..., as is the Gerber zip where
the variants' silkscreens differ.

NAME is the KiCad project, REV its board's title-block revision; the
board's profile (profiles/) names the fab whose files these are.  The
board must have a stackup and a stackup table matching it, ERC and DRC
(with the profile's rules, on a scratch copy of the project) must be clean
and the release's sources committed, unless --allow-dirty.
The commit it was built from gets an annotated tag NAME-YYYYMMDD-REV, which
it does not push: it prints the command.  A build from uncommitted sources
is not tagged, and never replaces a tagged release; a tag already on
another commit stops it, unless --retag.
"""

import argparse
import contextlib
import csv
import datetime
import io
import json
import math
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any

from ee_tooling.fabrication import (
    DRILL_SUFFIX,
    GERBER_JOB_SUFFIX,
    IPC_CLASSES,
    Fab,
    GerberFile,
    board_thickness,
    copper_layers,
    gerber_files,
    gerber_map,
    impedances,
    minimums,
    notes,
    stackup,
    stackup_document,
)
from ee_tooling.git_environment import environment
from ee_tooling.kicad_rules import Installed, board_setup, copied_message, install, installed
from ee_tooling.kicad_style_netclass import sheet_files
from ee_tooling.kicad_style_stackup import check_stackup
from ee_tooling.profiles import PROFILES
from ee_tooling.profiles.profile import (
    KICAD_GERBERS,
    GerberOptions,
    Part,
    Placement,
    Profile,
    write_csv,
)
from ee_tooling.project_libraries import libraries
from ee_tooling.sexpr import Atom, Expression, Item, ParseError, child, children, parse
from ee_tooling.stackup_table import layer_names

CONFIG = "fab.toml"
# The encoding of a project's text files: UTF-8, a byte order mark first
# taken off (an editor may write one, and adopt keeps it).
PROJECT_TEXT = "utf-8-sig"
# This ee_tooling's checkout: src/ee_tooling/fab_release.py's grandparent.
CHECKOUT = Path(__file__).resolve().parents[2]
# fab.toml's top-level keys.
TOP_KEYS = ("board",)
# A [[board]]'s keys whose values are a string, then those that are a list
# of strings, each with its default (None: required).
STRING_KEYS: dict[str, str | None] = {
    "name": None,
    "directory": None,
    "production": None,
    "profile": None,
}
LIST_KEYS: dict[str, list[str] | None] = {"sources": [], "variants": []}
# A [[board]]'s table of what its fab is to build to, every key required.
FAB = "fab"
# Keys fab.toml does not have, with why: present, each is an error.
GONE_KEYS = {"copper": "the copper layers come from the board's stackup"}
# A design variant's name, as KiCad 10 allows it: letters, digits, ".", "_"
# and "-", not starting with "-".
VARIANT_NAME = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]*")
# The Gerber X2 file functions a variant's Gerbers may differ in: the
# silkscreens and User.Drawings (kicad-cli's "Other,User").
VARIANT_FUNCTIONS = {("Legend",), ("Other",)}
# The Gerber map's name in the Gerber zip; the stackup and the fabrication
# notes are there by their names after NAME-REV-.
GERBER_README = "README.txt"
# git's own directory (or, in a submodule or worktree, file).
GIT_DIRECTORY = ".git"
# git's settings for the commands whose output the release reads, whatever
# the user's: paths from the repository's root, no branch line, no color,
# every untracked file shown (each status call also says so).
GIT_OPTIONS = (
    "-c",
    "status.showUntrackedFiles=all",
    "-c",
    "status.relativePaths=false",
    "-c",
    "status.branch=false",
    "-c",
    "color.ui=false",
)
# git ls-files --stage's mode of a submodule (a gitlink).
GITLINK = "160000 "
# A release is built in its production directory, in .DATE-REV plus this,
# then put in place of the old one.
BUILDING = ".building"
# The old release's name beside it, .DATE-REV plus this, while the new one
# is put in place and tagged.
OLD = ".old"
# The work directory's subdirectory DRC's scratch copy of the project is
# in, and the suffix of a project's custom rules.
DRC_PROJECT = "project"
# The work directory's subdirectory of the scratch copy whose rules end with
# CANARY, a rule every item breaks, named CANARY_NAME: DRC there reports
# it only if kicad-cli read the rules.
RULES_PROJECT = "rules"
CANARY_NAME = "ee_tooling canary"
CANARY = f'(rule "{CANARY_NAME}"\n\t(constraint assertion "false"))\n'
# The work directory's subdirectory of the scratch copy of the project, as
# the board has it, that every other kicad-cli command reads.
EXPORT_PROJECT = "board"
# In DRC's scratch copy, the project's files copied, by suffix to its name
# (kicad-cli reads them; a link would let anything KiCad writes beside
# them reach the board's), and those left out (KiCad's cache, which it
# writes); every other entry is a link.
DRC_COPIED = (".kicad_pcb", ".kicad_sch", ".kicad_prl")
DRC_LEFT_OUT = ("fp-info-cache",)
DRU = ".kicad_dru"
# The work directory's subdirectory kicad-cli writes the Gerbers and drill into.
GERBERS = "gerbers"
# The fabrication documents' names after NAME-REV-, then the house BOM's.
GERBER_MAP = "gerber-map.txt"
BOM = "bom.csv"
STACKUP = "stackup.txt"
NOTES = "fab-notes.txt"

# Every board's non-copper Gerber layers.
OTHER_LAYERS = (
    "F.Mask",
    "B.Mask",
    "F.Paste",
    "B.Paste",
    "F.Silkscreen",
    "B.Silkscreen",
    "Edge.Cuts",
    "User.Drawings",
)
# The board PDF's layers after the copper, one page each.
BOARD_PDF_LAYERS = ("F.Silkscreen", "B.Silkscreen", "F.Fab", "B.Fab", "User.Drawings")

# The BOM export's fields.  Whether a part is fitted comes from a second
# export with --exclude-dnp, which reads KiCad's DNP attribute: ${DNP} in
# --fields gives a symbol's own field named DNP when it has one (10.0.8).
BOM_FIELDS = (
    "Reference",
    "Value",
    "Footprint",
    "LCSC",
    "MPN",
    "Manufacturer",
    "Datasheet",
)
# The house BOM's columns (docs/kicad-style.md's fields), and its DNP
# column's value for a part not fitted.
HOUSE_BOM_HEADER = (
    "Reference",
    "Value",
    "Footprint",
    "Qty",
    "DNP",
    "LCSC",
    "MPN",
    "Manufacturer",
    "Datasheet",
)
DNP = "yes"

# A KiCad project's design files and libraries, by suffix to its name, then
# by name; those present go in the source tgz.
PROJECT_SUFFIXES = (".kicad_pcb", ".kicad_sch", ".kicad_pro", ".kicad_dru", ".pretty", ".3dshapes")
PROJECT_FILES = ("fp-lib-table", "sym-lib-table")

# A Git LFS pointer's first line (git-lfs's spec.md): the spec's version,
# then the pre-release one git-lfs still reads; a pointer is smaller than
# LFS_POINTER_SIZE bytes, and an empty file is not taken for one.
LFS_VERSIONS = (
    b"version https://git-lfs.github.com/spec/v1",
    b"version https://hawser.github.com/spec/v1",
)
LFS_POINTER_SIZE = 1024

# ODB++ export: mm, as the Gerbers and drill; 6 decimals, as kicad-cli's
# default 4.6 Gerbers, is KiCad's 1 nm unit, so nothing is rounded; a tgz of one
# odb/ job directory, ODB++'s usual archive and the one tools/odb_current reads.
ODB_UNITS = "mm"
ODB_PRECISION = 6
ODB_COMPRESSION = "tgz"

# IPC-2581 export: revision C, kicad-cli's default; mm and 6 decimals, as
# the ODB++; a zip of one XML file, NAME-REV-ipc2581.xml.  Its BOM takes
# the house BOM fields (docs/kicad-style.md), and the profile's distributor.
IPC2581_VERSION = "C"
IPC2581_UNITS = ODB_UNITS
IPC2581_PRECISION = ODB_PRECISION
IPC2581_BOM = (
    ("--bom-col-mfg-pn", "MPN"),
    ("--bom-col-mfg", "Manufacturer"),
)


@dataclass(frozen=True)
class Board:
    """A board that can be released (a [[board]] in fab.toml)."""

    name: str  # the KiCad project
    directory: Path  # holds NAME.kicad_pcb and NAME.kicad_sch, from the repository root
    production: Path  # where its YYYYMMDD-REV/ releases go, from the repository root
    sources: tuple[Path, ...]  # in the source tgz after the project's files, from the root
    profile: str  # its fab's, a key of PROFILES
    fab: Fab  # [board.fab]
    options: dict[str, Any] = field(default_factory=dict)  # [board.<profile>], unchecked
    variants: tuple[str, ...] = ()  # the design variants built, each a build; () the default

    @property
    def pcb(self) -> Path:
        return self.directory / f"{self.name}.kicad_pcb"

    @property
    def sch(self) -> Path:
        return self.directory / f"{self.name}.kicad_sch"

    @property
    def project(self) -> Path:
        return self.directory / f"{self.name}.kicad_pro"

    def source_paths(self, root: Path, sheets: Iterable[Path] = ()) -> list[Path]:
        """The source tgz's files and directories, from the repository
        root: the project's files, its schematic's other sheets' files
        (sheets, from the root) not among them, and the sources."""
        candidates = [
            *(self.directory / f"{self.name}{s}" for s in PROJECT_SUFFIXES),
            *(self.directory / f for f in PROJECT_FILES),
        ]
        present = [c for c in candidates if (root / c).exists()]
        return [*present, *(s for s in sheets if s not in present), *self.sources]


def string(where: str, table: dict[str, Any], key: str, default: str | None) -> str:
    """table[key], a string, or default; ValueError naming the key if it is
    missing with no default, or not a string."""
    found = table.get(key, default)
    if found is None:
        raise ValueError(f"{where}: no {key!r}")
    if not isinstance(found, str):
        raise ValueError(f"{where}: {key!r} is not a string")
    return found


def strings(where: str, table: dict[str, Any], key: str, default: list[str] | None) -> list[str]:
    """table[key], a list of strings, or default; ValueError naming the key
    if it is missing with no default, or not a list of strings."""
    found = table.get(key, default)
    if found is None:
        raise ValueError(f"{where}: no {key!r}")
    if not isinstance(found, list) or not all(isinstance(x, str) for x in found):
        raise ValueError(f"{where}: {key!r} is not a list of strings")
    return found


def fab(where: str, table: object) -> Fab:
    """A board's [board.fab], every key required; ValueError naming a bad
    key."""
    if table is None:
        raise ValueError(f"{where}: no [board.{FAB}]")
    if not isinstance(table, dict):
        raise ValueError(f"{where}: {FAB!r} is not a table")
    where = f"{where}: [board.{FAB}]"
    keys = [f.name for f in fields(Fab)]
    if unknown := sorted(set(table) - set(keys)):
        raise ValueError(f"{where}: unknown key {', '.join(repr(k) for k in unknown)}")
    if missing := [k for k in keys if k not in table]:
        raise ValueError(f"{where}: no {', '.join(repr(k) for k in missing)}")
    ipc_class, tg = table["ipc_class"], table["tg"]
    if type(ipc_class) is not int or ipc_class not in IPC_CLASSES:
        raise ValueError(f"{where}: 'ipc_class' is not one of {', '.join(map(str, IPC_CLASSES))}")
    if type(tg) not in (int, float) or not math.isfinite(tg) or tg <= 0:
        raise ValueError(f"{where}: 'tg' is not a temperature, deg C")
    for key in ("base_material", "electrical_test"):
        if not isinstance(table[key], str) or not table[key].strip():
            raise ValueError(f"{where}: {key!r} is not a string, or is empty")
    return Fab(ipc_class, table["base_material"], float(tg), table["electrical_test"])


def variant_errors(names: Iterable[str]) -> list[str]:
    """What is wrong with a board's variants' names: one not a KiCad name,
    or one there twice, ignoring case as KiCad does."""
    out = []
    seen: set[str] = set()
    for name in names:
        if not VARIANT_NAME.fullmatch(name):
            out.append(
                f"variant {name!r} is not letters, digits, '.', '_' and '-', not starting with '-'"
            )
        elif name.casefold() in seen:
            out.append(f"variant {name!r} is there twice, ignoring case")
        seen.add(name.casefold())
    return out


def board(table: dict[str, Any], number: int) -> Board:
    """A [[board]], the number-th; ValueError naming a bad key."""
    where = f"[[board]] {number}"
    name = string(where, table, "name", None)
    where = f"[[board]] {name!r}"
    for key, why in GONE_KEYS.items():
        if key in table:
            raise ValueError(f"{where}: {key!r} is gone: {why}")
    # Beside its own keys, a board has a table of its profile's options.
    # Its type checked first: the profile names a table, a key.
    options = string(where, table, "profile", None)
    known = {*STRING_KEYS, *LIST_KEYS, FAB, options}
    if unknown := sorted(set(table) - known):
        raise ValueError(f"{where}: unknown key {', '.join(repr(k) for k in unknown)}")
    text = {k: string(where, table, k, d) for k, d in STRING_KEYS.items()}
    lists = {k: strings(where, table, k, d) for k, d in LIST_KEYS.items()}
    if text["profile"] not in PROFILES:
        raise ValueError(
            f"{where}: profile {text['profile']!r} is not one of {', '.join(PROFILES)}"
        )
    if not isinstance(table.get(text["profile"], {}), dict):
        raise ValueError(f"{where}: {text['profile']!r} is not a table")
    for name in variant_errors(lists["variants"]):
        raise ValueError(f"{where}: {name}")
    for source in lists["sources"]:
        if os.path.isabs(source) or os.path.normpath(source).split(os.sep)[0] == os.pardir:
            raise ValueError(f"{where}: source {source!r} is outside the repository")
    return Board(
        name=name,
        directory=Path(text["directory"]),
        production=Path(text["production"]),
        sources=tuple(Path(s) for s in lists["sources"]),
        profile=text["profile"],
        fab=fab(where, table.get(FAB)),
        options=table.get(text["profile"], {}),
        variants=tuple(lists["variants"]),
    )


def load_config(text: str) -> dict[str, Board]:
    """fab.toml's boards, by name, the first being the default; ValueError
    naming a bad key."""
    config = tomllib.loads(text)
    if unknown := sorted(set(config) - set(TOP_KEYS)):
        raise ValueError(f"unknown key {', '.join(repr(k) for k in unknown)}")
    tables = config.get("board", [])
    if not isinstance(tables, list) or not tables or not all(isinstance(t, dict) for t in tables):
        raise ValueError(f"{CONFIG} has no [[board]]")
    out: dict[str, Board] = {}
    for number, table in enumerate(tables, 1):
        b = board(table, number)
        if b.name in out:
            raise ValueError(f"[[board]] {b.name!r} is there twice")
        out[b.name] = b
    if errors := production_errors(out.values()):
        raise ValueError(errors[0])
    return out


def normal(path: Path) -> Path:
    """A path from the repository root, normalized ("." for the root)."""
    return Path(os.path.normpath(path))


def nested(a: Path, b: Path) -> bool:
    """Whether a and b, from the root, are the same or one holds the other."""
    a, b = normal(a), normal(b)
    return a == b or a.is_relative_to(b) or b.is_relative_to(a) or Path(".") in (a, b)


def production_errors(boards: Iterable[Board]) -> list[str]:
    """A production directory outside the repository, or the same as or
    holding a board's directory or a source (the uncommitted-sources
    check leaves every production directory out); and each two boards
    whose production directories are the same or one holds the other: a
    release's directory, DATE-REV, does not name its board, so one
    board's --force would remove the other's release."""
    listed = list(boards)
    outside = [
        f"[[board]] {a.name!r}: production {str(a.production)!r} is outside the repository"
        for a in listed
        if a.production.is_absolute() or normal(a.production).parts[:1] == (os.pardir,)
    ]
    holding = [
        f"[[board]] {a.name!r}: production {str(a.production)!r} holds"
        f" {str(p)!r}, {b.name!r}'s {what}"
        for a in listed
        for b in listed
        for what, p in (("directory", b.directory), *(("source", s) for s in b.sources))
        if normal(p) == normal(a.production)
        or normal(p).is_relative_to(normal(a.production))
        or normal(a.production) == Path(".")
    ]
    return (
        outside
        + holding
        + [
            f"[[board]] {a.name!r} and {b.name!r}: production {str(a.production)!r} and"
            f" {str(b.production)!r} are the same directory, or one holds the other"
            for i, a in enumerate(listed)
            for b in listed[i + 1 :]
            if nested(a.production, b.production)
        ]
    )


def through_link(root: Path, path: Path) -> Path | None:
    """The first of a path's leading parts, from the root, that is a
    symbolic link, the path itself too; None if none."""
    parts = normal(path).parts
    return next(
        (
            Path(*parts[:i])
            for i in range(1, len(parts) + 1)
            if (root / Path(*parts[:i])).is_symlink()
        ),
        None,
    )


def located(root: Path, path: Path) -> Path | None:
    """A path from the root with its links followed, from the root again;
    None if it leads out of the repository."""
    top = root.resolve()
    found = (top / path).resolve()
    return found.relative_to(top) if found.is_relative_to(top) else None


def located_production_errors(root: Path, boards: Iterable[Board]) -> list[str]:
    """production_errors() on the paths with their links followed: a link
    can lead a production directory out of the repository, or make two
    names one directory."""
    out = []
    found = []
    for b in boards:
        production = located(root, b.production)
        if production is None:
            out.append(
                f"[[board]] {b.name!r}: production {str(b.production)!r} leads out of the"
                " repository (a link)"
            )
            continue
        found.append(
            replace(
                b,
                production=production,
                directory=located(root, b.directory) or b.directory,
                sources=tuple(located(root, s) or s for s in b.sources),
            )
        )
    return out + production_errors(found)


def lfs_pointer(path: Path) -> bool:
    """Whether the file is a Git LFS pointer, by its contents alone."""
    if not 0 < path.stat().st_size < LFS_POINTER_SIZE:
        return False
    return path.read_bytes().split(b"\n", 1)[0] in LFS_VERSIONS


def lfs_pointers(root: Path, paths: Iterable[Path], left_out: Iterable[Path] = ()) -> list[Path]:
    """The files among paths, from root, directories walked, that are Git
    LFS pointers whose files were never fetched; from root, each once (a
    library's 3D model may also be under a source directory).  Not walked,
    as the source tgz leaves them out: git's own directory and left_out
    (the production directories).  OSError if one cannot be read."""
    skipped = [normal(p) for p in left_out]

    def kept(f: Path) -> bool:
        rel = normal(f.relative_to(root))
        return GIT_DIRECTORY not in rel.parts and not any(rel.is_relative_to(s) for s in skipped)

    out: dict[Path, None] = {}
    for p in paths:
        top = root / p
        found = sorted(f for f in top.rglob("*") if f.is_file()) if top.is_dir() else [top]
        out.update((f.relative_to(root), None) for f in found if kept(f) and lfs_pointer(f))
    return list(out)


def git_out(cwd: Path, *args: str) -> str:
    """git's output in cwd; a failure stops the release, with git's
    message.  Paths in it are as git -z writes them, never quoted."""
    r = subprocess.run(
        ["git", *GIT_OPTIONS, *args],
        cwd=cwd,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} in {cwd} failed ({r.returncode}):\n{r.stderr}")
    return r.stdout


def status_entries(text: str) -> list[str]:
    """git status --porcelain -z's entries, each "XY PATH" (a rename's or
    copy's new path; its old one, the next field, dropped)."""
    fields = text.split("\0")
    out = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        out.append(entry)
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1
    return out


def submodules(root: Path) -> list[Path]:
    """The repository's submodules (gitlinks) and theirs, at any depth, each
    checked out, from root; deepest first, so the first holding a path is
    its repository.  A failing git stops the release."""
    out: list[Path] = []
    todo = [Path(".")]
    while todo:
        here = todo.pop()
        for entry in git_out(root / here, "ls-files", "-z", "--stage").split("\0"):
            if entry.startswith(GITLINK):
                found = normal(here / entry.split("\t", 1)[1])
                out.append(found)
                if (root / found / GIT_DIRECTORY).exists():
                    todo.append(found)
    return sorted(out, key=lambda p: len(p.parts), reverse=True)


def tracked(root: Path, paths: Iterable[Path], productions: Iterable[Path]) -> list[Path]:
    """The files git tracks under paths, from the root, in the
    repositories that hold them, submodules' files too, but every
    production directory and git's own; each once, in order."""
    modules = submodules(root)
    left_out = [q for p in productions for q in (normal(p), located(root, p)) if q is not None]
    out: dict[Path, None] = {}
    for p in paths:
        if through_link(root, p) not in (None, normal(p)):
            continue  # its target is among the paths too
        repo = repository_of(normal(p), modules)
        rel = normal(p).relative_to(repo)
        listed = git_out(
            root / repo,
            "ls-files",
            "-z",
            "--recurse-submodules",
            "--",
            "." if rel == Path(".") else f":(literal){rel}",
        )
        for name in filter(None, listed.split("\0")):
            f = normal(repo / name)
            if GIT_DIRECTORY in f.parts or any(f.is_relative_to(x) for x in left_out):
                continue
            out[f] = None
    return list(out)


def repository_of(path: Path, modules: Sequence[Path]) -> Path:
    """The repository a path from the root is in: its deepest submodule,
    or the root (".")."""
    return next((s for s in modules if normal(path).is_relative_to(s)), Path("."))


def pointer_message(pointers: Sequence[Path], modules: Sequence[Path]) -> str:
    """The release's refusal of pointers, each named, and the commands that
    fetch them, one pair per repository they are in."""
    groups: dict[Path, None] = dict.fromkeys(repository_of(p, modules) for p in pointers)
    commands = []
    for repo in groups:
        git = "git" if repo == Path(".") else f"git -C {shlex.quote(str(repo))}"
        commands.append(f"  {git} lfs install --local --skip-repo && {git} lfs pull\n")
    return (
        "Git LFS pointers among the release's inputs, their files never fetched"
        " (a clone without git-lfs):\n"
        + "".join(f"  {p}\n" for p in pointers)
        + "install git-lfs, then, from the repository's root:\n"
        + "".join(commands)
    ).rstrip("\n")


def house_bom(parts: Iterable[Part]) -> list[list[str]]:
    """The house BOM's rows: fitted parts grouped by every field but the
    reference, a DNP part a row of its own with Qty 0, in the order of each
    row's first part."""
    rows: dict[tuple[str, ...], list[Part]] = {}
    for p in parts:
        fields = (p.value, p.footprint, p.lcsc, p.mpn, p.manufacturer, p.datasheet)
        rows.setdefault((p.reference,) if p.dnp else fields, []).append(p)
    return [
        [
            ",".join(p.reference for p in group),
            first.value,
            first.footprint,
            "0" if first.dnp else str(len(group)),
            DNP if first.dnp else "",
            first.lcsc,
            first.mpn,
            first.manufacturer,
            first.datasheet,
        ]
        for group in rows.values()
        for first in group[:1]
    ]


def variant_names(expression: Expression) -> list[str]:
    """The names of the (variant (name "X") ...) lists in expression, at any
    depth, in order, each once."""
    out: dict[str, None] = {}
    todo: list[Item] = [expression]
    while todo:
        item = todo.pop()
        if not isinstance(item, list):
            continue
        if item[:1] == ["variant"] and (name := child(item, "name")) is not None and len(name) > 1:
            out[str(name[1])] = None
        todo += reversed(item[1:])
    return list(out)


def settings_variants(settings: object) -> list[str]:
    """The names in a .kicad_pro's variant lists, at any depth: a key
    "variants" whose value is a list of {"name": ...} (KiCad's GUI keeps
    a project's variants in schematic.variants)."""
    out: dict[str, None] = {}
    todo = [settings]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            found = item.get("variants")
            if isinstance(found, list):
                out.update(
                    (v["name"], None)
                    for v in found
                    if isinstance(v, dict) and isinstance(v.get("name"), str)
                )
            todo += reversed(list(item.values()))
        elif isinstance(item, list):
            todo += reversed(item)
    return list(out)


def project_variants(
    settings: object, pcb: Expression, schematic: Iterable[Expression]
) -> list[str]:
    """The project's variants, from every place KiCad 10.0.8 records one:
    the .kicad_pro's list (made in the GUI), the board's top-level
    (variants ...) (pcbnew's AddVariant), and each name a footprint's or a
    symbol instance's (variant (name ...)) override uses, which kicad-cli
    honors with no list naming it."""
    names = [
        *settings_variants(settings),
        *variant_names(pcb),
        *(v for sheet in schematic for v in variant_names(sheet)),
    ]
    return list(dict.fromkeys(names))


# The text variables a variant gives its own value (KiCad 10.0.8).
VARIANT_VARIABLES = ("${VARIANT}", "${VARIANT_DESC}")
# KiCad's default user names of the layers whose file (canonical) names
# differ, for a board whose (layers ...) gives none.
DEFAULT_LAYER_NAMES = {
    "F.SilkS": "F.Silkscreen",
    "B.SilkS": "B.Silkscreen",
    "Dwgs.User": "User.Drawings",
    "Cmts.User": "User.Comments",
    "F.CrtYd": "F.Courtyard",
    "B.CrtYd": "B.Courtyard",
}


def board_list_variants(pcb: Expression) -> list[str]:
    """The names in the board's own top-level (variants (variant (name X)))
    list, which pcbnew's AddVariant writes."""
    return [
        str(name[1])
        for variants in children(pcb, "variants")
        for variant in children(variants, "variant")
        if (name := child(variant, "name")) is not None and len(name) > 1
    ]


def variant_texts(pcb: Expression) -> list[tuple[str, str]]:
    """The board's shown texts that use a variant's variable, each with its
    layer (its own, or the nearest enclosing item's, as a table cell's), as
    the file names it."""
    out: list[tuple[str, str]] = []
    todo: list[tuple[Item, str | None]] = [(pcb, None)]
    while todo:
        item, layer = todo.pop()
        if not isinstance(item, list):
            continue
        own = child(item, "layer")
        here = str(own[1]) if own is not None and len(own) > 1 else layer
        hidden = child(item, "hide") is not None or "hide" in item[1:]
        texts = [i for i in item[1:] if isinstance(i, str) and not isinstance(i, Atom)]
        if not hidden and here is not None:
            out += [(here, t) for t in texts if any(v in t for v in VARIANT_VARIABLES)]
        todo += [(i, here) for i in reversed(item[1:])]
    return out


def variant_text_warning(
    listed: Sequence[str],
    pcb: Expression,
    gerbers: Iterable[str],
    pdfs: Iterable[str] = (),
) -> str | None:
    """A warning when listed variants are built, the board shows a variant's
    variable on a layer of the Gerbers or of the board PDF, and its own
    (variants ...) list lacks a listed one: kicad-cli 10.0.8 then gives the
    text one value for every variant.  It names what the text is on."""
    if not listed:
        return None
    missing = [v for v in listed if v not in board_list_variants(pcb)]
    names = {**DEFAULT_LAYER_NAMES, **layer_names(pcb)}
    in_gerbers, in_pdfs = set(gerbers), set(pdfs)
    shown = [(names.get(layer, layer), text) for layer, text in variant_texts(pcb)]
    shown = [(layer, text) for layer, text in shown if layer in in_gerbers | in_pdfs]
    if not missing or not shown:
        return None
    layers = ", ".join(dict.fromkeys(layer for layer, _ in shown))
    texts = ", ".join(dict.fromkeys(repr(text) for _, text in shown))
    outputs = [
        what
        for what, on in (("Gerbers", in_gerbers), ("board PDFs", in_pdfs))
        if any(layer in on for layer, _ in shown)
    ]
    return (
        f"text {texts} on {layers} will be the same on every variant's"
        f" {' and '.join(outputs)}: the board's own"
        f" variants list lacks {', '.join(missing)} (kicad-cli then gives the variables one"
        " value): add the variants to the board's list (pcbnew's AddVariant, then save)"
    )


def check_variants(listed: Sequence[str], project: Sequence[str]) -> tuple[list[str], list[str]]:
    """The errors and warnings of a board's variants: a listed variant the
    project lacks is an error (kicad-cli exports the default for a name it
    does not know); a project variant not listed is a warning."""
    errors = []
    for name in listed:
        if name in project:
            continue
        like = [v for v in project if v.casefold() == name.casefold()]
        hint = f" (it has {like[0]!r})" if like else ""
        errors.append(
            f"variant {name!r} is not one of the project's (its .kicad_pro, board or"
            f" schematic){hint}"
        )
    unlisted = [v for v in project if v.casefold() not in {n.casefold() for n in listed}]
    names = ", ".join(unlisted)
    one = len(unlisted) == 1
    warnings = []
    if unlisted and not listed:
        warnings.append(
            f"the project has {'variant' if one else 'variants'} {names}, and fab.toml lists"
            " none: the default variant is built"
        )
    elif unlisted:
        warnings.append(
            f"{'variant' if one else 'variants'} {names} of the project"
            f" {'is' if one else 'are'} not built"
        )
    return errors, warnings


def board_footprints(pcb: Expression) -> dict[str, str]:
    """The board's footprints' LIBRARY:NAME, by reference."""
    return {
        str(ref[2]): str(footprint[1])
        for footprint in children(pcb, "footprint")
        for ref in children(footprint, "property")
        if ref[1:2] == ["Reference"] and len(ref) > 2 and len(footprint) > 1
    }


def cross_checks(
    parts: Iterable[Part],
    fitted: Iterable[Placement],
    placed: Iterable[Placement],
    footprints: dict[str, str] | None = None,
) -> list[str]:
    """Where a build's schematic (its BOM's parts) and board (its
    placements: those fitted, and all placed, DNP too; its footprints'
    LIBRARY:NAME, by reference) differ: a part DNP in one and fitted in the
    other, a different Value, or a different footprint (a variant's
    Footprint override), the library too, whether placed or not."""
    on_board = {p.reference: p for p in placed}
    fitted_refs = {p.reference for p in fitted}
    out = []
    for p in parts:
        board_id = (footprints or {}).get(p.reference)
        if board_id is not None and p.footprint != board_id:
            out.append(
                f"{p.reference}: Footprint {p.footprint!r} in the schematic, {board_id!r}"
                " on the board: a variant cannot change a footprint"
            )
        found = on_board.get(p.reference)
        if found is None:
            continue
        if p.dnp and p.reference in fitted_refs:
            out.append(f"{p.reference}: DNP in the schematic, fitted on the board")
        elif not p.dnp and p.reference not in fitted_refs:
            out.append(f"{p.reference}: fitted in the schematic, DNP on the board")
        if p.value != found.value:
            out.append(
                f"{p.reference}: Value {p.value!r} in the schematic, {found.value!r} on the board"
            )
        if board_id is None and p.footprint.split(":", 1)[-1] != found.package:
            out.append(
                f"{p.reference}: Footprint {p.footprint!r} in the schematic, {found.package!r}"
                " on the board: a variant cannot change a footprint"
            )
    return out


def comparable(path: Path) -> object:
    """A Gerber, drill or job file's content less what differs from one
    export to the next: its comment lines (G04 and the X2 creation date in a
    Gerber, ; in Excellon) and the job file's creation date."""
    text = path.read_text()
    if path.suffix == GERBER_JOB_SUFFIX:
        job = json.loads(text)
        job.get("Header", {}).pop("CreationDate", None)
        return job
    comment = (";",) if path.suffix == DRILL_SUFFIX else ("G04", "%TF.CreationDate")
    return [line for line in text.splitlines() if not line.startswith(comment)]


def gerber_differences(first: Path, other: Path, files: Iterable[GerberFile]) -> list[GerberFile]:
    """The files of the Gerber directory first whose content differs in
    other, less comments and dates; a file missing from either differs."""
    known = {f.path: f for f in files}
    names = sorted({p.name for p in first.iterdir()} | {p.name for p in other.iterdir()})
    out = []
    for name in names:
        a, b = first / name, other / name
        if not (a.exists() and b.exists()) or comparable(a) != comparable(b):
            out.append(known.get(name, GerberFile(name, (), "", "")))
    return out


def silkscreen_only(differ: Iterable[GerberFile]) -> bool:
    """Whether these files are all silkscreen or User.Drawings, which a
    variant may change."""
    return all(f.function[:1] in VARIANT_FUNCTIONS for f in differ)


def gerber_zip(base: str, profile: Profile[Any]) -> str:
    """The Gerber zip's name: the profile's when its Gerber options are not
    kicad-cli's own."""
    suffix = "" if profile.gerbers == KICAD_GERBERS else f"-{profile.name}"
    return f"{base}-gerbers{suffix}.zip"


def write_gerber_zip(out: Path, directory: Path, documents: dict[str, str]) -> None:
    """The Gerber zip: the files in directory, then documents, by name."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(directory.iterdir()):
            z.write(f, f.name)
        for name, text in documents.items():
            z.writestr(name, text)


def revision(pcb_text: str) -> str | None:
    m = re.search(r'\(title_block\s.*?\(rev "([^"]+)"\)', pcb_text, re.S)
    return m.group(1) if m else None


# A text variable in a title-block field.
TEXT_VARIABLE = re.compile(r"\$\{([^}]*)\}")
# The passes resolving variables in variables' values take, at most.
VARIABLE_DEPTH = 8


def resolved_revision(rev: str, settings: object) -> str:
    """The title block's rev with its text variables resolved from the
    project's (the .kicad_pro's text_variables), as KiCad 10.0.8 resolves
    them on the title block; ValueError naming one it does not define."""
    variables = settings.get("text_variables", {}) if isinstance(settings, dict) else {}
    found = rev
    for _ in range(VARIABLE_DEPTH):
        names = TEXT_VARIABLE.findall(found)
        if not names:
            return found
        unknown = [n for n in names if not isinstance(variables.get(n), str)]
        if unknown:
            raise ValueError(
                f"the title block's rev {rev!r} uses ${{{unknown[0]}}}, which the project's"
                " text variables (Project Setup, Text Variables) do not define"
            )
        found = TEXT_VARIABLE.sub(lambda m: str(variables[m.group(1)]), found)
    raise ValueError(f"the title block's rev {rev!r}: its text variables do not resolve")


def report(path: Path) -> str:
    """A kicad-cli report's text, for the message that stops a release: its
    work directory goes with it."""
    try:
        return path.read_text()
    except OSError as e:
        return f"no report: {e}"


def variant_flag(variant: str | None) -> list[str]:
    """kicad-cli's argument for the variant; none for the default."""
    return [] if variant is None else ["--variant", variant]


def work_name(stem: str, variant: str | None, suffix: str) -> str:
    """A work file's name, the variant's if any."""
    return f"{stem}{suffix}" if variant is None else f"{stem}-{variant}{suffix}"


class TagPlan(Enum):
    """What a release does with its tag."""

    DIRTY = "not tagged: uncommitted sources"
    DIRTY_TAGGED = "tagged, and the sources uncommitted"
    PRESENT = "already on this commit"
    CREATE = "create it"
    MOVE = "move it here (--retag)"
    REFUSE = "on another commit, and no --retag"


# A build's files after its stem, but the Gerber zip and the profile's.
BUILD_OUTPUTS = ("odb.tgz", "ipc2581.zip", BOM)


def output_clash(
    name: str, rev: str, builds: Sequence[str | None], split: bool, profile: Profile[Any]
) -> str | None:
    """Two builds that would write a file of one name (variants "a" and
    "a-jlcpcb": NAME-REV-a-jlcpcb-bom.csv), named; None if none."""
    owner: dict[str, str | None] = {}
    for v in builds:
        base = f"{name}-{rev}" if v is None else f"{name}-{rev}-{v}"
        names = [
            *(f"{base}-{x}" for x in BUILD_OUTPUTS),
            *profile.outputs(base),
            *([gerber_zip(base, profile)] if split else []),
        ]
        for n in names:
            if n in owner and owner[n] != v:
                return f"variants {owner[n]} and {v} would both write {n}: rename one"
            owner[n] = v
    return None


def release_name_problem(root: Path, name: str, date: str, rev: str) -> str | None:
    """What is wrong with a release's directory name, DATE-REV, and its tag,
    NAME-DATE-REV: the directory one path component, the tag a name git
    takes (git check-ref-format); None if nothing."""
    directory = f"{date}-{rev}"
    where = f"(--date {date!r}, the title block's rev {rev!r})"
    if "/" in directory or "\0" in directory or directory in (os.curdir, os.pardir):
        return f"release directory {directory!r} is not one path component {where}"
    tag = f"{name}-{directory}"
    r = subprocess.run(
        ["git", "check-ref-format", f"refs/tags/{tag}"],
        cwd=root,
        env=environment(),
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        return f"tag {tag!r} is not a git tag name {where}; fix the title block's rev"
    return None


# The lock directory beside a release directory, .DATE-REV plus this.
LOCK = ".lock"


def replaceable(dest: Path, name: str, rev: str, force: bool) -> None:
    """Stop unless dest, a release directory, is not there, or --force and
    every file in it is the board's (NAME-REV-*)."""
    if not dest.exists():
        return
    if not force:
        sys.exit(f"{dest} exists; use --force to replace it")
    if foreign := sorted(f.name for f in dest.iterdir() if not f.name.startswith(f"{name}-{rev}-")):
        sys.exit(f"{dest} holds {foreign[0]}, not this board's ({name}-{rev}-*): not removed")


def running(pid: int) -> bool:
    """Whether a process of that id runs on this host."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# What each hidden directory beside a release holds, so that git add -A
# takes none of it: a .gitignore of everything.
IGNORE_ALL = ".gitignore"
# The lock directory's file naming the run that holds it: host, process.
OWNER = "owner"


def ignored_by_git(directory: Path) -> None:
    """A .gitignore of everything in directory, itself included."""
    (directory / IGNORE_ALL).write_text("*\n")


@contextlib.contextmanager
def release_lock(lock: Path) -> Iterator[None]:
    """Hold the lock directory, lock, from before the release's first
    write until after its tag and cleanup: made exclusively, its OWNER
    naming this host and process.  One there already: another run's,
    which stops this one; one a killed run left, its process gone from
    this host, which is taken over, said on stderr; one from another host,
    not judged (a process id means nothing there), which stops this run,
    naming the command that removes it."""
    # The directories made for the lock, removed with it if left empty.
    made = [d for d in [lock.parent, *lock.parent.parents] if not d.exists()]
    lock.parent.mkdir(parents=True, exist_ok=True)
    host = socket.gethostname()
    try:
        lock.mkdir()
    except FileExistsError:
        try:
            held = (lock / OWNER).read_text().split()
        except OSError:
            held = []
        if len(held) == 2 and held[0] == host and held[1].isdigit() and not running(int(held[1])):
            print(f"{lock}: left by a run that is gone ({held[1]}): taken over", file=sys.stderr)
            shutil.rmtree(lock)
            lock.mkdir()
        else:
            holder = " ".join(held) or "a run that left no owner"
            sys.exit(
                f"{lock}: another run ({holder}) is making this release, or was: wait for it,"
                f" or, if none runs, remove the lock (rm -r {shlex.quote(str(lock))})"
            )
    ignored_by_git(lock)
    (lock / OWNER).write_text(f"{host} {os.getpid()}\n")
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)
        for d in made:
            with contextlib.suppress(OSError):
                d.rmdir()


def package_commit(release: Path) -> str | None:
    """The commit a release directory's source tgz says it was built from
    (its COMMIT's first line; "-dirty" kept), or None if it says none."""
    for tgz in sorted(release.glob("*-source.tgz")):
        try:
            with tarfile.open(tgz) as t:
                member = next((m for m in t.getmembers() if m.name.endswith("/COMMIT")), None)
                found = t.extractfile(member) if member is not None else None
                if found is not None:
                    first = found.read().decode().split("\n", 1)[0]
                    return first.removeprefix("commit ").strip() or None
        except (OSError, tarfile.TarError, UnicodeDecodeError):
            continue
    return None


def recover(dest: Path, tagged: str | None) -> None:
    """Undo what a killed run left beside the release directory dest,
    under the lock, before anything else is written; each step said on
    stderr.  The tagged package is the one whose COMMIT is the tag's
    commit (tagged); one is never removed.  The building directory, never
    tagged, is removed.  An old release aside (.DATE-REV.old): with no
    release in place, put back; with one in place, the one the tag names
    kept and the other removed; neither named, the old one kept (the new
    one, untagged, is built again)."""
    building = dest.parent / f".{dest.name}{BUILDING}"
    aside = dest.parent / f".{dest.name}{OLD}"
    if building.exists():
        shutil.rmtree(building)
        print(f"{building}: an interrupted build's: removed", file=sys.stderr)
    if not aside.exists():
        return

    def put_back() -> None:
        (aside / IGNORE_ALL).unlink(missing_ok=True)
        aside.rename(dest)

    if not dest.exists():
        put_back()
        print(
            f"{dest}: put back from {aside.name}, where an interrupted run left it", file=sys.stderr
        )
        return
    if tagged is not None and package_commit(dest) == tagged:
        shutil.rmtree(aside)
        print(
            f"{dest}: the release its tag names kept; {aside.name}, the one it replaced, removed",
            file=sys.stderr,
        )
        return
    shutil.rmtree(dest)
    put_back()
    named = (
        "the release its tag names"
        if tagged is not None and package_commit(dest) == tagged
        else "the old release"
    )
    print(
        f"{dest}: {named} put back from {aside.name}; the untagged one an interrupted run put"
        " in its place removed",
        file=sys.stderr,
    )


def tool_command(tool: str, path: Path, root: Path) -> str:
    """The command that runs one of ee_tooling's tools on path from the
    current directory: with the uv project whose venv runs this one, when
    there is one, so it works from any directory (from here when the
    repository holds it, else absolute); the paths quoted for a shell."""
    here = Path(os.getcwd())
    project = Path(sys.prefix).parent
    target = shlex.quote(os.path.relpath(path, here))
    if not (project / "pyproject.toml").is_file():
        return f"{tool} {target}"
    inside = project.resolve().is_relative_to(root.resolve())
    named = os.path.relpath(project, here) if inside else str(project)
    return f"uv run --project {shlex.quote(named)} {tool} {target}"


def tag_refused(tag: str, tagged: str, commit: str) -> str:
    """Why a rebuild stops on a tag on another commit, and the choices."""
    return (
        f"tag {tag} is on {tagged}, the commit its release was built from; HEAD is {commit}:"
        " it has moved since (a commit of the production directory moves it), so a rebuild"
        " here would no longer match the tag.  Either leave the release as it is; or rebuild"
        f" from {tagged} (git checkout {tagged}); or, before the order is placed, rebuild"
        " here with --retag, which moves the tag to HEAD"
    )


def tag_plan(dirty: bool, tagged: str | None, head: str, retag: bool) -> TagPlan:
    """The plan for a release's tag, given the commit it is on (tagged).  A
    build from uncommitted sources is never tagged, and never replaces a
    tagged release: the tag would name a package it does not describe."""
    if dirty:
        return TagPlan.DIRTY if tagged is None else TagPlan.DIRTY_TAGGED
    if tagged is None:
        return TagPlan.CREATE
    if tagged == head:
        return TagPlan.PRESENT
    return TagPlan.MOVE if retag else TagPlan.REFUSE


class Release:
    """One board's release, built in a scratch directory."""

    def __init__(self, root: Path, board: Board, work: Path, copper: tuple[str, ...] = ()) -> None:
        self.root = root
        self.board = board
        self.work = work
        self.copper = copper  # the board's copper layers, top to bottom, from its stackup
        # The scratch copy of the board's project kicad-cli reads, once
        # check has made it.
        self.inputs: Path | None = None

    @property
    def layers(self) -> list[str]:
        """The layers in the Gerber zip."""
        return [*self.copper, *OTHER_LAYERS]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args, cwd=self.root, env=environment(), capture_output=True, text=True, check=False
        )

    def kicad(self, *args: str) -> None:
        r = self.run("kicad-cli", *args)
        if r.returncode != 0:
            sys.exit(f"kicad-cli {' '.join(args)} failed:\n{r.stdout}{r.stderr}")

    def path(self, p: Path) -> str:
        """A board file's path for kicad-cli: in the scratch copy of the
        project kicad-cli reads (inputs), once check has made it."""
        if self.inputs is not None and p.parent == self.board.directory:
            return str(self.inputs / p.name)
        return str(self.root / p)

    def rules_read(self, rules: Installed) -> None:
        """Stop unless kicad-cli reads the rules: on a .kicad_dru it cannot
        parse it drops every custom rule, the profile's too, and says
        nothing (10.0.8).  A DRC run on a scratch copy whose rules end with
        CANARY, a rule every item breaks, must report it."""
        project = json.dumps(rules.project, indent=2) + "\n"
        scratch = self.scratch_project(RULES_PROJECT, f"{rules.dru}\n{CANARY}", project)
        out = self.work / "rules.rpt"
        self.run("kicad-cli", "pcb", "drc", "-o", str(out), str(scratch / self.board.pcb.name))
        if CANARY_NAME not in report(out):
            dru = self.board.directory / f"{self.board.name}{DRU}"
            sys.exit(
                f"{dru}: kicad-cli reads none of the custom rules, ee_tooling's or the board's:"
                " one does not parse (Board Setup, Custom Rules, Check rule syntax)"
            )

    def drc_project(self, rules: Installed) -> Path:
        """A scratch copy of the board's project with the rules installed
        (scratch_project); return its .kicad_pcb."""
        project = json.dumps(rules.project, indent=2) + "\n"
        return self.scratch_project(DRC_PROJECT, rules.dru, project) / self.board.pcb.name

    def scratch_project(self, name: str, dru: str | None, project: str | None) -> Path:
        """A scratch copy of the board's project, in the work directory's
        subdirectory name, with dru and project as its .kicad_dru and
        .kicad_pro (none if None); return its directory.  The project's
        own files are copied (DRC_COPIED), and every other entry of its
        directory linked, at the board directory's own path from the
        repository's root under the scratch directory, beside a link to
        every other entry of each of its ancestors up to the root: so a
        library path relative to the project (${KIPRJMOD}/../lib), which
        DRC checks footprints against, finds what it finds for the board
        (a library is inside the repository: main refuses one outside).
        kicad-cli writes NAME.kicad_prl beside the project it reads (every
        command, 10.0.8): here, never in the board's directory."""
        b = self.board
        root = self.root.resolve()
        source = (root / b.directory).resolve()
        top = self.work / name
        scratch = top / source.relative_to(root)
        scratch.mkdir(parents=True)
        copied = {f"{b.name}{s}" for s in DRC_COPIED}
        written = {f"{b.name}{DRU}", b.project.name}
        for entry in sorted(source.iterdir()):
            if entry.name in written or entry.name in DRC_LEFT_OUT:
                continue
            if entry.name in copied:
                try:
                    shutil.copyfile(entry, scratch / entry.name)
                except OSError as e:
                    sys.exit(f"DRC's scratch copy: {entry}: {e.strerror}")
            else:
                (scratch / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
        ancestors = [p for p in source.parents if p.is_relative_to(root)]
        for here, parent in zip([source, *ancestors], ancestors, strict=False):
            mirror = top / parent.relative_to(root)
            try:
                entries = sorted(parent.iterdir())
            except OSError as e:
                print(f"DRC's scratch copy: {parent} not listed: {e}", file=sys.stderr)
                continue
            for entry in entries:
                if entry.name != here.name:
                    (mirror / entry.name).symlink_to(entry)
        if dru is not None:
            (scratch / f"{b.name}{DRU}").write_text(dru)
        if project is not None:
            (scratch / b.project.name).write_text(project)
        return scratch

    def submodules(self) -> list[Path]:
        """The repository's submodules, at any depth (submodules())."""
        return submodules(self.root)

    def check(
        self,
        allow_dirty: bool,
        rules: Installed,
        productions: Iterable[Path] = (),
        libraries: Iterable[Path] = (),
        start: str | None = None,
    ) -> tuple[str, str]:
        """Committed sources and libraries (inside the repository), less the
        production directories, first, before any input is copied; then
        ERC, DRC with schematic parity and the rules (drc_project).  Return
        the commit (HEAD's full hash, which must still be start, read as
        the release began) and what is uncommitted (uncommitted(), "" for
        nothing): the baseline the build is compared with at its end."""
        b = self.board
        head = self.head()
        if start is not None and head != start:
            sys.exit(
                f"HEAD moved while the release read its inputs, from {start[:12]}: build again"
            )
        status = self.uncommitted(productions, libraries)
        if status and not allow_dirty:
            sys.exit(f"uncommitted changes in the release's sources:\n{status}")
        own = [self.root / b.directory / f"{b.name}{s}" for s in (DRU, ".kicad_pro")]
        dru, project = (f.read_text(encoding=PROJECT_TEXT) if f.exists() else None for f in own)
        self.inputs = self.scratch_project(EXPORT_PROJECT, dru, project)
        erc = self.work / "erc.rpt"
        if self.run(
            "kicad-cli", "sch", "erc", "--exit-code-violations", "-o", str(erc), self.path(b.sch)
        ).returncode:
            sys.exit(f"ERC is not clean:\n{report(erc)}")
        self.rules_read(rules)
        drc = self.work / "drc.rpt"
        if self.run(
            "kicad-cli",
            "pcb",
            "drc",
            "--schematic-parity",
            "--exit-code-violations",
            "-o",
            str(drc),
            str(self.drc_project(rules)),
        ).returncode:
            sys.exit(f"DRC is not clean, with {self.board.profile}'s rules:\n{report(drc)}")
        return head, status

    def checkout(self, modules: Sequence[Path]) -> Path | None:
        """The ee_tooling checkout the release runs from, from the root, when
        the repository holds it as a submodule or as tracked files (its
        parts.toml, its rules are then inputs); None otherwise, as for a
        package installed in a venv."""
        found = located(self.root, CHECKOUT)
        if found is None or found == Path("."):
            return None
        if found in modules or git_out(self.root, "ls-files", "-z", "--", f":(literal){found}"):
            return found
        return None

    def named(self, extra: Iterable[Path]) -> list[Path]:
        """The paths the release reads by name, from the root: fab.toml, the
        project's own files, and extra (its sheets and libraries)."""
        b = self.board
        own = [*(b.directory / f"{b.name}{s}" for s in PROJECT_SUFFIXES)]
        own += [b.directory / f for f in PROJECT_FILES]
        present = [p for p in own if (self.root / p).exists()]
        return list(dict.fromkeys(normal(p) for p in [Path(CONFIG), *present, *extra]))

    def uncommitted(self, productions: Iterable[Path], extra: Iterable[Path]) -> str:
        """git status of what the release reads, every production directory
        left out (as written and with its links followed): the board's
        directory, its sources and the ee_tooling checkout when the
        repository holds it (checkout()), their untracked files too; and
        what it reads by name (named()), a file git ignores there too.
        Each path as written and with its links followed, a link out of the
        repository refused.  A path is asked of the repository that holds
        it; each submodule holding one, or under one, at every level, by
        its gitlink in its parent (a moved or changed submodule), and each
        under one by its own status, whatever any ignore setting.  Literal
        pathspecs: no name is a pattern."""
        b = self.board
        modules = submodules(self.root)
        named = self.named(extra)
        trees = [b.directory, *b.sources]
        if (checkout := self.checkout(modules)) is not None:
            trees.append(checkout)
        # Each path to ask, and whether git is asked of its ignored files.
        asked: dict[Path, bool] = {}
        for p, by_name in [*((p, False) for p in trees), *((p, True) for p in named)]:
            target = located(self.root, p)
            if target is None:
                sys.exit(f"{p} leads out of the repository (a link): the release reads it")
            # A path through a link is asked as the link (git refuses a
            # path beyond one), and as its target.
            for q in (through_link(self.root, p) or normal(p), target):
                asked[q] = asked.get(q, False) or by_name
        left_out = {
            q for p in productions for q in (normal(p), located(self.root, p)) if q is not None
        }
        lines: list[str] = []

        def status(repo: Path, specs: list[str], *options: str) -> list[str]:
            found = git_out(
                self.root / repo,
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
                *options,
                "--",
                *specs,
            )
            prefix = "" if repo == Path(".") else f"{repo}/"
            return [f"{e[:3]}{prefix}{e[3:]}" for e in status_entries(found)]

        def spec(path: Path, repo: Path) -> str:
            rel = path.relative_to(repo)
            return "." if rel == Path(".") else f":(literal){rel}"

        repos = dict.fromkeys(repository_of(q, modules) for q in asked)
        for repo in repos:
            mine = [q for q in asked if repository_of(q, modules) == repo]
            excluded = [
                f":(exclude,literal){p.relative_to(repo)}"
                for p in left_out
                if p.is_relative_to(repo)
            ]
            specs = [spec(q, repo) for q in mine]
            lines += status(repo, [*specs, *excluded])
            by_names = [spec(q, repo) for q in mine if asked[q]]
            if by_names:
                lines += [
                    e
                    for e in status(repo, [*by_names, *excluded], "--ignored=matching")
                    if e.startswith("!! ")
                ]
        # Every submodule holding a path asked, or under one, at every level:
        # its gitlink in its parent repository, and, under one, its own status.
        for m in modules:
            holds = any(q.is_relative_to(m) for q in asked)
            under = any(m.is_relative_to(q) for q in asked)
            if not (holds or under):
                continue
            parent = repository_of(m, [x for x in modules if x != m])
            lines += status(parent, [spec(m, parent)])
            if under:
                lines += status(m, ["."])
        return "\n".join(dict.fromkeys(lines))

    def tagged(self, name: str) -> str | None:
        """The full hash of the commit tag `name` is on, or None."""
        r = self.run("git", "rev-parse", "-q", "--verify", f"refs/tags/{name}^{{commit}}")
        return r.stdout.strip() if r.returncode == 0 else None

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        """git's output; a failure stops the release, with git's message."""
        r = self.run("git", *args)
        if r.returncode != 0:
            sys.exit(f"git {' '.join(args)} failed ({r.returncode}):\n{r.stderr}")
        return r.stdout

    def tag(self, name: str, message: str, move: bool, commit: str = "HEAD") -> str | None:
        """An annotated tag on commit; move replaces one of that name.  What
        went wrong, or None."""
        r = self.run("git", "tag", "-a", *(["-f"] if move else []), name, "-m", message, commit)
        if r.returncode != 0:
            return f"git tag {name} failed:\n{r.stdout}{r.stderr}"
        return None

    def export_gerbers(
        self, directory: Path, options: GerberOptions, variant: str | None = None
    ) -> list[GerberFile]:
        """The Gerbers, of the variant if any, and the drill (kicad-cli's
        drill export takes no variant), exported into directory; return its
        files."""
        b = self.board
        directory.mkdir()
        self.kicad(
            "pcb",
            "export",
            "gerbers",
            "--layers",
            ",".join(self.layers),
            *([] if options.x2 else ["--no-x2"]),
            *(["--subtract-soldermask"] if options.subtract_soldermask else []),
            *([] if options.protel_extensions else ["--no-protel-ext"]),
            "--check-zones",
            *variant_flag(variant),
            "-o",
            str(directory),
            self.path(b.pcb),
        )
        self.kicad(
            "pcb",
            "export",
            "drill",
            "--format",
            "excellon",
            "--excellon-units",
            "mm",
            "--excellon-zeros-format",
            "decimal",
            "--excellon-oval-format",
            "alternate",
            *(["--excellon-separate-th"] if options.separate_drill else []),
            "--drill-origin",
            "absolute",
            "-o",
            f"{directory}/",
            self.path(b.pcb),
        )
        try:
            return gerber_files(directory, b.name, self.layers)
        except (ValueError, KeyError, IndexError) as e:
            sys.exit(f"no Gerber map: {e}")

    def fabrication(
        self,
        label: str,
        pcb: Expression,
        files: list[GerberFile],
        directory: Path,
        stackup_name: str,
        map_name: str,
    ) -> tuple[str, str]:
        """The stackup and the fabrication notes, "NAME REV" being label,
        from the board, its project's net classes and the Gerber directory;
        the notes name the stackup and the Gerber map as given."""
        project = self.root / self.board.project
        try:
            settings = json.loads(project.read_text(encoding=PROJECT_TEXT))
        except (OSError, ValueError) as e:
            sys.exit(f"{project}: {e}")
        found = stackup(pcb)
        thickness = board_thickness(pcb)
        impedance = impedances(settings)
        return (
            stackup_document(f"{label}: stackup", found, thickness, files, impedance),
            notes(
                f"{label}: fabrication notes",
                found,
                thickness,
                files,
                impedance,
                minimums(directory),
                stackup_name,
                map_name,
                self.board.fab,
            ),
        )

    def ipc2581(
        self, out: Path, distributor: tuple[str, str] | None, variant: str | None = None
    ) -> None:
        """IPC-2581, its BOM with the distributor (the part-number field, the
        distributor's name), if any."""
        bom = [*IPC2581_BOM]
        if distributor is not None:
            field, name = distributor
            bom += [("--bom-col-dist-pn", field), ("--bom-col-dist", name)]
        self.kicad(
            "pcb",
            "export",
            "ipc2581",
            "--version",
            IPC2581_VERSION,
            "--units",
            IPC2581_UNITS,
            "--precision",
            str(IPC2581_PRECISION),
            "--compress",
            *(x for pair in bom for x in pair),
            *variant_flag(variant),
            "-o",
            str(out),
            self.path(self.board.pcb),
        )

    def odb(self, out: Path, variant: str | None = None) -> None:
        self.kicad(
            "pcb",
            "export",
            "odb",
            "--units",
            ODB_UNITS,
            "--precision",
            str(ODB_PRECISION),
            "--compression",
            ODB_COMPRESSION,
            "--check-zones",
            *variant_flag(variant),
            "-o",
            str(out),
            self.path(self.board.pcb),
        )

    def parts(self, variant: str | None = None) -> list[Part]:
        """The schematic's parts, of the variant if any, DNP too, in KiCad's
        order of reference."""
        raw = self.work / work_name("bom", variant, ".csv")
        fitted_raw = self.work / work_name("bom-fitted", variant, ".csv")
        for out, dnp in ((raw, []), (fitted_raw, ["--exclude-dnp"])):
            self.kicad(
                "sch",
                "export",
                "bom",
                "--fields",
                ",".join(BOM_FIELDS),
                *dnp,
                *variant_flag(variant),
                "-o",
                str(out),
                self.path(self.board.sch),
            )
        fitted = {r["Reference"] for r in csv.DictReader(io.StringIO(fitted_raw.read_text()))}
        return [
            Part(
                r["Reference"],
                r["Value"],
                r["Footprint"],
                r["LCSC"],
                dnp=r["Reference"] not in fitted,
                mpn=r["MPN"],
                manufacturer=r["Manufacturer"],
                datasheet=r["Datasheet"],
            )
            for r in csv.DictReader(io.StringIO(raw.read_text()))
        ]

    def placements(self, variant: str | None = None, dnp: bool = False) -> list[Placement]:
        """The board's fitted footprints, of the variant if any, both sides,
        in KiCad's order; with dnp, those not fitted too."""
        raw = self.work / work_name("pos-all" if dnp else "pos", variant, ".csv")
        self.kicad(
            "pcb",
            "export",
            "pos",
            "--format",
            "csv",
            "--units",
            "mm",
            "--side",
            "both",
            *([] if dnp else ["--exclude-dnp"]),
            *variant_flag(variant),
            "-o",
            str(raw),
            self.path(self.board.pcb),
        )
        return [
            Placement(
                r["Ref"],
                r["Val"],
                r["Package"],
                float(r["PosX"]),
                float(r["PosY"]),
                float(r["Rot"]),
                r["Side"],
            )
            for r in csv.DictReader(io.StringIO(raw.read_text()))
        ]

    def source(
        self,
        out: Path,
        commit: str,
        variants: Sequence[str | None] = (None,),
        productions: Sequence[Path] = (),
        sheets: Sequence[Path] = (),
    ) -> None:
        """The source tgz, with the schematic and board PDFs of each variant
        (None: the default)."""
        b = self.board
        pdf = self.work / "pdf"
        pdf.mkdir()
        for v in variants:
            stem = b.name if v is None else f"{b.name}-{v}"
            self.kicad(
                "sch",
                "export",
                "pdf",
                *variant_flag(v),
                "-o",
                str(pdf / f"{stem}-schematic.pdf"),
                self.path(b.sch),
            )
            self.kicad(
                "pcb",
                "export",
                "pdf",
                "--mode-multipage",
                "--layers",
                ",".join([*self.copper, *BOARD_PDF_LAYERS]),
                "--common-layers",
                "Edge.Cuts",
                *variant_flag(v),
                "-o",
                str(pdf / f"{stem}-board.pdf"),
                self.path(b.pcb),
            )
        top = out.stem.removesuffix(".tar")
        # The files git tracks: an ignored or untracked file under a source
        # (KiCad's backups, a venv) is not the commit's.
        files = tracked(self.root, b.source_paths(self.root, sheets), productions)
        with tarfile.open(out, "w:gz") as t:
            for f in files:
                t.add(self.root / f, arcname=f"{top}/{f}", recursive=False)
            for f in sorted(pdf.iterdir()):
                t.add(f, arcname=f"{top}/pdf/{f.name}")
            info = (
                f"commit {commit}\nbuilt {datetime.datetime.now().isoformat(timespec='seconds')}\n"
            )
            ti = tarfile.TarInfo(f"{top}/COMMIT")
            ti.size = len(info)
            t.addfile(ti, io.BytesIO(info.encode()))


def repository_root() -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        sys.exit(f"not in a git repository: {r.stderr.strip()}")
    return Path(r.stdout.strip())


def profile_options(boards: dict[str, Board], profiles: dict[str, Profile[Any]]) -> dict[str, Any]:
    """Each board's options, by name, checked by its profile; ValueError
    naming a bad key."""
    out = {}
    for b in boards.values():
        try:
            out[b.name] = profiles[b.profile].options(b.options)
        except ValueError as e:
            raise ValueError(f"[[board]] {b.name!r}: [board.{b.profile}]: {e}") from e
    return out


def board_rules(root: Path, b: Board, profile: Profile[Any], options: object) -> Installed:
    """The board's project with its profile's rules installed, for DRC; a
    warning on stderr for each way its own files differ (adopt --yes
    installs them there)."""
    dru_path, project_path = root / b.directory / f"{b.name}{DRU}", root / b.project
    try:
        project = json.loads(project_path.read_text(encoding=PROJECT_TEXT))
        dru = dru_path.read_text(encoding=PROJECT_TEXT) if dru_path.exists() else None
        section = installed(profile.name, profile.rules, options)
        found = install(dru, project, section, board_setup(profile.rules))
    except (OSError, ValueError) as e:
        sys.exit(f"{b.name}: its rules: {e}")
    adopt = "run ee_tooling's adopt --yes to make KiCad's DRC the release's"
    warnings = []
    if found.stale:
        warnings.append(
            f"{dru_path.name}: ee_tooling's {profile.name} rules missing or out of date"
        )
    if found.raised:
        warnings.append(
            f"{project_path.name}: Board Setup below {profile.name}'s minimums"
            f" ({'; '.join(found.raised)})"
        )
    for w in warnings:
        print(f"warning: {w}: {adopt}", file=sys.stderr)
    if found.copied:
        print(
            f"warning: {copied_message(str(b.directory / dru_path.name), found.copied)}",
            file=sys.stderr,
        )
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("board", nargs="?", help=f"a [[board]]'s name in {CONFIG}; the first if none")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--force", action="store_true", help="replace an existing release")
    ap.add_argument("--allow-dirty", action="store_true", help="allow uncommitted sources")
    ap.add_argument("--retag", action="store_true", help="move the release's tag to this commit")
    a = ap.parse_args()
    root = repository_root()
    # HEAD as the release begins, before any input is read: a commit made
    # while it reads them is not taken for the commit it is built from.
    begun = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "HEAD"],
        cwd=root,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    start = begun.stdout.strip() if begun.returncode == 0 else None
    try:
        boards = load_config((root / CONFIG).read_text(encoding=PROJECT_TEXT))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as e:
        sys.exit(f"{root / CONFIG}: {e}")
    if errors := located_production_errors(root, boards.values()):
        sys.exit(f"{root / CONFIG}: {errors[0]}")
    # Made before anything is checked or written: loading a profile's data can fail.
    try:
        profiles = {p: PROFILES[p]() for p in dict.fromkeys(b.profile for b in boards.values())}
    except ValueError as e:
        sys.exit(f"profile's data: {e}")
    try:
        options = profile_options(boards, profiles)
    except ValueError as e:
        sys.exit(f"{root / CONFIG}: {e}")

    name = a.board if a.board is not None else next(iter(boards))
    if name not in boards:
        sys.exit(f"{CONFIG} has no board {name!r}: one of {', '.join(boards)}")
    b = boards[name]
    # A source through a link would be archived as the bare link.
    for s in b.sources:
        if link := through_link(root, s):
            sys.exit(f"{CONFIG}: source {str(s)!r} goes through the link {link}: name its target")
    missing = [str(s) for s in (b.pcb, b.sch, *b.sources) if not (root / s).exists()]
    if missing:
        sys.exit(f"{CONFIG} names files that are not there: {', '.join(missing)}")
    # The files read first, then what they name: a pointer for either named
    # as one, not as a file that does not parse.
    modules = submodules(root)
    try:
        primary = [p for p in (b.pcb, b.sch, b.project) if (root / p).exists()]
        if pointers := lfs_pointers(root, primary):
            sys.exit(pointer_message(pointers, modules))
    except OSError as e:
        sys.exit(f"{e.filename}: {e.strerror}")
    pcb_text = (root / b.pcb).read_text(encoding=PROJECT_TEXT)
    try:
        pcb = parse(pcb_text)
    except ParseError as e:
        sys.exit(f"{b.pcb}: {e}")
    try:
        found_libraries = libraries(root, b.directory, pcb)
    except ValueError as e:
        sys.exit(str(e))
    if found_libraries.outside:
        sys.exit(
            f"{b.name}: libraries outside the repository, or under a variable not KiCad's own"
            " (its library tables' and 3D models' paths, as written, each with its fix):\n"
            + "".join(f"  {p}: {found_libraries.fixes[p]}\n" for p in found_libraries.outside)
            + "a library lives inside the repository (ee_tooling's docs/kicad-style.md, Files)"
        )
    for model in found_libraries.unresolved:
        print(
            f"warning: 3D model not found, from the project or its library: {model}",
            file=sys.stderr,
        )
    missing_sheets: list[tuple[Path, str]] = []
    parents: dict[Path, Path] = {}
    try:
        sheets_found = list(sheet_files((root / b.sch).resolve(), missing_sheets, parents))
    except (OSError, ParseError) as e:
        sys.exit(f"{b.sch}: {e}")
    schematic = [s for _, s in sheets_found]
    top = root.resolve()

    # kicad-cli takes a sheet file it cannot find as an empty sheet, and one
    # outside the repository is not committed with the board: either would
    # drop parts from ERC, parity and every BOM unseen.
    def from_root(path: Path) -> Path:
        return path.relative_to(top) if path.is_relative_to(top) else path

    if sheet_problems := [
        f"sheet {name}, named by {from_root(parent)}: not found" for parent, name in missing_sheets
    ] + [
        f"sheet {p}, named by {from_root(parents[p])}: outside the repository"
        for p, _ in sheets_found
        if not p.is_relative_to(top)
    ]:
        sys.exit("\n".join(f"{b.name}: {s}" for s in sheet_problems))
    sheet_paths = [p.relative_to(top) for p, _ in sheets_found]
    try:
        # The files the release reads: those git tracks under its sources and
        # libraries, and those it reads by name, tracked or not.
        read = [*b.source_paths(root, sheet_paths), *found_libraries.inside]
        read_by_name = [p for p in (Path(CONFIG), *read) if (root / p).is_file()]
        inputs = [*tracked(root, read, [x.production for x in boards.values()]), *read_by_name]
        pointers = lfs_pointers(root, list(dict.fromkeys(inputs)))
    except OSError as e:
        sys.exit(f"{e.filename}: {e.strerror}")
    if pointers:
        sys.exit(pointer_message(pointers, modules))
    rev = revision(pcb_text)
    if rev is None:
        sys.exit(f"no revision in {b.pcb.name}'s title block")
    try:
        settings = json.loads((root / b.project).read_text(encoding=PROJECT_TEXT))
        resolved = resolved_revision(rev, settings)
    except (OSError, ValueError) as e:
        sys.exit(f"{b.project}: {e}")
    if not resolved.strip():
        sys.exit(f"no revision in {b.pcb.name}'s title block: its rev {rev!r} is empty")
    rev = resolved
    if problem := release_name_problem(root, b.name, a.date, rev):
        sys.exit(problem)
    dest = root / b.production / f"{a.date}-{rev}"
    # The release directory's lock, held until its tag is made and the
    # old release removed; what a killed run left recovered under it.
    tag_name = f"{b.name}-{dest.name}"
    on_tag = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}^{{commit}}"],
        cwd=root,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    with release_lock(dest.parent / f".{dest.name}{LOCK}"):
        recover(dest, on_tag.stdout.strip() if on_tag.returncode == 0 else None)
        if dest.exists() and not a.force:
            sys.exit(f"{dest} exists; use --force to replace it")
        found = stackup(pcb)
        if found is None:
            sys.exit(f"{b.pcb.name} has no stackup: set one in Board Setup, Physical Stackup")
        copper = copper_layers(found)
        if not copper:
            sys.exit(f"{b.pcb.name}'s stackup has no copper layer")
        if table := check_stackup(pcb).findings:
            sys.exit(
                "".join(f"{b.pcb.name}: {f.message}\n" for f in table)
                + f"write or refresh it with: {tool_command('stackup-table', root / b.pcb, root)}"
            )
        errors, warnings = check_variants(b.variants, project_variants(settings, pcb, schematic))
        if errors:
            sys.exit("\n".join(f"{b.name}: {e}" for e in errors))
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        if (
            text := variant_text_warning(
                b.variants, pcb, [*copper, *OTHER_LAYERS], [*copper, *BOARD_PDF_LAYERS]
            )
        ) is not None:
            print(f"warning: {b.pcb.name}: {text}", file=sys.stderr)
        # Each build: a listed variant, or the default (None).
        builds: list[str | None] = [*b.variants] or [None]
        profile = profiles[b.profile]
        rules = board_rules(root, b, profile, options[b.name])
        with tempfile.TemporaryDirectory() as tmp:
            r = Release(root, b, Path(tmp), copper)
            productions = [x.production for x in boards.values()]
            read = [*found_libraries.inside, *sheet_paths]
            head, status = r.check(a.allow_dirty, rules, productions, read, start)
            # The commit the release is built from, read once: its COMMIT file,
            # its tag and what is printed all name it.
            short = r.git("rev-parse", "--short", head).strip()
            commit = head + ("-dirty" if status else "")
            tag = f"{b.name}-{dest.name}"
            tagged = r.tagged(tag)
            plan = tag_plan(bool(status), tagged, head, a.retag)
            if plan is TagPlan.REFUSE:
                sys.exit(tag_refused(tag, (tagged or "")[:7], short))
            if plan is TagPlan.DIRTY_TAGGED:
                sys.exit(
                    f"tag {tag} names this release, and its sources are uncommitted"
                    " (--allow-dirty): a build from them would replace the tagged package and leave"
                    " the tag naming one it does not describe.  Commit them and rebuild, or build"
                    " the trial under another --date"
                )
            # Each build's parts and placements, checked.
            rows: dict[str | None, tuple[list[Part], list[Placement]]] = {}
            refusals = []
            for v in builds:
                parts, placements = r.parts(v), r.placements(v)
                found_problems = cross_checks(
                    parts, placements, r.placements(v, True), board_footprints(pcb)
                )
                found_problems += profile.check(parts, placements, options[b.name])
                for w in profile.warnings(parts, placements, options[b.name]):
                    print(f"warning: {w if v is None else f'variant {v}: {w}'}", file=sys.stderr)
                refusals += [p if v is None else f"variant {v}: {p}" for p in found_problems]
                rows[v] = (parts, placements)
            if refusals:
                sys.exit("\n".join(refusals))
            # Each build's Gerbers, compared with the first's.
            directories = {v: r.work / work_name(GERBERS, v, "") for v in builds}
            exported = {v: r.export_gerbers(d, profile.gerbers, v) for v, d in directories.items()}
            files = exported[builds[0]]
            differ = {
                v: gerber_differences(directories[builds[0]], d, files)
                for v, d in list(directories.items())[1:]
            }
            if refused := [
                f"variant {v}: its Gerbers differ from variant {builds[0]}'s in"
                f" {', '.join(x.layer or x.path for x in d)}: a variant cannot change copper,"
                " mask, paste, Edge.Cuts or the drill"
                for v, d in differ.items()
                if not silkscreen_only(d)
            ]:
                sys.exit("\n".join(refused))
            split = any(differ.values())
            if split:
                layers = sorted({x.layer or x.path for d in differ.values() for x in d})
                print(
                    f"warning: variants {', '.join(map(str, builds))}: the Gerbers differ in"
                    f" {', '.join(layers)}: a Gerber zip per variant, each a bare-board order of"
                    " its own",
                    file=sys.stderr,
                )
            if clash := output_clash(b.name, rev, builds, split, profile):
                sys.exit(clash)
            replaceable(dest, b.name, rev, a.force)
            # Built beside the old release, which a failure leaves as it was.
            building = dest.parent / f".{dest.name}{BUILDING}"
            # The lock is held and recover() removed any left: this run's.
            building.mkdir()
            ignored_by_git(building)
            try:
                out = building
                base = f"{b.name}-{rev}"
                label = f"{b.name} {rev}"
                # Each build's file stem: NAME-REV, or NAME-REV-VARIANT.
                bases = {v: base if v is None else f"{base}-{v}" for v in builds}
                zips = {v: gerber_zip(bases[v] if split else base, profile) for v in builds}
                named = list(dict.fromkeys(zips.values()))
                title = (
                    f"{label}: the files in {named[0]}"
                    if len(named) == 1
                    else f"{label}: the files in each of {', '.join(named)}"
                )
                map_text = gerber_map(title, files)
                stackup_name, map_name = f"{base}-{STACKUP}", f"{base}-{GERBER_MAP}"
                stackup_text, notes_text = r.fabrication(
                    label, pcb, files, directories[builds[0]], stackup_name, map_name
                )
                # In the zip, the notes name the documents by their names there.
                _, zip_notes = r.fabrication(
                    label, pcb, files, directories[builds[0]], STACKUP, GERBER_README
                )
                documents = {GERBER_README: map_text, STACKUP: stackup_text, NOTES: zip_notes}
                for v in builds if split else builds[:1]:
                    write_gerber_zip(out / zips[v], directories[v], documents)
                (out / map_name).write_text(map_text)
                (out / stackup_name).write_text(stackup_text)
                (out / f"{base}-{NOTES}").write_text(notes_text)
                for v in builds:
                    r.odb(out / f"{bases[v]}-odb.tgz", v)
                    r.ipc2581(out / f"{bases[v]}-ipc2581.zip", profile.distributor, v)
                for v in builds:
                    parts, placements = rows[v]
                    write_csv(out / f"{bases[v]}-{BOM}", HOUSE_BOM_HEADER, house_bom(parts))
                    profile.assembly(out, bases[v], parts, placements, options[b.name])
                r.source(
                    out / f"{base}-source.tgz",
                    commit,
                    builds,
                    [x.production for x in boards.values()],
                    sheet_paths,
                )
                # The package describes the commit and the sources as they were
                # at the check: a commit or a change since, and it would not.
                if r.head() != head:
                    sys.exit(
                        f"HEAD moved during the build, from {short}: nothing replaced; build again"
                    )
                if (now := r.uncommitted(productions, read)) != status:
                    sys.exit(
                        f"the release's sources changed during the build: nothing replaced; build"
                        f" again\n{now}"
                    )
                # Another run may have made the release meanwhile.
                replaceable(dest, b.name, rev, a.force)
                (building / IGNORE_ALL).unlink()
            except BaseException:
                shutil.rmtree(building)
                raise
            # The old release kept aside until the tag is made: a failure at
            # any step leaves the old release with its old tag, or the new with
            # its new tag.
            aside = dest.parent / f".{dest.name}{OLD}"
            if dest.exists():
                dest.rename(aside)
                ignored_by_git(aside)

            def put_back() -> None:
                if aside.exists():
                    (aside / IGNORE_ALL).unlink(missing_ok=True)
                    aside.rename(dest)

            try:
                building.rename(dest)
            except OSError as e:
                put_back()
                shutil.rmtree(building)
                sys.exit(f"{building} not put in place: {e.strerror}; the old release is left")
            message = f"{b.name} {rev}: fab release {b.production / dest.name}"
            if b.variants:
                message += f"; variants {', '.join(b.variants)}"
            if plan in (TagPlan.CREATE, TagPlan.MOVE) and (
                failed := r.tag(tag, message, plan is TagPlan.MOVE, head)
            ):
                shutil.rmtree(dest)
                put_back()
                sys.exit(f"{failed}\nthe old release and its tag are left as they were")
            if aside.exists():
                shutil.rmtree(aside)
            if plan in (TagPlan.CREATE, TagPlan.MOVE):
                force = " --force" if plan is TagPlan.MOVE else ""
                refspec = shlex.quote(f"refs/tags/{tag}")
                print(f"tagged {tag}; push it with: git push{force} origin {refspec}")
            else:
                print(f"tag {tag}: {plan.value}")
        print(f"{dest.relative_to(root)} from {short}{'-dirty' if status else ''}:")
        for f in sorted(dest.iterdir()):
            print(f"  {f.name}")


if __name__ == "__main__":
    main()
