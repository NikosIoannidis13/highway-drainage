"""Lightweight Qt plan view; consumes domain coordinates, performs no GIS analysis."""

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
)

from highway_drainage.domain.crossings import CrossingResult
from highway_drainage.domain.preview import RasterPreview
from highway_drainage.presentation.navigation_view import NavigationView
from highway_drainage.presentation.raster_item import raster_item, raster_transform


class CrossingView(NavigationView):
    coordinates_changed = Signal(str)
    point_selected = Signal(str)
    data_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setBackgroundBrush(QColor("#fafbfc"))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.setMinimumHeight(180)
        self._origin = (0.0, 0.0)
        self._press_position: QPoint | None = None
        self._dragged = False
        self.result: CrossingResult | None = None
        self.markers: dict[str, QGraphicsEllipseItem] = {}
        self._lines: dict[tuple[str, str], QGraphicsPathItem] = {}
        self.snapshot: RasterPreview | None = None
        self._raster_item: QGraphicsPixmapItem | None = None
        self._raster_visible = False

    def set_raster_visible(self, visible: bool) -> None:
        """Change only the background visibility; keep cached data and overlays."""
        self._raster_visible = visible
        if self._raster_item is not None:
            scene = self.scene()
            assert scene is not None
            was_empty = not any(item.isVisible() for item in scene.items())
            self._raster_item.setVisible(visible)
            if visible and was_empty:
                self.fit_data()

    def set_raster(self, snapshot: RasterPreview | None) -> None:
        """Replace the cached background, without discarding engineering overlays."""
        scene = self.scene()
        assert scene is not None
        if self._raster_item is not None:
            scene.removeItem(self._raster_item)
            self._raster_item = None
        self.snapshot = snapshot
        if snapshot is not None:
            if self.result is None:
                self._origin = (snapshot.affine[2], snapshot.affine[5])
            self._raster_item = raster_item(snapshot, self._origin)
            self._raster_item.setVisible(self._raster_visible)
            scene.addItem(self._raster_item)
        self.fit_data()

    @staticmethod
    def _pen(color: str, width: float = 1.5) -> QPen:
        pen = QPen(QColor(color))
        pen.setCosmetic(True)
        pen.setWidthF(width)
        return pen

    def clear(self) -> None:
        scene = self.scene()
        assert scene is not None
        self.markers.clear()
        self._lines.clear()
        self.result = None
        self._raster_item = None
        scene.clear()
        self.set_raster(self.snapshot)
        self.reset_view()
        self.data_changed.emit()

    def show_result(self, result: CrossingResult) -> None:
        self.clear()
        self.result = result
        scene = self.scene()
        assert scene is not None
        all_lines = (*result.highways, *result.culverts)
        coordinates = [p for line in all_lines for p in line.vertices]
        coordinates.extend((p.x, p.y) for p in result.points)
        if not coordinates:
            return
        self._origin = (
            min(p[0] for p in coordinates),
            min(p[1] for p in coordinates),
        )
        ox, oy = self._origin
        if self._raster_item is not None and self.snapshot is not None:
            self._raster_item.setTransform(raster_transform(self.snapshot, self._origin))
        for category, lines, color in (
            ("h", result.highways, "#1768ac"),
            ("c", result.culverts, "#d97706"),
        ):
            for line in lines:
                path = QPainterPath()
                for i, (x, y) in enumerate(line.vertices):
                    display_point = QPointF(x - ox, -(y - oy))
                    if i == 0:
                        path.moveTo(display_point)
                    else:
                        path.lineTo(display_point)
                item = scene.addPath(path, self._pen(color))
                item.setToolTip(line.reference.label)
                self._lines[(category, line.reference.identifier)] = item
        for point in result.points:
            marker = QGraphicsEllipseItem(-4, -4, 8, 8)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.setPos(point.x - ox, -(point.y - oy))
            marker.setPen(self._pen("#ffffff", 1))
            marker.setBrush(QColor("#dc2626"))
            marker.setZValue(5)
            marker.setData(0, point.identifier)
            marker.setToolTip(
                f"{point.identifier}: E {point.x:.6f}, N {point.y:.6f}\n"
                f"Culvert {point.culvert.label}"
            )
            scene.addItem(marker)
            self.markers[point.identifier] = marker
        self.fit_data()

        self.data_changed.emit()

    def highlight(self, identifier: str) -> None:
        if self.result is None:
            return
        for (category, _), line in self._lines.items():
            line.setPen(self._pen("#1768ac" if category == "h" else "#d97706"))
            line.setZValue(0)
        for key, marker in self.markers.items():
            marker.setBrush(QColor("#fde047" if key == identifier else "#dc2626"))
            marker.setZValue(6 if key == identifier else 5)
        point = next((p for p in self.result.points if p.identifier == identifier), None)
        if point is None:
            return
        for category, ref in [("c", point.culvert), *(("h", ref) for ref in point.highways)]:
            item = self._lines[(category, ref.identifier)]
            item.setPen(self._pen("#7c3aed", 3))
            item.setZValue(2)
        self.centerOn(self.markers[identifier])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._press_position is not None
            and (event.position().toPoint() - self._press_position).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._dragged = True
        location = self.mapToScene(event.position().toPoint())
        self.coordinates_changed.emit(
            f"E {location.x() + self._origin[0]:.3f}   N {-location.y() + self._origin[1]:.3f}"
        )
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._dragged = False
        self._press_position = (
            event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        start = self._press_position
        self._press_position = None
        super().mouseReleaseEvent(event)
        if (
            start is not None
            and not self._dragged
            and event.button() == Qt.MouseButton.LeftButton
            and (event.position().toPoint() - start).manhattanLength()
            < QApplication.startDragDistance()
        ):
            item = self.itemAt(event.position().toPoint())
            if item is not None and isinstance(item.data(0), str):
                self.point_selected.emit(item.data(0))
