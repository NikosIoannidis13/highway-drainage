"""Integration coverage for crossings."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from pyproj import Transformer

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import CrossingLimits
from highway_drainage.infrastructure.cad_lines import CrossingLimitError
from tests.support.crossings import crossing_service, request_for
from tests.support.terrain_input import document


def test_line_crossing_retains_both_identifiers_and_ignores_z(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    road = highway.modelspace().add_line((0, 0, 100), (10, 0, 100))
    drain = culvert.modelspace().add_line((5, -2, 10), (5, 2, 10))
    request = request_for(tmp_path, highway, culvert)
    result = crossing_service().execute(request)
    assert result.highway_source == request.highway
    assert result.culvert_source == request.culverts
    assert len(result.points) == 1
    point = result.points[0]
    assert (point.x, point.y) == (5, 0)
    assert point.culvert.handle == drain.dxf.handle
    assert point.highways[0].handle == road.dxf.handle
    assert not point.endpoint_contact


@pytest.mark.parametrize("kind", ["lw", "2d", "3d"])
def test_common_polylines_and_multiple_crossings(tmp_path: Path, kind: str) -> None:
    highway, culvert = document(), document()
    msp = highway.modelspace()
    points = [(0, 0), (0, 4), (4, 4), (4, 0)]
    if kind == "lw":
        msp.add_lwpolyline(points)
    elif kind == "2d":
        msp.add_polyline2d(points)
    else:
        msp.add_polyline3d([(x, y, 10) for x, y in points])
    culvert.modelspace().add_line((-1, 2), (5, 2))
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert {(p.x, p.y) for p in result.points} == {(0, 2), (4, 2)}
    assert len({p.culvert.identifier for p in result.points}) == 1


def test_shared_vertex_merges_highway_matches_but_keeps_culvert_identities(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_line((0, 0), (5, 0))
    highway.modelspace().add_line((5, 0), (10, 0))
    culvert.modelspace().add_line((5, -2), (5, 2))
    culvert.modelspace().add_line((5, 2), (5, -2))
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert len(result.points) == 2
    assert all(len(point.highways) == 2 and point.endpoint_contact for point in result.points)
    assert len({p.culvert.identifier for p in result.points}) == 2
    assert any(i.code == "duplicate" for i in result.issues)


def test_overlapping_sections_do_not_manufacture_endpoint_candidates(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_lwpolyline([(0, 0), (2, 0), (4, 0), (6, 0)])
    culvert.modelspace().add_lwpolyline([(1, 0), (3, 0), (5, 0)])
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert not result.points
    assert sum(i.code == "overlap" for i in result.issues) == 1


def test_overlap_and_separate_point_on_same_pair(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_lwpolyline([(0, 0), (4, 0), (4, 4), (0, 4)])
    culvert.modelspace().add_lwpolyline([(1, 0), (3, 0), (3, 5)])
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert [(p.x, p.y) for p in result.points] == [(3, 4)]
    assert any(i.code == "overlap" for i in result.issues)


@pytest.mark.parametrize("kind", ["arc", "circle", "bulge", "polyline_bulge"])
def test_curves_use_bounded_approximation(tmp_path: Path, kind: str) -> None:
    highway, culvert = document(), document()
    if kind == "arc":
        highway.modelspace().add_arc((0, 0), 5, 0, 180)
    elif kind == "circle":
        highway.modelspace().add_circle((0, 0), 5)
    elif kind == "bulge":
        highway.modelspace().add_lwpolyline([(-5, 0, -1), (5, 0, 0)], format="xyb")
    else:
        poly = highway.modelspace().add_polyline2d([(-5, 0), (5, 0)])
        poly.vertices[0].dxf.bulge = -1
    culvert.modelspace().add_line((0, 1), (0, 6))
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert len(result.points) == 1
    assert result.points[0].x == pytest.approx(0)
    assert result.points[0].y == pytest.approx(5, abs=0.05)
    assert result.points[0].approximated


def test_nondefault_ocs_and_polyline_closure(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    # Negative extrusion reflects local X; using raw OCS coordinates would be wrong.
    highway.modelspace().add_lwpolyline(
        [(0, 0), (4, 0), (4, 4)], close=True, dxfattribs={"extrusion": (0, 0, -1)}
    )
    culvert.modelspace().add_line((-2, 1), (-2, 3))
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert [(p.x, p.y) for p in result.points] == [(-2, 2)]
    assert result.highways[0].vertices[0] == result.highways[0].vertices[-1]


def test_nested_insert_transform_and_instance_provenance(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_line((8, 21), (14, 21))
    leaf = culvert.blocks.new("LEAF")
    line = leaf.add_line((0, 0), (2, 0))
    parent = culvert.blocks.new("PARENT")
    inner = parent.add_blockref("LEAF", (1, 0), dxfattribs={"rotation": 90})
    outer = culvert.modelspace().add_blockref("PARENT", (10, 20), dxfattribs={"layer": "DRAIN"})
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert len(result.points) == 1
    point = result.points[0]
    assert (point.x, point.y) == pytest.approx((11, 21))
    assert point.culvert.inserts == (outer.dxf.handle, inner.dxf.handle)
    assert point.culvert.handle == line.dxf.handle
    assert point.culvert.layer == "DRAIN"


def test_source_crs_transformation_and_layer_filters(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_line(
        (500000, 4200000), (500010, 4200000), dxfattribs={"layer": "ROAD"}
    )
    highway.modelspace().add_line(
        (500000, 4200001), (500010, 4200001), dxfattribs={"layer": "ANNOTATION"}
    )
    tr = Transformer.from_crs(32634, 32635, always_xy=True)
    culvert.modelspace().add_line(tr.transform(500005, 4199990), tr.transform(500005, 4200010))
    request = request_for(tmp_path, highway, culvert)
    request = replace(
        request,
        highway=replace(request.highway, layers=("road",)),
        culverts=replace(request.culverts, crs="EPSG:32635"),
    )
    result = crossing_service().execute(request)
    assert len(result.points) == 1
    assert (result.points[0].x, result.points[0].y) == pytest.approx((500005, 4200000), abs=1e-6)


def test_invalid_unsupported_and_empty_inputs_are_reported(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_line((0, 0), (0, 0))
    highway.modelspace().add_text("annotation")
    culvert.modelspace().add_line((0, -1), (0, 1))
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert not result.points
    assert {i.code for i in result.issues} >= {"invalid_geometry", "unsupported", "empty"}


def test_repeated_vertices_cleaned_and_near_miss_not_snapped(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_polyline3d([(0, 0, 0), (0, 0, 0), (4, 0, 0)])
    culvert.modelspace().add_line((2, 0.001), (2, 1))
    result = crossing_service().execute(request_for(tmp_path, highway, culvert))
    assert len(result.highways[0].vertices) == 2
    assert not result.points


def test_curve_and_intersection_workload_limits(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_circle((0, 0), 100)
    culvert.modelspace().add_line((-200, 0), (200, 0))
    request = request_for(tmp_path, highway, culvert)
    with pytest.raises(CrossingLimitError, match="Curve"):
        crossing_service().execute(replace(request, curve_tolerance=1e-30))
    with pytest.raises(CrossingLimitError, match="segment pairs"):
        crossing_service().execute(replace(request, limits=CrossingLimits(max_candidate_pairs=1)))


def test_cancellation_and_bad_crs(tmp_path: Path) -> None:
    highway, culvert = document(), document()
    request = request_for(tmp_path, highway, culvert)
    event = Event()
    event.set()
    with pytest.raises(ImportCancelled):
        crossing_service().execute(request, event)
    with pytest.raises(ValueError, match="projected"):
        crossing_service().execute(replace(request, working_crs="EPSG:4326"))
