import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from shiboken6 import isValid

import main_window
from models import SearchOptions
from worker import LogWorker


def wait_until(app, condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert condition(), "Timed out waiting for scan lifecycle"


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    threads = []

    def make_thread():
        thread = QThread()
        threads.append(thread)
        return thread

    monkeypatch.setattr(main_window, "QThread", make_thread)
    log = tmp_path / "sample.log"
    log.write_text("ERROR first\n", encoding="utf-8")
    window = main_window.MainWindow()
    window.selected_path = str(log)
    window.pattern_input.setText("ERROR")
    yield window
    for thread in threads:
        if isValid(thread):
            thread.requestInterruption()
            thread.quit()
            assert thread.wait(3000), "Test thread did not stop"
    app.processEvents()
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_worker_always_finishes_after_error(tmp_path):
    log = tmp_path / "sample.log"
    log.write_text("test", encoding="utf-8")
    worker = LogWorker()
    errors, finished = [], []
    worker.error.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))

    worker.scan(SearchOptions(str(log), "[", use_regex=True))

    assert len(errors) == 1
    assert finished == [True]


def test_scan_runs_off_ui_thread(app, window, monkeypatch):
    scan_threads = []

    class RecordingWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            scan_threads.append(threading.get_ident())
            super()._scan_file(*args, **kwargs)

    monkeypatch.setattr(main_window, "LogWorker", RecordingWorker)
    window.start_scan()
    wait_until(app, lambda: bool(scan_threads))

    assert scan_threads[0] != threading.get_ident()


def test_success_cleans_up_and_allows_repeated_scans(app, window):
    for _ in range(2):
        window.start_scan()
        thread, worker = window.thread, window.worker
        wait_until(app, lambda: window.thread is None)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        assert window.worker is None
        assert not isValid(thread)
        assert not isValid(worker)
        assert window.start_button.isEnabled()
        assert not window.cancel_button.isEnabled()
        assert window.results.item(0).text() == "ERROR first"


def test_error_cleans_up_before_enabling_restart(app, window, monkeypatch):
    class FailingWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            raise PermissionError("Cannot read test log")

    monkeypatch.setattr(main_window, "LogWorker", FailingWorker)
    window.start_scan()
    thread = window.thread
    wait_until(app, lambda: window.thread is None)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert not isValid(thread)
    assert window.worker is None
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.results.item(0).text() == "Error: Cannot read test log"

    monkeypatch.setattr(main_window, "LogWorker", LogWorker)
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    assert window.results.item(0).text() == "ERROR first"


def test_second_start_does_not_replace_active_scan(app, window):
    window.start_scan()
    thread, worker = window.thread, window.worker

    window.start_scan()

    assert window.thread is thread
    assert window.worker is worker
    wait_until(app, lambda: window.thread is None)


@pytest.mark.parametrize("wait_for_worker", [False, True])
def test_cancellation_cleans_up(app, window, monkeypatch, wait_for_worker):
    entered = threading.Event()
    release = threading.Event()

    class PausedWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            super()._scan_file(*args, **kwargs)

    monkeypatch.setattr(main_window, "LogWorker", PausedWorker)
    try:
        window.start_scan()
        if wait_for_worker:
            wait_until(app, entered.is_set)
        window.cancel_scan()
        release.set()
        wait_until(app, lambda: window.thread is None)

        assert window.worker is None
        assert window.results.count() == 0
        assert not window.cancel_button.isEnabled()
        window.cancel_scan()
    finally:
        release.set()


def test_close_during_scan_is_deferred(app, window, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class PausedWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            super()._scan_file(*args, **kwargs)

    monkeypatch.setattr(main_window, "LogWorker", PausedWorker)
    window.show()
    try:
        window.start_scan()
        wait_until(app, entered.is_set)

        assert not window.close()
        assert window.isVisible()
        assert window.thread is not None

        release.set()
        wait_until(app, lambda: window.thread is None)
        assert not window.isVisible()
        assert window.worker is None
    finally:
        release.set()


@pytest.mark.parametrize("cancel", [False, True])
def test_folder_progress_returns_from_busy_state(app, window, monkeypatch, cancel):
    entered = threading.Event()
    release = threading.Event()

    class PausedWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            super()._scan_file(*args, **kwargs)

    monkeypatch.setattr(main_window, "LogWorker", PausedWorker)
    window.selected_path = str(Path(window.selected_path).parent)
    try:
        window.start_scan()
        wait_until(app, entered.is_set)
        app.processEvents()
        assert (window.progress.minimum(), window.progress.maximum()) == (0, 0)

        if cancel:
            window.cancel_scan()
        release.set()
        wait_until(app, lambda: window.thread is None)

        assert (window.progress.minimum(), window.progress.maximum()) == (0, 100)
        assert window.progress.value() == (0 if cancel else 100)
    finally:
        release.set()
