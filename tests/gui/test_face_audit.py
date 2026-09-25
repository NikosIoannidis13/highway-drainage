"""Review overlaps before exporting; settings changes invalidate completed checks."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QTabWidget
from pytestqt.qtbot import QtBot

from highway_drainage.presentation.face_review import FaceReviewDialog
from highway_drainage.presentation.main_window import MainWindow
from tests.support.dem import dem_service, plane
from tests.support.face_audit import overlapping
from tests.support.terrain_input import service


def test_conflicts_disable_build_and_recheck_uses_loaded_geometry(
    qtbot: QtBot, tmp_path: Path,
) -> None:
    window = MainWindow(service(), dem_use_case=dem_service())
    qtbot.addWidget(window)
    window.show()
    dataset = overlapping(tmp_path)
    window._show_result(dataset)
    window._finished()
    panel = window.export_panel
    assert panel.build_page.isEnabled()
    assert not panel.export_button.isEnabled()
    assert panel.audit_review_button.isEnabled()
    assert "DEM build blocked" in panel.audit_status.text()
    panel.overlap_area.setValue(5)
    assert window.dataset is not None and window.dataset.face_audit is None
    assert not panel.audit_review_button.isEnabled()
    assert not panel.export_button.isEnabled()
    # Rechecking must not read the original drawing again.
    dataset.sources[0].path.unlink()
    panel.audit_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.export_button.isEnabled(), window.report.toPlainText()
    assert "Face checks passed" in panel.audit_status.text()
    assert window.dataset is not None and window.dataset.face_audit is not None
    assert dataset.face_audit is not None
    assert window.dataset.face_audit.faces == dataset.face_audit.faces
    panel.overlap_z.setValue(0.001)
    assert not panel.export_button.isEnabled()
    panel.output.setText(str(tmp_path / "not_built.tif"))
    window._start_export()
    assert window._thread is None
    assert "Recheck faces" in window.report.toPlainText()


def test_overlap_report_selects_pair_and_exports_all_findings(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = overlapping(tmp_path, 0.05)
    assert dataset.face_audit is not None
    dialog = FaceReviewDialog(dataset.face_audit, dataset.crs_label)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.table.rowCount() == 1
    item = dialog.table.item(0, 6)
    assert item is not None and "0.05" in item.text()
    assert "layer" in dialog.details.text()
    scene = dialog.view.scene()
    assert scene is not None and len(scene.items()) == 3
    dialog._zoom_overlap()
    assert dialog.view.transform().m11() > 0
    path = tmp_path / "audit.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(path), ""))
    dialog._export()
    assert "unresolved" in path.read_text(encoding="utf-8-sig")


def test_clean_import_is_ready_and_source_change_discards_audit(
    qtbot: QtBot, tmp_path: Path,
) -> None:
    window = MainWindow(service(), dem_use_case=dem_service())
    qtbot.addWidget(window)
    window.show()
    window._show_result(plane(tmp_path))
    window._finished()
    assert window.export_panel.export_button.isEnabled()
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(1)
    window._invalidate()
    assert window.dataset is None
    assert not window.export_panel.build_page.isEnabled()
    assert not window.export_panel.audit_review_button.isEnabled()


def test_midpoint_is_default_and_recheck_unblocks_overlap(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow(service(), dem_use_case=dem_service())
    qtbot.addWidget(window)
    panel = window.export_panel
    assert panel.overlap_policy.currentData() == "midpoint"
    window._show_result(overlapping(tmp_path, 7))
    window._finished()
    assert panel.overlap_policy.currentData() == "strict"
    assert not panel.export_button.isEnabled()
    panel.overlap_policy.setCurrentIndex(panel.overlap_policy.findData("midpoint"))
    assert not panel.overlap_z.isEnabled()
    panel.audit_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.export_button.isEnabled(), window.report.toPlainText()
    assert "use midpoint elevations" in panel.audit_status.text()
    assert window.dataset is not None and window.dataset.face_audit is not None
    dialog = FaceReviewDialog(window.dataset.face_audit, window.dataset.crs_label)
    qtbot.addWidget(dialog)
    assert "lowest + highest" in dialog.details.text()
