from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio

from highway_drainage.application.dem import plan_dem
from highway_drainage.domain.dem import DemRequest, ResourceLimitError
from highway_drainage.domain.terrain import LineRole
from highway_drainage.infrastructure.surface import SurfaceBuilder
from tests.support.dem import dem_service
from tests.support.terrain_input import document, load, save


@pytest.mark.parametrize("spacing", [None, 1.0])
def test_varying_xyz_vertices_generate_plane_without_boundary(
    tmp_path: Path, spacing: float | None
) -> None:
    doc = document()
    for x in (0, 4):
        doc.modelspace().add_polyline3d([(x, 0, 10 + x), (x, 4, 14 + x)])
    dataset = load(save(doc, tmp_path / "xyz.dxf", LineRole.TERRAIN_SAMPLES))
    assert not dataset.has_errors
    request = DemRequest(
        dataset, tmp_path / "dem.tif", contour_spacing=spacing, sample_coverage="convex_hull"
    )
    model = SurfaceBuilder().build(request, plan_dem(request), Event())
    assert model.boundary is None
    assert {v for f in dataset.features for v in f.vertices}.issubset(
        {v for t in model.triangles for v in t}
    )
    result = dem_service().execute(request)
    assert result.method == "sampled_xyz_unconstrained"
    with rasterio.open(result.output) as src:
        expected = 10 + np.arange(0.5, 4)[None, :] + np.arange(3.5, 0, -1)[:, None]
        np.testing.assert_allclose(src.read(1), expected)
    with pytest.raises(ValueError, match="closed outer boundary"):
        dem_service().execute(
            replace(request, output=tmp_path / "other.tif", sample_coverage="boundary")
        )
    with pytest.raises(ResourceLimitError):
        plan_dem(replace(request, contour_spacing=1e-100))


def test_flat_xyz_surface_is_valid(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_polyline3d([(0, 0, 10), (4, 0, 10), (0, 4, 10)])
    dataset = load(save(doc, tmp_path / "flat.dxf", LineRole.TERRAIN_SAMPLES))
    result = dem_service().execute(
        DemRequest(dataset, tmp_path / "flat.tif", sample_coverage="convex_hull")
    )
    with rasterio.open(result.output) as src:
        data = src.read(1, masked=True)
        assert np.all(data.compressed() == 10)
        assert data.mask[0, -1]  # Outside the triangular sample hull.


def test_xyz_duplicate_xy_conflicting_z_is_rejected(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_polyline3d([(0, 0, 10), (4, 0, 14)])
    doc.modelspace().add_polyline3d([(0, 0, 11), (4, 4, 18)])
    dataset = load(save(doc, tmp_path / "conflict.dxf", LineRole.TERRAIN_SAMPLES))
    assert any(issue.code == "z_conflict" for issue in dataset.issues)
