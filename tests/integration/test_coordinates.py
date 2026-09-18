"""Integration coverage for coordinates."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from rasterio.transform import Affine

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import CoordinateRequest, PointLocation
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from tests.support.coordinates import candidates, raster, validate


@pytest.mark.parametrize(
    ("xy", "row", "column", "location"),
    [
        ((100.000001, 195), 1, 0, PointLocation.VALID),
        ((99.999999, 195), 1, -1, PointLocation.OUTSIDE),
        ((109.999999, 195), 1, 4, PointLocation.VALID),
        ((110.000001, 195), 1, 5, PointLocation.OUTSIDE),
        ((103, 199.999999), 0, 1, PointLocation.VALID),
        ((103, 200.000001), -1, 1, PointLocation.OUTSIDE),
        ((103, 188.000001), 3, 1, PointLocation.VALID),
        ((103, 187.999999), 4, 1, PointLocation.OUTSIDE),
        ((103, 192.5), 2, 1, PointLocation.VALID),
    ],
)
def test_near_edges_and_inside(
    tmp_path: Path, xy: tuple[float, float], row: int, column: int, location: PointLocation
) -> None:
    report = validate(raster(tmp_path), xy)
    check = report.outlets[0]
    assert (check.row, check.column, check.location) == (row, column, location)
    assert (check.point.x, check.point.y) == xy
    assert check.ordering_verified and check.roundtrip_verified
    assert report.dem.bounds == (100, 188, 110, 200)
    assert report.crs_matches and report.dem.affine_valid
    assert "Y decreases" in report.dem.y_direction


@pytest.mark.parametrize(
    ("xy", "row", "column", "state"),
    [
        ((100, 195), 1, 0, "valid"),
        ((110, 195), 1, 5, "no addressable cell"),
        ((103, 200), 0, 1, "valid"),
        ((103, 188), 4, 1, "no addressable cell"),
        ((100, 200), 0, 0, "valid"),
        ((110, 200), 0, 5, "no addressable cell"),
        ((100, 188), 4, 0, "no addressable cell"),
        ((110, 188), 4, 5, "no addressable cell"),
    ],
)
def test_exact_perimeter(
    tmp_path: Path, xy: tuple[float, float], row: int, column: int, state: str
) -> None:
    check = validate(raster(tmp_path), xy).outlets[0]
    assert check.location == PointLocation.EDGE
    assert (check.row, check.column, check.cell_state) == (row, column, state)
    assert check.on_pixel_boundary and check.edge_sides


@pytest.mark.parametrize(
    ("xy", "row", "column"),
    [
        ((102, 192.5), 2, 1),
        ((103, 194), 2, 1),
        ((102, 194), 2, 1),
    ],
)
def test_internal_grid_boundaries_use_floor(
    tmp_path: Path, xy: tuple[float, float], row: int, column: int
) -> None:
    check = validate(raster(tmp_path), xy).outlets[0]
    assert (check.row, check.column) == (row, column)
    assert check.location == PointLocation.VALID
    assert check.on_pixel_boundary and not check.edge_sides
    assert check.elevation == 11


def test_nodata_nan_and_mask(tmp_path: Path) -> None:
    report = validate(raster(tmp_path), (105, 195.5), (107, 192.5))
    assert all(c.location == PointLocation.NODATA for c in report.outlets)
    report = validate(raster(tmp_path, masked=True), (101, 198.5), (100, 198.5))
    assert report.outlets[0].location == PointLocation.NODATA
    assert report.outlets[1].location == PointLocation.EDGE
    assert all(c.cell_state == "NoData" and c.elevation is None for c in report.outlets)


@pytest.mark.parametrize("crs", [None, "EPSG:32635"])
def test_crs_missing_or_mismatch_does_not_map(tmp_path: Path, crs: str | None) -> None:
    report = validate(raster(tmp_path, crs=crs), (103, 192.5))
    assert not report.crs_matches
    assert report.outlets[0].location == PointLocation.UNVALIDATED
    assert report.outlets[0].row is None


def test_rotated_affine_uses_footprint_and_roundtrip(tmp_path: Path) -> None:
    transform = Affine(2, 1, 100, 1, -2, 200)
    inside = (105.5, 196.5)
    outside = (101.5, 195.75)
    report = validate(raster(tmp_path, transform), inside, outside)
    check = report.outlets[0]
    assert check.location == PointLocation.VALID
    assert (check.row, check.column) == (2, 1)
    assert check.ordering_verified and check.roundtrip_verified
    assert report.outlets[1].location == PointLocation.OUTSIDE
    assert any("north-up" in d for d in report.dem.diagnostics)


def test_south_up_does_not_flip_rows(tmp_path: Path) -> None:
    report = validate(raster(tmp_path, Affine(2, 0, 100, 0, 3, 188)), (103, 195.5))
    assert (report.outlets[0].row, report.outlets[0].column) == (2, 1)
    assert "row step XY=(0.0, 3.0)" in report.dem.y_direction


def test_singular_affine_and_nonfinite_points(tmp_path: Path) -> None:
    report = validate(raster(tmp_path, Affine(2, 1, 100, 4, 2, 200)), (103, 192.5))
    assert not report.dem.affine_valid
    assert report.outlets[0].location == PointLocation.UNVALIDATED
    report = validate(raster(tmp_path), (float("nan"), 192.5))
    assert report.outlets[0].location == PointLocation.UNVALIDATED


def test_cancel_and_missing_provenance(tmp_path: Path) -> None:
    request = CoordinateRequest(
        raster(tmp_path), replace(candidates((103, 192.5)), highway_source=None)
    )
    service = ValidateCoordinates(RasterCoordinateInspector())
    token = Event()
    token.set()
    with pytest.raises(ImportCancelled):
        service.execute(request, token)
    assert any("assumptions unavailable" in d for d in service.execute(request).dem.diagnostics)
