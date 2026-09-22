from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
import shapefile
from pyproj import CRS

from highway_drainage.application.point_export import (
    SHAPEFILE_SUFFIXES,
    crossing_export,
    line_export,
    outlet_export,
)
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import CadLine, CadReference
from highway_drainage.infrastructure.point_export import ShapefilePointWriter
from tests.support.coordinates import candidates
from tests.support.hydrology import chain


@pytest.mark.parametrize("culverts", [False, True])
def test_line_shapefile_preserves_vertices_crs_and_attributes(
    tmp_path: Path, culverts: bool
) -> None:
    road = CadLine(
        CadReference(tmp_path / "road.dxf", "A1", "road", "POLYLINE"),
        ((100.25, 200.5), (110, 220), (120, 210)),
    )
    pipe = CadLine(
        CadReference(tmp_path / "pipe.dxf", "B2", "culvert", "ARC", ("C3",)),
        ((105, 195), (108, 204), (111, 210)),
        approximated=True,
    )
    result = replace(candidates(), highways=(road,), culverts=(pipe,))
    request = line_export(result, tmp_path / "lines.shp", culverts=culverts)
    ShapefilePointWriter().write(request, Event())
    expected = pipe if culverts else road
    with shapefile.Reader(str(request.path), encoding="utf-8") as reader:
        assert reader.shapeType == shapefile.POLYLINE
        assert len(reader) == 1
        assert tuple(tuple(p) for p in reader.shape(0).points) == expected.vertices
        assert reader.record(0)["handle"] == expected.reference.handle
        assert reader.record(0)["layer"] == expected.reference.layer
        assert reader.record(0)["approx"] == expected.approximated
        assert reader.record(0)["source"] == expected.reference.path.name
    assert CRS(request.path.with_suffix(".prj").read_text()).equals(CRS(result.crs_wkt))


def test_crossing_shapefile_coordinates_crs_and_unicode(tmp_path: Path) -> None:
    result = candidates((123.25, 456.75))
    result = replace(result, points=(replace(result.points[0], identifier="Σημείο 1"),))
    request = crossing_export(result, tmp_path / "crossings.shp")
    ShapefilePointWriter().write(request, Event())
    for suffix in SHAPEFILE_SUFFIXES[:5]:
        assert request.path.with_suffix(suffix).is_file()
    with shapefile.Reader(str(request.path), encoding="utf-8") as reader:
        assert reader.shapeType == shapefile.POINT
        assert tuple(reader.shape(0).points[0]) == (123.25, 456.75)
        assert reader.record(0)["point_id"] == "Σημείο 1"
    assert CRS(request.path.with_suffix(".prj").read_text()).equals(CRS(result.crs_wkt))


def test_outlet_export_retains_shared_cells_and_excludes_rejected(tmp_path: Path) -> None:
    prepared = chain(tmp_path).prepared
    request = outlet_export(prepared, tmp_path / "outlets.shp")
    assert request.skipped == 1
    ShapefilePointWriter().write(request, Event())
    with shapefile.Reader(str(request.path)) as reader:
        assert len(reader) == 3
        assert tuple(reader.shape(1).points[0]) == tuple(reader.shape(2).points[0])
        assert reader.record(1)["point_id"] != reader.record(2)["point_id"]
        assert reader.record(1)["elevation"] == 1


def test_cancel_and_unconfirmed_overwrite_preserve_files(tmp_path: Path) -> None:
    request = crossing_export(candidates((1, 2)), tmp_path / "points.shp")
    writer = ShapefilePointWriter()
    writer.write(request, Event())
    before = {s: request.path.with_suffix(s).read_bytes() for s in SHAPEFILE_SUFFIXES[:5]}
    with pytest.raises(ValueError, match="already exist"):
        writer.write(request, Event())
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        writer.write(replace(request, overwrite=True), cancel)
    for suffix, data in before.items():
        assert request.path.with_suffix(suffix).read_bytes() == data


def test_failed_publication_restores_previous_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = crossing_export(candidates((1, 2)), tmp_path / "points.shp")
    writer = ShapefilePointWriter()
    writer.write(request, Event())
    before = {s: request.path.with_suffix(s).read_bytes() for s in SHAPEFILE_SUFFIXES[:5]}
    rename = Path.rename

    def fail(source: Path, target: Path) -> Path:
        if (
            source.parent.name.startswith(".point-export-")
            and source.suffix == ".dbf"
            and "backup" not in str(source)
        ):
            raise OSError("simulated publication failure")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError, match="simulated"):
        writer.write(replace(request, overwrite=True), Event())
    for suffix, data in before.items():
        assert request.path.with_suffix(suffix).read_bytes() == data
