"""Two persistent scenes; the selector changes visibility only."""

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox,
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
from highway_drainage.presentation.point_review_panel import PointReviewPanel
from highway_drainage.presentation.raster_item import raster_item
from highway_drainage.presentation.satellite_view import SatelliteView


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
        # Local origin avoids precision loss at survey coordinates; north is up.
        scene.addItem(raster_item(snapshot, (snapshot.affine[2], snapshot.affine[5])))
        self.fit_data()


class PreviewPanel(QWidget):
    satellite_update_requested = Signal()
    raster_background_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.boundaries: BoundaryPreview | None = None
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setAccessibleName("Preview mode")
        self.mode.addItems(["Terrain View", "Drainage View"])
        toolbar.addWidget(self.mode)
        self.review_points_button = QPushButton("Review inlets/outlets")
        self.review_points_button.setEnabled(False)
        self.review_points_button.clicked.connect(self._review_points)
        toolbar.addWidget(self.review_points_button)
        self.extents_button = QPushButton("Zoom to extents")
        self.extents_button.clicked.connect(self.fit_current)
        toolbar.addWidget(self.extents_button)
        self.reset_button = QPushButton("Reset view")
        self.reset_button.setToolTip("Restore the active preview to its full, north-up extent")
        self.reset_button.clicked.connect(self.reset_current)
        toolbar.addWidget(self.reset_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.earth_button = QPushButton("Open Google Earth Pro (separate window)")
        self.earth_button.setToolTip(
            "Export current layers to a local KMZ and launch the separate Google Earth Pro app. "
            "For satellite imagery here, use the Google satellite imagery checkbox."
        )
        self.earth_button.setEnabled(False)
        layout.addWidget(self.earth_button)
        self.show_satellite = QCheckBox("Google satellite imagery")
        self.show_satellite.setEnabled(False)
        layout.addWidget(self.show_satellite)
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
        self.point_review = PointReviewPanel(self.drainage_view)
        drainage_layout.addWidget(self.point_review)
        self.point_review.tool.currentIndexChanged.connect(self._point_tool_changed)
        self.show_raster_background = QCheckBox("Show raster background")
        self.show_raster_background.setToolTip("Display the loaded DEM beneath drainage results")
        self.show_raster_background.setEnabled(False)
        self.show_raster_background.toggled.connect(self._toggle_raster_background)
        self.show_raster_background.clicked.connect(self._focus_raster_background)
        drainage_layout.addWidget(self.show_raster_background)
        # A tooltip gives details without adding/removing rows and moving the map camera.
        self.show_raster_background.setToolTip("Show the loaded TIFF and fit its full extent.")
        drainage_layout.addWidget(self.drainage_view, 1)
        legend = QLabel(
            "Lines: blue highway, orange culverts. "
            "Points: green inlet, blue outlet, grey unassigned.\n"
            "Yellow outline: selected · Green rings: inlet pour points\n"
            "Grey: containing DEM pixels · Purple: catchment boundaries · North ↑"
        )
        legend.setWordWrap(True)
        drainage_layout.addWidget(legend)
        self.drainage_info = QLabel("Compute crossings to populate Drainage View.")
        self.drainage_info.setWordWrap(True)
        drainage_layout.addWidget(self.drainage_info)
        self.stack.addWidget(terrain)
        self.stack.addWidget(drainage)
        self.satellite_view = SatelliteView()
        self.stack.addWidget(self.satellite_view)
        layout.addWidget(self.stack)
        # Deliberately no loading, scene rebuilding or engineering callbacks here.
        self.mode.currentIndexChanged.connect(self._select_view)
        self.show_satellite.toggled.connect(self._select_view)
        self.show_raster_background.toggled.connect(self.satellite_update_requested)
        self.drainage_view.data_changed.connect(self.satellite_update_requested)
        self.drainage_view.data_changed.connect(self._update_raster_status)
        self.drainage_view.data_changed.connect(
            lambda: self.review_points_button.setEnabled(self.drainage_view.result is not None)
        )
        self.setMinimumWidth(350)

    def _select_view(self) -> None:
        if self.show_satellite.isChecked():
            self.point_review.tool.setCurrentIndex(0)
        self.stack.setCurrentIndex(
            2 if self.show_satellite.isChecked() else self.mode.currentIndex()
        )
        self.satellite_update_requested.emit()

    def _toggle_raster_background(self, visible: bool) -> None:
        if visible:
            if self.drainage_view.snapshot is None and self.terrain_view.snapshot is not None:
                self.drainage_view.set_raster(self.terrain_view.snapshot, preserve_camera=True)
            self.raster_background_requested.emit()
        self.drainage_view.set_raster_visible(visible)
        self._update_raster_status()

    def _focus_raster_background(self, checked: bool) -> None:
        if checked:
            self.drainage_view.fit_raster()
        self._update_raster_status()

    def _update_raster_status(self) -> None:
        message = (self.drainage_view.raster_background_status()
                   if self.show_raster_background.isChecked() else "")
        self.show_raster_background.setToolTip(
            message or "Show the loaded TIFF and fit its full extent."
        )

    def _review_points(self) -> None:
        self.show_satellite.setChecked(False)
        self.mode.setCurrentIndex(1)
        self.point_review.tool.setCurrentIndex(1)

    def _point_tool_changed(self) -> None:
        if self.point_review.tool.currentData() != "pan":
            self.show_satellite.setChecked(False)

    def fit_current(self) -> None:
        if self.show_satellite.isChecked():
            self.satellite_view.fit_data()
            return
        if self.mode.currentIndex() == 0:
            self.terrain_view.fit_data()
        else:
            self.drainage_view.fit_data()

    def reset_current(self) -> None:
        if self.show_satellite.isChecked():
            self.satellite_view.reset_view()
            return
        if self.mode.currentIndex() == 0:
            self.terrain_view.reset_view()
        else:
            self.drainage_view.reset_view()

    def show_raster(self, preview: RasterPreview) -> None:
        self.terrain_view.show_raster(preview)
        self.drainage_view.set_raster(preview)
        self.drainage_view.set_raster_visible(self.show_raster_background.isChecked())
        self.show_raster_background.setEnabled(True)
        self.terrain_info.setText(preview.information)
        self.satellite_update_requested.emit()

    def clear_terrain(self) -> None:
        self.terrain_view.clear()
        self.drainage_view.set_raster(None)
        self.show_raster_background.setEnabled(False)
        self.terrain_info.setText("DEM preview is not loaded for the current inputs.")
        self.satellite_update_requested.emit()

    def clear_boundaries(self) -> None:
        self.boundaries = None
        scene = self.drainage_view.scene()
        assert scene is not None
        for item in list(scene.items()):
            if item.data(1) == "catchment":
                scene.removeItem(item)
        self.satellite_update_requested.emit()

    def show_boundaries(self, preview: BoundaryPreview) -> None:
        self.clear_boundaries()
        self.boundaries = preview
        scene = self.drainage_view.scene()
        assert scene is not None
        ox, oy = self.drainage_view._origin
        for outline in preview.outlines:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            for ring in outline.rings:
                for i, (x, y) in enumerate(ring):
                    point = QPointF(x - ox, -(y - oy))
                    if i == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                path.closeSubpath()
            item = scene.addPath(
                path, self.drainage_view._pen("#7c3aed", 2), QBrush(QColor(124, 58, 237, 35))
            )
            item.setData(1, "catchment")
            item.setToolTip(f"Catchment {outline.identifier}")
            item.setZValue(-1)
        self.drainage_info.setText(
            f"{len(preview.outlines)} catchment outlines. {preview.diagnostic}"
        )
        self.drainage_view.fit_data()
        self.satellite_update_requested.emit()
