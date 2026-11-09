import csv
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import TextIO

from PySide6.QtCore import QObject, QThread, Signal, Slot


def _safe_csv_cell(value: str | int | None) -> str | int | None:
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n"))
        or re.match(r"^[\s\ufeff]*[=+\-@]", value)
    ):
        return "'" + value
    return value


class ExportWorker(QObject):
    committing = Signal()
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, source: TextIO):
        super().__init__()
        self._source = source
        self._cancel_requested = threading.Event()
        self._commit_lock = threading.Lock()
        self._committing = False

    def cancel(self) -> bool:
        with self._commit_lock:
            if self._committing:
                return False
            self._cancel_requested.set()
            return True

    @Slot(str)
    def export(self, destination: str) -> None:
        temporary_path = None
        outcome = "Failed"
        try:
            if self._should_stop():
                outcome = "Cancelled"
                return
            target = Path(destination)
            suffix = target.suffix.lower()
            if not destination or suffix not in (".txt", ".csv"):
                raise ValueError("Choose a filename ending in .txt or .csv.")
            self._source.seek(0)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8-sig" if suffix == ".csv" else "utf-8",
                newline="", dir=target.parent, prefix=".log-analyser-", suffix=".tmp", delete=False,
            ) as output:
                temporary_path = Path(output.name)
                writer = csv.writer(output) if suffix == ".csv" else None
                if writer is not None:
                    writer.writerow(("type", "path", "line", "text"))
                for record in self._source:
                    if self._should_stop():
                        outcome = "Cancelled"
                        return
                    kind, path, line, text = json.loads(record)
                    if writer is not None:
                        writer.writerow(_safe_csv_cell(value) for value in (kind, path, line, text))
                    else:
                        output.write(f"{path}:{line}: {text}\n" if kind == "match" else text + "\n")
                output.flush()
                os.fsync(output.fileno())
            with self._commit_lock:
                if self._should_stop():
                    outcome = "Cancelled"
                    return
                self._committing = True
            self.committing.emit()
            os.replace(temporary_path, target)
            temporary_path = None
            outcome = "Finished"
        except Exception as e:
            outcome = "Failed"
            self.error.emit(f"Could not save export: {e}")
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as e:
                    outcome = "Failed"
                    self.error.emit(f"Could not remove temporary export file {temporary_path}: {e}")
            self.finished.emit(outcome)

    def _should_stop(self) -> bool:
        return self._cancel_requested.is_set() or QThread.currentThread().isInterruptionRequested()
