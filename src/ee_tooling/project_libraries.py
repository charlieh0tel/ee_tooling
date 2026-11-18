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
    unresolved: list[str]  # a relative 3D model path found nowhere: its footprint and path


@dataclass(frozen=True)
class Model:
    """A footprint's 3D model path in the board."""

    footprint: str  # its LIBRARY:NAME
    reference: str
    path: str  # as written


def table_uris(text: str) -> list[str]:
    """A library table's (lib (uri "...")) paths; ParseError if it is not
    one."""
    return list(table_libraries(text).values())


def table_libraries(text: str) -> dict[str, str]:
    """A library table's paths by nickname; ParseError if it is not one."""
    out = {}
    for lib in children(parse(text), "lib"):
        name, uri = child(lib, "name"), child(lib, "uri")
        if uri is not None and len(uri) > 1:
            out[str(name[1]) if name is not None and len(name) > 1 else ""] = str(uri[1])
    return out


def models(pcb: Expression) -> list[Model]:
    """The board's footprints' 3D models, but embedded ones."""
    out = []
    for footprint in children(pcb, "footprint"):
        reference = next(
            (str(p[2]) for p in children(footprint, "property") if p[1:2] == ["Reference"]), ""
        )
        out += [
            Model(str(footprint[1]), reference, str(model[1]))
            for model in children(footprint, "model")
            if len(model) > 1 and not str(model[1]).startswith(EMBEDDED)
        ]
    return out


def model_paths(pcb: Expression) -> list[str]:
    """The board's footprints' 3D model paths, but embedded ones."""
    return [m.path for m in models(pcb)]


def expand(path: str, project: Path) -> Path:
    """A path with ${KIPRJMOD} its project's directory, a relative one from
    there, normalized."""
    return Path(os.path.normpath(project / path.replace(f"${{{PROJECT}}}", str(project))))


def libraries(root: Path, directory: Path, pcb: Expression) -> Libraries:
    """The paths the project in directory (from root) reads libraries by:
    its library tables' and its board's 3D models'.  A 3D model's
    relative path (no variable) is found as KiCad 10.0.8 finds it: from
    the project's directory, else from its footprint's library's
    directory (the project fp-lib-table's, by the footprint's LIBRARY);
    one found nowhere is unresolved.  ValueError naming a table that does
    not parse."""
    tables: dict[str, dict[str, str]] = {}
    for name in TABLES:
        file = root / directory / name
        if file.exists():
            try:
                tables[name] = table_libraries(file.read_text())
            except ParseError as e:
                raise ValueError(f"{directory / name}: {e}") from e
    top = Path(os.path.normpath(root.absolute()))
    project = top / directory
    inside: dict[Path, None] = {}
    outside: dict[str, None] = {}
    stock: dict[str, None] = {}
    unresolved: list[str] = []

    def kind(path: str) -> str:
        names = VARIABLE.findall(path)
        if any(STOCK.fullmatch(n) for n in names):
            return "stock"
        return "unknown" if any(n != PROJECT for n in names) else "project"

    def place(path: str, resolved: Path) -> None:
        if resolved.is_relative_to(top):
            inside[resolved.relative_to(top)] = None
        else:
            outside[path] = None

    for table in tables.values():
        for path in table.values():
            match kind(path):
                case "stock":
                    stock[path] = None
                case "unknown":
                    outside[path] = None
                case _:
                    place(path, expand(path, project))
    footprints = tables.get(TABLES[0], {})
    for m in models(pcb):
        match kind(m.path):
            case "stock":
                stock[m.path] = None
                continue
            case "unknown":
                outside[m.path] = None
                continue
        if VARIABLE.search(m.path) or os.path.isabs(m.path):
            place(m.path, expand(m.path, project))
            continue
        nickname = m.footprint.split(":", 1)[0] if ":" in m.footprint else ""
        library = footprints.get(nickname)
        candidates = [expand(m.path, project)]
        if library is not None and kind(library) == "project":
            candidates.append(Path(os.path.normpath(expand(library, project) / m.path)))
        found = next((c for c in candidates if c.exists()), None)
        if found is not None:
            place(m.path, found)
        elif library is not None and kind(library) == "stock":
            stock[m.path] = None  # from a stock library's directory, KiCad's own
        else:
            why = "" if library is not None else f" (library {nickname!r} not in fp-lib-table)"
            unresolved.append(f"{m.reference} ({m.footprint}): {m.path}{why}")
    return Libraries(list(inside), list(outside), list(stock), unresolved)
