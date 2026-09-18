"""Display the immutable audit; no coordinate calculations live here."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.domain.coordinates import CoordinateReport, CoordinateRequest
from highway_drainage.domain.crossings import CrossingResult
from highway_drainage.domain.outlets import SnapMode, SnapRequest, SnapResult
from highway_drainage.presentation.outlet_view import OutletView


class CoordinatePanel(QWidget):
    validate_requested = Signal()
    snap_requested = Signal()
    invalidated = Signal()

    def __init__(self, view: OutletView | None = None) -> None:
        super().__init__()
        self.result: CoordinateReport | None = None
        self.snap_result: SnapResult | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Validate current crossing candidates against DEM band 1."))
        row = QHBoxLayout()
        self.dem_path = QLineEdit()
        self.dem_path.setPlaceholderText("Select DEM GeoTIFF")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.dem_path)
        row.addWidget(browse)
        layout.addLayout(row)
        self.validate_button = QPushButton("Validate outlet coordinates")
        self.validate_button.clicked.connect(self.validate_requested)
        layout.addWidget(self.validate_button)
        form = QFormLayout()
        self.snap_mode = QComboBox()
        self.snap_mode.addItems([mode.value for mode in SnapMode])
        self.snap_distance = QLineEdit("10")
        self.accumulation_path = QLineEdit()
        flow_row = QHBoxLayout()
        flow_row.addWidget(self.accumulation_path)
        browse_flow = QPushButton("Browse...")
        browse_flow.clicked.connect(self._browse_accumulation)
        flow_row.addWidget(browse_flow)
        self.flow_threshold = QLineEdit("1")
        self.flow_units = QLineEdit("cells")
        form.addRow("Snapping mode", self.snap_mode)
        form.addRow("Maximum distance from crossing (m)", self.snap_distance)
        form.addRow("Aligned flow accumulation GeoTIFF", flow_row)
        form.addRow("Minimum accumulation", self.flow_threshold)
        form.addRow("Accumulation units (as supplied)", self.flow_units)
        layout.addLayout(form)
        self.snap_button = QPushButton("Select pour points")
        self.snap_button.clicked.connect(self.snap_requested)
        layout.addWidget(self.snap_button)
        legend = QLabel(
            "Red: geometric crossing; grey: containing DEM pixel; green ring: pour point. "
            "Nearest-cell results are provisional. Review flow-based selections against culverts."
        )
        legend.setWordWrap(True)
        layout.addWidget(legend)
        self.outlet_view = view if view is not None else OutletView()
        if view is None:
            layout.addWidget(self.outlet_view)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report)
        self.dem_path.textChanged.connect(self.invalidate)
        for field in (
            self.snap_distance,
            self.accumulation_path,
            self.flow_threshold,
            self.flow_units,
        ):
            field.textChanged.connect(self.invalidate)
        self.snap_mode.currentTextChanged.connect(self.invalidate)

    def _browse_accumulation(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select flow accumulation", "", "GeoTIFF (*.tif *.tiff)"
        )
        if path:
            self.accumulation_path.setText(path)

    def snap_request(self, crossings: CrossingResult) -> SnapRequest:
        if not self.dem_path.text().strip():
            raise ValueError("Select a DEM before snapping.")
        flow = self.accumulation_path.text().strip()
        return SnapRequest(
            CoordinateRequest(Path(self.dem_path.text().strip()), crossings),
            float(self.snap_distance.text()),
            SnapMode(self.snap_mode.currentText()),
            Path(flow) if flow else None,
            float(self.flow_threshold.text()),
            self.flow_units.text(),
        )

    def show_snapping(self, result: SnapResult) -> None:
        self.show_result(result.validation)
        self.snap_result = result
        self.outlet_view.show_outlets(result)
        lines = [
            "\nPOUR POINT SELECTION",
            f"Mode={result.request.mode.value}; maximum distance={result.request.max_distance} m",
            f"Accumulation={result.request.accumulation}; "
            f"threshold={result.request.minimum_accumulation} {result.request.accumulation_units}",
        ]
        for selection in result.outlets:
            original, target = selection.original, selection.pour_point
            lines.append(f"{original.point.identifier}: {selection.status}. {selection.diagnostic}")
            lines.append(
                f"Original XY=({original.point.x}, {original.point.y}); "
                f"containing pixel=({original.row}, {original.column}), {original.cell_state}"
            )
            if target:
                lines.append(
                    f"Pour-point XY=({target.x}, {target.y}); "
                    f"pixel=({target.row}, {target.column}); "
                    f"distance={target.distance:g} m; accumulation={target.accumulation}"
                )
            if selection.shares_cell_with:
                lines.append(f"REVIEW: shares pour-point cell with {selection.shares_cell_with}")
        self.report.appendPlainText("\n".join(lines))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select DEM", "", "GeoTIFF (*.tif *.tiff)")
        if path:
            self.dem_path.setText(path)

    def invalidate(self) -> None:
        self.result = None
        self.snap_result = None
        self.outlet_view.clear()
        self.report.clear()
        self.invalidated.emit()

    def show_result(self, result: CoordinateReport) -> None:
        self.result = result
        dem = result.dem
        lines = [
            f"DEM: {dem.path}",
            f"DEM CRS: {dem.crs}",
            f"Candidate XY CRS: {result.crossings.crs_wkt}",
            f"CRS match: {result.crs_matches}",
        ]
        for label, source in (
            ("Highway", result.crossings.highway_source),
            ("Culvert", result.crossings.culvert_source),
        ):
            lines.append(
                f"{label} DXF CRS assumption: {source.crs}; file: {source.path}"
                if source
                else f"{label} DXF CRS assumption: unavailable"
            )
        lines.extend(
            [
                "DXF CRS declarations are user assumptions, not verified survey metadata. "
                "Candidates were normalized to their working CRS with X/Y axis order.",
                f"DEM bounds (left, bottom, right, top): {dem.bounds}",
                f"Dimensions: {dem.width} columns x {dem.height} rows; band 1; NoData={dem.nodata}",
                f"Affine (a,b,c,d,e,f): {dem.affine}; finite/invertible: {dem.affine_valid}",
                "X=a*column+b*row+c; Y=d*column+e*row+f (pixel corners).",
                f"Raster axes: {dem.y_direction}",
                "XY input -> inverse affine (column, row) -> floor -> array[row, column].",
                "Pixel domain: [0,width) x [0,height). Perimeter classified as edge; "
                "column=width and row=height have no cell. Internal boundaries use floor.",
                "Rotated grids use the actual footprint, not just its bounding rectangle. "
                "No tolerance, clamping, swapping, movement or reprojection is applied.",
                *dem.diagnostics,
            ]
        )
        for check in result.outlets:
            point = check.point
            lines.extend(
                [
                    "",
                    f"{point.identifier}: X={point.x!r}, Y={point.y!r}; {check.location.value}",
                    f"Culvert: {point.culvert.label}; highways: "
                    + "; ".join(h.label for h in point.highways),
                    f"Row={check.row}, column={check.column}; "
                    f"fractional row={check.fractional_row!r}, "
                    f"column={check.fractional_column!r}",
                    f"Cell={check.cell_state}; elevation={check.elevation}; "
                    f"edge sides={check.edge_sides}; pixel boundary={check.on_pixel_boundary}",
                    f"XY/row-column ordering verified={check.ordering_verified}; "
                    f"affine round trip verified={check.roundtrip_verified}",
                    check.diagnostic,
                ]
            )
        self.report.setPlainText("\n".join(lines))
