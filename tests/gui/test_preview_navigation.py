"""Exercise real mouse events against both preview cameras; no GIS calculations."""

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from highway_drainage.presentation.crossing_view import CrossingView
from highway_drainage.presentation.navigation_view import NavigationView
from highway_drainage.presentation.preview_panel import PreviewPanel, TerrainView
from tests.support.coordinates import candidates


def wheel(view: NavigationView, position: QPoint, delta: int) -> None:
    event = QWheelEvent(
        QPointF(position),
        QPointF(view.viewport().mapToGlobal(position)),
        QPoint(),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(view.viewport(), event)


def drag(view: NavigationView, start: QPoint, end: QPoint) -> None:
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


@pytest.fixture(params=[TerrainView, CrossingView], ids=["terrain", "drainage"])
def view(request: pytest.FixtureRequest, qtbot: QtBot) -> Iterator[NavigationView]:
    widget: NavigationView = request.param()
    qtbot.addWidget(widget)
    widget.resize(500, 400)
    scene = widget.scene()
    assert scene is not None
    scene.addRect(0, 0, 100, 80)
    widget.show()
    QApplication.processEvents()
    widget.fit_data()
    yield widget
    widget.close()


def test_wheel_zoom_at_pointer_and_zero_delta(view: NavigationView) -> None:
    position = QPoint(170, 130)
    anchor = view.mapToScene(position)
    initial = view.transform().m11()
    wheel(view, position, 120)
    assert view.transform().m11() == pytest.approx(initial * 1.25)
    mapped = view.mapFromScene(anchor)
    assert (mapped - position).manhattanLength() <= 3  # integer scrollbar rounding
    wheel(view, position, -120)
    assert view.transform().m11() == pytest.approx(initial)
    transform = view.transform()
    wheel(view, position, 0)
    assert view.transform() == transform


def test_drag_at_full_extent_then_fit_and_reset(view: NavigationView) -> None:
    scene = view.scene()
    assert scene is not None
    original_items = scene.items()
    center = view.viewport().rect().center()
    initial = view.mapToScene(center)
    drag(view, center, center + QPoint(50, 30))
    moved = view.mapToScene(center)
    assert moved.x() < initial.x() and moved.y() < initial.y()
    view.fit_data()
    assert abs(view.mapToScene(center).x() - initial.x()) < 1
    wheel(view, center, 240)
    view.rotate(20)  # Reset also removes any accidental camera orientation.
    view.reset_view()
    assert view.transform().m12() == 0 and view.transform().m21() == 0
    assert abs(view.mapToScene(center).x() - initial.x()) < 1
    assert scene.items() == original_items


def test_zoom_limits_remain_recoverable(view: NavigationView) -> None:
    center = view.viewport().rect().center()
    initial = view.transform().m11()
    for _ in range(70):
        wheel(view, center, 960)
    assert view.transform().m11() == pytest.approx(initial * 1024)
    for _ in range(70):
        wheel(view, center, -960)
    assert view.transform().m11() == pytest.approx(initial / 32)
    view.reset_view()
    assert view.transform().m11() == pytest.approx(initial)


def test_switches_preserve_each_camera_and_buttons_target_active_view(qtbot: QtBot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.resize(700, 600)
    panel.show()
    QApplication.processEvents()
    for view in (panel.terrain_view, panel.drainage_view):
        scene = view.scene()
        assert scene is not None
        scene.addRect(0, 0, 100, 80)
    panel.terrain_view.fit_data()
    center = panel.terrain_view.viewport().rect().center()
    wheel(panel.terrain_view, center, 240)
    drag(panel.terrain_view, center, center + QPoint(40, 30))
    terrain_transform = panel.terrain_view.transform()
    terrain_scroll = (
        panel.terrain_view.horizontalScrollBar().value(),
        panel.terrain_view.verticalScrollBar().value(),
    )
    panel.mode.setCurrentIndex(1)
    panel.drainage_view.fit_data()
    center = panel.drainage_view.viewport().rect().center()
    wheel(panel.drainage_view, center, 120)
    drag(panel.drainage_view, center, center + QPoint(-30, -20))
    drainage_transform = panel.drainage_view.transform()
    drainage_scroll = (
        panel.drainage_view.horizontalScrollBar().value(),
        panel.drainage_view.verticalScrollBar().value(),
    )
    for index in [0, 1] * 5:
        panel.mode.setCurrentIndex(index)
    assert panel.terrain_view.transform() == terrain_transform
    assert panel.drainage_view.transform() == drainage_transform
    assert terrain_scroll == (
        panel.terrain_view.horizontalScrollBar().value(),
        panel.terrain_view.verticalScrollBar().value(),
    )
    assert drainage_scroll == (
        panel.drainage_view.horizontalScrollBar().value(),
        panel.drainage_view.verticalScrollBar().value(),
    )
    panel.reset_button.click()
    assert panel.drainage_view.transform() != drainage_transform
    assert panel.terrain_view.transform() == terrain_transform
    panel.mode.setCurrentIndex(0)
    panel.extents_button.click()
    assert panel.terrain_view.transform() != terrain_transform
    panel.close()


def test_marker_drag_does_not_select_but_click_does(qtbot: QtBot) -> None:
    view = CrossingView()
    qtbot.addWidget(view)
    view.resize(500, 400)
    view.show()
    QApplication.processEvents()
    view.show_result(candidates((100, 100), (120, 120)))
    selected: list[str] = []
    view.point_selected.connect(selected.append)
    marker = view.markers["0"]
    start = view.mapFromScene(marker.scenePos())
    drag(view, start, start + QPoint(30, 30))
    assert not selected
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(marker.scenePos())
    )
    assert selected == ["0"]
    view.close()


def test_empty_views_allow_navigation_controls(qtbot: QtBot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    for index in (0, 1):
        panel.mode.setCurrentIndex(index)
        panel.extents_button.click()
        panel.reset_button.click()
    assert panel.terrain_view.transform().isIdentity()
    assert panel.drainage_view.transform().isIdentity()
