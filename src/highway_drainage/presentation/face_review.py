"""Paged overlap report with a local geometry preview, without requiring a DEM."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.application.face_report import write_face_report
from highway_drainage.domain.terrain import FaceAudit, FaceFinding
from highway_drainage.presentation.navigation_view import NavigationView


class FaceReviewDialog(QDialog):
    PAGE_SIZE = 500

    def __init__(self, audit: FaceAudit, crs_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.audit = audit
        self._page = 0
        self._rows: list[FaceFinding] = []
        self._overlap_bounds = QRectF()
        self.setWindowTitle("Face overlap review")
        self.resize(1150, 780)
        layout = QVBoxLayout(self)
        summary = QLabel(audit.summary() + f"\nCoordinates: {crs_label}")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        if audit.errors:
            errors = QLabel("\n".join(audit.errors[:10]) + (
                "\nMore geometry errors are included in the CSV." if len(audit.errors) > 10 else ""
            ))
            errors.setWordWrap(True)
            layout.addWidget(errors)
        toolbar = QHBoxLayout()
        self.conflicts_only = QCheckBox("Unresolved pairs only")
        self.conflicts_only.setChecked(audit.blocked)
        self.conflicts_only.toggled.connect(self._filter)
        toolbar.addWidget(self.conflicts_only)
        self.previous = QPushButton("Previous")
        self.previous.clicked.connect(lambda: self._turn_page(-1))
        self.next = QPushButton("Next")
        self.next.clicked.connect(lambda: self._turn_page(1))
        self.page_label = QLabel()
        toolbar.addWidget(self.previous)
        toolbar.addWidget(self.page_label)
        toolbar.addWidget(self.next)
        export = QPushButton("Export full CSV report")
        export.clicked.connect(self._export)
        toolbar.addWidget(export)
        layout.addLayout(toolbar)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Finding", "Face A", "Face B", "Overlap m²", "Triangle A %", "Triangle B %",
            "Max ΔZ m", "Cleanup",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._select)
        splitter.addWidget(self.table)
        self.view = NavigationView()
        splitter.addWidget(self.view)
        layout.addWidget(splitter, 1)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        buttons = QHBoxLayout()
        fit = QPushButton("Fit face pair")
        fit.clicked.connect(self.view.reset_view)
        zoom = QPushButton("Zoom to overlap")
        zoom.clicked.connect(self._zoom_overlap)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(QLabel("Blue: face A · Green: face B · Red: overlap / shared edge"))
        buttons.addWidget(fit)
        buttons.addWidget(zoom)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._filter()

    def _filter(self) -> None:
        self._rows = [f for f in self.audit.findings
                      if not self.conflicts_only.isChecked() or not f.repairable]
        self._page = 0
        self._populate()

    def _turn_page(self, step: int) -> None:
        self._page += step
        self._populate()

    def _populate(self) -> None:
        start = self._page * self.PAGE_SIZE
        rows = self._rows[start:start + self.PAGE_SIZE]
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for row, finding in enumerate(rows):
            a, b = (self.audit.faces[self.audit.owners[i]].reference
                    for i in (finding.first, finding.second))
            values = (
                finding.reason, f"{a.path.name}/{a.handle}", f"{b.path.name}/{b.handle}",
                f"{finding.area:.9g}", f"{finding.first_fraction * 100:.6g}",
                f"{finding.second_fraction * 100:.6g}", f"{finding.max_z_difference:.9g}",
                "Midpoint" if self.audit.options.overlap_policy == "midpoint" else
                "Eligible" if finding.repairable else "Blocked",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.page_label.setText(
            f"{start + 1 if rows else 0}–{start + len(rows)} / {len(self._rows)}"
        )
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(start + len(rows) < len(self._rows))
        if rows:
            self.table.selectRow(0)
        else:
            self.details.setText("No pairs match this filter. Geometry errors appear above.")
            scene = self.view.scene()
            assert scene is not None
            scene.clear()

    def _select(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        finding = self._rows[self._page * self.PAGE_SIZE + row]
        scene = self.view.scene()
        assert scene is not None
        scene.clear()
        for index, color in ((finding.first, QColor("#147dcc")),
                             (finding.second, QColor("#198746"))):
            points = [QPointF(v.x - finding.x, finding.y - v.y)
                      for v in self.audit.triangles[index]]
            pen = QPen(color, 2)
            pen.setCosmetic(True)
            fill = QColor(color)
            fill.setAlpha(40)
            scene.addPolygon(QPolygonF(points), pen, QBrush(fill))
        path = QPainterPath()
        for outline in finding.outlines:
            if not outline:
                continue
            x, y = outline[0]
            path.moveTo(x - finding.x, finding.y - y)
            for x, y in outline[1:]:
                path.lineTo(x - finding.x, finding.y - y)
        pen = QPen(QColor("#d72b35"), 3)
        pen.setCosmetic(True)
        scene.addPath(path, pen, QBrush(QColor(215, 43, 53, 100)))
        self._overlap_bounds = path.boundingRect()
        a, b = (self.audit.faces[self.audit.owners[i]].reference
                for i in (finding.first, finding.second))
        kept = self.audit.faces[self.audit.owners[finding.retained]].reference
        self.details.setText(
            f"A: {a.path}, layer {a.layer}, handle {a.handle}\n"
            f"B: {b.path}, layer {b.layer}, handle {b.handle}\n"
            f"Location: X={finding.x:.6f}, Y={finding.y:.6f}. "
            + ("Overlapping cell elevations use (lowest + highest face Z) / 2. "
               "This pair will not block the DEM build."
               if self.audit.options.overlap_policy == "midpoint" else
               f"Accepted overlap retains {kept.path.name}/{kept.handle}. "
               "Cleanup is applied only after the entire audit passes."
               if finding.repairable else
               "Resolve the conflicting surface in CAD and reimport. "
               "Use tolerance changes only when supported by survey accuracy.")
        )
        self.view.reset_view()

    def _zoom_overlap(self) -> None:
        margin = max(self._overlap_bounds.width(), self._overlap_bounds.height(), 0.01) * 0.1
        self.view.fitInView(self._overlap_bounds.adjusted(-margin, -margin, margin, margin),
                            Qt.AspectRatioMode.KeepAspectRatio)

    def _export(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save full face audit", "face_overlap_report.csv", "CSV (*.csv)",
        )
        if filename:
            try:
                write_face_report(self.audit, Path(filename))
            except OSError as exc:
                QMessageBox.warning(self, "Could not save report", str(exc))
