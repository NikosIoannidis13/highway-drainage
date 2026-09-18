"""Qt bridge; numerical hydrology stays inside the use-case adapter."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.hydrology import DelineateCatchments
from highway_drainage.application.preview import PreviewReader
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import HydrologyRequest


class HydrologyWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    finished = Signal()
    boundaries_ready = Signal(object)

    def __init__(
        self,
        service: DelineateCatchments,
        request: HydrologyRequest,
        cancel: Event,
        previews: PreviewReader | None = None,
    ) -> None:
        super().__init__()
        self._service, self._request, self._cancel = service, request, cancel
        self._previews = previews

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.execute(
                self._request, self._cancel, lambda message: self.progress.emit(message)
            )
            self.succeeded.emit(result)
            if self._previews is not None and not self._cancel.is_set():
                try:
                    self.boundaries_ready.emit(self._previews.boundaries(result, self._cancel))
                except ImportCancelled:
                    pass
                except Exception as exc:
                    self.progress.emit(f"Catchment preview unavailable: {exc}")
        except ImportCancelled:
            self.failed.emit("Hydrology cancelled; no completed output published.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
