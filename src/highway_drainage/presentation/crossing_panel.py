"""CAD source form and linked crossing table/map."""

from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.application.point_export import DirectLineExport
from highway_drainage.domain.crossings import CrossingRequest, CrossingResult, LineSource
from highway_drainage.presentation.crossing_view import CrossingView


class CrossingPanel(QWidget):
    find_requested = Signal()
    invalidated = Signal()
    raster_requested = Signal(str)
    export_requested = Signal()
    highway_export_requested = Signal()
    culvert_export_requested = Signal()

    def __init__(self, view: CrossingView | None = None) -> None:
        super().__init__()
        self.result: CrossingResult | None = None
        layout = QVBoxLayout(self)
        self.raster_path: Path | None = None
        self.raster_label = QLabel("No raster required: enter the project EPSG code below.")
        self.raster_label.setWordWrap(True)
        layout.addWidget(self.raster_label)
        self.load_raster_button = QPushButton("Open ready raster TIFF?")
        self.load_raster_button.clicked.connect(self._choose_raster)
        layout.addWidget(self.load_raster_button)
        self.source_form = QWidget()
        form = QFormLayout(self.source_form)
        self.highway_path = QLineEdit()
        self.culvert_path = QLineEdit()
        self.highway_crs = QLineEdit(self)
        self.culvert_crs = QLineEdit(self)
        self.working_crs = QLineEdit(self)
        self.working_crs.setReadOnly(True)
        self.working_crs.hide()
        self.project_epsg = QLineEdit()
        self.project_epsg.setPlaceholderText("EPSG number, e.g. 2100")
        self.project_epsg.setValidator(
            QRegularExpressionValidator(QRegularExpression("[0-9]+"), self.project_epsg)
        )
        form.addRow("Project EPSG code (without raster)", self.project_epsg)
        self.highway_layers = QLineEdit()
        self.culvert_layers = QLineEdit()
        self.tolerance = QLineEdit("0.05")
        for label, path, crs, layers in (
            ("Highway", self.highway_path, self.highway_crs, self.highway_layers),
            ("Culverts", self.culvert_path, self.culvert_crs, self.culvert_layers),
        ):
            path.textChanged.connect(crs.clear)
            row = QHBoxLayout()
            row.addWidget(path, 3)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, field=path: self._choose_file(field))
            row.addWidget(browse)
            crs.setPlaceholderText("Source CRS code, e.g. 2100")
            crs.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]+"), crs))
            form.addRow(f"{label} DXF", row)
            form.addRow(f"{label} source EPSG (optional)", crs)
            layers.setPlaceholderText("Optional layer names separated by semicolons; blank = all")
            form.addRow(f"{label} layers", layers)
        self.working_crs.setPlaceholderText("Projected working CRS in metres, e.g. 2100")
        form.addRow(
            QLabel(
                "Lines and crossings use the project CRS (or the loaded raster CRS). "
                "Leave source EPSG blank for DXF metadata / .prj detection. "
                "Without metadata, the project CRS is assumed."
            )
        )
        form.addRow("Curve approximation tolerance (m)", self.tolerance)
        layout.addWidget(self.source_form)
        controls = QHBoxLayout()
        self.find_button = QPushButton("Extract lines and find crossings")
        self.find_button.clicked.connect(self.find_requested)
        fit = QPushButton("Fit view")
        controls.addWidget(self.find_button)
        controls.addWidget(fit)
        layout.addLayout(controls)
        self.export_button = QPushButton("Export crossings as Shapefile (.shp)")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_requested)
        layout.addWidget(self.export_button)
        self.highway_export_button = QPushButton("Export highway lines as Shapefile (.shp)")
        self.culvert_export_button = QPushButton("Export culvert lines as Shapefile (.shp)")
        self.highway_export_button.setEnabled(False)
        self.culvert_export_button.setEnabled(False)
        self.highway_export_button.clicked.connect(self.highway_export_requested)
        self.culvert_export_button.clicked.connect(self.culvert_export_requested)
        layout.addWidget(self.highway_export_button)
        layout.addWidget(self.culvert_export_button)
        legend = QLabel(
            "Blue: highway  •  Orange: culverts  •  Red: candidates  •  North ↑  "
            "Wheel: zoom; drag: pan. Select a point or table row to verify its sources."
        )
        legend.setWordWrap(True)
        layout.addWidget(legend)
        self.summary = QLabel(
            "Plan-view candidates only. Overlaps are reported, not made into outlets."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.view = view if view is not None else CrossingView()
        fit.clicked.connect(self.view.fit_data)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Point", "Easting", "Northing", "Culvert", "Highway", "Flags"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selected)
        self.view.point_selected.connect(self._select_point)
        splitter = QSplitter(Qt.Orientation.Vertical)
        if view is None:
            splitter.addWidget(self.view)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.coordinates = QLabel("E / N in the working CRS, metres")
        self.view.coordinates_changed.connect(self.coordinates.setText)
        layout.addWidget(self.coordinates)
        for field in (
            self.highway_path,
            self.culvert_path,
            self.highway_crs,
            self.culvert_crs,
            self.working_crs,
            self.project_epsg,
            self.highway_layers,
            self.culvert_layers,
            self.tolerance,
        ):
            field.textChanged.connect(self.invalidate)

    def _choose_raster(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project DEM", "", "GeoTIFF (*.tif *.tiff)"
        )
        if path:
            self.raster_requested.emit(path)

    def set_project_raster(self, path: Path, crs: str, label: str) -> None:
        self.raster_path = path
        self.working_crs.setText(crs)
        self.project_epsg.setEnabled(False)
        self.project_epsg.setToolTip("Project CRS comes from the loaded raster.")
        self.raster_label.setText(f"DEM: {path.name}\nProject CRS: {label}")
        self.source_form.setEnabled(True)
        self.find_button.setEnabled(True)

    def _choose_file(self, field: QLineEdit) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Select DXF", "", "DXF files (*.dxf)")
        if name:
            (self.highway_crs if field is self.highway_path else self.culvert_crs).clear()
            field.setText(name)

    def invalidate(self) -> None:
        self.highway_export_button.setEnabled(bool(self.highway_path.text().strip()))
        self.culvert_export_button.setEnabled(bool(self.culvert_path.text().strip()))
        self.export_button.setEnabled(False)
        self.result = None
        self.view.clear()
        self.table.setRowCount(0)
        self.summary.setText("Inputs changed. Recompute crossings to verify them.")
        self.invalidated.emit()

    def line_export_request(self, culverts: bool) -> DirectLineExport:
        path = self.culvert_path if culverts else self.highway_path
        crs = self.culvert_crs if culverts else self.highway_crs
        layers = self.culvert_layers if culverts else self.highway_layers
        if not path.text().strip():
            raise ValueError("Select the DXF file to export.")
        target = (
            self.working_crs.text().strip()
            if self.raster_path
            else self.project_epsg.text().strip()
        )
        return DirectLineExport(
            Path("culverts.shp" if culverts else "highway.shp"),
            LineSource(
                Path(path.text().strip()).resolve(),
                crs.text().strip(),
                tuple(s.strip() for s in layers.text().split(";") if s.strip()),
                fallback_crs=target,
            ),
            target,
            "culvert" if culverts else "highway",
            float(self.tolerance.text()),
        )

    def request(self) -> CrossingRequest:
        target = (
            self.working_crs.text().strip()
            if self.raster_path
            else self.project_epsg.text().strip()
        )
        if not target:
            raise ValueError(
                "Enter the project EPSG number (e.g. 2100), or load a project GeoTIFF."
            )
        if not self.highway_path.text().strip() or not self.culvert_path.text().strip():
            raise ValueError("Select both the highway DXF and the culvert DXF.")
        return CrossingRequest(
            LineSource(
                Path(self.highway_path.text().strip()).resolve(),
                self.highway_crs.text().strip(),
                tuple(s.strip() for s in self.highway_layers.text().split(";") if s.strip()),
                fallback_crs=target,
            ),
            LineSource(
                Path(self.culvert_path.text().strip()).resolve(),
                self.culvert_crs.text().strip(),
                tuple(s.strip() for s in self.culvert_layers.text().split(";") if s.strip()),
                fallback_crs=target,
            ),
            target,
            float(self.tolerance.text()),
        )

    def show_result(self, result: CrossingResult) -> None:
        self.highway_export_button.setEnabled(
            bool(self.highway_path.text().strip()) or bool(result.highways)
        )
        self.culvert_export_button.setEnabled(
            bool(self.culvert_path.text().strip()) or bool(result.culverts)
        )
        self.export_button.setEnabled(bool(result.points))
        self.result = result
        self.view.show_result(result)
        self.table.setRowCount(len(result.points))
        for row, point in enumerate(result.points):
            flags = ["endpoint contact"] if point.endpoint_contact else []
            if point.approximated:
                flags.append("approximated curve")
            values = (
                point.identifier,
                f"{point.x:.6f}",
                f"{point.y:.6f}",
                point.culvert.label,
                "; ".join(ref.label for ref in point.highways),
                ", ".join(flags),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, col, item)
        self.summary.setText(
            (
                "Partial extraction — review skipped/invalid geometry. "
                if any(i.severity == "error" or i.code == "unsupported" for i in result.issues)
                else ""
            )
            + f"{len(result.highways)} highway elements, {len(result.culverts)} culvert elements, "
            f"{len(result.points)} candidate points. {len(result.issues)} findings; see report."
        )

    def _selected(self) -> None:
        if self.result is None or not self.table.selectedIndexes():
            return
        row = self.table.selectedIndexes()[0].row()
        self.view.highlight(self.result.points[row].identifier)

    def _select_point(self, identifier: str) -> None:
        if self.result is not None:
            for row, point in enumerate(self.result.points):
                if point.identifier == identifier:
                    self.table.selectRow(row)
                    return
