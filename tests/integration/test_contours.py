from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio

from highway_drainage.application.dem import plan_dem
from highway_drainage.domain.dem import DemRequest, ResourceLimitError, ResourceLimits
from highway_drainage.domain.terrain import LineRole, TerrainDataset
from tests.support.dem import dem_service
from tests.support.terrain_input import document, load, save


def contours(tmp_path: Path, boundary: bool = True) -> TerrainDataset:
    doc = document()
    for x in (0, 4):
        doc.modelspace().add_polyline3d([(x, 0, 10 + x), (x, 4, 10 + x)])
    source = save(doc, tmp_path / "contours.dxf", LineRole.CONTOUR)
    if not boundary:
        return load(source)
    outline = document()
    outline.modelspace().add_polyline3d([(0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)], close=True)
    return load(source, save(outline, tmp_path / "boundary.dxf", LineRole.BOUNDARY))


@pytest.mark.parametrize("spacing", [None, 1.0])
def test_contours_export_analytic_plane_and_hull_nodata(
    tmp_path: Path, spacing: float | None
) -> None:
    dataset = contours(tmp_path)
    assert not dataset.has_errors  # Boundary Z=0 is a mask, not a terrain height.
    request = DemRequest(dataset, tmp_path / "dem.tif", contour_spacing=spacing)
    result = dem_service().execute(request)
    assert result.method == "sampled_contours_unconstrained"
    assert result.valid_cells == 16
    with rasterio.open(result.output) as src:
        data = src.read(1)
        np.testing.assert_allclose(data[:, :4], np.tile([10.5, 11.5, 12.5, 13.5], (4, 1)))
        assert np.all(data[:, 4:] == src.nodata)
        assert src.crs.to_epsg() == 32634
        assert src.tags()["surface_method"] == result.method


def test_contours_require_boundary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="closed outer boundary"):
        dem_service().execute(DemRequest(contours(tmp_path, False), tmp_path / "dem.tif"))


def test_variable_contour_elevation_is_import_error(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_polyline3d([(0, 0, 10), (4, 4, 11)])
    dataset = load(save(doc, tmp_path / "bad.dxf", LineRole.CONTOUR))
    assert any(i.code == "contour_z" for i in dataset.issues)


def test_crossing_contours_are_rejected(tmp_path: Path) -> None:
    dataset = contours(tmp_path)
    feature = dataset.features[0]
    from highway_drainage.domain.terrain import Point3D

    crossing = replace(feature, vertices=(Point3D(-1, 2, 20), Point3D(5, 2, 20)))
    dataset = replace(dataset, features=(*dataset.features, crossing))
    with pytest.raises(ValueError, match="different elevations"):
        dem_service().execute(DemRequest(dataset, tmp_path / "dem.tif"))


def test_contour_sampling_budget_is_checked_before_allocation(tmp_path: Path) -> None:
    request = DemRequest(contours(tmp_path), tmp_path / "dem.tif", contour_spacing=1e-100)
    with pytest.raises(ResourceLimitError, match="sampling spacing") as exc:
        plan_dem(request)
    assert "Resolution:" in str(exc.value) and "extent" in str(exc.value)
    assert not request.output.exists()


def test_sampling_count_and_edge_filter(tmp_path: Path) -> None:
    request = DemRequest(contours(tmp_path), tmp_path / "dem.tif", contour_spacing=1)
    assert plan_dem(request).input_vertices == 14  # 8 input + 6 intermediate references.
    with pytest.raises(ResourceLimitError):
        plan_dem(replace(request, limits=ResourceLimits(max_input_vertices=13)))
    with pytest.raises(ValueError, match="No contour triangles remain"):
        dem_service().execute(replace(request, max_contour_edge=3))


def test_mixed_faces_are_not_silently_ignored(tmp_path: Path) -> None:
    dataset = contours(tmp_path)
    feature = replace(dataset.features[0], is_face=True)
    dataset = replace(dataset, features=(*dataset.features, feature))
    with pytest.raises(ValueError, match="combined with faces"):
        dem_service().execute(DemRequest(dataset, tmp_path / "dem.tif"))


def test_concave_boundary_masks_pixels(tmp_path: Path) -> None:
    from highway_drainage.domain.terrain import Point3D

    dataset = contours(tmp_path)
    boundary = replace(
        dataset.features[-1],
        vertices=tuple(
            Point3D(x, y, 0) for x, y in [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]
        ),
    )
    dataset = replace(dataset, features=(*dataset.features[:-1], boundary))
    result = dem_service().execute(DemRequest(dataset, tmp_path / "dem.tif"))
    assert result.valid_cells == 12
    with rasterio.open(result.output) as src:
        assert np.all(src.read(1)[:2, 2:] == src.nodata)
