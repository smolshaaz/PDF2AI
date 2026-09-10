"""Local distribution smoke test; only creates synthetic documents in its folder."""
from pathlib import Path


def run(folder):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from pdf2ai.ui.main_window import MainWindow
    import pymupdf
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    digital = folder / "digital.pdf"
    scan = folder / "scan.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=300)
        page.insert_text((40, 70), "INSURANCE POLICY", fontsize=24)
        page.insert_text((40, 130), "Flood damage is excluded.", fontsize=20)
        doc.save(digital)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=300)
        page.insert_image(page.rect, stream=pixels)
        doc.save(scan)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(startup_check=False, output_dir=folder / "PDF2AI Output")
    window.add_files([str(digital), str(scan)])
    window.show()
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    outcome = [1]
    def finished():
        timer.stop()
        try:
            assert window.successes == 2, f"Expected 2 successes, got {window.successes}. Details: {window.details.toPlainText()}"
            assert len(ticks) > 5, "GUI timer did not remain responsive"
            for path in [digital, scan]:
                outputs = list((folder / "PDF2AI Output").glob(f"{path.stem}.ai*.md"))
                assert outputs, f"No output files found for {path.stem} in {folder / 'PDF2AI Output'}"
                output = max(outputs, key=lambda p: p.stat().st_mtime_ns)
                text = output.read_text(encoding="utf-8")
                assert "<!-- PAGE 1 -->" in text
                assert "flood damage is excluded" in text.lower()
            (folder / "PASS.txt").write_text("PASS: packaged Qt window, spawned worker, digital PDF, scanned PDF, local OCR, UTF-8, page markers and responsive GUI.\n", encoding="utf-8")
            outcome[0] = 0
        except Exception as exc:
            import traceback
            (folder / "FAIL.txt").write_text(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", encoding="utf-8")
        app.quit()
    window.start_conversion()
    window.worker.finished.connect(finished)
    app.exec()
    return outcome[0]
