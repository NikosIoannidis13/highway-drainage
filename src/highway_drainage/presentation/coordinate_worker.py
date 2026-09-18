"""Qt bridge for read-only raster inspection."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.application.preview import PreviewReader
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import CoordinateRequest
from highway_drainage.presentation.preview_loading import load_raster_preview


class CoordinateWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    raster_ready = Signal(object)
    progress = Signal(str)

    def __init__(
        self,
        use_case: ValidateCoordinates,
        request: CoordinateRequest,
        cancel: Event,
        previews: PreviewReader | None = None,
    ) -> None:
        super().__init__()
        self._use_case, self._request, self._cancel = use_case, request, cancel
        self._previews = previews

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._use_case.execute(self._request, self._cancel))
            load_raster_preview(
                self._previews,
                self._request.dem,
                self._cancel,
                self.raster_ready.emit,
                self.progress.emit,
            )
        except ImportCancelled:
            self.failed.emit("Coordinate validation cancelled.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
