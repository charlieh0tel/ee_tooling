"""The libraries a KiCad project reads, by the paths its library tables
and its board's 3D models name (docs/kicad-style.md, Files: a board's
libraries live inside its repository).

A path is resolved as KiCad 10.0.8 does: ${KIPRJMOD} is the project's
directory, and a relative path is from it.  A path under one of KiCad's
own install variables names its stock libraries, which are not the
repository's; any other variable is the user's, unknown here.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ee_tooling.sexpr import Expression, ParseError, child, children, parse

# The project's library tables, beside its .kicad_pro.
TABLES = ("fp-lib-table", "sym-lib-table")
# A ${NAME} variable in a path.
VARIABLE = re.compile(r"\$\{([^}]*)\}")
PROJECT = "KIPRJMOD"
# KiCad's own install variables, for its stock libraries: versioned
# (KICAD10_3DMODEL_DIR, KICAD6_FOOTPRINT_DIR, ...) and the legacy names.
STOCK = re.compile(r"KICAD\d*_(FOOTPRINT|SYMBOL|3DMODEL|TEMPLATE)_DIR|KISYS3DMOD|KISYSMOD")
# A 3D model embedded in the board file, not a path.
EMBEDDED = "kicad-embed://"


@dataclass(frozen=True)
class Libraries:
    """What a project's library paths resolve to."""

    inside: list[Path]  # each inside the repository, from its root, once
    outside: list[str]  # each path as written that leads outside it or is unknown
    stock: list[str]  # each path as written under a KiCad install variable


def table_uris(text: str) -> list[str]:
    """A library table's (lib (uri "...")) paths; ParseError if it is not
    one."""
    return [
        str(uri[1])
        for lib in children(parse(text), "lib")
        if (uri := child(lib, "uri")) is not None and len(uri) > 1
    ]


def model_paths(pcb: Expression) -> list[str]:
    """The board's footprints' 3D model paths, but embedded ones."""
    return [
        str(model[1])
        for footprint in children(pcb, "footprint")
        for model in children(footprint, "model")
        if len(model) > 1 and not str(model[1]).startswith(EMBEDDED)
    ]


def libraries(root: Path, directory: Path, pcb: Expression) -> Libraries:
    """The paths the project in directory (from root) reads libraries
    by: its library tables' and its board's 3D models'.  ValueError
    naming a table that does not parse."""
    written: list[str] = []
    for name in TABLES:
        table = root / directory / name
        if table.exists():
            try:
                written += table_uris(table.read_text())
            except ParseError as e:
                raise ValueError(f"{directory / name}: {e}") from e
    written += model_paths(pcb)
    top = Path(os.path.normpath(root.absolute()))
    project = top / directory
    inside: dict[Path, None] = {}
    outside: dict[str, None] = {}
    stock: dict[str, None] = {}
    for path in written:
        names = VARIABLE.findall(path)
        if any(STOCK.fullmatch(n) for n in names):
            stock[path] = None
            continue
        if any(n != PROJECT for n in names):
            outside[path] = None
            continue
        resolved = Path(os.path.normpath(project / path.replace(f"${{{PROJECT}}}", str(project))))
        if resolved.is_relative_to(top):
            inside[resolved.relative_to(top)] = None
        else:
            outside[path] = None
    return Libraries(list(inside), list(outside), list(stock))
