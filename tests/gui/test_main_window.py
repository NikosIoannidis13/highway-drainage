"""Check the real window lifecycle through pytest-qt."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget
from pytestqt.qtbot import QtBot

from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import raster
from tests.support.crossings import crossing_service, request_for
from tests.support.dem import dem_service, plane
from tests.support.terrain_input import TRIANGLE, document, save, service


def test_main_window_opens_and_closes(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.isVisible()
    assert window.windowTitle() == "Highway Drainage"
    assert window.centralWidget() is not None

    assert window.close()
    assert not window.isVisible()


def test_multiple_file_selection_and_background_import(qtbot: QtBot, tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface(TRIANGLE)
    a = save(doc, tmp_path / "a.dxf")
    b = save(doc, tmp_path / "b.dxf")
    window = MainWindow(service())
    qtbot.addWidget(window)
    window.working_crs.setText("2100")
    for source in (a, b):
        source.path.with_suffix(".prj").write_text("EPSG:2100")
    window.add_files([str(a.path), str(b.path), str(a.path)])
    assert window.sources.rowCount() == 2
    window.show()
    window.import_button.click()
    qtbot.waitUntil(lambda: window.inputs.isEnabled(), timeout=10000)
    assert window.dataset is not None
    assert len(window.dataset.sources) == 2
    assert "2100" in window.dataset.sources[0].crs
    assert not window.sources.isColumnHidden(1)
    assert window.dataset.vertical_reference == "Survey elevations as supplied"
    assert not hasattr(window, "vertical_reference")
    assert len(window.dataset.features) == 1
    assert "duplicate" in window.report.toPlainText()
    window.working_crs.setText("EPSG:32635")
    assert window.dataset is None
    window.close()


def test_import_failure_restores_ui(qtbot: QtBot) -> None:
    window = MainWindow(service())
    qtbot.addWidget(window)
    window.show()
    window.import_button.click()
    qtbot.waitUntil(lambda: window.inputs.isEnabled(), timeout=10000)
    assert window.dataset is None
    assert "at least one DXF" in window.status.text()
    assert not window.cancel_button.isEnabled()
    window.close()


def test_gui_dem_export_and_resource_diagnostic(qtbot: QtBot, tmp_path: Path) -> None:
    dataset = plane(tmp_path)
    window = MainWindow(service(), dem_use_case=dem_service())
    qtbot.addWidget(window)
    window.working_crs.setText("EPSG:32634")
    dataset.sources[0].path.with_suffix(".prj").write_text("EPSG:32634")
    window.add_files([str(dataset.sources[0].path)])
    window.show()
    window.import_button.click()
    qtbot.waitUntil(lambda: window.inputs.isEnabled(), timeout=10000)
    assert window.export_panel.isEnabled()
    target = tmp_path / "gui.tif"
    window.export_panel.output.setText(str(target))
    window.export_panel.cell_size.setText("0.000001")
    window.export_panel.export_button.click()
    qtbot.waitUntil(lambda: window.inputs.isEnabled(), timeout=10000)
    assert "cells/samples" in window.report.toPlainText()
    assert "Recommended action" in window.report.toPlainText()
    assert not target.exists()
    window.export_panel.cell_size.setText("1")
    window.export_panel.export_button.click()
    qtbot.waitUntil(lambda: window.inputs.isEnabled(), timeout=10000)
    assert target.exists()
    assert "Exported 16 valid cells" in window.status.text()
    window.close()


def test_crossing_map_table_selection_and_invalidation(qtbot: QtBot, tmp_path: Path) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_line((0, 0), (10, 0))
    highway.modelspace().add_line((0, 4), (10, 4))
    culvert.modelspace().add_line((5, -1), (5, 5))
    request = request_for(tmp_path, highway, culvert)
    window = MainWindow(crossing_use_case=crossing_service())
    qtbot.addWidget(window)
    panel = window.crossing_panel
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(2)  # Crossing form is inside a scrollable tab page.
    panel.highway_path.setText(str(request.highway.path))
    panel.culvert_path.setText(str(request.culverts.path))
    panel.highway_crs.setText("EPSG:32634")
    panel.culvert_crs.setText("EPSG:32634")
    panel.set_project_raster(raster(tmp_path), "EPSG:32634", "EPSG:32634")
    window.show()
    panel.find_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=10000)
    assert panel.result is not None
    assert panel.table.rowCount() == 2
    assert len(panel.view.markers) == 2
    a, b = panel.result.points
    assert b.y > a.y
    assert panel.view.markers[b.identifier].pos().y() < panel.view.markers[a.identifier].pos().y()
    panel.table.selectRow(0)
    assert panel.view.markers[a.identifier].pen().color().name() == "#facc15"
    panel.view.fit_data()
    marker_position = panel.view.mapFromScene(panel.view.markers[b.identifier].scenePos())
    QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=marker_position)
    assert panel.table.currentRow() == 1
    assert panel.view.markers[b.identifier].pen().color().name() == "#facc15"
    panel.tolerance.setText("0.1")
    assert panel.result is None
    assert not panel.view.markers
    assert panel.table.rowCount() == 0
    window.close()


def test_crossing_failure_restores_controls(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(crossing_use_case=crossing_service())
    qtbot.addWidget(window)
    panel = window.crossing_panel
    panel.highway_path.setText(str(tmp_path / "missing_highway.dxf"))
    panel.culvert_path.setText(str(tmp_path / "missing_culvert.dxf"))
    panel.highway_crs.setText("EPSG:32634")
    panel.culvert_crs.setText("EPSG:32634")
    panel.set_project_raster(raster(tmp_path), "EPSG:32634", "EPSG:32634")
    panel.find_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=10000)
    assert "Cannot read" in window.status.text()
    assert panel.result is None
    assert not window.cancel_button.isEnabled()
    window.close()
