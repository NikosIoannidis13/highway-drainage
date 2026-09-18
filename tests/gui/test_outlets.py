"""Gui coverage for outlets."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from highway_drainage.presentation.main_window import MainWindow
from tests.support.outlets import request, service


def test_gui_outlet_selection_map_and_invalidation(qtbot: QtBot, tmp_path: Path) -> None:
    base = request(tmp_path, (105, 195.5))
    validator = ValidateCoordinates(RasterCoordinateInspector())
    window = MainWindow(coordinate_use_case=validator, outlet_use_case=service())
    qtbot.addWidget(window)
    panel = window.coordinate_panel
    window.crossing_panel.show_result(base.coordinates.crossings)
    panel.dem_path.setText(str(base.coordinates.dem))
    panel.snap_distance.setText("2")
    panel.snap_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=10000)
    assert panel.snap_result is not None
    assert "containing pixel=(1, 2), NoData" in panel.report.toPlainText()
    assert "Pour-point XY=(103.0, 195.5)" in panel.report.toPlainText()
    scene = panel.outlet_view.scene()
    assert scene is not None
    assert any("Pour point XY" in item.toolTip() for item in scene.items())
    assert len(panel.outlet_view.markers) == 1
    panel.snap_distance.setText("1")
    assert panel.snap_result is None
    # Shared Drainage View keeps the geometric crossings when only snapping changes.
    assert not any("Pour point XY" in item.toolTip() for item in scene.items())
    panel.snap_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=10000)
    assert panel.snap_result is not None and panel.snap_result.outlets[0].status == "rejected"
    window.close()
