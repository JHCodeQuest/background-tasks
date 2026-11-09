import errno
import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer, Qt
from PySide6.QtTest import QTest
from shiboken6 import isValid

import main_window
from models import SearchOptions
import result_export
from result_export import ExportWorker
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
    worker.finished.connect(finished.append)

    worker.scan(SearchOptions(str(log), "[", use_regex=True))

    assert len(errors) == 1
    assert finished == ["Failed"]


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
        assert window.results.item(0).text() == f"{window.selected_path}:1: ERROR first"
        assert window.status_label.text() == "Finished | Matches: 1 | Skipped files: 0 | Skipped folders: 0"


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
    assert window.status_label.text() == "Failed | Matches: 0 | Skipped files: 0 | Skipped folders: 0"

    monkeypatch.setattr(main_window, "LogWorker", LogWorker)
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    assert window.results.item(0).text() == f"{window.selected_path}:1: ERROR first"
    assert window.status_label.text() == "Finished | Matches: 1 | Skipped files: 0 | Skipped folders: 0"


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
        assert window.status_label.text().startswith("Scanning | ")
        if wait_for_worker:
            wait_until(app, entered.is_set)
        window.cancel_scan()
        assert window.status_label.text().startswith("Cancelling | ")
        release.set()
        wait_until(app, lambda: window.thread is None)

        assert window.worker is None
        assert window.results.count() == 0
        assert not window.cancel_button.isEnabled()
        assert window.status_label.text() == "Cancelled | Matches: 0 | Skipped files: 0 | Skipped folders: 0"
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


def test_summary_counts_matches_and_skipped_files_and_folders(app, window, monkeypatch):
    root = Path(window.selected_path).parent
    (root / "binary.log").write_bytes(b"ERROR\x00binary\n")
    blocked = root / "blocked"
    blocked.mkdir()
    original_scandir = os.scandir

    def discover(path):
        if Path(path) == blocked:
            raise PermissionError(errno.EACCES, "Access denied", str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", discover)
    window.selected_path = str(root)
    for _ in range(2):
        window.start_scan()
        wait_until(app, lambda: window.thread is None)

        assert window.results.count() == 3
        assert window.status_label.text() == "Finished | Matches: 1 | Skipped files: 1 | Skipped folders: 1"
        assert window.status_label.accessibleDescription() == window.status_label.text()


def test_no_matches_still_reports_finished(app, window):
    window.pattern_input.setText("missing")
    window.start_scan()
    wait_until(app, lambda: window.thread is None)

    assert window.results.count() == 0
    assert window.status_label.text() == "Finished | Matches: 0 | Skipped files: 0 | Skipped folders: 0"


def test_start_and_cancel_work_from_keyboard(app, window, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class PausedWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            super()._scan_file(*args, **kwargs)

    monkeypatch.setattr(main_window, "LogWorker", PausedWorker)
    window.show()
    window.activateWindow()
    app.processEvents()
    try:
        window.start_button.setFocus()
        QTest.keyClick(window.start_button, Qt.Key.Key_Space)
        wait_until(app, entered.is_set)
        assert not window.start_button.isEnabled()
        assert window.cancel_button.isEnabled()

        window.cancel_button.setFocus()
        QTest.keyClick(window.cancel_button, Qt.Key.Key_Space)
        release.set()
        wait_until(app, lambda: window.thread is None)

        assert window.status_label.text().startswith("Cancelled | ")
    finally:
        release.set()


def test_large_scan_limits_display_but_counts_all_matches(app, window):
    Path(window.selected_path).write_text("ERROR match\n" * 10_005, encoding="utf-8")
    window.start_scan()
    wait_until(app, lambda: window.thread is None)

    assert window.results.count() == 5_000
    assert window.results.item(0).text() == f"{window.selected_path}:1: ERROR match"
    assert window.results.item(4_999).text() == f"{window.selected_path}:5000: ERROR match"
    assert window.status_label.text() == (
        "Finished | Matches: 10005 | Skipped files: 0 | Skipped folders: 0"
        " | Display limited to 5,000 rows"
    )

    Path(window.selected_path).write_text("INFO no matches\n", encoding="utf-8")
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    assert window.results.count() == 0
    assert window.status_label.text() == "Finished | Matches: 0 | Skipped files: 0 | Skipped folders: 0"


def test_fatal_error_remains_visible_after_display_limit(app, window):
    Path(window.selected_path).write_bytes(b"ERROR match\n" * 5_001 + b"INFO\x00binary\n")
    window.start_scan()
    wait_until(app, lambda: window.thread is None)

    assert window.results.count() == 5_001
    assert window.results.item(5_000).text().startswith("Error: Binary data")
    assert window.status_label.text() == (
        "Failed | Matches: 5001 | Skipped files: 0 | Skipped folders: 0"
        " | Display limited to 5,000 rows"
    )


def test_large_scan_keeps_ui_timer_responsive_and_can_be_cancelled(app, window):
    Path(window.selected_path).write_text("ERROR match\n" * 1_000_000, encoding="utf-8")
    ticks = []
    timer = QTimer()

    def tick():
        ticks.append(time.monotonic())
        if len(ticks) >= 3 and window._match_count > 0:
            window.cancel_scan()

    timer.timeout.connect(tick)
    try:
        window.start_scan()
        timer.start(5)
        wait_until(app, lambda: window.thread is None)

        assert len(ticks) >= 3
        assert 0 < window._match_count < 1_000_000
        assert window.results.count() <= 5_000
        assert window.status_label.text().startswith("Cancelled | ")
    finally:
        timer.stop()


def test_full_scan_can_be_exported_repeatedly_off_ui_thread(app, window, monkeypatch, tmp_path):
    log = Path(window.selected_path)
    log.write_text("ERROR match\n" * 5_002, encoding="utf-8")
    window.start_scan()
    assert not window.export_button.isEnabled()
    wait_until(app, lambda: window.thread is None)
    source = window._export_source
    assert window.export_button.isEnabled()
    log.unlink()
    thread_ids = []

    class RecordingExporter(ExportWorker):
        def _should_stop(self):
            thread_ids.append(threading.get_ident())
            return super()._should_stop()

    monkeypatch.setattr(main_window, "ExportWorker", RecordingExporter)
    for extension in (".txt", ".csv"):
        target = tmp_path / ("export" + extension)
        monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
        window.export_button.click()
        assert not window.start_button.isEnabled()
        assert not window.export_button.isEnabled()
        wait_until(app, lambda: window.thread is None)

        assert window.export_button.isEnabled()
        assert not source.closed
        assert window.status_label.text().endswith(f"Exported to {target}")
        if extension == ".txt":
            lines = target.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 5_003
            assert lines[-2] == f"{log}:5002: ERROR match"
        else:
            with target.open(encoding="utf-8-sig", newline="") as output:
                rows = list(csv.reader(output))
            assert len(rows) == 5_004
            assert rows[-2] == ["match", str(log), "5002", "ERROR match"]
    assert thread_ids and all(identifier != threading.get_ident() for identifier in thread_ids)


def test_cancelling_save_picker_keeps_snapshot_and_status(app, window, monkeypatch):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    source, status = window._export_source, window.status_label.text()
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *args: ("", ""))

    window.export_button.click()

    assert window.thread is None
    assert window._export_source is source and not source.closed
    assert window.status_label.text() == status
    assert window.export_button.isEnabled()


def test_unsupported_export_filename_is_rejected_without_modifying_file(app, window, monkeypatch, tmp_path):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    target = tmp_path / "existing.log"
    target.write_bytes(b"Keep this")
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))

    window.export_button.click()

    assert window.thread is None
    assert target.read_bytes() == b"Keep this"
    assert ".txt or .csv" in window.status_label.text()
    assert window.export_button.isEnabled()


def test_export_error_preserves_destination_and_allows_retry(app, window, monkeypatch, tmp_path):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    source = window._export_source
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Keep this")
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))

    def fail(*args):
        raise PermissionError("Destination is locked")

    with monkeypatch.context() as patch:
        patch.setattr(result_export.os, "replace", fail)
        window.export_button.click()
        wait_until(app, lambda: window.thread is None)
        assert target.read_bytes() == b"Keep this"
        assert "Destination is locked" in window.status_label.text()
        assert window.export_button.isEnabled()
        assert not source.closed

    window.export_button.click()
    wait_until(app, lambda: window.thread is None)
    assert "ERROR first" in target.read_text(encoding="utf-8")
    assert window.status_label.text().endswith(f"Exported to {target}")


@pytest.mark.parametrize("close", [False, True])
def test_export_cancellation_and_close_preserve_files_and_handle_lifetime(app, window, monkeypatch, tmp_path, close):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    source = window._export_source
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Keep this")
    entered, release = threading.Event(), threading.Event()

    class PausedExporter(ExportWorker):
        def _should_stop(self):
            if not entered.is_set():
                entered.set()
                release.wait(2)
            return super()._should_stop()

    monkeypatch.setattr(main_window, "ExportWorker", PausedExporter)
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    window.show()
    try:
        window.export_button.click()
        wait_until(app, entered.is_set)
        thread = window.thread
        window.start_scan()
        window.export_results()
        assert window.thread is thread
        assert window._export_source is source and not source.closed

        if close:
            assert not window.close()
            assert window.isVisible()
        else:
            window.cancel_button.click()
        assert not source.closed
        release.set()
        wait_until(app, lambda: window.thread is None)

        assert target.read_bytes() == b"Keep this"
        assert list(tmp_path.glob(".log-analyser-*.tmp")) == []
        assert source.closed == close
        if close:
            assert not window.isVisible()
        else:
            assert window.export_button.isEnabled()
            assert window.status_label.text().endswith("Export cancelled; destination unchanged.")
    finally:
        release.set()


def test_new_scan_and_window_close_release_previous_snapshots(app, window):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    previous = window._export_source
    window.start_scan()
    assert previous.closed
    assert not window.export_button.isEnabled()
    wait_until(app, lambda: window.thread is None)
    current = window._export_source
    assert current is not previous and not current.closed

    window.close()

    assert current.closed
    assert not window.export_button.isEnabled()


def test_failed_snapshot_creation_keeps_previous_scan_exportable(app, window, monkeypatch):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    previous = window._export_source

    def fail(*args, **kwargs):
        raise OSError("No temporary disk space")

    monkeypatch.setattr(main_window.tempfile, "TemporaryFile", fail)
    window.start_scan()

    assert window.thread is None
    assert window._export_source is previous and not previous.closed
    assert window.results.count() == 1
    assert "No temporary disk space" in window.status_label.text()
    assert window.export_button.isEnabled()


def test_snapshot_write_failure_disables_export_and_closes_storage(app, window, monkeypatch):
    original_file = main_window.tempfile.TemporaryFile
    created = []

    def failed_file(*args, **kwargs):
        source = original_file(*args, **kwargs)

        def fail_write(*args):
            raise OSError("No temporary disk space")

        source.write = fail_write
        created.append(source)
        return source

    monkeypatch.setattr(main_window.tempfile, "TemporaryFile", failed_file)
    window.start_scan()
    wait_until(app, lambda: window.thread is None)

    assert window.status_label.text().startswith("Failed | ")
    assert not window.export_button.isEnabled()
    assert window._export_source is None
    assert created[0].closed
    assert any("No temporary disk space" in window.results.item(index).text() for index in range(window.results.count()))


def test_closing_window_inside_save_dialog_does_not_start_export(app, window, monkeypatch, tmp_path):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    target = tmp_path / "never-created.txt"

    def choose(*args):
        window.close()
        return str(target), ""

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", choose)
    window.export_results()

    assert window.thread is None
    assert not target.exists()


@pytest.mark.parametrize("close", [False, True])
def test_late_export_cancel_does_not_block_ui_or_falsely_report_cancelled(app, window, monkeypatch, tmp_path, close):
    window.start_scan()
    wait_until(app, lambda: window.thread is None)
    source = window._export_source
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Keep this")
    entered, release = threading.Event(), threading.Event()
    original_replace = result_export.os.replace

    def paused_commit(temporary, destination):
        entered.set()
        release.wait(2)
        original_replace(temporary, destination)

    monkeypatch.setattr(result_export.os, "replace", paused_commit)
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    window.show()
    try:
        window.export_button.click()
        wait_until(app, entered.is_set)
        if close:
            assert not window.close()
        else:
            window.cancel_scan()

        assert target.read_bytes() == b"Keep this"
        assert "cannot cancel now" in window.status_label.text()
        assert not window.cancel_button.isEnabled()
        assert not source.closed
        release.set()
        wait_until(app, lambda: window.thread is None)

        assert "ERROR first" in target.read_text(encoding="utf-8")
        assert "Exported to" in window.status_label.text()
        assert source.closed == close
    finally:
        release.set()
import csv
