"""Read-only outlet validation boundary."""

from threading import Event
from typing import Protocol

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import CoordinateReport, CoordinateRequest


class CoordinateInspector(Protocol):
    def inspect(self, request: CoordinateRequest, cancel: Event) -> CoordinateReport: ...


class ValidateCoordinates:
    def __init__(self, inspector: CoordinateInspector) -> None:
        self._inspector = inspector

    def execute(self, request: CoordinateRequest, cancel: Event | None = None) -> CoordinateReport:
        token = cancel if cancel is not None else Event()
        if token.is_set():
            raise ImportCancelled()
        if len(request.crossings.points) > 100_000:
            raise ValueError("Validate at most 100,000 outlets at once; split the study area.")
        return self._inspector.inspect(request, token)
