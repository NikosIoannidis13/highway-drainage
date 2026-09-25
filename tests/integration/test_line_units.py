"""Coordinate-unit choices preserve survey placement or explicitly convert local drawings."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
import shapefile
from pyproj import Transformer

from highway_drainage.application.crossings import FindCrossings
from highway_drainage.application.point_export import DirectLineExport
from highway_drainage.domain.crossings import CrossingLimits, DrawingUnits, LineSource
from highway_drainage.domain.preview import RasterPreview
from highway_drainage.infrastructure.cad_lines import CadLineReader
from highway_drainage.infrastructure.crossings import ShapelyCrossings
from highway_drainage.infrastructure.point_export import ShapefilePointWriter
from tests.support.crossings import crossing_service, request_for
from tests.support.terrain_input import document, save


def test_rotated_raster_extent_includes_all_four_corners() -> None:
    preview = RasterPreview(Path("rotated.tif"), 4, 3, b"", (2, 1, 100, .5, -3, 200), "")
    assert preview.bounds == (100, 191, 111, 202)


@pytest.mark.parametrize("choice, factor", [
    (DrawingUnits.SOURCE_CRS, 1), (DrawingUnits.HEADER, 0.0254),
    (DrawingUnits.METRES, 1), (DrawingUnits.MILLIMETRES, 0.001),
    (DrawingUnits.FEET, 0.3048), (DrawingUnits.US_SURVEY_FEET, 1200 / 3937),
    (DrawingUnits.INCHES, 0.0254),
])
def test_coordinate_units_and_direct_export(
    tmp_path: Path, choice: DrawingUnits, factor: float,
) -> None:
    doc = document()
    doc.units = 1
    doc.modelspace().add_line((588694, 3917500), (588704, 3917510))
    path = save(doc, tmp_path / "units.dxf").path
    before = path.read_bytes()
    source = LineSource(path, "2100", units=choice)
    result = CadLineReader().read(source, "2100", .05, CrossingLimits(), Event())
    expected = (588694 * factor, 3917500 * factor)
    assert result.lines[0].vertices[0] == pytest.approx(expected)
    assert result.source is not None and result.source.units == choice
    assert "DXF header: Inches" in result.source.unit_summary
    output = tmp_path / "lines.shp"
    ShapefilePointWriter().write(DirectLineExport(output, source, "2100", "highway", .05), Event())
    with shapefile.Reader(str(output)) as reader:
        assert reader.shape(0).points[0] == pytest.approx(expected)
    assert path.read_bytes() == before


def test_source_crs_units_still_reprojects_foot_crs(tmp_path: Path) -> None:
    doc = document()
    doc.units = 1  # Wrong header must not override source-CRS coordinates in survey feet.
    doc.modelspace().add_line((1000000, 200000), (1000020, 200040))
    path = save(doc, tmp_path / "feet.dxf").path
    source = LineSource(path, "2263", units=DrawingUnits.SOURCE_CRS)
    result = CadLineReader().read(source, "26918", .05, CrossingLimits(), Event())
    expected = Transformer.from_crs(2263, 26918, always_xy=True).transform(1000000, 200000)
    assert result.lines[0].vertices[0] == pytest.approx(expected)


def test_preserve_crs_retains_geodata_and_rejects_conflicting_unit_override(tmp_path: Path) -> None:
    doc = document()
    doc.units = 2
    doc.modelspace().new_geodata().setup_local_grid(
        design_point=(0, 0), reference_point=(1000, 2000),
    )
    doc.modelspace().add_line((10, 20), (20, 40))
    path = save(doc, tmp_path / "located.dxf").path
    source = LineSource(path, "", units=DrawingUnits.SOURCE_CRS)
    result = CadLineReader().read(source, "3395", .05, CrossingLimits(), Event())
    assert result.lines[0].vertices[0] == pytest.approx((1003.048, 2006.096))
    assert result.source is not None and "GEODATA" in result.source.unit_summary
    with pytest.raises(ValueError, match="embedded GEODATA"):
        CadLineReader().read(
            replace(source, units=DrawingUnits.METRES), "3395", .05, CrossingLimits(), Event(),
        )


@pytest.mark.parametrize("road_units, pipe_units, failing", [
    (1, 1, "Highway"), (6, 1, "Culverts"), (1, 6, "Highway"),
])
def test_bad_extent_stops_before_intersections_and_preserve_crs_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    road_units: int, pipe_units: int, failing: str,
) -> None:
    road, pipe = document(), document()
    road.units, pipe.units = road_units, pipe_units
    road.modelspace().add_line((588690, 3917500), (588710, 3917500))
    pipe.modelspace().add_line((588700, 3917490), (588700, 3917510))
    request = replace(request_for(tmp_path, road, pipe),
                      raster_bounds=(588600, 3917400, 588800, 3917600))
    engine = ShapelyCrossings()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Intersection work must not start for non-overlapping raster/DXF extents")

    monkeypatch.setattr(engine, "intersect", forbidden)
    with pytest.raises(ValueError, match=failing) as error:
        FindCrossings(CadLineReader(), engine).execute(request)
    assert "does not overlap" in str(error.value)
    assert "XY scale 0.0254" in str(error.value)
    recovered = crossing_service().execute(replace(
        request, highway=replace(request.highway, units=DrawingUnits.SOURCE_CRS),
        culverts=replace(request.culverts, units=DrawingUnits.SOURCE_CRS),
    ))
    assert len(recovered.points) == 1
    assert (recovered.points[0].x, recovered.points[0].y) == (588700, 3917500)
