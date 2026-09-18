"""Integration coverage for dem."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio

from highway_drainage.application.dem import plan_dem
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import DemRequest, ResourceLimitError, ResourceLimits
from highway_drainage.domain.terrain import LineRole
from highway_drainage.infrastructure.raster import GeoTiffWriter
from highway_drainage.infrastructure.surface import SurfaceBuilder, edge_key
from tests.support.dem import dem_service, plane
from tests.support.terrain_input import document, load, save


def test_confirmed_dem_replacement_preserves_previous_file_until_success(tmp_path: Path) -> None:
    request = DemRequest(plane(tmp_path), tmp_path / "replace.tif")
    dem_service().execute(request)
    before = request.output.read_bytes()
    cancel = Event()

    def stop(message: str) -> None:
        if message.startswith("Writing DEM: 1/"):
            cancel.set()

    replacement = replace(request, overwrite=True, cell_size=2)
    with pytest.raises(ImportCancelled):
        dem_service().execute(replacement, cancel, stop)
    assert request.output.read_bytes() == before
    dem_service().execute(replacement)
    with rasterio.open(request.output) as src:
        assert src.shape == (2, 2)
    assert not list(tmp_path.glob(".*.part.tif"))


def test_preserved_triangles_and_planar_geotiff(tmp_path: Path) -> None:
    dataset = plane(tmp_path)
    request = DemRequest(dataset, tmp_path / "dem.tif")
    plan = plan_dem(request)
    model = SurfaceBuilder().build(request, plan, Event())
    assert model.triangles == tuple(f.vertices for f in dataset.features)
    result = dem_service().execute(request)
    assert result.valid_cells == 16
    with rasterio.open(result.output) as src:
        x, y = np.meshgrid(np.arange(4) + 0.5, 4 - (np.arange(4) + 0.5))
        np.testing.assert_allclose(src.read(1), 10 + x + 2 * y)
        assert src.crs.to_epsg() == 32634
        assert src.transform.a == 1
        assert src.transform.e == -1
        assert tuple(src.bounds) == (0, 0, 4, 4)
        assert src.nodata == -9999
        assert src.units == ("m",)
        assert src.tags()["vertical_reference"] == "survey datum"
        assert src.tags()["surface_method"] == "preserved_faces"


def test_tiling_extent_padding_units_and_nodata(tmp_path: Path) -> None:
    request = DemRequest(
        plane(tmp_path),
        tmp_path / "feet.tif",
        cell_size=0.75,
        extent=(-1, -1, 5, 5),
        elevation_unit="ft",
        nodata=-12345,
        limits=ResourceLimits(tile_size=3),
    )
    result = dem_service().execute(request)
    with rasterio.open(result.output) as src:
        data = src.read(1, masked=True)
        assert data.mask.any()
        assert not data.mask.all()
        x, y = src.xy(2, 2)
        assert data[2, 2] == pytest.approx((10 + x + 2 * y) / 0.3048, rel=1e-6)
        assert src.units == ("ft",)
        assert src.nodata == -12345
    rounded = plan_dem(replace(request, extent=(0, 0, 4, 4)))
    assert (rounded.width, rounded.height) == (6, 6)
    assert rounded.extent == (0, -0.5, 4.5, 4)


def test_constrained_ridge_keeps_edges_and_uses_no_new_vertices(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_polyline3d(
        [(0, 0, 0), (2, 0, 10), (4, 0, 0), (4, 4, 0), (2, 4, 10), (0, 4, 0)],
        close=True,
        dxfattribs={"layer": "BOUNDARY"},
    )
    ridge = [(2, 0, 10), (2, 2, 10), (2, 4, 10)]
    doc.modelspace().add_polyline3d(ridge, dxfattribs={"layer": "RIDGE"})
    source = replace(
        save(doc, tmp_path / "ridge.dxf"),
        layer_roles=(
            ("BOUNDARY", LineRole.BOUNDARY),
            ("RIDGE", LineRole.BREAKLINE),
        ),
    )
    request = DemRequest(load(source), tmp_path / "ridge.tif")
    model = SurfaceBuilder().build(request, plan_dem(request), Event())
    vertices = {v for f in request.dataset.features for v in f.vertices}
    assert {v for t in model.triangles for v in t} == vertices
    edges = {
        edge_key(a, b)
        for t in model.triangles
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))
    }
    assert ((2, 0), (2, 2)) in edges and ((2, 2), (2, 4)) in edges
    dem_service().execute(request)
    with rasterio.open(request.output) as src:
        np.testing.assert_allclose(src.read(1), np.tile([2.5, 7.5, 7.5, 2.5], (4, 1)))


@pytest.mark.parametrize("dangling", [True, False])
def test_unsupported_breaklines_fail_without_output(tmp_path: Path, dangling: bool) -> None:
    doc = document()
    doc.modelspace().add_polyline3d(
        [(0, 0, 0), (2, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)],
        close=True,
        dxfattribs={"layer": "BOUNDARY"},
    )
    doc.modelspace().add_line(
        (2, 0, 0), (2, 2 if dangling else 4, 0), dxfattribs={"layer": "BREAK"}
    )
    source = replace(
        save(doc, tmp_path / "bad_break.dxf"),
        layer_roles=(
            ("BOUNDARY", LineRole.BOUNDARY),
            ("BREAK", LineRole.BREAKLINE),
        ),
    )
    request = DemRequest(load(source), tmp_path / "bad.tif")
    with pytest.raises(ValueError, match="Dangling|Node/split"):
        dem_service().execute(request)
    assert not request.output.exists()


def test_existing_mesh_breaklines_must_be_embedded(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 10), (4, 0, 14), (4, 4, 22)])
    line = doc.modelspace().add_line((0, 0, 10), (4, 4, 22))
    source = save(doc, tmp_path / "mesh_break.dxf", LineRole.BREAKLINE)
    dem_service().execute(DemRequest(load(source), tmp_path / "embedded.tif"))
    line.dxf.end = (3, 2, 17)
    source = save(doc, source.path, LineRole.BREAKLINE)
    with pytest.raises(ValueError, match="not an existing mesh edge"):
        dem_service().execute(DemRequest(load(source), tmp_path / "not_embedded.tif"))


def test_planar_quad_is_split_but_nonplanar_quad_is_rejected(tmp_path: Path) -> None:
    doc = document()
    face = doc.modelspace().add_3dface([(0, 0, 10), (4, 0, 14), (4, 4, 22), (0, 4, 18)])
    source = save(doc, tmp_path / "quad.dxf")
    result = dem_service().execute(DemRequest(load(source), tmp_path / "quad.tif"))
    assert result.triangle_count == 2
    face.dxf.vtx3 = (0, 4, 20)
    source = save(doc, source.path)
    with pytest.raises(ValueError, match="Nonplanar"):
        dem_service().execute(DemRequest(load(source), tmp_path / "bad_quad.tif"))


def test_overlap_is_rejected_even_if_no_sample_would_hit_it(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 1), (4, 0, 1), (0, 4, 1)])
    doc.modelspace().add_3dface([(0.1, 0.1, 2), (0.2, 0.1, 2), (0.1, 0.2, 2)])
    request = DemRequest(load(save(doc, tmp_path / "overlap.dxf")), tmp_path / "overlap.tif")
    with pytest.raises(ValueError, match="overlap"):
        dem_service().execute(request)


@pytest.mark.parametrize("resolution", [0, -1, float("nan"), float("inf")])
def test_invalid_resolution(tmp_path: Path, resolution: float) -> None:
    with pytest.raises(ValueError, match="cell size"):
        plan_dem(DemRequest(plane(tmp_path), tmp_path / "invalid.tif", resolution))


@pytest.mark.parametrize("resolution", [0.00001, 1e-300])
def test_huge_grid_diagnostic_precedes_build_and_allocation(
    tmp_path: Path, resolution: float
) -> None:
    request = DemRequest(plane(tmp_path), tmp_path / "huge.tif", resolution)
    with pytest.raises(ResourceLimitError) as exc:
        dem_service().execute(request)
    message = str(exc.value)
    assert all(word in message for word in ("cells/samples", "Resolution", "extent", "Recommended"))
    assert exc.value.plan.cells > request.limits.max_cells
    assert "Increase cell size" in message
    assert not request.output.exists()


@pytest.mark.parametrize(
    "limits,match",
    [
        (ResourceLimits(max_memory_bytes=1), "working memory"),
        (ResourceLimits(max_input_vertices=2), "count limit"),
        (ResourceLimits(max_triangles=1), "count limit"),
        (ResourceLimits(max_sample_evaluations=1), "sample evaluations"),
    ],
)
def test_resource_limits(tmp_path: Path, limits: ResourceLimits, match: str) -> None:
    request = DemRequest(plane(tmp_path), tmp_path / "limit.tif", limits=limits)
    with pytest.raises(ResourceLimitError, match=match):
        dem_service().execute(request)
    assert not list(tmp_path.glob("*.tif"))


def test_no_data_collision_nan_and_no_extrapolation(tmp_path: Path) -> None:
    dataset = plane(tmp_path)
    with pytest.raises(ValueError, match="NoData overlaps"):
        dem_service().execute(DemRequest(dataset, tmp_path / "collision.tif", nodata=15))
    request = DemRequest(dataset, tmp_path / "nan.tif", extent=(-1, -1, 5, 5), nodata=float("nan"))
    dem_service().execute(request)
    with rasterio.open(request.output) as src:
        assert np.isnan(src.nodata)
        data = src.read(1)
        assert np.isnan(data[0, 0])
        assert np.isfinite(data[2, 2])


@pytest.mark.parametrize("stage", ["Preparing DEM:", "Writing DEM:"])
def test_empty_extent_and_cancellation_leave_no_partial_output(tmp_path: Path, stage: str) -> None:
    request = DemRequest(plane(tmp_path), tmp_path / "empty.tif", extent=(100, 100, 104, 104))
    with pytest.raises(ValueError, match="No cell centres"):
        dem_service().execute(request)
    assert not list(tmp_path.glob("*.tif"))
    cancel = Event()

    def stop_after_tile(message: str) -> None:
        if message.startswith(stage) and not message.startswith("Writing DEM: 0/"):
            cancel.set()

    with pytest.raises(ImportCancelled):
        dem_service().execute(replace(request, extent=None), cancel, stop_after_tile)
    assert not list(tmp_path.glob("*.tif"))


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    request = DemRequest(plane(tmp_path), tmp_path / "existing.tif")
    request.output.write_bytes(b"keep me")
    with pytest.raises(ValueError, match="already exists"):
        dem_service().execute(request)
    assert request.output.read_bytes() == b"keep me"


def test_boundary_masks_mesh_without_retriangulating(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 10), (4, 0, 14), (4, 4, 22)])
    doc.modelspace().add_3dface([(0, 0, 10), (4, 4, 22), (0, 4, 18)])
    doc.modelspace().add_polyline3d(
        [(1, 1, 13), (3, 1, 15), (3, 3, 19), (1, 3, 17)],
        close=True,
        dxfattribs={"layer": "BOUNDARY"},
    )
    source = replace(
        save(doc, tmp_path / "clip.dxf"), layer_roles=(("BOUNDARY", LineRole.BOUNDARY),)
    )
    request = DemRequest(load(source), tmp_path / "clip.tif", extent=(0, 0, 4, 4))
    result = dem_service().execute(request)
    assert result.triangle_count == 2
    assert result.valid_cells == 4
    with rasterio.open(result.output) as src:
        mask = src.read(1, masked=True).mask
        assert mask[0, :].all() and mask[-1, :].all()
        assert not mask[1:3, 1:3].any()


def test_limit_blocks_builder_and_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Preflight must reject before invoking model construction or raster writing")

    monkeypatch.setattr(SurfaceBuilder, "build", unexpected)
    monkeypatch.setattr(GeoTiffWriter, "write", unexpected)
    request = DemRequest(plane(tmp_path), tmp_path / "oversized.tif", cell_size=0.00001)
    with pytest.raises(ResourceLimitError):
        dem_service().execute(request)


def test_shared_edge_height_discontinuity_is_rejected(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 0), (4, 0, 0), (0, 4, 0)])
    # This smaller face touches the first along part of its diagonal, without
    # sharing input vertices. A vertex-only Z check would miss the discontinuity.
    doc.modelspace().add_3dface([(1, 3, 1), (3, 1, 1), (4, 4, 1)])
    request = DemRequest(load(save(doc, tmp_path / "seam.dxf")), tmp_path / "seam.tif")
    with pytest.raises(ValueError, match="Adjacent faces"):
        dem_service().execute(request)


def test_allocation_failure_has_useful_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exhausted(*args: object, **kwargs: object) -> None:
        raise MemoryError()

    monkeypatch.setattr(SurfaceBuilder, "build", exhausted)
    request = DemRequest(plane(tmp_path), tmp_path / "failed.tif")
    with pytest.raises(ResourceLimitError, match="Allocation failed") as exc:
        dem_service().execute(request)
    assert "16 cells/samples" in str(exc.value)
    assert "Recommended action" in str(exc.value)
