"""What a release asks of a fab's profile, and the rows it gives it:
internal, not public API."""

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class GerberOptions:
    """kicad-cli's Gerber and drill export, as a fab reads it.  The Gerber
    map and the fabrication notes take JLCPCB's values (separate drill
    files, no X2)."""

    x2: bool  # X2 attributes
    protel_extensions: bool  # .gtl ..., or KiCad's .gbr
    subtract_soldermask: bool  # soldermask openings cut from the silkscreen
    separate_drill: bool  # PTH and NPTH in separate files


@dataclass(frozen=True)
class Rules:
    """A profile's KiCad rules, files in the ee_tooling package by their
    paths there."""

    dru: str  # its .kicad_dru, in sections headed "# --- <name> ---"
    optional: dict[str, str]  # an option of its board options to the section left out when false
    board_setup: str  # its Board Setup minimums, a .kicad_pro fragment (JSON)


# kicad-cli's own Gerber and drill options: a profile with them writes the
# neutral NAME-REV-gerbers.zip, one with others NAME-REV-gerbers-<name>.zip.
KICAD_GERBERS = GerberOptions(
    x2=True, protel_extensions=False, subtract_soldermask=False, separate_drill=False
)


@dataclass(frozen=True)
class Part:
    """A part, one per reference, from the schematic's BOM: those fitted
    and those not (DNP); not those excluded from the BOM.  A field the
    part lacks is ""."""

    reference: str
    value: str
    footprint: str  # LIBRARY:NAME
    lcsc: str  # its LCSC field
    dnp: bool = False  # do not populate
    mpn: str = ""
    manufacturer: str = ""
    datasheet: str = ""


@dataclass(frozen=True)
class Placement:
    """A fitted footprint's place on the board, from KiCad's position file."""

    reference: str
    value: str
    package: str  # the footprint's name
    x: float  # mm, KiCad's absolute coordinates (y negative)
    y: float
    rotation: float  # degrees
    side: str  # KiCad's: top or bottom


class Profile[Options](Protocol):
    """What a release shapes for one fab; Options, its board options."""

    name: str  # fab.toml's profile value; names its files
    gerbers: GerberOptions  # its Gerber and drill export
    rules: Rules  # its DRC rules and Board Setup minimums
    distributor: tuple[str, str] | None  # IPC-2581: the part-number field, the distributor
    fab_defaults: dict[str, Any]  # the [board.fab] values adopt writes for a new board

    def options(self, table: dict[str, Any]) -> Options:
        """A board's [board.<name>] table, checked; ValueError naming a bad
        key."""
        ...

    def check(self, parts: list[Part], placements: list[Placement], options: Options) -> list[str]:
        """Its refusals of the release, beyond the core's; none, to go on."""
        ...

    def warnings(
        self, parts: list[Part], placements: list[Placement], options: Options
    ) -> list[str]:
        """What the release says on stderr of the parts, going on."""
        ...

    def outputs(self, base: str) -> list[str]:
        """The names of the files assembly writes, NAME-REV being base."""
        ...

    def assembly(
        self,
        dest: Path,
        base: str,
        parts: list[Part],
        placements: list[Placement],
        options: Options,
    ) -> list[Path]:
        """Writes its BOM and placement of the parts into dest, NAME-REV
        being base; returns them."""
        ...


def write_csv(out: Path, header: Iterable[str], rows: Iterable[list[str]]) -> None:
    """A CSV file, as Python's csv module writes it (CRLF line ends)."""
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
