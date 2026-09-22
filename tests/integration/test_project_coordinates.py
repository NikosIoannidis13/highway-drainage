from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from pyproj import CRS, Transformer

from highway_drainage.domain.crossings import CrossingLimits, CrossingRequest, LineSource
from highway_drainage.domain.terrain import LineRole, TerrainRequest
from highway_drainage.infrastructure.cad_lines import CadLineReader
from highway_drainage.infrastructure.cad_reference import preflight_source_crs
from highway_drainage.infrastructure.dxf import DxfTerrainReader
from highway_drainage.infrastructure.project_raster import RasterProjectReader
from tests.support.coordinates import raster
from tests.support.crossings import crossing_service
from tests.support.terrain_input import document, save, service


@pytest.mark.parametrize("unit,metres", [(2, 0.3048), (4, 0.001), (5, 0.01)])
def test_dxf_units_are_converted_once_for_terrain_and_crossings(
    tmp_path: Path, unit: int, metres: float
) -> None:
    doc = document()
    doc.units = unit
    doc.modelspace().add_polyline3d(
        [(100 / metres, 200 / metres, 10 / metres), (104 / metres, 204 / metres, 14 / metres)]
    )
    source = replace(
        save(doc, tmp_path / "units.dxf", LineRole.TERRAIN_SAMPLES), crs="", z_unit="auto"
    )
    source.path.with_suffix(".prj").write_text("EPSG:2100")
    dataset = service().execute(TerrainRequest((source,), "2100", "survey datum"))
    assert not dataset.has_errors
    first = dataset.features[0].vertices[0]
    assert (first.x, first.y, first.z) == pytest.approx((100, 200, 10))
    lines = CadLineReader().read(
        LineSource(source.path, ""), "2100", 0.05, CrossingLimits(), Event()
    )
    assert lines.lines[0].vertices[0] == pytest.approx((100, 200))


def test_foot_based_source_crs_reprojects_into_raster_crs(tmp_path: Path) -> None:
    doc = document()
    doc.units = 21  # US survey feet, matching EPSG:2263.
    doc.modelspace().add_line((1000000, 200000, 30), (1000020, 200040, 40))
    source = replace(
        save(doc, tmp_path / "feet.dxf", LineRole.TERRAIN_SAMPLES), crs="2263", z_unit="auto"
    )
    result = service().execute(TerrainRequest((source,), "26918", "survey datum"))
    expected = Transformer.from_crs(2263, 26918, always_xy=True).transform(1000000, 200000)
    first = result.features[0].vertices[0]
    assert (first.x, first.y) == pytest.approx(expected)
    assert first.z == pytest.approx(30 * 1200 / 3937)
    assert CRS(result.crs_wkt).to_epsg() == 26918


def test_missing_source_crs_requires_declaration(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_line((100, 200, 1), (110, 210, 2))
    source = replace(save(doc, tmp_path / "missing.dxf"), crs="")
    with pytest.raises(ValueError, match="Load a project GeoTIFF"):
        service().execute(TerrainRequest((source,), "2100", "survey datum"))


@pytest.mark.parametrize("binary", [False, True])
def test_missing_crs_fails_before_full_dxf_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binary: bool
) -> None:
    doc = document()
    doc.modelspace().add_line((100, 200, 1), (110, 210, 2))
    source = replace(save(doc, tmp_path / "missing.dxf"), crs="")
    if binary:
        doc.saveas(source.path, fmt="bin")

    def unexpected_read(*args: object, **kwargs: object) -> None:
        pytest.fail("Full DXF parsing started before missing CRS was rejected")

    for module in ("cad_reference", "dxf", "cad_lines"):
        monkeypatch.setattr(f"highway_drainage.infrastructure.{module}.readfile", unexpected_read)
    with pytest.raises(ValueError, match="source CRS is missing"):
        service().execute(TerrainRequest((source,), "2100", "survey datum"))
    with pytest.raises(ValueError, match="source CRS is missing"):
        list(DxfTerrainReader().read(source))
    with pytest.raises(ValueError, match="source CRS is missing"):
        CadLineReader().read(
            LineSource(source.path, ""), "2100", 0.05, CrossingLimits(), Event()
        )


def test_crs_preflight_detects_geodata_across_scan_chunks(tmp_path: Path) -> None:
    path = tmp_path / "chunk_boundary.dxf"
    path.write_bytes(b" " * (1024 * 1024 - 3) + b"GEODATA")
    preflight_source_crs(path, "")


def test_unreferenced_dxf_uses_explicit_raster_fallback_and_reports_assumption(
    tmp_path: Path,
) -> None:
    doc = document()
    doc.modelspace().add_line((100, 200, 10), (110, 210, 20))
    source = replace(
        save(doc, tmp_path / "unreferenced.dxf", LineRole.TERRAIN_SAMPLES),
        crs="",
        fallback_crs="2100",
    )
    result = service().execute(TerrainRequest((source,), "2100", "survey datum"))
    assert not result.has_errors
    assert result.features[0].vertices[0].x == 100
    assert any(i.code == "assumed_raster_crs" for i in result.issues)
    lines = CadLineReader().read(
        LineSource(source.path, "", fallback_crs="2100"), "2100", 0.05, CrossingLimits(), Event()
    )
    assert lines.lines[0].vertices[0] == (100, 200)
    assert any(i.code == "assumed_raster_crs" for i in lines.issues)


def test_project_raster_crs_and_preview_are_loaded_without_modifying_file(tmp_path: Path) -> None:
    path = raster(tmp_path)
    before = path.read_bytes()
    result = RasterProjectReader().read(path, Event())
    assert CRS(result.crs).to_epsg() == 32634
    assert result.preview.path == path
    assert "32634" in result.crs_label
    assert path.read_bytes() == before


def test_project_raster_missing_crs_is_rejected(tmp_path: Path) -> None:
    path = raster(tmp_path, crs=None)
    with pytest.raises(ValueError, match="no CRS"):
        RasterProjectReader().read(path, Event())


def test_embedded_geodata_applies_placement_and_units(tmp_path: Path) -> None:
    doc = document()
    doc.units = 2
    geo = doc.modelspace().new_geodata()
    geo.setup_local_grid(design_point=(0, 0), reference_point=(1000, 2000))
    doc.modelspace().add_line((10, 20, 30), (20, 40, 40))
    source = replace(
        save(doc, tmp_path / "located.dxf", LineRole.TERRAIN_SAMPLES), crs="", z_unit="auto"
    )
    result = service().execute(TerrainRequest((source,), "3395", "survey datum"))
    vertex = result.features[0].vertices[0]
    assert (vertex.x, vertex.y, vertex.z) == pytest.approx((1003.048, 2006.096, 9.144))
    lines = CadLineReader().read(
        LineSource(source.path, ""), "3395", 0.05, CrossingLimits(), Event()
    )
    assert lines.lines[0].vertices[0] == pytest.approx((1003.048, 2006.096))


def test_conflicting_declared_and_detected_crs_is_rejected(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_line((100, 200, 1), (110, 210, 2))
    source = save(doc, tmp_path / "conflict.dxf")
    source.path.with_suffix(".prj").write_text("EPSG:2100")
    with pytest.raises(ValueError, match="conflicts"):
        service().execute(TerrainRequest((source,), "2100", "survey datum"))


def test_mixed_dxf_crs_crossings_target_loaded_raster_and_retain_source_crs(tmp_path: Path) -> None:
    project = RasterProjectReader().read(raster(tmp_path), Event())
    highway, culvert = document(), document()
    highway.modelspace().add_line((100, 195), (110, 195))
    reprojection = Transformer.from_crs(32634, 32635, always_xy=True)
    endpoints = [reprojection.transform(105, y) for y in (190, 200)]
    culvert.units = 2
    culvert.modelspace().add_line(
        tuple(v / 0.3048 for v in endpoints[0]), tuple(v / 0.3048 for v in endpoints[1])
    )
    road = save(highway, tmp_path / "road.dxf")
    pipe = save(culvert, tmp_path / "pipe.dxf")
    road.path.with_suffix(".prj").write_text("EPSG:32634")
    pipe.path.with_suffix(".prj").write_text("EPSG:32635")
    result = crossing_service().execute(
        CrossingRequest(
            LineSource(road.path, "", fallback_crs=project.crs),
            LineSource(pipe.path, "", fallback_crs=project.crs),
            project.crs,
        )
    )
    assert len(result.points) == 1
    assert (result.points[0].x, result.points[0].y) == pytest.approx((105, 195), abs=1e-3)
    assert result.highway_source is not None and result.culvert_source is not None
    assert CRS(result.highway_source.crs).to_epsg() == 32634
    assert CRS(result.culvert_source.crs).to_epsg() == 32635
    assert CRS(result.crs_wkt).equals(CRS(project.crs))
