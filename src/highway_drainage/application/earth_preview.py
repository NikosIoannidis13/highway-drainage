"""Snapshot export port for Google Earth; no engineering processing is performed."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from highway_drainage.domain.crossings import CrossingResult
from highway_drainage.domain.outlets import SnapResult
from highway_drainage.domain.preview import BoundaryPreview, RasterPreview


@dataclass(frozen=True)
class EarthPreview:
    crs: str
    crossings: CrossingResult | None = None
    outlets: SnapResult | None = None
    boundaries: BoundaryPreview | None = None
    raster: RasterPreview | None = None


class EarthPreviewWriter(Protocol):
    def write(
        self, preview: EarthPreview, destination: Path, cancel: Event, *, overwrite: bool = False
    ) -> None: ...
