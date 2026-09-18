"""Application boundary for catchment delineation, independent of pyflwdir and Qt."""

import math
from collections.abc import Callable
from dataclasses import replace
from threading import Event
from typing import Protocol

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import HydrologyRequest, HydrologyResult


def with_large_dem_limits(request: HydrologyRequest) -> HydrologyRequest:
    """Explicit larger processing budget; never changes elevations or outlet locations."""
    return replace(
        request,
        max_cells=50_000_000,
        max_memory_bytes=12 * 1024**3,
        max_cell_visits=2_000_000_000,
        max_output_bytes=4 * 1024**3,
    )


def check_hydrology_budget(
    request: HydrologyRequest,
    cells: int,
    outlets: int,
    resolution: float,
    extent: tuple[float, float, float, float],
) -> str:
    """Check estimates before reading full raster arrays or running pyflwdir."""
    memory = 96 * 1024**2 + cells * 192
    disk = cells * (25 + outlets)
    visits = cells * max(1, outlets)
    summary = (
        f"Hydrology estimates {cells:,} cells, {memory / 1024**2:.1f} MiB memory, "
        f"{disk / 1024**2:.1f} MiB output, {outlets} outlets, {visits:,} cell/outlet visits; "
        f"resolution={resolution:g} m, DEM extent={extent}."
    )
    exceeded = [
        f"{name}: estimated {value:,} > limit {limit:,}"
        for name, value, limit in (
            ("cells", cells, request.max_cells),
            ("memory bytes", memory, request.max_memory_bytes),
            ("cell/outlet visits", visits, request.max_cell_visits),
            ("output bytes", disk, request.max_output_bytes),
        )
        if value > limit
    ]
    if exceeded:
        raise ValueError(
            summary
            + "\nExceeded limits: "
            + "; ".join(exceeded)
            + ".\nSelect Large DEM if your machine has sufficient free memory and disk, "
            "or use a coarser DEM or a smaller hydrologically complete extent. Fewer outlets "
            "reduce output and cell visits, but not the DEM cell count or base memory estimate. "
            "If the DEM grid changes, validate and prepare outlets again."
        )
    return summary


class HydrologyEngine(Protocol):
    def delineate(
        self, request: HydrologyRequest, cancel: Event, progress: Callable[[str], None]
    ) -> HydrologyResult: ...


class DelineateCatchments:
    def __init__(self, engine: HydrologyEngine) -> None:
        self._engine = engine

    def execute(
        self,
        request: HydrologyRequest,
        cancel: Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> HydrologyResult:
        token = cancel if cancel is not None else Event()
        if token.is_set():
            raise ImportCancelled()
        if not request.prepared.outlets:
            raise ValueError("Prepare at least one outlet before delineation.")
        identifiers = [s.original.point.identifier for s in request.prepared.outlets]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Outlet identifiers must be unique.")
        if (
            not math.isfinite(request.minimum_accumulation_cells)
            or request.minimum_accumulation_cells < 1
        ):
            raise ValueError("Minimum accumulation must be at least one contributing cell.")
        if (
            min(
                request.max_cells,
                request.max_memory_bytes,
                request.max_cell_visits,
                request.max_output_bytes,
            )
            < 1
        ):
            raise ValueError("Hydrology resource limits must be positive.")
        return self._engine.delineate(request, token, progress or (lambda message: None))
