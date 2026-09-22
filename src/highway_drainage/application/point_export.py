"""Build point exports without changing the selected coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from highway_drainage.domain.crossings import CadLine, CrossingResult, LineSource
from highway_drainage.domain.outlets import SnapResult


@dataclass(frozen=True)
class ExportPoint:
    identifier: str
    kind: str
    x: float
    y: float
    status: str
    original_x: float
    original_y: float
    elevation: float | None = None
    distance: float | None = None
    accumulation: float | None = None
    accumulation_units: str = ""


@dataclass(frozen=True)
class PointExport:
    path: Path
    crs: str
    points: tuple[ExportPoint, ...]
    overwrite: bool = False
    skipped: int = 0

    @property
    def count(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class LineExport:
    path: Path
    crs: str
    lines: tuple[CadLine, ...]
    kind: str
    overwrite: bool = False
    skipped: int = 0

    @property
    def count(self) -> int:
        return len(self.lines)


class PointWriter(Protocol):
    def write(
        self, request: PointExport | LineExport | DirectLineExport, cancel: Event
    ) -> PointExport | LineExport: ...


@dataclass(frozen=True)
class DirectLineExport:
    path: Path
    source: LineSource
    crs: str
    kind: str
    tolerance: float
    overwrite: bool = False


def line_export(result: CrossingResult, path: Path, *, culverts: bool) -> LineExport:
    return LineExport(
        path,
        result.crs_wkt,
        result.culverts if culverts else result.highways,
        "culvert" if culverts else "highway",
    )


def crossing_export(result: CrossingResult, path: Path) -> PointExport:
    return PointExport(
        path,
        result.crs_wkt,
        tuple(
            ExportPoint(p.identifier, "crossing", p.x, p.y, "candidate", p.x, p.y)
            for p in result.points
        ),
    )


def outlet_export(result: SnapResult, path: Path) -> PointExport:
    points = tuple(
        ExportPoint(
            o.original.point.identifier,
            "outlet",
            p.x,
            p.y,
            o.status,
            o.original.point.x,
            o.original.point.y,
            p.elevation,
            p.distance,
            p.accumulation,
            result.request.accumulation_units if p.accumulation is not None else "",
        )
        for o in result.outlets
        if o.status != "rejected" and (p := o.pour_point) is not None
    )
    return PointExport(
        path, result.validation.dem.crs, points, skipped=len(result.outlets) - len(points)
    )


SHAPEFILE_SUFFIXES = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")
