"""CAD source form and linked crossing table/map."""

from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError
from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.application.point_export import DirectLineExport
from highway_drainage.domain.crossings import (
    CrossingRequest,
    CrossingResult,
    DrawingUnits,
    LineSource,
    PointRole,
)
from highway_drainage.presentation.crossing_view import CrossingView


class PointRoleEditor(QComboBox):
    role_changed = Signal(str, object)

    def __init__(self, identifier: str, role: PointRole) -> None:
        super().__init__()
        self._identifier = identifier
        self.setAccessibleName(f"Role for crossing {identifier}")
        for label, choice in (
            ("Inlet", PointRole.INLET), ("Outlet", PointRole.OUTLET),
            ("Unassigned", PointRole.UNCLASSIFIED),
        ):
            self.addItem(label, choice.value)
        self.setCurrentIndex(self.findData(role.value))
        self.currentIndexChanged.connect(self._changed)

    def _changed(self, index: int) -> None:
        if index >= 0:
            self.role_changed.emit(self._identifier, PointRole(self.itemData(index)))


class CrossingPanel(QWidget):
    find_requested = Signal()
    invalidated = Signal()
    raster_requested = Signal(str)
    export_requested = Signal()
    highway_export_requested = Signal()
    culvert_export_requested = Signal()
    role_change_requested = Signal(str, object)

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
        self.highway_units = self._units_selector("Highway coordinate units")
        self.culvert_units = self._units_selector("Culverts coordinate units")
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
        for label, path, crs, layers, units in (
            ("Highway", self.highway_path, self.highway_crs, self.highway_layers,
             self.highway_units),
            ("Culverts", self.culvert_path, self.culvert_crs, self.culvert_layers,
             self.culvert_units),
        ):
            path.textChanged.connect(crs.clear)
            path.textChanged.connect(lambda text, selector=units: selector.setCurrentIndex(0))
            row = QHBoxLayout()
            row.addWidget(path, 3)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, field=path: self._choose_file(field))
            row.addWidget(browse)
            crs.setPlaceholderText("Source CRS code, e.g. 2100")
            crs.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]+"), crs))
            form.addRow(f"{label} DXF", row)
            form.addRow(f"{label} source EPSG (optional)", crs)
            form.addRow(f"{label} coordinate units", units)
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
        self.show_crs = QCheckBox("Show input coordinate systems (CRS) and units")
        self.show_crs.setChecked(True)
        layout.addWidget(self.show_crs)
        self.crs_details = QWidget()
        crs_form = QFormLayout(self.crs_details)
        crs_form.setContentsMargins(0, 0, 0, 0)
        self.raster_crs_info = QLabel("Not loaded")
        self.highway_crs_info = QLabel("Not imported")
        self.culvert_crs_info = QLabel("Not imported")
        self.output_crs_info = QLabel("Not selected")
        for title, info in (
            ("Raster CRS", self.raster_crs_info),
            ("Highway source CRS", self.highway_crs_info),
            ("Culverts source CRS", self.culvert_crs_info),
            ("Preview / crossings CRS", self.output_crs_info),
        ):
            info.setWordWrap(True)
            policy = info.sizePolicy()
            policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
            info.setSizePolicy(policy)
            info.setTextFormat(Qt.TextFormat.PlainText)
            info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            crs_form.addRow(title, info)
        self.show_crs.toggled.connect(self.crs_details.setVisible)
        layout.addWidget(self.crs_details)
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
            "Blue lines: highway; orange lines: culverts. Click a point's Role dropdown "
            "to choose Inlet, Outlet or Unassigned. Only inlets are used for catchments."
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
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Point", "Easting", "Northing", "Culvert", "Highway", "Flags", "Role"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selected)
        self.view.selection_changed.connect(self._select_points)
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
        self.highway_units.currentIndexChanged.connect(self.invalidate)
        self.culvert_units.currentIndexChanged.connect(self.invalidate)

    @staticmethod
    def _units_selector(name: str) -> QComboBox:
        selector = QComboBox()
        selector.setAccessibleName(name)
        for label, units in (
            ("Coordinates already in source CRS", DrawingUnits.SOURCE_CRS),
            ("Use DXF header units", DrawingUnits.HEADER),
            ("Metres", DrawingUnits.METRES),
            ("Millimetres", DrawingUnits.MILLIMETRES),
            ("Feet (international)", DrawingUnits.FEET),
            ("US survey feet", DrawingUnits.US_SURVEY_FEET),
            ("Inches", DrawingUnits.INCHES),
        ):
            selector.addItem(label, units.value)
        selector.setToolTip(
            "Default: preserve coordinates already in the source CRS, even if the DXF "
            "header declares different units. For local drawings, choose their actual units "
            "or use the DXF header. Embedded GEODATA placement is always honoured. "
            "CRS reprojection still applies when source and project CRSs differ."
        )
        return selector

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
        self._update_crs_details()

    @staticmethod
    def _crs_description(value: str) -> str:
        if not value.strip():
            return "Not selected"
        try:
            crs = CRS.from_user_input(value)
        except CRSError:
            return f"Invalid CRS: {value}"
        authority = crs.to_authority()
        code = ":".join(authority) if authority else "Custom CRS"
        return f"{code} — {crs.name}"

    def _update_crs_details(self) -> None:
        target = self.working_crs.text() if self.raster_path else self.project_epsg.text()
        self.raster_crs_info.setText(
            self._crs_description(self.working_crs.text()) if self.raster_path else "Not loaded"
        )
        self.output_crs_info.setText(self._crs_description(target))
        for path, declared, units, info, source in (
            (self.highway_path, self.highway_crs, self.highway_units, self.highway_crs_info,
             self.result.highway_source if self.result else None),
            (self.culvert_path, self.culvert_crs, self.culvert_units, self.culvert_crs_info,
             self.result.culvert_source if self.result else None),
        ):
            if source is not None and self.result is not None:
                assumed = any(
                    issue.code == "assumed_raster_crs" and issue.source.path == source.path
                    for issue in self.result.issues
                )
                origin = ("assumed project CRS; no DXF metadata" if assumed else
                          "entered by user" if declared.text().strip() else "detected from file")
                info.setText(
                    f"{self._crs_description(source.crs)} ({origin})\n{source.unit_summary}"
                )
            elif not path.text().strip():
                info.setText("Not imported")
            elif declared.text().strip():
                info.setText(
                    f"{self._crs_description(declared.text())} (entered; awaiting import)"
                    f"\nUnits: {units.currentText()}"
                )
            else:
                info.setText(
                    "Awaiting extraction: detect from DXF / .prj, otherwise assume project CRS"
                    f"\nUnits: {units.currentText()}"
                )
            info.setToolTip(info.text())

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
        self._update_crs_details()
        self.view.clear()
        self.table.setRowCount(0)
        self.summary.setText("Inputs changed. Recompute crossings to verify them.")
        self.invalidated.emit()

    def line_export_request(self, culverts: bool) -> DirectLineExport:
        path = self.culvert_path if culverts else self.highway_path
        crs = self.culvert_crs if culverts else self.highway_crs
        layers = self.culvert_layers if culverts else self.highway_layers
        units = self.culvert_units if culverts else self.highway_units
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
                units=DrawingUnits(units.currentData()),
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
                units=DrawingUnits(self.highway_units.currentData()),
            ),
            LineSource(
                Path(self.culvert_path.text().strip()).resolve(),
                self.culvert_crs.text().strip(),
                tuple(s.strip() for s in self.culvert_layers.text().split(";") if s.strip()),
                fallback_crs=target,
                units=DrawingUnits(self.culvert_units.currentData()),
            ),
            target,
            float(self.tolerance.text()),
        )

    def show_result(self, result: CrossingResult, *, preserve_camera: bool = False) -> None:
        self.highway_export_button.setEnabled(
            bool(self.highway_path.text().strip()) or bool(result.highways)
        )
        self.culvert_export_button.setEnabled(
            bool(self.culvert_path.text().strip()) or bool(result.culverts)
        )
        self.export_button.setEnabled(bool(result.points))
        self.result = result
        self._update_crs_details()
        self.table.blockSignals(True)
        self.table.setRowCount(len(result.points))
        for row, point in enumerate(result.points):
            flags = ["endpoint contact"] if point.endpoint_contact else []
            if point.approximated:
                flags.append("approximated curve")
            if point.manual:
                flags.append("manually placed")
            values = (
                point.identifier,
                f"{point.x:.6f}",
                f"{point.y:.6f}",
                point.culvert.label,
                "; ".join(ref.label for ref in point.highways),
                ", ".join(flags),
                "",  # The dropdown paints the role; cell text would show through native styles.
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, col, item)
            role_editor = PointRoleEditor(point.identifier, point.role)
            role_editor.role_changed.connect(self.role_change_requested)
            self.table.setCellWidget(row, 6, role_editor)
        self.table.setColumnWidth(6, max(120, self.table.columnWidth(6)))
        self.table.blockSignals(False)
        self.view.show_result(result, preserve_camera=preserve_camera)
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
        if self.result is None:
            return
        self.view.set_selected({
            self.result.points[index.row()].identifier
            for index in self.table.selectionModel().selectedRows()
        })

    def _select_points(self, identifiers: set[str]) -> None:
        if self.result is None:
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel

        selection = QItemSelection()
        for row, point in enumerate(self.result.points):
            if point.identifier in identifiers:
                selection.select(self.table.model().index(row, 0),
                                 self.table.model().index(row, self.table.columnCount() - 1))
        self.table.blockSignals(True)
        self.table.selectionModel().select(
            selection, QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        if len(identifiers) == 1 and not selection.isEmpty():
            self.table.selectionModel().setCurrentIndex(
                selection.indexes()[0], QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        self.table.blockSignals(False)

    def _select_point(self, identifier: str) -> None:
        if self.result is not None:
            for row, point in enumerate(self.result.points):
                if point.identifier == identifier:
                    self.table.selectRow(row)
                    return
