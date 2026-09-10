import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
import pymupdf


@pytest.fixture
def digital_pdf(tmp_path):
    path = tmp_path / "policy.pdf"
    with pymupdf.open() as doc:
        for number in range(2):
            page = doc.new_page()
            page.insert_text((60, 30), "POLICY HEADER - retain every page", fontsize=9)
            page.insert_text((60, 90), f"Coverage section {number + 1}. Exact wording must be preserved.", fontsize=12)
            page.insert_text((60, 130), "Tiny exclusion: flood damage is not covered.", fontsize=4)
            page.insert_text((60, 812), "FOOTNOTE: All exclusions remain applicable.", fontsize=6)
        doc.new_page()
        doc.save(path)
    return path


@pytest.fixture(scope="session")
def ocr_state():
    from pdf2ai.extraction.converter import check_ocr
    state = check_ocr()
    assert state["available"], state
    return state
