"""Integration coverage for terrain_input."""

from dataclasses import replace
from math import nan
from pathlib import Path
from threading import Event

import pytest
from pyproj import Transformer

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.terrain import (
    LineRole,
    Point3D,
    TerrainRequest,
    TerrainSource,
)
from tests.support.terrain_input import TRIANGLE, document, load, save, service


def test_triangles_preserve_vertices_and_provenance_across_files(tmp_path: Path) -> None:
    first, second = document(), document()
    face = first.modelspace().add_3dface(TRIANGLE, dxfattribs={"layer": "TIN"})
    second.modelspace().add_3dface([(x + 20, y, z) for x, y, z in TRIANGLE])
    a = save(first, tmp_path / "a.dxf")
    b = save(second, tmp_path / "b.dxf")
    result = load(a, b)
    assert len(result.features) == 2
    assert result.features[0].vertices == tuple(Point3D(*p) for p in TRIANGLE)
    assert result.features[0].reference.handle == face.dxf.handle
    assert result.features[0].reference.layer == "TIN"
    assert result.features[1].reference.path == b.path
    assert not result.has_errors


def test_reversed_duplicate_faces_across_files_are_reported(tmp_path: Path) -> None:
    a, b = document(), document()
    a.modelspace().add_3dface(TRIANGLE)
    b.modelspace().add_3dface(list(reversed(TRIANGLE)))
    result = load(save(a, tmp_path / "a.dxf"), save(b, tmp_path / "b.dxf"))
    assert len(result.features) == 1
    duplicate = next(i for i in result.issues if i.code == "duplicate")
    assert duplicate.related == result.features[0].reference
    assert duplicate.reference.path.name == "b.dxf"


def test_quad_retained_without_invented_diagonal(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 1), (1, 0, 1), (1, 1, 2), (0, 1, 1)])
    result = load(save(doc, tmp_path / "quad.dxf"))
    assert len(result.features) == 1
    assert len(result.features[0].vertices) == 4
    assert any(i.code == "quad" for i in result.issues)


def test_layer_roles_preserve_order_and_boundaries(tmp_path: Path) -> None:
    doc = document()
    msp = doc.modelspace()
    msp.add_polyline3d(TRIANGLE, dxfattribs={"layer": "RIDGE"})
    msp.add_polyline3d(
        [(x + 100, y, z) for x, y, z in TRIANGLE], close=True, dxfattribs={"layer": "LIMIT"}
    )
    msp.add_line((0, 0, 3), (1, 1, 4), dxfattribs={"layer": "OTHER"})
    source = replace(
        save(doc, tmp_path / "lines.dxf"),
        layer_roles=(
            ("RIDGE", LineRole.BREAKLINE),
            ("LIMIT", LineRole.BOUNDARY),
        ),
    )
    result = load(source)
    assert [f.role for f in result.features] == [
        LineRole.BREAKLINE,
        LineRole.BOUNDARY,
        LineRole.UNASSIGNED,
    ]
    assert result.features[0].vertices == tuple(Point3D(*p) for p in TRIANGLE)
    assert result.features[1].closed
    assert any(i.code == "unassigned" for i in result.issues)


@pytest.mark.parametrize(
    "points,closed",
    [
        ([(0, 0, 1), (1, 0, 1), (1, 1, 1)], False),
        ([(0, 0, 1), (1, 1, 1), (0, 1, 1), (1, 0, 1)], True),
    ],
)
def test_invalid_boundaries_are_quarantined(
    tmp_path: Path, points: list[tuple[int, int, int]], closed: bool
) -> None:
    doc = document()
    doc.modelspace().add_polyline3d(points, close=closed)
    result = load(save(doc, tmp_path / "boundary.dxf", LineRole.BOUNDARY))
    assert not result.features
    assert any(i.code == "geometry" for i in result.issues)


def test_reversed_lines_and_rotated_rings_are_duplicates(tmp_path: Path) -> None:
    doc = document()
    msp = doc.modelspace()
    msp.add_line((0, 0, 1), (1, 1, 2))
    msp.add_polyline3d([(1, 1, 2), (0, 0, 1)])
    msp.add_polyline3d(TRIANGLE, close=True)
    msp.add_polyline3d([TRIANGLE[1], TRIANGLE[0], TRIANGLE[2]], close=True)
    result = load(save(doc, tmp_path / "dupes.dxf", LineRole.BREAKLINE))
    assert len(result.features) == 2
    assert sum(i.code == "duplicate" for i in result.issues) == 2


def test_duplicate_roles_conflict_instead_of_silent_choice(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_polyline3d(TRIANGLE, close=True, dxfattribs={"layer": "A"})
    doc.modelspace().add_polyline3d(TRIANGLE, close=True, dxfattribs={"layer": "B"})
    source = replace(
        save(doc, tmp_path / "roles.dxf"),
        layer_roles=(
            ("A", LineRole.BOUNDARY),
            ("B", LineRole.BREAKLINE),
        ),
    )
    assert any(i.code == "duplicate_role_conflict" for i in load(source).issues)


@pytest.mark.parametrize("height", [nan, float("inf"), -float("inf")])
def test_nonfinite_z_rejected(tmp_path: Path, height: float) -> None:
    doc = document()
    doc.modelspace().add_line((0, 0, height), (1, 1, 3))
    result = load(save(doc, tmp_path / "invalid.dxf"))
    assert not result.features
    assert any(i.code == "nonfinite" for i in result.issues)


def test_zero_z_is_valid_but_warned_and_negative_z_is_valid(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_line((0, 0, 0), (1, 1, 0))
    doc.modelspace().add_line((2, 2, -10), (3, 3, -11))
    result = load(save(doc, tmp_path / "heights.dxf", LineRole.BREAKLINE))
    assert len(result.features) == 2
    assert not result.has_errors
    assert sum(i.code == "zero_z" for i in result.issues) == 1


def test_xy_z_conflicts_are_reported_without_averaging(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_line((0, 0, 1), (1, 1, 1))
    doc.modelspace().add_line((0, 0, 2), (2, 2, 2))
    result = load(save(doc, tmp_path / "conflict.dxf"))
    assert result.has_errors
    assert [f.vertices[0].z for f in result.features] == [1, 2]
    assert any(i.code == "z_conflict" and i.related is not None for i in result.issues)


def test_mixed_crs_and_vertical_units_are_normalized(tmp_path: Path) -> None:
    a, b = document(), document()
    a.modelspace().add_line(TRIANGLE[0], TRIANGLE[1])
    transform = Transformer.from_crs(32634, 32635, always_xy=True)
    converted = [(*transform.transform(x + 100, y), z / 0.3048) for x, y, z in TRIANGLE[:2]]
    b.modelspace().add_line(converted[0], converted[1])
    source_b = replace(save(b, tmp_path / "b.dxf"), crs="EPSG:32635", z_unit="ft")
    result = load(save(a, tmp_path / "a.dxf"), source_b)
    point = result.features[1].vertices[0]
    assert (point.x, point.y, point.z) == pytest.approx((500100, 4200000, 10))
    assert result.crs_wkt


@pytest.mark.parametrize("crs", ["", "not a crs", "EPSG:4326", "EPSG:2263"])
def test_unknown_geographic_and_nonmetre_crs_are_rejected(tmp_path: Path, crs: str) -> None:
    doc = document()
    source = replace(save(doc, tmp_path / "crs.dxf"), crs=crs)
    with pytest.raises((ValueError, RuntimeError)):
        load(source)


def test_units_vertical_reference_limits_and_duplicate_files(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_line((0, 0, 10), (1, 1, 11))
    source = save(doc, tmp_path / "units.dxf")
    with pytest.raises(ValueError, match="vertical reference"):
        load(replace(source, vertical_reference="different datum"))
    with pytest.raises(ValueError, match="more than once"):
        load(source, source)
    result = service().execute(TerrainRequest((source,), "EPSG:32634", "survey datum", max_z=5))
    assert any(i.code == "z_range" for i in result.issues)
    doc.units = 2
    source = save(doc, source.path)
    assert any(i.code == "drawing_units" for i in load(source).issues)


def test_unsupported_and_ignored_entities_are_reported(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_lwpolyline([(0, 0), (1, 1)])
    doc.modelspace().add_circle((0, 0), 5)
    block = doc.blocks.new("TEST")
    block.add_3dface(TRIANGLE)
    doc.modelspace().add_blockref("TEST", (0, 0))
    doc.modelspace().add_line((0, 0, 2), (1, 1, 2), dxfattribs={"layer": "IGNORE"})
    source = replace(
        save(doc, tmp_path / "unsupported.dxf"), layer_roles=(("IGNORE", LineRole.IGNORE),)
    )
    result = load(source)
    assert sum(i.code == "unsupported" for i in result.issues) == 3
    assert any(i.code == "ignored" for i in result.issues)
    assert not result.features


def test_missing_file_and_cancellation(tmp_path: Path) -> None:
    source = TerrainSource(tmp_path / "missing.dxf", "EPSG:32634", "survey datum")
    request = TerrainRequest((source,), "EPSG:32634", "survey datum")
    with pytest.raises(ValueError, match="Cannot read"):
        service().execute(request)
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        service().execute(request, cancel)


def test_degenerate_face_and_vertical_line_are_rejected(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 1), (1, 1, 1), (2, 2, 1)])
    doc.modelspace().add_line((0, 0, 1), (0, 0, 2))
    result = load(save(doc, tmp_path / "degenerate.dxf"))
    assert not result.features
    assert sum(i.code == "geometry" for i in result.issues) == 2


def test_explicit_repeated_endpoint_is_closed_without_changing_vertices(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_polyline3d([*TRIANGLE, TRIANGLE[0]])
    result = load(save(doc, tmp_path / "closed.dxf", LineRole.BOUNDARY))
    assert result.features[0].closed
    assert result.features[0].vertices == tuple(Point3D(*p) for p in TRIANGLE)
    assert not result.has_errors


def test_unspecified_units_warn_and_malformed_file_fails(tmp_path: Path) -> None:
    doc = document()
    doc.units = 0
    doc.modelspace().add_3dface(TRIANGLE)
    source = save(doc, tmp_path / "unknown_units.dxf")
    assert any(i.code == "unspecified_units" for i in load(source).issues)
    source.path.write_text("not a DXF", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read"):
        load(source)
