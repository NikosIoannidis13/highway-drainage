"""Keep Shapefile writing off the GUI thread."""

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.point_export import (
    DirectLineExport,
    LineExport,
    PointExport,
    PointWriter,
)
from highway_drainage.application.terrain import ImportCancelled


class PointExportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        writer: PointWriter,
        request: PointExport | LineExport | DirectLineExport,
        cancel: Event,
    ) -> None:
        super().__init__()
        self._writer, self._request, self._cancel = writer, request, cancel

    @Slot()
    def run(self) -> None:
        try:
            result = self._writer.write(self._request, self._cancel)
            self.succeeded.emit(result)
        except ImportCancelled:
            self.failed.emit("Shapefile export cancelled.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
