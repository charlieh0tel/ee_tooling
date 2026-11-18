"""The fabs a board can be released for, one module each, and what the
release asks of each: internal, not public API."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ee_tooling.fab_release import Release


@dataclass(frozen=True)
class GerberOptions:
    """kicad-cli's Gerber and drill export, as a fab reads it.  The Gerber
    map and the fabrication notes take JLCPCB's values (separate drill
    files, no X2)."""

    x2: bool  # X2 attributes
    protel_extensions: bool  # .gtl ..., or KiCad's .gbr
    subtract_soldermask: bool  # soldermask openings cut from the silkscreen
    separate_drill: bool  # PTH and NPTH in separate files


class Profile(Protocol):
    """What a release shapes for one fab."""

    name: str  # names its files
    gerbers: GerberOptions  # its Gerber and drill export
    distributor: tuple[str, str] | None  # IPC-2581: the part-number field, the distributor

    def assembly(self, release: "Release", dest: Path, base: str) -> list[Path]:
        """Writes its BOM and placement into dest, NAME-REV being base;
        returns them."""
        ...
