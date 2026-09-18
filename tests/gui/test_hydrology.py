"""Gui coverage for hydrology."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from highway_drainage.presentation.main_window import MainWindow
from tests.support.hydrology import chain, service


def test_gui_hydrology_run_and_source_invalidation(qtbot: QtBot, tmp_path: Path) -> None:
    base = chain(tmp_path)
    window = MainWindow(hydrology_use_case=service())
    qtbot.addWidget(window)
    window.coordinate_panel.snap_result = base.prepared
    panel = window.hydrology_panel
    panel.output.setText(str(base.output))
    panel.run_button.click()
    qtbot.waitUntil(lambda: panel.isEnabled(), timeout=120000)
    assert panel.result is not None
    assert len(panel.result.catchments) == 4
    assert "area=20 m2" in panel.report.toPlainText()
    window.coordinate_panel.invalidate()
    assert panel.result is None
    window.close()
