"""Open the local KMZ in Google Earth Pro, or use the system file association."""

import os
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices


def open_google_earth(path: Path) -> bool:
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            executable = Path(root) / "Google/Google Earth Pro/client/googleearth.exe"
            if executable.is_file():
                started, _ = QProcess.startDetached(str(executable), [str(path)])
                return started
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
