"""Run flow-grid generation without blocking outlet-selection controls."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.hydrology import GenerateFlow
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import FlowRequest


class FlowWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    finished = Signal()

    def __init__(self, service: GenerateFlow, request: FlowRequest, cancel: Event) -> None:
        super().__init__()
        self._service, self._request, self._cancel = service, request, cancel

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._service.execute(
                self._request, self._cancel, lambda message: self.progress.emit(message)
            ))
        except ImportCancelled:
            self.failed.emit("Flow generation cancelled; no completed output published.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
