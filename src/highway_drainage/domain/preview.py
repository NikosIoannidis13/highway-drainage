"""Bounded display snapshots, independent of Qt and GIS libraries."""

from dataclasses import dataclass
from pathlib import Path

from highway_drainage.domain.crossings import XY


@dataclass(frozen=True)
class RasterPreview:
    path: Path
    width: int
    height: int
    rgba: bytes
    affine: tuple[float, float, float, float, float, float]
    information: str


@dataclass(frozen=True)
class CatchmentOutline:
    identifier: str
    rings: tuple[tuple[XY, ...], ...]


@dataclass(frozen=True)
class BoundaryPreview:
    outlines: tuple[CatchmentOutline, ...]
    diagnostic: str = ""
