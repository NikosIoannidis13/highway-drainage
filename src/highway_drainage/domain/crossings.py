"""Plan-view CAD geometry and crossing records, independent of Qt/ezdxf/Shapely."""

from dataclasses import dataclass
from pathlib import Path

type XY = tuple[float, float]


@dataclass(frozen=True)
class CadReference:
    path: Path
    handle: str
    layer: str
    entity_type: str
    inserts: tuple[str, ...] = ()

    @property
    def identifier(self) -> str:
        return f"{self.path.resolve()}::{'/'.join((*self.inserts, self.handle))}"

    @property
    def label(self) -> str:
        return f"{self.path.name}:{'/'.join((*self.inserts, self.handle))} [{self.layer}]"


@dataclass(frozen=True)
class CadLine:
    reference: CadReference
    vertices: tuple[XY, ...]
    approximated: bool = False


@dataclass(frozen=True)
class CrossingIssue:
    severity: str
    code: str
    message: str
    source: CadReference
    related: CadReference | None = None


@dataclass(frozen=True)
class LineSource:
    path: Path
    crs: str
    layers: tuple[str, ...] = ()  # empty means all modelspace layers
    fallback_crs: str = ""  # Explicit project assumption for unreferenced CAD.


@dataclass(frozen=True)
class CrossingLimits:
    max_entities: int = 100_000
    max_features: int = 5_000
    max_vertices: int = 100_000
    max_curve_vertices: int = 20_000
    max_block_depth: int = 8
    max_candidate_pairs: int = 1_000_000
    max_hits: int = 100_000
    max_points: int = 10_000


@dataclass(frozen=True)
class CrossingRequest:
    highway: LineSource
    culverts: LineSource
    working_crs: str
    curve_tolerance: float = 0.05  # metres in source drawing WCS
    limits: CrossingLimits = CrossingLimits()


@dataclass(frozen=True)
class ExtractedLines:
    lines: tuple[CadLine, ...]
    issues: tuple[CrossingIssue, ...]
    crs_wkt: str
    source: LineSource | None = None


@dataclass(frozen=True)
class CrossingPoint:
    identifier: str
    x: float
    y: float
    culvert: CadReference
    highways: tuple[CadReference, ...]
    endpoint_contact: bool
    approximated: bool


@dataclass(frozen=True)
class CrossingResult:
    crs_wkt: str
    highways: tuple[CadLine, ...]
    culverts: tuple[CadLine, ...]
    points: tuple[CrossingPoint, ...]
    issues: tuple[CrossingIssue, ...]
    curve_tolerance: float
    highway_source: LineSource | None = None
    culvert_source: LineSource | None = None
