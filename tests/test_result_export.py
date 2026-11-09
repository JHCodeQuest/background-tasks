import csv
import json
from pathlib import Path
import tempfile

import pytest

from models import SearchOptions
import result_export
from result_export import ExportWorker
from worker import LogWorker, MAX_DISPLAY_ROWS


@pytest.fixture
def snapshot(tmp_path):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="", dir=tmp_path) as source:
        yield source


def run_export(source, target):
    worker = ExportWorker(source)
    errors, outcomes = [], []
    worker.error.connect(errors.append)
    worker.finished.connect(outcomes.append)
    worker.export(str(target))
    return errors, outcomes


@pytest.mark.parametrize("extension", [".txt", ".csv", ".CSV"])
def test_full_export_includes_hidden_results_without_rescanning(tmp_path, snapshot, extension):
    log = tmp_path / "large.log"
    log.write_text('ERROR "comma, café"\n' * (MAX_DISPLAY_ROWS + 7), encoding="utf-8")
    LogWorker(snapshot).scan(SearchOptions(str(log), "ERROR"))
    log.unlink()
    target = tmp_path / ("export" + extension)

    assert run_export(snapshot, target) == ([], ["Finished"])

    if extension.lower() == ".csv":
        with target.open(encoding="utf-8-sig", newline="") as output:
            rows = list(csv.reader(output))
        assert rows[0] == ["type", "path", "line", "text"]
        assert len(rows) == MAX_DISPLAY_ROWS + 9
        assert rows[-2] == ["match", str(log), "5007", 'ERROR "comma, café"']
        assert rows[-1][0] == "summary"
        assert rows[-1][3].startswith("Finished")
    else:
        rows = target.read_text(encoding="utf-8").splitlines()
        assert len(rows) == MAX_DISPLAY_ROWS + 8
        assert rows[-2] == f'{log}:5007: ERROR "comma, café"'
        assert rows[-1].startswith("Finished")


def test_repeated_exports_rewind_the_snapshot(tmp_path, snapshot):
    snapshot.write(json.dumps(["match", "sample.log", 2, "ERROR first"]) + "\n")
    for name in ("first.txt", "second.txt"):
        target = tmp_path / name
        assert run_export(snapshot, target) == ([], ["Finished"])
        assert target.read_text(encoding="utf-8") == "sample.log:2: ERROR first\n"


@pytest.mark.parametrize("text", ["=1+1", "+SUM(1,2)", "-2", "@SUM(A1)", "  =1+1", "\t=1+1", "\ufeff=1+1", "\u2003\ufeff =1+1"])
def test_csv_formula_like_cells_are_escaped_but_text_export_is_unchanged(tmp_path, snapshot, text):
    snapshot.write(json.dumps(["match", "sample.log", 1, text]) + "\n")
    csv_target, text_target = tmp_path / "safe.csv", tmp_path / "raw.txt"

    assert run_export(snapshot, csv_target) == ([], ["Finished"])
    assert run_export(snapshot, text_target) == ([], ["Finished"])

    with csv_target.open(encoding="utf-8-sig", newline="") as output:
        rows = list(csv.reader(output))
    assert rows[1] == ["match", "sample.log", "1", "'" + text]
    assert text_target.read_text(encoding="utf-8") == f"sample.log:1: {text}\n"


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
def test_failed_write_or_commit_preserves_existing_destination(tmp_path, snapshot, monkeypatch, failure):
    snapshot.write(json.dumps(["match", "sample.log", 1, "ERROR first"]) + "\n")
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Do not lose this")

    def fail(*args):
        raise OSError("Test disk failure")

    if failure == "write":
        original_file = result_export.tempfile.NamedTemporaryFile

        def failing_file(*args, **kwargs):
            output = original_file(*args, **kwargs)
            output.write = fail
            return output

        monkeypatch.setattr(result_export.tempfile, "NamedTemporaryFile", failing_file)
    else:
        monkeypatch.setattr(result_export.os, failure, fail)
    errors, outcomes = run_export(snapshot, target)

    assert outcomes == ["Failed"]
    assert "Test disk failure" in errors[0]
    assert target.read_bytes() == b"Do not lose this"
    assert list(tmp_path.glob(".log-analyser-*.tmp")) == []


def test_corrupt_snapshot_preserves_existing_destination(tmp_path, snapshot):
    snapshot.write("not JSON\n")
    target = tmp_path / "existing.csv"
    target.write_bytes(b"Do not lose this")

    errors, outcomes = run_export(snapshot, target)

    assert outcomes == ["Failed"]
    assert errors
    assert target.read_bytes() == b"Do not lose this"
    assert list(tmp_path.glob(".log-analyser-*.tmp")) == []


@pytest.mark.parametrize("extension", [".txt", ".csv"])
def test_cancelled_export_preserves_destination_and_removes_partial_file(tmp_path, snapshot, monkeypatch, extension):
    for index in range(20):
        snapshot.write(json.dumps(["match", "sample.log", index + 1, "ERROR match"]) + "\n")
    target = tmp_path / ("existing" + extension)
    target.write_bytes(b"Do not lose this")
    worker = ExportWorker(snapshot)
    checks = 0

    def stop():
        nonlocal checks
        checks += 1
        return checks >= 4

    monkeypatch.setattr(worker, "_should_stop", stop)
    outcomes = []
    worker.finished.connect(outcomes.append)
    worker.export(str(target))

    assert outcomes == ["Cancelled"]
    assert target.read_bytes() == b"Do not lose this"
    assert list(tmp_path.glob(".log-analyser-*.tmp")) == []


def test_invalid_extension_is_rejected_without_touching_destination(tmp_path, snapshot):
    target = tmp_path / "existing.log"
    target.write_bytes(b"Do not lose this")

    errors, outcomes = run_export(snapshot, target)

    assert outcomes == ["Failed"]
    assert ".txt or .csv" in errors[0]
    assert target.read_bytes() == b"Do not lose this"


def test_missing_destination_folder_is_reported(tmp_path, snapshot):
    target = tmp_path / "missing" / "results.txt"

    errors, outcomes = run_export(snapshot, target)

    assert outcomes == ["Failed"]
    assert errors
    assert not target.exists()


def test_cancellation_accepted_before_commit_preserves_destination(tmp_path, snapshot, monkeypatch):
    snapshot.write(json.dumps(["match", "sample.log", 1, "ERROR match"]) + "\n")
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Keep this")
    worker = ExportWorker(snapshot)
    outcomes = []
    worker.finished.connect(outcomes.append)
    original_sync = result_export.os.fsync

    def cancel_before_commit(descriptor):
        original_sync(descriptor)
        assert worker.cancel()

    monkeypatch.setattr(result_export.os, "fsync", cancel_before_commit)
    worker.export(str(target))

    assert outcomes == ["Cancelled"]
    assert target.read_bytes() == b"Keep this"
    assert list(tmp_path.glob(".log-analyser-*.tmp")) == []


def test_cancellation_after_commit_boundary_is_declined(tmp_path, snapshot, monkeypatch):
    snapshot.write(json.dumps(["match", "sample.log", 1, "ERROR match"]) + "\n")
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Keep this")
    worker = ExportWorker(snapshot)
    outcomes = []
    worker.finished.connect(outcomes.append)
    original_replace = result_export.os.replace

    def late_cancel(source, destination):
        assert not worker.cancel()
        original_replace(source, destination)

    monkeypatch.setattr(result_export.os, "replace", late_cancel)
    worker.export(str(target))

    assert outcomes == ["Finished"]
    assert target.read_text(encoding="utf-8") == "sample.log:1: ERROR match\n"


def test_temporary_cleanup_failure_is_reported_with_path(tmp_path, snapshot, monkeypatch):
    snapshot.write("invalid JSON\n")
    target = tmp_path / "existing.txt"
    target.write_bytes(b"Keep this")
    original_unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path.name.startswith(".log-analyser-"):
            raise PermissionError("Test temporary-file lock")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        errors, outcomes = run_export(snapshot, target)

    leftovers = list(tmp_path.glob(".log-analyser-*.tmp"))
    assert outcomes == ["Failed"]
    assert target.read_bytes() == b"Keep this"
    assert len(leftovers) == 1
    assert str(leftovers[0]) in errors[-1]
    assert "Could not remove temporary export file" in errors[-1]
    leftovers[0].unlink()


@pytest.mark.parametrize("outcome", ["Failed", "Cancelled"])
@pytest.mark.parametrize("extension", [".txt", ".csv"])
def test_partial_scan_exports_remain_clearly_labelled(tmp_path, snapshot, monkeypatch, outcome, extension):
    log = tmp_path / "partial.log"
    log.write_bytes(b"ERROR match\n" * 3 + b"INFO\x00binary\n")
    worker = LogWorker(snapshot)
    if outcome == "Cancelled":
        monkeypatch.setattr(worker, "_should_stop", lambda: worker._match_count >= 3)
    worker.scan(SearchOptions(str(log), "ERROR"))
    target = tmp_path / ("partial" + extension)

    assert run_export(snapshot, target) == ([], ["Finished"])

    if extension == ".csv":
        with target.open(encoding="utf-8-sig", newline="") as output:
            rows = list(csv.reader(output))
        assert sum(row[0] == "match" for row in rows) == 3
        assert rows[-1][0] == "summary"
        assert rows[-1][3].startswith(f"{outcome} | Matches: 3 | ")
    else:
        lines = target.read_text(encoding="utf-8").splitlines()
        assert sum(line.startswith(f"{log}:") for line in lines) == 3
        assert lines[-1].startswith(f"{outcome} | Matches: 3 | ")
