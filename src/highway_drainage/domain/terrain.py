"""Immutable terrain inputs and import findings, independent of CAD/GIS libraries."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path


class LineRole(StrEnum):
    UNASSIGNED = "unassigned"
    TERRAIN_SAMPLES = "terrain samples"
    CONTOUR = "contour"
    BREAKLINE = "breakline"
    BOUNDARY = "boundary"
    IGNORE = "ignore"


@dataclass(frozen=True)
class Point3D:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class EntityReference:
    path: Path
    handle: str
    layer: str
    entity_type: str


@dataclass(frozen=True)
class TerrainSource:
    path: Path
    crs: str
    vertical_reference: str
    z_unit: str = "m"
    default_role: LineRole = LineRole.UNASSIGNED
    layer_roles: tuple[tuple[str, LineRole], ...] = ()
    fallback_crs: str = ""  # Project raster CRS when CAD metadata is absent.

    def role_for(self, layer: str) -> LineRole:
        return dict(self.layer_roles).get(layer, self.default_role)


@dataclass(frozen=True)
class TerrainFeature:
    reference: EntityReference
    vertices: tuple[Point3D, ...]
    is_face: bool = False
    closed: bool = False
    role: LineRole = LineRole.UNASSIGNED


@dataclass(frozen=True)
class ImportIssue:
    severity: str
    code: str
    message: str
    reference: EntityReference
    related: EntityReference | None = None


@dataclass(frozen=True)
class FaceAuditOptions:
    max_overlap_area: float = 1e-6  # m², partial overlaps only
    max_z_difference: float = 1e-6  # m, including shared-edge checks
    overlap_policy: str = "strict"

    def validate(self) -> None:
        if self.overlap_policy not in ("strict", "midpoint"):
            raise ValueError("Choose strict face cleanup or midpoint elevations.")
        if any(not isfinite(v) or v < 0 for v in (
            self.max_overlap_area, self.max_z_difference,
        )):
            raise ValueError("Face overlap tolerances must be finite and nonnegative.")


@dataclass(frozen=True)
class FaceFinding:
    first: int  # Triangle indices, with source provenance in FaceAudit.owners.
    second: int
    area: float
    first_fraction: float
    second_fraction: float
    max_z_difference: float
    x: float
    y: float
    outlines: tuple[tuple[tuple[float, float], ...], ...]
    reason: str
    repairable: bool
    retained: int


@dataclass(frozen=True)
class FaceAudit:
    faces: tuple[TerrainFeature, ...]
    options: FaceAuditOptions
    triangles: tuple[tuple[Point3D, Point3D, Point3D], ...]
    owners: tuple[int, ...]
    findings: tuple[FaceFinding, ...]
    cleaned_triangles: tuple[tuple[Point3D, Point3D, Point3D], ...] = ()
    errors: tuple[str, ...] = ()
    complete: bool = True
    checks: int = 0

    @property
    def blocked(self) -> bool:
        return not self.complete or bool(self.errors) or any(
            not finding.repairable for finding in self.findings
        )

    def matches(self, features: tuple[TerrainFeature, ...]) -> bool:
        return self.faces == tuple(f for f in features if f.is_face)

    def summary(self) -> str:
        conflicts = sum(not f.repairable for f in self.findings)
        repairs = len(self.findings) - conflicts
        status = "DEM build blocked" if self.blocked else "Face checks passed"
        if self.options.overlap_policy == "midpoint":
            return (
                f"{status}: {len(self.faces):,} faces; {len(self.findings):,} reported pairs "
                f"use midpoint elevations; {len(self.errors):,} geometry errors. "
                + ("Audit incomplete. " if not self.complete else "")
                + "At each cell: (lowest + highest face elevation) / 2."
            )
        return (
            f"{status}: {len(self.faces):,} faces; {conflicts:,} unresolved pairs; "
            f"{repairs:,} pairs eligible for cleanup; {len(self.errors):,} geometry errors. "
            + ("Audit incomplete. " if not self.complete else "")
            + f"Limits: partial overlap {self.options.max_overlap_area:g} m², "
            f"elevation difference {self.options.max_z_difference:g} m."
        )

    def require_ready(self) -> None:
        if not self.blocked:
            return
        details = list(self.errors[:3])
        for finding in (f for f in self.findings if not f.repairable):
            a, b = (self.faces[self.owners[i]].reference for i in (finding.first, finding.second))
            details.append(
                f"{finding.reason}: {a.path.name}/{a.handle} and {b.path.name}/{b.handle}; "
                f"overlap {finding.area:.9g} m², maximum Z difference "
                f"{finding.max_z_difference:.9g} m."
            )
            if len(details) >= 3:
                break
        raise ValueError(
            self.summary() + "\n" + "\n".join(details)
            + "\nOpen Review face overlaps in DEM / GeoTIFF. Correct conflicting faces in CAD "
            "and reimport, or set tolerances justified by the survey and recheck. "
            "Changing raster resolution cannot resolve these geometry conflicts."
        )


@dataclass(frozen=True)
class TerrainRequest:
    sources: tuple[TerrainSource, ...]
    working_crs: str
    vertical_reference: str
    min_z: float | None = None
    max_z: float | None = None
    face_options: FaceAuditOptions = FaceAuditOptions()


@dataclass(frozen=True)
class TerrainDataset:
    sources: tuple[TerrainSource, ...]
    crs_wkt: str
    vertical_reference: str
    features: tuple[TerrainFeature, ...]
    issues: tuple[ImportIssue, ...]
    crs_label: str = ""
    face_audit: FaceAudit | None = None

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)
