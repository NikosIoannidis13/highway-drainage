from pathlib import Path

from pytestqt.qtbot import QtBot

from highway_drainage.presentation.dem_panel import DemPanel
from highway_drainage.presentation.main_window import MainWindow
from tests.support.dem import plane


def test_contour_role_and_export_controls(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    combo = window._role_combo()
    assert combo.findText("contour") >= 0
    assert combo.findText("terrain samples") >= 0
    panel = DemPanel()
    qtbot.addWidget(panel)
    panel.output.setText(str(tmp_path / "contours.tif"))
    panel.contour_spacing.setText("2")
    panel.max_contour_edge.setText("50")
    panel.sample_coverage.setCurrentIndex(1)
    dataset = plane(tmp_path)
    assert dataset.face_audit is not None
    panel.set_face_options(dataset.face_audit.options)
    request = panel.request(dataset)
    assert request.contour_spacing == 2 and request.max_contour_edge == 50
    assert request.sample_coverage == "convex_hull"
    panel.contour_spacing.clear()
    assert panel.request(dataset).contour_spacing is None
