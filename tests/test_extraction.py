from pathlib import Path
import socket
import pytest
import pymupdf
from pdf2ai.extraction.converter import convert_pdf, PDFError

pytestmark = pytest.mark.integration


def test_digital_fidelity_and_blank(digital_pdf, ocr_state, monkeypatch):
    from pdf2ai.extraction import multilingual_ocr
    def unexpected_ocr(*args, **kwargs):
        raise AssertionError("Healthy native text must not be OCRed")
    monkeypatch.setattr(multilingual_ocr, "exec_ocr", unexpected_ocr)
    result = convert_pdf(digital_pdf, ocr_state)
    output = Path(result["output"]).read_text(encoding="utf-8")
    assert result["pages"] == 3
    assert output.count("<!-- PAGE ") == 3
    assert output.index("Coverage section 1") < output.index("Coverage section 2")
    assert output.count("POLICY HEADER") == 2
    assert output.count("Tiny exclusion: flood damage is not covered.") == 2
    assert output.count("FOOTNOTE: All exclusions remain applicable.") == 2
    assert any("text: 3" in w for w in result["warnings"])
    second = convert_pdf(digital_pdf, ocr_state)
    assert Path(second["output"]).name == "policy.ai (2).md"


def test_table(tmp_path, ocr_state):
    path = tmp_path / "table.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), "Schedule of benefits")
        for x in [50, 250, 450]:
            page.draw_line((x, 100), (x, 220))
        for y in [100, 140, 180, 220]:
            page.draw_line((50, y), (450, y))
        for row, values in enumerate([("Benefit", "Limit"), ("Hospital", "5000"), ("Dental", "1000")]):
            for col, value in enumerate(values):
                page.insert_text((60 + col * 200, 125 + row * 40), value)
        doc.save(path)
    output = Path(convert_pdf(path, ocr_state)["output"]).read_text()
    assert "Hospital" in output and "5000" in output and "Dental" in output
    assert "|" in output


def test_scanned_pdf(tmp_path, ocr_state):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as original:
        page = original.new_page(width=600, height=300)
        page.insert_text((40, 80), "INSURANCE POLICY", fontsize=26)
        page.insert_text((40, 140), "Flood damage is excluded.", fontsize=22)
        image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    with pymupdf.open() as scan:
        page = scan.new_page(width=600, height=300)
        page.insert_image(page.rect, stream=image)
        assert not page.get_text().strip()
        scan.save(path)
    result = convert_pdf(path, ocr_state)
    text = Path(result["output"]).read_text()
    assert "INSURANCE" in text.upper()
    assert "flood damage is excluded" in text.lower()
    assert not result["warnings"]


def test_mixed_english_arabic_and_handwriting(tmp_path, ocr_state, qtbot):
    """One scanned page keeps English handwriting and logical-order Arabic."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
    from pdf2ai.extraction.multilingual_ocr import asset_path

    english = "Handwritten note: waive the exclusion"
    arabic = "هذه وثيقة تأمين ويجب مراجعة الشروط"
    image_path = tmp_path / "mixed.png"
    image = QImage(1800, 700, QImage.Format.Format_RGB888)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QColor("black"))
    caveat_id = QFontDatabase.addApplicationFont(
        str(asset_path("fonts", "Caveat.ttf"))
    )
    arabic_id = QFontDatabase.addApplicationFont(
        str(asset_path("fonts", "NotoSansArabic.ttf"))
    )
    painter.setFont(QFont(QFontDatabase.applicationFontFamilies(caveat_id)[0], 32))
    painter.drawText(
        QRect(50, 40, 800, 200), Qt.AlignLeft | Qt.TextWordWrap, english
    )
    painter.setFont(QFont(QFontDatabase.applicationFontFamilies(arabic_id)[0], 32))
    painter.drawText(
        QRect(950, 40, 800, 200), Qt.AlignRight | Qt.AlignTop | Qt.TextWordWrap, arabic
    )
    painter.end()
    assert image.save(str(image_path))

    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as scan:
        page = scan.new_page(width=900, height=350)
        page.insert_image(page.rect, filename=image_path)
        scan.save(path)
    result = convert_pdf(path, ocr_state)
    text = Path(result["output"]).read_text(encoding="utf-8")
    assert english in text
    assert arabic in text
    assert arabic[::-1] not in text


def test_small_scanned_footnote_uses_fidelity_dpi(tmp_path, ocr_state):
    """A 6-point legal line in a realistic text block survives OCR."""
    path = tmp_path / "small-scan.pdf"
    expected = "SMALL SIX POINT FOOTNOTE: POLICY LIMITS APPLY PER OCCURRENCE."
    lines = (
        (5, "TINY FIVE POINT EXCLUSION: WATER SEEPAGE IS NOT COVERED."),
        (6, expected),
        (8, "EIGHT POINT CONDITION: WRITTEN NOTICE IS REQUIRED WITHIN 30 DAYS."),
        (10, "TEN POINT CLAUSE: BENEFITS END WHEN COVERAGE TERMINATES."),
    )
    with pymupdf.open() as original:
        page = original.new_page(width=612, height=792)
        for row, (size, text) in enumerate(lines):
            page.insert_text((45, 70 + row * 55), text, fontsize=size)
        image = page.get_pixmap(dpi=150, alpha=False).tobytes("png")
    with pymupdf.open() as scan:
        page = scan.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=image)
        scan.save(path)
    result = convert_pdf(path, ocr_state)
    text = Path(result["output"]).read_text(encoding="utf-8")
    assert expected in text


def test_corrupt_empty_deleted_encrypted(tmp_path, ocr_state):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a PDF")
    with pytest.raises(PDFError, match="damaged"):
        convert_pdf(bad, ocr_state)
    bad.write_bytes(b"")
    with pytest.raises(PDFError, match="empty"):
        convert_pdf(bad, ocr_state)
    bad.unlink()
    with pytest.raises(FileNotFoundError):
        convert_pdf(bad, ocr_state)
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(bad, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(PDFError, match="password"):
        convert_pdf(bad, ocr_state)


def test_missing_ocr_keeps_flagged_output(digital_pdf):
    result = convert_pdf(digital_pdf, {"available": False})
    assert Path(result["output"]).is_file()
    assert any("OCR is unavailable" in w for w in result["warnings"])


def test_write_failure_cleans_temp(digital_pdf, ocr_state, monkeypatch):
    import pdf2ai.extraction.converter as module
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(OSError):
        convert_pdf(digital_pdf, ocr_state)
    assert not list((digital_pdf.parent / "PDF2AI Output").iterdir())


def test_network_disabled(ocr_state):
    with socket.socket() as connection:
        with pytest.raises(PermissionError, match="network access disabled"):
            connection.connect(("127.0.0.1", 443))


def test_output_permission_failure(digital_pdf, ocr_state, monkeypatch):
    import pdf2ai.extraction.converter as module
    from pdf2ai.extraction.converter import friendly_error
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", denied)
    with pytest.raises(PermissionError) as caught:
        convert_pdf(digital_pdf, ocr_state)
    assert "permissions" in friendly_error(caught.value)
    assert not list((digital_pdf.parent / "PDF2AI Output").iterdir())


def test_ocr_missing_model_report(monkeypatch):
    from pdf2ai.extraction.converter import check_ocr
    actual = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: False if p.suffix == ".onnx" else actual(p))
    result = check_ocr()
    assert not result["available"]
    assert "models are missing" in result["detail"]
