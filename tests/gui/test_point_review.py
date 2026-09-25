from dataclasses import replace
from pathlib import Path

import pytest
import shapefile
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QTabWidget
from pytestqt.qtbot import QtBot

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.application.point_export import crossing_export
from highway_drainage.domain.crossings import CadLine, PointRole
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from highway_drainage.infrastructure.point_export import ShapefilePointWriter
from highway_drainage.presentation.crossing_view import CrossingView
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import candidates
from tests.support.crossings import crossing_service
from tests.support.hydrology import chain
from tests.support.hydrology import service as hydrology_service
from tests.support.outlets import service as outlet_service


def drag(view: CrossingView, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(end),
        QPointF(view.viewport().mapToGlobal(end)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(view.viewport(), event)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)


def display(window: MainWindow, qtbot: QtBot) -> None:
    qtbot.addWidget(window)
    window.resize(1300, 1000)
    window.show()
    result = candidates((103, 197), (107, 197), (111, 197))
    result = replace(
        result, culverts=(CadLine(result.points[0].culvert, ((101, 197), (113, 197))),)
    )
    window._show_crossings(result)
    QApplication.processEvents()
    window.preview.drainage_view.fit_data()


def test_rectangle_click_roles_table_undo_and_camera(qtbot: QtBot) -> None:
    window = MainWindow()
    display(window, qtbot)
    view, controls = window.preview.drainage_view, window.preview.point_review
    controls.tool.setCurrentIndex(1)
    first = view.mapFromScene(view.markers["0"].scenePos())
    second = view.mapFromScene(view.markers["1"].scenePos())
    center = view.mapToScene(view.viewport().rect().center())
    transform = view.transform()
    drag(view, first - QPoint(12, 12), second + QPoint(12, 12))
    assert view.selected_ids == {"0", "1"}
    assert len(window.crossing_panel.table.selectionModel().selectedRows()) == 2
    controls.inlet_button.click()
    assert "2 inlets" in controls.counts.text()
    item = window.crossing_panel.table.item(0, 6)
    assert item is not None and item.text() == ""
    editor = window.crossing_panel.table.cellWidget(0, 6)
    assert isinstance(editor, QComboBox) and editor.currentText() == "Inlet"
    assert view.markers["0"].brush().color().name() == "#16a34a"
    assert view.markers["0"].pen().color().name() == "#facc15"
    assert view.transform() == transform
    assert (view.mapToScene(view.viewport().rect().center()) - center).manhattanLength() < 1
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=view.mapFromScene(view.markers["2"].scenePos()),
    )
    assert view.selected_ids == {"2"}
    controls.outlet_button.click()
    assert view.markers["2"].brush().color().name() == "#2563eb"
    controls.clear_button.click()
    assert view.markers["2"].brush().color().name() == "#64748b"
    controls.undo_button.click()
    assert view.markers["2"].brush().color().name() == "#2563eb"
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
        pos=view.mapFromScene(view.markers["0"].scenePos()),
    )
    assert view.selected_ids == {"0", "2"}
    window.preview.mode.setCurrentIndex(0)
    window.preview.show_satellite.setChecked(True)
    window.preview.review_points_button.click()
    assert window.preview.mode.currentIndex() == 1
    assert not window.preview.show_satellite.isChecked()
    assert controls.tool.currentData() == "select"
    assert "2 inlets" in controls.counts.text()
    window.crossing_panel.invalidate()
    assert not controls.tool.isEnabled() and not controls.undo_button.isEnabled()


def test_manual_inlet_coordinates_culvert_export_and_undo(qtbot: QtBot, tmp_path: Path) -> None:
    from threading import Event

    window = MainWindow()
    display(window, qtbot)
    view, controls = window.preview.drainage_view, window.preview.point_review
    controls.tool.setCurrentIndex(2)
    target = view.mapFromScene(QPointF(5, 1))
    expected = view.mapToScene(target)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    result = window.crossing_panel.result
    assert result is not None
    point = result.points[-1]
    assert point.manual and point.role == PointRole.INLET
    assert (point.x, point.y) == pytest.approx((expected.x() + 101, 197 - expected.y()))
    assert point.culvert == result.culverts[0].reference
    assert controls.tool.currentData() == "select"
    assert view.selected_ids == {point.identifier}
    assert view._lines[("c", point.culvert.identifier)].pen().widthF() == 3
    export = crossing_export(result, tmp_path / "reviewed.shp")
    ShapefilePointWriter().write(export, Event())
    with shapefile.Reader(str(export.path)) as reader:
        assert reader.record(-1)["culv_role"] == "inlet"
        assert reader.record(-1)["manual"]
    controls.undo_button.click()
    assert len(view.markers) == 3


def test_role_cell_dropdown_changes_only_its_point_and_supports_undo(qtbot: QtBot) -> None:
    window = MainWindow(crossing_use_case=crossing_service())
    display(window, qtbot)
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(2)
    table = window.crossing_panel.table
    view = window.preview.drainage_view
    view.set_selected({"0", "1"})
    transform = view.transform()
    editor = table.cellWidget(0, 6)
    assert isinstance(editor, QComboBox)
    item = table.item(0, 6)
    assert item is not None
    table.scrollToItem(item)
    assert [editor.itemText(i) for i in range(editor.count())] == [
        "Inlet", "Outlet", "Unassigned",
    ]
    assert editor.currentText() == "Unassigned"
    QTest.mouseClick(editor, Qt.MouseButton.LeftButton)
    assert editor.view().isVisible()
    QTest.keyClick(editor, Qt.Key.Key_Home)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: view.markers["0"].brush().color().name() == "#16a34a")
    assert window.crossing_panel.result is not None
    assert window.crossing_panel.result.points[1].role == PointRole.UNCLASSIFIED
    assert view.transform() == transform
    assert view.selected_ids == {"0"}  # Clicking the editor selects its row.
    window.preview.point_review.undo_button.click()
    editor = table.cellWidget(0, 6)
    assert isinstance(editor, QComboBox) and editor.currentText() == "Unassigned"
    editor.setCurrentIndex(editor.findData(PointRole.OUTLET.value))
    qtbot.waitUntil(lambda: view.markers["0"].brush().color().name() == "#2563eb")
    editor = table.cellWidget(0, 6)
    assert isinstance(editor, QComboBox)
    editor.setCurrentIndex(editor.findData(PointRole.UNCLASSIFIED.value))
    qtbot.waitUntil(lambda: view.markers["0"].brush().color().name() == "#64748b")


def test_only_inlets_processed_and_role_changes_invalidate_results(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow(
        coordinate_use_case=ValidateCoordinates(RasterCoordinateInspector()),
        outlet_use_case=outlet_service(),
        hydrology_use_case=hydrology_service(),
    )
    display(window, qtbot)
    request = chain(tmp_path)
    panel = window.coordinate_panel
    panel.dem_path.setText(str(request.prepared.request.coordinates.dem))
    panel.snap_distance.setText("0")
    panel.snap_button.click()
    assert window._thread is None
    assert "Mark at least one" in window.status.text()
    view, controls = window.preview.drainage_view, window.preview.point_review
    inlet = window.crossing_panel.table.cellWidget(0, 6)
    outlet = window.crossing_panel.table.cellWidget(1, 6)
    assert isinstance(inlet, QComboBox) and isinstance(outlet, QComboBox)
    inlet.setCurrentIndex(inlet.findData(PointRole.INLET.value))
    qtbot.waitUntil(lambda: view.markers["0"].brush().color().name() == "#16a34a")
    outlet = window.crossing_panel.table.cellWidget(1, 6)
    assert isinstance(outlet, QComboBox)
    outlet.setCurrentIndex(outlet.findData(PointRole.OUTLET.value))
    qtbot.waitUntil(lambda: view.markers["1"].brush().color().name() == "#2563eb")
    panel.validate_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is not None and len(panel.result.outlets) == 1
    panel.snap_button.click()
    assert not controls.isEnabled()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.snap_result is not None and len(panel.snap_result.outlets) == 1
    assert panel.snap_result.outlets[0].original.point.identifier == "0"
    assert len(view.markers) == 3
    assert view.markers["1"].brush().color().name() == "#2563eb"
    window.hydrology_panel.output.setText(str(tmp_path / "inlet_catchments"))
    window.hydrology_panel.run_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=120000)
    result = window.hydrology_panel.result
    assert result is not None, window.report.toPlainText()
    assert len(result.catchments) == 1
    inlet = window.crossing_panel.table.cellWidget(0, 6)
    assert isinstance(inlet, QComboBox)
    inlet.setCurrentIndex(inlet.findData(PointRole.OUTLET.value))
    qtbot.waitUntil(lambda: panel.snap_result is None)
    assert panel.snap_result is None
    assert window.hydrology_panel.result is None
    assert window.preview.boundaries is None
    assert len(view.markers) == 3
