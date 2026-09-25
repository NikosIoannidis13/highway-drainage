"""Show resolved source CRSs separately from the raster/output CRS."""

from pathlib import Path
from threading import Event

import pytest
from pyproj import CRS
from pytestqt.qtbot import QtBot

from highway_drainage.domain.crossings import DrawingUnits
from highway_drainage.infrastructure.project_raster import RasterProjectReader
from highway_drainage.presentation.crossing_panel import CrossingPanel
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import raster
from tests.support.crossings import crossing_service, request_for
from tests.support.terrain_input import document


@pytest.mark.parametrize("origin", ["detected", "entered", "assumed"])
def test_imported_crs_summary(qtbot: QtBot, tmp_path: Path, origin: str) -> None:
    highway, culvert = document(), document()
    highway.modelspace().add_line((500000, 4400000), (500010, 4400000))
    culvert.modelspace().add_line((500005, 4399995), (500005, 4400005))
    files = request_for(tmp_path, highway, culvert)
    panel = CrossingPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project_raster(tmp_path / "dem.tif", "EPSG:32634", "WGS 84 / UTM zone 34N")
    panel.highway_path.setText(str(files.highway.path))
    panel.culvert_path.setText(str(files.culverts.path))
    if origin == "detected":
        files.highway.path.with_suffix(".prj").write_text(CRS(32635).to_wkt(), encoding="utf-8")
    elif origin == "entered":
        panel.highway_crs.setText("32635")
    assert "awaiting" in panel.highway_crs_info.text().lower()
    panel.show_result(crossing_service().execute(panel.request()))
    assert "EPSG:32634" in panel.raster_crs_info.text()
    assert "EPSG:32634" in panel.output_crs_info.text()
    assert "EPSG:32634" in panel.culvert_crs_info.text()
    assert "assumed project CRS" in panel.culvert_crs_info.text()
    assert origin in panel.highway_crs_info.text()
    assert ("EPSG:32634" if origin == "assumed" else "EPSG:32635") in (
        panel.highway_crs_info.text()
    )
    assert "WGS 84 / UTM zone" in panel.highway_crs_info.text()
    assert "DXF header: Meters" in panel.highway_crs_info.text()
    assert "XY scale 1 " in panel.highway_crs_info.text()
    result = panel.result
    panel.show_crs.click()
    assert panel.crs_details.isHidden()
    panel.show_crs.click()
    assert not panel.crs_details.isHidden() and panel.result is result
    panel.highway_units.setCurrentIndex(panel.highway_units.findData(DrawingUnits.FEET.value))
    assert panel.result is None
    assert panel.line_export_request(False).source.units == DrawingUnits.FEET
    assert panel.request().highway.units == DrawingUnits.FEET
    panel.highway_path.setText(str(tmp_path / "replacement.dxf"))
    assert panel.highway_units.currentData() == DrawingUnits.SOURCE_CRS.value
    assert "awaiting" in panel.highway_crs_info.text().lower()
    assert "EPSG:32635" not in panel.highway_crs_info.text()


def test_unit_change_invalidates_crossings_and_wrong_scale_stops_gui_import(
    qtbot: QtBot, tmp_path: Path,
) -> None:
    road, pipe = document(), document()
    pipe.units = 1
    road.modelspace().add_line((100, 195), (110, 195))
    pipe.modelspace().add_line((105, 190), (105, 200))
    files = request_for(tmp_path, road, pipe)
    window = MainWindow(crossing_use_case=crossing_service())
    qtbot.addWidget(window)
    window._show_project_raster(RasterProjectReader().read(raster(tmp_path), Event()))
    panel = window.crossing_panel
    panel.highway_path.setText(str(files.highway.path))
    panel.culvert_path.setText(str(files.culverts.path))
    panel.find_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is not None and len(panel.result.points) == 1
    assert "Inches" in panel.culvert_crs_info.text()
    assert "XY scale 1 " in panel.culvert_crs_info.text()
    panel.culvert_units.setCurrentIndex(panel.culvert_units.findData(DrawingUnits.HEADER.value))
    assert panel.result is None and not panel.export_button.isEnabled()
    panel.find_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is None and panel.table.rowCount() == 0
    assert "Culverts" in window.status.text() and "does not overlap" in window.status.text()
    assert "0.0254" in window.status.text()
    assert panel.find_button.isEnabled()
    panel.culvert_units.setCurrentIndex(panel.culvert_units.findData(DrawingUnits.METRES.value))
    panel.find_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is not None and len(panel.result.points) == 1
    assert (panel.result.points[0].x, panel.result.points[0].y) == (105, 195)
    assert "XY scale 1 " in window.report.toPlainText()
