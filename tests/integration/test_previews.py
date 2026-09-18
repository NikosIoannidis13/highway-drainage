"""Integration coverage for previews."""

from pathlib import Path
from threading import Event

from highway_drainage.infrastructure.preview import RasterPreviewReader
from tests.support.coordinates import raster


def test_raster_snapshot_is_bounded_georeferenced_and_masks_nodata(tmp_path: Path) -> None:
    snapshot = RasterPreviewReader().raster(raster(tmp_path), Event())
    assert (snapshot.width, snapshot.height) == (5, 4)
    assert len(snapshot.rgba) == 5 * 4 * 4
    assert snapshot.rgba[(1 * 5 + 2) * 4 + 3] == 0
    assert snapshot.rgba[3] == 255
    assert snapshot.affine == (2, 0, 100, 0, -3, 200)
    assert "32634" in snapshot.information and "Sampled elevation" in snapshot.information
