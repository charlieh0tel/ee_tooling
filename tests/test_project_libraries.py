from pathlib import Path

import pytest

from ee_tooling.project_libraries import Libraries, libraries, model_paths, table_uris
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
