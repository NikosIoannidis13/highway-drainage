"""Export form; parsing values only, no terrain or raster calculations."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.domain.dem import DemRequest, Extent
from highway_drainage.domain.terrain import TerrainDataset


class DemPanel(QWidget):
    export_requested = Signal()
    combine_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(["Build from DXF terrain", "Combine existing rasters"])
        layout.addWidget(self.mode)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        self.build_page = QWidget()
        self.pages.addWidget(self.build_page)
        self.mode.currentIndexChanged.connect(self.pages.setCurrentIndex)
        layout = QVBoxLayout(self.build_page)
        layout.addWidget(
            QLabel("DEM uses the imported working CRS. Change CRS by reimporting terrain.")
        )
        form = QFormLayout()
        self.surface_mode = QComboBox()
        self.surface_mode.addItem("Single terrain surface", "single")
        self.surface_mode.addItem(
            "3D faces with polyline gap filling", "faces_with_polyline_gaps"
        )
        form.addRow("Build method", self.surface_mode)
        self.surface_note = QLabel(
            "Build a single surface from the imported terrain geometry."
        )
        self.surface_note.setWordWrap(True)
        form.addRow(self.surface_note)
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
        form.addRow("Output file", output_row)
        layout.addLayout(form)
        note = QLabel(
            "Use terrain samples for XYZ polylines with varying elevations; "
            "contour requires constant Z. "
            "Sampled line segments are not enforced mesh edges. Boundary Z is ignored for samples. "
            "Outside the boundary or sample hull remains NoData. "
            "Existing triangles are preserved. Linework reconstruction requires one boundary and "
            "breaklines forming closed, noded regions. Default limits: 100 million cells, "
            "5 million vertices, 10 million triangles and 24 GiB estimated working memory. "
            "Replacing an existing output requires confirmation."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.export_button = QPushButton("Build terrain and export GeoTIFF")
        self.export_button.clicked.connect(self.export_requested)
        self.surface_mode.currentIndexChanged.connect(self._surface_mode_changed)
        layout.addWidget(self.export_button)
        layout.addStretch()
        self.combine_page = QWidget()
        self.pages.addWidget(self.combine_page)
        merge_layout = QVBoxLayout(self.combine_page)
        note = QLabel(
            "Keep primary elevations; fill only missing cells from the second raster. "
            "Use the 3D-face TIFF as primary and the polyline TIFF as gap filler. "
            "The output covers both inputs on the primary grid; the filler is aligned "
            "using bilinear interpolation. Remaining gaps stay NoData."
        )
        note.setWordWrap(True)
        merge_layout.addWidget(note)
        merge_form = QFormLayout()
        self.primary_path = QLineEdit()
        self.filler_path = QLineEdit()
        self.combined_path = QLineEdit("combined_dem.tif")
        for label, field, save in (
            ("Primary DEM (3D faces)", self.primary_path, False),
            ("Gap-filling DEM (polylines)", self.filler_path, False),
            ("Combined output", self.combined_path, True),
        ):
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton("Browse…")
            button.clicked.connect(
                lambda checked=False, field=field, save=save: self._choose_merge(field, save)
            )
            row.addWidget(button)
            merge_form.addRow(label, row)
        merge_layout.addLayout(merge_form)
        self.same_vertical = QCheckBox("Both DEMs use the same elevation units and vertical datum")
        merge_layout.addWidget(self.same_vertical)
        self.use_combined = QCheckBox("Use combined DEM for subsequent processing")
        self.use_combined.setChecked(True)
        merge_layout.addWidget(self.use_combined)
        self.combine_button = QPushButton("Create combined DEM")
        self.combine_button.clicked.connect(self.combine_requested)
        merge_layout.addWidget(self.combine_button)
        merge_layout.addStretch()

    def set_build_available(self, available: bool) -> None:
        self.build_page.setEnabled(available)

    def _surface_mode_changed(self) -> None:
        combined = self.surface_mode.currentData() == "faces_with_polyline_gaps"
        self.surface_note.setText(
            "Uses the imported DXFs directly; no input TIFFs are needed. 3D faces supply "
            "the primary elevations. Polylines assigned as terrain samples or contour fill "
            "only uncovered cells. Both surfaces use the cell size and extent below. "
            "If you have no boundary, select 'Use sample convex hull if no boundary'."
            if combined else "Build a single surface from the imported terrain geometry."
        )
        self.export_button.setText(
            "Build combined terrain and export GeoTIFF" if combined
            else "Build terrain and export GeoTIFF"
        )
        if combined and not self.output.text().strip():
            self.output.setText("combined_dem.tif")

    def _choose_merge(self, field: QLineEdit, save: bool) -> None:
        if save:
            path, _ = QFileDialog.getSaveFileName(
                self, "Combined DEM output", field.text(), "GeoTIFF (*.tif *.tiff)",
                options=QFileDialog.Option.DontConfirmOverwrite,
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select elevation raster", field.text(), "GeoTIFF (*.tif *.tiff)"
            )
        if path:
            field.setText(path)

    def combine_paths(self) -> tuple[Path, Path, Path]:
        if not self.same_vertical.isChecked():
            raise ValueError("Confirm that both DEMs use the same elevation units and datum.")
        fields = (self.primary_path, self.filler_path, self.combined_path)
        if any(not field.text().strip() for field in fields):
            raise ValueError("Select two input TIFFs and an output file.")
        primary, filler, output = (Path(field.text().strip()).resolve() for field in fields)
        if not primary.is_file() or not filler.is_file():
            raise ValueError("Both input TIFFs must exist.")
        if primary == filler or output in (primary, filler):
            raise ValueError("Choose two different inputs and a separate output file.")
        if output.suffix.lower() not in (".tif", ".tiff"):
            raise ValueError("Use a .tif or .tiff output filename.")
        return primary, filler, output

    def _choose_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "DEM output", "", "GeoTIFF (*.tif *.tiff)")
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
            raise ValueError("Choose a GeoTIFF output filename.")
        return DemRequest(
            dataset,
            Path(self.output.text().strip()),
            float(self.cell_size.text()),
            extent,
            float(self.nodata.text()),
            self.units.currentText(),
            surface_mode=str(self.surface_mode.currentData()),
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
