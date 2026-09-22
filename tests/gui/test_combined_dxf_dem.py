from pathlib import Path

import numpy as np
import rasterio
from PySide6.QtWidgets import QComboBox, QTabWidget
from pytestqt.qtbot import QtBot

from highway_drainage.infrastructure.preview import RasterPreviewReader
from highway_drainage.presentation.main_window import MainWindow
from tests.support.combined_dem import combined_sources
from tests.support.dem import dem_service
from tests.support.terrain_input import service


def test_import_two_dxfs_and_build_combined_dem_in_gui(qtbot: QtBot, tmp_path: Path) -> None:
    sources = combined_sources(tmp_path)
    for source in sources:
        source.path.with_suffix(".prj").write_text("EPSG:32634")
    window = MainWindow(service(), dem_use_case=dem_service(), previews=RasterPreviewReader())
    qtbot.addWidget(window)
    window.show()
    window.add_files([str(source.path) for source in sources])
    role = window.sources.cellWidget(1, 3)
    assert isinstance(role, QComboBox)
    role.setCurrentText("terrain samples")
    window.import_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert window.dataset is not None and not window.dataset.has_errors
    panel = window.export_panel
    assert panel.build_page.isEnabled()
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.surface_mode.setCurrentIndex(1)
    assert panel.mode.currentText() == "Build from DXF terrain"
    assert panel.output.text() == "combined_dem.tif"
    panel.sample_coverage.setCurrentIndex(1)
    target = tmp_path / "combined_dem.tif"
    panel.output.setText(str(target))
    assert panel.request(window.dataset).surface_mode == "faces_with_polyline_gaps"
    panel.export_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert target.exists(), window.report.toPlainText()
    assert window.active_raster is not None
    assert window.active_raster.path == target
    assert window.preview.terrain_view.snapshot is not None
    assert window.coordinate_panel.dem_path.text() == str(target)
    assert "8 face cells, 8 polyline gap-filler cells" in window.report.toPlainText()
    assert panel.build_page.isEnabled()
    with rasterio.open(target) as src:
        data = src.read(1)
        np.testing.assert_array_equal(data[:, :2], 0)
        assert (data[:, 2:] > 100).all()
    panel.surface_mode.setCurrentIndex(0)
    assert panel.export_button.text() == "Build terrain and export GeoTIFF"
    assert panel.output.text() == str(target)
