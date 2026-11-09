import json
import os
from pathlib import Path
import tempfile

import pytest

from models import SearchOptions
from worker import LogWorker, MAX_DISPLAY_ROWS


@pytest.fixture
def snapshot(tmp_path):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="", dir=tmp_path) as source:
        yield source


def test_snapshot_retains_rows_beyond_display_limit(tmp_path, snapshot):
    log = tmp_path / "large.log"
    log.write_text("ERROR match\n" * (MAX_DISPLAY_ROWS + 7), encoding="utf-8")
    worker = LogWorker(snapshot)
    displayed, ready = [], []
    worker.results_ready.connect(lambda rows, counts: displayed.extend(rows))
    worker.export_ready.connect(lambda: ready.append(True))

    worker.scan(SearchOptions(str(log), "ERROR"))

    records = [json.loads(line) for line in snapshot]
    assert len(displayed) == MAX_DISPLAY_ROWS
    assert len(records) == MAX_DISPLAY_ROWS + 8
    assert records[-2] == ["match", str(log), MAX_DISPLAY_ROWS + 7, "ERROR match"]
    assert records[-1] == ["summary", str(log), None, "Finished | Matches: 5007 | Skipped files: 0 | Skipped folders: 0"]
    assert ready == [True]


def test_snapshot_does_not_scan_itself_when_inside_selected_folder(tmp_path, snapshot, monkeypatch):
    log = tmp_path / "sample.log"
    log.write_text("ERROR first\n", encoding="utf-8")
    worker = LogWorker(snapshot)
    errors = []
    worker.error.connect(errors.append)
    original_open = Path.open

    def open_input(path, *args, **kwargs):
        if os.path.samestat(path.stat(), os.fstat(snapshot.fileno())):
            pytest.fail("The active snapshot must never be opened as an input file")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_input)

    worker.scan(SearchOptions(str(tmp_path), "ERROR"))

    records = [json.loads(line) for line in snapshot]
    assert errors == []
    assert records == [
        ["match", str(log), 1, "ERROR first"],
        ["summary", str(tmp_path), None, "Finished | Matches: 1 | Skipped files: 0 | Skipped folders: 0"],
    ]


def test_failed_scan_retains_partial_rows_and_failed_summary(tmp_path, snapshot):
    log = tmp_path / "partial.log"
    log.write_bytes(b"ERROR first\nINFO\x00binary\n")
    worker = LogWorker(snapshot)
    ready = []
    worker.export_ready.connect(lambda: ready.append(True))

    worker.scan(SearchOptions(str(log), "ERROR"))

    records = [json.loads(line) for line in snapshot]
    assert [record[0] for record in records] == ["match", "error", "summary"]
    assert records[-1][3].startswith("Failed | Matches: 1 | ")
    assert ready == [True]


def test_cancelled_scan_retains_pending_rows_and_cancelled_summary(tmp_path, snapshot, monkeypatch):
    log = tmp_path / "cancelled.log"
    log.write_text("ERROR match\n" * 100, encoding="utf-8")
    worker = LogWorker(snapshot)
    monkeypatch.setattr(worker, "_should_stop", lambda: worker._match_count >= 3)

    worker.scan(SearchOptions(str(log), "ERROR"))

    records = [json.loads(line) for line in snapshot]
    assert len(records) == 4
    assert records[-1][3] == "Cancelled | Matches: 3 | Skipped files: 0 | Skipped folders: 0"


@pytest.mark.parametrize("operation", ["write", "flush"])
def test_snapshot_io_failure_disables_export_and_is_not_an_input_skip(tmp_path, snapshot, monkeypatch, operation):
    log = tmp_path / "sample.log"
    log.write_text("ERROR first\n", encoding="utf-8")
    worker = LogWorker(snapshot)
    errors, outcomes, ready, counts = [], [], [], []
    worker.error.connect(errors.append)
    worker.finished.connect(outcomes.append)
    worker.export_ready.connect(lambda: ready.append(True))
    worker.results_ready.connect(lambda rows, totals: counts.append(totals))

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(snapshot, operation, fail)
    worker.scan(SearchOptions(str(tmp_path), "ERROR"))

    assert errors == ["Could not retain results for export: Disk full"]
    assert outcomes == ["Failed"]
    assert ready == []
    assert counts[-1] == (1, 0, 0)


def test_snapshot_keeps_original_matching_line_whitespace(tmp_path, snapshot):
    log = tmp_path / "spaces.log"
    log.write_text("ERROR trailing spaces  \t\n", encoding="utf-8")
    LogWorker(snapshot).scan(SearchOptions(str(log), "ERROR"))

    records = [json.loads(line) for line in snapshot]
    assert records[0] == ["match", str(log), 1, "ERROR trailing spaces  \t"]


def test_snapshot_includes_skipped_files_and_folders(tmp_path, snapshot, monkeypatch):
    log = tmp_path / "good.log"
    log.write_text("ERROR match\n", encoding="utf-8")
    bad = tmp_path / "binary.log"
    bad.write_bytes(b"ERROR\x00binary\n")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    original_scandir = os.scandir

    def discover(path):
        if Path(path) == blocked:
            raise PermissionError(13, "Access denied", str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", discover)
    LogWorker(snapshot).scan(SearchOptions(str(tmp_path), "ERROR"))

    records = [json.loads(line) for line in snapshot]
    assert sorted(record[0] for record in records) == ["match", "skipped_file", "skipped_folder", "summary"]
    assert next(record[1] for record in records if record[0] == "skipped_file") == str(bad)
    assert next(record[1] for record in records if record[0] == "skipped_folder") == str(blocked)
    assert records[-1][3] == "Finished | Matches: 1 | Skipped files: 1 | Skipped folders: 1"
