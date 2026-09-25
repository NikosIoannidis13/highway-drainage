"""Bounded CAD-to-line conversion with OCS and nested INSERT transformations."""

from collections.abc import Iterable, Iterator
from dataclasses import replace
from math import asin, ceil, isfinite, radians, sqrt
from threading import Event

from ezdxf.entities.arc import Arc
from ezdxf.entities.circle import Circle
from ezdxf.entities.dxfentity import DXFEntity
from ezdxf.entities.insert import Insert
from ezdxf.entities.line import Line
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.entities.polyline import Polyline
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFError
from ezdxf.math import Matrix44, Vec3
from pyproj import Transformer
from shapely.geometry import LineString

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import (
    XY,
    CadLine,
    CadReference,
    CrossingIssue,
    CrossingLimits,
    ExtractedLines,
    LineSource,
)
from highway_drainage.infrastructure.cad_reference import cad_frame, preflight_source_crs
from highway_drainage.infrastructure.terrain import _projected_metres


class CrossingLimitError(ValueError):
    """Stop before unbounded curve expansion or intersection work."""


def _arc_vertices(entity: Circle, tolerance: float, maximum: int) -> Iterator[Vec3]:
    radius = float(entity.dxf.radius)
    if not isfinite(radius) or radius <= 0:
        raise ValueError("Arc/circle radius must be positive and finite.")
    start, span = 0.0, 360.0
    if isinstance(entity, Arc):
        start, end = float(entity.dxf.start_angle), float(entity.dxf.end_angle)
        if not isfinite(start) or not isfinite(end):
            raise ValueError("Arc angles must be finite.")
        span = (end - start) % 360
        if span == 0:
            if start == end:
                raise ValueError("Arc has zero angular span.")
            span = 360
    # Equivalent to 2*acos(1-tolerance/radius), but stable for small tolerances.
    step = 4 * asin(sqrt(min(1.0, tolerance / radius / 2)))
    if step <= radians(span) / max(1, maximum - 1):
        raise CrossingLimitError(
            f"Curve exceeds {maximum:,} vertices at tolerance {tolerance:g} m. "
            "Increase curve tolerance or simplify/filter the CAD geometry."
        )
    count = max(4 if not isinstance(entity, Arc) else 1, ceil(radians(span) / step))
    if count + 1 > maximum:
        raise CrossingLimitError("Curve vertex limit exceeded. Increase tolerance.")
    yield from entity.vertices(start + span * i / count for i in range(count + 1))


def _vertices(
    entity: DXFEntity, tolerance: float, maximum: int
) -> tuple[Iterable[Vec3], bool, bool]:
    if isinstance(entity, Line):
        return (Vec3(entity.dxf.start), Vec3(entity.dxf.end)), False, False
    if isinstance(entity, Circle):
        return _arc_vertices(entity, tolerance, maximum), not isinstance(entity, Arc), True
    if isinstance(entity, Polyline):
        if not (entity.is_3d_polyline or entity.is_2d_polyline) or int(entity.dxf.flags) & 6:
            raise ValueError("Fitted polylines and mesh POLYLINE modes are unsupported.")
        if entity.is_3d_polyline:
            return entity.points(), bool(entity.is_closed), False
    if isinstance(entity, (LWPolyline, Polyline)):

        def parts() -> Iterator[Vec3]:
            first = True
            for primitive in entity.virtual_entities():
                points, _, _ = _vertices(primitive, tolerance, maximum)
                for index, point in enumerate(points):
                    if first or index > 0:
                        yield point
                first = False

        return parts(), bool(entity.is_closed), bool(entity.has_arc)
    raise ValueError(f"Unsupported {entity.dxftype()}; convert to supported linework in CAD.")


class CadLineReader:
    def read(
        self,
        source: LineSource,
        working_crs: str,
        tolerance: float,
        limits: CrossingLimits,
        cancel: Event,
    ) -> ExtractedLines:
        target = _projected_metres(working_crs)
        preflight_source_crs(source.path, source.crs, source.fallback_crs)
        try:
            document = readfile(source.path)
        except (OSError, DXFError) as exc:
            raise ValueError(f"Cannot read {source.path.name}: {exc}") from exc
        file_ref = CadReference(source.path, "", "", "FILE")
        issues: list[CrossingIssue] = []
        frame = cad_frame(document, source.path, source.crs, source.fallback_crs, source.units)
        source = replace(
            source, crs=source.crs.strip() or frame.crs.to_wkt(), unit_summary=frame.unit_summary,
        )
        if frame.assumed:
            issues.append(
                CrossingIssue(
                    "warning",
                    "assumed_raster_crs",
                    "DXF has no CRS metadata; assumed the project CRS.",
                    file_ref,
                )
            )
        transformer = Transformer.from_crs(frame.crs, target, always_xy=True, allow_ballpark=False)
        metres_per_unit = frame.z_factor
        if frame.matrix is not None:
            origin = frame.matrix.transform(Vec3())
            metres_per_unit = (
                max(
                    (frame.matrix.transform(axis) - origin).magnitude
                    for axis in (Vec3(1, 0, 0), Vec3(0, 1, 0))
                )
                * frame.crs.axis_info[0].unit_conversion_factor
            )
        if int(document.units) == 0:
            issues.append(
                CrossingIssue(
                    "warning",
                    "unspecified_units",
                    "Drawing units unspecified: using source CRS linear units.",
                    file_ref,
                )
            )
        lines: list[CadLine] = []
        entity_count, vertex_count = 0, 0
        selected_layers = {name.casefold() for name in source.layers}
        duplicate_keys: dict[tuple[XY, ...], CadReference] = {}

        def walk(
            entities: Iterable[DXFEntity],
            matrices: tuple[Matrix44, ...] = (),
            inserts: tuple[str, ...] = (),
            names: tuple[str, ...] = (),
            inherited_layer: str = "0",
            scale_bound: float = 1.0,
        ) -> None:
            nonlocal entity_count, vertex_count
            for entity in entities:
                if cancel.is_set():
                    raise ImportCancelled()
                entity_count += 1
                if entity_count > limits.max_entities:
                    raise CrossingLimitError("CAD entity limit exceeded. Filter/split the drawing.")
                layer = str(entity.dxf.get("layer", "0"))
                if layer == "0" and inserts:
                    layer = inherited_layer
                ref = CadReference(
                    source.path, str(entity.dxf.handle), layer, entity.dxftype(), inserts
                )
                if isinstance(entity, Insert):
                    name = str(entity.dxf.name).casefold()
                    if name in names or len(inserts) >= limits.max_block_depth:
                        raise CrossingLimitError("Cyclic or excessively nested block INSERT.")
                    if int(entity.dxf.row_count) > 1 or int(entity.dxf.column_count) > 1:
                        issues.append(
                            CrossingIssue(
                                "warning",
                                "unsupported",
                                "MINSERT arrays require expansion in CAD.",
                                ref,
                            )
                        )
                        continue
                    block = entity.block()
                    if block is None:
                        issues.append(
                            CrossingIssue(
                                "error",
                                "missing_block",
                                "Block definition is unavailable (possibly an external reference).",
                                ref,
                            )
                        )
                        continue
                    matrix = entity.matrix44()
                    bound = sqrt(
                        sum(
                            matrix.transform_direction(Vec3(axis)).magnitude_square
                            for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))
                        )
                    )
                    next_scale = scale_bound * bound
                    if not isfinite(next_scale) or next_scale <= 0:
                        raise ValueError("Invalid block transform scale.")
                    walk(
                        block,
                        (*matrices, matrix),
                        (*inserts, str(entity.dxf.handle)),
                        (*names, name),
                        layer,
                        next_scale,
                    )
                    continue
                if selected_layers and layer.casefold() not in selected_layers:
                    continue
                try:
                    vertices, closed, curved = _vertices(
                        entity,
                        tolerance / (scale_bound * metres_per_unit),
                        limits.max_curve_vertices,
                    )
                    cleaned: list[XY] = []
                    for i, vertex in enumerate(vertices):
                        if cancel.is_set():
                            raise ImportCancelled()
                        if i >= limits.max_curve_vertices:
                            raise CrossingLimitError(
                                "Vertex limit exceeded. Increase tolerance or split linework."
                            )
                        if not all(isfinite(v) for v in vertex):
                            raise ValueError("Non-finite CAD coordinates.")
                        for matrix in reversed(matrices):
                            vertex = matrix.transform(vertex)
                        vertex = frame.point(vertex)
                        x, y = transformer.transform(vertex.x, vertex.y, errcheck=True)
                        point = float(x), float(y)
                        if not all(isfinite(v) for v in point):
                            raise ValueError("Non-finite transformed coordinates.")
                        if not cleaned or point != cleaned[-1]:
                            cleaned.append(point)
                    if closed and cleaned:
                        # Virtual polyline primitives already include the closing segment.
                        if curved and isinstance(entity, Circle):
                            cleaned[-1] = cleaned[0]
                        elif cleaned[-1] != cleaned[0]:
                            cleaned.append(cleaned[0])
                    if len(cleaned) < 2 or LineString(cleaned).length == 0:
                        raise ValueError("Line collapses to zero length in plan view.")
                    geometry = LineString(cleaned)
                    if not geometry.is_valid:
                        raise ValueError("Invalid plan-view line geometry.")
                    if not geometry.is_simple:
                        issues.append(
                            CrossingIssue(
                                "warning",
                                "self_intersection",
                                "Linework intersects itself; inspect crossing candidates.",
                                ref,
                            )
                        )
                    vertex_count += len(cleaned)
                    if vertex_count > limits.max_vertices or len(lines) >= limits.max_features:
                        raise CrossingLimitError(
                            "Geometry limit exceeded. Select relevant layers or split the files."
                        )
                    coords = tuple(cleaned)
                    key = min(coords, tuple(reversed(coords)))
                    if key in duplicate_keys:
                        issues.append(
                            CrossingIssue(
                                "warning",
                                "duplicate",
                                "Duplicate line retained to preserve all source identifiers.",
                                ref,
                                duplicate_keys[key],
                            )
                        )
                    else:
                        duplicate_keys[key] = ref
                    lines.append(CadLine(ref, coords, curved))
                    if curved:
                        issues.append(
                            CrossingIssue(
                                "info",
                                "curve_approximation",
                                f"Curve approximated at {tolerance:g} m source-WCS tolerance.",
                                ref,
                            )
                        )
                except CrossingLimitError:
                    raise
                except (ValueError, DXFError, ZeroDivisionError) as exc:
                    issues.append(
                        CrossingIssue(
                            "warning" if "Unsupported" in str(exc) else "error",
                            "unsupported" if "Unsupported" in str(exc) else "invalid_geometry",
                            str(exc),
                            ref,
                        )
                    )

        walk(document.modelspace())
        if not lines:
            issues.append(
                CrossingIssue(
                    "error",
                    "empty",
                    "No usable linework found; check layer filters and entity types.",
                    file_ref,
                )
            )
        return ExtractedLines(tuple(lines), tuple(issues), target.to_wkt(), source)
