"""Integration coverage for outlets."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import PointLocation
from highway_drainage.domain.outlets import SnapMode
from tests.support.outlets import accumulation, request, service


def test_nearest_cell_preserves_geometric_and_containing_pixel(tmp_path: Path) -> None:
    result = service().execute(request(tmp_path, (105, 195.5)))
    outlet = result.outlets[0]
    assert (outlet.original.point.x, outlet.original.point.y) == (105, 195.5)
    assert (outlet.original.row, outlet.original.column) == (1, 2)
    assert outlet.original.location == PointLocation.NODATA
    assert outlet.pour_point is not None
    assert (outlet.pour_point.row, outlet.pour_point.column) == (1, 1)
    assert (outlet.pour_point.x, outlet.pour_point.y, outlet.pour_point.distance) == (103, 195.5, 2)
    assert outlet.status == "provisional DEM-valid"


@pytest.mark.parametrize("distance", [0, 1.999999])
def test_distance_cap_rejects_without_fallback(tmp_path: Path, distance: float) -> None:
    result = service().execute(replace(request(tmp_path, (105, 195.5)), max_distance=distance))
    assert result.outlets[0].pour_point is None
    assert result.outlets[0].status == "rejected"


def test_zero_radius_accepts_only_exact_valid_center(tmp_path: Path) -> None:
    result = service().execute(replace(request(tmp_path, (103, 195.5)), max_distance=0))
    assert result.outlets[0].pour_point is not None
    assert result.outlets[0].pour_point.distance == 0


def test_outside_point_may_snap_but_is_not_clamped(tmp_path: Path) -> None:
    result = service().execute(request(tmp_path, (99, 195.5), (90, 195.5)))
    first, second = result.outlets
    assert first.original.location == PointLocation.OUTSIDE
    assert first.original.column == -1
    assert first.pour_point is not None and first.pour_point.x == 101
    assert first.pour_point.distance == 2
    assert second.pour_point is None


def test_accumulation_ranking_and_threshold(tmp_path: Path) -> None:
    base = request(tmp_path, (105, 195.5))
    base = replace(
        base,
        mode=SnapMode.ACCUMULATION,
        accumulation=accumulation(tmp_path),
        minimum_accumulation=50,
        max_distance=3,
    )
    selected = service().execute(base).outlets[0]
    assert selected.status == "accumulation-qualified"
    assert selected.pour_point is not None
    assert (selected.pour_point.row, selected.pour_point.column) == (0, 2)
    assert selected.pour_point.accumulation == 100 and selected.pour_point.distance == 3
    assert service().execute(replace(base, minimum_accumulation=101)).outlets[0].pour_point is None
    assert service().execute(replace(base, max_distance=2.99)).outlets[0].pour_point is None


def test_masks_and_nonfinite_accumulation_are_excluded(tmp_path: Path) -> None:
    base = request(tmp_path, (105, 195.5))
    flow = accumulation(tmp_path)
    with rasterio.open(flow, "r+") as dst:
        values = np.full((4, 5), np.nan, dtype="float32")
        values[0, 2] = 100
        dst.write(values, 1)
        mask = np.full((4, 5), 255, dtype="uint8")
        mask[0, 2] = 0
        dst.write_mask(mask)
    result = service().execute(
        replace(base, mode=SnapMode.ACCUMULATION, accumulation=flow, max_distance=5)
    )
    assert result.outlets[0].pour_point is None


def test_dem_mask_no_valid_neighbors_and_circular_radius(tmp_path: Path) -> None:
    base = request(tmp_path, (103, 195.5))
    with rasterio.open(base.coordinates.dem, "r+") as dst:
        mask = np.zeros((4, 5), dtype="uint8")
        mask[0, 2] = 255  # Center delta=(2,3), distance sqrt(13)>3, inside square window.
        dst.write_mask(mask)
    result = service().execute(replace(base, max_distance=3))
    assert result.outlets[0].pour_point is None


def test_shared_cells_keep_identities(tmp_path: Path) -> None:
    result = service().execute(request(tmp_path, (103, 195.5), (103.1, 195.5)))
    assert result.outlets[0].shares_cell_with == ("1",)
    assert result.outlets[1].shares_cell_with == ("0",)


def test_misaligned_accumulation_is_rejected(tmp_path: Path) -> None:
    base = request(tmp_path, (103, 195.5))
    flow = accumulation(tmp_path)
    with rasterio.open(flow, "r+") as dst:
        dst.transform = Affine(2, 0, 100.5, 0, -3, 200)
    with pytest.raises(ValueError, match="exact CRS, affine and dimensions"):
        service().execute(replace(base, accumulation=flow, mode=SnapMode.ACCUMULATION))


def test_crs_mismatch_is_rejected_per_point(tmp_path: Path) -> None:
    base = request(tmp_path, (103, 195.5))
    crossings = replace(base.coordinates.crossings, crs_wkt="EPSG:32635")
    result = service().execute(
        replace(base, coordinates=replace(base.coordinates, crossings=crossings))
    )
    assert result.outlets[0].pour_point is None
    assert result.outlets[0].original.location == PointLocation.UNVALIDATED


def test_resource_limits_and_cancellation(tmp_path: Path) -> None:
    base = request(tmp_path, (103, 195.5))
    with pytest.raises(ValueError, match="resolution=.*DEM extent=.*Reduce"):
        service().execute(replace(base, max_window_cells=1))
    with pytest.raises(ValueError, match="cumulative"):
        service().execute(replace(base, max_total_cells=1))
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        service().execute(base, cancel)


@pytest.mark.parametrize("distance", [-1, float("nan"), float("inf")])
def test_invalid_settings(tmp_path: Path, distance: float) -> None:
    with pytest.raises(ValueError, match="distance"):
        service().execute(replace(request(tmp_path, (103, 195.5)), max_distance=distance))
