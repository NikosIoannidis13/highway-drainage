"""Bounded face topology audit and conservative, source-preserving overlap cleanup."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from math import isfinite
from threading import Event

from shapely import constrained_delaunay_triangles, get_coordinates
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import Triangle
from highway_drainage.domain.terrain import (
    FaceAudit,
    FaceAuditOptions,
    FaceFinding,
    Point3D,
    TerrainDataset,
)


def plane_z(triangle: Triangle, x: float, y: float) -> float:
    a, b, c = triangle
    bx, by, cx, cy = b.x - a.x, b.y - a.y, c.x - a.x, c.y - a.y
    determinant = bx * cy - by * cx
    u = ((x - a.x) * cy - (y - a.y) * cx) / determinant
    v = (bx * (y - a.y) - by * (x - a.x)) / determinant
    return a.z + u * (b.z - a.z) + v * (c.z - a.z)


def _polygons(geometry: BaseGeometry) -> Iterator[Polygon]:
    if isinstance(geometry, Polygon):
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _polygons(part)


def _outlines(geometry: BaseGeometry) -> tuple[tuple[tuple[float, float], ...], ...]:
    if isinstance(geometry, Polygon):
        return tuple(tuple((float(x), float(y)) for x, y in ring.coords)
                     for ring in (geometry.exterior, *geometry.interiors))
    if hasattr(geometry, "geoms"):
        return tuple(ring for part in geometry.geoms for ring in _outlines(part))
    return (tuple((float(x), float(y)) for x, y in get_coordinates(geometry)),)


class FaceAuditor:
    def __init__(
        self, max_checks: int = 10_000_000, max_findings: int = 100_000,
        max_vertices: int = 5_000_000, max_triangles: int = 10_000_000,
    ) -> None:
        self.max_checks = max_checks
        self.max_findings = max_findings
        self.max_vertices = max_vertices
        self.max_triangles = max_triangles

    def audit(
        self, dataset: TerrainDataset, options: FaceAuditOptions, cancel: Event,
        progress: Callable[[str], None],
    ) -> TerrainDataset:
        options.validate()
        faces = tuple(f for f in dataset.features if f.is_face)
        triangles: list[Triangle] = []
        owners: list[int] = []
        errors: list[str] = []
        findings: list[FaceFinding] = []
        checks = 0

        def checkpoint() -> None:
            if cancel.is_set():
                raise ImportCancelled()

        def finish(complete: bool = True, cleaned: tuple[Triangle, ...] = ()) -> TerrainDataset:
            checkpoint()
            audit = FaceAudit(
                faces, options, tuple(triangles), tuple(owners), tuple(findings),
                cleaned, tuple(errors), complete, checks,
            )
            progress(audit.summary())
            return replace(dataset, face_audit=audit)

        checkpoint()
        progress(f"Checking face geometry before DEM construction: {len(faces):,} faces")
        if sum(len(f.vertices) for f in faces) > self.max_vertices:
            errors.append("Face audit vertex limit exceeded; clip/split the source DXFs.")
            return finish(False)
        for index, face in enumerate(faces):
            checkpoint()
            if index % 10000 == 0:
                progress(f"Preparing face audit: {index:,}/{len(faces):,} faces")
            label = f"{face.reference.path.name}/{face.reference.handle}"
            vertices = face.vertices
            if len(vertices) not in (3, 4) or any(
                not isfinite(value) for v in vertices for value in (v.x, v.y, v.z)
            ):
                errors.append(f"{label}: A face must have three or four finite XYZ vertices.")
                continue
            polygon = Polygon([(v.x, v.y) for v in vertices])
            if not polygon.is_valid or not isfinite(polygon.area) or polygon.area <= 0:
                errors.append(f"{label}: A face is degenerate or invalid in XY.")
                continue
            pieces: list[Triangle]
            if len(vertices) == 3:
                pieces = [(vertices[0], vertices[1], vertices[2])]
            else:
                points = {(v.x, v.y): v for v in vertices}
                pieces = []
                for piece in constrained_delaunay_triangles(polygon).geoms:
                    assert isinstance(piece, Polygon)
                    a, b, c = (points[(x, y)] for x, y in list(piece.exterior.coords)[:-1])
                    pieces.append((a, b, c))
                if not pieces or any(
                    abs(plane_z(pieces[0], v.x, v.y) - v.z) > 1e-6 for v in vertices
                ):
                    errors.append(f"{label}: Nonplanar 3DFACE quad; triangulate in CAD first.")
                    continue
            triangles.extend(pieces)
            owners.extend([index] * len(pieces))
            if len(triangles) > self.max_triangles:
                errors.append("Face audit triangle limit exceeded; split the source DXFs.")
                return finish(False)
        checkpoint()
        polygons = [Polygon([(v.x, v.y) for v in triangle]) for triangle in triangles]
        tree = STRtree(polygons)
        # Only accepted overlaps need cuts. Larger faces take precedence; stable source
        # identifiers break ties. Elevation conflicts never enter this ordering policy.
        cuts: dict[int, list[int]] = {}

        def priority(i: int) -> tuple[float, str, str, int]:
            ref = faces[owners[i]].reference
            return (-polygons[i].area, str(ref.path).casefold(), ref.handle, i)

        for i, polygon in enumerate(polygons):
            checkpoint()
            if i % 5000 == 0:
                progress(f"Checking face overlaps: {i:,}/{len(polygons):,} triangles")
            for raw_j in tree.query(polygon):
                j = int(raw_j)
                if j <= i or owners[i] == owners[j]:
                    continue
                checkpoint()
                checks += 1
                if checks > self.max_checks or len(findings) >= self.max_findings:
                    errors.append(
                        "Face audit workload/report limit reached. This is a partial report; "
                        "split the source DXFs and reimport before building."
                    )
                    return finish(False)
                common = polygon.intersection(polygons[j])
                if common.is_empty:
                    continue
                dz = max(abs(plane_z(triangles[i], float(x), float(y))
                             - plane_z(triangles[j], float(x), float(y)))
                         for x, y in get_coordinates(common))
                if not isfinite(dz):
                    errors.append("Face elevation interpolation exceeds numeric range.")
                    return finish(False)
                area = float(common.area)
                if area == 0 and dz <= options.max_z_difference:
                    continue
                retained, trimmed = sorted((i, j), key=priority)
                covered = polygons[retained].covers(polygons[trimmed])
                repairable = (
                    area > 0 and dz <= options.max_z_difference
                    and (covered or area <= options.max_overlap_area)
                )
                if options.overlap_policy == "midpoint":
                    repairable = True
                reason = (
                    "Adjacent faces disagree in elevation" if area == 0 else
                    "Elevation conflict in overlap" if dz > options.max_z_difference else
                    "Overlap scheduled for midpoint elevations"
                    if options.overlap_policy == "midpoint" else
                    "Redundant coverage" if covered else
                    "Small overlap eligible for trimming" if repairable else
                    "Partial overlap exceeds area tolerance"
                )
                centroid = common.centroid
                findings.append(FaceFinding(
                    i, j, area, area / polygon.area, area / polygons[j].area,
                    dz, float(centroid.x), float(centroid.y), _outlines(common),
                    reason, repairable, retained,
                ))
                if repairable and options.overlap_policy == "strict":
                    cuts.setdefault(trimmed, []).append(retained)
        if errors or any(not finding.repairable for finding in findings):
            return finish()
        if not cuts:
            return finish(cleaned=tuple(triangles))
        progress(f"Trimming accepted overlaps in {len(cuts):,} triangles")
        cleaned: list[Triangle] = []
        for i, triangle in enumerate(triangles):
            checkpoint()
            if i not in cuts:
                cleaned.append(triangle)
                continue
            remaining: BaseGeometry = polygons[i]
            for j in sorted(cuts[i], key=priority):
                checkpoint()
                remaining = remaining.difference(polygons[j])
            for polygon in _polygons(remaining):
                for piece in constrained_delaunay_triangles(polygon).geoms:
                    assert isinstance(piece, Polygon)
                    checkpoint()
                    if piece.area <= 0:
                        continue
                    a, b, c = (Point3D(float(x), float(y), plane_z(triangle, x, y))
                               for x, y in list(piece.exterior.coords)[:-1])
                    cleaned.append((a, b, c))
            if len(cleaned) > self.max_triangles:
                errors.append("Overlap cleanup triangle limit exceeded; split the source DXFs.")
                return finish(False)
        return finish(cleaned=tuple(cleaned))
