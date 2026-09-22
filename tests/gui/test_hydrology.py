"""Gui coverage for hydrology."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from highway_drainage.application.hydrology import GenerateFlow
from highway_drainage.domain.outlets import SnapMode
from highway_drainage.infrastructure.hydrology import PyFlwdirHydrology
from highway_drainage.presentation.main_window import MainWindow
from tests.integration.test_flow import flow_request
from tests.support.hydrology import chain, service


def test_generate_flow_before_selecting_any_outlets(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = flow_request(tmp_path)
    window = MainWindow(flow_use_case=GenerateFlow(PyFlwdirHydrology()))
    qtbot.addWidget(window)
    panel = window.coordinate_panel
    panel.dem_path.setText(str(request.dem))
    assert panel.snap_result is None
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    panel.flow_button.click()
    assert window.task_progress.timer.isActive()
    qtbot.waitUntil(lambda: window._thread is None, timeout=120000)
    assert panel.isEnabled()
    accumulation = Path(panel.accumulation_path.text())
    assert accumulation.is_file()
    assert accumulation.name == "accumulation_cells.tif"
    assert panel.flow_units.text() == "cells"
    assert panel.snap_mode.currentText() == SnapMode.ACCUMULATION.value
    assert panel.snap_result is None
    assert "Flow grids ready" in window.status.text()
    panel.dem_path.setText(str(tmp_path / "different.tif"))
    assert not panel.accumulation_path.text()


def test_gui_hydrology_run_and_source_invalidation(qtbot: QtBot, tmp_path: Path) -> None:
    base = chain(tmp_path)
    window = MainWindow(hydrology_use_case=service())
    qtbot.addWidget(window)
    window.coordinate_panel.snap_result = base.prepared
    panel = window.hydrology_panel
    panel.output.setText(str(base.output))
    panel.resource_profile.setCurrentText("Large DEM")
    assert panel.request(base.prepared).max_cells == 50_000_000
    panel.run_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=120000)
    assert panel.result is not None
    assert len(panel.result.catchments) == 4
    assert "area=20 m2" in panel.report.toPlainText()
    window.coordinate_panel.invalidate()
    assert panel.result is None
    window.close()
