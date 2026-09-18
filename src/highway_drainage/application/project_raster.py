"""An externally supplied raster defines the active project's coordinate system."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from highway_drainage.domain.preview import RasterPreview


@dataclass(frozen=True)
class ProjectRaster:
    path: Path
    crs: str
    crs_label: str
    preview: RasterPreview


class ProjectRasterReader(Protocol):
    def read(self, path: Path, cancel: Event) -> ProjectRaster: ...
