# Log Analyser

A local Python/PySide6 desktop tool for searching a file or folder by keyword or regular expression.

## Using the window

- Choose **File** or **Folder** independently, enter a search term, and press **Start**. Cancelling a picker preserves the current selection.
- Matches use `path:line: text`, with physical line numbers starting at 1, for both file and folder scans.
- The status shows Scanning/Cancelling followed by Finished, Cancelled, or Failed, plus counts of matching lines, skipped files, and skipped folders. Counts reset for each scan; warnings and errors are not counted as matches.
- Finished means traversal completed, not that every file was readable. Check skip counts and warning messages for incomplete coverage.
- Controls support Tab/Shift+Tab, Space to activate buttons/checkboxes, and Alt-key mnemonics shown by the native platform. The search field has a visible label; results, progress, and status expose accessible names.

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
