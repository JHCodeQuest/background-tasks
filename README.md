# Log Analyser

A local Python/PySide6 desktop tool for searching a file or folder by keyword or regular expression.

## Setup and launch (Windows)

Tested with Python 3.11, PySide6 6.12.0, and pytest 9.2.0 on Windows. Install Python 3.11 with the Python launcher (`py`); running the application requires a desktop session. Other operating systems and Python versions have not been verified.

Run these commands from the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\log_analyser\main.py
```

No virtual-environment activation or external services are needed. Use the script command above; a packaged executable or module entry point is not provided.

## Development and tests

The development requirements include the runtime requirements, so they can also be installed into a fresh virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest
git diff --check
```

Tests automatically use Qt's offscreen platform, synthetic files, and isolated temporary directories. They cover matching, scanning errors, worker/thread lifecycle, keyboard/accessibility semantics, capped/batched output, large scans, and a real startup/shutdown smoke test. They do not replace native file-dialog or screen-reader testing.

The GitHub Actions **Tests** workflow runs these checks on Windows/Python 3.11 for pushes, pull requests, and manual runs. Dependency versions are pinned in `requirements.txt` and `requirements-dev.txt`; update them intentionally and rerun the suite.

## Using the window

- Choose **File** or **Folder** independently, enter a search term, and press **Start**. Cancelling a picker preserves the current selection.
- Matches use `path:line: text`, with physical line numbers starting at 1, for both file and folder scans.
- The status shows Scanning/Cancelling followed by Finished, Cancelled, or Failed, plus counts of matching lines, skipped files, and skipped folders. Counts reset for each scan; warnings and errors are not counted as matches.
- Finished means traversal completed, not that every file was readable. Check skip counts and warning messages for incomplete coverage.
- Controls support Tab/Shift+Tab, Space to activate buttons/checkboxes, and Alt-key mnemonics shown by the native platform. The search field has a visible label; results, progress, and status expose accessible names.

## Large scans

- The first 5,000 match/warning rows are displayed in discovery order, with a clear **Display limited to 5,000 rows** notice when more are found. This shared limit includes skipped-file and skipped-folder warnings.
- Scanning continues after the display limit. Match/skip counts cover everything processed, not just visible rows; a cancelled scan only counts work done before stopping. Some warning details may be hidden by the display limit.
- Fatal errors are always displayed, even after the row limit. They may add one extra row and are not included in match/skip counts.
- Results are delivered in batches of up to 200 rows or roughly every 100 ms as scanning advances; pending rows are flushed on completion, cancellation, or error. After the cap, only periodic count updates are sent. File progress updates are sent only when the percentage changes.
- Files are still read one line at a time. The row cap is not a fixed RAM limit: exceptionally long individual lines can still consume significant memory. Cancellation still waits for an in-progress filesystem operation or regex evaluation to return.

## Supported input

- Regular text files encoded as UTF-8, with or without a UTF-8 BOM.
- Invalid UTF-8 and NUL-containing data are reported, not silently decoded. Other encodings, including UTF-16, are not supported.
- Binary detection is deliberately limited to NUL bytes and invalid UTF-8; it is not a general file-format detector.

## Folder scans

- Files are scanned recursively as they are discovered, without collecting the whole tree first. All file extensions are considered; directory symlinks are not followed.
- Unreadable, deleted, unsupported, or non-regular files and inaccessible subfolders are reported as skipped; other files continue scanning.
- An invalid or unreadable selected file or root folder produces an error.
- Matches already displayed before a later read/encoding failure are retained and may be partial. A skipped-file message does not mean that no earlier lines were searched.
- File scans show byte-based progress. Folder scans show indeterminate progress and reach 100% on completion, including empty folders and scans with skipped files.
- Cancellation is checked between folders, files, and lines. It does not force completion to 100%. An in-progress filesystem operation, directory listing, or regex evaluation cannot be interrupted; closing the window waits for the worker to stop safely.
