import os
import re
import stat
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QThread

from models import SearchOptions


# shortcut: row limits do not bound line length, use chunked reads if giant log lines are expected.
MAX_DISPLAY_ROWS = 5_000
RESULT_BATCH_SIZE = 200
RESULT_UPDATE_INTERVAL = 0.1


class LogWorker(QObject):
    progress = Signal(int)
    progress_range = Signal(int, int)
    results_ready = Signal(list, object)
    finished = Signal(str)
    error = Signal(str)

    @Slot(object)
    def scan(self, options: SearchOptions) -> None:
        self._pending_results = []
        self._match_count = 0
        self._skipped_file_count = 0
        self._skipped_folder_count = 0
        self._reported_count = 0
        self._last_result_update = time.monotonic()
        completed = False
        directory = False
        outcome = "Failed"
        self.progress_range.emit(0, 100)
        self.progress.emit(0)
        try:
            if self._should_stop():
                outcome = "Cancelled"
                return
            if not options.path:
                raise ValueError("Select a file or folder first.")
            path_obj = Path(options.path)
            mode = path_obj.stat().st_mode

            if stat.S_ISREG(mode):
                self._scan_file(path_obj, options)
            elif stat.S_ISDIR(mode):
                directory = True
                self.progress_range.emit(0, 0)

                def on_walk_error(error: OSError) -> None:
                    if Path(error.filename or path_obj) == path_obj:
                        raise error
                    self._skipped_folder_count += 1
                    self._queue_result(f"Skipped folder {error.filename}: {error.strerror}")

                for root, _, filenames in os.walk(path_obj, onerror=on_walk_error):
                    if self._should_stop():
                        break
                    for name in filenames:
                        if self._should_stop():
                            break
                        file_path = Path(root) / name
                        try:
                            self._scan_file(file_path, options, emit_progress=False)
                        except (OSError, UnicodeError, ValueError) as e:
                            self._skipped_file_count += 1
                            self._queue_result(f"Skipped file {file_path}: {e}")
                    if self._should_stop():
                        break
            else:
                raise ValueError("Select a regular file or folder.")
            completed = True
        except Exception as e:
            self._flush_results(force=True)
            self.error.emit(str(e))
        finally:
            self._flush_results(force=True)
            if completed:
                outcome = "Cancelled" if self._should_stop() else "Finished"
            self.progress_range.emit(0, 100)
            if directory:
                self.progress.emit(0)
            if outcome == "Finished":
                self.progress.emit(100)
            self.finished.emit(outcome)

    def _queue_result(self, message: str) -> None:
        total = self._match_count + self._skipped_file_count + self._skipped_folder_count
        if total <= MAX_DISPLAY_ROWS:
            self._pending_results.append(message)
        self._flush_results()

    def _flush_results(self, force: bool = False) -> None:
        total = self._match_count + self._skipped_file_count + self._skipped_folder_count
        if total == self._reported_count:
            return
        now = time.monotonic()
        if (
            not force
            and len(self._pending_results) < RESULT_BATCH_SIZE
            and now - self._last_result_update < RESULT_UPDATE_INTERVAL
        ):
            return
        rows = self._pending_results
        self._pending_results = []
        self._reported_count = total
        self._last_result_update = now
        self.results_ready.emit(rows, (self._match_count, self._skipped_file_count, self._skipped_folder_count))

    def _should_stop(self):
        return QThread.currentThread().isInterruptionRequested()

    def _scan_file(self, file_path: Path, options: SearchOptions, emit_progress: bool = True) -> None:
        info = file_path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file.")
        size = info.st_size or 1
        flags = 0 if options.case_sensitive else re.IGNORECASE
        regex = re.compile(options.pattern, flags) if options.use_regex else None
        pattern = options.pattern if options.case_sensitive else options.pattern.lower()
        line_number = 0
        last_progress = 0

        with file_path.open("r", encoding="utf-8-sig", errors="strict") as f:
            while not self._should_stop():
                line = f.readline()
                if not line:
                    break
                line_number += 1

                # shortcut: NUL detection only, use format detection if broader binary filtering is needed.
                if "\x00" in line:
                    raise ValueError("Binary data (NUL byte) is not supported.")

                if emit_progress:
                    pct = min(int(f.tell() / size * 100), 100)
                    if pct != last_progress:
                        self.progress.emit(pct)
                        last_progress = pct

                text = line if options.case_sensitive else line.lower()
                hit = bool(regex.search(line)) if options.use_regex else (pattern in text)
                if hit:
                    self._match_count += 1
                    out = f"{file_path}:{line_number}: {line.rstrip()}"
                    self._queue_result(out)
                else:
                    self._flush_results()
