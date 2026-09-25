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

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        a, b, c, d, e, f = self.affine
        corners = [(a * x + b * y + c, d * x + e * y + f)
                   for x, y in ((0, 0), (self.width, 0),
                                (0, self.height), (self.width, self.height))]
        return (min(x for x, _ in corners), min(y for _, y in corners),
                max(x for x, _ in corners), max(y for _, y in corners))


@dataclass(frozen=True)
class CatchmentOutline:
    identifier: str
    rings: tuple[tuple[XY, ...], ...]


@dataclass(frozen=True)
class BoundaryPreview:
    outlines: tuple[CatchmentOutline, ...]
    diagnostic: str = ""
