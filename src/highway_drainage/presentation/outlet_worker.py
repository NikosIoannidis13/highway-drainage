"""Qt bridge for outlet selection."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.outlets import SelectOutlets
from highway_drainage.application.preview import PreviewReader
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.outlets import SnapRequest
from highway_drainage.presentation.preview_loading import load_raster_preview


class OutletWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    raster_ready = Signal(object)
    progress = Signal(str)

    def __init__(
        self,
        service: SelectOutlets,
        request: SnapRequest,
        cancel: Event,
        previews: PreviewReader | None = None,
    ) -> None:
        super().__init__()
        self._service, self._request, self._cancel = service, request, cancel
        self._previews = previews

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._service.execute(self._request, self._cancel))
            load_raster_preview(
                self._previews,
                self._request.coordinates.dem,
                self._cancel,
                self.raster_ready.emit,
                self.progress.emit,
            )
        except ImportCancelled:
            self.failed.emit("Outlet selection cancelled.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
