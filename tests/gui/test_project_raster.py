from pathlib import Path

import pytest
from pyproj import CRS, Transformer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QPushButton, QTabWidget
from pytestqt.qtbot import QtBot

from highway_drainage.infrastructure.project_raster import RasterProjectReader
from highway_drainage.presentation.main_window import MainWindow
from highway_drainage.presentation.task_progress import TaskProgress
from tests.support.coordinates import candidates, raster
from tests.support.crossings import crossing_service
from tests.support.terrain_input import TRIANGLE, document, save, service


@pytest.mark.parametrize("with_raster", [False, True])
def test_manual_epsg_import_and_project_transformation(
    qtbot: QtBot, tmp_path: Path, with_raster: bool
) -> None:
    window = MainWindow(service(), project_rasters=RasterProjectReader())
    qtbot.addWidget(window)
    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(0)
    window.show()
    if with_raster:
        window.load_raster(str(raster(tmp_path)))
        qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    doc = document()
    doc.modelspace().add_3dface(TRIANGLE)
    source = save(doc, tmp_path / "manual.dxf")
    window.add_files([str(source.path)])
    editor = window.sources.cellWidget(0, 1)
    assert isinstance(editor, QLineEdit)
    qtbot.waitUntil(editor.isVisible)
    QTest.keyClicks(editor, "2100")
    window.import_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert window.dataset is not None
    target = 32634 if with_raster else 2100
    assert CRS(window.dataset.crs_wkt).to_epsg() == target
    assert CRS(window.dataset.sources[0].crs).to_epsg() == 2100
    expected = Transformer.from_crs(2100, target, always_xy=True).transform(*TRIANGLE[0][:2])
    point = window.dataset.features[0].vertices[0]
    assert (point.x, point.y) == pytest.approx(expected)
    editor.setText("32634")
    assert window.dataset is None


@pytest.mark.parametrize("code", ["EPSG:2100", "99999999", "4326"])
def test_invalid_manual_epsg_rejected_before_dxf_loading(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    window = MainWindow(service())
    qtbot.addWidget(window)
    source = save(document(), tmp_path / "invalid.dxf")
    window.add_files([str(source.path)])
    editor = window.sources.cellWidget(0, 1)
    assert isinstance(editor, QLineEdit)
    editor.setText(code)
    reads: list[object] = []

    def unexpected_read(*args: object, **kwargs: object) -> None:
        reads.append(args)
        raise AssertionError("Invalid CRS must be rejected before DXF loading")

    for module in ("dxf", "cad_reference"):
        monkeypatch.setattr(f"highway_drainage.infrastructure.{module}.readfile", unexpected_read)
    window.import_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert not reads
    assert window.dataset is None
    assert "CRS" in window.status.text() or "EPSG" in window.status.text()
    assert window.inputs.isEnabled()


def test_open_external_raster_sets_project_crs_and_clears_stale_results(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow(project_rasters=RasterProjectReader())
    qtbot.addWidget(window)
    window.show()
    window._show_crossings(candidates((101, 199)))
    assert window.crossing_panel.result is not None
    path = raster(tmp_path)
    window.load_raster(str(path))
    assert window.task_progress.timer.isActive()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert not window.task_progress.timer.isActive()
    assert window.task_progress.bar.value() == 100
    assert "32634" in window.project_label.text()
    assert window.working_crs.isReadOnly() and not window.working_crs.isVisible()
    assert window.crossing_panel.working_crs.text() == window.working_crs.text()
    assert window.coordinate_panel.dem_path.text() == str(path)
    assert window.preview.terrain_view.snapshot is not None
    assert window.preview.drainage_view.snapshot is window.preview.terrain_view.snapshot
    assert window.preview.mode.currentIndex() == 0
    assert window.crossing_panel.result is None
    assert window.dataset is None
    original = window.active_raster
    window.load_raster(str(tmp_path / "missing.tif"))
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert window.active_raster is original
    assert window.task_progress.bar.value() == 0
    assert window.open_raster_button.isEnabled()


def test_progress_uses_real_counts_and_busy_for_unknown_stages(qtbot: QtBot) -> None:
    progress = TaskProgress()
    qtbot.addWidget(progress)
    progress.begin("Conditioning DEM")
    assert progress.bar.maximum() == 0
    progress.update_message("Writing DEM: 5/10 tiles")
    assert progress.bar.value() == 500
    progress.update_message("Computing flow directions")
    assert progress.bar.maximum() == 0
    progress.finish()
    assert not progress.timer.isActive()
    assert progress.bar.value() == 100
    assert progress.elapsed.text().endswith("Complete")
    assert "Computing flow directions" not in progress.elapsed.text()
    progress.begin("Preparing Google Earth preview")
    progress.finish(failed=True)
    assert "Stopped" in progress.elapsed.text()
    assert "Preparing" not in progress.elapsed.text()


def test_crossing_tab_optional_raster_sets_project_crs(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow(project_rasters=RasterProjectReader(), crossing_use_case=crossing_service())
    qtbot.addWidget(window)
    panel = window.crossing_panel
    assert panel.source_form.isEnabled()
    assert panel.find_button.isEnabled()
    assert not any(
        "source settings" in button.text().lower() for button in window.findChildren(QPushButton)
    )
    path = raster(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    panel.load_raster_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.source_form.isEnabled() and panel.find_button.isEnabled()
    assert "32634" in panel.raster_label.text()
    road, pipe = document(), document()
    road.modelspace().add_line((100, 195), (110, 195))
    pipe.modelspace().add_line((105, 190), (105, 200))
    panel.highway_path.setText(str(save(road, tmp_path / "road.dxf").path))
    panel.culvert_path.setText(str(save(pipe, tmp_path / "pipe.dxf").path))
    panel.find_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert panel.result is not None
    assert len(panel.result.points) == 1
    assert (panel.result.points[0].x, panel.result.points[0].y) == (105, 195)
    assert sum(i.code == "assumed_raster_crs" for i in panel.result.issues) == 2
    assert window.preview.mode.currentIndex() == 1
    scene = window.preview.drainage_view.scene()
    assert scene is not None
    assert sum(item.data(1) == "raster" for item in scene.items()) == 1
    assert window.preview.drainage_view.snapshot is window.preview.terrain_view.snapshot
