"""Reusable synthetic builders; no test functions."""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine

from highway_drainage.application.hydrology import DelineateCatchments
from highway_drainage.domain.coordinates import CoordinateRequest
from highway_drainage.domain.hydrology import HydrologyRequest
from highway_drainage.domain.outlets import SnapRequest
from highway_drainage.infrastructure.hydrology import PyFlwdirHydrology
from tests.support.coordinates import as_inlets, candidates
from tests.support.outlets import service as outlet_service


def service() -> DelineateCatchments:
    return DelineateCatchments(PyFlwdirHydrology())


def chain(tmp_path: Path) -> HydrologyRequest:
    path = tmp_path / "chain.tif"
    data = np.full((3, 7), -9999, dtype="float32")
    data[1, 1:6] = [5, 4, 3, 2, 1]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=3,
        width=7,
        count=1,
        dtype="float32",
        crs="EPSG:32634",
        transform=Affine(2, 0, 100, 0, -2, 200),
        nodata=-9999,
    ) as dst:
        dst.write(data, 1)
        dst.set_band_unit(1, "m")
    # Channel centers: upstream/middle/downstream; duplicate downstream; rejected far outside.
    points = as_inlets(candidates((107, 197), (111, 197), (111, 197), (999, 999)))
    prepared = outlet_service().execute(
        SnapRequest(CoordinateRequest(path, points), max_distance=0)
    )
    return HydrologyRequest(prepared, tmp_path / "catchments")
