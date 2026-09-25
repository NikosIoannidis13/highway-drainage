"""Midpoint sampling is local, independent of face order and excludes the gap filler."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio

from highway_drainage.domain.dem import DemRequest, ResourceLimits
from highway_drainage.domain.terrain import FaceAuditOptions, TerrainDataset
from highway_drainage.infrastructure.face_audit import FaceAuditor
from tests.support.combined_dem import combined_sources
from tests.support.dem import dem_service
from tests.support.face_audit import overlapping
from tests.support.terrain_input import document, load, save


def midpoint(dataset: TerrainDataset) -> TerrainDataset:
    return FaceAuditor().audit(
        dataset, FaceAuditOptions(overlap_policy="midpoint"), Event(), lambda _: None,
    )


def test_partial_overlap_changes_only_shared_cells(tmp_path: Path) -> None:
    dataset = midpoint(overlapping(tmp_path, 6))
    assert dataset.face_audit is not None and not dataset.face_audit.blocked
    result = dem_service().execute(DemRequest(dataset, tmp_path / "midpoint.tif"))
    with rasterio.open(result.output) as src:
        values = list(src.sample([(0.5, 0.5), (1.5, 0.5), (4.5, 0.5)]))
        np.testing.assert_allclose(np.array(values).ravel(), [11.5, 15.5, 21.5])
        assert src.tags()["face_overlap_policy"] == "midpoint"
    assert result.method == "midpoint_faces"


@pytest.mark.parametrize("reverse,tile", [(False, 2), (True, 256)])
def test_three_faces_use_extremes_without_double_weighting_quad_diagonals(
    tmp_path: Path, reverse: bool, tile: int,
) -> None:
    doc = document()
    for z in ([10, 2, 0] if reverse else [0, 2, 10]):
        doc.modelspace().add_3dface([(0, 0, z), (4, 0, z), (4, 4, z), (0, 4, z)])
    dataset = midpoint(load(save(doc, tmp_path / "three.dxf")))
    result = dem_service().execute(DemRequest(
        dataset, tmp_path / "three.tif", limits=ResourceLimits(tile_size=tile),
    ))
    with rasterio.open(result.output) as src:
        np.testing.assert_array_equal(src.read(1), np.full((4, 4), 5))


def test_polyline_gap_filler_never_enters_face_midpoint(tmp_path: Path) -> None:
    dataset = load(*combined_sources(tmp_path))
    extra = tuple(replace(
        f, vertices=tuple(replace(v, z=v.z + 10) for v in f.vertices),
        reference=replace(f.reference, handle=f.reference.handle + "-upper"),
    ) for f in dataset.features if f.is_face)
    dataset = midpoint(replace(dataset, features=dataset.features + extra))
    result = dem_service().execute(DemRequest(
        dataset, tmp_path / "combined.tif", surface_mode="faces_with_polyline_gaps",
        sample_coverage="convex_hull", limits=ResourceLimits(tile_size=2),
    ))
    with rasterio.open(result.output) as src:
        data = src.read(1)
        np.testing.assert_array_equal(data[:, :2], 5)
        assert (data[:, 2:] > 100).all()
        assert src.tags()["primary_cells"] == "8"
        assert src.tags()["gap_filler_cells"] == "8"


def test_midpoint_does_not_bypass_nonplanar_or_incomplete_audits(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 0), (4, 0, 0), (4, 4, 5), (0, 4, 0)])
    dataset = midpoint(load(save(doc, tmp_path / "bad.dxf")))
    assert dataset.face_audit is not None and dataset.face_audit.blocked
    with pytest.raises(ValueError, match="Nonplanar"):
        dem_service().execute(DemRequest(dataset, tmp_path / "bad.tif"))
    dataset = FaceAuditor(max_checks=0).audit(
        overlapping(tmp_path, 6), FaceAuditOptions(overlap_policy="midpoint"),
        Event(), lambda _: None,
    )
    assert dataset.face_audit is not None and dataset.face_audit.blocked
