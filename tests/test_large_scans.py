from types import SimpleNamespace

import worker as worker_module
from models import SearchOptions
from worker import LogWorker, MAX_DISPLAY_ROWS, RESULT_BATCH_SIZE


def test_file_progress_is_not_emitted_for_every_line(tmp_path):
    log = tmp_path / "large.log"
    log.write_text("INFO no matches\n" * 20_000, encoding="utf-8")
    worker = LogWorker()
    updates = []
    worker.progress.connect(updates.append)

    worker.scan(SearchOptions(str(log), "ERROR"))

    assert updates[-1] == 100
    assert len(updates) <= 103


def test_large_log_batches_and_caps_rows_without_losing_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    log = tmp_path / "large.log"
    log.write_text("ERROR match\n" * 20_000, encoding="utf-8")
    worker = LogWorker()
    batches, outcomes = [], []
    worker.results_ready.connect(lambda rows, counts: batches.append((rows, counts)))
    worker.finished.connect(outcomes.append)

    worker.scan(SearchOptions(str(log), "ERROR"))

    rows = [row for batch, counts in batches for row in batch]
    assert len(rows) == MAX_DISPLAY_ROWS
    assert rows[0] == f"{log}:1: ERROR match"
    assert rows[-1] == f"{log}:{MAX_DISPLAY_ROWS}: ERROR match"
    assert all(len(batch) <= RESULT_BATCH_SIZE for batch, counts in batches)
    assert len(batches) == MAX_DISPLAY_ROWS // RESULT_BATCH_SIZE + 1
    assert batches[-1] == ([], (20_000, 0, 0))
    assert outcomes == ["Finished"]


def test_pending_rows_are_flushed_before_fatal_error(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    log = tmp_path / "partial.log"
    log.write_bytes(b"ERROR first\nINFO\x00binary\n")
    worker = LogWorker()
    events = []
    worker.results_ready.connect(lambda rows, counts: events.append(("rows", rows, counts)))
    worker.error.connect(lambda message: events.append(("error", message)))
    worker.finished.connect(lambda outcome: events.append(("finished", outcome)))

    worker.scan(SearchOptions(str(log), "ERROR"))

    assert events[0] == ("rows", [f"{log}:1: ERROR first"], (1, 0, 0))
    assert events[1][0] == "error"
    assert events[2] == ("finished", "Failed")


def test_cancellation_flushes_small_pending_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    log = tmp_path / "cancelled.log"
    log.write_text("ERROR match\n" * 100, encoding="utf-8")
    worker = LogWorker()
    monkeypatch.setattr(worker, "_should_stop", lambda: worker._match_count >= 3)
    batches, outcomes = [], []
    worker.results_ready.connect(lambda rows, counts: batches.append((rows, counts)))
    worker.finished.connect(outcomes.append)

    worker.scan(SearchOptions(str(log), "ERROR"))

    assert len(batches) == 1
    assert len(batches[0][0]) == 3
    assert batches[0][1] == (3, 0, 0)
    assert outcomes == ["Cancelled"]


def test_sparse_results_are_reported_while_nonmatching_lines_continue(tmp_path, monkeypatch):
    now = 0.0

    def clock():
        nonlocal now
        now += 0.02
        return now

    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=clock))
    log = tmp_path / "sparse.log"
    log.write_text("ERROR first\n" + "INFO other\n" * 1_000, encoding="utf-8")
    worker = LogWorker()
    batches, progress = [], []
    worker.results_ready.connect(lambda rows, counts: batches.append((rows, counts)))
    worker.progress.connect(progress.append)
    monkeypatch.setattr(worker, "_should_stop", lambda: bool(batches))

    worker.scan(SearchOptions(str(log), "ERROR"))

    assert batches == [([f"{log}:1: ERROR first"], (1, 0, 0))]
    assert 100 not in progress


def test_skipped_warnings_share_the_display_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "MAX_DISPLAY_ROWS", 20)
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    for index in range(120):
        (tmp_path / f"binary-{index}.log").write_bytes(b"ERROR\x00binary\n")
    worker = LogWorker()
    batches = []
    worker.results_ready.connect(lambda rows, counts: batches.append((rows, counts)))

    worker.scan(SearchOptions(str(tmp_path), "ERROR"))

    assert len(batches) == 1
    assert len(batches[0][0]) == 20
    assert all(row.startswith("Skipped file ") for row in batches[0][0])
    assert batches[0][1] == (0, 120, 0)


def test_many_file_folder_scan_is_batched_across_files(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    expected = set()
    for directory in range(30):
        folder = tmp_path / f"folder-{directory}"
        folder.mkdir()
        for index in range(20):
            log = folder / f"sample-{index}.log"
            log.write_text("INFO first\nERROR second\n", encoding="utf-8")
            expected.add(f"{log}:2: ERROR second")
    worker = LogWorker()
    batches = []
    worker.results_ready.connect(lambda rows, counts: batches.append((rows, counts)))

    worker.scan(SearchOptions(str(tmp_path), "ERROR"))

    assert {row for rows, counts in batches for row in rows} == expected
    assert batches[-1][1] == (600, 0, 0)
    assert len(batches) == 3
    assert all(len(rows) == RESULT_BATCH_SIZE for rows, counts in batches)
