"""Export form; parsing values only, no terrain or raster calculations."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
from highway_drainage.domain.terrain import FaceAuditOptions, TerrainDataset


class DemPanel(QWidget):
    export_requested = Signal()
    combine_requested = Signal()
    audit_requested = Signal()
    audit_review_requested = Signal()
    audit_settings_changed = Signal()

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
        self.overlap_policy = QComboBox()
        self.overlap_policy.addItem("Use midpoint elevation and continue", "midpoint")
        self.overlap_policy.addItem("Strict checks and tolerance-based cleanup", "strict")
        form.addRow("Overlapping 3D faces", self.overlap_policy)
        self.overlap_area = QDoubleSpinBox()
        self.overlap_z = QDoubleSpinBox()
        for tolerance_field, maximum in ((self.overlap_area, 1e9), (self.overlap_z, 1000)):
            tolerance_field.setDecimals(9)
            tolerance_field.setRange(0, maximum)
            tolerance_field.setValue(1e-6)
            tolerance_field.valueChanged.connect(self.audit_settings_changed)
        form.addRow("Maximum partial face overlap (m²)", self.overlap_area)
        form.addRow("Maximum face elevation difference (m)", self.overlap_z)
        self.overlap_policy.currentIndexChanged.connect(self._overlap_policy_changed)
        self._overlap_policy_changed()
        self.audit_status = QLabel("Face overlaps are checked during terrain import.")
        self.audit_status.setWordWrap(True)
        form.addRow(self.audit_status)
        audit_note = QLabel(
            "Midpoint uses (lowest + highest face elevation) / 2 at each overlapping cell. "
            "Two faces give their average. Elevation conflicts are reported but do not block "
            "building. Single-face areas retain their elevations. Tolerances apply only in "
            "strict mode. Original DXFs are unchanged."
        )
        audit_note.setWordWrap(True)
        form.addRow(audit_note)
        audit_row = QHBoxLayout()
        self.audit_button = QPushButton("Recheck faces")
        self.audit_button.clicked.connect(self.audit_requested)
        self.audit_review_button = QPushButton("Review face overlaps")
        self.audit_review_button.clicked.connect(self.audit_review_requested)
        audit_row.addWidget(self.audit_button)
        audit_row.addWidget(self.audit_review_button)
        form.addRow(audit_row)
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
            "Faces are preserved except for accepted overlap cleanup. "
            "Linework reconstruction requires one boundary and "
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

    def face_options(self) -> FaceAuditOptions:
        return FaceAuditOptions(
            self.overlap_area.value(), self.overlap_z.value(),
            str(self.overlap_policy.currentData()),
        )

    def _overlap_policy_changed(self) -> None:
        strict = self.overlap_policy.currentData() == "strict"
        self.overlap_area.setEnabled(strict)
        self.overlap_z.setEnabled(strict)
        self.audit_settings_changed.emit()

    def set_face_options(self, options: FaceAuditOptions) -> None:
        for widget in (self.overlap_policy, self.overlap_area, self.overlap_z):
            widget.blockSignals(True)
        self.overlap_policy.setCurrentIndex(self.overlap_policy.findData(options.overlap_policy))
        self.overlap_area.setValue(options.max_overlap_area)
        self.overlap_z.setValue(options.max_z_difference)
        strict = options.overlap_policy == "strict"
        self.overlap_area.setEnabled(strict)
        self.overlap_z.setEnabled(strict)
        for widget in (self.overlap_policy, self.overlap_area, self.overlap_z):
            widget.blockSignals(False)

    def show_face_status(self, dataset: TerrainDataset | None) -> None:
        audit = dataset.face_audit if dataset else None
        faces = bool(dataset and any(f.is_face for f in dataset.features))
        current = bool(audit and dataset and audit.matches(dataset.features)
                       and audit.options == self.face_options())
        self.audit_review_button.setEnabled(current)
        self.audit_button.setEnabled(faces)
        self.export_button.setEnabled(bool(
            dataset and not dataset.has_errors
            and (not faces or (current and audit and not audit.blocked))
        ))
        self.audit_status.setText(
            audit.summary() if current and audit else
            "Face checks required. Click Recheck faces before building." if faces else
            "No 3D faces to check." if dataset else
            "Face overlaps are checked during terrain import."
        )

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
        if any(f.is_face for f in dataset.features):
            audit = dataset.face_audit
            if (audit is None or not audit.matches(dataset.features)
                    or audit.options != self.face_options()):
                raise ValueError("Click Recheck faces before building the DEM.")
            audit.require_ready()
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
