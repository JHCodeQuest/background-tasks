import re
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QThread

from models import SearchOptions


class LogWorker(QObject):
    progress = Signal(int)
    match_found = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self._cancel_requested = False

    @Slot()
    def cancel(self):
        self._cancel_requested = True
        thread = self.thread()
        if thread is not None:
            thread.requestInterruption()

    @Slot(object)
    def scan(self,options: SearchOptions):
        try:
            self._cancel_requested = False
            path_obj = Path(options.path)

            if path_obj.is_file():
                self._scan_file(path_obj, options)
            else:
                files = [p for p in path_obj.rglob("*") if p.is_file()]
                total = len(files) or 1
                for i, file_path in enumerate(files, start=1):
                    if self._should_stop():
                        break
                    self._scan_file(file_path, options, prefix=str(file_path), emit_progress=False)
                    self.progress.emit(int(i / total * 100))

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def _should_stop(self):
        return self._cancel_requested or QThread.currentThread().isInterruptionRequested()

    def _scan_file(self, file_path, options: SearchOptions, prefix="", emit_progress=True):
        size = file_path.stat().st_size or 1
        flags = 0 if options.case_sensitive else re.IGNORECASE
        regex = re.compile(options.pattern, flags)

        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            while not self._should_stop():
                line = f.readline()
                if not line:
                    break

                if emit_progress:
                    pct = int(f.tell() / size * 100)
                    self.progress.emit(min(pct, 100))

                text = line if options.case_sensitive else line.lower()
                hit = bool(regex.search(line)) if options.use_regex else (options.pattern.lower() in text)
                if hit:
                    out = f"{prefix}: {line.rstrip()}" if prefix else line.rstrip()
                    self.match_found.emit(out)