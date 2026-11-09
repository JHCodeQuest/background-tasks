import os
import re
import stat
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QThread

from models import SearchOptions


class LogWorker(QObject):
    progress = Signal(int)
    progress_range = Signal(int, int)
    match_found = Signal(str)
    skipped = Signal(str, bool)
    finished = Signal(str)
    error = Signal(str)

    @Slot(object)
    def scan(self, options: SearchOptions) -> None:
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
                    self.skipped.emit(f"Skipped folder {error.filename}: {error.strerror}", True)

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
                            self.skipped.emit(f"Skipped file {file_path}: {e}", False)
                    if self._should_stop():
                        break
            else:
                raise ValueError("Select a regular file or folder.")
            completed = True
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if completed:
                outcome = "Cancelled" if self._should_stop() else "Finished"
            self.progress_range.emit(0, 100)
            if directory:
                self.progress.emit(0)
            if outcome == "Finished":
                self.progress.emit(100)
            self.finished.emit(outcome)

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
                    pct = int(f.tell() / size * 100)
                    self.progress.emit(min(pct, 100))

                text = line if options.case_sensitive else line.lower()
                hit = bool(regex.search(line)) if options.use_regex else (pattern in text)
                if hit:
                    out = f"{file_path}:{line_number}: {line.rstrip()}"
                    self.match_found.emit(out)
