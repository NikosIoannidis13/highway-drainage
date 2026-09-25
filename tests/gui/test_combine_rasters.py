from pathlib import Path

import numpy as np
import rasterio
from pytestqt.qtbot import QtBot
from rasterio.transform import from_origin

from highway_drainage.infrastructure.project_raster import RasterProjectReader
from highway_drainage.presentation.main_window import MainWindow


def test_combine_without_dxf_sets_active_dem_and_restores_controls(
    qtbot: QtBot, tmp_path: Path,
) -> None:
    primary, filler, output = (tmp_path / name for name in ("a.tif", "b.tif", "out.tif"))
    for path, values in ((primary, [[0., -9999.]]), (filler, [[8., 9.]])):
        with rasterio.open(path, "w", driver="GTiff", count=1, dtype="float32",
                           width=2, height=1, crs="EPSG:2100",
                           transform=from_origin(590000, 3917000, 1, 1), nodata=-9999) as dst:
            dst.write(np.array(values, dtype="float32"), 1)
    window = MainWindow(project_rasters=RasterProjectReader())
    qtbot.addWidget(window)
    panel = window.export_panel
    assert panel.isEnabled()
    assert not panel.build_page.isEnabled()
    panel.mode.setCurrentIndex(1)
    panel.primary_path.setText(str(primary))
    panel.filler_path.setText(str(filler))
    panel.combined_path.setText(str(output))
    panel.same_vertical.setChecked(True)
    panel.combine_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert window.active_raster is not None, window.report.toPlainText()
    assert window.active_raster.path == output
    assert window.coordinate_panel.dem_path.text() == str(output)
    assert panel.isEnabled()
    assert panel.combine_button.isEnabled()
    assert not panel.build_page.isEnabled()
    with rasterio.open(output) as src:
        np.testing.assert_array_equal(src.read(1), [[0, 9]])
