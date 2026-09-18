"""Validate coordinates afresh before selecting pour-point cells."""

import math
from threading import Event
from typing import Protocol

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.domain.coordinates import CoordinateReport
from highway_drainage.domain.outlets import SnapMode, SnapRequest, SnapResult


class OutletSnapper(Protocol):
    def snap(self, request: SnapRequest, audit: CoordinateReport, cancel: Event) -> SnapResult: ...


class SelectOutlets:
    def __init__(self, validator: ValidateCoordinates, snapper: OutletSnapper) -> None:
        self._validator, self._snapper = validator, snapper

    def execute(self, request: SnapRequest, cancel: Event | None = None) -> SnapResult:
        if not math.isfinite(request.max_distance) or request.max_distance < 0:
            raise ValueError("Maximum snapping distance must be finite and nonnegative (metres).")
        if not isinstance(request.mode, SnapMode):
            raise ValueError("Unknown snapping mode.")
        if request.max_window_cells < 1 or request.max_total_cells < 1:
            raise ValueError("Search resource limits must be positive.")
        if request.mode == SnapMode.ACCUMULATION:
            if request.accumulation is None or not request.accumulation_units.strip():
                raise ValueError("Select an aligned accumulation raster and declare its units.")
            if not math.isfinite(request.minimum_accumulation) or request.minimum_accumulation <= 0:
                raise ValueError("Minimum flow accumulation must be finite and positive.")
        token = cancel if cancel is not None else Event()
        audit = self._validator.execute(request.coordinates, token)
        return self._snapper.snap(request, audit, token)
