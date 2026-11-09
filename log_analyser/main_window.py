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
        
        # label
        self.path_label = QLabel("No file selected")
        self.pattern_input = QLineEdit()
        self.pattern_input.setPlaceholderText("Enter keyword or regex")

        # check box
        self.regex_checkbox = QCheckBox("Regex")
        self.case_checkbox = QCheckBox("Case sensitve")
        
        #buttons
        self.pick_button = QPushButton("Choose File/Folder")
        self.start_button = QPushButton("Start")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)

        # progress bar
        self.progress = QProgressBar()
        self.results = QListWidget()

        # widget
        top = QHBoxLayout()
        top.addWidget(self.pick_button)
        top.addWidget(self.start_button)
        top.addWidget(self.cancel_button)

        # options
        opts = QHBoxLayout()
        opts.addWidget(self.regex_checkbox)
        opts.addWidget(self.case_checkbox)

        # layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.path_label)
        layout.addWidget(self.pattern_input)
        layout.addLayout(opts)
        layout.addLayout(top)
        layout.addWidget(self.progress)
        layout.addWidget(self.results)

        # button connect
        self.pick_button.clicked.connect(self.pick_path)
        self.start_button.clicked.connect(self.start_scan)
        self.cancel_button.clicked.connect(self.cancel_scan)

    def pick_path(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Log File",
            "",
            "Log Files(*.log *.txt);;All Files (*)"
        )

        if file_path:
            self.selected_path = file_path
            self.path_label.setText(file_path)
            return
        
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
        self.worker.match_found.connect(self.results.addItem)
        self.worker.error.connect(self.on_error)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.on_finished)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()
        self.scan_requested.emit(options)
    
    def cancel_scan(self):
        if self.thread is not None:
            self.thread.requestInterruption()

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
