"""Qt bridge for the UI-independent terrain use case."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.terrain import ImportCancelled, ImportTerrain
from highway_drainage.domain.terrain import TerrainRequest


class TerrainWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    progress = Signal(str)

    def __init__(self, use_case: ImportTerrain, request: TerrainRequest, cancel: Event) -> None:
        super().__init__()
        self._use_case = use_case
        self._request = request
        self._cancel = cancel

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(
                self._use_case.execute(self._request, self._cancel, self.progress.emit)
            )
        except ImportCancelled:
            self.failed.emit("Import cancelled.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
