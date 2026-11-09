# Log Analyser

A local Python/PySide6 desktop tool for searching a file or folder by keyword or regular expression.

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
