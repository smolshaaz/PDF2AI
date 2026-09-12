from PySide6.QtCore import QMimeData, QUrl, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog
from pdf2ai.ui.main_window import MainWindow
from pdf2ai.workers.conversion_worker import ConversionWorker


def test_picker_drop_duplicates_remove(qtbot, digital_pdf, monkeypatch):
    window = MainWindow(startup_check=False)
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([str(digital_pdf)], ""))
    qtbot.mouseClick(window.choose, Qt.LeftButton)
    assert len(window.paths) == 1
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(digital_pdf))])
    enter = QDragEnterEvent(QPoint(15, 15), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(15, 15), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.dropEvent(drop)
    assert len(window.paths) == 1
    window.add_files([str(digital_pdf.parent / "wrong.txt")])
    assert "Only accessible PDF" in window.message.text()
    window.table.selectRow(0)
    qtbot.mouseClick(window.remove, Qt.LeftButton)
    assert not window.paths
    window.start_conversion()
    assert "Add a PDF" in window.message.text()


def test_real_background_conversion_responsive(qtbot, digital_pdf):
    bad = digital_pdf.parent / "corrupt.pdf"
    bad.write_bytes(b"bad")
    window = MainWindow(startup_check=False)
    qtbot.addWidget(window)
    window.show()
    window.add_files([str(bad), str(digital_pdf)])
    ticks = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    qtbot.mouseClick(window.convert, Qt.LeftButton)
    assert not window.convert.isEnabled()
    qtbot.waitUntil(lambda: window.worker is None, timeout=120000)
    timer.stop()
    assert len(ticks) > 10
    assert window.table.item(0, 2).text() == "Failed"
    assert window.table.item(1, 2).text() == "Done — review suggested"
    assert window.open_output.isVisible()
    assert window.convert.isEnabled()


def test_stop_and_close_prompt(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    class BusyWorker:
        stopped = False
        def stop_after_current(self):
            self.stopped = True
        def stop_now(self):
            self.stopped = True
    window = MainWindow(startup_check=False)
    qtbot.addWidget(window)
    window.show()
    fake = BusyWorker()
    window.worker = fake
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(b for b in self.buttons() if b.text() == "Keep running"))
    assert not window.close()
    assert not fake.stopped
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(b for b in self.buttons() if b.text() == "Stop now and close"))
    assert not window.close()
    assert fake.stopped and window.close_pending
    window.worker = None
    assert window.close()


def test_output_dir_default_and_persistence(qtbot, tmp_path, monkeypatch):
    """Output folder defaults sensibly and persists across window recreations."""
    from pathlib import Path
    from PySide6.QtCore import QSettings

    ini = str(tmp_path / "test.ini")

    # Redirect _settings() to an isolated file so the test doesn't touch real
    # user settings and each call returns the same persistent backing store.
    monkeypatch.setattr(
        "pdf2ai.ui.main_window.MainWindow._settings",
        lambda self: QSettings(ini, QSettings.Format.IniFormat),
    )

    window1 = MainWindow(startup_check=False)
    qtbot.addWidget(window1)
    # Default should be an absolute path ending in "PDF2AI Output"
    assert Path(window1._output_dir).is_absolute()
    assert window1._output_dir.name == "PDF2AI Output"
    assert window1.output_dir_edit.text() == str(window1._output_dir)

    # Simulate user browsing to a custom folder
    custom = tmp_path / "my_out"
    custom.mkdir()
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
        lambda *a, **kw: str(custom),
    )
    qtbot.mouseClick(
        window1.browse_output,
        __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.LeftButton,
    )
    assert window1._output_dir == custom
    assert window1.output_dir_edit.text() == str(custom)

    # Second window should restore the saved directory from the same ini file
    window2 = MainWindow(startup_check=False)
    qtbot.addWidget(window2)
    assert window2._output_dir == custom
    assert window2.output_dir_edit.text() == str(custom)


def test_worker_crash_reports_exit_without_pipe_error(qtbot, monkeypatch):
    """A frozen-style worker crash produces one useful event, never BrokenPipe."""
    import sys

    monkeypatch.setattr(
        "pdf2ai.workers.conversion_worker._worker_command",
        lambda job: [sys.executable, "-c", "raise SystemExit(7)"],
    )
    worker = ConversionWorker(check_only=True)
    events = []
    worker.event.connect(events.append)
    with qtbot.waitSignal(worker.finished, timeout=5000):
        worker.start()
    assert events == [
        ("fatal", "Extraction worker stopped unexpectedly (exit code 7).")
    ]
    assert "BrokenPipe" not in repr(events)


def test_stop_now_terminates_busy_process(qtbot, monkeypatch):
    import sys
    monkeypatch.setattr("pdf2ai.workers.conversion_worker._worker_command",
                        lambda job: [sys.executable, "-c", "import time; time.sleep(60)"])
    worker = ConversionWorker()
    events = []
    worker.event.connect(events.append)
    worker.start()
    qtbot.waitUntil(lambda: worker._process is not None, timeout=5000)
    with qtbot.waitSignal(worker.finished, timeout=5000):
        worker.stop_now()
    assert worker._process is None
    assert not events


def test_page_progress(qtbot, digital_pdf):
    window = MainWindow(startup_check=False)
    qtbot.addWidget(window)
    window.add_files([str(digital_pdf)])
    window.batch_rows = [0]
    window.on_event(("started", 0, str(digital_pdf)))
    window.on_event(("progress", 3, 500, "Reading page", 5.0))
    assert window.progress.value() == 3
    assert window.progress.maximum() == 500
    assert "3/500" in window.message.text()
    assert "min left" in window.message.text()
