from pathlib import Path

import pytest

from ee_tooling.project_libraries import (
    LIBRARY_MODEL_FIX,
    MODEL_FIX,
    TABLE_FIX,
    Libraries,
    libraries,
    model_paths,
    table_uris,
)
from ee_tooling.sexpr import parse

FP_TABLE = """(fp_lib_table
  (version 7)
  (lib (name "own")(type "KiCad")(uri "${KIPRJMOD}/lib/own.pretty"))
  (lib (name "shared")(type "KiCad")(uri "${KIPRJMOD}/../libs/shared.pretty"))
  (lib (name "plain")(type "KiCad")(uri "fp/plain.pretty"))
  (lib (name "stock")(type "KiCad")(uri "${KICAD10_FOOTPRINT_DIR}/Resistor_SMD.pretty"))
)
"""
SYM_TABLE = """(sym_lib_table
  (version 7)
  (lib (name "sibling")(type "KiCad")(uri "${KIPRJMOD}/../../../sibling/s.kicad_sym"))
  (lib (name "home")(type "KiCad")(uri "/home/x/lib.kicad_sym"))
  (lib (name "pcm")(type "KiCad")(uri "${KICAD10_3RD_PARTY}/symbols/x.kicad_sym"))
)
"""
PCB = """(kicad_pcb
  (footprint "own:R" (model "${KIPRJMOD}/lib/3d/R.step"))
  (footprint "stock:R" (model "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R.wrl")
    (model "${KISYS3DMOD}/R.wrl"))
  (footprint "user:R" (model "${MYLIBS}/R.step"))
  (footprint "e:R" (model "kicad-embed://R.step"))
)
"""


def test_table_uris_and_models() -> None:
    assert table_uris(SYM_TABLE)[1] == "/home/x/lib.kicad_sym"
    assert model_paths(parse(PCB)) == [
        "${KIPRJMOD}/lib/3d/R.step",
        "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R.wrl",
        "${KISYS3DMOD}/R.wrl",
        "${MYLIBS}/R.step",
    ]


def test_libraries(tmp_path: Path) -> None:
    """${KIPRJMOD} and a relative path from the project's directory, inside
    the repository even beside the project; KiCad's install variables
    stock; anything outside, or under a variable not KiCad's, outside."""
    board = tmp_path / "hardware/board"
    board.mkdir(parents=True)
    (board / "fp-lib-table").write_text(FP_TABLE)
    (board / "sym-lib-table").write_text(SYM_TABLE)
    found = libraries(tmp_path, Path("hardware/board"), parse(PCB))
    assert found.inside == [
        Path("hardware/board/lib/own.pretty"),
        Path("hardware/libs/shared.pretty"),
        Path("hardware/board/fp/plain.pretty"),
        Path("hardware/board/lib/3d/R.step"),
    ]
    assert found.outside == [
        "${KIPRJMOD}/../../../sibling/s.kicad_sym",
        "/home/x/lib.kicad_sym",
        "${KICAD10_3RD_PARTY}/symbols/x.kicad_sym",
        "${MYLIBS}/R.step",
    ]
    assert found.stock == [
        "${KICAD10_FOOTPRINT_DIR}/Resistor_SMD.pretty",
        "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R.wrl",
        "${KISYS3DMOD}/R.wrl",
    ]


def test_libraries_at_the_root(tmp_path: Path) -> None:
    """The project at the repository root: ../ is outside."""
    (tmp_path / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "a")(uri "${KIPRJMOD}/../a.pretty")))'
    )
    found = libraries(tmp_path, Path("."), parse("(kicad_pcb)"))
    assert found.outside == ["${KIPRJMOD}/../a.pretty"]
    assert found.inside == []


def test_libraries_bad_table(tmp_path: Path) -> None:
    (tmp_path / "sym-lib-table").write_text("(sym_lib_table")
    with pytest.raises(ValueError, match=r"^sym-lib-table: "):
        libraries(tmp_path, Path("."), parse("(kicad_pcb)"))


def relative_models(tmp_path: Path, model: str, files: list[str]) -> Libraries:
    """The libraries of a repository at tmp_path/repo whose board, in
    hardware/board, has one footprint, lib:R, its 3D model at model, the
    library lib in hardware/libs/lib.pretty; with files (from tmp_path)
    present."""
    root = tmp_path / "repo"
    board = root / "hardware/board"
    (root / "hardware/libs/lib.pretty").mkdir(parents=True)
    board.mkdir(parents=True)
    (board / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "lib")(uri "${KIPRJMOD}/../libs/lib.pretty")))'
    )
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("model\n")
    pcb = f'(kicad_pcb (footprint "lib:R" (property "Reference" "R1") (model "{model}")))'
    return libraries(root, Path("hardware/board"), parse(pcb))


@pytest.mark.parametrize(
    ("model", "files", "inside", "outside", "unresolved"),
    [
        # Found only from the library's directory: inside.
        ("../3d/R.step", ["repo/hardware/libs/3d/R.step"], ["hardware/libs/3d/R.step"], [], []),
        # Found from both: the project's directory first, as KiCad.
        (
            "../3d/R.step",
            ["repo/hardware/libs/3d/R.step", "repo/hardware/3d/R.step"],
            ["hardware/3d/R.step"],
            [],
            [],
        ),
        # Found from the library's directory, outside the repository.
        ("../../../../R.step", ["R.step"], [], ["../../../../R.step"], []),
        # Found nowhere: a warning, not a refusal.
        ("../3d/R.step", [], [], [], ["R1 (lib:R): ../3d/R.step"]),
    ],
)
def test_relative_models(
    tmp_path: Path,
    model: str,
    files: list[str],
    inside: list[str],
    outside: list[str],
    unresolved: list[str],
) -> None:
    found = relative_models(tmp_path, model, files)
    assert found.inside == [Path("hardware/libs/lib.pretty"), *map(Path, inside)]
    assert found.outside == outside
    assert found.unresolved == unresolved


def test_relative_model_of_a_library_not_in_the_table(tmp_path: Path) -> None:
    root = tmp_path
    pcb = '(kicad_pcb (footprint "other:R" (property "Reference" "R1") (model "3d/R.step")))'
    found = libraries(root, Path("."), parse(pcb))
    assert found.unresolved == ["R1 (other:R): 3d/R.step (library 'other' not in fp-lib-table)"]


@pytest.mark.parametrize(
    ("name", "stock"),
    [
        ("KICAD10_FOOTPRINT_DIR", True),
        ("KICAD10_SYMBOL_DIR", True),
        ("KICAD10_3DMODEL_DIR", True),
        ("KICAD6_3DMODEL_DIR", True),
        ("KICAD_3DMODEL_DIR", True),
        ("KISYS3DMOD", True),
        ("KISYSMOD", True),
        ("KICAD10_TEMPLATE_DIR", False),
        ("KICAD10_3RD_PARTY", False),
        ("KICAD_USER_TEMPLATE_DIR", False),
        ("HOME", False),
    ],
)
def test_stock_variables(tmp_path: Path, name: str, stock: bool) -> None:
    """KiCad's stock footprint, symbol and 3D model variables, and no other."""
    (tmp_path / "fp-lib-table").write_text(
        f'(fp_lib_table (lib (name "a")(uri "${{{name}}}/a.pretty")))'
    )
    found = libraries(tmp_path, Path("."), parse("(kicad_pcb)"))
    assert (found.stock, found.outside) == (
        ([f"${{{name}}}/a.pretty"], []) if stock else ([], [f"${{{name}}}/a.pretty"])
    )


@pytest.mark.parametrize(
    ("path", "stock"),
    [
        ("${KICAD10_FOOTPRINT_DIR}/a.pretty", True),
        ("${KICAD10_FOOTPRINT_DIR}/x/./a.pretty", True),
        ("${KICAD10_FOOTPRINT_DIR}/../../../home/u/mylib.pretty", False),
        ("${KICAD10_FOOTPRINT_DIR}/a/../a.pretty", False),
        ("${KICAD10_FOOTPRINT_DIR}/..", False),
        ("${KICAD10_FOOTPRINT_DIR}/${MYLIBS}/a.pretty", False),
        ("${MYLIBS}/${KICAD10_FOOTPRINT_DIR}/a.pretty", False),
        ("${KIPRJMOD}/${KICAD10_FOOTPRINT_DIR}/a.pretty", False),
        ("/opt/${KICAD10_FOOTPRINT_DIR}/a.pretty", False),
    ],
)
def test_stock_paths(tmp_path: Path, path: str, stock: bool) -> None:
    """A stock path is one stock variable, first, then no .. and no other
    variable; anything else under a stock variable is refused."""
    (tmp_path / "fp-lib-table").write_text(f'(fp_lib_table (lib (name "a")(uri "{path}")))')
    found = libraries(tmp_path, Path("."), parse("(kicad_pcb)"))
    assert (found.stock, found.outside) == (([path], []) if stock else ([], [path]))


@pytest.mark.parametrize("path", ["$(HOME)/lib.pretty", "~/lib.pretty", "~user/lib.pretty"])
def test_unknown_forms(tmp_path: Path, path: str) -> None:
    """KiCad 5's $(NAME), and a home directory: not placed, refused."""
    (tmp_path / "fp-lib-table").write_text(f'(fp_lib_table (lib (name "a")(uri "{path}")))')
    assert libraries(tmp_path, Path("."), parse("(kicad_pcb)")).outside == [path]


def test_link_out_of_the_repository(tmp_path: Path) -> None:
    """A link inside the repository to a directory outside it is outside."""
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere.pretty").mkdir()
    (root / "lib.pretty").symlink_to(tmp_path / "elsewhere.pretty")
    (root / "here.pretty").mkdir()
    (root / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "a")(uri "${KIPRJMOD}/lib.pretty"))'
        ' (lib (name "b")(uri "${KIPRJMOD}/here.pretty")))'
    )
    found = libraries(root, Path("."), parse("(kicad_pcb)"))
    assert found.outside == ["${KIPRJMOD}/lib.pretty"]
    assert found.inside == [Path("here.pretty")]


def test_link_out_of_the_repository_in_a_library(tmp_path: Path) -> None:
    """A library inside the repository with a file in it that is a link out
    of it is outside, its fix naming the file; a link within it is not."""
    root = tmp_path / "repo"
    (root / "out.pretty").mkdir(parents=True)
    (root / "in.pretty").mkdir()
    (tmp_path / "R.kicad_mod").write_text("(footprint R)\n")
    (root / "C.kicad_mod").write_text("(footprint C)\n")
    (root / "out.pretty/R.kicad_mod").symlink_to(tmp_path / "R.kicad_mod")
    (root / "in.pretty/C.kicad_mod").symlink_to(root / "C.kicad_mod")
    (tmp_path / "s.kicad_sym").write_text("(kicad_symbol_lib)\n")
    (root / "s.kicad_sym").symlink_to(tmp_path / "s.kicad_sym")
    (root / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "a")(uri "${KIPRJMOD}/out.pretty"))'
        ' (lib (name "b")(uri "${KIPRJMOD}/in.pretty")))'
    )
    (root / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "s")(uri "${KIPRJMOD}/s.kicad_sym")))'
    )
    found = libraries(root, Path("."), parse("(kicad_pcb)"))
    assert found.outside == ["${KIPRJMOD}/out.pretty", "${KIPRJMOD}/s.kicad_sym"]
    assert found.inside == [Path("in.pretty")]
    assert found.fixes["${KIPRJMOD}/out.pretty"] == (
        "its file R.kicad_mod is a link out of the repository: put the file itself in the library"
    )


def test_fixes_by_kind(tmp_path: Path) -> None:
    """A table's path is repointed; a model's relative to its footprint's
    library needs no change once the library is inside; another model's
    is renamed."""
    root = tmp_path / "repo"
    board = root / "hw"
    board.mkdir(parents=True)
    (tmp_path / "lib.pretty").mkdir()
    (tmp_path / "3d").mkdir()
    (tmp_path / "3d/R.step").write_text("model\n")
    (board / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "lib")(uri "${KIPRJMOD}/../../lib.pretty")))'
    )
    pcb = parse(
        '(kicad_pcb (footprint "lib:R" (property "Reference" "R1") (model "../3d/R.step"))'
        ' (footprint "lib:C" (property "Reference" "C1") (model "/elsewhere/C.step")))'
    )
    found = libraries(root, Path("hw"), pcb)
    assert found.fixes == {
        "${KIPRJMOD}/../../lib.pretty": TABLE_FIX,
        "../3d/R.step": LIBRARY_MODEL_FIX,
        "/elsewhere/C.step": MODEL_FIX,
    }
