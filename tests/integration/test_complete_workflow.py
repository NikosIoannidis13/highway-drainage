"""Real adapters from multi-DXF terrain through one catchment per culvert; no Qt."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.domain.coordinates import CoordinateRequest, PointLocation
from highway_drainage.domain.dem import DemRequest
from highway_drainage.domain.hydrology import HydrologyRequest
from highway_drainage.domain.outlets import SnapMode, SnapRequest
from highway_drainage.domain.terrain import LineRole
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from tests.support.crossings import crossing_service, request_for
from tests.support.dem import dem_service
from tests.support.hydrology import service as hydrology_service
from tests.support.outlets import service as outlet_service
from tests.support.terrain_input import document, load, save


@pytest.mark.parametrize("reverse_files", [False, True], ids=["forward-files", "reverse-files"])
def test_complete_multi_dxf_to_catchment_workflow(tmp_path: Path, reverse_files: bool) -> None:
    # A 10 x 2 m strip, z=110-x. Its five DEM cells drain east in a known chain.
    first, second = document(), document()
    triangle = [(100, 200, 10), (110, 200, 0), (110, 202, 0)]
    first.modelspace().add_3dface(triangle)
    second.modelspace().add_3dface([(100, 200, 10), (110, 202, 0), (100, 202, 10)])
    second.modelspace().add_3dface(list(reversed(triangle)))
    diagonal = [(100, 200, 10), (110, 202, 0)]
    polyline = second.modelspace().add_polyline3d(diagonal, dxfattribs={"layer": "BREAKLINE"})
    a = save(first, tmp_path / "terrain_a.dxf")
    b = replace(
        save(second, tmp_path / "terrain_b.dxf"), layer_roles=(("BREAKLINE", LineRole.BREAKLINE),)
    )
    terrain = load(*((b, a) if reverse_files else (a, b)))
    assert not terrain.has_errors
    assert len(terrain.sources) == 2
    assert sum(f.is_face for f in terrain.features) == 2
    assert any(i.code == "duplicate" for i in terrain.issues)
    breakline = next(f for f in terrain.features if not f.is_face)
    assert breakline.role == LineRole.BREAKLINE
    assert breakline.reference.handle == polyline.dxf.handle
    assert [(p.x, p.y, p.z) for p in breakline.vertices] == diagonal

    dem = dem_service().execute(DemRequest(terrain, tmp_path / "dem.tif", cell_size=2))
    assert dem.method == "preserved_faces" and dem.triangle_count == 2
    assert dem.valid_cells == 5
    with rasterio.open(dem.output) as src:
        np.testing.assert_allclose(src.read(1), [[9, 7, 5, 3, 1]])
        assert tuple(src.bounds) == (100, 200, 110, 202)
        assert src.crs.to_epsg() == 32634
        assert (src.transform.a, src.transform.e) == (2, -2)

    highway, culverts = document(), document()
    road = highway.modelspace().add_lwpolyline([(100, 201), (110, 201)])
    handles = [
        culverts.modelspace().add_line((x, 200), (x, 202)).dxf.handle for x in (104.8, 109, 110)
    ]
    crossings = crossing_service().execute(request_for(tmp_path, highway, culverts))
    assert [(p.x, p.y) for p in crossings.points] == [(104.8, 201), (109, 201), (110, 201)]
    assert [p.culvert.handle for p in crossings.points] == handles
    assert all(p.highways[0].handle == road.dxf.handle for p in crossings.points)

    coordinates = CoordinateRequest(dem.output, crossings)
    audit = ValidateCoordinates(RasterCoordinateInspector()).execute(coordinates)
    assert [(p.row, p.column) for p in audit.outlets] == [(0, 2), (0, 4), (0, 5)]
    assert audit.outlets[2].location == PointLocation.EDGE
    assert audit.outlets[2].cell_state == "no addressable cell"
    prepared = outlet_service().execute(SnapRequest(coordinates, max_distance=0.3))
    up, down, rejected = prepared.outlets
    assert up.pour_point is not None and down.pour_point is not None
    assert up.pour_point.x == 105 and up.pour_point.distance == pytest.approx(0.2)
    assert up.original.point.x == 104.8
    assert down.pour_point.distance == 0
    assert rejected.pour_point is None and rejected.status == "rejected"

    result = hydrology_service().execute(HydrologyRequest(prepared, tmp_path / "catchments"))
    assert [c.cell_count for c in result.catchments] == [3, 5, 0]
    assert [c.area_m2 for c in result.catchments] == [12, 20, 0]
    for catchment, expected in zip(
        result.catchments[:2], ([1, 1, 1, 0, 0], [1, 1, 1, 1, 1]), strict=True
    ):
        assert catchment.mask is not None
        with rasterio.open(catchment.mask) as src:
            assert src.read(1).tolist() == [expected]
    assert result.catchments[2].mask is None
    manifest = json.loads((result.output / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["result"]["catchments"]) == len(handles)
    # Reuse THIS flow model's accumulation for the optional second preparation mode.
    qualified = outlet_service().execute(
        SnapRequest(
            coordinates,
            max_distance=0.3,
            mode=SnapMode.ACCUMULATION,
            accumulation=result.accumulation,
            minimum_accumulation=4,
        )
    )
    assert qualified.outlets[0].pour_point is None
    assert qualified.outlets[1].status == "accumulation-qualified"
    assert qualified.outlets[1].pour_point is not None
    assert qualified.outlets[1].pour_point.accumulation == 5
