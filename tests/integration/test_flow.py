"""Flow grids can be generated before any outlet selection."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from highway_drainage.application.hydrology import GenerateFlow
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import FlowRequest
from highway_drainage.infrastructure.hydrology import PyFlwdirHydrology


def flow_request(tmp_path: Path) -> FlowRequest:
    dem = tmp_path / "dem.tif"
    data = np.full((3, 7), -9999, dtype="float32")
    data[1, 1:6] = [5, 4, 3, 2, 1]
    with rasterio.open(
        dem, "w", driver="GTiff", height=3, width=7, count=1, dtype="float32",
        crs="EPSG:2100", transform=Affine(2, 0, 100, 0, -2, 200), nodata=-9999,
    ) as dst:
        dst.write(data, 1)
        dst.set_band_unit(1, "m")
    return FlowRequest(dem, tmp_path / "flow")


def test_flow_without_outlets_produces_aligned_grids(tmp_path: Path) -> None:
    request = flow_request(tmp_path)
    original = request.dem.read_bytes()
    result = GenerateFlow(PyFlwdirHydrology()).execute(request)
    assert result.catchments == ()
    assert not list(result.output.glob("catchment_*.tif"))
    with rasterio.open(request.dem) as dem:
        for path in (result.conditioned_dem, result.flow_direction, result.accumulation):
            with rasterio.open(path) as grid:
                assert (grid.crs, grid.transform, grid.shape) == (dem.crs, dem.transform, dem.shape)
    with rasterio.open(result.accumulation) as grid:
        assert grid.units == ("cells",)
        assert grid.read(1)[1, 1:6].tolist() == [1, 2, 3, 4, 5]
        assert grid.read(1, masked=True).mask[0, 0]
    assert request.dem.read_bytes() == original


def test_flow_budget_failure_does_not_publish(tmp_path: Path) -> None:
    request = replace(flow_request(tmp_path), max_cells=1)
    with pytest.raises(ValueError, match="Exceeded limits"):
        GenerateFlow(PyFlwdirHydrology()).execute(request)
    assert not request.output.exists()


def test_flow_cancel_and_overwrite_preserve_existing_results(tmp_path: Path) -> None:
    request = flow_request(tmp_path)
    service = GenerateFlow(PyFlwdirHydrology())
    result = service.execute(request)
    before = result.accumulation.read_bytes()
    with pytest.raises(ValueError, match="confirmation"):
        service.execute(request)
    cancel = Event()

    def stop(message: str) -> None:
        if "Calculating upstream" in message:
            cancel.set()

    with pytest.raises(ImportCancelled):
        service.execute(replace(request, overwrite=True), cancel, stop)
    assert result.accumulation.read_bytes() == before


def test_flow_rejects_non_square_grid(tmp_path: Path) -> None:
    request = flow_request(tmp_path)
    with rasterio.open(request.dem, "r+") as dem:
        dem.transform = Affine(2, 0, 100, 0, -3, 200)
    with pytest.raises(ValueError, match="square cells"):
        GenerateFlow(PyFlwdirHydrology()).execute(request)
    assert not request.output.exists()
