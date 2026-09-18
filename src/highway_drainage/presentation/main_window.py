"""Terrain input forms and reporting; all engineering work runs in the use case."""

from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.application.crossings import FindCrossings
from highway_drainage.application.dem import GenerateDem
from highway_drainage.application.earth_preview import EarthPreview, EarthPreviewWriter
from highway_drainage.application.hydrology import DelineateCatchments
from highway_drainage.application.outlets import SelectOutlets
from highway_drainage.application.preview import PreviewReader
from highway_drainage.application.project_raster import ProjectRaster, ProjectRasterReader
from highway_drainage.application.satellite import SatelliteBuilder, SatelliteRequest
from highway_drainage.application.terrain import ImportTerrain
from highway_drainage.domain.coordinates import CoordinateReport, CoordinateRequest
from highway_drainage.domain.crossings import CrossingResult
from highway_drainage.domain.dem import DemResult
from highway_drainage.domain.hydrology import HydrologyResult
from highway_drainage.domain.outlets import SnapResult
from highway_drainage.domain.preview import RasterPreview
from highway_drainage.domain.terrain import LineRole, TerrainDataset, TerrainRequest, TerrainSource
from highway_drainage.presentation.coordinate_panel import CoordinatePanel
from highway_drainage.presentation.coordinate_worker import CoordinateWorker
from highway_drainage.presentation.crossing_panel import CrossingPanel
from highway_drainage.presentation.crossing_worker import CrossingWorker
from highway_drainage.presentation.dem_panel import DemPanel
from highway_drainage.presentation.dem_worker import DemWorker
from highway_drainage.presentation.earth_launcher import open_google_earth
from highway_drainage.presentation.earth_worker import EarthPreviewWorker
from highway_drainage.presentation.hydrology_panel import HydrologyPanel
from highway_drainage.presentation.hydrology_worker import HydrologyWorker
from highway_drainage.presentation.outlet_worker import OutletWorker
from highway_drainage.presentation.preview_panel import PreviewPanel
from highway_drainage.presentation.raster_worker import RasterWorker
from highway_drainage.presentation.task_progress import TaskProgress
from highway_drainage.presentation.terrain_worker import TerrainWorker

_SUPPLIED_ELEVATIONS = "Survey elevations as supplied"


class MainWindow(QMainWindow):
    """Select sources, declare their meaning, and inspect import findings."""

    def __init__(
        self,
        use_case: ImportTerrain | None = None,
        parent: QWidget | None = None,
        dem_use_case: GenerateDem | None = None,
        crossing_use_case: FindCrossings | None = None,
        coordinate_use_case: ValidateCoordinates | None = None,
        outlet_use_case: SelectOutlets | None = None,
        hydrology_use_case: DelineateCatchments | None = None,
        previews: PreviewReader | None = None,
        project_rasters: ProjectRasterReader | None = None,
        earth_writer: EarthPreviewWriter | None = None,
        satellite_builder: SatelliteBuilder | None = None,
    ) -> None:
        super().__init__(parent)
        self._use_case = use_case
        self._dem_use_case = dem_use_case
        self._crossing_use_case = crossing_use_case
        self._coordinate_use_case = coordinate_use_case
        self._outlet_use_case = outlet_use_case
        self._hydrology_use_case = hydrology_use_case
        self._previews = previews
        self._project_rasters = project_rasters
        self._earth_writer = earth_writer
        self.active_raster: ProjectRaster | None = None
        self._task_failed = False
        self._thread: QThread | None = None
        self._worker: (
            RasterWorker
            | TerrainWorker
            | DemWorker
            | CrossingWorker
            | CoordinateWorker
            | OutletWorker
            | HydrologyWorker
            | EarthPreviewWorker
            | None
        ) = None
        self._cancel = Event()
        self._closing = False
        self.dataset: TerrainDataset | None = None
        self.setWindowTitle("Highway Drainage")
        self.resize(1100, 800)
        root = QWidget()
        layout = QVBoxLayout(root)
        raster_row = QHBoxLayout()
        self.open_raster_button = QPushButton("Open existing DEM / GeoTIFF?")
        self.open_raster_button.clicked.connect(self._choose_raster)
        self.open_raster_button.setEnabled(project_rasters is not None)
        raster_row.addWidget(self.open_raster_button)
        self.project_label = QLabel(
            "Project CRS: load a raster, or import georeferenced terrain first"
        )
        self.project_label.setWordWrap(True)
        raster_row.addWidget(self.project_label, 1)
        layout.addLayout(raster_row)
        self.inputs = QWidget()
        form_layout = QVBoxLayout(self.inputs)
        form = QFormLayout()
        self.working_crs = QLineEdit(self)
        self.working_crs.setReadOnly(True)
        self.working_crs.hide()
        self.working_crs.setPlaceholderText("Projected CRS in metres, e.g. 2100")
        form_layout.addLayout(form)
        form_layout.addWidget(
            QLabel(
                "CRS comes from the active raster. DXF metadata and units are read automatically. "
                "DXFs without CRS metadata use the raster CRS. Known drawing units are converted."
            )
        )
        buttons = QHBoxLayout()
        add = QPushButton("Add DXF files…")
        add.clicked.connect(self._choose_files)
        remove = QPushButton("Remove selected files")
        remove.clicked.connect(self._remove_files)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        form_layout.addLayout(buttons)
        self.sources = QTableWidget(0, 4)
        self.sources.setHorizontalHeaderLabels(
            ["DXF file", "Source CRS", "Z units", "Linework role"]
        )
        self.sources.horizontalHeader().setStretchLastSection(True)
        self.sources.setColumnWidth(0, 230)
        self.sources.setColumnHidden(1, True)
        form_layout.addWidget(self.sources)
        form_layout.addWidget(
            QLabel(
                "Layer overrides (exact layer names, applied to all selected files). "
                "3DFACE geometry always retains its surface role."
            )
        )
        self.roles = QTableWidget(0, 2)
        self.roles.setHorizontalHeaderLabels(["Layer", "Linework role"])
        self.roles.setMaximumHeight(130)
        form_layout.addWidget(self.roles)
        role_buttons = QHBoxLayout()
        add_role = QPushButton("Add layer override")
        add_role.clicked.connect(self._add_role)
        remove_role = QPushButton("Remove selected overrides")
        remove_role.clicked.connect(self._remove_roles)
        role_buttons.addWidget(add_role)
        role_buttons.addWidget(remove_role)
        form_layout.addLayout(role_buttons)
        self.import_button = QPushButton("Import and validate terrain")
        self.import_button.setEnabled(use_case is not None)
        self.import_button.clicked.connect(self._start_import)
        form_layout.addWidget(self.import_button)
        self.export_panel = DemPanel()
        self.export_panel.setEnabled(False)
        self.export_panel.export_requested.connect(self._start_export)
        self.preview = PreviewPanel()
        self.preview.show_satellite.setEnabled(satellite_builder is not None)
        self.preview.satellite_view.builder = satellite_builder
        self._satellite_timer = QTimer(self)
        self._satellite_timer.setSingleShot(True)
        self._satellite_timer.setInterval(50)
        self._satellite_timer.timeout.connect(self._refresh_satellite)
        self.preview.satellite_update_requested.connect(self._satellite_timer.start)
        self.preview.satellite_view.idle.connect(self._satellite_idle)
        self.preview.earth_button.setEnabled(earth_writer is not None)
        self.preview.earth_button.clicked.connect(self._start_earth_preview)
        self.crossing_panel = CrossingPanel(self.preview.drainage_view)
        self.crossing_panel.setEnabled(crossing_use_case is not None)
        self.crossing_panel.find_requested.connect(self._start_crossings)
        self.crossing_panel.raster_requested.connect(self.load_raster)
        self.coordinate_panel = CoordinatePanel(self.preview.drainage_view)
        self.coordinate_panel.setEnabled(coordinate_use_case is not None)
        self.coordinate_panel.validate_requested.connect(self._start_coordinates)
        self.coordinate_panel.snap_requested.connect(self._start_outlets)
        self.coordinate_panel.snap_button.setEnabled(outlet_use_case is not None)
        self.crossing_panel.invalidated.connect(self.coordinate_panel.invalidate)
        tabs = QTabWidget()
        tabs.addTab(self.inputs, "Terrain input")
        tabs.addTab(self.export_panel, "DEM / GeoTIFF")
        tabs.addTab(self.crossing_panel, "Highway / culvert crossings")
        tabs.addTab(self.coordinate_panel, "Outlet coordinate validation")
        self.hydrology_panel = HydrologyPanel()
        self.hydrology_panel.setEnabled(hydrology_use_case is not None)
        self.hydrology_panel.run_requested.connect(self._start_hydrology)
        self.coordinate_panel.invalidated.connect(self.hydrology_panel.invalidate)
        tabs.addTab(self.hydrology_panel, "Catchments")
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # Forms can scroll on smaller screens while the map retains useful space.
        tabs.setUsesScrollButtons(True)
        for index in range(tabs.count()):
            page = tabs.widget(index)
            title = tabs.tabText(index)
            assert page is not None
            for label in page.findChildren(QLabel):
                label.setWordWrap(True)
            tabs.removeTab(index)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            tabs.insertTab(index, scroll, title)
        splitter.addWidget(tabs)
        splitter.addWidget(self.preview)
        splitter.setSizes([470, 630])
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.cancel_button = QPushButton("Cancel operation")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_import)
        self.task_progress = TaskProgress()
        layout.addWidget(self.task_progress)
        layout.addWidget(self.cancel_button)
        self.status = QLabel("Select DXF files and declare their coordinate reference and roles.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(110)
        layout.addWidget(self.report)
        self.setCentralWidget(root)
        self.sources.itemChanged.connect(self._invalidate)
        self.roles.itemChanged.connect(self._invalidate)
        self.working_crs.textChanged.connect(self._invalidate)
        self.coordinate_panel.invalidated.connect(self._invalidate_drainage_preview)
        self.coordinate_panel.dem_path.textChanged.connect(self.preview.clear_terrain)
        self.coordinate_panel.raster_requested.connect(self.load_raster)
        self.coordinate_panel.dem_path.setReadOnly(True)
        self.hydrology_panel.output.textChanged.connect(self.preview.clear_boundaries)
        self.hydrology_panel.minimum_cells.textChanged.connect(self.preview.clear_boundaries)

    def _begin_task(self) -> None:
        self._task_failed = False
        self.open_raster_button.setEnabled(False)
        self.preview.earth_button.setEnabled(False)
        self.hydrology_panel.setEnabled(False)
        self.task_progress.begin(self.status.text())

    def _choose_raster(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open elevation raster", "", "GeoTIFF (*.tif *.tiff)"
        )
        if path:
            self.load_raster(path)

    @Slot(str)
    def load_raster(self, path: str) -> None:
        if self._thread is not None or self._project_rasters is None:
            return
        for panel in (
            self.inputs,
            self.export_panel,
            self.crossing_panel,
            self.coordinate_panel,
            self.hydrology_panel,
        ):
            panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Reading raster metadata and terrain preview?")
        self._cancel = Event()
        thread = QThread(self)
        worker = RasterWorker(self._project_rasters, Path(path), self._cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_project_raster)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _show_project_raster(self, raster: ProjectRaster) -> None:
        self.active_raster = raster
        self.dataset = None
        self.export_panel.setEnabled(False)
        self.working_crs.blockSignals(True)
        self.working_crs.setText(raster.crs)
        self.working_crs.blockSignals(False)
        self.crossing_panel.set_project_raster(raster.path, raster.crs, raster.crs_label)
        self.crossing_panel.invalidate()
        self.coordinate_panel.dem_path.setText(str(raster.path))
        self.coordinate_panel.invalidate()
        self.hydrology_panel.invalidate()
        self.preview.clear_boundaries()
        self.preview.show_raster(raster.preview)
        self.preview.mode.setCurrentIndex(0)
        self.project_label.setText(f"Project CRS: {raster.crs_label}")
        self.status.setText(
            f"Loaded {raster.path.name}. Ready for DXF crossings and outlet preparation."
        )

    @Slot()
    def _invalidate_drainage_preview(self) -> None:
        self.preview.clear_boundaries()
        if self.crossing_panel.result is None:
            self.preview.drainage_view.clear()
            self.preview.drainage_info.setText("Compute crossings to populate Drainage View.")
        else:
            self.preview.drainage_view.show_result(self.crossing_panel.result)
            self.preview.drainage_info.setText(
                "Crossings retained; prepare outlets for these inputs."
            )

    @Slot()
    def _invalidate(self) -> None:
        self.dataset = None
        if self.active_raster is None:
            self.preview.clear_terrain()
        self.export_panel.setEnabled(False)
        self.report.clear()
        self.status.setText("Inputs changed. Import again to validate.")

    def _role_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItems([role.value for role in LineRole])
        combo.currentTextChanged.connect(self._invalidate)
        return combo

    @Slot()
    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select terrain DXFs", "", "DXF files (*.dxf)"
        )
        self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        existing = {
            Path(self._text(self.sources, row, 0)) for row in range(self.sources.rowCount())
        }
        for name in paths:
            path = Path(name).resolve()
            if path in existing:
                continue
            existing.add(path)
            row = self.sources.rowCount()
            self.sources.insertRow(row)
            item = QTableWidgetItem(str(path))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sources.setItem(row, 0, item)
            self.sources.setItem(row, 1, QTableWidgetItem(""))
            units = QComboBox()
            units.addItems(["auto", "m", "ft"])
            units.currentTextChanged.connect(self._invalidate)
            self.sources.setCellWidget(row, 2, units)
            self.sources.setCellWidget(row, 3, self._role_combo())

    @Slot()
    def _remove_files(self) -> None:
        for row in sorted({i.row() for i in self.sources.selectedIndexes()}, reverse=True):
            self.sources.removeRow(row)
        self._invalidate()

    @Slot()
    def _add_role(self) -> None:
        row = self.roles.rowCount()
        self.roles.insertRow(row)
        self.roles.setItem(row, 0, QTableWidgetItem(""))
        self.roles.setCellWidget(row, 1, self._role_combo())

    @Slot()
    def _remove_roles(self) -> None:
        for row in sorted({i.row() for i in self.roles.selectedIndexes()}, reverse=True):
            self.roles.removeRow(row)
        self._invalidate()

    @staticmethod
    def _text(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _choice(table: QTableWidget, row: int, column: int) -> str:
        widget = table.cellWidget(row, column)
        assert isinstance(widget, QComboBox)
        return widget.currentText()

    @Slot()
    def _start_import(self) -> None:
        if self._use_case is None or self._thread is not None:
            return
        overrides = tuple(
            (self._text(self.roles, row, 0), LineRole(self._choice(self.roles, row, 1)))
            for row in range(self.roles.rowCount())
        )
        request = TerrainRequest(
            tuple(
                TerrainSource(
                    Path(self._text(self.sources, row, 0)),
                    self._text(self.sources, row, 1),
                    _SUPPLIED_ELEVATIONS,
                    self._choice(self.sources, row, 2),
                    LineRole(self._choice(self.sources, row, 3)),
                    overrides,
                    fallback_crs=self.working_crs.text().strip(),
                )
                for row in range(self.sources.rowCount())
            ),
            self.working_crs.text().strip(),
            _SUPPLIED_ELEVATIONS,
        )
        self._invalidate()
        self.inputs.setEnabled(False)
        self.crossing_panel.setEnabled(False)
        self.coordinate_panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Reading and validating DXFs…")
        self._cancel = Event()
        thread = QThread(self)
        worker = TerrainWorker(self._use_case, request, self._cancel)
        worker.progress.connect(self._dem_progress)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_result)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _show_result(self, dataset: TerrainDataset) -> None:
        self.dataset = dataset
        if self.active_raster is None:
            self.working_crs.blockSignals(True)
            self.working_crs.setText(dataset.crs_wkt)
            self.working_crs.blockSignals(False)
            self.project_label.setText(f"Project CRS: {dataset.crs_label} (from imported terrain)")
        self.preview.terrain_info.setText(
            f"{len(dataset.sources)} terrain DXFs; {len(dataset.features)} features; "
            f"{len(dataset.issues)} findings.\nCRS: {dataset.crs_label}\n"
            "Export a DEM for raster preview."
        )
        faces = sum(feature.is_face for feature in dataset.features)
        self.status.setText(
            f"{faces} faces, {len(dataset.features) - faces} line features, "
            f"{len(dataset.issues)} findings. "
            + ("Errors require review." if dataset.has_errors else "Review findings before use.")
        )
        lines = [
            f"{issue.severity.upper()} [{issue.code}] {issue.reference.path.name} "
            f"layer={issue.reference.layer} handle={issue.reference.handle}: {issue.message}"
            + (
                f" Original: {issue.related.path.name}/{issue.related.handle}"
                if issue.related
                else ""
            )
            for issue in dataset.issues[:500]
        ]
        if len(dataset.issues) > 500:
            lines.append("Showing the first 500 findings; the full report remains in memory.")
        self.report.setPlainText("\n".join(lines) if lines else "No validation findings.")

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self._task_failed = True
        self.status.setText(message)
        self.report.appendPlainText(message)

    @Slot()
    def _start_export(self) -> None:
        if self.dataset is None or self._dem_use_case is None or self._thread is not None:
            return
        try:
            request = self.export_panel.request(self.dataset)
            if request.output.exists():
                if not self._confirm_overwrite(request.output, directory=False):
                    return
                request = replace(request, overwrite=True)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.inputs.setEnabled(False)
        self.export_panel.setEnabled(False)
        self.crossing_panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Checking raster and terrain resource requirements…")
        self.coordinate_panel.setEnabled(False)
        self._cancel = Event()
        thread = QThread(self)
        worker = DemWorker(self._dem_use_case, request, self._cancel, self._previews)
        worker.raster_ready.connect(self._show_generated_preview)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_dem_result)
        worker.failed.connect(self._show_error)
        worker.progress.connect(self._dem_progress)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _show_generated_preview(self, preview: RasterPreview) -> None:
        if self.dataset is not None:
            self.active_raster = ProjectRaster(
                preview.path, self.dataset.crs_wkt, self.dataset.crs_label, preview
            )
        self.preview.show_raster(preview)

    @Slot(str)
    def _dem_progress(self, message: str) -> None:
        self.task_progress.update_message(message)
        self.status.setText(message)
        if not message.startswith("Writing DEM"):
            self.report.appendPlainText(message)

    @Slot(object)
    def _show_dem_result(self, result: DemResult) -> None:
        # Replacing a TIFF can change its grid without changing the path text.
        self.coordinate_panel.invalidate()
        self.preview.clear_terrain()
        self.coordinate_panel.dem_path.setText(str(result.output))
        if self.dataset is not None:
            self.crossing_panel.set_project_raster(
                result.output, self.dataset.crs_wkt, self.dataset.crs_label
            )
            self.project_label.setText(f"Project CRS: {self.dataset.crs_label} (generated DEM)")
        self.status.setText(f"Exported {result.valid_cells:,} valid cells to {result.output}")
        self.report.appendPlainText(f"GeoTIFF complete: {result.output}\n{result.plan.describe()}")

    @Slot()
    def _start_crossings(self) -> None:
        if self._crossing_use_case is None or self._thread is not None:
            return
        if self.active_raster is not None:
            self.crossing_panel.working_crs.setText(self.active_raster.crs)
        try:
            request = self.crossing_panel.request()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.crossing_panel.invalidate()
        self.coordinate_panel.setEnabled(False)
        self.inputs.setEnabled(False)
        self.export_panel.setEnabled(False)
        self.crossing_panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.report.clear()
        self.status.setText("Reading highway and culvert geometry…")
        self._cancel = Event()
        thread = QThread(self)
        worker = CrossingWorker(self._crossing_use_case, request, self._cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_crossings)
        worker.failed.connect(self._show_error)
        worker.progress.connect(self._dem_progress)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _show_crossings(self, result: CrossingResult) -> None:
        self.coordinate_panel.invalidate()
        self.crossing_panel.show_result(result)
        self.preview.mode.setCurrentIndex(1)
        self.preview.drainage_info.setText(self.crossing_panel.summary.text())
        self.status.setText(self.crossing_panel.summary.text())
        lines = [
            f"{issue.severity.upper()} [{issue.code}] {issue.source.label}: {issue.message}"
            + (f" Related: {issue.related.label}" if issue.related else "")
            for issue in result.issues[:500]
        ]
        if len(result.issues) > 500:
            lines.append("Showing first 500 findings; all findings are retained in the result.")
        self.report.setPlainText(
            "\n".join(lines) if lines else "No extraction or overlap findings."
        )

    @Slot()
    def _start_coordinates(self) -> None:
        if self._coordinate_use_case is None or self._thread is not None:
            return
        self.coordinate_panel.invalidate()
        crossings = self.crossing_panel.result
        path = self.coordinate_panel.dem_path.text().strip()
        if crossings is None or not path:
            self._show_error("Compute crossings and select a DEM before coordinate validation.")
            return
        self.inputs.setEnabled(False)
        self.export_panel.setEnabled(False)
        self.crossing_panel.setEnabled(False)
        self.coordinate_panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Validating outlet coordinates against DEM metadata and cell masks...")
        self._cancel = Event()
        thread = QThread(self)
        worker = CoordinateWorker(
            self._coordinate_use_case,
            CoordinateRequest(Path(path), crossings),
            self._cancel,
            self._previews,
        )
        worker.raster_ready.connect(self.preview.show_raster)
        worker.progress.connect(self._dem_progress)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_coordinates)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _show_coordinates(self, result: CoordinateReport) -> None:
        self.coordinate_panel.show_result(result)
        self.status.setText(
            f"Audited {len(result.outlets)} outlets. Review classifications and diagnostics."
        )

    @Slot()
    def _start_outlets(self) -> None:
        if self._outlet_use_case is None or self._thread is not None:
            return
        self.coordinate_panel.invalidate()
        crossings = self.crossing_panel.result
        if crossings is None:
            self._show_error("Compute crossing candidates before selecting pour points.")
            return
        try:
            request = self.coordinate_panel.snap_request(crossings)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.inputs.setEnabled(False)
        self.export_panel.setEnabled(False)
        self.crossing_panel.setEnabled(False)
        self.coordinate_panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Validating and selecting outlet cells...")
        self._cancel = Event()
        thread = QThread(self)
        worker = OutletWorker(self._outlet_use_case, request, self._cancel, self._previews)
        worker.raster_ready.connect(self.preview.show_raster)
        worker.progress.connect(self._dem_progress)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_outlets)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _show_outlets(self, result: SnapResult) -> None:
        self.coordinate_panel.show_snapping(result)
        self.preview.mode.setCurrentIndex(1)
        accepted = sum(s.pour_point is not None for s in result.outlets)
        self.preview.drainage_info.setText(
            f"{len(result.outlets)} crossings; {accepted} selected pour points."
        )
        self.status.setText(
            f"Selected {accepted} pour points; rejected {len(result.outlets) - accepted}. "
            "Review selection status, movement and shared cells."
        )

    @Slot()
    def _start_hydrology(self) -> None:
        if self._hydrology_use_case is None or self._thread is not None:
            return
        prepared = self.coordinate_panel.snap_result
        if prepared is None:
            self._show_error("Prepare pour points before catchment delineation.")
            return
        try:
            request = self.hydrology_panel.request(prepared)
            if request.output.exists():
                if not self._confirm_overwrite(request.output, directory=True):
                    return
                request = replace(request, overwrite=True)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.hydrology_panel.invalidate()
        self.preview.clear_boundaries()
        for panel in (
            self.inputs,
            self.export_panel,
            self.crossing_panel,
            self.coordinate_panel,
            self.hydrology_panel,
        ):
            panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Starting hydrology; first run may compile numerical kernels...")
        self._cancel = Event()
        thread = QThread(self)
        worker = HydrologyWorker(self._hydrology_use_case, request, self._cancel, self._previews)
        worker.boundaries_ready.connect(self.preview.show_boundaries)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_hydrology)
        worker.failed.connect(self._show_error)
        worker.progress.connect(self._dem_progress)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    def _confirm_overwrite(self, path: Path, *, directory: bool) -> bool:
        description = (
            "Generated catchment results in this folder will be replaced. "
            "Obsolete catchment masks from the previous run will be removed. "
            "Unrelated files will be kept."
            if directory
            else "The existing GeoTIFF will be replaced."
        )
        return (
            QMessageBox.warning(
                self,
                "Overwrite existing results?",
                f"{path.resolve()}\n\n{description}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        )

    @Slot(object)
    def _show_hydrology(self, result: HydrologyResult) -> None:
        self.hydrology_panel.show_result(result)
        self.preview.mode.setCurrentIndex(1)

        count = sum(c.status == "delineated" for c in result.catchments)
        self.status.setText(
            f"Delineated {count} catchments; {len(result.catchments) - count} rejected. "
            f"Results: {result.output}"
        )

    @Slot()
    def _finished(self) -> None:
        self.task_progress.finish(self._task_failed)
        self.open_raster_button.setEnabled(self._project_rasters is not None)
        self.preview.earth_button.setEnabled(self._earth_writer is not None)
        self._thread = None
        self._worker = None
        self.inputs.setEnabled(True)
        self.crossing_panel.setEnabled(self._crossing_use_case is not None)
        self.coordinate_panel.setEnabled(self._coordinate_use_case is not None)
        self.hydrology_panel.setEnabled(self._hydrology_use_case is not None)
        self.export_panel.setEnabled(self.dataset is not None and self._dem_use_case is not None)
        self.cancel_button.setEnabled(False)
        self._satellite_timer.start()
        if self._closing:
            self.close()

    @Slot()
    def _start_earth_preview(self) -> None:
        if self._earth_writer is None or self._thread is not None:
            return
        crossings = self.crossing_panel.result
        crs = (
            self.active_raster.crs
            if self.active_raster
            else (crossings.crs_wkt if crossings else "")
        )
        raster = (
            self.preview.terrain_view.snapshot
            if self.preview.mode.currentIndex() == 0
            or self.preview.show_raster_background.isChecked()
            else None
        )
        if not crs or (crossings is None and raster is None and self.preview.boundaries is None):
            self._show_error("Load a raster or compute crossings before opening Google Earth.")
            return
        snapshot = EarthPreview(
            crs, crossings, self.coordinate_panel.snap_result, self.preview.boundaries, raster
        )
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Google Earth preview",
            str(
                self.active_raster.path.with_suffix(".kmz")
                if self.active_raster
                else Path("highway-drainage.kmz")
            ),
            "Google Earth KMZ (*.kmz)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".kmz":
            path = path.with_suffix(".kmz")
        overwrite = path.exists()
        if (
            overwrite
            and QMessageBox.warning(
                self,
                "Overwrite existing KMZ?",
                f"{path}\n\nThe existing KMZ will be replaced after export succeeds. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for panel in (self.inputs, self.export_panel, self.crossing_panel, self.coordinate_panel):
            panel.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Preparing Google Earth preview from current results...")
        self._cancel = Event()
        thread = QThread(self)
        worker = EarthPreviewWorker(
            self._earth_writer, snapshot, path, self._cancel, overwrite=overwrite
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._open_earth_preview)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self._begin_task()
        thread.start()

    @Slot(object)
    def _open_earth_preview(self, path: Path) -> None:
        if self._closing or self._cancel.is_set():
            return
        if open_google_earth(path):
            self.status.setText(
                "KMZ export complete. Launch requested in a separate Google Earth Pro window. "
                "If no window appears, open Google Earth Pro and use File > Open. "
                f"Local KMZ: {path}"
            )
        else:
            self.status.setText(f"Google Earth preview saved: {path}")
            QMessageBox.information(
                self,
                "Open Google Earth preview",
                f"The KMZ was saved but could not be opened automatically.\n\n{path}\n\n"
                "Open it using File > Open in Google Earth Pro.",
            )

    @Slot()
    def _cancel_import(self) -> None:
        self._cancel.set()
        self.status.setText("Cancelling after the current read, geometry operation or raster tile…")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._satellite_timer.stop()
        if self._thread is not None:
            self._closing = True
            self._cancel_import()
            event.ignore()
        elif not self.preview.satellite_view.shutdown():
            self._closing = True
            event.ignore()
        else:
            super().closeEvent(event)

    @Slot()
    def _satellite_idle(self) -> None:
        if self._closing:
            self.close()

    @Slot()
    def _refresh_satellite(self) -> None:
        if self._closing or not self.preview.show_satellite.isChecked():
            return
        crossings = self.crossing_panel.result
        crs = (
            self.active_raster.crs
            if self.active_raster
            else (crossings.crs_wkt if crossings else "")
        )
        if not crs:
            self.preview.satellite_view.clear_layers()
            return
        terrain_mode = self.preview.mode.currentIndex() == 0
        raster = self.preview.terrain_view.snapshot
        preview = EarthPreview(
            crs,
            None if terrain_mode else crossings,
            None if terrain_mode else self.coordinate_panel.snap_result,
            None if terrain_mode else self.preview.boundaries,
            raster if terrain_mode or self.preview.show_raster_background.isChecked() else None,
        )
        self.preview.satellite_view.submit(
            SatelliteRequest(preview, raster, "terrain" if terrain_mode else "drainage")
        )
