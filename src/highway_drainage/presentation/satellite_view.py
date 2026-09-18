"""Optional embedded Google map, with local key setup and asynchronous overlays."""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import Event, Thread
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from highway_drainage.application.satellite import SatelliteBuilder, SatelliteRequest
from highway_drainage.application.terrain import ImportCancelled

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView


class _MapWorker(QObject):
    ready = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, builder: SatelliteBuilder, request: SatelliteRequest, cancel: Event) -> None:
        super().__init__()
        self.builder, self.request, self.cancel = builder, request, cancel

    @Slot()
    def run(self) -> None:
        try:
            self.ready.emit(self.builder.build(self.request, self.cancel))
        except ImportCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class MapPageServer:
    """Serve only an in-memory map page, on a fixed loopback origin for key restrictions."""

    def __init__(self, key: str, port: int = 8765) -> None:
        self.page = (
            files("highway_drainage.presentation")
            .joinpath("satellite.html")
            .read_text(encoding="utf-8")
            .replace("__API_KEY__", quote(key, safe=""))
            .encode("utf-8")
        )
        path = f"/{uuid4().hex}/map"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != path:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(owner.page)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
                self.end_headers()
                self.wfile.write(owner.page)

            def log_message(self, format: str, *args: object) -> None:
                pass  # Never log the page or API key.

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}{path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class SatelliteView(QWidget):
    idle = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.builder: SatelliteBuilder | None = None
        self._pending: SatelliteRequest | None = None
        self._current: SatelliteRequest | None = None
        self._thread: QThread | None = None
        self._worker: _MapWorker | None = None
        self._cancel = Event()
        self._server: MapPageServer | None = None
        self._payload = "null"
        self._closing = False
        # WebEngine is loaded only after Connect, so offline engineering startup stays light.
        self.web: QWebEngineView | None = None
        self.layout_ = QVBoxLayout(self)
        note = QLabel(
            "Google satellite imagery requires internet and your Maps JavaScript API key "
            "with billing enabled. Enter the key here, then Connect."
        )
        note.setWordWrap(True)
        self.layout_.addWidget(note)
        row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Google Maps JavaScript API key")
        self.key.setText(os.environ.get("HIGHWAY_DRAINAGE_GOOGLE_MAPS_KEY", ""))
        row.addWidget(self.key)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_map)
        row.addWidget(self.connect_button)
        self.layout_.addLayout(row)
        self.remember = QCheckBox("Remember key on this computer")
        self.layout_.addWidget(self.remember)
        saved = str(QSettings("HighwayDrainage", "Satellite").value("api_key", ""))
        if saved and not self.key.text():
            self.key.setText(saved)
            self.remember.setChecked(True)
        help_label = QLabel(
            '<a href="https://developers.google.com/maps/documentation/javascript/get-api-key">'
            "Google API key setup</a><br>Website restriction: http://127.0.0.1:8765/*"
        )
        help_label.setOpenExternalLinks(True)
        self.layout_.addWidget(help_label)
        self.message = QLabel(
            "Satellite imagery is optional; ordinary previews work without a key."
        )
        self.message.setWordWrap(True)
        self.layout_.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.layout_.addWidget(self.progress)

    @Slot()
    def connect_map(self) -> None:
        key = self.key.text().strip()
        if not key:
            self.message.setText(
                "Enter a Google Maps JavaScript API key to load satellite imagery."
            )
            return
        try:
            if self._server is not None:
                self._server.close()
                self._server = None
            self._server = MapPageServer(key)
            if self.web is None:
                from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
                from PySide6.QtWebEngineWidgets import QWebEngineView

                self.web = QWebEngineView(self)
                # An unnamed profile is off-the-record (no persistent imagery cache).
                profile = QWebEngineProfile(self)
                self.web.setPage(QWebEnginePage(profile, self.web))
                self.web.loadFinished.connect(self._loaded)
                self.layout_.addWidget(self.web, 1)
            self.web.setUrl(QUrl(self._server.url))
            settings = QSettings("HighwayDrainage", "Satellite")
            if self.remember.isChecked():
                settings.setValue("api_key", key)
            else:
                settings.remove("api_key")
            self.message.setText("Connecting to Google satellite imagery...")
        except (OSError, ImportError) as exc:
            self.message.setText(
                f"Cannot start the satellite preview: {type(exc).__name__}. "
                "Check that another app instance is not using port 8765."
            )

    @Slot(bool)
    def _loaded(self, success: bool) -> None:
        if success and self.web is not None:
            self.web.page().runJavaScript(
                f"window.updatePreview && window.updatePreview({self._payload});"
            )
            self.message.setText(
                "Use the wheel to zoom and drag to pan. Google attribution remains visible."
            )
        elif not success:
            self.message.setText(
                "The satellite page could not load. Check your internet connection."
            )

    def submit(self, request: SatelliteRequest) -> None:
        if self._closing:
            return
        self._current = request
        self._pending = request
        self._cancel.set()
        if self._thread is None:
            QTimer.singleShot(0, self._start_pending)

    @Slot()
    def _start_pending(self) -> None:
        if (
            self._closing
            or self._thread is not None
            or self._pending is None
            or self.builder is None
        ):
            return
        request, self._pending = self._pending, None
        self._cancel = Event()
        thread = QThread(self)
        worker = _MapWorker(self.builder, request, self._cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.ready.connect(self._show_payload)
        worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self.progress.show()
        self.message.setText("Preparing existing layers for satellite preview...")
        thread.start()

    @Slot(str)
    def _show_payload(self, payload: str) -> None:
        if self._cancel.is_set() or self._closing:
            return
        self._payload = payload
        if self.web is not None:
            self.web.page().runJavaScript(
                f"window.updatePreview && window.updatePreview({payload});"
            )
        self.message.setText(
            "Project layers updated."
            if self.web is not None
            else "Project layers ready. Connect to load Google satellite imagery."
        )

    @Slot(str)
    def _failed(self, message: str) -> None:
        if not self._cancel.is_set():
            self.clear_layers()
            self.message.setText(f"Satellite overlay unavailable: {message}")

    def clear_layers(self) -> None:
        self._cancel.set()
        self._pending = None
        self._payload = (
            '{"view":"empty","geojson":{"type":"FeatureCollection","features":[]},'
            '"image":null,"bounds":null,"diagnostic":""}'
        )
        if self.web is not None:
            self.web.page().runJavaScript(
                f"window.updatePreview && window.updatePreview({self._payload});"
            )

    @Slot()
    def _finished(self) -> None:
        self._thread, self._worker = None, None
        self.progress.hide()
        if self._pending is not None and not self._closing:
            self._start_pending()
        self.idle.emit()

    def fit_data(self) -> None:
        if self.web is not None:
            self.web.page().runJavaScript("window.fitPreview && window.fitPreview();")

    def reset_view(self) -> None:
        if self.web is not None:
            self.web.page().runJavaScript("window.resetPreview && window.resetPreview();")

    def shutdown(self) -> bool:
        self._closing = True
        self._pending = None
        self._cancel.set()
        if self._thread is not None:
            return False
        if self._server is not None:
            self._server.close()
            self._server = None
        return True
