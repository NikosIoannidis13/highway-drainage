"""Reusable synthetic builders; no test functions."""

from pathlib import Path

import numpy as np
import rasterio

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.application.outlets import SelectOutlets
from highway_drainage.domain.coordinates import CoordinateRequest
from highway_drainage.domain.outlets import SnapRequest
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from highway_drainage.infrastructure.outlets import RasterOutletSnapper
from tests.support.coordinates import candidates, raster


def service() -> SelectOutlets:
    return SelectOutlets(ValidateCoordinates(RasterCoordinateInspector()), RasterOutletSnapper())


def request(tmp_path: Path, *xy: tuple[float, float]) -> SnapRequest:
    return SnapRequest(CoordinateRequest(raster(tmp_path), candidates(*xy)), max_distance=2)


def accumulation(tmp_path: Path) -> Path:
    path = tmp_path / "flow.tif"
    with rasterio.open(tmp_path / "coordinates.tif") as dem:
        profile = dem.profile
    values = np.ones((4, 5), dtype="float32")
    values[0, 2] = 100
    values[1, 2] = 1000  # Higher flow on DEM NoData is ineligible.
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values, 1)
    return path
