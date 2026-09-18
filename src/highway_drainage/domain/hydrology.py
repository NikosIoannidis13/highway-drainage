"""Hydrology requests/results contain Python records and artifact paths only."""

from dataclasses import dataclass
from pathlib import Path

from highway_drainage.domain.outlets import OutletSelection, SnapResult


@dataclass(frozen=True)
class HydrologyRequest:
    prepared: SnapResult
    output: Path
    minimum_accumulation_cells: float = 1.0
    max_cells: int = 2_000_000
    max_memory_bytes: int = 512 * 1024**2
    max_cell_visits: int = 100_000_000
    max_output_bytes: int = 512 * 1024**2


@dataclass(frozen=True)
class CatchmentResult:
    outlet: OutletSelection
    status: str
    diagnostic: str
    mask: Path | None = None
    cell_count: int = 0
    area_m2: float = 0.0
    accumulation_cells: float | None = None
    touches_data_boundary: bool = False


@dataclass(frozen=True)
class HydrologyResult:
    output: Path
    conditioned_dem: Path
    flow_direction: Path
    accumulation: Path
    catchments: tuple[CatchmentResult, ...]
    filled_cells: int
    maximum_fill: float
    elevation_unit: str
    diagnostics: tuple[str, ...]
