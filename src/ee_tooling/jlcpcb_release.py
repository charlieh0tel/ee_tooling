"""Build a JLCPCB release package for a KiCad board.

Reads jlcpcb.toml at the root of the git repository it is run in, and writes
the board's production directory, YYYYMMDD-REV/, with:
  NAME-REV-jlcpcb.zip   Gerbers and drill files, for the PCB order, and
                        README.txt, the Gerber map
  NAME-REV-gerber-map.txt  what each file in the Gerber zip is
  NAME-REV-stackup.txt  the board's stackup, each layer's Gerber, and its
                        impedance requirements
  NAME-REV-fab-notes.txt  fabrication notes, filled from the board where it
                        says, [TBD: ...] where it cannot
  NAME-REV-odb.tgz      ODB++ job, for the record and ODB++ tools
  NAME-REV-bom.csv      BOM in JLCPCB's columns, for assembly
  NAME-REV-cpl.csv      placement in JLCPCB's columns, for assembly
  NAME-REV-source.tgz   the design files, PDFs and docs it was made from

NAME is the KiCad project, REV its board's title-block revision.  ERC and
DRC must be clean and the release's sources committed, unless --allow-dirty.
"""

import argparse
import csv
import datetime
import importlib.resources
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ee_tooling.fabrication import (
    GerberFile,
    board_thickness,
    gerber_files,
    gerber_map,
    impedances,
    minimums,
    notes,
    stackup,
    stackup_document,
)
from ee_tooling.sexpr import Expression, ParseError, parse

CONFIG = "jlcpcb.toml"
# The Gerber map's name in the Gerber zip.
GERBER_README = "README.txt"
# The work directory's subdirectory kicad-cli writes the Gerbers and drill into.
GERBERS = "gerbers"
# The fabrication documents' names after NAME-REV-.
GERBER_MAP = "gerber-map.txt"
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

# A KiCad project's design files and libraries, by suffix to its name, then
# by name; those present go in the source tgz.
PROJECT_SUFFIXES = (".kicad_pcb", ".kicad_sch", ".kicad_pro", ".kicad_dru", ".pretty", ".3dshapes")
PROJECT_FILES = ("fp-lib-table", "sym-lib-table")

BOM_HEADER = ("Comment", "Designator", "Footprint", "LCSC Part #")
CPL_HEADER = ("Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer")

# ODB++ export: mm, as the Gerbers and drill; 6 decimals, as kicad-cli's
# default 4.6 Gerbers, is KiCad's 1 nm unit, so nothing is rounded; a tgz of one
# odb/ job directory, ODB++'s usual archive and the one tools/odb_current reads.
ODB_UNITS = "mm"
ODB_PRECISION = 6
ODB_COMPRESSION = "tgz"


@dataclass(frozen=True)
class Parts:
    """JLCPCB's corrections to KiCad's placement, by LCSC part (parts.toml)."""

    rotation: dict[str, float]  # degrees added to KiCad's rotation
    offset: dict[str, tuple[float, float]]  # mm added, in the part's frame (rotation 0, y up)
    unchecked: dict[str, str]  # part description, for parts with no correction known yet

    @classmethod
    def load(cls, text: str) -> "Parts":
        t = tomllib.loads(text)
        return cls(
            rotation={k: float(v) for k, v in t.get("rotation", {}).items()},
            offset={k: (float(v[0]), float(v[1])) for k, v in t.get("offset", {}).items()},
            unchecked=dict(t.get("unchecked", {})),
        )

    @classmethod
    def builtin(cls) -> "Parts":
        return cls.load(importlib.resources.files(__package__).joinpath("parts.toml").read_text())


@dataclass(frozen=True)
class Board:
    """A board that can be released (a [[board]] in jlcpcb.toml)."""

    name: str  # the KiCad project
    directory: Path  # holds NAME.kicad_pcb and NAME.kicad_sch, from the repository root
    copper: tuple[str, ...]  # copper layers, top to bottom
    production: Path  # where its YYYYMMDD-REV/ releases go, from the repository root
    sources: tuple[Path, ...]  # in the source tgz after the project's files, from the root

    @property
    def pcb(self) -> Path:
        return self.directory / f"{self.name}.kicad_pcb"

    @property
    def sch(self) -> Path:
        return self.directory / f"{self.name}.kicad_sch"

    @property
    def project(self) -> Path:
        return self.directory / f"{self.name}.kicad_pro"

    @property
    def layers(self) -> list[str]:
        return [*self.copper, *OTHER_LAYERS]

    def source_paths(self, root: Path) -> list[Path]:
        """The source tgz's files and directories, from the repository root."""
        candidates = [
            *(self.directory / f"{self.name}{s}" for s in PROJECT_SUFFIXES),
            *(self.directory / f for f in PROJECT_FILES),
        ]
        return [*(c for c in candidates if (root / c).exists()), *self.sources]


def load_config(text: str) -> dict[str, Board]:
    """jlcpcb.toml's boards, by name, the first being the default."""
    boards: list[dict[str, Any]] = tomllib.loads(text).get("board", [])
    if not boards:
        raise ValueError(f"{CONFIG} has no [[board]]")
    return {
        b["name"]: Board(
            name=b["name"],
            directory=Path(b["directory"]),
            copper=tuple(b["copper"]),
            production=Path(b["production"]),
            sources=tuple(Path(s) for s in b.get("sources", ())),
        )
        for b in boards
    }


def revision(pcb_text: str) -> str | None:
    m = re.search(r'\(title_block\s.*?\(rev "([^"]+)"\)', pcb_text, re.S)
    return m.group(1) if m else None


def jlcpcb_bom(rows: Iterable[dict[str, str]]) -> tuple[list[list[str]], dict[str, str], list[str]]:
    """KiCad's grouped BOM in JLCPCB's columns.

    Returns the rows, each designator's LCSC part, and the designators of
    rows with no LCSC part.
    """
    out = []
    refs: dict[str, str] = {}
    missing = []
    for r in rows:
        lcsc = r["LCSC Part #"]
        out.append([r["Comment"], r["Designator"], r["Footprint"].split(":", 1)[-1], lcsc])
        for x in r["Designator"].split(","):
            refs[x.strip()] = lcsc
        if not lcsc:
            missing.append(r["Designator"])
    return out, refs, missing


def jlcpcb_cpl(
    rows: Iterable[dict[str, str]], refs: dict[str, str], parts: Parts
) -> tuple[list[list[str]], set[str]]:
    """KiCad's placement in JLCPCB's columns, for the parts in refs.

    Returns the rows and the designators placed.
    """
    out = []
    placed = set()
    for r in rows:
        ref = r["Ref"]
        if ref not in refs:
            continue
        lcsc = refs[ref]
        kicad_rot = float(r["Rot"])
        ox, oy = parts.offset.get(lcsc, (0.0, 0.0))
        t = math.radians(kicad_rot)
        x = float(r["PosX"]) + ox * math.cos(t) - oy * math.sin(t)
        y = float(r["PosY"]) + ox * math.sin(t) + oy * math.cos(t)
        rot = (kicad_rot + parts.rotation.get(lcsc, 0.0)) % 360
        layer = "Top" if r["Side"].lower() == "top" else "Bottom"
        out.append([ref, r["Val"], r["Package"], f"{x:.4f}mm", f"{y:.4f}mm", f"{rot:g}", layer])
        placed.add(ref)
    return out, placed


def write_csv(out: Path, header: Iterable[str], rows: Iterable[list[str]]) -> None:
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


class Release:
    """One board's release, built in a scratch directory."""

    def __init__(self, root: Path, board: Board, work: Path) -> None:
        self.root = root
        self.board = board
        self.work = work

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

    def gerbers(self, out: Path, title: str) -> list[GerberFile]:
        """The Gerber zip, with the Gerber map titled title; return its files."""
        b = self.board
        g = self.work / GERBERS
        g.mkdir()
        self.kicad(
            "pcb",
            "export",
            "gerbers",
            "--layers",
            ",".join(b.layers),
            "--no-x2",
            "--subtract-soldermask",
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
            "--excellon-separate-th",
            "--drill-origin",
            "absolute",
            "-o",
            f"{g}/",
            self.path(b.pcb),
        )
        try:
            files = gerber_files(g, b.name, b.layers)
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
        if found is None:
            print(f"warning: {self.board.pcb.name} has no stackup; see {stackup_name}")
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
            )
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

    def bom(self, out: Path) -> dict[str, str]:
        """JLCPCB BOM, without not-fitted parts; return its designators' LCSC parts."""
        raw = self.work / "bom.csv"
        self.kicad(
            "sch",
            "export",
            "bom",
            "--exclude-dnp",
            "--fields",
            "Value,Reference,Footprint,LCSC,${QUANTITY}",
            "--labels",
            "Comment,Designator,Footprint,LCSC Part #,Qty",
            "--group-by",
            "Value,Footprint,LCSC",
            "--ref-range-delimiter",
            "",
            "-o",
            str(raw),
            self.path(self.board.sch),
        )
        rows, refs, missing = jlcpcb_bom(csv.DictReader(io.StringIO(raw.read_text())))
        write_csv(out, BOM_HEADER, rows)
        if missing:
            print("warning: no LCSC part number for", ", ".join(missing))
        return refs

    def cpl(self, out: Path, refs: dict[str, str], parts: Parts) -> None:
        """JLCPCB placement for the parts in the BOM."""
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
        rows, placed = jlcpcb_cpl(csv.DictReader(io.StringIO(raw.read_text())), refs, parts)
        write_csv(out, CPL_HEADER, rows)
        for lcsc, part in parts.unchecked.items():
            if lcsc in refs.values():
                print(f"check {part} ({lcsc}) in JLCPCB's placement preview: no correction known")
        if unplaced := set(refs) - placed:
            sys.exit(f"in the BOM but not placed: {', '.join(sorted(unplaced))}")

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
            ",".join([*b.copper, "F.Silkscreen", "B.Silkscreen", "F.Fab", "B.Fab"]),
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


def main() -> None:
    root = repository_root()
    try:
        boards = load_config((root / CONFIG).read_text())
    except (OSError, tomllib.TOMLDecodeError, KeyError, ValueError) as e:
        sys.exit(f"{root / CONFIG}: {e!r}")

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("board", nargs="?", choices=boards, default=next(iter(boards)))
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--force", action="store_true", help="replace an existing release")
    ap.add_argument("--allow-dirty", action="store_true", help="allow uncommitted sources")
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
    if dest.exists():
        if not a.force:
            sys.exit(f"{dest} exists; use --force to replace it")
        shutil.rmtree(dest)
    try:
        pcb = parse(pcb_text)
    except ParseError as e:
        sys.exit(f"{b.pcb}: {e}")
    parts = Parts.builtin()
    with tempfile.TemporaryDirectory() as tmp:
        r = Release(root, b, Path(tmp))
        commit = r.check(a.allow_dirty)
        dest.mkdir(parents=True)
        base = f"{b.name}-{rev}"
        label = f"{b.name} {rev}"
        gerbers = f"{base}-jlcpcb.zip"
        title = f"{label}: the files in {gerbers}"
        files = r.gerbers(dest / gerbers, title)
        (dest / f"{base}-{GERBER_MAP}").write_text(gerber_map(title, files))
        r.fabrication(dest, base, label, pcb, files)
        r.odb(dest / f"{base}-odb.tgz")
        refs = r.bom(dest / f"{base}-bom.csv")
        r.cpl(dest / f"{base}-cpl.csv", refs, parts)
        r.source(dest / f"{base}-source.tgz", commit)
    print(f"{dest.relative_to(root)} from {commit}:")
    for f in sorted(dest.iterdir()):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
