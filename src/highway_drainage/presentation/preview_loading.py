"""Worker-side display loading. A preview failure never invalidates engineering output."""

from collections.abc import Callable
from pathlib import Path
from threading import Event

from highway_drainage.application.preview import PreviewReader
from highway_drainage.application.terrain import ImportCancelled


def load_raster_preview(
    reader: PreviewReader | None,
    path: Path,
    cancel: Event,
    ready: Callable[[object], None],
    warning: Callable[[str], None],
) -> None:
    if reader is None or cancel.is_set():
        return
    try:
        ready(reader.raster(path, cancel))
    except ImportCancelled:
        pass
    except Exception as exc:
        warning(f"DEM preview unavailable: {exc}")
