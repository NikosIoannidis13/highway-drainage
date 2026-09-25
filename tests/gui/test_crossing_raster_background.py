"""Ready TIFF -> DXFs -> extraction retains a visible, correctly placed DEM background."""

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QTabWidget
from pytestqt.qtbot import QtBot

from highway_drainage.infrastructure.preview import RasterPreviewReader
from highway_drainage.infrastructure.project_raster import RasterProjectReader
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import raster
from tests.support.crossings import crossing_service, request_for
from tests.support.terrain_input import document


@pytest.mark.parametrize("enable_before_extraction", [False, True])
def test_ready_raster_survives_dxf_inputs_extraction_and_refresh(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    enable_before_extraction: bool,
) -> None:
    path = raster(tmp_path)
    highway, culvert = document(), document()
    # Both files falsely declare inches despite storing projected metre coordinates.
    highway.units = culvert.units = 1
    highway.modelspace().add_line((100, 195), (110, 195))
    culvert.modelspace().add_line((103, 190), (103, 200))
    request = request_for(tmp_path, highway, culvert)
    window = MainWindow(
        crossing_use_case=crossing_service(), project_rasters=RasterProjectReader(),
    )
    qtbot.addWidget(window)
    window.show()
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(2)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.crossing_panel.load_raster_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    preview = window.preview
    snapshot = preview.drainage_view.snapshot
    assert snapshot is not None

    def no_reread(*args: object, **kwargs: object) -> None:
        pytest.fail("Crossing extraction and background toggles should reuse the loaded preview")

    monkeypatch.setattr(RasterPreviewReader, "raster", no_reread)
    preview.show_raster_background.setChecked(enable_before_extraction)
    panel = window.crossing_panel
    panel.highway_path.setText(str(request.highway.path))
    panel.culvert_path.setText(str(request.culverts.path))
    panel.highway_crs.setText("32634")
    panel.culvert_crs.setText("32634")
    panel.find_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is not None and len(panel.result.points) == 1
    assert preview.mode.currentIndex() == 1
    preview.show_raster_background.setChecked(True)
    view = preview.drainage_view
    item = view._raster_item
    assert view.snapshot is snapshot and item is not None and item.isVisible()
    assert view.mapToScene(view.viewport().rect()).boundingRect().intersects(
        item.sceneBoundingRect()
    )
    a, b, c, d, e, f = snapshot.affine
    location = item.mapToScene(QPointF(1, 1))
    assert location.x() + view._origin[0] == pytest.approx(a + b + c)
    assert view._origin[1] - location.y() == pytest.approx(d + e + f)
    assert item.zValue() < next(iter(view.markers.values())).zValue()
    # A lost drawing layer can be restored directly from the already loaded project.
    preview.show_raster_background.setChecked(False)
    view.set_raster(None)
    preview.terrain_view.clear()
    transform = view.transform()
    center = view.mapToScene(view.viewport().rect().center())
    preview.show_raster_background.setChecked(True)
    assert view.snapshot is snapshot
    assert view._raster_item is not None and view._raster_item.isVisible()
    assert view.transform() == transform
    assert (view.mapToScene(view.viewport().rect().center()) - center).manhattanLength() < 1

    # A coordinate mismatch must be explained, never hidden by moving either layer.
    preview.show_raster_background.setChecked(False)
    a, b, c, d, e, f = snapshot.affine
    shifted = replace(snapshot, affine=(a, b, c + 2_000_000, d, e, f))
    view.set_raster(shifted, preserve_camera=True)
    transform = view.transform()
    center = view.mapToScene(view.viewport().rect().center())
    preview.show_raster_background.setChecked(True)
    assert preview.show_raster_background.text() == "Show raster background"
    assert "do not overlap" in preview.show_raster_background.toolTip()
    assert view.transform() == transform
    assert (view.mapToScene(view.viewport().rect().center()) - center).manhattanLength() < 1
    # An explicit click frames the entire TIFF, not the union with distant DXFs.
    preview.show_raster_background.setChecked(False)
    origin, result = view._origin, view.result
    marker_positions = {key: marker.scenePos() for key, marker in view.markers.items()}
    QTest.mouseClick(preview.show_raster_background, Qt.MouseButton.LeftButton)
    assert view._raster_item is not None and view._raster_item.isVisible()
    bounds = view._raster_item.sceneBoundingRect()
    viewport = view.viewport().rect()
    assert view.mapToScene(viewport).boundingRect().contains(bounds)
    screen_bounds = view.mapFromScene(bounds).boundingRect()
    assert max(screen_bounds.width() / viewport.width(),
               screen_bounds.height() / viewport.height()) > 0.7
    assert "do not overlap" in preview.show_raster_background.toolTip()
    assert view.snapshot is shifted and view._origin == origin and view.result is result
    assert marker_positions == {key: marker.scenePos() for key, marker in view.markers.items()}
    # Turning the background off leaves the camera alone.
    transform = view.transform()
    center = view.mapToScene(view.viewport().rect().center())
    QTest.mouseClick(preview.show_raster_background, Qt.MouseButton.LeftButton)
    assert view.transform() == transform
    assert (view.mapToScene(view.viewport().rect().center()) - center).manhattanLength() < 1
    # The same click also recovers an aligned TIFF after panning away.
    view.set_raster(snapshot, preserve_camera=True)
    view.centerOn(10000, 10000)
    QTest.mouseClick(preview.show_raster_background, Qt.MouseButton.LeftButton)
    assert view._raster_item is not None
    assert view.mapToScene(view.viewport().rect()).boundingRect().contains(
        view._raster_item.sceneBoundingRect()
    )
