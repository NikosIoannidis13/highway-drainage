"""Bounded XYZ sample interpolation with optional strict contour semantics."""

from math import ceil, hypot, isfinite

from shapely import delaunay_triangles
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.strtree import STRtree

from highway_drainage.domain.dem import (
    DemRequest,
    GridPlan,
    ResourceLimitError,
    TerrainModel,
    Triangle,
)
from highway_drainage.domain.terrain import LineRole, Point3D, TerrainFeature
from highway_drainage.infrastructure.surface import _Budget, segments, xy


def contour_model(
    request: DemRequest,
    plan: GridPlan,
    budget: _Budget,
    boundary: TerrainFeature | None,
    clip: Polygon | None,
) -> TerrainModel:
    if any(f.is_face or f.role == LineRole.BREAKLINE for f in request.dataset.features):
        raise ValueError(
            "Sample interpolation cannot yet be combined with faces or breaklines. "
            "Export sampled terrain separately; mixed inputs are never silently ignored."
        )
    if (boundary is None or clip is None) and request.sample_coverage == "boundary":
        raise ValueError("Contour interpolation requires one explicit closed outer boundary.")
    samples = [
        f
        for f in request.dataset.features
        if f.role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES)
    ]
    for feature in samples:
        if len(feature.vertices) < 2 or not all(
            isfinite(c) for v in feature.vertices for c in (v.x, v.y, v.z)
        ):
            raise ValueError("Terrain linework requires at least two finite XYZ vertices.")
    contours = [f for f in samples if f.role == LineRole.CONTOUR]
    lines: list[LineString] = []
    for feature in contours:
        budget.check()
        if len(feature.vertices) < 2 or not all(
            isfinite(c) for v in feature.vertices for c in (v.x, v.y, v.z)
        ):
            raise ValueError("Contours require at least two finite XYZ vertices.")
        if max(v.z for v in feature.vertices) - min(v.z for v in feature.vertices) > 1e-6:
            raise ValueError("Each contour must have constant elevation (tolerance 0.000001 m).")
        coordinates = [xy(v) for v in feature.vertices]
        if feature.closed:
            coordinates.append(coordinates[0])
        line = LineString(coordinates)
        if not line.is_simple or line.length == 0:
            raise ValueError("Contours must not self-intersect or have zero length.")
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
            if abs(contours[i].vertices[0].z - contours[j].vertices[0].z) > 1e-6:
                raise ValueError("Contours of different elevations intersect or touch.")
            endpoints = {line.coords[0], line.coords[-1]} & {
                lines[j].coords[0],
                lines[j].coords[-1],
            }
            if common.geom_type != "Point" or tuple(common.coords[0]) not in endpoints:
                raise ValueError("Contours cross or overlap; clean the linework before export.")
    points: dict[tuple[float, float], Point3D] = {}

    def add(point: Point3D) -> None:
        previous = points.get(xy(point))
        if previous is not None and abs(previous.z - point.z) > 1e-6:
            raise ValueError("Contour samples have conflicting elevations at identical XY.")
        points[xy(point)] = point
        if len(points) > request.limits.max_input_vertices:
            raise ResourceLimitError(
                "Contour sample count limit exceeded.",
                plan,
                "Increase contour spacing or clip the source contours.",
            )

    for feature in samples:
        for point in feature.vertices:
            budget.check()
            add(point)
        if request.contour_spacing is not None:
            for a, b in segments(feature):
                count = max(1, ceil(hypot(b.x - a.x, b.y - a.y) / request.contour_spacing))
                for i in range(1, count):
                    budget.check()
                    fraction = i / count
                    add(
                        Point3D(
                            a.x + fraction * (b.x - a.x),
                            a.y + fraction * (b.y - a.y),
                            a.z + fraction * (b.z - a.z),
                        )
                    )
    if len(contours) == len(samples) and len({p.z for p in points.values()}) < 2:
        raise ValueError("Supply contours at two or more elevations to reconstruct terrain.")
    # Translate before triangulation to improve precision at survey coordinates.
    origin = min(points)
    local = {(x - origin[0], y - origin[1]): point for (x, y), point in points.items()}
    if len(local) != len(points):
        raise ValueError(
            "Contour coordinates lose precision after translation. Review XY precision."
        )
    pieces = delaunay_triangles(MultiPoint(list(local)), tolerance=0.0)
    budget.check()
    triangles: list[Triangle] = []
    for polygon in pieces.geoms:
        budget.check()
        if not isinstance(polygon, Polygon):
            raise ValueError("Contour triangulation returned a non-polygon.")
        a, b, c = (local[(float(x), float(y))] for x, y in list(polygon.exterior.coords)[:3])
        if request.max_contour_edge is not None and any(
            hypot(u.x - v.x, u.y - v.y) > request.max_contour_edge
            for u, v in ((a, b), (b, c), (c, a))
        ):
            continue
        if clip is None or Polygon([xy(a), xy(b), xy(c)]).intersects(clip):
            triangles.append((a, b, c))
            if len(triangles) > request.limits.max_triangles:
                raise ResourceLimitError(
                    "Contour triangle limit exceeded.",
                    plan,
                    "Increase contour spacing or clip the source contours.",
                )
    if not triangles:
        raise ValueError(
            "No contour triangles remain in the coverage boundary. Check non-collinear input, "
            "boundary location and maximum triangle edge length."
        )
    return TerrainModel(
        tuple(triangles),
        request.dataset.crs_wkt,
        request.dataset.vertical_reference,
        boundary.vertices if boundary else None,
        "sampled_contours_unconstrained"
        if len(contours) == len(samples)
        else "sampled_xyz_unconstrained",
    )
