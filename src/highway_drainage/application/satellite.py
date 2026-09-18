"""Display-only transformation of application snapshots into an embedded map."""

from dataclasses import dataclass
from threading import Event
from typing import Protocol

from highway_drainage.application.earth_preview import EarthPreview
from highway_drainage.domain.preview import RasterPreview


@dataclass(frozen=True)
class SatelliteRequest:
    preview: EarthPreview
    footprint: RasterPreview | None = None
    view: str = "drainage"


class SatelliteBuilder(Protocol):
    def build(self, request: SatelliteRequest, cancel: Event) -> str: ...
