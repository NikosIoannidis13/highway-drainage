"""Integration coverage for hydrology."""

import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import CoordinateRequest
from highway_drainage.domain.outlets import SnapMode, SnapRequest
from tests.support.coordinates import candidates
from tests.support.hydrology import chain, service
from tests.support.outlets import service as outlet_service


def test_nested_and_shared_outlets_get_independent_full_catchments(tmp_path: Path) -> None:
    request = chain(tmp_path)
    result = service().execute(request)
    assert len(result.catchments) == 4
    assert [c.cell_count for c in result.catchments] == [3, 5, 5, 0]
    assert [c.area_m2 for c in result.catchments] == [12, 20, 20, 0]
    assert result.catchments[-1].status == "rejected"
    assert result.catchments[-1].mask is None
    assert result.catchments[1].outlet.shares_cell_with == ("2",)
    for c in result.catchments[:3]:
        assert c.mask is not None
        with rasterio.open(c.mask) as src:
            mask = src.read(1)
            assert np.count_nonzero(mask == 1) == c.cell_count == c.accumulation_cells
            assert src.crs is not None and src.crs.to_epsg() == 32634
            assert src.transform == Affine(2, 0, 100, 0, -2, 200)
            assert mask[0, 0] == 255
    with rasterio.open(result.accumulation) as src:
        assert src.read(1)[1, 1:6].tolist() == [1, 2, 3, 4, 5]
    assert result.filled_cells == 0
    manifest = json.loads((result.output / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["result"]["catchments"]) == 4
    assert "pyflwdir" in manifest["engine"]
    assert not list(tmp_path.glob(".hydrology-*"))


def test_conditioning_fills_closed_depression(tmp_path: Path) -> None:
    base = chain(tmp_path)
    path = tmp_path / "bowl.tif"
    values = np.full((5, 5), 10, dtype="float32")
    values[2, 2] = 0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=5,
        width=5,
        count=1,
        dtype="float32",
        crs="EPSG:32634",
        transform=Affine(1, 0, 100, 0, -1, 200),
        nodata=-9999,
    ) as dst:
        dst.write(values, 1)
    prepared = outlet_service().execute(
        SnapRequest(CoordinateRequest(path, candidates((102.5, 197.5))), max_distance=0)
    )
    result = service().execute(replace(base, prepared=prepared))
    assert result.filled_cells == 1 and result.maximum_fill == 10
    with rasterio.open(result.conditioned_dem) as src:
        assert src.read(1)[2, 2] == 10
    with rasterio.open(path) as src:
        assert src.read(1)[2, 2] == 0  # Source is untouched.
    assert result.catchments[0].status == "delineated"


def test_current_accumulation_threshold_rejects_without_resnapping(tmp_path: Path) -> None:
    base = chain(tmp_path)
    result = service().execute(replace(base, minimum_accumulation_cells=4))
    assert result.catchments[0].status == "rejected"
    assert result.catchments[0].accumulation_cells == 3
    assert result.catchments[1].status == "delineated"
    assert result.catchments[0].outlet == base.prepared.outlets[0]


def test_changed_outlet_elevation_rejected(tmp_path: Path) -> None:
    base = chain(tmp_path)
    with rasterio.open(base.prepared.request.coordinates.dem, "r+") as dst:
        values = dst.read(1)
        values[1, 3] = 3.5
        dst.write(values, 1)
    result = service().execute(base)
    assert result.catchments[0].status == "rejected"
    assert "changed" in result.catchments[0].diagnostic


def test_previous_area_threshold_is_preserved_against_current_model(tmp_path: Path) -> None:
    base = chain(tmp_path)
    # A prior accumulation raster selected cells using 16 m2; each current cell is 4 m2.
    prepared = replace(
        base.prepared,
        request=replace(
            base.prepared.request,
            mode=SnapMode.ACCUMULATION,
            minimum_accumulation=16,
            accumulation_units="m2",
        ),
    )
    result = service().execute(replace(base, prepared=prepared))
    assert result.catchments[0].status == "rejected"  # 3 cells = 12 m2
    assert result.catchments[1].status == "delineated"  # 5 cells = 20 m2


def test_selected_cell_becoming_nodata_is_rejected(tmp_path: Path) -> None:
    base = chain(tmp_path)
    with rasterio.open(base.prepared.request.coordinates.dem, "r+") as dst:
        values = dst.read(1)
        values[1, 3] = -9999
        dst.write(values, 1)
    result = service().execute(base)
    assert result.catchments[0].status == "rejected"
    assert result.catchments[1].cell_count == 2


def test_changed_grid_rejected_before_writing(tmp_path: Path) -> None:
    base = chain(tmp_path)
    with rasterio.open(base.prepared.request.coordinates.dem, "r+") as dst:
        dst.transform = Affine(2, 0, 101, 0, -2, 200)
    with pytest.raises(ValueError, match="grid/CRS"):
        service().execute(base)
    assert not base.output.exists()


@pytest.mark.parametrize(
    "limit", ["max_cells", "max_memory_bytes", "max_cell_visits", "max_output_bytes"]
)
def test_resource_guards(tmp_path: Path, limit: str) -> None:
    base = chain(tmp_path)
    with pytest.raises(ValueError, match="resolution=.*DEM extent=.*coarser"):
        service().execute(
            replace(
                base,
                max_cells=1 if limit == "max_cells" else base.max_cells,
                max_memory_bytes=1 if limit == "max_memory_bytes" else base.max_memory_bytes,
                max_cell_visits=1 if limit == "max_cell_visits" else base.max_cell_visits,
                max_output_bytes=1 if limit == "max_output_bytes" else base.max_output_bytes,
            )
        )
    assert not base.output.exists()


def test_cancel_after_staging_cleans_outputs(tmp_path: Path) -> None:
    base = chain(tmp_path)
    cancel = Event()

    def progress(message: str) -> None:
        if message.startswith("Delineating outlet"):
            cancel.set()

    with pytest.raises(ImportCancelled):
        service().execute(base, cancel, progress)
    assert not base.output.exists()
    assert not list(tmp_path.glob(".hydrology-*"))


def test_existing_output_not_overwritten(tmp_path: Path) -> None:
    base = chain(tmp_path)
    base.output.mkdir()
    marker = base.output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="never overwritten"):
        service().execute(base)
    assert marker.read_text(encoding="utf-8") == "keep"
