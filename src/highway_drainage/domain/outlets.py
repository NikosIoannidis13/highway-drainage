"""Separate geometric crossings, containing pixels and selected pour points."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from highway_drainage.domain.coordinates import (
    CoordinateReport,
    CoordinateRequest,
    OutletCoordinateCheck,
)


class SnapMode(StrEnum):
    NEAREST = "nearest valid DEM cell"
    ACCUMULATION = "highest flow accumulation"


@dataclass(frozen=True)
class SnapRequest:
    coordinates: CoordinateRequest
    max_distance: float = 10.0
    mode: SnapMode = SnapMode.NEAREST
    accumulation: Path | None = None
    minimum_accumulation: float = 1.0
    accumulation_units: str = "cells"
    max_window_cells: int = 250_000
    max_total_cells: int = 5_000_000


@dataclass(frozen=True)
class PourPoint:
    row: int
    column: int
    x: float
    y: float
    elevation: float
    distance: float
    accumulation: float | None


@dataclass(frozen=True)
class OutletSelection:
    original: OutletCoordinateCheck
    pour_point: PourPoint | None
    status: str
    diagnostic: str
    shares_cell_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapResult:
    request: SnapRequest
    validation: CoordinateReport
    outlets: tuple[OutletSelection, ...]
