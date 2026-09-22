from dataclasses import replace
from pathlib import Path

import pytest
import shapefile
from pyproj import CRS
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from highway_drainage.domain.crossings import CadLine, CadReference
from highway_drainage.infrastructure.point_export import ShapefilePointWriter
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import candidates
from tests.support.crossings import crossing_service
from tests.support.hydrology import chain
from tests.support.outlets import service as outlet_service
from tests.support.terrain_input import document, save


@pytest.mark.parametrize("culverts", [False, True])
@pytest.mark.parametrize("metadata", [False, True])
def test_export_single_dxf_immediately_without_other_inputs(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    culverts: bool,
    metadata: bool,
) -> None:
    doc = document()
    doc.modelspace().add_line((100, 200), (110, 220), dxfattribs={"layer": "keep"})
    doc.modelspace().add_line((0, 0), (5, 5), dxfattribs={"layer": "exclude"})
    source = save(doc, tmp_path / "single.dxf").path
    window = MainWindow(crossing_use_case=crossing_service(), point_writer=ShapefilePointWriter())
    qtbot.addWidget(window)
    panel = window.crossing_panel
    field = panel.culvert_path if culverts else panel.highway_path
    button = panel.culvert_export_button if culverts else panel.highway_export_button
    other = panel.highway_export_button if culverts else panel.culvert_export_button
    field.setText(str(source))
    assert button.isEnabled()
    assert not other.isEnabled()
    if metadata:
        source.with_suffix(".prj").write_text("EPSG:2100")
    else:
        panel.project_epsg.setText("2100")
    layers = panel.culvert_layers if culverts else panel.highway_layers
    layers.setText("keep")
    assert button.isEnabled()
    assert panel.result is None
    target = tmp_path / "single_export.shp"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is None
    assert window.active_raster is None
    with shapefile.Reader(str(target)) as reader:
        assert len(reader) == 1
        assert tuple(tuple(p) for p in reader.shape(0).points) == ((100, 200), (110, 220))
    assert CRS(target.with_suffix(".prj").read_text()).to_epsg() == 2100
    field.clear()
    assert not button.isEnabled()


def test_compute_and_export_all_cad_layers_without_raster(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    road, pipe = document(), document()
    road.modelspace().add_line((500000, 4200000), (500010, 4200000))
    pipe.modelspace().add_line((500005, 4199995), (500005, 4200005))
    window = MainWindow(crossing_use_case=crossing_service(), point_writer=ShapefilePointWriter())
    qtbot.addWidget(window)
    panel = window.crossing_panel
    panel.highway_path.setText(str(save(road, tmp_path / "road.dxf").path))
    panel.culvert_path.setText(str(save(pipe, tmp_path / "pipe.dxf").path))
    panel.project_epsg.setText("2100")
    panel.find_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert window.active_raster is None
    assert panel.result is not None
    assert len(panel.result.points) == 1
    assert CRS(panel.result.crs_wkt).to_epsg() == 2100
    for name, button, shape_type in (
        ("crossings", panel.export_button, shapefile.POINT),
        ("highway", panel.highway_export_button, shapefile.POLYLINE),
        ("culverts", panel.culvert_export_button, shapefile.POLYLINE),
    ):
        target = tmp_path / f"{name}.shp"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, target=target, **k: (str(target), "")
        )
        assert button.isEnabled()
        button.click()
        qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
        with shapefile.Reader(str(target)) as reader:
            assert reader.shapeType == shape_type
            assert len(reader) == 1
        assert CRS(target.with_suffix(".prj").read_text()).to_epsg() == 2100
    panel.project_epsg.setText("32634")
    assert panel.result is None
    assert not panel.export_button.isEnabled()


@pytest.mark.parametrize("culverts", [False, True])
def test_line_export_buttons_work_without_crossing_points(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, culverts: bool
) -> None:
    line = CadLine(CadReference(tmp_path / "input.dxf", "1", "lines", "LINE"), ((1, 2), (3, 4)))
    result = replace(candidates(), highways=(line,), culverts=(line,))
    window = MainWindow(crossing_use_case=crossing_service(), point_writer=ShapefilePointWriter())
    qtbot.addWidget(window)
    panel = window.crossing_panel
    panel.show_result(result)
    assert not panel.export_button.isEnabled()
    button = panel.culvert_export_button if culverts else panel.highway_export_button
    assert button.isEnabled()
    target = tmp_path / "export_lines.shp"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    with shapefile.Reader(str(target)) as reader:
        assert reader.shapeType == shapefile.POLYLINE
        assert len(reader) == 1
    panel.invalidate()
    assert not panel.highway_export_button.isEnabled()
    assert not panel.culvert_export_button.isEnabled()


@pytest.mark.parametrize("outlets", [False, True])
def test_point_export_buttons_and_invalidation(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outlets: bool
) -> None:
    prepared = chain(tmp_path).prepared
    window = MainWindow(
        crossing_use_case=crossing_service(),
        outlet_use_case=outlet_service(),
        point_writer=ShapefilePointWriter(),
    )
    qtbot.addWidget(window)
    window.crossing_panel.show_result(prepared.validation.crossings)
    window.coordinate_panel.show_snapping(prepared)
    panel = window.coordinate_panel if outlets else window.crossing_panel
    panel.setEnabled(True)
    assert panel.export_button.isEnabled()
    target = tmp_path / "export.shp"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    panel.export_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert target.is_file()
    with shapefile.Reader(str(target)) as reader:
        assert len(reader) == (3 if outlets else 4)
    panel.invalidate()
    assert not panel.export_button.isEnabled()
