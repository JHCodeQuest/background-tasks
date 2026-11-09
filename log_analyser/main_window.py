from pathlib import Path
import re
import tempfile

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QListWidget, QProgressBar, QFileDialog,
    QLabel, QCheckBox
)
from worker import LogWorker, MAX_DISPLAY_ROWS
from models import SearchOptions
from result_export import ExportWorker


class MainWindow(QWidget):
    scan_requested = Signal(object)
    export_requested = Signal(str)

    def __init__(self):
        super().__init__()
        # window
        self.setWindowTitle("Log Analyser")
        self.selected_path = ""
        self.thread = None
        self.worker = None
        self._close_requested = False
        self._scan_status = "Ready"
        self._match_count = 0
        self._skipped_file_count = 0
        self._skipped_folder_count = 0
        self._export_source = None
        self._export_ready = False
        self._exporting = False
        self._export_status = ""
        self._export_destination = ""
        
        # label
        self.path_label = QLabel("No file or folder selected")
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setWordWrap(True)
        self.pattern_input = QLineEdit()
        self.pattern_input.setPlaceholderText("Enter keyword or regex")
        self.pattern_input.setAccessibleName("Search term")
        self.search_label = QLabel("S&earch:")
        self.search_label.setBuddy(self.pattern_input)

        # check box
        self.regex_checkbox = QCheckBox("&Regex")
        self.case_checkbox = QCheckBox("Case sensi&tive")
        
        #buttons
        self.pick_file_button = QPushButton("Choose &File")
        self.pick_folder_button = QPushButton("Choose F&older")
        self.start_button = QPushButton("&Start")
        self.cancel_button = QPushButton("&Cancel")
        self.cancel_button.setEnabled(False)
        self.export_button = QPushButton("E&xport...")
        self.export_button.setEnabled(False)
        self.export_button.setToolTip("Export the full scan, including results beyond the display limit.")

        # progress bar
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setAccessibleName("Scan progress")
        self.results = QListWidget()
        self.results.setAccessibleName("Search results")
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setAccessibleName("Scan status")
        self.status_label.setWordWrap(True)
        self._update_status()

        # widget
        top = QHBoxLayout()
        top.addWidget(self.pick_file_button)
        top.addWidget(self.pick_folder_button)
        top.addWidget(self.start_button)
        top.addWidget(self.cancel_button)
        top.addWidget(self.export_button)

        # options
        opts = QHBoxLayout()
        opts.addWidget(self.regex_checkbox)
        opts.addWidget(self.case_checkbox)

        # layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.path_label)
        layout.addWidget(self.search_label)
        layout.addWidget(self.pattern_input)
        layout.addLayout(opts)
        layout.addLayout(top)
        layout.addWidget(self.progress)
        layout.addWidget(self.results)
        layout.addWidget(self.status_label)

        # button connect
        self.pick_file_button.clicked.connect(self.pick_file)
        self.pick_folder_button.clicked.connect(self.pick_folder)
        self.start_button.clicked.connect(self.start_scan)
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.export_button.clicked.connect(self.export_results)

    def pick_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Log File",
            "",
            "Log Files(*.log *.txt);;All Files (*)"
        )

        if file_path:
            self.selected_path = file_path
            self.path_label.setText(file_path)

    def pick_folder(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Choose Folder"
        )

        if folder_path:
            self.selected_path = folder_path
            self.path_label.setText(folder_path)

    def start_scan(self):
        if self.thread is not None:
            return

        if not self.selected_path:
            self.results.addItem("Select a file or folder first.")
            return
        
        pattern = self.pattern_input.text().strip()
        if not pattern:
            self.results.addItem("Enter a search term.")
            return

        if self.regex_checkbox.isChecked():
            try:
                flags = 0 if self.case_checkbox.isChecked() else re.IGNORECASE
                re.compile(pattern, flags)
            except re.error as e:
                self.results.addItem(f"Invalid regular expression: {e}")
                return
        
        try:
            snapshot = tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="")
        except OSError as e:
            self._export_status = f"Cannot start scan: could not create temporary result storage ({e})."
            self._update_status()
            return
        self._close_export_source()
        self._export_source = snapshot
        self._export_status = ""
        self.results.clear()
        self._match_count = 0
        self._skipped_file_count = 0
        self._skipped_folder_count = 0
        self._scan_status = "Scanning"
        self._update_status()
        self.progress.setValue(0)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        options = SearchOptions(
            path = self.selected_path,
            pattern=pattern,
            use_regex=self.regex_checkbox.isChecked(),
            case_sensitive=self.case_checkbox.isChecked(),
        )

        self.thread = QThread()
        self.worker = LogWorker(self._export_source)
        self.worker.moveToThread(self.thread)

        self.scan_requested.connect(self.worker.scan, Qt.ConnectionType.QueuedConnection)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.progress_range.connect(self.progress.setRange)
        self.worker.results_ready.connect(self.on_results)
        self.worker.export_ready.connect(self.on_export_ready)
        self.worker.error.connect(self.on_error)
        self.worker.finished.connect(self.on_scan_finished)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.on_finished)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()
        self.scan_requested.emit(options)
    
    def cancel_scan(self):
        if self.thread is not None:
            self.cancel_button.setEnabled(False)
            if self._exporting:
                if not self.worker.cancel():
                    self.on_export_committing()
                    return
                self._export_status = "Cancelling export..."
                self._update_status()
            else:
                self.thread.requestInterruption()
                if self._scan_status == "Scanning":
                    self._scan_status = "Cancelling"
                    self._update_status()

    @Slot(list, object)
    def on_results(self, rows: list[str], counts: tuple[int, int, int]) -> None:
        self.results.addItems(rows)
        self._match_count, self._skipped_file_count, self._skipped_folder_count = counts
        self._update_status()

    @Slot(str)
    def on_scan_finished(self, outcome: str) -> None:
        self._scan_status = outcome
        self._update_status()

    @Slot()
    def on_export_ready(self) -> None:
        self._export_ready = True

    def export_results(self) -> None:
        if self.thread is not None or not self._export_ready or self._export_source is None:
            return
        source = self._export_source
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export all results (.txt or .csv)", "log-results.txt", "Text or CSV files (*.txt *.csv)",
        )
        # The modal dialog can process a close event or a change to the current scan.
        if not destination or self.thread is not None or self._export_source is not source:
            return
        if Path(destination).suffix.lower() not in (".txt", ".csv"):
            self._export_status = "Export failed: choose a filename ending in .txt or .csv."
            self._update_status()
            return
        self._export_destination = destination
        self._exporting = True
        self._export_status = "Exporting..."
        self._update_status()
        self.start_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.thread = QThread()
        self.worker = ExportWorker(source)
        self.worker.moveToThread(self.thread)
        self.export_requested.connect(self.worker.export, Qt.ConnectionType.QueuedConnection)
        self.worker.committing.connect(self.on_export_committing)
        self.worker.error.connect(self.on_export_error)
        self.worker.finished.connect(self.on_export_finished)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.on_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()
        self.export_requested.emit(destination)

    @Slot()
    def on_export_committing(self) -> None:
        self.cancel_button.setEnabled(False)
        self._export_status = "Finishing export (cannot cancel now)..."
        self._update_status()

    @Slot(str)
    def on_export_error(self, message: str) -> None:
        self._export_status = f"Export failed: {message}"
        self._update_status()

    @Slot(str)
    def on_export_finished(self, outcome: str) -> None:
        self._exporting = False
        if outcome == "Finished":
            self._export_status = f"Exported to {self._export_destination}"
        elif outcome == "Cancelled":
            self._export_status = "Export cancelled; destination unchanged."
        self._update_status()

    def _close_export_source(self) -> None:
        if self._export_source is not None:
            try:
                self._export_source.close()
            except OSError:
                # Discarding a temporary snapshot must not prevent closing or starting another scan.
                pass
        self._export_source = None
        self._export_ready = False
        self.export_button.setEnabled(False)

    def _update_status(self) -> None:
        status = (
            f"{self._scan_status} | Matches: {self._match_count} | "
            f"Skipped files: {self._skipped_file_count} | Skipped folders: {self._skipped_folder_count}"
        )
        if self._match_count + self._skipped_file_count + self._skipped_folder_count > MAX_DISPLAY_ROWS:
            status += f" | Display limited to {MAX_DISPLAY_ROWS:,} rows"
        if self._export_status:
            status += f" | {self._export_status}"
        self.status_label.setText(status)
        self.status_label.setAccessibleDescription(self.status_label.text())

    @Slot(str)
    def on_error(self, message):
        self.results.addItem(f"Error: {message}")

    @Slot()
    def on_finished(self):
        self.worker = None
        self.thread = None
        self._exporting = False
        if not self._export_ready:
            self._close_export_source()
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.export_button.setEnabled(self._export_ready)
        if self._close_requested:
            self._close_requested = False
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.thread is not None:
            self._close_requested = True
            self.cancel_scan()
            event.ignore()
        else:
            self._close_export_source()
            event.accept()
