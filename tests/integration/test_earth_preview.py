"""Inspect local KMZ contents without opening Google Earth or accessing the network."""

from dataclasses import replace
from pathlib import Path
from threading import Event
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

import numpy as np
import pytest
from pyproj import Transformer
from rasterio.io import MemoryFile

from highway_drainage.application.earth_preview import EarthPreview
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import CadLine
from highway_drainage.domain.preview import BoundaryPreview, CatchmentOutline
from highway_drainage.infrastructure.earth_preview import KmzPreviewWriter
from highway_drainage.infrastructure.preview import RasterPreviewReader
from tests.support.coordinates import candidates
from tests.support.hydrology import chain

NS = {"k": "http://www.opengis.net/kml/2.2"}


def test_kmz_geographic_coordinates_layers_holes_and_escaping(tmp_path: Path) -> None:
    crossings = replace(candidates((590000, 3917000)), crs_wkt="EPSG:2100")
    reference = replace(crossings.points[0].culvert, layer="Road & <survey>")
    line = CadLine(reference, ((590000, 3917000), (590100, 3917100)))
    crossings = replace(crossings, highways=(line,), culverts=(line,))
    outer = (
        (590000.0, 3917000.0),
        (590100.0, 3917000.0),
        (590100.0, 3917100.0),
        (590000.0, 3917100.0),
        (590000.0, 3917000.0),
    )
    hole = tuple((590020 + (x - 590000) / 5, 3917020 + (y - 3917000) / 5) for x, y in outer)
    island = tuple((x + 200, y) for x, y in outer)
    boundaries = BoundaryPreview(
        (CatchmentOutline("Basin 1", (outer, hole, island)),), "Simplified display"
    )
    path = tmp_path / "preview.kmz"
    KmzPreviewWriter().write(
        EarthPreview("EPSG:2100", crossings, boundaries=boundaries), path, Event()
    )
    with ZipFile(path) as archive:
        assert archive.namelist() == ["doc.kml"]
        root = fromstring(archive.read("doc.kml"))
    folders = root.findall("k:Document/k:Folder/k:name", NS)
    assert [node.text for node in folders] == [
        "Highway",
        "Culverts",
        "Geometric crossings",
        "Catchments",
    ]
    name = root.findtext(".//k:Placemark/k:name", namespaces=NS)
    assert name is not None and "Road & <survey>" in name
    point = root.findtext(".//k:Point/k:coordinates", namespaces=NS)
    assert point is not None
    lon, lat, z = map(float, point.split(","))
    expected = Transformer.from_crs(2100, 4326, always_xy=True).transform(590000, 3917000)
    assert (lon, lat) == pytest.approx(expected, abs=1e-8)
    assert z == 0
    assert len(root.findall(".//k:Polygon", NS)) == 2
    assert len(root.findall(".//k:innerBoundaryIs", NS)) == 1
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        KmzPreviewWriter().write(EarthPreview("EPSG:2100", crossings), path, Event())
    assert path.read_bytes() == before
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        KmzPreviewWriter().write(EarthPreview("EPSG:2100", crossings), path, cancel, overwrite=True)
    assert path.read_bytes() == before
    KmzPreviewWriter().write(EarthPreview("EPSG:2100", crossings), path, Event(), overwrite=True)
    with ZipFile(path) as archive:
        root = fromstring(archive.read("doc.kml"))
    assert not root.findall(".//k:Polygon", NS)


@pytest.mark.parametrize("with_raster", [False, True])
def test_kmz_uses_cached_raster_and_preserves_original_and_snapped_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_raster: bool
) -> None:
    prepared = chain(tmp_path).prepared
    snapshot = RasterPreviewReader().raster(prepared.request.coordinates.dem, Event())

    def no_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("Export must not reread the DEM")

    monkeypatch.setattr("rasterio.open", no_read)
    path = tmp_path / "cached.kmz"
    KmzPreviewWriter().write(
        EarthPreview(
            "EPSG:32634",
            prepared.validation.crossings,
            prepared,
            raster=snapshot if with_raster else None,
        ),
        path,
        Event(),
    )
    with ZipFile(path) as archive:
        root = fromstring(archive.read("doc.kml"))
        assert len(root.findall(".//k:Point", NS)) == 7  # Four original, three accepted.
        assert ("terrain.png" in archive.namelist()) == with_raster
        if with_raster:
            assert root.findtext(".//k:GroundOverlay/k:Icon/k:href", namespaces=NS) == "terrain.png"
            with MemoryFile(archive.read("terrain.png")) as memory, memory.open() as src:
                assert src.count == 4 and src.width <= 768 and src.height <= 768
                assert np.any(src.read(4) == 0)
                assert np.any(src.read(4) > 0)


def test_kmz_cancel_and_mismatched_crs_do_not_publish(tmp_path: Path) -> None:
    writer = KmzPreviewWriter()
    path = tmp_path / "bad.kmz"
    snapshot = EarthPreview("EPSG:2100", candidates((100, 200)))
    with pytest.raises(ValueError, match="CRS differ"):
        writer.write(snapshot, path, Event())
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        writer.write(snapshot, path, cancel)
    assert not path.exists() and not list(tmp_path.glob("*.part.kmz"))
