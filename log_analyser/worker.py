import json
import os
import re
import stat
import time
from pathlib import Path
from typing import TextIO

from PySide6.QtCore import QObject, Signal, Slot, QThread

from models import SearchOptions


# shortcut: row limits do not bound line length, use chunked reads if giant log lines are expected.
MAX_DISPLAY_ROWS = 5_000
RESULT_BATCH_SIZE = 200
RESULT_UPDATE_INTERVAL = 0.1


class ResultStorageError(Exception):
    """Keep snapshot write failures out of the unreadable-input skip handler."""


class LogWorker(QObject):
    progress = Signal(int)
    progress_range = Signal(int, int)
    results_ready = Signal(list, object)
    export_ready = Signal()
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, export_file: TextIO | None = None):
        super().__init__()
        self._export_file = export_file

    @Slot(object)
    def scan(self, options: SearchOptions) -> None:
        self._pending_results = []
        self._match_count = 0
        self._skipped_file_count = 0
        self._skipped_folder_count = 0
        self._reported_count = 0
        self._last_result_update = time.monotonic()
        self._export_failed = False
        error_message = None
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
                    message = f"Skipped folder {error.filename}: {error.strerror}"
                    self._queue_result(message)
                    self._write_export_row("skipped_folder", error.filename or "", None, message)

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
                            message = f"Skipped file {file_path}: {e}"
                            self._queue_result(message)
                            self._write_export_row("skipped_file", str(file_path), None, message)
                    if self._should_stop():
                        break
            else:
                raise ValueError("Select a regular file or folder.")
            completed = True
        except Exception as e:
            self._flush_results(force=True)
            error_message = str(e)
            self.error.emit(error_message)
        finally:
            self._flush_results(force=True)
            if completed:
                outcome = "Cancelled" if self._should_stop() else "Finished"
            if self._export_file is not None and not self._export_failed:
                try:
                    if error_message is not None:
                        self._write_export_row("error", options.path, None, f"Error: {error_message}")
                    summary = (
                        f"{outcome} | Matches: {self._match_count} | "
                        f"Skipped files: {self._skipped_file_count} | Skipped folders: {self._skipped_folder_count}"
                    )
                    self._write_export_row("summary", options.path, None, summary)
                    self._export_file.flush()
                    self._export_file.seek(0)
                except (ResultStorageError, OSError, UnicodeError, ValueError) as e:
                    outcome = "Failed"
                    self._export_failed = True
                    message = str(e) if isinstance(e, ResultStorageError) else f"Could not retain results for export: {e}"
                    self.error.emit(message)
                else:
                    self.export_ready.emit()
            self.progress_range.emit(0, 100)
            if directory:
                self.progress.emit(0)
            if outcome == "Finished":
                self.progress.emit(100)
            self.finished.emit(outcome)

    def _write_export_row(self, kind: str, path: str, line: int | None, text: str) -> None:
        if self._export_file is None:
            return
        try:
            record = json.dumps([kind, path, line, text]) + "\n"
            if self._export_file.write(record) != len(record):
                raise OSError("Incomplete temporary result write.")
        except (OSError, UnicodeError, ValueError) as e:
            self._export_failed = True
            raise ResultStorageError(f"Could not retain results for export: {e}") from e

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
        if self._export_file is not None and os.path.samestat(info, os.fstat(self._export_file.fileno())):
            return
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
                    self._write_export_row("match", str(file_path), line_number, line.rstrip("\r\n"))
                else:
                    self._flush_results()
