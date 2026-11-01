import itertools
import math
import re
from collections import Counter
from pathlib import Path

import pytest

from ee_tooling.step_model import BLACK, FACETS, Point, Solid, box, cylinder, step_text, write


def edges(solid: Solid) -> Counter[tuple[Point, Point]]:
    return Counter(
        (a, b) for face in solid.faces for a, b in zip(face, face[1:] + face[:1], strict=True)
    )


def closed(solid: Solid) -> bool:
    """Every edge in exactly one face each way: a closed, consistently wound shell."""
    e = edges(solid)
    return all(n == 1 and e[(b, a)] == 1 for (a, b), n in e.items())


def volume(solid: Solid) -> float:
    """The signed volume, by the divergence theorem over a fan of each face:
    positive when the faces wind counterclockwise seen from outside."""
    total = 0.0
    for face in solid.faces:
        a = face[0]
        for b, c in itertools.pairwise(face[1:]):
            total += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
    return total / 6


def test_box() -> None:
    b = box((0, 0, 0), (1, 2, 3), BLACK)
    assert closed(b)
    assert volume(b) == pytest.approx(6.0)


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_cylinder(axis: int) -> None:
    c = cylinder(axis, (1.0, -2.0), 3.0, (-1.0, 4.0), BLACK)
    assert closed(c)
    # A regular n-gon's area is n/2 r^2 sin(360 degrees / n); the height is 5.
    assert volume(c) == pytest.approx(FACETS / 2 * 3.0**2 * math.sin(math.tau / FACETS) * 5)
    along = {p[axis] for face in c.faces for p in face}
    assert along == {-1.0, 4.0}


def test_step_text() -> None:
    solids = [box((0, 0, 0), (1, 1, 1), BLACK), cylinder(2, (0, 0), 1, (0, 2), BLACK)]
    text = step_text("part", "part.step", solids)
    assert text.startswith("ISO-10303-21;\n")
    assert text.endswith("END-ISO-10303-21;\n")
    assert text.count("FACETED_BREP('',") == 2
    assert text.count("STYLED_ITEM(") == 2
    assert text.count("FACE_SURFACE(") == 6 + 24 + 2
    assert "PRODUCT('part','part'," in text
    # Entities are numbered in order, and every reference is to one defined.
    numbers = [int(n) for n in re.findall(r"^#(\d+)=", text, re.M)]
    assert numbers == list(range(1, len(numbers) + 1))
    assert {int(n) for n in re.findall(r"#(\d+)[,)]", text)} <= set(numbers)
    assert step_text("part", "part.step", solids) == text


def test_write(tmp_path: Path) -> None:
    path = tmp_path / "Maker_Part.step"
    write(path, [box((0, 0, 0), (1, 1, 1), BLACK)])
    assert "FILE_NAME('Maker_Part.step'," in path.read_text()
    assert "PRODUCT('Maker_Part','Maker_Part'," in path.read_text()


def test_quote_in_name() -> None:
    with pytest.raises(ValueError, match="quote"):
        step_text("it's", "x.step", [box((0, 0, 0), (1, 1, 1), BLACK)])


def test_flat_face() -> None:
    flat = Solid((((0, 0, 0), (1, 0, 0), (2, 0, 0)),), BLACK)
    with pytest.raises(ValueError, match="no area"):
        step_text("flat", "flat.step", [flat])
