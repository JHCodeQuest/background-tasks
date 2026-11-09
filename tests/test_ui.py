import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest

import main_window
from models import SearchOptions
from worker import LogWorker


@pytest.fixture
def window(app):
    window = main_window.MainWindow()
    yield window
    window.close()


def test_initial_labels_and_button_states(window):
    assert window.case_checkbox.text().replace("&", "") == "Case sensitive"
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert not window.export_button.isEnabled()
    assert window.search_label.buddy() is window.pattern_input
    assert window.status_label.text() == "Ready | Matches: 0 | Skipped files: 0 | Skipped folders: 0"


@pytest.mark.parametrize("folder", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_file_and_folder_pickers_are_independent(window, monkeypatch, folder, cancel):
    window.selected_path = "previous.log"
    window.path_label.setText("previous.log")
    selected = "" if cancel else "selected-folder" if folder else "selected.log"

    def unexpected_dialog(*args, **kwargs):
        pytest.fail("Opening one picker must not open the other")

    monkeypatch.setattr(
        main_window.QFileDialog, "getOpenFileName",
        unexpected_dialog if folder else lambda *args: (selected, ""),
    )
    monkeypatch.setattr(
        main_window.QFileDialog, "getExistingDirectory",
        (lambda *args: selected) if folder else unexpected_dialog,
    )

    button = window.pick_folder_button if folder else window.pick_file_button
    button.click()

    assert window.selected_path == (selected or "previous.log")
    assert window.path_label.text() == (selected or "previous.log")


def test_keyboard_tab_order_and_checkbox_activation(app, window):
    window.show()
    window.activateWindow()
    app.processEvents()
    window.pattern_input.setFocus()
    controls = [
        window.pattern_input, window.regex_checkbox, window.case_checkbox,
        window.pick_file_button, window.pick_folder_button, window.start_button,
        window.results,
    ]

    for control in controls:
        assert window.focusWidget() is control
        QTest.keyClick(control, Qt.Key.Key_Tab)
    assert window.focusWidget() is window.pattern_input

    window.regex_checkbox.setFocus()
    QTest.keyClick(window.regex_checkbox, Qt.Key.Key_Space)
    assert window.regex_checkbox.isChecked()


def test_controls_expose_accessible_names(window):
    expected = [
        (window.pattern_input, "Search term"),
        (window.pick_file_button, "Choose File"),
        (window.pick_folder_button, "Choose Folder"),
        (window.regex_checkbox, "Regex"),
        (window.case_checkbox, "Case sensitive"),
        (window.start_button, "Start"),
        (window.cancel_button, "Cancel"),
        (window.export_button, "Export..."),
        (window.progress, "Scan progress"),
        (window.results, "Search results"),
        (window.status_label, "Scan status"),
    ]
    for control, name in expected:
        interface = QAccessible.queryAccessibleInterface(control)
        assert interface.text(QAccessible.Text.Name) == name


def test_matches_include_file_and_physical_line_number(tmp_path):
    log = tmp_path / "sample.log"
    log.write_text("INFO first\n\nERROR third\nERROR fourth\n", encoding="utf-8")
    worker = LogWorker()
    matches = []
    worker.results_ready.connect(lambda rows, counts: matches.extend(rows))

    worker.scan(SearchOptions(str(log), "ERROR"))

    assert matches == [f"{log}:3: ERROR third", f"{log}:4: ERROR fourth"]
