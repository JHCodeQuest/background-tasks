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

## Exporting results

- After a scan stops, choose **Export...** (Alt+X). Use a filename ending in **.txt** or **.csv**; the extension selects the format. The native save dialog handles confirmation before overwriting an existing file.
- Export includes **all recorded matches and warnings**, not just the first 5,000 displayed rows. It uses a snapshot of that scan, so changing or deleting the original logs afterwards does not change the export.
- Text files use UTF-8 and `path:line: text` for matching lines. CSV uses UTF-8 with a BOM and the columns `type,path,line,text`. Record types are `match`, `skipped_file`, `skipped_folder`, `error`, and `summary`.
- Every export ends with the scan outcome and counts. Cancelled and failed scans can export partial results, clearly labelled **Cancelled** or **Failed**. Check warnings and counts before treating an export as complete coverage.
- CSV formula-like string cells are prefixed with an apostrophe for spreadsheet safety, including formulas preceded by whitespace. Raw CSV readers will see that prefix; text exports preserve the matching text, including trailing whitespace.
- Export runs in the background, with cancellation checked between records. Very large records or blocked filesystem calls may delay cancellation. Cancellation accepted before the final commit preserves the existing destination. Once replacement is starting, the status says **Finishing export (cannot cancel now)** and Cancel is disabled. Closing the window waits for the job to stop. Write/commit failures are shown in the status; the snapshot remains available for another attempt.
- Files are written to a sibling temporary file, flushed, and atomically moved into place only on success. Temporary-file cleanup failures are reported with the path rather than hidden. Forced termination or a machine crash can leave a staging file behind; automatic cleanup is only guaranteed during normal execution when the filesystem permits it.
- Full results are temporarily stored on disk until the next scan or window closure. This keeps RAM usage bounded by the existing display policy, but temporary disk use grows with result volume and can exceed the original log size. If snapshot storage fails, the scan reports an error and export is disabled; failure to create a new snapshot preserves the previous scan.
- Exports contain full file paths and matching log content. Keep them private if the logs contain sensitive information. There is no automatic network upload. Files go where you choose: network/synced save folders also hold the sibling temporary file, and the OS temporary directory used for snapshots may itself be redirected or synced. Atomic replacement relies on the destination filesystem; network/sync-specific behaviour has not been verified.

## Large scans

- The first 5,000 match/warning rows are displayed in discovery order, with a clear **Display limited to 5,000 rows** notice when more are found. This shared limit includes skipped-file and skipped-folder warnings.
- Scanning continues after the display limit. Match/skip counts cover everything processed, not just visible rows; a cancelled scan only counts work done before stopping. Some warning details may be hidden by the display limit, but remain available in the full export.
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
