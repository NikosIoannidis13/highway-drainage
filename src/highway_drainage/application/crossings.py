"""Extract two CAD sources and find candidate crossings, without hydrology."""

from collections.abc import Callable
from dataclasses import fields
from math import isfinite
from threading import Event
from typing import Protocol

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import (
    CrossingLimits,
    CrossingRequest,
    CrossingResult,
    ExtractedLines,
    LineSource,
)


class LineReader(Protocol):
    def read(
        self,
        source: LineSource,
        working_crs: str,
        tolerance: float,
        limits: CrossingLimits,
        cancel: Event,
    ) -> ExtractedLines: ...


class IntersectionEngine(Protocol):
    def intersect(
        self,
        highways: ExtractedLines,
        culverts: ExtractedLines,
        request: CrossingRequest,
        cancel: Event,
    ) -> CrossingResult: ...


class FindCrossings:
    def __init__(self, reader: LineReader, engine: IntersectionEngine) -> None:
        self._reader, self._engine = reader, engine

    def execute(
        self,
        request: CrossingRequest,
        cancel: Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> CrossingResult:
        token = cancel if cancel is not None else Event()
        report = progress if progress is not None else lambda _: None
        if not isfinite(request.curve_tolerance) or request.curve_tolerance <= 0:
            raise ValueError("Curve tolerance must be finite and greater than zero.")
        if any(getattr(request.limits, field.name) <= 0 for field in fields(request.limits)):
            raise ValueError("Crossing resource limits must be positive.")
        for source in (request.highway, request.culverts):
            if source.path.suffix.lower() != ".dxf":
                raise ValueError("Select a highway DXF and a culvert DXF.")
            if not request.working_crs.strip():
                raise ValueError("Load a project raster before computing crossings.")
        if token.is_set():
            raise ImportCancelled()
        report("Extracting highway linework…")
        highways = self._reader.read(
            request.highway, request.working_crs, request.curve_tolerance, request.limits, token
        )
        report("Extracting culvert linework…")
        culverts = self._reader.read(
            request.culverts, request.working_crs, request.curve_tolerance, request.limits, token
        )
        report("Computing plan-view intersection points…")
        return self._engine.intersect(highways, culverts, request, token)
