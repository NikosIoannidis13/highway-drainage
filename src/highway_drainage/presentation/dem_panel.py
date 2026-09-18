"""Export form; parsing values only, no terrain or raster calculations."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.domain.dem import DemRequest, Extent
from highway_drainage.domain.terrain import TerrainDataset


class DemPanel(QWidget):
    export_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("DEM uses the imported working CRS. Change CRS by reimporting terrain.")
        )
        form = QFormLayout()
        self.cell_size = QLineEdit("1.0")
        self.extent = QLineEdit()
        self.extent.setPlaceholderText(
            "Optional xmin, ymin, xmax, ymax; blank = terrain/boundary extent"
        )
        self.contour_spacing = QLineEdit()
        self.contour_spacing.setPlaceholderText("Blank = source vertices only")
        self.max_contour_edge = QLineEdit()
        self.max_contour_edge.setPlaceholderText("Blank = no edge-length filter")
        form.addRow("Polyline sample spacing (m)", self.contour_spacing)
        form.addRow("Maximum sample triangle edge (m)", self.max_contour_edge)
        self.sample_coverage = QComboBox()
        self.sample_coverage.addItem("Require an outer boundary", "boundary")
        self.sample_coverage.addItem("Use sample convex hull if no boundary", "convex_hull")
        form.addRow("Sampled terrain coverage", self.sample_coverage)
        self.nodata = QLineEdit("-9999")
        self.units = QComboBox()
        self.units.addItems(["m", "ft"])
        self.output = QLineEdit()
        form.addRow("Cell size (metres)", self.cell_size)
        form.addRow("Extent (working CRS)", self.extent)
        form.addRow("NoData (output elevation units; NaN allowed)", self.nodata)
        form.addRow("Output elevation units", self.units)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output)
        browse = QPushButton("Choose GeoTIFF…")
        browse.clicked.connect(self._choose_output)
        output_row.addWidget(browse)
        form.addRow("New output file", output_row)
        layout.addLayout(form)
        note = QLabel(
            "Use terrain samples for XYZ polylines with varying elevations; "
            "contour requires constant Z. "
            "Sampled line segments are not enforced mesh edges. Boundary Z is ignored for samples. "
            "Outside the boundary or sample hull remains NoData. "
            "Existing triangles are preserved. Linework reconstruction requires one boundary and "
            "breaklines forming closed, noded regions. Default limits: 100 million cells, "
            "5 million vertices, 10 million triangles and 24 GiB estimated working memory. "
            "Existing output files are not overwritten."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.export_button = QPushButton("Build terrain and export GeoTIFF")
        self.export_button.clicked.connect(self.export_requested)
        layout.addWidget(self.export_button)
        layout.addStretch()

    def _choose_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "New DEM output", "", "GeoTIFF (*.tif *.tiff)")
        if path:
            self.output.setText(path)

    def request(self, dataset: TerrainDataset) -> DemRequest:
        extent: Extent | None = None
        if self.extent.text().strip():
            values = [float(value.strip()) for value in self.extent.text().split(",")]
            if len(values) != 4:
                raise ValueError("Extent requires four numbers: xmin, ymin, xmax, ymax.")
            extent = values[0], values[1], values[2], values[3]
        if not self.output.text().strip():
            raise ValueError("Choose a new GeoTIFF output filename.")
        return DemRequest(
            dataset,
            Path(self.output.text().strip()),
            float(self.cell_size.text()),
            extent,
            float(self.nodata.text()),
            self.units.currentText(),
            sample_coverage=str(self.sample_coverage.currentData()),
            contour_spacing=(
                float(self.contour_spacing.text()) if self.contour_spacing.text().strip() else None
            ),
            max_contour_edge=(
                float(self.max_contour_edge.text())
                if self.max_contour_edge.text().strip()
                else None
            ),
        )
