"""Reusable synthetic builders; no test functions."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.domain.coordinates import CoordinateReport, CoordinateRequest
from highway_drainage.domain.crossings import (
    CadReference,
    CrossingPoint,
    CrossingResult,
    LineSource,
    PointRole,
)
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector


def candidates(*xy: tuple[float, float]) -> CrossingResult:
    reference = CadReference(Path("culverts.dxf"), "A1", "culverts", "LINE")
    return CrossingResult(
        "EPSG:32634",
        (),
        (),
        tuple(
            CrossingPoint(str(i), x, y, reference, (), False, False) for i, (x, y) in enumerate(xy)
        ),
        (),
        0.05,
        LineSource(Path("highway.dxf"), "EPSG:32634"),
        LineSource(reference.path, "EPSG:32634"),
    )


def as_inlets(result: CrossingResult) -> CrossingResult:
    """Fixture for workflows whose inlet locations have already been reviewed."""
    return replace(result, points=tuple(replace(p, role=PointRole.INLET) for p in result.points))


DEFAULT_TRANSFORM = Affine(2, 0, 100, 0, -3, 200)


def raster(
    tmp_path: Path,
    transform: Affine = DEFAULT_TRANSFORM,
    crs: str | None = "EPSG:32634",
    masked: bool = False,
) -> Path:
    path = tmp_path / "coordinates.tif"
    values = np.arange(20, dtype="float32").reshape(4, 5)
    values[1, 2] = -9999
    values[2, 3] = np.nan
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=5,
        height=4,
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=-9999,
    ) as dst:
        dst.write(values, 1)
        if masked:
            mask = np.full((4, 5), 255, dtype="uint8")
            mask[0, 0] = 0
            dst.write_mask(mask)
    return path


def validate(path: Path, *xy: tuple[float, float]) -> CoordinateReport:
    return ValidateCoordinates(RasterCoordinateInspector()).execute(
        CoordinateRequest(path, candidates(*xy))
    )
