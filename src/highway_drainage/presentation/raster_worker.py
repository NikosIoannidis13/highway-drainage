"""Load an external project raster without blocking the Qt event loop."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.project_raster import ProjectRasterReader
from highway_drainage.application.terrain import ImportCancelled


class RasterWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, reader: ProjectRasterReader, path: Path, cancel: Event) -> None:
        super().__init__()
        self.reader, self.path, self.cancel = reader, path, cancel

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.reader.read(self.path, self.cancel))
        except ImportCancelled:
            self.failed.emit("Raster loading cancelled.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
