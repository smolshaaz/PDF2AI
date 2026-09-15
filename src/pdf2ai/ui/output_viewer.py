"""Rendered/source page viewer and local, cancellable PDF/text exports."""
import os
from pathlib import Path
import tempfile
import textwrap

from PySide6.QtCore import QByteArray, QMarginsF, QRectF, QSizeF, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMenu,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout)

from pdf2ai.utils.markdown import CSS, iter_pages, plain_text, rendered_html


def document_font():
    """Use the bundled Unicode font so exported PDFs retain searchable text."""
    font_path = Path(__file__).parents[1] / "assets" / "fonts" / "NotoSansArabic.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id)
    return QFont(families[0] if families else "Arial", 11)


class LocalBrowser(QTextBrowser):
    def loadResource(self, resource_type, url):
        # Defense in depth: no remote images, file references or CSS can load.
        return QByteArray()


def make_document(markdown):
    document = QTextDocument()
    document.setDefaultFont(document_font())
    document.setDefaultStyleSheet(CSS)
    document.setHtml(rendered_html(markdown))
    return document


def export_document(source, target, kind, stopped=lambda: False, progress=lambda n: None):
    """Atomic export; never overwrite the source Markdown or original PDF."""
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("Choose a different output file.")
    descriptor, temp_name = tempfile.mkstemp(prefix=".pdf2ai-export-", suffix="." + kind, dir=target.parent)
    os.close(descriptor)
    temp = Path(temp_name)
    try:
        if kind == "txt":
            with temp.open("w", encoding="utf-8", newline="\n") as stream:
                for number, markdown in iter_pages(source):
                    if stopped():
                        raise InterruptedError()
                    stream.write(f"\n[Page {number}]\n\n" + plain_text(markdown))
                    progress(number)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            _export_searchable_pdf(source, temp, stopped, progress)
        if stopped():
            raise InterruptedError()
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _export_searchable_pdf(source, target, stopped, progress):
    """Create a local reading PDF with reliable Unicode text extraction.

    Qt's QPdfWriter can emit an invalid character map on Windows for embedded
    fonts. PyMuPDF writes the Unicode map directly, keeping exported English
    and Arabic searchable and copyable across platforms.
    """
    import pymupdf

    font_path = Path(__file__).parents[1] / "assets" / "fonts" / "NotoSansArabic.ttf"
    document = pymupdf.open()
    page_rect = pymupdf.paper_rect("a4")
    text_rect = pymupdf.Rect(42, 42, page_rect.width - 42, page_rect.height - 52)
    footer_rect = pymupdf.Rect(42, page_rect.height - 35, page_rect.width - 42, page_rect.height - 16)

    def wrapped_lines(text):
        for line in text.splitlines() or [""]:
            if not line:
                yield ""
            else:
                yield from textwrap.wrap(line.expandtabs(4), width=88, replace_whitespace=False,
                                           break_long_words=False, break_on_hyphens=False) or [""]

    try:
        for number, markdown in iter_pages(source):
            lines = list(wrapped_lines(plain_text(markdown)))
            for part, start in enumerate(range(0, max(1, len(lines)), 48), start=1):
                if stopped():
                    raise InterruptedError()
                page = document.new_page(width=page_rect.width, height=page_rect.height)
                body = "\n".join(lines[start:start + 48])
                # Base-14 Helvetica gives clean searchable Latin text. Switch
                # to the embedded Unicode font only for pages carrying Arabic.
                body_font = "pdf2aiunicode" if any("\u0600" <= char <= "\u06ff" for char in body) else "helv"
                if body_font == "pdf2aiunicode":
                    page.insert_font(fontname=body_font, fontfile=str(font_path))
                page.insert_textbox(text_rect, body, fontname=body_font,
                                    fontsize=10, lineheight=1.28, color=(0.14, 0.19, 0.26))
                label = f"Source page {number}" + (f" · continued {part}" if part > 1 else "")
                page.insert_textbox(footer_rect, label, fontname="helv", fontsize=8,
                                    align=pymupdf.TEXT_ALIGN_RIGHT, color=(0.32, 0.38, 0.47))
            progress(number)
        document.set_metadata({"title": source.name, "author": "PDF2AI", "creator": "PDF2AI local export"})
        document.save(str(target), garbage=4, deflate=True)
    finally:
        document.close()


class ExportWorker(QThread):
    completed = Signal(bool, str)
    progress = Signal(int)

    def __init__(self, source, target, kind, parent=None):
        super().__init__(parent)
        self.source, self.target, self.kind = source, target, kind

    def run(self):
        try:
            export_document(self.source, self.target, self.kind, self.isInterruptionRequested, self.progress.emit)
            self.completed.emit(True, str(self.target))
        except InterruptedError:
            self.completed.emit(False, "Export stopped. No incomplete file was saved.")
        except Exception as exc:
            self.completed.emit(False, f"Export could not finish ({type(exc).__name__}). Check free space and folder permissions.")


class OutputViewer(QDialog):
    def __init__(self, path, page_count, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.worker = None
        self.close_pending = False
        self.setWindowTitle(self.path.name + " — PDF2AI")
        self.resize(1000, 760)
        self.setMinimumSize(680, 480)
        self.setStyleSheet("""
            QDialog { background: #f4f7fb; color: #243143; }
            QLabel { color: #243143; }
            QTextBrowser, QPlainTextEdit { background: white; color: #243143; selection-background-color: #265fd5; selection-color: white; border: 1px solid #d4dce7; padding: 16px; }
            QPushButton, QSpinBox { background: white; color: #243143; padding: 7px 12px; border: 1px solid #cbd5e1; border-radius: 5px; }
            QTabBar::tab { background: #eaf0f8; color: #243143; padding: 10px 24px; }
            QTabBar::tab:selected { background: white; color: #265fd5; }
        """)
        layout = QVBoxLayout(self)
        title = QLabel(self.path.name)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        title.setToolTip(str(self.path))
        layout.addWidget(title)
        bar = QHBoxLayout()
        previous, following = QPushButton("Previous"), QPushButton("Next")
        self.page = QSpinBox()
        self.page.setRange(1, page_count)
        previous.clicked.connect(self.page.stepDown)
        following.clicked.connect(self.page.stepUp)
        for widget in (previous, QLabel("Page"), self.page, QLabel(f"of {page_count}"), following):
            bar.addWidget(widget)
        bar.addStretch()
        for label, target in (("Open file", self.path), ("Open folder", self.path.parent)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, p=target: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            bar.addWidget(button)
        self.export = QPushButton("Export…")
        menu = QMenu(self.export)
        menu.addAction("PDF — formatted reading copy", lambda: self.start_export("pdf"))
        menu.addAction("Text — plain text with table columns", lambda: self.start_export("txt"))
        self.export.setMenu(menu)
        bar.addWidget(self.export)
        layout.addLayout(bar)
        self.tabs = QTabWidget()
        self.view = LocalBrowser()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setFont(QFont("Menlo", 11))
        self.tabs.addTab(self.view, "View")
        self.tabs.addTab(self.source, "Source")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("Rendered view changes presentation only. Source shows the saved Markdown for this page.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, page_count)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.stop_export = QPushButton("Stop export")
        self.stop_export.clicked.connect(lambda: self.worker.requestInterruption() if self.worker else None)
        self.stop_export.hide()
        layout.addWidget(self.stop_export)
        self.page.valueChanged.connect(self.show_page)
        self.show_page(1)

    def show_page(self, number):
        try:
            markdown = next((text for page, text in iter_pages(self.path) if page == number), "")
            self.source.setPlainText(markdown)
            self.view.document().setDefaultStyleSheet(CSS)
            self.view.setHtml(rendered_html(markdown))
        except OSError:
            self.status.setText("This output file has moved or cannot be opened.")

    def start_export(self, kind):
        if self.worker:
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export reading copy" if kind == "pdf" else "Export plain text",
            str(self.path.with_suffix("." + kind)), f"{kind.upper()} files (*.{kind})")
        if not target:
            return
        if not target.lower().endswith("." + kind):
            target += "." + kind
        self.worker = ExportWorker(self.path, Path(target), kind, self)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.completed.connect(lambda ok, message: self.status.setText("Saved: " + message if ok else message))
        self.worker.finished.connect(self.export_finished)
        self.progress.setValue(0)
        self.progress.show()
        self.stop_export.show()
        self.export.setEnabled(False)
        self.status.setText("Exporting locally…")
        self.worker.start()

    def export_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.export.setEnabled(True)
        self.stop_export.hide()
        self.progress.hide()
        if self.close_pending:
            self.close()

    def closeEvent(self, event):
        if self.worker:
            self.close_pending = True
            self.worker.requestInterruption()
            event.ignore()
        else:
            event.accept()
