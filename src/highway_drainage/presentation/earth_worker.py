"""Export an immutable Google Earth snapshot without blocking the GUI."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.earth_preview import EarthPreview, EarthPreviewWriter
from highway_drainage.application.terrain import ImportCancelled


class EarthPreviewWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        writer: EarthPreviewWriter,
        preview: EarthPreview,
        path: Path,
        cancel: Event,
        *,
        overwrite: bool = False,
    ) -> None:
        super().__init__()
        self.writer, self.preview, self.path, self.cancel = writer, preview, path, cancel
        self.overwrite = overwrite

    @Slot()
    def run(self) -> None:
        try:
            self.writer.write(self.preview, self.path, self.cancel, overwrite=self.overwrite)
            self.succeeded.emit(self.path)
        except ImportCancelled:
            self.failed.emit("Google Earth preview cancelled.")
        except Exception as exc:
            self.failed.emit(f"Google Earth preview unavailable: {exc}")
        finally:
            self.finished.emit()
