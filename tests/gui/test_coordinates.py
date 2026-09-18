"""Gui coverage for coordinates."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import candidates, raster


def test_gui_validation_and_invalidation(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(coordinate_use_case=ValidateCoordinates(RasterCoordinateInspector()))
    qtbot.addWidget(window)
    window.crossing_panel.show_result(candidates((103, 192.5), (110, 195)))
    panel = window.coordinate_panel
    panel.dem_path.setText(str(raster(tmp_path)))
    panel.validate_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=10000)
    assert panel.result is not None
    text = panel.report.toPlainText()
    assert "Row=2, column=1" in text and "no addressable cell" in text
    assert "DXF CRS assumption: EPSG:32634" in text
    assert "DEM bounds" in text and "DEM CRS" in text
    assert "ordering verified=True" in text
    window.crossing_panel.invalidate()
    assert panel.result is None and not panel.report.toPlainText()
    panel.validate_button.click()
    assert "Compute crossings" in window.status.text()
    window.close()
