from pathlib import Path
import pytest
import pymupdf
from pdf2ai.utils.markdown import iter_pages, plain_text, rendered_html
from pdf2ai.ui.output_viewer import OutputViewer, export_document, make_document


@pytest.fixture
def markdown_file(tmp_path):
    path = tmp_path / "example.ai.md"
    path.write_text("""<!-- PDF2AI
Source: example.pdf
-->

<!-- PAGE 1 -->

# Schedule of benefits

| Benefit | Limit |
| --- | --- |
| Hospital<br>Emergency | 5000 |
| Dental | 1000 |

Exact wording: exclusions remain applicable.

<!-- PAGE 2 -->

## شروط التأمين

هذه وثيقة تأمين ويجب مراجعة الشروط
""", encoding="utf-8")
    return path


def test_view_tables_breaks_and_source(qtbot, markdown_file):
    viewer = OutputViewer(markdown_file, 2)
    qtbot.addWidget(viewer)
    viewer.show()
    assert "<br>" in viewer.source.toPlainText()
    assert "<br>" not in viewer.view.toPlainText()
    assert "Hospital\nEmergency" in viewer.view.toPlainText()
    assert "<table" in viewer.view.document().toHtml()
    assert viewer.source.toPlainText().startswith("<!-- PDF2AI")
    viewer.page.setValue(2)
    assert "هذه وثيقة" in viewer.view.toPlainText()


def test_safe_preview_never_loads_resources():
    rendered = rendered_html('<script>bad()</script>\n\n![x](https://example.com/image.png)\n\n<img src="file:///etc/passwd"><p onclick="bad()">Keep me<br>Here</p>')
    assert "<script" not in rendered and "bad()" not in rendered
    assert "src=" not in rendered and "onclick" not in rendered
    assert "Keep me<br>Here" in rendered


def test_text_preserves_table_cells_and_arabic(markdown_file):
    pages = list(iter_pages(markdown_file))
    assert "".join(text for _, text in pages) == markdown_file.read_text(encoding="utf-8")
    text = plain_text(pages[0][1])
    assert '"Hospital\nEmergency"\t5000' in text
    assert "Dental\t1000" in text
    assert "هذه وثيقة" in plain_text(pages[1][1])
    assert "3. Keep this clause" in plain_text("3. Keep this clause\n4. And this one\n")


def test_pdf_and_text_export(qapp, markdown_file):
    for kind in ("pdf", "txt"):
        target = markdown_file.with_suffix("." + kind)
        export_document(markdown_file, target, kind)
        assert target.stat().st_size > 0
    with pymupdf.open(markdown_file.with_suffix(".pdf")) as document:
        assert document.page_count >= 2
        text = "".join(page.get_text() for page in document)
        assert "Hospital" in text and "5000" in text
        assert "Source page 2" in text
        assert any("\u0600" <= char <= "\u06ff" for char in text)
    assert "[Page 2]" in markdown_file.with_suffix(".txt").read_text(encoding="utf-8")


def test_cancelled_export_preserves_existing(qapp, markdown_file):
    target = markdown_file.with_suffix(".pdf")
    target.write_bytes(b"existing")
    with pytest.raises(InterruptedError):
        export_document(markdown_file, target, "pdf", stopped=lambda: True)
    assert target.read_bytes() == b"existing"
    assert not list(target.parent.glob(".pdf2ai-export-*"))


def test_pdf_export_continues_long_source_page(qapp, tmp_path):
    source = tmp_path / "long.ai.md"
    source.write_text("<!-- PAGE 1 -->\n\n" + "\n\n".join(f"Clause {i}: Exact legal wording." for i in range(130)), encoding="utf-8")
    target = source.with_suffix(".pdf")
    export_document(source, target, "pdf")
    with pymupdf.open(target) as doc:
        assert doc.page_count > 1
        text = "".join(page.get_text() for page in doc)
        assert "Clause 0:" in text and "Clause 129:" in text


def test_background_export(qtbot, markdown_file, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    target = markdown_file.with_suffix(".pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    viewer = OutputViewer(markdown_file, 2)
    qtbot.addWidget(viewer)
    viewer.start_export("pdf")
    assert viewer.worker is not None
    qtbot.waitUntil(lambda: viewer.worker is None, timeout=10000)
    assert target.exists()
    assert viewer.status.text().startswith("Saved:")
