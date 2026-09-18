"""Coordinate terrain import without depending on Qt, ezdxf, or concrete adapters."""

from collections.abc import Callable, Iterable
from math import isfinite
from threading import Event
from typing import Protocol

from highway_drainage.domain.terrain import (
    ImportIssue,
    TerrainDataset,
    TerrainFeature,
    TerrainRequest,
    TerrainSource,
)


class ImportCancelled(Exception):
    """Raised at a cooperative cancellation checkpoint."""


class TerrainReader(Protocol):
    def read(self, source: TerrainSource) -> Iterable[TerrainFeature | ImportIssue]: ...


class TerrainNormalizer(Protocol):
    def normalize(
        self, request: TerrainRequest, items: Iterable[TerrainFeature | ImportIssue], cancel: Event
    ) -> TerrainDataset: ...


class ImportTerrain:
    def __init__(self, reader: TerrainReader, normalizer: TerrainNormalizer) -> None:
        self._reader = reader
        self._normalizer = normalizer

    def execute(
        self,
        request: TerrainRequest,
        cancel: Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> TerrainDataset:
        if not request.sources:
            raise ValueError("Select at least one DXF file.")
        if not request.vertical_reference.strip():
            raise ValueError("Declare a common vertical reference (survey datum).")
        for bound in (request.min_z, request.max_z):
            if bound is not None and not isfinite(bound):
                raise ValueError("Elevation limits must be finite.")
        if (
            request.min_z is not None
            and request.max_z is not None
            and request.min_z > request.max_z
        ):
            raise ValueError("Minimum elevation exceeds maximum elevation.")
        paths = [source.path.resolve() for source in request.sources]
        if len(set(paths)) != len(paths):
            raise ValueError("The same DXF file was selected more than once.")
        for source in request.sources:
            if source.path.suffix.lower() != ".dxf":
                raise ValueError(f"Not a DXF file: {source.path.name}")
            if source.vertical_reference.strip() != request.vertical_reference.strip():
                raise ValueError("All sources must use the same declared vertical reference.")
            layers = [layer for layer, _ in source.layer_roles]
            if any(not layer.strip() for layer in layers) or len(set(layers)) != len(layers):
                raise ValueError("Layer overrides must have unique, nonempty layer names.")

        token = cancel if cancel is not None else Event()

        report = progress or (lambda _: None)

        def items() -> Iterable[TerrainFeature | ImportIssue]:
            for source in request.sources:
                if token.is_set():
                    raise ImportCancelled()
                report(f"Reading terrain DXF: {source.path.name}")
                for count, feature in enumerate(self._reader.read(source), start=1):
                    if token.is_set():
                        raise ImportCancelled()
                    if count % 10000 == 0:
                        report(f"Validating {source.path.name}: {count:,} entities read")
                    yield feature

        return self._normalizer.normalize(request, items(), token)
