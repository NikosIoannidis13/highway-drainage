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
from tests.support.combined_dem import combined_sources
from tests.support.dem import dem_service
from tests.support.terrain_input import document, load, save


@pytest.mark.parametrize("spacing", [None, 1.0])
def test_combined_plan_counts_only_samples_for_fallback_triangles(
    tmp_path: Path, spacing: float | None,
) -> None:
    sources = combined_sources(tmp_path)
    request = DemRequest(
        load(*sources), tmp_path / "combined.tif", surface_mode="faces_with_polyline_gaps",
        sample_coverage="convex_hull", contour_spacing=spacing,
    )
    combined = plan_dem(request)
    face_plan, sample_plan = (
        plan_dem(replace(request, dataset=load(source), surface_mode="single"))
        for source in sources
    )
    # Building both surfaces together must not invent triangles from face vertices.
    assert combined.estimated_triangles == (
        face_plan.estimated_triangles + sample_plan.estimated_triangles
    )
    assert combined.input_vertices == face_plan.input_vertices + sample_plan.input_vertices
    limited = replace(request, limits=replace(
        request.limits, max_memory_bytes=combined.estimated_memory_bytes,
    ))
    assert dem_service().execute(limited).valid_cells == 16
    with pytest.raises(ResourceLimitError, match="working memory"):
        plan_dem(replace(limited, limits=replace(
            limited.limits, max_memory_bytes=combined.estimated_memory_bytes - 1,
        )))


def test_clipping_boundary_does_not_generate_fallback_triangles(tmp_path: Path) -> None:
    sources = combined_sources(tmp_path)
    request = DemRequest(
        load(*sources), tmp_path / "combined.tif", surface_mode="faces_with_polyline_gaps",
    )
    boundary = document()
    boundary.modelspace().add_polyline3d(
        [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)], close=True,
    )
    bounded = replace(request, dataset=load(
        *sources, save(boundary, tmp_path / "boundary.dxf", LineRole.BOUNDARY),
    ))
    assert plan_dem(bounded).estimated_triangles == plan_dem(request).estimated_triangles
    assert plan_dem(bounded).input_vertices == plan_dem(request).input_vertices + 4


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("contours", [False, True])
def test_dxf_priority_export_preserves_zero_and_fills_only_gaps(
    tmp_path: Path, reverse: bool, contours: bool,
) -> None:
    sources = combined_sources(tmp_path, contours=contours)
    dataset = load(*(tuple(reversed(sources)) if reverse else sources))
    assert not dataset.has_errors
    assert any(i.code == "surface_z_difference" for i in dataset.issues)
    request = DemRequest(
        dataset, tmp_path / "combined_dem.tif", extent=(-1, -1, 5, 5),
        surface_mode="faces_with_polyline_gaps", sample_coverage="convex_hull",
        elevation_unit="ft", nodata=-12345, limits=ResourceLimits(tile_size=2),
    )
    result = dem_service().execute(request)
    assert result.valid_cells == 16
    assert result.method == "faces_with_polyline_gaps"
    with rasterio.open(result.output) as src:
        values = src.read(1, masked=True)
        assert values.mask.sum() == 20
        x, y = np.meshgrid(np.arange(4) + 0.5, 4 - (np.arange(4) + 0.5))
        expected = np.where(x < 2, 0, (100 + (0 if contours else x) + 2 * y) / 0.3048)
        np.testing.assert_allclose(values[1:5, 1:5], expected, rtol=1e-6)
        assert src.tags()["primary_cells"] == "8"
        assert src.tags()["gap_filler_cells"] == "8"
        assert src.units == ("ft",)
        assert src.nodata == -12345
    assert not list(tmp_path.glob(".*.part.tif"))
    with pytest.raises(ValueError, match="differ in elevation"):
        plan_dem(replace(request, surface_mode="single"))


def test_internal_sample_conflicts_still_block_even_after_cross_surface_difference(
    tmp_path: Path,
) -> None:
    sources = combined_sources(tmp_path)
    extra = document()
    extra.modelspace().add_polyline3d([(0, 0, 101), (4, 0, 104)])
    source = save(extra, tmp_path / "conflict.dxf", LineRole.TERRAIN_SAMPLES)
    dataset = load(*sources, source)
    assert any(i.code == "z_conflict" and i.severity == "error" for i in dataset.issues)
    with pytest.raises(ValueError, match="import errors"):
        dem_service().execute(DemRequest(
            dataset, tmp_path / "blocked.tif", surface_mode="faces_with_polyline_gaps",
        ))
    assert not (tmp_path / "blocked.tif").exists()


def test_combined_boundary_clips_both_surfaces_ignoring_boundary_z(tmp_path: Path) -> None:
    sources = combined_sources(tmp_path)
    boundary = document()
    boundary.modelspace().add_polyline3d(
        [(0, 0, 999), (3, 0, 999), (3, 3, 999), (0, 3, 999)], close=True,
    )
    dataset = load(*sources, save(boundary, tmp_path / "boundary.dxf", LineRole.BOUNDARY))
    result = dem_service().execute(DemRequest(
        dataset, tmp_path / "bounded.tif", surface_mode="faces_with_polyline_gaps",
        extent=(-1, -1, 5, 5),
    ))
    assert result.valid_cells == 9


def test_combined_limits_cancellation_and_missing_source(tmp_path: Path) -> None:
    sources = combined_sources(tmp_path)
    request = DemRequest(
        load(*sources), tmp_path / "combined.tif", surface_mode="faces_with_polyline_gaps",
        sample_coverage="convex_hull", limits=ResourceLimits(tile_size=2),
    )
    with pytest.raises(ResourceLimitError, match="count limit"):
        plan_dem(replace(request, limits=ResourceLimits(max_triangles=3)))
    with pytest.raises(ValueError, match="both 3D faces"):
        dem_service().execute(replace(request, dataset=load(sources[0])))
    with pytest.raises(ValueError, match="outer boundary"):
        dem_service().execute(replace(request, sample_coverage="boundary"))
    request.output.write_bytes(b"existing output")
    cancel = Event()

    def stop(message: str) -> None:
        if message.startswith("Writing DEM: 1/"):
            cancel.set()

    with pytest.raises(ImportCancelled):
        dem_service().execute(replace(request, overwrite=True), cancel, stop)
    assert request.output.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".*.part.tif"))
