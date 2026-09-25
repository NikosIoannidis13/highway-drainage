"""Lightweight Qt plan view; consumes domain coordinates, performs no GIS analysis."""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsView,
    QRubberBand,
)

from highway_drainage.domain.crossings import CrossingResult, PointRole
from highway_drainage.domain.preview import RasterPreview
from highway_drainage.presentation.navigation_view import NavigationView
from highway_drainage.presentation.raster_item import raster_item, raster_transform


class CrossingView(NavigationView):
    coordinates_changed = Signal(str)
    point_selected = Signal(str)
    data_changed = Signal()
    selection_changed = Signal(object)
    manual_point_requested = Signal(float, float)

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
        self.selected_ids: set[str] = set()
        self.interaction_mode = "pan"
        self.editing_enabled = True
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())

    def set_interaction_mode(self, mode: str) -> None:
        self.interaction_mode = mode
        self._press_position = None
        self._rubber_band.hide()
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag if mode == "pan"
            else QGraphicsView.DragMode.NoDrag
        )
        self.viewport().setCursor(
            Qt.CursorShape.OpenHandCursor if mode == "pan" else Qt.CursorShape.CrossCursor
        )
        self.setToolTip({
            "pan": "Wheel: zoom at pointer · Left-drag: pan · Click: select point",
            "select": "Click or drag a rectangle to select points. Shift adds to selection.",
            "add_inlet": "Click to place an inlet for the culvert selected above.",
        }[mode])

    def set_raster_visible(self, visible: bool) -> None:
        """Change only the background visibility; keep cached data and overlays."""
        self._raster_visible = visible
        if self._raster_item is not None:
            self._raster_item.setVisible(visible)
            self.viewport().update()

    def fit_raster(self) -> None:
        """Frame the TIFF alone, even when the DXFs are far outside its extent."""
        if self._raster_item is not None and self._raster_item.isVisible():
            self.fit_bounds(self._raster_item.sceneBoundingRect())

    def raster_background_status(self) -> str:
        """Explain a blank display using loaded data only; never move/reproject either layer."""
        if self.snapshot is None or self._raster_item is None:
            return "No DEM preview is loaded. Open a ready raster TIFF first."
        if not any(self.snapshot.rgba[3::4]):
            return f"{self.snapshot.path.name}: the raster preview contains only NoData."
        raster_bounds = self._raster_item.sceneBoundingRect()
        outside: list[str] = []
        for category, label in (("h", "highway"), ("c", "culverts")):
            drawing_bounds: QRectF | None = None
            for (kind, _), item in self._lines.items():
                if kind != category:
                    continue
                bounds = item.sceneBoundingRect()
                drawing_bounds = (bounds if drawing_bounds is None
                                  else drawing_bounds.united(bounds))
            if drawing_bounds is not None and not drawing_bounds.intersects(raster_bounds):
                outside.append(label)
        if outside:
            return (
                f"{self.snapshot.path.name}: raster and {', '.join(outside)} extents "
                "do not overlap. Check source EPSG codes and coordinate units."
            )
        viewport_bounds = self.mapToScene(self.viewport().rect()).boundingRect()
        if not viewport_bounds.intersects(raster_bounds):
            return f"{self.snapshot.path.name}: raster is outside this view. Use Zoom to extents."
        return ""

    def set_raster(
        self, snapshot: RasterPreview | None, *, preserve_camera: bool = False,
    ) -> None:
        """Replace the cached background, without discarding engineering overlays."""
        transform, scene_rect = self.transform(), self.sceneRect()
        center = self.mapToScene(self.viewport().rect().center())
        origin, fit_scale = self._origin, self._fit_scale
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
        if preserve_camera:
            dx, dy = origin[0] - self._origin[0], self._origin[1] - origin[1]
            self.setSceneRect(scene_rect.translated(dx, dy))
            self.setTransform(transform)
            self._fit_scale = fit_scale
            self.centerOn(center.x() + dx, center.y() + dy)
        elif self.result is None and self._raster_item is not None:
            # Initialize the raster-only camera on load, even if its layer is hidden.
            self._raster_item.setVisible(True)
            self.fit_data()
            self._raster_item.setVisible(self._raster_visible)
        else:
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
        self.selected_ids.clear()
        self._rubber_band.hide()
        self._raster_item = None
        scene.clear()
        self.set_raster(self.snapshot)
        self.reset_view()
        self.data_changed.emit()
        self.selection_changed.emit(set())

    def show_result(self, result: CrossingResult, *, preserve_camera: bool = False) -> None:
        transform = self.transform()
        center = self.mapToScene(self.viewport().rect().center())
        origin = self._origin
        fit_scale = self._fit_scale
        selected = self.selected_ids.copy()
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
            marker.setBrush(QColor(self._role_color(point.role)))
            marker.setZValue(5)
            marker.setData(0, point.identifier)
            marker.setToolTip(
                f"{point.identifier}: E {point.x:.6f}, N {point.y:.6f}\n"
                f"Culvert {point.culvert.label}"
                "\nRole: "
                + ("Unassigned" if point.role == PointRole.UNCLASSIFIED
                   else point.role.value.title())
                + (" · manually placed" if point.manual else "")
            )
            scene.addItem(marker)
            self.markers[point.identifier] = marker
        self.fit_data()
        if preserve_camera:
            self.setTransform(transform)
            self._fit_scale = fit_scale
            self.centerOn(center.x() + origin[0] - self._origin[0],
                          center.y() - origin[1] + self._origin[1])
        self.set_selected(selected)
        self.data_changed.emit()

    def highlight(self, identifier: str) -> None:
        self.set_selected({identifier})
        if identifier in self.markers:
            self.centerOn(self.markers[identifier])

    @staticmethod
    def _role_color(role: PointRole) -> str:
        return {
            PointRole.INLET: "#16a34a", PointRole.OUTLET: "#2563eb",
            PointRole.UNCLASSIFIED: "#64748b",
        }[role]

    def set_selected(self, identifiers: set[str]) -> None:
        self.selected_ids = identifiers.intersection(self.markers)
        if self.result is None:
            return
        for (category, _), line in self._lines.items():
            line.setPen(self._pen("#1768ac" if category == "h" else "#d97706"))
            line.setZValue(0)
        for point in self.result.points:
            chosen = point.identifier in self.selected_ids
            marker = self.markers[point.identifier]
            marker.setBrush(QColor(self._role_color(point.role)))
            marker.setPen(self._pen("#facc15" if chosen else "#ffffff", 3 if chosen else 1))
            marker.setZValue(6 if chosen else 5)
            if chosen:
                refs = [("c", point.culvert), *(("h", ref) for ref in point.highways)]
                for category, ref in refs:
                    item = self._lines.get((category, ref.identifier))
                    if item is not None:
                        item.setPen(self._pen("#7c3aed", 3))
                        item.setZValue(2)
        self.selection_changed.emit(self.selected_ids.copy())

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
        if self.interaction_mode != "pan" and self.editing_enabled:
            if self._press_position is not None and self.interaction_mode == "select":
                self._rubber_band.setGeometry(
                    QRect(self._press_position, event.position().toPoint()).normalized()
                )
                if self._dragged:
                    self._rubber_band.show()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._dragged = False
        self._press_position = (
            event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        )
        if self.interaction_mode != "pan":
            if not self.editing_enabled:
                self._press_position = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        start = self._press_position
        self._press_position = None
        self._rubber_band.hide()
        if self.interaction_mode != "pan":
            if (
                start is None or not self.editing_enabled
                or event.button() != Qt.MouseButton.LeftButton
            ):
                event.accept()
                return
            end = event.position().toPoint()
            dragged = (end - start).manhattanLength() >= QApplication.startDragDistance()
            if self.interaction_mode == "add_inlet":
                if not dragged:
                    location = self.mapToScene(end)
                    self.manual_point_requested.emit(
                        location.x() + self._origin[0], -location.y() + self._origin[1],
                    )
            else:
                if dragged:
                    rectangle = QRect(start, end).normalized()
                    identifiers = {
                        identifier for identifier, marker in self.markers.items()
                        if rectangle.contains(self.mapFromScene(marker.scenePos()))
                    }
                else:
                    # Hit the point center in screen pixels even if a snapped marker overlays it.
                    near = [(self.mapFromScene(marker.scenePos()) - end).manhattanLength()
                            for marker in self.markers.values()]
                    identifier = list(self.markers)[near.index(min(near))] if near else None
                    identifiers = {identifier} if identifier and min(near) <= 10 else set()
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    identifiers |= self.selected_ids
                self.set_selected(identifiers)
            event.accept()
            return
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
                self.set_selected({item.data(0)})
                self.point_selected.emit(item.data(0))
