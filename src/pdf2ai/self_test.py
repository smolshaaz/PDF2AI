"""Local distribution smoke test; only creates synthetic documents in its folder."""
from pathlib import Path


def run(folder):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from pdf2ai.extraction.multilingual_ocr import asset_path
    from pdf2ai.ui.main_window import MainWindow
    import pymupdf
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    digital = folder / "digital.pdf"
    scan = folder / "scan.pdf"
    mixed = folder / "mixed.pdf"
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

    image = QImage(1800, 700, QImage.Format.Format_RGB888)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QColor("black"))
    hand_id = QFontDatabase.addApplicationFont(
        str(asset_path("fonts", "Caveat.ttf"))
    )
    arabic_id = QFontDatabase.addApplicationFont(
        str(asset_path("fonts", "NotoSansArabic.ttf"))
    )
    painter.setFont(QFont(QFontDatabase.applicationFontFamilies(hand_id)[0], 32))
    painter.drawText(
        QRect(50, 40, 800, 200),
        Qt.AlignLeft | Qt.TextWordWrap,
        "Handwritten note: waive the exclusion",
    )
    painter.setFont(QFont(QFontDatabase.applicationFontFamilies(arabic_id)[0], 32))
    painter.drawText(
        QRect(950, 40, 800, 200),
        Qt.AlignRight | Qt.AlignTop | Qt.TextWordWrap,
        "هذه وثيقة تأمين ويجب مراجعة الشروط",
    )
    painter.end()
    mixed_image = folder / "mixed.png"
    if not image.save(str(mixed_image)):
        raise RuntimeError("Could not create mixed-language self-test image")
    with pymupdf.open() as doc:
        page = doc.new_page(width=900, height=350)
        page.insert_image(page.rect, filename=mixed_image)
        doc.save(mixed)

    window = MainWindow(startup_check=False, output_dir=folder / "PDF2AI Output")
    window.add_files([str(digital), str(scan), str(mixed)])
    window.show()
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    outcome = [1]
    def finished():
        timer.stop()
        try:
            assert window.successes == 3, f"Expected 3 successes, got {window.successes}. Details: {window.details.toPlainText()}"
            assert len(ticks) > 5, "GUI timer did not remain responsive"
            extracted = {}
            for path in [digital, scan, mixed]:
                matches = list((folder / "PDF2AI Output").glob(f"{path.stem}.ai*.md"))
                assert matches, f"No output files found for {path.stem} in {folder / 'PDF2AI Output'}"
                output = max(matches, key=lambda p: p.stat().st_mtime_ns)
                text = output.read_text(encoding="utf-8")
                assert "<!-- PAGE 1 -->" in text
                extracted[path.name] = text
            assert "flood damage is excluded" in extracted[digital.name].lower()
            assert "flood damage is excluded" in extracted[scan.name].lower()
            assert "Handwritten note: waive the exclusion" in extracted[mixed.name]
            assert "هذه وثيقة تأمين ويجب مراجعة الشروط" in extracted[mixed.name]
            (folder / "PASS.txt").write_text("PASS: packaged Qt window, file-signalled worker, digital PDF, scanned PDF, local English handwriting and Arabic OCR, UTF-8, page markers and responsive GUI.\n", encoding="utf-8")
            outcome[0] = 0
        except Exception as exc:
            import traceback
            (folder / "FAIL.txt").write_text(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", encoding="utf-8")
        app.quit()
    window.start_conversion()
    window.worker.finished.connect(finished)
    app.exec()
    return outcome[0]
