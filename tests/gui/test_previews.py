"""Gui coverage for previews."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsPathItem
from pytestqt.qtbot import QtBot

from highway_drainage.application.dem import GenerateDem
from highway_drainage.application.hydrology import DelineateCatchments
from highway_drainage.application.outlets import SelectOutlets
from highway_drainage.infrastructure.preview import RasterPreviewReader
from highway_drainage.presentation.main_window import MainWindow
from highway_drainage.presentation.preview_panel import PreviewPanel
from tests.support.dem import dem_service, plane
from tests.support.hydrology import chain
from tests.support.hydrology import service as hydrology_service


@pytest.mark.parametrize("rotated", [False, True])
def test_raster_background_aligns_and_survives_overlay_refresh(
    qtbot: QtBot, tmp_path: Path, rotated: bool
) -> None:
    request = chain(tmp_path)
    snapshot = RasterPreviewReader().raster(request.prepared.request.coordinates.dem, Event())
    if rotated:
        snapshot = replace(snapshot, affine=(2, 0.5, 100, 0.25, -2, 200))
    preview = PreviewPanel()
    qtbot.addWidget(preview)
    assert not preview.show_raster_background.isEnabled()
    preview.show_raster(snapshot)
    assert preview.show_raster_background.isEnabled()
    assert not preview.show_raster_background.isChecked()
    view = preview.drainage_view
    scene = view.scene()
    assert scene is not None

    # Raster-only, crossings, and snapped outlets all share the same background.
    refreshes: tuple[Callable[[], None], ...] = (
        lambda: None,
        lambda: view.show_result(request.prepared.validation.crossings),
        lambda: view.show_outlets(request.prepared),
        view.clear,
    )
    for refresh in refreshes:
        refresh()
        rasters = [item for item in scene.items() if item.data(1) == "raster"]
        assert len(rasters) == 1
        item = rasters[0]
        assert not item.isVisible()
        assert view.snapshot is snapshot
        a, b, c, d, e, f = snapshot.affine
        ox, oy = view._origin
        for col, row in ((0, 0), (snapshot.width, snapshot.height), (3.5, 1.5)):
            location = item.mapToScene(QPointF(col, row))
            assert location.x() + ox == pytest.approx(a * col + b * row + c)
            assert -location.y() + oy == pytest.approx(d * col + e * row + f)
        assert all(item.zValue() < overlay.zValue() for overlay in scene.items() if overlay != item)

    replacement = replace(snapshot, path=tmp_path / "replacement.tif")
    preview.show_raster(replacement)
    assert view.snapshot is replacement
    assert len(scene.items()) == 1
    preview.show_raster_background.setChecked(True)
    assert scene.items()[0].isVisible()
    view.show_outlets(request.prepared)
    assert next(item for item in scene.items() if item.data(1) == "raster").isVisible()
    view.clear()
    preview.clear_terrain()
    assert view.snapshot is None and not scene.items()
    assert not preview.show_raster_background.isEnabled()
    preview.show_raster(snapshot)
    assert scene.items()[0].isVisible()  # Retain the user's choice when loading another DEM.


def test_switching_preserves_scenes_results_and_zoom_without_work(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = chain(tmp_path)
    hydrology = hydrology_service().execute(request)
    reader = RasterPreviewReader()
    image = reader.raster(request.prepared.request.coordinates.dem, Event())
    boundaries = reader.boundaries(hydrology, Event())
    assert len(boundaries.outlines) == 3
    # Three contiguous 2 m cells start at X=102 and end at X=108.
    assert min(x for ring in boundaries.outlines[0].rings for x, _ in ring) == 102
    assert max(x for ring in boundaries.outlines[0].rings for x, _ in ring) == 108
    window = MainWindow()
    qtbot.addWidget(window)
    window._show_crossings(request.prepared.validation.crossings)
    window._show_outlets(request.prepared)
    window._show_hydrology(hydrology)
    window.preview.show_raster(image)
    window.preview.show_boundaries(boundaries)
    window.show()
    preview = window.preview
    assert window.crossing_panel.view is preview.drainage_view
    assert window.coordinate_panel.outlet_view is preview.drainage_view
    scene = preview.drainage_view.scene()
    assert scene is not None
    items = scene.items()
    assert sum(item.data(1) == "catchment" for item in items) == 3
    preview.terrain_view.scale(1.4, 1.4)
    preview.drainage_view.scale(1.7, 1.7)
    terrain_transform = preview.terrain_view.transform()
    drainage_transform = preview.drainage_view.transform()
    background = next(item for item in items if item.data(1) == "raster")
    assert not background.isVisible()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("View toggles must not read files or execute engineering")

    monkeypatch.setattr(reader, "raster", forbidden)
    monkeypatch.setattr(reader, "boundaries", forbidden)
    monkeypatch.setattr("rasterio.open", forbidden)
    for use_case in (GenerateDem, SelectOutlets, DelineateCatchments):
        monkeypatch.setattr(use_case, "execute", forbidden)
    for index in [0, 1] * 8:
        preview.mode.setCurrentIndex(index)
        preview.show_raster_background.setChecked(index == 1)
        assert background.isVisible() == (index == 1)
        assert preview.terrain_view.isVisible() == (index == 0)
        assert preview.drainage_view.isVisible() == (index == 1)
    assert preview.terrain_view.snapshot is image
    assert preview.boundaries is boundaries
    assert scene.items() == items
    assert preview.terrain_view.transform() == terrain_transform
    assert preview.drainage_view.transform() == drainage_transform
    assert window.coordinate_panel.snap_result is request.prepared
    assert window.hydrology_panel.result is hydrology
    terrain_scene = preview.terrain_view.scene()
    assert terrain_scene is not None and terrain_scene.items()[0].isVisible()
    preview.show_raster_background.setChecked(False)
    preview.drainage_view.fit_data()
    without_raster = preview.drainage_view.transform()
    scene.removeItem(background)
    preview.drainage_view.fit_data()
    assert preview.drainage_view.transform() == without_raster
    window.close()


def test_hydrology_worker_caches_boundaries_and_input_changes_clear_them(
    qtbot: QtBot, tmp_path: Path
) -> None:
    request = chain(tmp_path)
    window = MainWindow(hydrology_use_case=hydrology_service(), previews=RasterPreviewReader())
    qtbot.addWidget(window)
    window._show_crossings(request.prepared.validation.crossings)
    window._show_outlets(request.prepared)
    window.preview.show_raster(
        RasterPreviewReader().raster(request.prepared.request.coordinates.dem, Event())
    )
    window.hydrology_panel.output.setText(str(request.output))
    window.hydrology_panel.run_button.click()
    qtbot.waitUntil(lambda: window.hydrology_panel.isEnabled(), timeout=120000)
    assert window.preview.boundaries is not None
    assert len(window.preview.boundaries.outlines) == 3
    assert window.preview.mode.currentIndex() == 1
    scene = window.preview.drainage_view.scene()
    assert scene is not None
    background = next(item for item in scene.items() if item.data(1) == "raster")
    catchments = [item for item in scene.items() if item.data(1) == "catchment"]
    assert len(catchments) == 3
    for item in catchments:
        assert isinstance(item, QGraphicsPathItem)
        assert background.zValue() < item.zValue() < 0
        assert 0 < item.brush().color().alpha() < 255
        assert item.path().fillRule() == Qt.FillRule.OddEvenFill
    window.coordinate_panel.snap_distance.setText("2")
    assert window.preview.boundaries is None
    assert window.coordinate_panel.snap_result is None
    assert len(window.preview.drainage_view.markers) == 4
    window.crossing_panel.invalidate()
    assert not window.preview.drainage_view.markers
    window.close()


def test_dem_export_populates_terrain_preview_in_worker(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(dem_use_case=dem_service(), previews=RasterPreviewReader())
    qtbot.addWidget(window)
    window._show_result(plane(tmp_path))
    window.export_panel.setEnabled(True)
    window.export_panel.output.setText(str(tmp_path / "preview_dem.tif"))
    window.export_panel.cell_size.setText("1")
    window.export_panel.export_button.click()
    qtbot.waitUntil(lambda: window.inputs.isEnabled(), timeout=10000)
    snapshot = window.preview.terrain_view.snapshot
    assert snapshot is not None and snapshot.path.name == "preview_dem.tif"
    assert "4 x 4" in window.preview.terrain_info.text()
    scene = window.preview.terrain_view.scene()
    assert scene is not None and len(scene.items()) == 1
    window.coordinate_panel.dem_path.setText(str(tmp_path / "different.tif"))
    assert window.preview.terrain_view.snapshot is None
    window.close()


def test_preview_failure_keeps_successful_engineering_result(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = chain(tmp_path)
    reader = RasterPreviewReader()

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("preview file unavailable")

    monkeypatch.setattr(reader, "boundaries", fail)
    window = MainWindow(hydrology_use_case=hydrology_service(), previews=reader)
    qtbot.addWidget(window)
    window._show_outlets(request.prepared)
    window.hydrology_panel.output.setText(str(request.output))
    window.hydrology_panel.run_button.click()
    qtbot.waitUntil(lambda: window.hydrology_panel.isEnabled(), timeout=120000)
    assert window.hydrology_panel.result is not None
    assert request.output.is_dir()
    assert "Catchment preview unavailable" in window.report.toPlainText()
    window.close()
