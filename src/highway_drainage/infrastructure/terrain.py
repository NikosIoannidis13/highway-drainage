"""CRS normalization, geometry validation, and exact duplicate detection."""

from collections.abc import Iterable
from dataclasses import replace
from math import isclose, isfinite
from threading import Event

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Polygon

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.terrain import (
    EntityReference,
    ImportIssue,
    LineRole,
    Point3D,
    TerrainDataset,
    TerrainFeature,
    TerrainRequest,
)

type VertexKey = tuple[float, float, float]
type GeometryKey = tuple[bool, bool, tuple[VertexKey, ...]]


def _projected_metres(value: str) -> CRS:
    crs = CRS.from_user_input(value)
    if not crs.is_projected or len(crs.axis_info) != 2:
        raise ValueError("Use a two-dimensional projected CRS with metre-based XY coordinates.")
    if any(not isclose(axis.unit_conversion_factor, 1.0) for axis in crs.axis_info):
        raise ValueError("Horizontal CRS units must be metres.")
    return crs


def _key(feature: TerrainFeature) -> GeometryKey:
    points = tuple((v.x, v.y, v.z) for v in feature.vertices)
    if feature.closed:
        # A valid ring has unique vertices, so rotate to its unique minimum vertex.
        start = points.index(min(points))
        forward = points[start:] + points[:start]
        reverse = (forward[0], *reversed(forward[1:]))
        points = min(forward, reverse)
    else:
        points = min(points, tuple(reversed(points)))
    return feature.is_face, feature.closed, points


def _invalid_geometry(feature: TerrainFeature) -> str | None:
    vertices = feature.vertices
    minimum = 3 if feature.is_face or feature.closed else 2
    if len(vertices) < minimum:
        return f"Geometry requires at least {minimum} vertices."
    xy = [(v.x, v.y) for v in vertices]
    edges = list(zip(xy, xy[1:], strict=False))
    if feature.closed:
        edges.append((xy[-1], xy[0]))
    if any(a == b for a, b in edges):
        return "Zero-length or vertical edge in XY cannot define a 2.5D terrain constraint."
    if feature.is_face or feature.role == LineRole.BOUNDARY:
        if not feature.closed:
            return "A boundary must be closed; open lines are never closed automatically."
        polygon = Polygon(xy)
        if polygon.area == 0 or not polygon.is_valid:
            return "Face/boundary has zero XY area or invalid/self-intersecting geometry."
    elif not LineString(xy + [xy[0]] if feature.closed else xy).is_simple:
        return "Self-intersecting linework requires review."
    return None


class TerrainNormalizer:
    def normalize(
        self, request: TerrainRequest, items: Iterable[TerrainFeature | ImportIssue], cancel: Event
    ) -> TerrainDataset:
        target = _projected_metres(request.working_crs)
        transforms: dict[object, Transformer] = {}
        scales: dict[object, float] = {}
        for source in request.sources:
            if source.z_unit not in ("m", "ft"):
                raise ValueError("Z units must be m or international ft.")
            transforms[source.path] = Transformer.from_crs(
                _projected_metres(source.crs), target, always_xy=True, allow_ballpark=False
            )
            scales[source.path] = 1.0 if source.z_unit == "m" else 0.3048

        features: list[TerrainFeature] = []
        issues: list[ImportIssue] = []
        seen: dict[GeometryKey, TerrainFeature] = {}
        elevations: dict[tuple[float, float], tuple[float, EntityReference]] = {}
        contour_mode = any(
            source.default_role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES)
            or any(
                role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES)
                for _, role in source.layer_roles
            )
            for source in request.sources
        )
        for item in items:
            if cancel.is_set():
                raise ImportCancelled()
            if isinstance(item, ImportIssue):
                issues.append(item)
                continue
            ref = item.reference
            if not all(isfinite(c) for v in item.vertices for c in (v.x, v.y, v.z)):
                issues.append(ImportIssue("error", "nonfinite", "XYZ must be finite.", ref))
                continue
            transformed: list[Point3D] = []
            try:
                for vertex in item.vertices:
                    x, y = transforms[ref.path].transform(vertex.x, vertex.y, errcheck=True)
                    transformed.append(Point3D(float(x), float(y), vertex.z * scales[ref.path]))
            except Exception as exc:
                raise ValueError(f"Coordinate transformation failed for {ref.path.name}.") from exc
            if not all(isfinite(c) for v in transformed for c in (v.x, v.y, v.z)):
                issues.append(
                    ImportIssue("error", "nonfinite", "Transformed XYZ is not finite.", ref)
                )
                continue
            if any(
                (request.min_z is not None and v.z < request.min_z)
                or (request.max_z is not None and v.z > request.max_z)
                for v in transformed
            ):
                issues.append(
                    ImportIssue("error", "z_range", "Z is outside the declared range.", ref)
                )
                continue
            closed = item.closed
            if not item.is_face and len(transformed) > 1 and transformed[0] == transformed[-1]:
                transformed.pop()
                closed = True
            feature = replace(item, vertices=tuple(transformed), closed=closed)
            invalid = _invalid_geometry(feature)
            if invalid:
                issues.append(ImportIssue("error", "geometry", invalid, ref))
                continue
            if feature.role == LineRole.CONTOUR and (
                max(v.z for v in feature.vertices) - min(v.z for v in feature.vertices) > 1e-6
            ):
                issues.append(
                    ImportIssue(
                        "error",
                        "contour_z",
                        "A contour must have constant elevation (tolerance 0.000001 m).",
                        ref,
                    )
                )
                continue
            key = _key(feature)
            if key in seen:
                original = seen[key]
                conflict = original.role != feature.role
                issues.append(
                    ImportIssue(
                        "error" if conflict else "warning",
                        "duplicate_role_conflict" if conflict else "duplicate",
                        "Duplicate geometry excluded; original retained with source provenance.",
                        ref,
                        original.reference,
                    )
                )
                continue
            seen[key] = feature
            elevation_vertices = (
                () if contour_mode and feature.role == LineRole.BOUNDARY else feature.vertices
            )
            for v in elevation_vertices:
                previous = elevations.get((v.x, v.y))
                if previous is not None and previous[0] != v.z:
                    issues.append(
                        ImportIssue(
                            "error",
                            "z_conflict",
                            "Shared XY has conflicting elevations; not averaged.",
                            ref,
                            previous[1],
                        )
                    )
                    break
            for v in elevation_vertices:
                elevations.setdefault((v.x, v.y), (v.z, ref))
            if all(v.z == 0 for v in feature.vertices):
                issues.append(
                    ImportIssue(
                        "warning",
                        "zero_z",
                        "All elevations are zero; confirm survey elevations.",
                        ref,
                    )
                )
            if feature.is_face and len(feature.vertices) == 4:
                issues.append(
                    ImportIssue(
                        "warning",
                        "quad",
                        "Quad retained; triangulation and planarity review required.",
                        ref,
                    )
                )
            if not feature.is_face and feature.role == LineRole.UNASSIGNED:
                issues.append(
                    ImportIssue(
                        "warning",
                        "unassigned",
                        "Assign a linework role before surface construction.",
                        ref,
                    )
                )
            features.append(feature)
        if cancel.is_set():
            raise ImportCancelled()
        if not features:
            issues.append(
                ImportIssue(
                    "error",
                    "empty",
                    "No valid terrain geometry was imported.",
                    EntityReference(request.sources[0].path, "", "", "FILE"),
                )
            )
        return TerrainDataset(
            request.sources,
            target.to_wkt(),
            request.vertical_reference,
            tuple(features),
            tuple(issues),
        )
