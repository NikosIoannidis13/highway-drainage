"""Immutable coordinate audit records; no raster or GUI dependencies."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from highway_drainage.domain.crossings import CrossingPoint, CrossingResult


class PointLocation(StrEnum):
    OUTSIDE = "outside DEM bounds"
    EDGE = "on DEM edge"
    VALID = "inside valid raster cell"
    NODATA = "inside NoData cell"
    UNVALIDATED = "not validated"


@dataclass(frozen=True)
class CoordinateRequest:
    dem: Path
    crossings: CrossingResult


@dataclass(frozen=True)
class DemCoordinates:
    path: Path
    crs: str
    bounds: tuple[float, float, float, float]
    width: int
    height: int
    affine: tuple[float, float, float, float, float, float]
    affine_valid: bool
    y_direction: str
    nodata: float | None
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class OutletCoordinateCheck:
    point: CrossingPoint
    location: PointLocation
    row: int | None = None
    column: int | None = None
    fractional_row: float | None = None
    fractional_column: float | None = None
    cell_state: str = "not read"
    elevation: float | None = None
    edge_sides: tuple[str, ...] = ()
    on_pixel_boundary: bool = False
    ordering_verified: bool = False
    roundtrip_verified: bool = False
    diagnostic: str = ""


@dataclass(frozen=True)
class CoordinateReport:
    dem: DemCoordinates
    crossings: CrossingResult
    crs_matches: bool
    outlets: tuple[OutletCoordinateCheck, ...]
