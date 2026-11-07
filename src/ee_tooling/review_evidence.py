"""The evidence a design review starts from, exported with kicad-cli 10
and poppler from a KiCad project into one directory:

erc.json, netlist.net, bom.csv, sch/NAME.pdf, sch/*.svg, pcb/*.svg (a
copper or mask layer each, with Edge.Cuts), pcb/positions.csv,
pcb/drill/, drc.json, drc-parity.json, png/render-top.png,
png/render-bottom.png, png/LAYERS.png (per-layer PDF rasterized), then
nets.txt, components.txt, board.md and erc.md from the review modules.
"""

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ee_tooling.review_board import copper_layers
from ee_tooling.review_board import summary as board_summary
from ee_tooling.review_erc import summary as erc_summary
from ee_tooling.review_netlist import condense
from ee_tooling.sexpr import parse

# Layers plotted one to a file, each with Edge.Cuts, after the board's copper layers.
PLOT_LAYERS = ("F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "F.Paste", "B.Paste", "F.Fab", "B.Fab")
# Layer groups rasterized for the layout reviewers, after each copper layer alone.
PNG_GROUPS = (
    ("F.SilkS", "F.Fab"),
    ("B.SilkS", "B.Fab"),
    ("F.Cu", "F.SilkS"),
    ("F.Mask", "F.Paste"),
    ("B.Mask", "B.Paste"),
)
# The board outline, plotted with every layer.
EDGE = "Edge.Cuts"
# The 3D renders' width and height, pixels.
RENDER_SIZE = ("3600", "2000")
# The layer PNGs' resolution, dots per inch.
PNG_DPI = "220"
# The BOM's columns, and the fields its rows are grouped by.
BOM_FIELDS = "Reference,Value,Footprint,MPN,Manufacturer,${QUANTITY},DNP"
BOM_GROUP = "Value,Footprint,MPN"
# kicad-cli pcb export svg --page-size-mode: 2, the board's area only.
SVG_BOARD_AREA = "2"
# kicad-cli pcb export pdf --scale: 0, fit to the page.
PDF_FIT = "0"
# The layer PDFs' background, white for rasterizing.
PDF_BACKGROUND = "#ffffff"
# Seconds one export may take: a high-quality render of a large board
# takes a minute or two.
EXPORT_TIMEOUT = 600
# The exports the summaries read, and the summaries: removed before a
# run, so a failed export leaves no summary of an earlier run's file.
SUMMARIZED = ("erc.json", "netlist.net", "erc.md", "nets.txt", "components.txt", "board.md")


@dataclass(frozen=True)
class Project:
    """A KiCad project: NAME.kicad_sch and NAME.kicad_pcb in directory."""

    directory: Path
    name: str

    @property
    def schematic(self) -> Path:
        return self.directory / f"{self.name}.kicad_sch"

    @property
    def board(self) -> Path:
        return self.directory / f"{self.name}.kicad_pcb"


def commands(project: Project, out: Path, copper: list[str]) -> list[list[str]]:
    """The export commands, in order; copper is the board's copper layers."""
    sch, pcb = str(project.schematic), str(project.board)
    found = [
        [
            "kicad-cli",
            "sch",
            "erc",
            "--format",
            "json",
            "--severity-all",
            "-o",
            str(out / "erc.json"),
            sch,
        ],
        [
            "kicad-cli",
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "-o",
            str(out / "netlist.net"),
            sch,
        ],
        ["kicad-cli", "sch", "export", "pdf", "-o", str(out / "sch" / f"{project.name}.pdf"), sch],
        ["kicad-cli", "sch", "export", "svg", "-o", str(out / "sch"), sch],
        [
            "kicad-cli",
            "sch",
            "export",
            "bom",
            "--fields",
            BOM_FIELDS,
            "--group-by",
            BOM_GROUP,
            "-o",
            str(out / "bom.csv"),
            sch,
        ],
        [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "-o",
            str(out / "drc.json"),
            pcb,
        ],
        [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--schematic-parity",
            "-o",
            str(out / "drc-parity.json"),
            pcb,
        ],
        [
            "kicad-cli",
            "pcb",
            "export",
            "pos",
            "--format",
            "csv",
            "--units",
            "mm",
            "--side",
            "both",
            "-o",
            str(out / "pcb" / "positions.csv"),
            pcb,
        ],
        [
            "kicad-cli",
            "pcb",
            "export",
            "drill",
            "--format",
            "excellon",
            "--generate-map",
            "--map-format",
            "pdf",
            "-o",
            str(out / "pcb" / "drill") + "/",
            pcb,
        ],
    ]
    found += [
        [
            "kicad-cli",
            "pcb",
            "export",
            "svg",
            "--layers",
            f"{layer},{EDGE}",
            "--page-size-mode",
            SVG_BOARD_AREA,
            "--exclude-drawing-sheet",
            "-o",
            str(out / "pcb" / f"{layer}.svg"),
            pcb,
        ]
        for layer in (*copper, *PLOT_LAYERS)
    ]
    for side in ("top", "bottom"):
        found.append(
            [
                "kicad-cli",
                "pcb",
                "render",
                "--side",
                side,
                "-w",
                RENDER_SIZE[0],
                "-h",
                RENDER_SIZE[1],
                "--quality",
                "high",
                "--background",
                "opaque",
                "-o",
                str(out / "png" / f"render-{side}.png"),
                pcb,
            ]
        )
    for group in (*((layer,) for layer in copper), *PNG_GROUPS):
        stem = "_".join(group)
        pdf = out / "png" / f"{stem}.pdf"
        found.append(
            [
                "kicad-cli",
                "pcb",
                "export",
                "pdf",
                "-l",
                ",".join((*group, EDGE)),
                "--mode-single",
                "--bg-color",
                PDF_BACKGROUND,
                "--scale",
                PDF_FIT,
                "-o",
                str(pdf),
                pcb,
            ]
        )
        found.append(
            ["pdftoppm", "-r", PNG_DPI, "-png", "-singlefile", str(pdf), str(out / "png" / stem)]
        )
    return found


def export(project: Project, out: Path, dry_run: bool = False) -> list[str]:
    """Runs the commands; the ones that failed, on stderr and returned.
    A failed export, or a tool not on the path, does not stop the rest;
    a summary without its source is skipped, on stderr, with no file
    left from an earlier run in its place.  A board that cannot be read
    (ValueError covers ParseError and BoardError) loses its copper
    layers' exports and board.md, which is returned as failed."""
    try:
        board = parse(project.board.read_text(encoding="utf-8"))
        copper = [name for name, _ in copper_layers(board)]
    except (OSError, ValueError) as e:
        print(
            f"skipped: the copper layers' exports, unreadable {project.board}: {e}", file=sys.stderr
        )
        copper = []
    if dry_run:
        print("\n".join(shlex.join(cmd) for cmd in commands(project, out, copper)))
        return []
    for sub in ("sch", "pcb", "png"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    for stale in SUMMARIZED:
        (out / stale).unlink(missing_ok=True)
    failed = []
    for cmd in commands(project, out, copper):
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=EXPORT_TIMEOUT)
        except FileNotFoundError:
            print(f"failed: {cmd[0]} not on the path: {shlex.join(cmd)}", file=sys.stderr)
            failed.append(shlex.join(cmd))
            continue
        except subprocess.TimeoutExpired:
            print(f"failed: over {EXPORT_TIMEOUT} s: {shlex.join(cmd)}", file=sys.stderr)
            failed.append(shlex.join(cmd))
            continue
        if done.returncode != 0:
            print(f"failed: {shlex.join(cmd)}\n{done.stderr.strip()}", file=sys.stderr)
            failed.append(shlex.join(cmd))
    netlist, erc = out / "netlist.net", out / "erc.json"
    if netlist.exists():
        condense(netlist, out)
    else:
        print(f"skipped: nets.txt and components.txt, without {netlist}", file=sys.stderr)
    try:
        (out / "board.md").write_text(board_summary(project.board), encoding="utf-8")
    except (OSError, ValueError) as e:
        print(f"failed: board.md, from {project.board}: {e}", file=sys.stderr)
        failed.append(f"board.md: {e}")
    if erc.exists():
        (out / "erc.md").write_text(erc_summary(erc), encoding="utf-8")
    else:
        print(f"skipped: erc.md, without {erc}", file=sys.stderr)
    return failed
