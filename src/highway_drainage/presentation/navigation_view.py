"""Shared, display-only camera behavior for the two persistent preview scenes."""

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


class NavigationView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # Keep viewport dimensions stable while fitting/zooming. Dragging still
        # uses Qt's internal scroll positions, without scrollbars appearing mid-fit.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fit_scale = 1.0
        self.setToolTip("Wheel: zoom at pointer · Left-drag: pan")

    def fit_data(self) -> None:
        """Fit all currently displayed geometry, leaving room to pan around it."""
        scene = self.scene()
        if scene is None or not scene.items():
            return
        bounds = scene.itemsBoundingRect()
        margin = max(bounds.width(), bounds.height(), 1.0) * 0.05
        bounds = bounds.adjusted(-margin, -margin, margin, margin)
        # A tight scene rectangle prevents dragging when the entire dataset fits.
        padding = max(bounds.width(), bounds.height()) * 64
        self.setSceneRect(bounds.adjusted(-padding, -padding, padding, padding))
        self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(bounds.center())
        self._fit_scale = math.hypot(self.transform().m11(), self.transform().m12())

    def reset_view(self) -> None:
        """Restore the default north-up, full-extent camera without touching data."""
        self.resetTransform()
        self._fit_scale = 1.0
        scene = self.scene()
        if scene is not None and scene.items():
            self.fit_data()
        else:
            self.setSceneRect(0, 0, 1, 1)
            self.centerOn(0, 0)

    def wheelEvent(self, event: QWheelEvent) -> None:
        scene = self.scene()
        delta = event.angleDelta().y() / 120.0
        if delta == 0:
            delta = event.pixelDelta().y() / 120.0
        if scene is None or not scene.items() or delta == 0:
            event.accept()
            return
        current = math.hypot(self.transform().m11(), self.transform().m12())
        if current <= 0 or not math.isfinite(current):
            self.reset_view()
            event.accept()
            return
        target = current * 1.25 ** max(-8.0, min(8.0, delta))
        target = min(self._fit_scale * 1024, max(self._fit_scale / 32, target))
        position = event.position().toPoint()
        before = self.mapToScene(position)
        self.scale(target / current, target / current)
        after = self.mapToScene(position)
        center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(center + before - after)
        event.accept()
