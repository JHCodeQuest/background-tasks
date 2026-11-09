import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from models import SearchOptions
from worker import LogWorker


@pytest.fixture
def scan():
    def run(path, pattern="ERROR", worker=None):
        worker = worker or LogWorker()
        events = {"matches": [], "errors": [], "skipped": [], "progress": [], "ranges": [], "finished": []}
        def collect(rows, counts):
            for row in rows:
                events["skipped" if row.startswith("Skipped ") else "matches"].append(row)

        worker.results_ready.connect(collect)
        worker.error.connect(events["errors"].append)
        worker.progress.connect(events["progress"].append)
        worker.progress_range.connect(lambda low, high: events["ranges"].append((low, high)))
        worker.finished.connect(events["finished"].append)
        worker.scan(SearchOptions(str(path), pattern))
        assert len(events["finished"]) == 1
        return events

    return run


def test_missing_path_is_an_error(tmp_path, scan):
    events = scan(tmp_path / "missing")

    assert len(events["errors"]) == 1
    assert events["matches"] == []
    assert 100 not in events["progress"]


@pytest.mark.parametrize("directory", [False, True])
def test_empty_input_completes_at_100_percent(tmp_path, scan, directory):
    path = tmp_path if directory else tmp_path / "empty.log"
    if not directory:
        path.touch()

    events = scan(path)

    assert events["errors"] == []
    assert events["matches"] == []
    assert events["progress"][-1:] == [100]
    assert events["ranges"][-1] == (0, 100)
    assert ((0, 0) in events["ranges"]) == directory


def test_utf8_bom_is_not_part_of_first_line(tmp_path, scan):
    log = tmp_path / "bom.log"
    log.write_text("ERROR café\n", encoding="utf-8-sig")

    events = scan(log)

    assert events["errors"] == []
    assert events["matches"] == [f"{log}:1: ERROR café"]


@pytest.mark.parametrize("content", [b"ERROR\x00binary\n", b"ERROR\xffinvalid\n", "ERROR\n".encode("utf-16")])
def test_unsupported_file_is_reported_not_silently_searched(tmp_path, scan, content):
    log = tmp_path / "unsupported.log"
    log.write_bytes(content)

    events = scan(log)

    assert len(events["errors"]) == 1
    assert events["matches"] == []
    assert 100 not in events["progress"]
    assert events["ranges"][-1] == (0, 100)


@pytest.mark.parametrize("exception", [PermissionError, FileNotFoundError])
def test_unreadable_file_does_not_abort_folder(tmp_path, scan, monkeypatch, exception):
    blocked = tmp_path / "blocked.log"
    blocked.touch()
    good = tmp_path / "good.log"
    good.write_text("ERROR readable\n", encoding="utf-8")
    original_open = Path.open

    def open_file(path, *args, **kwargs):
        if path == blocked:
            raise exception("Cannot read test log")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    events = scan(tmp_path)

    assert events["errors"] == []
    assert events["matches"] == [f"{good}:1: ERROR readable"]
    assert events["progress"][-1] == 100
    assert len(events["skipped"]) == 1
    assert str(blocked) in events["skipped"][0]
    assert "Cannot read test log" in events["skipped"][0]


def test_unreadable_selected_folder_is_an_error(tmp_path, scan, monkeypatch):
    def denied(path):
        raise PermissionError(errno.EACCES, "Access denied", str(path))

    monkeypatch.setattr(os, "scandir", denied)
    events = scan(tmp_path)

    assert len(events["errors"]) == 1
    assert events["matches"] == []
    assert 100 not in events["progress"]


def test_cancellation_stops_folder_discovery(tmp_path, scan, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    (child / "nested.log").write_text("ERROR nested\n", encoding="utf-8")
    original_scandir = os.scandir
    visited = []
    worker = LogWorker()
    monkeypatch.setattr(worker, "_should_stop", lambda: bool(visited))

    def discover(path):
        visited.append(Path(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", discover)
    events = scan(tmp_path, worker=worker)

    assert visited == [tmp_path]
    assert events["matches"] == []
    assert events["errors"] == []
    assert 100 not in events["progress"]
    assert events["ranges"][-1] == (0, 100)


def test_empty_path_is_not_treated_as_current_folder(scan):
    events = scan("")

    assert events["errors"] == ["Select a file or folder first."]
    assert events["matches"] == []


@pytest.mark.parametrize("content", [b"ERROR\x00binary\n", b"ERROR\xffinvalid\n", "ERROR\n".encode("utf-16")])
def test_unsupported_files_are_reported_and_other_files_continue(tmp_path, scan, content):
    bad = tmp_path / "unsupported.log"
    bad.write_bytes(content)
    nested = tmp_path / "nested"
    nested.mkdir()
    good = nested / "good.txt"
    good.write_text("ERROR café\n", encoding="utf-8")

    events = scan(tmp_path)

    assert events["errors"] == []
    assert events["matches"] == [f"{good}:1: ERROR café"]
    assert len(events["skipped"]) == 1
    assert str(bad) in events["skipped"][0]
    assert events["progress"][-1] == 100


def test_unreadable_selected_file_is_an_error(tmp_path, scan, monkeypatch):
    log = tmp_path / "blocked.log"
    log.touch()

    def denied(*args, **kwargs):
        raise PermissionError("Cannot read selected log")

    monkeypatch.setattr(Path, "open", denied)
    events = scan(log)

    assert events["errors"] == ["Cannot read selected log"]
    assert events["skipped"] == []
    assert 100 not in events["progress"]


def test_unreadable_subfolder_is_reported_and_other_files_continue(tmp_path, scan, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    good = tmp_path / "good.log"
    good.write_text("ERROR readable\n", encoding="utf-8")
    original_scandir = os.scandir

    def discover(path):
        if Path(path) == blocked:
            raise PermissionError(errno.EACCES, "Access denied", str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", discover)
    events = scan(tmp_path)

    assert events["errors"] == []
    assert events["matches"] == [f"{good}:1: ERROR readable"]
    assert len(events["skipped"]) == 1
    assert str(blocked) in events["skipped"][0]
    assert events["progress"][-1] == 100


def test_cancellation_after_file_does_not_discover_next_folder(tmp_path, scan, monkeypatch):
    (tmp_path / "first.log").write_text("ERROR first\n", encoding="utf-8")
    (tmp_path / "child").mkdir()
    original_scandir = os.scandir
    visited = []
    stopped = False

    class CancellingWorker(LogWorker):
        def _scan_file(self, *args, **kwargs):
            nonlocal stopped
            super()._scan_file(*args, **kwargs)
            stopped = True

    worker = CancellingWorker()
    monkeypatch.setattr(worker, "_should_stop", lambda: stopped)

    def discover(path):
        visited.append(Path(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", discover)
    events = scan(tmp_path, worker=worker)

    assert visited == [tmp_path]
    assert len(events["matches"]) == 1
    assert events["errors"] == []
    assert 100 not in events["progress"]


@pytest.mark.parametrize("directory", [False, True])
def test_non_regular_files_are_not_opened(tmp_path, scan, monkeypatch, directory):
    special = tmp_path / "special"
    special.touch()
    original_stat = Path.stat

    def file_info(path, *args, **kwargs):
        if path == special:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)
        return original_stat(path, *args, **kwargs)

    def unexpected_open(*args, **kwargs):
        pytest.fail("Non-regular files must not be opened")

    monkeypatch.setattr(Path, "stat", file_info)
    monkeypatch.setattr(Path, "open", unexpected_open)
    events = scan(tmp_path if directory else special)

    assert events["matches"] == []
    assert len(events["skipped"]) == int(directory)
    assert len(events["errors"]) == int(not directory)


def test_matches_before_later_file_failure_are_retained_with_warning(tmp_path, scan):
    log = tmp_path / "partial.log"
    log.write_bytes(b"ERROR readable\nINFO\x00binary\n")

    events = scan(tmp_path)

    assert events["matches"] == [f"{log}:1: ERROR readable"]
    assert events["errors"] == []
    assert len(events["skipped"]) == 1
    assert str(log) in events["skipped"][0]
    assert "Binary data" in events["skipped"][0]
