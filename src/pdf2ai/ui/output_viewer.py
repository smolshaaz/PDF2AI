"""Rendered/source page viewer and local, cancellable PDF/text exports."""
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QByteArray, QMarginsF, QRectF, QSizeF, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMenu,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout)

from pdf2ai.utils.markdown import CSS, iter_pages, plain_text, rendered_html


class LocalBrowser(QTextBrowser):
    def loadResource(self, resource_type, url):
        # Defense in depth: no remote images, file references or CSS can load.
        return QByteArray()


def make_document(markdown):
    document = QTextDocument()
    document.setDefaultFont(QFont("Segoe UI", 11))
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
            writer = QPdfWriter(str(temp))
            writer.setResolution(96)
            writer.setPageSize(QPageSize(QPageSize.A4))
            writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
            writer.setTitle(source.name)
            writer.setCreator("PDF2AI — local rendered Markdown export")
            painter = QPainter(writer)
            if not painter.isActive():
                raise OSError("Could not start PDF export")
            first = True
            width, height = writer.width(), writer.height() - 36
            try:
                for number, markdown in iter_pages(source):
                    document = make_document(markdown)
                    document.setPageSize(QSizeF(width, height))
                    for part in range(document.pageCount()):
                        if stopped():
                            raise InterruptedError()
                        if not first and not writer.newPage():
                            raise OSError("Could not write PDF page")
                        first = False
                        painter.save()
                        painter.setClipRect(QRectF(0, 0, width, height))
                        painter.translate(0, -part * height)
                        document.drawContents(painter, QRectF(0, part * height, width, height))
                        painter.restore()
                        painter.setFont(QFont("Segoe UI", 9))
                        painter.drawText(QRectF(0, height + 8, width, 24), Qt.AlignRight,
                                         f"Source page {number}" + (f" · continued {part + 1}" if part else ""))
                    progress(number)
            finally:
                painter.end()
                del writer
        if stopped():
            raise InterruptedError()
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


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
