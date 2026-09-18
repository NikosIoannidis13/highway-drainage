"""Qt-visible task progress with a tqdm console companion; no fake percentages."""

import re
import sys
from time import monotonic
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget
from tqdm import tqdm


class TaskProgress(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.elapsed = QLabel()
        layout.addWidget(self.bar)
        layout.addWidget(self.elapsed)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.console: tqdm[object] | None = None
        self.started = 0.0
        self.stage = ""
        self.hide()

    def begin(self, message: str) -> None:
        self.started = monotonic()
        self.bar.setRange(0, 0)
        self.stage = message
        self.console = cast(
            "tqdm[object]",
            tqdm(
                total=None,
                desc=message[:60],
                unit="steps",
                disable=sys.stderr is None or not sys.stderr.isatty(),
            ),
        )
        self.show()
        self.timer.start()
        self._tick()

    def update_message(self, message: str) -> None:
        self.stage = message
        match = re.search(r"([\d,]+)/([\d,]+)", message)
        if match:
            done, total = (int(v.replace(",", "")) for v in match.groups())
            if total > 0:
                self.bar.setRange(0, 1000)
                self.bar.setValue(min(1000, done * 1000 // total))
                self.bar.setFormat(f"{done:,} / {total:,} (%p%)")
                if self.console is not None:
                    self.console.total = total
                    self.console.n = done
                    self.console.refresh()
        else:
            self.bar.setRange(0, 0)
            if self.console is not None:
                self.console.total = None
                self.console.n = 0
        if self.console is not None:
            self.console.set_description(message[:60])
        self._tick()

    def _tick(self) -> None:
        seconds = int(monotonic() - self.started)
        self.elapsed.setText(f"Elapsed: {seconds // 60:02d}:{seconds % 60:02d} — {self.stage}")
        self.elapsed.setWordWrap(True)

    def finish(self, failed: bool = False) -> None:
        self.timer.stop()
        if self.console is not None:
            self.console.close()
            self.console = None
        self.bar.setRange(0, 100)
        self.bar.setValue(0 if failed else 100)
        self.bar.setFormat("Stopped — see diagnostic" if failed else "Complete")
        self.stage = "Stopped — see diagnostic" if failed else "Complete"
        self._tick()
