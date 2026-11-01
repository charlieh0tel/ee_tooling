from pathlib import Path

import pytest

from ee_tooling.sexpr import (
    SYMBOL_DIR_VARIABLE,
    Atom,
    Expression,
    ParseError,
    child,
    children,
    library,
    library_symbol,
    number,
    parse,
    write,
)

# KiCad's demo projects, where KiCad is installed: real KiCad 10 files.
DEMOS = Path("/usr/share/kicad/demos")
DEMO_LIMIT = 4  # schematics read, of the smallest, to keep the tests quick

SAMPLE = r"""(kicad_sch
	(version 20250114)
	(generator "eeschema")
	(paper "A4")
	(title_block (title "A \"quoted\" title") (comment 1 "back\\slash"))
	(text "two\nlines" (at 25.4 -0 0))
	(wire (pts (xy 1.27 -2.54) (xy 3.81 2.54)))
)"""


def test_parse() -> None:
    t = parse(SAMPLE)
    assert t[0] == "kicad_sch" and isinstance(t[0], Atom)
    assert child(t, "generator") == ["generator", "eeschema"]
    assert not isinstance(child(t, "generator")[1], Atom)  # type: ignore[index]
    block = child(t, "title_block")
    assert block is not None
    assert child(block, "title") == ["title", 'A "quoted" title']
    assert child(block, "comment") == ["comment", "1", "back\\slash"]
    text = child(t, "text")
    assert text is not None and text[1] == "two\nlines"
    assert [number(a) for a in child(text, "at")[1:]] == [25.4, 0.0, 0.0]  # type: ignore[index]


def test_round_trip() -> None:
    t = parse(SAMPLE)
    assert parse(write(t)) == t
    assert write(parse(write(t))) == write(t)


def test_nesting() -> None:
    t = parse("(a (b (c (d (e 1)))) (f))")
    assert t == ["a", ["b", ["c", ["d", ["e", "1"]]]], ["f"]]
    assert parse(write(t)) == t


def test_write_layout() -> None:
    t: Expression = [Atom("a"), Atom("x"), [Atom("b"), 1.5], [Atom("c"), [Atom("d")]]]
    assert write(t) == "(a x\n\t(b 1.5)\n\t(c\n\t\t(d)\n\t)\n)"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (-0.00001, "0"),
        (1.0, "1"),
        (-2.54, "-2.54"),
        (0.12345, "0.1235"),
        (1e6, "1000000"),
        (7, "7"),
        ('say "hi"\\\n', '"say \\"hi\\"\\\\\\n"'),
        (Atom("yes"), "yes"),
    ],
)
def test_write_atoms(value: Atom | str | float | int, text: str) -> None:
    assert write([Atom("x"), value]) == f"(x {text})"


def test_strings_round_trip() -> None:
    awkward = 'quote " backslash \\ newline \n tab \t and ( )'
    t = parse(write([Atom("s"), awkward]))
    assert t == ["s", awkward]


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_write_not_finite(value: float) -> None:
    with pytest.raises(ValueError, match="no S-expression"):
        write([Atom("x"), value])


def test_write_bool() -> None:
    with pytest.raises(TypeError, match="Atom"):
        write([Atom("x"), True])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "0 items"),
        ("   ", "0 items"),
        ("atom", "not one expression"),
        ("(a) (b)", "2 items"),
        ("(a (b)", "1 '\\(' left open"),
        ("(a))", "unbalanced"),
        ('(a "open)', "unterminated string"),
        ('(a "ends in backslash\\', "unterminated string"),
    ],
)
def test_parse_errors(text: str, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse(text)


def test_number() -> None:
    assert number(Atom("-1.5")) == -1.5
    with pytest.raises(ValueError, match="not a number"):
        number("1.5")
    with pytest.raises(ValueError):
        number(Atom("yes"))


def test_children() -> None:
    t = parse("(a (b 1) (c) (b 2) b)")
    assert children(t, "b") == [["b", "1"], ["b", "2"]]
    assert child(t, "c") == ["c"]
    assert child(t, "d") is None


LIBRARY = """(kicad_symbol_lib
	(symbol "R" (property "Reference" "R"))
	(symbol "C" (property "Reference" "C"))
)"""


def test_library_symbol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Device.kicad_sym").write_text(LIBRARY)
    assert library_symbol("Device:C", tmp_path)[1] == "C"
    monkeypatch.setenv(SYMBOL_DIR_VARIABLE, str(tmp_path))
    library.cache_clear()
    assert library_symbol("Device:R")[1] == "R"
    with pytest.raises(KeyError, match="no symbol 'L'"):
        library_symbol("Device:L")
    with pytest.raises(KeyError, match="LIBRARY:NAME"):
        library_symbol("Device")
    with pytest.raises(FileNotFoundError):
        library_symbol("Other:R")


def demos() -> list[Path]:
    return sorted(DEMOS.rglob("*.kicad_sch"), key=lambda p: p.stat().st_size)[:DEMO_LIMIT]


@pytest.mark.skipif(not DEMOS.exists(), reason=f"no KiCad demos in {DEMOS}")
@pytest.mark.parametrize("path", demos(), ids=lambda p: p.name)
def test_kicad_demo_round_trip(path: Path) -> None:
    t = parse(path.read_text())
    assert t[0] == "kicad_sch"
    assert child(t, "lib_symbols") is not None
    assert parse(write(t)) == t
