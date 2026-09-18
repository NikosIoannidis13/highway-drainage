"""Hydrology request form and per-outlet result report."""

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

from highway_drainage.application.hydrology import with_large_dem_limits
from highway_drainage.domain.hydrology import HydrologyRequest, HydrologyResult
from highway_drainage.domain.outlets import SnapResult


class HydrologyPanel(QWidget):
    run_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.result: HydrologyResult | None = None
        layout = QVBoxLayout(self)
        note = QLabel(
            "Uses the current prepared pour points. Fills depressions, generates D8 flow and "
            "accumulation, and delineates a full catchment per outlet. Points remain fixed. "
            "Requires square north-up DEM pixels in a projected metre CRS."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText(
            "Results directory (confirm before replacing existing results)"
        )
        row = QHBoxLayout()
        row.addWidget(self.output)
        browse = QPushButton("Choose parent folder...")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        form.addRow("Output directory", row)
        self.minimum_cells = QLineEdit("1")
        form.addRow("Minimum contributing cells at outlet", self.minimum_cells)
        self.resource_profile = QComboBox()
        self.resource_profile.addItems(["Standard", "Large DEM"])
        self.resource_profile.setToolTip(
            "Large DEM: 50 million cells, 12 GiB estimated memory, 4 GiB estimated output, "
            "2 billion cell/outlet visits. These are limits, not a guarantee of available RAM."
        )
        form.addRow("Processing budget", self.resource_profile)
        layout.addLayout(form)
        self.run_button = QPushButton("Delineate catchments")
        self.run_button.clicked.connect(self.run_requested)
        layout.addWidget(self.run_button)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report)
        self.output.textChanged.connect(self.invalidate)
        self.minimum_cells.textChanged.connect(self.invalidate)
        self.resource_profile.currentIndexChanged.connect(self.invalidate)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output parent directory")
        if path:
            self.output.setText(str(Path(path) / "catchments"))

    def request(self, prepared: SnapResult) -> HydrologyRequest:
        if not self.output.text().strip():
            raise ValueError("Choose a hydrology output directory.")
        request = HydrologyRequest(
            prepared, Path(self.output.text().strip()), float(self.minimum_cells.text())
        )
        return (
            with_large_dem_limits(request) if self.resource_profile.currentIndex() == 1 else request
        )

    def invalidate(self) -> None:
        self.result = None
        self.report.clear()

    def show_result(self, result: HydrologyResult) -> None:
        self.result = result
        lines = [
            f"Output: {result.output}",
            f"Conditioning: {result.filled_cells:,} cells raised; "
            f"maximum fill {result.maximum_fill:g} {result.elevation_unit}",
            f"Conditioned DEM: {result.conditioned_dem}",
            f"D8 directions: {result.flow_direction}",
            f"Flow accumulation (cells): {result.accumulation}",
            *result.diagnostics,
        ]
        for catchment in result.catchments:
            point = catchment.outlet.original.point
            lines.extend(
                [
                    "",
                    f"{point.identifier} / {point.culvert.label}: {catchment.status}",
                    f"Original XY=({point.x}, {point.y}); pour point={catchment.outlet.pour_point}",
                    f"Cells={catchment.cell_count:,}; area={catchment.area_m2:g} m2; "
                    f"accumulation={catchment.accumulation_cells}; mask={catchment.mask}",
                    catchment.diagnostic,
                ]
            )
        self.report.setPlainText("\n".join(lines))
