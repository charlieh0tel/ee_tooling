"""Build a board's release package for its fab.

Reads fab.toml at the root of the git repository it is run in, and writes
the board's production directory, YYYYMMDD-REV/, with:
  NAME-REV-gerbers-jlcpcb.zip  Gerbers and drill files, for the PCB order,
                        and README.txt, the Gerber map (NAME-REV-gerbers.zip
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

NAME is the KiCad project, REV its board's title-block revision; the
board's profile (profiles/) names the fab whose files these are.  ERC and
DRC must be clean and the release's sources committed, unless --allow-dirty.
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
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any

from ee_tooling.fabrication import (
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
from ee_tooling.profiles import PROFILES
from ee_tooling.profiles.profile import (
    KICAD_GERBERS,
    GerberOptions,
    Part,
    Placement,
    Profile,
    write_csv,
)
from ee_tooling.sexpr import Expression, ParseError, parse

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
LIST_KEYS: dict[str, list[str] | None] = {"sources": []}
# A [[board]]'s table of what its fab is to build to, every key required.
FAB = "fab"
# Keys fab.toml no longer has, with why: present, each is an error.
GONE_KEYS = {"copper": "the copper layers come from the board's stackup"}
# The Gerber map's name in the Gerber zip.
GERBER_README = "README.txt"
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
)

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


def gerber_zip(base: str, profile: Profile[Any]) -> str:
    """The Gerber zip's name: the profile's when its Gerber options are not
    kicad-cli's own."""
    suffix = "" if profile.gerbers == KICAD_GERBERS else f"-{profile.name}"
    return f"{base}-gerbers{suffix}.zip"


def revision(pcb_text: str) -> str | None:
    m = re.search(r'\(title_block\s.*?\(rev "([^"]+)"\)', pcb_text, re.S)
    return m.group(1) if m else None


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

    def check(self, allow_dirty: bool) -> str:
        """ERC, DRC with schematic parity, and committed sources; return the commit."""
        b = self.board
        erc = self.work / "erc.rpt"
        if self.run(
            "kicad-cli", "sch", "erc", "--exit-code-violations", "-o", str(erc), self.path(b.sch)
        ).returncode:
            sys.exit(f"ERC is not clean: see {erc}")
        drc = self.work / "drc.rpt"
        if self.run(
            "kicad-cli",
            "pcb",
            "drc",
            "--schematic-parity",
            "--exit-code-violations",
            "-o",
            str(drc),
            self.path(b.pcb),
        ).returncode:
            sys.exit(f"DRC is not clean: see {drc}")
        paths = [str(b.directory), *(str(s) for s in b.sources)]
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

    def gerbers(self, out: Path, title: str, options: GerberOptions) -> list[GerberFile]:
        """The Gerber zip, with the Gerber map titled title; return its files."""
        b = self.board
        g = self.work / GERBERS
        g.mkdir()
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
            "-o",
            str(g),
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
            f"{g}/",
            self.path(b.pcb),
        )
        try:
            files = gerber_files(g, b.name, self.layers)
            text = gerber_map(title, files)
        except (ValueError, KeyError, IndexError) as e:
            sys.exit(f"no Gerber map: {e}")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(g.iterdir()):
                z.write(f, f.name)
            z.writestr(GERBER_README, text)
        return files

    def fabrication(
        self, dest: Path, base: str, label: str, pcb: Expression, files: list[GerberFile]
    ) -> None:
        """The stackup and the fabrication notes, NAME-REV being base and
        "NAME REV" label, from the board, its project's net classes and the
        Gerber directory."""
        project = self.root / self.board.project
        try:
            settings = json.loads(project.read_text())
        except (OSError, ValueError) as e:
            sys.exit(f"{project}: {e}")
        found = stackup(pcb)
        thickness = board_thickness(pcb)
        impedance = impedances(settings)
        stackup_name = f"{base}-{STACKUP}"
        (dest / stackup_name).write_text(
            stackup_document(f"{label}: stackup", found, thickness, files, impedance)
        )
        (dest / f"{base}-{NOTES}").write_text(
            notes(
                f"{label}: fabrication notes",
                found,
                thickness,
                files,
                impedance,
                minimums(self.work / GERBERS),
                stackup_name,
                f"{base}-{GERBER_MAP}",
                self.board.fab,
            )
        )

    def ipc2581(self, out: Path, distributor: tuple[str, str] | None) -> None:
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
            "-o",
            str(out),
            self.path(self.board.pcb),
        )

    def odb(self, out: Path) -> None:
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
            "-o",
            str(out),
            self.path(self.board.pcb),
        )

    def parts(self) -> list[Part]:
        """The schematic's parts, DNP too, in KiCad's order of reference."""
        raw = self.work / "bom.csv"
        self.kicad(
            "sch",
            "export",
            "bom",
            "--fields",
            ",".join(BOM_FIELDS),
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

    def placements(self) -> list[Placement]:
        """The board's fitted footprints, both sides, in KiCad's order."""
        raw = self.work / "pos.csv"
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
            "--exclude-dnp",
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

    def source(self, out: Path, commit: str) -> None:
        b = self.board
        pdf = self.work / "pdf"
        pdf.mkdir()
        self.kicad(
            "sch", "export", "pdf", "-o", str(pdf / f"{b.name}-schematic.pdf"), self.path(b.sch)
        )
        self.kicad(
            "pcb",
            "export",
            "pdf",
            "--mode-multipage",
            "--layers",
            ",".join([*self.copper, "F.Silkscreen", "B.Silkscreen", "F.Fab", "B.Fab"]),
            "--common-layers",
            "Edge.Cuts",
            "-o",
            str(pdf / f"{b.name}-board.pdf"),
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
    profile = profiles[b.profile]
    with tempfile.TemporaryDirectory() as tmp:
        r = Release(root, b, Path(tmp), copper)
        commit = r.check(a.allow_dirty)
        tag = f"{b.name}-{dest.name}"
        tagged = r.tagged(tag)
        plan = tag_plan(commit.endswith("-dirty"), tagged, r.head(), a.retag)
        if plan is TagPlan.REFUSE:
            sys.exit(f"tag {tag} is on {(tagged or '')[:7]}, not {commit}; use --retag to move it")
        parts, placements = r.parts(), r.placements()
        if refusals := profile.check(parts, placements, options[b.name]):
            sys.exit("\n".join(refusals))
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        base = f"{b.name}-{rev}"
        label = f"{b.name} {rev}"
        gerbers = gerber_zip(base, profile)
        title = f"{label}: the files in {gerbers}"
        files = r.gerbers(dest / gerbers, title, profile.gerbers)
        (dest / f"{base}-{GERBER_MAP}").write_text(gerber_map(title, files))
        r.fabrication(dest, base, label, pcb, files)
        r.odb(dest / f"{base}-odb.tgz")
        r.ipc2581(dest / f"{base}-ipc2581.zip", profile.distributor)
        write_csv(dest / f"{base}-{BOM}", HOUSE_BOM_HEADER, house_bom(parts))
        profile.assembly(dest, base, parts, placements, options[b.name])
        r.source(dest / f"{base}-source.tgz", commit)
        if plan in (TagPlan.CREATE, TagPlan.MOVE):
            message = f"{b.name} {rev}: JLCPCB release {b.production / dest.name}"
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
