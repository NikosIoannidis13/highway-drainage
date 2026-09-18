"""Run the DEM use case without blocking the GUI."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.dem import GenerateDem
from highway_drainage.application.preview import PreviewReader
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import DemRequest
from highway_drainage.presentation.preview_loading import load_raster_preview


class DemWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    finished = Signal()
    raster_ready = Signal(object)

    def __init__(
        self,
        use_case: GenerateDem,
        request: DemRequest,
        cancel: Event,
        previews: PreviewReader | None = None,
    ) -> None:
        super().__init__()
        self._use_case, self._request, self._cancel = use_case, request, cancel
        self._previews = previews

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(
                self._use_case.execute(
                    self._request, self._cancel, lambda message: self.progress.emit(message)
                )
            )
            load_raster_preview(
                self._previews,
                self._request.output,
                self._cancel,
                self.raster_ready.emit,
                self.progress.emit,
            )
        except ImportCancelled:
            self.failed.emit("DEM generation cancelled. No partial output was published.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
