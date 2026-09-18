"""Exercise Chromium and JavaScript offline in an isolated Qt process."""

import os
import subprocess
import sys
from pathlib import Path


def test_embedded_browser_loads_local_page_and_receives_layers(tmp_path: Path) -> None:
    script = r"""
import sys
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication
from highway_drainage.presentation import satellite_view as module

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication([])
original_server = module.MapPageServer
def offline_server(key):
    server = original_server(key, port=0)
    # Keep the real application JavaScript, remove the remote Google loader.
    server.page = server.page.split(b'<script async src=')[0] + b'</body></html>'
    return server
module.MapPageServer = offline_server
module.QSettings = lambda *a: QSettings(sys.argv[1], QSettings.Format.IniFormat)
view = module.SatelliteView()
view.key.setText('offline-test-key')
view._payload = ('{"view":"drainage","geojson":{"type":"FeatureCollection","features":[]},'
                 '"image":null,"bounds":null,"diagnostic":""}')
view.connect_map()
assert view.web is not None
def loaded(ok):
    if not ok:
        app.exit(2)
        return
    view.web.page().runJavaScript('payload && payload.view', checked)
def checked(value):
    if value != 'drainage':
        app.exit(3)
        return
    print('OFFLINE_MAP_OK')
    view.shutdown()
    app.quit()
view.web.loadFinished.connect(loaded)
QTimer.singleShot(15000, lambda: app.exit(4))
sys.exit(app.exec())
"""
    environment = dict(os.environ)
    environment["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "settings.ini")],
        capture_output=True,
        text=True,
        timeout=25,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OFFLINE_MAP_OK" in result.stdout
