import pytest

import main_window
from models import SearchOptions
from worker import LogWorker


@pytest.mark.parametrize(
    "pattern,use_regex,case_sensitive,expected",
    [
        ("ERROR", False, True, ["ERROR first"]),
        ("error", False, True, ["error second"]),
        ("ErRoR", False, False, ["ERROR first", "error second"]),
        ("[", False, False, ["[INFO] third"]),
        ("(", False, False, ["value(foo)"]),
        ("*", False, True, ["wild*card"]),
        (".", False, False, ["file.log"]),
        (r"^ERROR\b", True, True, ["ERROR first"]),
        (r"^ERROR\b", True, False, ["ERROR first", "error second"]),
        (r"\[INFO\]", True, True, ["[INFO] third"]),
        ("missing", False, False, []),
        ("^missing$", True, False, []),
    ],
)
def test_search_matches(tmp_path, pattern, use_regex, case_sensitive, expected):
    log = tmp_path / "sample.log"
    lines = ["ERROR first", "error second", "[INFO] third", "value(foo)", "wild*card", "file.log"]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    worker = LogWorker()
    matches, errors, finished = [], [], []
    worker.match_found.connect(matches.append)
    worker.error.connect(errors.append)
    worker.finished.connect(finished.append)

    worker.scan(SearchOptions(str(log), pattern, use_regex, case_sensitive))

    assert errors == []
    assert matches == [f"{log}:{lines.index(line) + 1}: {line}" for line in expected]
    assert finished == ["Finished"]


@pytest.mark.parametrize("pattern", ["[", "(", "*"])
def test_invalid_regex_is_rejected_before_starting_thread(app, monkeypatch, pattern):
    def unexpected_thread():
        pytest.fail("Invalid regex must be rejected before creating a thread")

    monkeypatch.setattr(main_window, "QThread", unexpected_thread)
    window = main_window.MainWindow()
    try:
        window.selected_path = "sample.log"
        window.pattern_input.setText(pattern)
        window.regex_checkbox.setChecked(True)

        window.start_scan()

        assert window.thread is None
        assert window.worker is None
        assert window.start_button.isEnabled()
        assert window.results.count() == 1
        assert window.results.item(0).text().startswith("Invalid regular expression: ")
    finally:
        window.close()
