from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot


class BackgroundSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    done = pyqtSignal()


class BackgroundCall(QRunnable):
    def __init__(self, function: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = BackgroundSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass
        else:
            try:
                self.signals.succeeded.emit(result)
            except RuntimeError:
                pass
        finally:
            try:
                self.signals.done.emit()
            except RuntimeError:
                pass
