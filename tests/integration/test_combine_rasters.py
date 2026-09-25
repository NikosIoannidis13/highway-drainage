from pathlib import Path
from threading import Event

import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.infrastructure.combine_rasters import combine_rasters
from tests.support.terrain_input import TRIANGLE, document, load, save


def write(path: Path, data: npt.NDArray[np.float64], transform: Affine) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype="float64",
        crs="EPSG:2100",
        transform=transform,
        nodata=-9999,
    ) as dst:
        dst.write(data, 1)


def test_primary_zero_and_precision_preserved_gaps_filled_extent_extended(tmp_path: Path) -> None:
    a, b, out = (tmp_path / name for name in ("a.tif", "b.tif", "out.tif"))
    write(a, np.array([[0.0, -9999], [123.123456789, np.nan]]), from_origin(1, 2, 1, 1))
    write(b, np.full((2, 4), 7.0), from_origin(0, 2, 1, 1))
    assert combine_rasters(a, b, out, Event(), lambda _: None) == (2, 6)
    with rasterio.open(out) as src:
        np.testing.assert_array_equal(src.read(1), [[7, 0, 7, 7], [7, 123.123456789, 7, 7]])
        assert src.transform == from_origin(0, 2, 1, 1)


def test_cancellation_keeps_existing_output_and_cleans_partial(tmp_path: Path) -> None:
    a, b, out = (tmp_path / name for name in ("a.tif", "b.tif", "out.tif"))
    for path in (a, b):
        write(path, np.ones((2, 2)), from_origin(0, 2, 1, 1))
    out.write_bytes(b"original")
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        combine_rasters(a, b, out, cancel, lambda _: None, overwrite=True)
    assert out.read_bytes() == b"original"
    assert len(list(tmp_path.iterdir())) == 3


def test_resolution_alignment_and_remaining_nodata(tmp_path: Path) -> None:
    a, b, out = (tmp_path / name for name in ("a.tif", "b.tif", "out.tif"))
    write(a, np.array([[1.0, -9999], [-9999, -9999]]), from_origin(0, 2, 1, 1))
    write(b, np.full((1, 1), 9.0), from_origin(2, 2, 2, 2))
    combine_rasters(a, b, out, Event(), lambda _: None)
    with rasterio.open(out) as src:
        data = src.read(1)
        assert src.res == (1, 1)
        assert data[0, 0] == 1
        assert np.isnan(data[0, 1])
        np.testing.assert_array_equal(data[:, 2:], 9)


@pytest.mark.parametrize(
    "points",
    [
        [(0, 0, 1), (1, 1, 2), (1, 1, 2), (1, 1, 2)],
        [(0, 0, 1), (1, 1, 2), (0, 0, 1), (0, 0, 1)],
    ],
)
def test_collapsed_faces_warn_and_valid_surface_survives(
    tmp_path: Path, points: list[tuple[float, float, float]],
) -> None:
    doc = document()
    doc.modelspace().add_3dface(TRIANGLE)
    face = doc.modelspace().add_3dface(points)
    result = load(save(doc, tmp_path / "terrain.dxf"))
    assert not result.has_errors
    assert len(result.features) == 1
    issue = next(i for i in result.issues if i.code == "collapsed_face")
    assert issue.severity == "warning"
    assert issue.reference.handle == face.dxf.handle
