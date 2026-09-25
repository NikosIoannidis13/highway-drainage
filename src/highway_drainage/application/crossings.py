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
        if request.raster_bounds is not None:
            xmin, ymin, xmax, ymax = request.raster_bounds
            if (not all(isfinite(v) for v in request.raster_bounds)
                    or xmin >= xmax or ymin >= ymax):
                raise ValueError("Raster bounds must be finite and have positive width and height.")
        for source in (request.highway, request.culverts):
            if source.path.suffix.lower() != ".dxf":
                raise ValueError("Select a highway DXF and a culvert DXF.")
            if not request.working_crs.strip():
                raise ValueError("Enter a project EPSG code or load a project raster.")
        if token.is_set():
            raise ImportCancelled()
        report("Extracting highway linework…")
        highways = self._reader.read(
            request.highway, request.working_crs, request.curve_tolerance, request.limits, token
        )
        self._check_raster_extent(highways, request.raster_bounds, "Highway")
        report("Extracting culvert linework…")
        culverts = self._reader.read(
            request.culverts, request.working_crs, request.curve_tolerance, request.limits, token
        )
        self._check_raster_extent(culverts, request.raster_bounds, "Culverts")
        report("Computing plan-view intersection points…")
        return self._engine.intersect(highways, culverts, request, token)

    @staticmethod
    def _check_raster_extent(
        extracted: ExtractedLines, raster: tuple[float, float, float, float] | None, label: str,
    ) -> None:
        if raster is None or not extracted.lines:
            return
        vertices = [point for line in extracted.lines for point in line.vertices]
        if not vertices:
            return
        xmin, ymin = min(p[0] for p in vertices), min(p[1] for p in vertices)
        xmax, ymax = max(p[0] for p in vertices), max(p[1] for p in vertices)
        if xmax < raster[0] or xmin > raster[2] or ymax < raster[1] or ymin > raster[3]:
            source = extracted.source
            detail = f" {source.unit_summary}" if source is not None else ""
            filename = f" ({source.path.name})" if source is not None else ""
            raise ValueError(
                f"{label}{filename} does not overlap the loaded raster after import. "
                f"Drawing extent: ({xmin:.3f}, {ymin:.3f}, {xmax:.3f}, {ymax:.3f}); "
                f"raster extent: {raster}.{detail} "
                f"Check {label.lower()} source CRS and coordinate units before finding crossings. "
                "For survey coordinates already in the source CRS, choose "
                "'Coordinates already in source CRS'. Otherwise select the actual drawing units "
                "or a raster covering the drawing."
            )
