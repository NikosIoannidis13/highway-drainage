"""Application boundary for catchment delineation, independent of pyflwdir and Qt."""

import math
from collections.abc import Callable
from threading import Event
from typing import Protocol

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import HydrologyRequest, HydrologyResult


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
