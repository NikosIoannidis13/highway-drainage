"""Two persistent scenes; the selector changes visibility only."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainterPath, QPixmap, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.domain.preview import BoundaryPreview, RasterPreview
from highway_drainage.presentation.navigation_view import NavigationView
from highway_drainage.presentation.outlet_view import OutletView


class TerrainView(NavigationView):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot: RasterPreview | None = None

    def clear(self) -> None:
        self.snapshot = None
        scene = self.scene()
        assert scene is not None
        scene.clear()
        self.reset_view()

    def show_raster(self, snapshot: RasterPreview) -> None:
        self.clear()
        self.snapshot = snapshot
        scene = self.scene()
        assert scene is not None
        image = QImage(
            snapshot.rgba,
            snapshot.width,
            snapshot.height,
            snapshot.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        item = scene.addPixmap(QPixmap.fromImage(image))
        a, b, _, d, e, _ = snapshot.affine
        # Local origin avoids precision loss at survey coordinates; north is up.
        item.setTransform(QTransform(a, -d, b, -e, 0, 0))
        self.fit_data()


class PreviewPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.boundaries: BoundaryPreview | None = None
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setAccessibleName("Preview mode")
        self.mode.addItems(["Terrain View", "Drainage View"])
        toolbar.addWidget(self.mode)
        self.extents_button = QPushButton("Zoom to extents")
        self.extents_button.clicked.connect(self.fit_current)
        toolbar.addWidget(self.extents_button)
        self.reset_button = QPushButton("Reset view")
        self.reset_button.setToolTip("Restore the active preview to its full, north-up extent")
        self.reset_button.clicked.connect(self.reset_current)
        toolbar.addWidget(self.reset_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.stack = QStackedWidget()
        terrain = QWidget()
        terrain_layout = QVBoxLayout(terrain)
        self.terrain_view = TerrainView()
        self.terrain_info = QLabel("Import terrain or prepare a DEM to see terrain information.")
        self.terrain_info.setWordWrap(True)
        self.terrain_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        terrain_layout.addWidget(self.terrain_view, 1)
        terrain_layout.addWidget(self.terrain_info)
        drainage = QWidget()
        drainage_layout = QVBoxLayout(drainage)
        self.drainage_view = OutletView()
        drainage_layout.addWidget(self.drainage_view, 1)
        legend = QLabel(
            "Blue: highway · Orange: culverts · Red: crossings · Green rings: snapped outlets\n"
            "Grey: containing DEM pixels · Purple: catchment boundaries · North ↑"
        )
        legend.setWordWrap(True)
        drainage_layout.addWidget(legend)
        self.drainage_info = QLabel("Compute crossings to populate Drainage View.")
        self.drainage_info.setWordWrap(True)
        drainage_layout.addWidget(self.drainage_info)
        self.stack.addWidget(terrain)
        self.stack.addWidget(drainage)
        layout.addWidget(self.stack)
        # Deliberately no loading, scene rebuilding or engineering callbacks here.
        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.setMinimumWidth(350)

    def fit_current(self) -> None:
        if self.mode.currentIndex() == 0:
            self.terrain_view.fit_data()
        else:
            self.drainage_view.fit_data()

    def reset_current(self) -> None:
        if self.mode.currentIndex() == 0:
            self.terrain_view.reset_view()
        else:
            self.drainage_view.reset_view()

    def show_raster(self, preview: RasterPreview) -> None:
        self.terrain_view.show_raster(preview)
        self.terrain_info.setText(preview.information)

    def clear_terrain(self) -> None:
        self.terrain_view.clear()
        self.terrain_info.setText("DEM preview is not loaded for the current inputs.")

    def clear_boundaries(self) -> None:
        self.boundaries = None
        scene = self.drainage_view.scene()
        assert scene is not None
        for item in list(scene.items()):
            if item.data(1) == "catchment":
                scene.removeItem(item)

    def show_boundaries(self, preview: BoundaryPreview) -> None:
        self.clear_boundaries()
        self.boundaries = preview
        scene = self.drainage_view.scene()
        assert scene is not None
        ox, oy = self.drainage_view._origin
        for outline in preview.outlines:
            path = QPainterPath()
            for ring in outline.rings:
                for i, (x, y) in enumerate(ring):
                    point = QPointF(x - ox, -(y - oy))
                    if i == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                path.closeSubpath()
            item = scene.addPath(path, self.drainage_view._pen("#7c3aed", 2))
            item.setData(1, "catchment")
            item.setToolTip(f"Catchment {outline.identifier}")
            item.setZValue(-1)
        self.drainage_info.setText(
            f"{len(preview.outlines)} catchment outlines. {preview.diagnostic}"
        )
        self.drainage_view.fit_data()
