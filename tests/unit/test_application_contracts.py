"""Boundary rules with strict mock ports; no CAD, raster, hydrology or Qt imports."""

from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import create_autospec

import pytest

from highway_drainage.application.coordinates import CoordinateInspector, ValidateCoordinates
from highway_drainage.application.dem import plan_dem
from highway_drainage.application.hydrology import DelineateCatchments, HydrologyEngine
from highway_drainage.application.outlets import OutletSnapper, SelectOutlets
from highway_drainage.application.terrain import (
    ImportCancelled,
    ImportTerrain,
    TerrainNormalizer,
    TerrainReader,
)
from highway_drainage.domain.coordinates import (
    CoordinateReport,
    CoordinateRequest,
    DemCoordinates,
    OutletCoordinateCheck,
    PointLocation,
)
from highway_drainage.domain.crossings import CadReference, CrossingPoint, CrossingResult
from highway_drainage.domain.dem import DemRequest, ResourceLimitError
from highway_drainage.domain.hydrology import HydrologyRequest
from highway_drainage.domain.outlets import (
    OutletSelection,
    PourPoint,
    SnapMode,
    SnapRequest,
    SnapResult,
)
from highway_drainage.domain.terrain import (
    EntityReference,
    Point3D,
    TerrainDataset,
    TerrainFeature,
    TerrainRequest,
    TerrainSource,
)


@pytest.fixture
def dataset() -> TerrainDataset:
    source = TerrainSource(Path("terrain.dxf"), "EPSG:32634", "datum")
    feature = TerrainFeature(
        EntityReference(source.path, "F1", "TIN", "3DFACE"),
        (Point3D(0, 0, 0), Point3D(4, 0, 4), Point3D(0, 4, 4)),
        is_face=True,
    )
    return TerrainDataset((source,), "EPSG:32634", "datum", (feature,), ())


@pytest.fixture
def audit() -> CoordinateReport:
    point = CrossingPoint(
        "CP1", 1, 1, CadReference(Path("culvert.dxf"), "C1", "C", "LINE"), (), False, False
    )
    crossings = CrossingResult("EPSG:32634", (), (), (point,), (), 0.05)
    dem = DemCoordinates(
        Path("dem.tif"),
        "EPSG:32634",
        (0, 0, 4, 4),
        2,
        2,
        (2, 0, 0, 0, -2, 4),
        True,
        "north-up",
        -9999,
        (),
    )
    return CoordinateReport(
        dem,
        crossings,
        True,
        (OutletCoordinateCheck(point, PointLocation.VALID, 1, 0, cell_state="valid"),),
    )


@pytest.fixture
def prepared(audit: CoordinateReport) -> SnapResult:
    request = SnapRequest(CoordinateRequest(audit.dem.path, audit.crossings))
    selection = OutletSelection(
        audit.outlets[0], PourPoint(1, 0, 1, 1, 2, 0, None), "provisional DEM-valid", ""
    )
    return SnapResult(request, audit, (selection,))


@pytest.mark.parametrize("cell_size", [0, -1, float("nan"), float("inf")])
def test_grid_planning_rejects_invalid_resolution(
    dataset: TerrainDataset, cell_size: float
) -> None:
    with pytest.raises(ValueError, match="[Cc]ell size"):
        plan_dem(DemRequest(dataset, Path("unused.tif"), cell_size=cell_size))


def test_grid_planning_alignment_and_preallocation_guard(dataset: TerrainDataset) -> None:
    plan = plan_dem(DemRequest(dataset, Path("unused.tif"), cell_size=1.5))
    assert (plan.width, plan.height) == (3, 3)
    assert plan.extent == (0, -0.5, 4.5, 4)
    with pytest.raises(ResourceLimitError, match="No raster samples have been allocated"):
        plan_dem(DemRequest(dataset, Path("unused.tif"), cell_size=1e-12))


def test_import_validation_precedes_any_adapter_call(dataset: TerrainDataset) -> None:
    reader = create_autospec(TerrainReader, instance=True)
    normalizer = create_autospec(TerrainNormalizer, instance=True)
    with pytest.raises(ValueError, match="more than once"):
        ImportTerrain(reader, normalizer).execute(
            TerrainRequest(dataset.sources * 2, dataset.crs_wkt, "datum")
        )
    reader.read.assert_not_called()
    normalizer.normalize.assert_not_called()


def test_cancelled_coordinate_check_never_opens_raster(audit: CoordinateReport) -> None:
    inspector = create_autospec(CoordinateInspector, instance=True)
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        ValidateCoordinates(inspector).execute(
            CoordinateRequest(audit.dem.path, audit.crossings), cancel
        )
    inspector.inspect.assert_not_called()


def test_snapping_receives_fresh_validation_and_same_cancel_token(prepared: SnapResult) -> None:
    inspector = create_autospec(CoordinateInspector, instance=True)
    inspector.inspect.return_value = prepared.validation
    snapper = create_autospec(OutletSnapper, instance=True)
    snapper.snap.return_value = prepared
    cancel = Event()
    result = SelectOutlets(ValidateCoordinates(inspector), snapper).execute(
        prepared.request, cancel
    )
    assert result is prepared
    inspector.inspect.assert_called_once_with(prepared.request.coordinates, cancel)
    snapper.snap.assert_called_once_with(prepared.request, prepared.validation, cancel)


def test_failed_validation_never_calls_snapper(prepared: SnapResult) -> None:
    inspector = create_autospec(CoordinateInspector, instance=True)
    inspector.inspect.side_effect = ValueError("DEM read failed")
    snapper = create_autospec(OutletSnapper, instance=True)
    with pytest.raises(ValueError, match="DEM read failed"):
        SelectOutlets(ValidateCoordinates(inspector), snapper).execute(prepared.request)
    snapper.snap.assert_not_called()


@pytest.mark.parametrize("maximum", [-1, float("nan"), float("inf")])
def test_bad_snap_distance_never_calls_adapters(prepared: SnapResult, maximum: float) -> None:
    inspector = create_autospec(CoordinateInspector, instance=True)
    snapper = create_autospec(OutletSnapper, instance=True)
    with pytest.raises(ValueError, match="distance"):
        SelectOutlets(ValidateCoordinates(inspector), snapper).execute(
            replace(prepared.request, max_distance=maximum)
        )
    inspector.inspect.assert_not_called()
    snapper.snap.assert_not_called()


def test_accumulation_mode_requires_a_source(prepared: SnapResult) -> None:
    inspector = create_autospec(CoordinateInspector, instance=True)
    snapper = create_autospec(OutletSnapper, instance=True)
    with pytest.raises(ValueError, match="accumulation raster"):
        SelectOutlets(ValidateCoordinates(inspector), snapper).execute(
            replace(prepared.request, mode=SnapMode.ACCUMULATION)
        )
    inspector.inspect.assert_not_called()


def test_duplicate_outlet_ids_block_hydrology(prepared: SnapResult) -> None:
    engine = create_autospec(HydrologyEngine, instance=True)
    request = HydrologyRequest(replace(prepared, outlets=prepared.outlets * 2), Path("unused"))
    with pytest.raises(ValueError, match="unique"):
        DelineateCatchments(engine).execute(request)
    engine.delineate.assert_not_called()


def test_cancelled_hydrology_never_calls_engine(prepared: SnapResult) -> None:
    engine = create_autospec(HydrologyEngine, instance=True)
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        DelineateCatchments(engine).execute(HydrologyRequest(prepared, Path("unused")), cancel)
    engine.delineate.assert_not_called()
