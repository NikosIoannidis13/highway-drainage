"""Display a cached raster in the same local, north-up frame as CAD overlays."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem

from highway_drainage.domain.preview import RasterPreview


def raster_item(snapshot: RasterPreview, origin: tuple[float, float]) -> QGraphicsPixmapItem:
    image = QImage(
        snapshot.rgba,
        snapshot.width,
        snapshot.height,
        snapshot.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    item.setTransform(raster_transform(snapshot, origin))
    item.setZValue(-10)
    item.setData(1, "raster")
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    return item


def raster_transform(snapshot: RasterPreview, origin: tuple[float, float]) -> QTransform:
    a, b, c, d, e, f = snapshot.affine
    ox, oy = origin
    return QTransform(a, -d, b, -e, c - ox, -(f - oy))
