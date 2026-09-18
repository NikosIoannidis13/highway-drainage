"""Integration coverage for previews."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine
from rasterio.windows import Window

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import CatchmentResult, HydrologyResult
from highway_drainage.infrastructure.preview import RasterPreviewReader
from tests.support.coordinates import raster
from tests.support.hydrology import chain


def test_raster_snapshot_is_bounded_georeferenced_and_masks_nodata(tmp_path: Path) -> None:
    snapshot = RasterPreviewReader().raster(raster(tmp_path), Event())
    assert (snapshot.width, snapshot.height) == (5, 4)
    assert len(snapshot.rgba) == 5 * 4 * 4
    assert snapshot.rgba[(1 * 5 + 2) * 4 + 3] == 0
    assert snapshot.rgba[3] == 255
    assert snapshot.affine == (2, 0, 100, 0, -3, 200)
    assert "32634" in snapshot.information and "Sampled elevation" in snapshot.information


def mask_result(tmp_path: Path, mask: Path, count: int = 1) -> HydrologyResult:
    outlet = chain(tmp_path).prepared.outlets[0]
    return HydrologyResult(
        tmp_path,
        mask,
        mask,
        mask,
        tuple(CatchmentResult(outlet, "delineated", "", mask) for _ in range(count)),
        0,
        0,
        "m",
        (),
    )


def test_large_catchments_keep_all_outlets_and_tiny_regions_without_changing_files(
    tmp_path: Path,
) -> None:
    # The user's 32-million-cell grid, repeated for twenty outlets. Empty blocks
    # are NoData; individual contributing cells must survive display reduction.
    width, height = 8637, 3711
    path = tmp_path / "large_mask.tif"
    transform = Affine(1, 0.2, 585964, 0.1, -1, 3918843)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint8",
        crs="EPSG:2100",
        nodata=255,
        tiled=True,
        compress="deflate",
        transform=transform,
    ) as dst:
        for col, row in ((0, 0), (width - 1, height - 1)):
            dst.write(np.ones((1, 1), dtype="uint8"), 1, window=Window(col, row, 1, 1))
    before = path.read_bytes()
    result = mask_result(tmp_path, path, 20)
    preview = RasterPreviewReader().boundaries(result, Event())
    assert len(preview.outlines) == 20
    assert "Simplified" in preview.diagnostic
    assert sum(len(ring) for o in preview.outlines for ring in o.rings) <= 250_000
    for outline in preview.outlines:
        assert len(outline.rings) == 2  # Both tiny islands, no false NoData polygons.
        inverse = ~transform
        pixels = [
            (inverse.a * x + inverse.b * y + inverse.c, inverse.d * x + inverse.e * y + inverse.f)
            for ring in outline.rings
            for x, y in ring
        ]
        assert min(x for x, _ in pixels) == pytest.approx(0, abs=1e-8)
        assert max(x for x, _ in pixels) == pytest.approx(width)
        assert min(y for _, y in pixels) == pytest.approx(0, abs=1e-8)
        assert max(y for _, y in pixels) == pytest.approx(height)
    assert path.read_bytes() == before


def test_small_boundary_keeps_holes_and_vertex_budget_can_simplify(tmp_path: Path) -> None:
    path = tmp_path / "mask.tif"
    data = np.zeros((400, 400), dtype="uint8")
    data[1:10, 1:10] = 1
    data[3:7, 3:7] = 0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=400,
        height=400,
        count=1,
        dtype="uint8",
        crs="EPSG:2100",
        transform=Affine(1, 0, 100, 0, -1, 200),
    ) as dst:
        dst.write(data, 1)
    result = mask_result(tmp_path, path)
    reader = RasterPreviewReader()
    exact = reader.boundaries(result, Event())
    assert len(exact.outlines[0].rings) == 2
    assert "Full-resolution" in exact.diagnostic
    # Many isolated cells exceed the per-outlet vertex budget even on a small grid.
    data[::2, ::2] = 1
    with rasterio.open(path, "r+") as dst:
        dst.write(data, 1)
    limited = reader.boundaries(replace(result, catchments=result.catchments * 2), Event())
    assert len(limited.outlines) == 2
    assert "Simplified" in limited.diagnostic
    assert sum(len(r) for o in limited.outlines for r in o.rings) <= 250_000
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        reader.boundaries(result, cancel)
