"""Immutable terrain inputs and import findings, independent of CAD/GIS libraries."""

from dataclasses import dataclass
from enum import StrEnum
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
class TerrainRequest:
    sources: tuple[TerrainSource, ...]
    working_crs: str
    vertical_reference: str
    min_z: float | None = None
    max_z: float | None = None


@dataclass(frozen=True)
class TerrainDataset:
    sources: tuple[TerrainSource, ...]
    crs_wkt: str
    vertical_reference: str
    features: tuple[TerrainFeature, ...]
    issues: tuple[ImportIssue, ...]
    crs_label: str = ""

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)
