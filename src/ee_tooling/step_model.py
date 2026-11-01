"""Stand-in 3D models as STEP files, for parts whose makers' models may
not be redistributed.

A model is a list of `Solid`s (boxes and faceted cylinders), drawn in the
maker's model's frame and extents, so the footprint that places it needs
no change.  `write` saves them as one STEP AP214 file: a
FACETED_BREP_SHAPE_REPRESENTATION, one FACETED_BREP per solid, each with
a STYLED_ITEM color.  KiCad 10 loads it (pcb export step, pcb render) but
draws it gray, ignoring the colors.
"""

import math
from dataclasses import dataclass
from pathlib import Path

Point = tuple[float, float, float]  # mm
Color = tuple[float, float, float]  # red, green, blue, 0 to 1

NICKEL = (0.78, 0.78, 0.80)
TIN = (0.85, 0.85, 0.87)
GOLD = (0.85, 0.68, 0.25)
BLACK = (0.10, 0.10, 0.10)

FACETS = 24  # sides of a cylinder's prism
# FILE_NAME's time stamp, fixed so a model's file changes only with its shape.
TIME_STAMP = "2026-01-01T00:00:00"
DECIMALS = 4  # of a coordinate, mm
DIRECTION_DECIMALS = 6
COLOR_DECIMALS = 3


@dataclass(frozen=True)
class Solid:
    """A closed polyhedron: faces as point loops, counterclockwise seen from
    outside, so every edge is in one face each way."""

    faces: tuple[tuple[Point, ...], ...]
    color: Color


def box(low: Point, high: Point, color: Color) -> Solid:
    """An axis-aligned box between two corners."""
    (x0, y0, z0), (x1, y1, z1) = low, high
    # corners[i]: x from bit 0, y from bit 1, z from bit 2.
    corners = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    quads = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))
    return Solid(tuple(tuple(corners[i] for i in q) for q in quads), color)


def cylinder(
    axis: int, center: tuple[float, float], radius: float, span: tuple[float, float], color: Color
) -> Solid:
    """A prism of FACETS sides about an axis (0 x, 1 y, 2 z) through
    center, given in the next two axes in turn, over span along the axis."""

    def point(along: float, angle: float) -> Point:
        p = [0.0, 0.0, 0.0]
        p[axis] = along
        p[(axis + 1) % 3] = center[0] + radius * math.cos(angle)
        p[(axis + 2) % 3] = center[1] + radius * math.sin(angle)
        return p[0], p[1], p[2]

    angles = [math.tau * k / FACETS for k in range(FACETS)]
    start, end = span
    sides = tuple(
        (
            point(start, angles[k]),
            point(start, angles[(k + 1) % FACETS]),
            point(end, angles[(k + 1) % FACETS]),
            point(end, angles[k]),
        )
        for k in range(FACETS)
    )
    bottom = tuple(point(start, a) for a in reversed(angles))
    top = tuple(point(end, a) for a in angles)
    return Solid((*sides, bottom, top), color)


def numbers(values: Point, decimals: int) -> str:
    return ",".join(f"{v:.{decimals}f}" for v in values)


def unit(vector: Point) -> Point:
    length = math.hypot(*vector)
    if length == 0:
        raise ValueError("a face with no area")
    return vector[0] / length, vector[1] / length, vector[2] / length


def normal(loop: tuple[Point, ...]) -> Point:
    """Newell's normal of a loop of points."""
    n = [0.0, 0.0, 0.0]
    for (x0, y0, z0), (x1, y1, z1) in zip(loop, loop[1:] + loop[:1], strict=True):
        n[0] += (y0 - y1) * (z0 + z1)
        n[1] += (z0 - z1) * (x0 + x1)
        n[2] += (x0 - x1) * (y0 + y1)
    return unit((n[0], n[1], n[2]))


class Step:
    """One STEP file's entities, numbered #1 on in the order added."""

    def __init__(self) -> None:
        self.entities: list[str] = []

    def add(self, entity: str) -> str:
        self.entities.append(entity)
        return f"#{len(self.entities)}"

    def point(self, p: Point) -> str:
        return self.add(f"CARTESIAN_POINT('',({numbers(p, DECIMALS)}))")

    def direction(self, d: Point) -> str:
        return self.add(f"DIRECTION('',({numbers(d, DIRECTION_DECIMALS)}))")

    def plane(self, loop: tuple[Point, ...]) -> str:
        """The plane of a face: through its first point, normal to it, its
        reference direction along its first edge."""
        first_edge = unit(
            (loop[1][0] - loop[0][0], loop[1][1] - loop[0][1], loop[1][2] - loop[0][2])
        )
        frame = self.add(
            f"AXIS2_PLACEMENT_3D('',{self.point(loop[0])},{self.direction(normal(loop))},"
            f"{self.direction(first_edge)})"
        )
        return self.add(f"PLANE('',{frame})")

    def solid(self, s: Solid) -> str:
        faces = []
        for loop in s.faces:
            polygon = self.add(f"POLY_LOOP('',({','.join(self.point(p) for p in loop)}))")
            bound = self.add(f"FACE_OUTER_BOUND('',{polygon},.T.)")
            faces.append(self.add(f"FACE_SURFACE('',({bound}),{self.plane(loop)},.T.)"))
        shell = self.add(f"CLOSED_SHELL('',({','.join(faces)}))")
        return self.add(f"FACETED_BREP('',{shell})")

    def style(self, item: str, color: Color) -> str:
        rgb = self.add(f"COLOUR_RGB('',{numbers(color, COLOR_DECIMALS)})")
        fill_color = self.add(f"FILL_AREA_STYLE_COLOUR('',{rgb})")
        fill = self.add(f"FILL_AREA_STYLE('',({fill_color}))")
        area = self.add(f"SURFACE_STYLE_FILL_AREA({fill})")
        side = self.add(f"SURFACE_SIDE_STYLE('',({area}))")
        usage = self.add(f"SURFACE_STYLE_USAGE(.BOTH.,{side})")
        assignment = self.add(f"PRESENTATION_STYLE_ASSIGNMENT(({usage}))")
        return self.add(f"STYLED_ITEM('',({assignment}),{item})")

    def model(self, name: str, solids: list[Solid]) -> None:
        """The entities of one product, name, made of solids."""
        units = (
            self.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )"),
            self.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )"),
            self.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )"),
        )
        uncertainty = self.add(
            f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-04),{units[0]},"
            "'distance_accuracy_value','')"
        )
        context = self.add(
            "( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(("
            f"{uncertainty})) GLOBAL_UNIT_ASSIGNED_CONTEXT(({','.join(units)})) "
            "REPRESENTATION_CONTEXT('',''))"
        )
        breps = [self.solid(s) for s in solids]
        styled = [self.style(b, s.color) for b, s in zip(breps, solids, strict=True)]
        self.add(
            "MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION("
            f"'',({','.join(styled)}),{context})"
        )
        origin = self.point((0.0, 0.0, 0.0))
        frame = self.add(
            f"AXIS2_PLACEMENT_3D('',{origin},{self.direction((0.0, 0.0, 1.0))},"
            f"{self.direction((1.0, 0.0, 0.0))})"
        )
        shape = self.add(
            f"FACETED_BREP_SHAPE_REPRESENTATION('{name}',({','.join(breps)},{frame}),{context})"
        )
        application = self.add("APPLICATION_CONTEXT('automotive design')")
        self.add(
            "APPLICATION_PROTOCOL_DEFINITION("
            f"'international standard','automotive_design',2000,{application})"
        )
        product_context = self.add(f"PRODUCT_CONTEXT('',{application},'mechanical')")
        product = self.add(f"PRODUCT('{name}','{name}','',({product_context}))")
        formation = self.add(f"PRODUCT_DEFINITION_FORMATION('','',{product})")
        definition_context = self.add(
            f"PRODUCT_DEFINITION_CONTEXT('part definition',{application},'design')"
        )
        definition = self.add(f"PRODUCT_DEFINITION('design','',{formation},{definition_context})")
        definition_shape = self.add(f"PRODUCT_DEFINITION_SHAPE('','',{definition})")
        self.add(f"SHAPE_DEFINITION_REPRESENTATION({definition_shape},{shape})")


def step_text(name: str, file_name: str, solids: list[Solid]) -> str:
    """A STEP file holding one product, name, made of solids."""
    if "'" in name or "'" in file_name:
        raise ValueError("a name with a quote")
    s = Step()
    s.model(name, solids)
    data = "\n".join(f"#{n}={entity};" for n, entity in enumerate(s.entities, 1))
    return (
        "ISO-10303-21;\nHEADER;\n"
        "FILE_DESCRIPTION(('stand-in model, ee_tooling step_model'),'2;1');\n"
        f"FILE_NAME('{file_name}','{TIME_STAMP}',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\n"
        f"DATA;\n{data}\nENDSEC;\nEND-ISO-10303-21;\n"
    )


def write(path: Path, solids: list[Solid]) -> None:
    """Save solids as a STEP file, its product named for the file's stem."""
    path.write_text(step_text(path.stem, path.name, solids))
