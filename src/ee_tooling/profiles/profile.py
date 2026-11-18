"""What a release asks of a fab's profile, and the rows it gives it:
internal, not public API."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
class Part:
    """A fitted part, one per reference, from the schematic's BOM."""

    reference: str
    value: str
    footprint: str  # LIBRARY:NAME
    lcsc: str  # its LCSC field; "" if none


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


class Profile(Protocol):
    """What a release shapes for one fab."""

    name: str  # names its files
    gerbers: GerberOptions  # its Gerber and drill export
    distributor: tuple[str, str] | None  # IPC-2581: the part-number field, the distributor

    def assembly(
        self, dest: Path, base: str, parts: list[Part], placements: list[Placement]
    ) -> list[Path]:
        """Writes its BOM and placement of the parts into dest, NAME-REV
        being base; returns them."""
        ...
