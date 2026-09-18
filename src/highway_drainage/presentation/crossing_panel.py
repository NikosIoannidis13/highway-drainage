"""CAD source form and linked crossing table/map."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
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

from highway_drainage.domain.crossings import CrossingRequest, CrossingResult, LineSource
from highway_drainage.presentation.crossing_view import CrossingView


class CrossingPanel(QWidget):
    find_requested = Signal()
    invalidated = Signal()

    def __init__(self, view: CrossingView | None = None) -> None:
        super().__init__()
        self.result: CrossingResult | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.highway_path = QLineEdit()
        self.culvert_path = QLineEdit()
        self.highway_crs = QLineEdit()
        self.culvert_crs = QLineEdit()
        self.working_crs = QLineEdit()
        self.highway_layers = QLineEdit()
        self.culvert_layers = QLineEdit()
        self.tolerance = QLineEdit("0.05")
        for label, path, crs, layers in (
            ("Highway", self.highway_path, self.highway_crs, self.highway_layers),
            ("Culverts", self.culvert_path, self.culvert_crs, self.culvert_layers),
        ):
            row = QHBoxLayout()
            row.addWidget(path, 3)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, field=path: self._choose_file(field))
            row.addWidget(browse)
            crs.setPlaceholderText("Source CRS code, e.g. 2100")
            row.addWidget(crs, 1)
            form.addRow(f"{label} DXF / CRS", row)
            layers.setPlaceholderText("Optional layer names separated by semicolons; blank = all")
            form.addRow(f"{label} layers", layers)
        self.working_crs.setPlaceholderText("Projected working CRS in metres, e.g. 2100")
        form.addRow("Working CRS", self.working_crs)
        form.addRow("Curve approximation tolerance (m)", self.tolerance)
        layout.addLayout(form)
        controls = QHBoxLayout()
        self.find_button = QPushButton("Extract lines and find crossings")
        self.find_button.clicked.connect(self.find_requested)
        fit = QPushButton("Fit view")
        controls.addWidget(self.find_button)
        controls.addWidget(fit)
        layout.addLayout(controls)
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
            self.highway_layers,
            self.culvert_layers,
            self.tolerance,
        ):
            field.textChanged.connect(self.invalidate)

    def _choose_file(self, field: QLineEdit) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Select DXF", "", "DXF files (*.dxf)")
        if name:
            field.setText(name)

    def invalidate(self) -> None:
        self.result = None
        self.view.clear()
        self.table.setRowCount(0)
        self.summary.setText("Inputs changed. Recompute crossings to verify them.")
        self.invalidated.emit()

    def request(self) -> CrossingRequest:
        if not self.highway_path.text().strip() or not self.culvert_path.text().strip():
            raise ValueError("Select both the highway DXF and the culvert DXF.")
        return CrossingRequest(
            LineSource(
                Path(self.highway_path.text().strip()).resolve(),
                self.highway_crs.text().strip(),
                tuple(s.strip() for s in self.highway_layers.text().split(";") if s.strip()),
            ),
            LineSource(
                Path(self.culvert_path.text().strip()).resolve(),
                self.culvert_crs.text().strip(),
                tuple(s.strip() for s in self.culvert_layers.text().split(";") if s.strip()),
            ),
            self.working_crs.text().strip(),
            float(self.tolerance.text()),
        )

    def show_result(self, result: CrossingResult) -> None:
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
