"""Reuse supplied faces or triangulate explicitly bounded, already-noded constraints."""

from collections.abc import Iterator
from math import isfinite
from threading import Event

from shapely import constrained_delaunay_triangles, get_coordinates, polygonize_full
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import (
    DemRequest,
    GridPlan,
    ResourceLimitError,
    TerrainModel,
    Triangle,
)
from highway_drainage.domain.terrain import FaceAuditOptions, LineRole, Point3D, TerrainFeature
from highway_drainage.infrastructure.face_audit import FaceAuditor
from highway_drainage.infrastructure.terrain import _projected_metres

type XY = tuple[float, float]
type Edge = tuple[XY, XY]
Z_TOLERANCE = 1e-6  # metres; agreement check only, never coordinate welding


def xy(point: Point3D) -> XY:
    return point.x, point.y


def edge_key(a: Point3D, b: Point3D) -> Edge:
    return min((xy(a), xy(b)), (xy(b), xy(a)))


def segments(feature: TerrainFeature) -> Iterator[tuple[Point3D, Point3D]]:
    yield from zip(feature.vertices, feature.vertices[1:], strict=False)
    if feature.closed:
        yield feature.vertices[-1], feature.vertices[0]


def elevation(triangle: Triangle, x: float, y: float) -> float:
    a, b, c = triangle
    bx, by, cx, cy = b.x - a.x, b.y - a.y, c.x - a.x, c.y - a.y
    determinant = bx * cy - by * cx
    u = ((x - a.x) * cy - (y - a.y) * cx) / determinant
    v = (bx * (y - a.y) - by * (x - a.x)) / determinant
    return a.z + u * (b.z - a.z) + v * (c.z - a.z)


class _Budget:
    def __init__(self, request: DemRequest, plan: GridPlan, cancel: Event) -> None:
        self.request, self.plan, self.cancel = request, plan, cancel
        self.checks = 0

    def check(self, amount: int = 0) -> None:
        if self.cancel.is_set():
            raise ImportCancelled()
        self.checks += amount
        if self.checks > self.request.limits.max_geometry_checks:
            raise ResourceLimitError(
                "Geometry intersection workload limit exceeded.",
                self.plan,
                "Remove overlapping/redundant geometry or split the input dataset spatially.",
            )


def _triangulate(polygon: Polygon, points: dict[XY, Point3D]) -> list[Triangle]:
    result: list[Triangle] = []
    for piece in constrained_delaunay_triangles(polygon).geoms:
        if not isinstance(piece, Polygon):
            raise ValueError("Triangulation returned a non-polygon geometry.")
        coordinates = list(piece.exterior.coords)[:-1]
        if len(coordinates) != 3:
            raise ValueError("Constrained triangulation did not produce triangles.")
        try:
            a, b, c = (points[(float(p[0]), float(p[1]))] for p in coordinates)
        except KeyError as exc:
            raise ValueError(
                "Triangulation introduced an unapproved vertex; no Z was invented."
            ) from exc
        result.append((a, b, c))
    return result


def _validate_mesh(triangles: list[Triangle], budget: _Budget) -> None:
    polygons = [Polygon([xy(v) for v in triangle]) for triangle in triangles]
    tree = STRtree(polygons)
    for i, polygon in enumerate(polygons):
        budget.check()
        for raw_j in tree.query(polygon):
            j = int(raw_j)
            if j <= i:
                continue
            budget.check(1)
            common = polygon.intersection(polygons[j])
            if common.area > 0:
                raise ValueError(
                    "Input faces overlap in XY. Resolve overlapping surfaces before export; "
                    "the exporter does not choose the highest, lowest or first surface."
                )
            for x, y in get_coordinates(common):
                if (
                    abs(
                        elevation(triangles[i], float(x), float(y))
                        - elevation(triangles[j], float(x), float(y))
                    )
                    > Z_TOLERANCE
                ):
                    raise ValueError("Adjacent faces disagree in elevation along a shared edge.")


class SurfaceBuilder:
    def build(self, request: DemRequest, plan: GridPlan, cancel: Event) -> TerrainModel:
        budget = _Budget(request, plan, cancel)
        budget.check()
        _projected_metres(request.dataset.crs_wkt)
        features = request.dataset.features
        faces = [f for f in features if f.is_face]
        if faces and any(f.role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES)
                         for f in features if not f.is_face):
            raise ValueError(
                "Sample interpolation cannot be combined with faces in single-surface mode. "
                "Choose '3D faces with polyline gap filling' to build the two surfaces."
            )
        audit = request.dataset.face_audit
        if faces:
            if audit is None or not audit.matches(features):
                audited = FaceAuditor(
                    max_checks=request.limits.max_geometry_checks,
                    max_vertices=request.limits.max_input_vertices,
                    max_triangles=request.limits.max_triangles,
                ).audit(request.dataset, FaceAuditOptions(), cancel, lambda _: None)
                audit = audited.face_audit
            assert audit is not None
            audit.require_ready()
            if len(audit.cleaned_triangles) > request.limits.max_triangles:
                raise ResourceLimitError("Triangle limit exceeded.", plan, "Split the terrain.")
        boundaries = [f for f in features if f.role == LineRole.BOUNDARY]
        if len(boundaries) > 1:
            raise ValueError(
                "Use one outer boundary. Multiple rings/holes need explicit semantics."
            )
        boundary = boundaries[0] if boundaries else None
        clip = Polygon([xy(v) for v in boundary.vertices]) if boundary else None
        if boundary and (
            not boundary.closed or clip is None or not clip.is_valid or clip.area <= 0
        ):
            raise ValueError("The outer boundary must be closed, simple and have positive area.")
        if any(f.role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES) for f in features):
            from highway_drainage.infrastructure.contours import contour_model

            return contour_model(request, plan, budget, boundary, clip)
        points: dict[XY, Point3D] = {}
        midpoint = bool(faces and audit and audit.options.overlap_policy == "midpoint")
        if midpoint and any(f.role == LineRole.BREAKLINE for f in features):
            raise ValueError("Midpoint face elevations cannot enforce breaklines; use strict mode.")
        for feature in features:
            budget.check()
            for point in feature.vertices:
                if not all(isfinite(v) for v in (point.x, point.y, point.z)):
                    raise ValueError("Model coordinates must be finite.")
                previous = points.get(xy(point))
                tolerance = audit.options.max_z_difference if faces and audit else Z_TOLERANCE
                if (previous is not None and abs(previous.z - point.z) > tolerance
                        and not (midpoint and feature.is_face)):
                    raise ValueError("Terrain vertices disagree in elevation at the same XY.")
                points[xy(point)] = point
        breaks = [f for f in features if f.role == LineRole.BREAKLINE]
        triangles: list[Triangle] = []
        if faces:
            assert audit is not None
            triangles = list(audit.cleaned_triangles)
            edges = {
                edge_key(a, b)
                for t in triangles
                for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))
            }
            for breakline in breaks:
                for a, b in segments(breakline):
                    budget.check()
                    if edge_key(a, b) not in edges:
                        raise ValueError(
                            f"Breakline {breakline.reference.handle} is not an existing mesh edge. "
                            "Split at mesh vertices or supply a mesh with this constraint. "
                            "Existing triangles will not be silently rebuilt."
                        )
            method = ("midpoint_faces" if midpoint else
                      "cleaned_faces" if audit.findings else "preserved_faces")
        else:
            if boundary is None or clip is None:
                raise ValueError(
                    "Linework reconstruction requires one explicit closed outer boundary."
                )
            lines: list[LineString] = []
            seen: set[Edge] = set()
            for feature in [boundary, *breaks]:
                for a, b in segments(feature):
                    budget.check()
                    key = edge_key(a, b)
                    if key[0] == key[1]:
                        raise ValueError("Constraint segment has zero XY length.")
                    if key in seen:
                        continue
                    seen.add(key)
                    line = LineString(key)
                    if not clip.covers(line):
                        raise ValueError("A breakline extends outside the declared boundary.")
                    lines.append(line)
            tree = STRtree(lines)
            for i, line in enumerate(lines):
                budget.check()
                for raw_j in tree.query(line):
                    j = int(raw_j)
                    if j <= i:
                        continue
                    budget.check(1)
                    common = line.intersection(lines[j])
                    if common.is_empty:
                        continue
                    endpoints = set(line.coords) & set(lines[j].coords)
                    if common.geom_type != "Point" or tuple(common.coords[0]) not in endpoints:
                        raise ValueError(
                            "Constraints cross, overlap or meet between vertices. Node/split all "
                            "segments at junctions in CAD with consistent Z; no samples were added."
                        )
            regions, cuts, dangles, invalid = polygonize_full(lines)
            if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
                raise ValueError(
                    "Dangling/cut breaklines cannot be preserved by polygon triangulation. "
                    "Connect them into closed regions or provide a constrained triangle mesh."
                )
            for region in regions.geoms:
                budget.check()
                if not isinstance(region, Polygon):
                    raise ValueError("Constraint polygonization returned a non-polygon.")
                triangles.extend(_triangulate(region, points))
                if len(triangles) > request.limits.max_triangles:
                    raise ResourceLimitError(
                        "Triangle limit exceeded.", plan, "Split the input terrain."
                    )
            area = sum(Polygon([xy(v) for v in t]).area for t in triangles)
            if abs(area - clip.area) > max(1e-8, clip.area * 1e-10):
                raise ValueError(
                    "Constrained triangles do not cover the declared boundary exactly."
                )
            edges = {
                edge_key(a, b)
                for t in triangles
                for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))
            }
            if not seen.issubset(edges):
                raise ValueError("Triangulation did not preserve every constraint segment.")
            method = "constrained_regions"
        if not triangles:
            raise ValueError("No terrain triangles could be constructed.")
        if not faces:
            _validate_mesh(triangles, budget)
        return TerrainModel(
            tuple(triangles),
            request.dataset.crs_wkt,
            request.dataset.vertical_reference,
            boundary.vertices if boundary else None,
            method,
            face_overlap_policy="midpoint" if midpoint else "strict",
        )
