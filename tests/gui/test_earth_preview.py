from pathlib import Path
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from highway_drainage.infrastructure.earth_preview import KmzPreviewWriter
from highway_drainage.infrastructure.project_raster import RasterProjectReader
from highway_drainage.presentation.main_window import MainWindow
from tests.support.coordinates import candidates, raster


def test_button_exports_cached_preview_then_opens_earth(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []

    def launch(path: Path) -> bool:
        opened.append(path)
        return True

    monkeypatch.setattr("highway_drainage.presentation.main_window.open_google_earth", launch)
    window = MainWindow(project_rasters=RasterProjectReader(), earth_writer=KmzPreviewWriter())
    qtbot.addWidget(window)
    window.show()
    window.load_raster(str(raster(tmp_path)))
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    window._show_crossings(candidates((101, 199)))
    original = window.crossing_panel.result
    for include in (False, True):
        destination = tmp_path / f"chosen preview {include}"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, chosen=destination, **k: (str(chosen), "")
        )
        window.preview.show_raster_background.setChecked(include)
        window.preview.earth_button.click()
        qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
        assert window.crossing_panel.result is original
        assert window.preview.earth_button.isEnabled()
        assert opened[-1] == destination.with_suffix(".kmz")
        with ZipFile(opened[-1]) as archive:
            root = fromstring(archive.read("doc.kml"))
            overlays = root.findall(".//{http://www.opengis.net/kml/2.2}GroundOverlay")
            assert bool(overlays) == include
    assert len(opened) == 2 and opened[0] != opened[1]
    assert "KMZ export complete" in window.status.text()
    assert "separate Google Earth Pro window" in window.status.text()
    assert "Preparing" not in window.task_progress.elapsed.text()
    assert window.task_progress.elapsed.text().endswith("Complete")
    window.close()


@pytest.mark.parametrize("choice", ["cancel_save", "cancel_replace", "replace"])
def test_kmz_save_cancellation_and_overwrite(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str
) -> None:
    destination = tmp_path / "existing.kmz"
    destination.write_bytes(b"previous export")
    opened: list[Path] = []
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: ("" if choice == "cancel_save" else str(destination), ""),
    )
    warnings: list[str] = []

    def confirm(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        warnings.append(str(args[2]))
        return (
            QMessageBox.StandardButton.Yes
            if choice == "replace"
            else QMessageBox.StandardButton.Cancel
        )

    monkeypatch.setattr(QMessageBox, "warning", confirm)
    def launch(path: Path) -> bool:
        opened.append(path)
        return True

    monkeypatch.setattr("highway_drainage.presentation.main_window.open_google_earth", launch)
    window = MainWindow(earth_writer=KmzPreviewWriter())
    qtbot.addWidget(window)
    window._show_crossings(candidates((101, 199)))
    before = window.status.text()
    window.preview.earth_button.click()
    qtbot.waitUntil(lambda: window._thread is None, timeout=10000)
    assert bool(warnings) == (choice != "cancel_save")
    if choice == "replace":
        assert opened == [destination]
        with ZipFile(destination) as archive:
            assert "doc.kml" in archive.namelist()
    else:
        assert not opened
        assert destination.read_bytes() == b"previous export"
        assert window.status.text() == before
    window.close()
