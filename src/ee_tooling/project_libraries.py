"""The libraries a KiCad project reads, by the paths its library tables
and its board's 3D models name (docs/kicad-style.md, Files: a board's
libraries live inside its repository).

A path is resolved as KiCad 10.0.8 does: ${KIPRJMOD} is the project's
directory, and a relative path is from it.  A path under one of KiCad's
own install variables, as its only variable and with no .. after it,
names its stock libraries, which are not the repository's; any other
variable is the user's, unknown here.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ee_tooling.sexpr import Expression, ParseError, child, children, parse

# The project's library tables, beside its .kicad_pro.
TABLES = ("fp-lib-table", "sym-lib-table")
# A ${NAME} variable in a path.
VARIABLE = re.compile(r"\$\{([^}]*)\}")
PROJECT = "KIPRJMOD"
# KiCad's own install variables for its stock footprint, symbol and 3D
# model libraries: versioned (KICAD10_3DMODEL_DIR, KICAD6_FOOTPRINT_DIR,
# ...) and the legacy names of the footprint and 3D model directories.
STOCK = re.compile(r"KICAD\d*_(FOOTPRINT|SYMBOL|3DMODEL)_DIR|KISYS3DMOD|KISYSMOD")
# A path's separators, either platform's, and its parent directory.
SEPARATOR = re.compile(r"[/\\]")
PARENT = ".."
# A path naming what this module cannot place: an environment variable in
# KiCad 5's $(NAME) form, or a home directory, ~ or ~user.
UNKNOWN_FORMS = ("$(",)
HOME = "~"
# What makes each kind of outside path inside the repository.
TABLE_FIX = (
    "a library table's: put the library in the repository (a git submodule) and point the"
    " table at it, ${KIPRJMOD}-relative"
)
MODEL_FIX = (
    "a 3D model's: put the model in the repository and name it ${KIPRJMOD}-relative, or"
    " relative to its footprint's library there"
)
LIBRARY_MODEL_FIX = (
    "a 3D model's, relative to its footprint's library: no change, once that library is in"
    " the repository"
)
# A library inside the repository with a file in it, named, that is a link
# out of it.
LINK_FIX = "its file {} is a link out of the repository: put the file itself in the library"
# A 3D model embedded in the board file, not a path.
EMBEDDED = "kicad-embed://"


@dataclass(frozen=True)
class Libraries:
    """What a project's library paths resolve to."""

    inside: list[
        Path
    ]  # each inside the repository, from its root, once; through a link, as written too
    outside: list[str]  # each path as written that leads outside it or is unknown
    stock: list[str]  # each path as written under a KiCad install variable
    unresolved: list[str]  # a relative 3D model path found nowhere: its footprint and path
    fixes: dict[str, str] = field(default_factory=dict)  # each outside path to what makes it inside


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
                tables[name] = table_libraries(file.read_text(encoding="utf-8-sig"))
            except ParseError as e:
                raise ValueError(f"{directory / name}: {e}") from e
    top = root.resolve()
    project = top / directory
    inside: dict[Path, None] = {}
    outside: dict[str, str] = {}  # each path as written, to its fix
    stock: dict[str, None] = {}
    unresolved: list[str] = []

    def kind(path: str) -> str:
        if any(f in path for f in UNKNOWN_FORMS) or path.startswith(HOME):
            return "unknown"
        names = VARIABLE.findall(path)
        if any(STOCK.fullmatch(n) for n in names):
            # Stock only as the one variable, first, and not left by ..
            first = VARIABLE.match(path)
            rest = path[first.end() :] if first is not None else ""
            stock = len(names) == 1 and first is not None and PARENT not in SEPARATOR.split(rest)
            return "stock" if stock else "unknown"
        return "unknown" if any(n != PROJECT for n in names) else "project"

    def place(path: str, written: Path, fix: str) -> None:
        # Links followed: a link inside the repository may lead out of it.
        resolved = written.resolve()
        if not resolved.is_relative_to(top):
            outside[path] = fix
            return
        # A library directory's own files, one level: a link among them may
        # lead out of it too.
        files = sorted(resolved.iterdir()) if resolved.is_dir() else []
        if leaving := [f for f in files if f.is_symlink() and not f.resolve().is_relative_to(top)]:
            outside[path] = LINK_FIX.format(leaving[0].name)
            return
        # As written too, when a link leads there: the link is an input.
        plain = Path(os.path.normpath(written))
        if plain != resolved and plain.is_relative_to(top):
            inside[plain.relative_to(top)] = None
        inside[resolved.relative_to(top)] = None

    for table in tables.values():
        for path in table.values():
            match kind(path):
                case "stock":
                    stock[path] = None
                case "unknown":
                    outside[path] = TABLE_FIX
                case _:
                    place(path, expand(path, project), TABLE_FIX)
    footprints = tables.get(TABLES[0], {})
    for m in models(pcb):
        match kind(m.path):
            case "stock":
                stock[m.path] = None
                continue
            case "unknown":
                outside[m.path] = MODEL_FIX
                continue
        if VARIABLE.search(m.path) or os.path.isabs(m.path):
            place(m.path, expand(m.path, project), MODEL_FIX)
            continue
        nickname = m.footprint.split(":", 1)[0] if ":" in m.footprint else ""
        library = footprints.get(nickname)
        candidates = [expand(m.path, project)]
        if library is not None and kind(library) == "project":
            candidates.append(Path(os.path.normpath(expand(library, project) / m.path)))
        found = next((c for c in candidates if c.exists()), None)
        if found is not None:
            fix = MODEL_FIX if found == candidates[0] else LIBRARY_MODEL_FIX
            place(m.path, found, fix)
        elif library is not None and kind(library) == "stock":
            stock[m.path] = None  # from a stock library's directory, KiCad's own
        else:
            why = "" if library is not None else f" (library {nickname!r} not in fp-lib-table)"
            unresolved.append(f"{m.reference} ({m.footprint}): {m.path}{why}")
    return Libraries(list(inside), list(outside), list(stock), unresolved, outside)
