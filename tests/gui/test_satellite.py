import json
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from highway_drainage.application.hydrology import DelineateCatchments
from highway_drainage.infrastructure.project_raster import RasterProjectReader
from highway_drainage.infrastructure.satellite import GoogleSatelliteBuilder
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import candidates, raster


def test_embedded_mode_switches_layers_without_hydrology_or_key(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Satellite view must not recompute hydrology")

    monkeypatch.setattr(DelineateCatchments, "execute", forbidden)
    monkeypatch.setattr(
        "highway_drainage.presentation.satellite_view.QSettings",
        lambda *a: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
    )
    window = MainWindow(satellite_builder=GoogleSatelliteBuilder())
    qtbot.addWidget(window)
    window.show()
    window._show_project_raster(RasterProjectReader().read(raster(tmp_path), Event()))
    window._show_crossings(candidates((101, 199)))
    original = window.crossing_panel.result
    preview = window.preview
    satellite = preview.satellite_view
    preview.show_satellite.setChecked(True)
    qtbot.waitUntil(lambda: '"crossing"' in satellite._payload, timeout=10000)
    assert preview.stack.currentWidget() is satellite
    assert json.loads(satellite._payload)["image"] is None
    satellite.key.clear()
    satellite.connect_button.click()
    assert "Enter a Google" in satellite.message.text() and satellite.web is None
    preview.show_raster_background.setChecked(True)
    qtbot.waitUntil(lambda: json.loads(satellite._payload)["image"] is not None, timeout=10000)
    preview.mode.setCurrentIndex(0)
    qtbot.waitUntil(lambda: json.loads(satellite._payload)["view"] == "terrain", timeout=10000)
    assert json.loads(satellite._payload)["geojson"]["features"] == []
    preview.show_satellite.setChecked(False)
    assert preview.stack.currentWidget() is not satellite
    assert window.crossing_panel.result is original
    qtbot.waitUntil(lambda: satellite._thread is None, timeout=10000)
    window.close()
