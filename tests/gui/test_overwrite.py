"""A warning is required before starting a replacement; Cancel preserves the current result."""

from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from highway_drainage.domain.dem import DemRequest
from highway_drainage.presentation.main_window import MainWindow
from tests.support.dem import dem_service, plane
from tests.support.hydrology import chain, service


@pytest.mark.parametrize("kind", ["dem", "catchments"])
def test_overwrite_warning_cancel_then_confirm(qtbot: QtBot, tmp_path: Path, kind: str) -> None:
    window = MainWindow(dem_use_case=dem_service(), hydrology_use_case=service())
    qtbot.addWidget(window)
    window.show()
    if kind == "dem":
        dataset = plane(tmp_path)
        path = tmp_path / "existing.tif"
        dem_service().execute(DemRequest(dataset, path))
        window._show_result(dataset)
        window.export_panel.set_build_available(True)
        window.export_panel.output.setText(str(path))
        window.export_panel.cell_size.setText("2")
        button = window.export_panel.export_button
        before = path.read_bytes()
    else:
        request = chain(tmp_path)
        first = service().execute(request)
        path = request.output
        window.coordinate_panel.snap_result = request.prepared
        window.hydrology_panel.output.setText(str(path))
        window.hydrology_panel.minimum_cells.setText("4")
        window._show_hydrology(first)
        button = window.hydrology_panel.run_button
        before = (path / "manifest.json").read_bytes()

    def respond(answer: QMessageBox.StandardButton) -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert str(path.resolve()) in dialog.text()
        assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.Cancel)
        dialog.button(answer).click()

    QTimer.singleShot(0, lambda: respond(QMessageBox.StandardButton.Cancel))
    button.click()
    assert window._thread is None
    artifact = path if kind == "dem" else path / "manifest.json"
    assert artifact.read_bytes() == before
    if kind == "catchments":
        assert window.hydrology_panel.result is first
    QTimer.singleShot(0, lambda: respond(QMessageBox.StandardButton.Yes))
    button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=120000)
    assert artifact.read_bytes() != before
    if kind == "catchments":
        assert window.hydrology_panel.result is not None
        assert window.hydrology_panel.result.catchments[0].status == "rejected"
    window.close()
