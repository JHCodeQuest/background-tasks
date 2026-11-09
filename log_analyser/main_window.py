import re

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QListWidget, QProgressBar, QFileDialog,
    QLabel, QCheckBox
)
from worker import LogWorker
from models import SearchOptions


class MainWindow(QWidget):
    scan_requested = Signal(object)

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

        # progress bar
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setAccessibleName("Scan progress")
        self.results = QListWidget()
        self.results.setAccessibleName("Search results")
        self.status_label = QLabel()
        self.status_label.setAccessibleName("Scan status")
        self.status_label.setWordWrap(True)
        self._update_status()

        # widget
        top = QHBoxLayout()
        top.addWidget(self.pick_file_button)
        top.addWidget(self.pick_folder_button)
        top.addWidget(self.start_button)
        top.addWidget(self.cancel_button)

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
            directory_mode=False
        )

        self.thread = QThread()
        self.worker = LogWorker()
        self.worker.moveToThread(self.thread)

        self.scan_requested.connect(self.worker.scan, Qt.ConnectionType.QueuedConnection)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.progress_range.connect(self.progress.setRange)
        self.worker.match_found.connect(self.on_match)
        self.worker.skipped.connect(self.on_skipped)
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
            self.thread.requestInterruption()
            self.cancel_button.setEnabled(False)
            if self._scan_status == "Scanning":
                self._scan_status = "Cancelling"
                self._update_status()

    @Slot(str)
    def on_match(self, message: str) -> None:
        self.results.addItem(message)
        self._match_count += 1
        self._update_status()

    @Slot(str, bool)
    def on_skipped(self, message: str, directory: bool) -> None:
        self.results.addItem(message)
        if directory:
            self._skipped_folder_count += 1
        else:
            self._skipped_file_count += 1
        self._update_status()

    @Slot(str)
    def on_scan_finished(self, outcome: str) -> None:
        self._scan_status = outcome
        self._update_status()

    def _update_status(self) -> None:
        self.status_label.setText(
            f"{self._scan_status} | Matches: {self._match_count} | "
            f"Skipped files: {self._skipped_file_count} | Skipped folders: {self._skipped_folder_count}"
        )
        self.status_label.setAccessibleDescription(self.status_label.text())

    @Slot(str)
    def on_error(self, message):
        self.results.addItem(f"Error: {message}")

    @Slot()
    def on_finished(self):
        self.worker = None
        self.thread = None
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self._close_requested:
            self._close_requested = False
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.thread is not None:
            self._close_requested = True
            self.cancel_scan()
            event.ignore()
        else:
            event.accept()
