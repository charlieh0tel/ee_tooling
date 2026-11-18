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
is not tagged; a tag already on another commit stops it, unless --retag.
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
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
from ee_tooling.kicad_rules import Installed, board_setup, install, installed
from ee_tooling.kicad_style_netclass import sheets
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
from ee_tooling.sexpr import Expression, Item, ParseError, child, parse

CONFIG = "fab.toml"
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
# Keys fab.toml no longer has, with why: present, each is an error.
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
# The work directory's subdirectory DRC's scratch copy of the project is
# in, and the suffix of a project's custom rules.
DRC_PROJECT = "project"
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

# The BOM export's fields: ${DNP} is kicad-cli's, "DNP" for a part not
# fitted, "" for one fitted, under the header DNP.
BOM_FIELDS = (
    "Reference",
    "Value",
    "Footprint",
    "LCSC",
    "MPN",
    "Manufacturer",
    "Datasheet",
    "${DNP}",
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

    def source_paths(self, root: Path) -> list[Path]:
        """The source tgz's files and directories, from the repository root."""
        candidates = [
            *(self.directory / f"{self.name}{s}" for s in PROJECT_SUFFIXES),
            *(self.directory / f for f in PROJECT_FILES),
        ]
        return [*(c for c in candidates if (root / c).exists()), *self.sources]


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
    if type(tg) not in (int, float) or tg <= 0:
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
    options = table.get("profile")
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
    return out


def lfs_pointer(path: Path) -> bool:
    """Whether the file is a Git LFS pointer, by its contents alone."""
    if not 0 < path.stat().st_size < LFS_POINTER_SIZE:
        return False
    return path.read_bytes().split(b"\n", 1)[0] in LFS_VERSIONS


def lfs_pointers(root: Path, paths: Iterable[Path]) -> list[Path]:
    """The files among paths, from root, directories walked, that are Git
    LFS pointers whose files were never fetched; from root.  OSError if one
    cannot be read."""
    out = []
    for p in paths:
        top = root / p
        found = sorted(f for f in top.rglob("*") if f.is_file()) if top.is_dir() else [top]
        out += [f.relative_to(root) for f in found if lfs_pointer(f)]
    return out


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


def board_variants(pcb: Expression) -> list[str]:
    """The variants the board defines, its top-level (variants (variant (name
    "X")))."""
    section = child(pcb, "variants")
    return variant_names(section) if section is not None else []


def check_variants(
    listed: Sequence[str], board: Sequence[str], schematic: Sequence[str]
) -> tuple[list[str], list[str]]:
    """The errors and warnings of a board's variants: a listed variant the
    board lacks is an error (kicad-cli exports the default for a name it
    does not know); a project variant (the board's or the schematic's) not
    listed is a warning."""
    errors = []
    for name in listed:
        if name in board:
            continue
        like = [b for b in board if b.casefold() == name.casefold()]
        hint = f" (it has {like[0]!r})" if like else ""
        errors.append(f"variant {name!r} is not one of the board's{hint}")
    project = list(dict.fromkeys([*board, *schematic]))
    unlisted = [v for v in project if v.casefold() not in {n.casefold() for n in listed}]
    warnings = []
    if unlisted and not listed:
        warnings.append(
            f"the project has variants {', '.join(unlisted)}, and fab.toml lists none:"
            " the default variant is built"
        )
    elif unlisted:
        warnings.append(f"variants {', '.join(unlisted)} of the project are not built")
    return errors, warnings


def cross_checks(
    parts: Iterable[Part], fitted: Iterable[Placement], placed: Iterable[Placement]
) -> list[str]:
    """Where a variant's schematic (its BOM's parts) and board (its
    placements: those fitted, and all placed, DNP too) differ: a part DNP in
    one and fitted in the other, a different Value, or a different
    footprint (a variant's Footprint override)."""
    on_board = {p.reference: p for p in placed}
    fitted_refs = {p.reference for p in fitted}
    out = []
    for p in parts:
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
        if p.footprint.split(":", 1)[-1] != found.package:
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
    PRESENT = "already on this commit"
    CREATE = "create it"
    MOVE = "move it here (--retag)"
    REFUSE = "on another commit, and no --retag"


def tag_plan(dirty: bool, tagged: str | None, head: str, retag: bool) -> TagPlan:
    """The plan for a release's tag, given the commit it is on (tagged)."""
    if dirty:
        return TagPlan.DIRTY
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

    @property
    def layers(self) -> list[str]:
        """The layers in the Gerber zip."""
        return [*self.copper, *OTHER_LAYERS]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=self.root, capture_output=True, text=True, check=False)

    def kicad(self, *args: str) -> None:
        r = self.run("kicad-cli", *args)
        if r.returncode != 0:
            sys.exit(f"kicad-cli {' '.join(args)} failed:\n{r.stdout}{r.stderr}")

    def path(self, p: Path) -> str:
        return str(self.root / p)

    def drc_project(self, rules: Installed) -> Path:
        """A scratch copy of the board's project with the rules installed;
        return its .kicad_pcb.  Its directory's files are copied and its
        directories linked, at the board directory's own absolute path
        under the scratch directory, beside a link to every other entry of
        each of its ancestors: so a library path relative to the project
        (${KIPRJMOD}/../lib), which DRC checks footprints against, finds
        what it finds for the board.  The board's own files are not
        written."""
        b = self.board
        source = (self.root / b.directory).resolve()
        top = self.work / DRC_PROJECT
        scratch = top / source.relative_to(source.anchor)
        scratch.mkdir(parents=True)
        for entry in sorted(source.iterdir()):
            if entry.is_dir():
                (scratch / entry.name).symlink_to(entry, target_is_directory=True)
            else:
                shutil.copyfile(entry, scratch / entry.name)
        for here, parent in zip([source, *source.parents], source.parents, strict=False):
            mirror = top / parent.relative_to(parent.anchor)
            try:
                entries = sorted(parent.iterdir())
            except OSError as e:
                print(f"DRC's scratch copy: {parent} not listed: {e}", file=sys.stderr)
                continue
            for entry in entries:
                if entry.name != here.name:
                    (mirror / entry.name).symlink_to(entry)
        (scratch / f"{b.name}{DRU}").write_text(rules.dru)
        (scratch / b.project.name).write_text(json.dumps(rules.project, indent=2) + "\n")
        return scratch / b.pcb.name

    def check(self, allow_dirty: bool, rules: Installed, productions: Iterable[Path] = ()) -> str:
        """ERC, DRC with schematic parity and the rules (drc_project), and
        committed sources, less the production directories; return the
        commit."""
        b = self.board
        erc = self.work / "erc.rpt"
        if self.run(
            "kicad-cli", "sch", "erc", "--exit-code-violations", "-o", str(erc), self.path(b.sch)
        ).returncode:
            sys.exit(f"ERC is not clean:\n{report(erc)}")
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
        # Every board's production directory left out: a release's own
        # output, which may be in the board's directory, is not a source.
        paths = [
            str(b.directory),
            *(str(s) for s in b.sources),
            *(f":(exclude){p}" for p in productions),
        ]
        status = self.run("git", "status", "--porcelain", "--", *paths).stdout.strip()
        if status and not allow_dirty:
            sys.exit(f"uncommitted changes in the release's sources:\n{status}")
        commit = self.run("git", "rev-parse", "--short", "HEAD").stdout.strip()
        return commit + ("-dirty" if status else "")

    def tagged(self, name: str) -> str | None:
        """The full hash of the commit tag `name` is on, or None."""
        r = self.run("git", "rev-parse", "-q", "--verify", f"refs/tags/{name}^{{commit}}")
        return r.stdout.strip() if r.returncode == 0 else None

    def head(self) -> str:
        return self.run("git", "rev-parse", "HEAD").stdout.strip()

    def tag(self, name: str, message: str, move: bool) -> None:
        """An annotated tag on HEAD; move replaces one of that name."""
        r = self.run("git", "tag", "-a", *(["-f"] if move else []), name, "-m", message, "HEAD")
        if r.returncode != 0:
            sys.exit(f"git tag {name} failed:\n{r.stdout}{r.stderr}")

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
            settings = json.loads(project.read_text())
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
        self.kicad(
            "sch",
            "export",
            "bom",
            "--fields",
            ",".join(BOM_FIELDS),
            *variant_flag(variant),
            "-o",
            str(raw),
            self.path(self.board.sch),
        )
        return [
            Part(
                r["Reference"],
                r["Value"],
                r["Footprint"],
                r["LCSC"],
                dnp=bool(r["DNP"]),
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

    def source(self, out: Path, commit: str, variants: Sequence[str | None] = (None,)) -> None:
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
        with tarfile.open(out, "w:gz") as t:
            for s in b.source_paths(self.root):
                t.add(self.root / s, arcname=f"{top}/{s}")
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
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
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
        project = json.loads(project_path.read_text())
        dru = dru_path.read_text() if dru_path.exists() else None
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
            f"warning: {dru_path.name}: rules {', '.join(found.copied)} after ee_tooling's"
            " copy them and win over them: remove them",
            file=sys.stderr,
        )
    return found


def main() -> None:
    root = repository_root()
    try:
        boards = load_config((root / CONFIG).read_text())
    except (OSError, tomllib.TOMLDecodeError, ValueError) as e:
        sys.exit(f"{root / CONFIG}: {e}")
    # Made before anything is checked or written: loading a profile's data can fail.
    profiles = {p: PROFILES[p]() for p in dict.fromkeys(b.profile for b in boards.values())}
    try:
        options = profile_options(boards, profiles)
    except ValueError as e:
        sys.exit(f"{root / CONFIG}: {e}")

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("board", nargs="?", choices=boards, default=next(iter(boards)))
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--force", action="store_true", help="replace an existing release")
    ap.add_argument("--allow-dirty", action="store_true", help="allow uncommitted sources")
    ap.add_argument("--retag", action="store_true", help="move the release's tag to this commit")
    a = ap.parse_args()

    b = boards[a.board]
    missing = [str(s) for s in (b.pcb, b.sch, *b.sources) if not (root / s).exists()]
    if missing:
        sys.exit(f"{CONFIG} names files that are not there: {', '.join(missing)}")
    try:
        pointers = lfs_pointers(root, b.source_paths(root))
    except OSError as e:
        sys.exit(f"{e.filename}: {e.strerror}")
    if pointers:
        sys.exit(
            "Git LFS pointers among the release's inputs, their files never fetched"
            " (a clone without git-lfs):\n"
            + "".join(f"  {p}\n" for p in pointers)
            + "install git-lfs, then run git lfs install --local --skip-repo and git lfs pull"
        )
    pcb_text = (root / b.pcb).read_text()
    rev = revision(pcb_text)
    if rev is None:
        sys.exit(f"no revision in {b.pcb.name}'s title block")
    dest = root / b.production / f"{a.date}-{rev}"
    if dest.exists() and not a.force:
        sys.exit(f"{dest} exists; use --force to replace it")
    try:
        pcb = parse(pcb_text)
    except ParseError as e:
        sys.exit(f"{b.pcb}: {e}")
    found = stackup(pcb)
    if found is None:
        sys.exit(f"{b.pcb.name} has no stackup: set one in Board Setup, Physical Stackup")
    copper = copper_layers(found)
    if not copper:
        sys.exit(f"{b.pcb.name}'s stackup has no copper layer")
    if table := check_stackup(pcb).findings:
        sys.exit(
            "".join(f"{b.pcb.name}: {f.message}\n" for f in table)
            + f"write or refresh it with: stackup-table {b.pcb}"
        )
    try:
        schematic = [v for sheet in sheets((root / b.sch).resolve()) for v in variant_names(sheet)]
    except (OSError, ParseError) as e:
        sys.exit(f"{b.sch}: {e}")
    errors, warnings = check_variants(b.variants, board_variants(pcb), schematic)
    if errors:
        sys.exit("\n".join(f"{b.pcb.name}: {e}" for e in errors))
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    # Each build: a listed variant, or the default (None).
    builds: list[str | None] = [*b.variants] or [None]
    profile = profiles[b.profile]
    rules = board_rules(root, b, profile, options[b.name])
    with tempfile.TemporaryDirectory() as tmp:
        r = Release(root, b, Path(tmp), copper)
        commit = r.check(a.allow_dirty, rules, [x.production for x in boards.values()])
        tag = f"{b.name}-{dest.name}"
        tagged = r.tagged(tag)
        plan = tag_plan(commit.endswith("-dirty"), tagged, r.head(), a.retag)
        if plan is TagPlan.REFUSE:
            sys.exit(f"tag {tag} is on {(tagged or '')[:7]}, not {commit}; use --retag to move it")
        # Each build's parts and placements, checked.
        rows: dict[str | None, tuple[list[Part], list[Placement]]] = {}
        refusals = []
        for v in builds:
            parts, placements = r.parts(v), r.placements(v)
            found_problems = (
                [] if v is None else cross_checks(parts, placements, r.placements(v, True))
            )
            found_problems += profile.check(parts, placements, options[b.name])
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
                f"variants {', '.join(map(str, builds))}: the Gerbers differ in"
                f" {', '.join(layers)}: a Gerber zip per variant, each a bare-board order of"
                " its own",
                file=sys.stderr,
            )
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
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
            write_gerber_zip(dest / zips[v], directories[v], documents)
        (dest / map_name).write_text(map_text)
        (dest / stackup_name).write_text(stackup_text)
        (dest / f"{base}-{NOTES}").write_text(notes_text)
        for v in builds:
            r.odb(dest / f"{bases[v]}-odb.tgz", v)
            r.ipc2581(dest / f"{bases[v]}-ipc2581.zip", profile.distributor, v)
        for v in builds:
            parts, placements = rows[v]
            write_csv(dest / f"{bases[v]}-{BOM}", HOUSE_BOM_HEADER, house_bom(parts))
            profile.assembly(dest, bases[v], parts, placements, options[b.name])
        r.source(dest / f"{base}-source.tgz", commit, builds)
        if plan in (TagPlan.CREATE, TagPlan.MOVE):
            message = f"{b.name} {rev}: fab release {b.production / dest.name}"
            if b.variants:
                message += f"; variants {', '.join(b.variants)}"
            r.tag(tag, message, plan is TagPlan.MOVE)
            force = " --force" if plan is TagPlan.MOVE else ""
            print(f"tagged {tag}; push it with: git push{force} origin refs/tags/{tag}")
        else:
            print(f"tag {tag}: {plan.value}")
    print(f"{dest.relative_to(root)} from {commit}:")
    for f in sorted(dest.iterdir()):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
