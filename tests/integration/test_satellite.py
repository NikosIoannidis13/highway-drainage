import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from pyproj import Transformer

from highway_drainage.application.earth_preview import EarthPreview
from highway_drainage.application.satellite import SatelliteRequest
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.preview import BoundaryPreview, CatchmentOutline
from highway_drainage.infrastructure.preview import RasterPreviewReader
from highway_drainage.infrastructure.satellite import GoogleSatelliteBuilder
from tests.support.hydrology import chain


def test_satellite_cached_data_coordinates_holes_and_optional_raster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = chain(tmp_path).prepared
    raster = RasterPreviewReader().raster(prepared.request.coordinates.dem, Event())
    boundary = BoundaryPreview(
        (
            CatchmentOutline(
                "catchment",
                (
                    (
                        (100.0, 200.0),
                        (114.0, 200.0),
                        (114.0, 194.0),
                        (100.0, 194.0),
                        (100.0, 200.0),
                    ),
                    (
                        (104.0, 199.0),
                        (106.0, 199.0),
                        (106.0, 197.0),
                        (104.0, 197.0),
                        (104.0, 199.0),
                    ),
                ),
            ),
        ),
        "Simplified display boundary",
    )
    preview = EarthPreview("EPSG:32634", prepared.validation.crossings, prepared, boundary)
    request = SatelliteRequest(preview, raster)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Satellite rendering must not read source rasters")

    monkeypatch.setattr("rasterio.open", forbidden)
    builder = GoogleSatelliteBuilder()
    payload = json.loads(builder.build(request, Event()))
    assert payload["image"] is None
    points = [f for f in payload["geojson"]["features"] if f["properties"]["kind"] == "crossing"]
    expected = Transformer.from_crs(32634, 4326, always_xy=True).transform(107, 197)
    assert points[0]["geometry"]["coordinates"] == pytest.approx(expected)
    catchment = payload["geojson"]["features"][-1]["geometry"]
    assert catchment["type"] == "Polygon" and len(catchment["coordinates"]) == 2
    assert payload["diagnostic"] == boundary.diagnostic
    with_image = json.loads(
        builder.build(replace(request, preview=replace(preview, raster=raster)), Event())
    )
    assert with_image["image"]["url"].startswith("data:image/png;base64,")
    assert len(with_image["image"]["bounds"]) == 4
    footprint = json.loads(
        builder.build(SatelliteRequest(EarthPreview("EPSG:32634"), raster), Event())
    )
    assert footprint["bounds"] is not None and footprint["image"] is None
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        builder.build(request, cancel)
