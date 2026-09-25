"""Original crossings, containing cell outlines and selected centers in one plan view."""

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPolygonF
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem

from highway_drainage.domain.crossings import CrossingResult
from highway_drainage.domain.outlets import SnapResult
from highway_drainage.presentation.crossing_view import CrossingView


class OutletView(CrossingView):
    def show_outlets(self, result: SnapResult, crossings: CrossingResult | None = None) -> None:
        self.show_result(
            crossings or result.validation.crossings, preserve_camera=crossings is not None,
        )
        scene = self.scene()
        assert scene is not None
        ox, oy = self._origin
        a, b, c, d, e, f = result.validation.dem.affine
        for selection in result.outlets:
            original = selection.original
            point = original.point
            if (
                original.row is not None
                and original.column is not None
                and (
                    0 <= original.row < result.validation.dem.height
                    and 0 <= original.column < result.validation.dem.width
                )
            ):
                row, col = original.row, original.column
                polygon = QPolygonF(
                    [
                        QPointF(a * j + b * i + c - ox, -(d * j + e * i + f - oy))
                        for j, i in ((col, row), (col + 1, row), (col + 1, row + 1), (col, row + 1))
                    ]
                )
                item = scene.addPolygon(polygon, self._pen("#64748b"))
                item.setToolTip(f"{point.identifier}: containing DEM cell [{row}, {col}]")
            selected = selection.pour_point
            if selected is None:
                continue
            scene.addLine(
                point.x - ox,
                -(point.y - oy),
                selected.x - ox,
                -(selected.y - oy),
                self._pen("#16a34a"),
            )
            marker = QGraphicsEllipseItem(-6, -6, 12, 12)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.setPos(selected.x - ox, -(selected.y - oy))
            marker.setPen(self._pen("#16a34a", 2))
            marker.setBrush(QColor("transparent"))
            marker.setZValue(7)
            marker.setToolTip(
                f"{point.identifier}: {selection.status}\n"
                f"Pour point XY=({selected.x}, {selected.y}), "
                f"cell=[{selected.row}, {selected.column}], distance={selected.distance:g} m"
            )
            scene.addItem(marker)
        self.fit_data()
