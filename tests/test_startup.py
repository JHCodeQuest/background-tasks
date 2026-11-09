import os
from pathlib import Path
import subprocess
import sys


def test_documented_script_starts_and_closes_real_window(tmp_path):
    hook = tmp_path / "sitecustomize.py"
    hook.write_text(
        """from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

original_exec = QApplication.exec

def checked_exec(self):
    assert any(
        window.windowTitle() == "Log Analyser" and window.isVisible()
        for window in self.topLevelWidgets()
    ), "The application did not show its main window"
    print("Launch smoke test passed", flush=True)
    QTimer.singleShot(0, self.closeAllWindows)
    return original_exec()

QApplication.exec = checked_exec
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "log_analyser/main.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Launch smoke test passed" in result.stdout
