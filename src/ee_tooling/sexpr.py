"""KiCad's S-expressions: parse, write, and look up library symbols.

A parsed expression is a list whose items are lists, `Atom`s (unquoted:
keywords, numbers, yes/no) or `str`s (quoted).  Numbers stay atoms, as
written; `number` reads one.  `write` lays an expression out much as
KiCad does, though not byte for byte.
"""

import functools
import math
import os
import re
from pathlib import Path

# KiCad's own symbol libraries, unless KICAD10_SYMBOL_DIR names others.
SYMBOL_DIR_VARIABLE = "KICAD10_SYMBOL_DIR"
SYMBOL_DIR = Path("/usr/share/kicad/symbols")

# Tokens: parentheses, a quoted string (backslash escapes), or an atom.
TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)("?)|([^\s()"]+))', re.S)
ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
ESCAPE = re.compile(r"\\(.)", re.S)
# What write escapes in a string, and how.
UNESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
# Decimal places write keeps: KiCad's files resolve 0.1 um.
DECIMALS = 4


class Atom(str):
    """An unquoted atom: a keyword, a number or yes/no, as written."""

    __slots__ = ()


type Item = Atom | str | float | int | list[Item]
type Expression = list[Item]


class ParseError(ValueError):
    """Text that is not one S-expression."""


def unescape(text: str) -> str:
    return ESCAPE.sub(lambda m: ESCAPES.get(m.group(1), m.group(1)), text)


def parse(text: str) -> Expression:
    """The one expression text holds."""
    stack: list[Expression] = [[]]
    position = 0
    while m := TOKEN.match(text, position):
        if m.end() == position:
            break
        position = m.end()
        opening, closing, quoted, quote_end, atom = m.groups()
        if opening:
            stack.append([])
        elif closing:
            if len(stack) == 1:
                raise ParseError(f"unbalanced ')' at offset {m.start(2)}")
            done = stack.pop()
            stack[-1].append(done)
        elif quoted is not None:
            if not quote_end:
                raise ParseError(f"unterminated string at offset {m.start(3) - 1}")
            stack[-1].append(unescape(quoted))
        else:
            stack[-1].append(Atom(atom))
    if text[position:].strip():
        raise ParseError(f"unreadable text at offset {position}")
    if len(stack) > 1:
        raise ParseError(f"{len(stack) - 1} '(' left open at the end")
    top = stack[0]
    if len(top) != 1 or not isinstance(top[0], list):
        raise ParseError(f"{len(top)} items at the top level, not one expression")
    return top[0]


def quote(text: str) -> str:
    return '"' + "".join(UNESCAPES.get(c, c) for c in text) + '"'


def atom(item: Atom | str | float | int) -> str:
    match item:
        case Atom():
            return str(item)
        case str():
            return quote(item)
        case bool():
            raise TypeError("write yes or no as an Atom, not a bool")
        case float():
            if not math.isfinite(item):
                raise ValueError(f"{item} has no S-expression")
            text = f"{item:.{DECIMALS}f}".rstrip("0").rstrip(".")
            return "0" if text == "-0" else text
        case int():
            return str(item)


def write(expression: Expression, depth: int = 0) -> str:
    """expression, a list on a line if it holds no list, else its head's
    atoms on its line and each item after on its own line, tab indented."""
    indent = "\t" * depth
    head = []
    for n, item in enumerate(expression):
        if isinstance(item, list):
            rest = expression[n:]
            break
        head.append(atom(item))
    else:
        return f"{indent}({' '.join(head)})"
    lines = [f"{indent}({' '.join(head)}"]
    for item in rest:
        lines.append(
            write(item, depth + 1) if isinstance(item, list) else "\t" * (depth + 1) + atom(item)
        )
    lines.append(f"{indent})")
    return "\n".join(lines)


def number(item: Item) -> float:
    """An atom read as a number."""
    if not isinstance(item, Atom):
        raise ValueError(f"{item!r} is not a number")
    return float(item)


def children(expression: Expression, keyword: str) -> list[Expression]:
    """expression's lists headed by keyword, in order."""
    return [i for i in expression if isinstance(i, list) and i and i[0] == keyword]


def child(expression: Expression, keyword: str) -> Expression | None:
    """expression's first list headed by keyword, or None."""
    return next(iter(children(expression, keyword)), None)


@functools.cache
def library(path: Path) -> Expression:
    """A symbol library file, parsed once."""
    return parse(path.read_text())


def symbol_directory() -> Path:
    return Path(os.environ.get(SYMBOL_DIR_VARIABLE, SYMBOL_DIR))


def library_symbol(lib_id: str, directory: Path | None = None) -> Expression:
    """The symbol LIBRARY:NAME from directory/LIBRARY.kicad_sym, by default
    in KiCad's symbol directory."""
    name, _, symbol = lib_id.partition(":")
    if not symbol:
        raise KeyError(f"{lib_id!r} is not LIBRARY:NAME")
    path = (directory or symbol_directory()) / f"{name}.kicad_sym"
    for s in children(library(path), "symbol"):
        if s[1] == symbol:
            return s
    raise KeyError(f"{path} has no symbol {symbol!r}")
