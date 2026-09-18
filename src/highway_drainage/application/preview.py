"""Display-data port; previews never run terrain or hydrology use cases."""

from pathlib import Path
from threading import Event
from typing import Protocol

from highway_drainage.domain.hydrology import HydrologyResult
from highway_drainage.domain.preview import BoundaryPreview, RasterPreview


class PreviewReader(Protocol):
    def raster(self, path: Path, cancel: Event) -> RasterPreview: ...
    def boundaries(self, result: HydrologyResult, cancel: Event) -> BoundaryPreview: ...
