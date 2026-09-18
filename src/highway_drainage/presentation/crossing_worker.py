"""Qt bridge for the independent crossing use case."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.crossings import FindCrossings
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import CrossingRequest


class CrossingWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    finished = Signal()

    def __init__(self, use_case: FindCrossings, request: CrossingRequest, cancel: Event) -> None:
        super().__init__()
        self._use_case, self._request, self._cancel = use_case, request, cancel

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(
                self._use_case.execute(
                    self._request, self._cancel, lambda message: self.progress.emit(message)
                )
            )
        except ImportCancelled:
            self.failed.emit("Crossing extraction cancelled.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
