"""Combine raster inputs off the GUI thread."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from highway_drainage.application.project_raster import ProjectRasterReader
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.infrastructure.combine_rasters import combine_rasters


class CombineWorker(QObject):
    succeeded = Signal(object)
    progress = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        primary: Path,
        filler: Path,
        output: Path,
        cancel: Event,
        reader: ProjectRasterReader | None,
        overwrite: bool,
    ) -> None:
        super().__init__()
        self.primary, self.filler, self.output = primary, filler, output
        self.cancel, self.reader, self.overwrite = cancel, reader, overwrite

    @Slot()
    def run(self) -> None:
        try:
            kept, filled = combine_rasters(
                self.primary,
                self.filler,
                self.output,
                self.cancel,
                self.progress.emit,
                overwrite=self.overwrite,
            )
            self.progress.emit(
                f"Saved {self.output}: {kept:,} primary cells, {filled:,} gap-filler cells."
            )
            if self.reader is not None:
                self.succeeded.emit(self.reader.read(self.output, self.cancel))
        except ImportCancelled:
            self.failed.emit("Combination cancelled. Any completed output is retained.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
