from pathlib import Path
import logging
from PySide6.QtCore import Qt, QUrl, QTimer, QSettings, QStandardPaths
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QMessageBox, QPlainTextEdit, QAbstractItemView, QMenu,
    QLineEdit,
)
from pdf2ai.utils.paths import path_key
from pdf2ai.workers.conversion_worker import ConversionWorker


def _default_output_dir() -> Path:
    """Return Documents/PDF2AI Output as the first-run default."""
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(docs) / "PDF2AI Output"


class MainWindow(QMainWindow):
    def __init__(self, logger=None, startup_check=True, output_dir=None):
        super().__init__()
        self.logger = logger or logging.getLogger("pdf2ai")
        self.paths = []
        self.keys = set()
        self.outputs = set()
        self.worker = None
        self.active_row = None
        self.batch_rows = []
        self.close_pending = False
        self.successes = 0
        self.failures = 0
        self.setWindowTitle("PDF2AI")
        self.resize(740, 680)
        self.setMinimumSize(580, 560)
        self.setAcceptDrops(True)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        title = QLabel("PDF2AI")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Your PDF, ready for AI. Entirely on your computer.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        drop = QWidget()
        drop.setObjectName("drop")
        dl = QVBoxLayout(drop)
        dl.setContentsMargins(18, 24, 18, 24)
        label = QLabel("Drop PDF files anywhere here")
        label.setAlignment(Qt.AlignCenter)
        dl.addWidget(label)
        self.choose = QPushButton("Choose PDFs")
        self.choose.clicked.connect(self.choose_files)
        dl.addWidget(self.choose, alignment=Qt.AlignCenter)
        layout.addWidget(drop)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Pages", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setAcceptDrops(False)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.remove = QPushButton("Remove selected")
        self.remove.clicked.connect(self.remove_selected)
        self.clear = QPushButton("Clear queue")
        self.clear.clicked.connect(self.clear_queue)
        controls.addWidget(self.remove)
        controls.addWidget(self.clear)
        controls.addStretch()
        layout.addLayout(controls)

        # ── Output Folder row ──────────────────────────────────────────────
        out_row = QHBoxLayout()
        out_label = QLabel("Output Folder:")
        out_label.setObjectName("fieldlabel")
        out_row.addWidget(out_label)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setObjectName("outputpath")
        out_row.addWidget(self.output_dir_edit, 1)
        self.browse_output = QPushButton("Browse…")
        self.browse_output.clicked.connect(self.browse_output_dir)
        out_row.addWidget(self.browse_output)
        layout.addLayout(out_row)
        # Load persisted directory, falling back to Documents/PDF2AI Output
        self._load_output_dir()
        # ──────────────────────────────────────────────────────────────────

        self.convert = QPushButton("Convert")
        self.convert.setObjectName("primary")
        self.convert.setMinimumHeight(46)
        self.convert.clicked.connect(self.start_conversion)
        layout.addWidget(self.convert)
        self.message = QLabel("Add PDFs to get started.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.ocr_notice = QLabel("Checking local scanned-page support…")
        self.ocr_notice.setWordWrap(True)
        self.ocr_notice.setObjectName("muted")
        layout.addWidget(self.ocr_notice)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.cancel = QPushButton("Stop after current PDF")
        self.cancel.clicked.connect(self.stop)
        self.cancel.hide()
        self.open_output = QPushButton("Open Output Folder")
        self.open_output.clicked.connect(self.open_folders)
        self.open_output.hide()
        self.details_button = QPushButton("Details")
        self.details_button.setCheckable(True)
        bottom.addWidget(self.cancel)
        bottom.addWidget(self.open_output)
        bottom.addStretch()
        bottom.addWidget(self.details_button)
        layout.addLayout(bottom)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(130)
        self.details.hide()
        self.details_button.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details)
        self.setStyleSheet('''
            QWidget { font-family: "Segoe UI", "Helvetica Neue", sans-serif; font-size: 13px; color: #243143; }
            QMainWindow, QWidget#centralwidget { background: #f7f8fa; }
            QLabel#title { font-size: 30px; font-weight: 700; }
            QLabel#muted { color: #627084; }
            QLabel#fieldlabel { font-weight: 600; }
            QWidget#drop { border: 2px dashed #bbc7d6; border-radius: 12px; background: #f3f6fb; }
            QPushButton { background: #fff; border: 1px solid #ccd4df; border-radius: 7px; padding: 8px 15px; }
            QPushButton:hover { background: #eaf0f8; }
            QPushButton:disabled { color: #98a1af; background: #edf0f4; }
            QPushButton#primary { background: #265fd5; color: white; border: none; font-size: 16px; font-weight: 600; }
            QPushButton#primary:disabled { background: #a7bce7; }
            QTableWidget { background: white; alternate-background-color: #f7f9fc; border: 1px solid #dce2ea; border-radius: 6px; gridline-color: #edf0f5; }
            QHeaderView::section { background: #edf1f7; border: 0; padding: 9px; font-weight: 600; }
            QProgressBar { border: none; background: #e2e9f3; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #265fd5; border-radius: 4px; }
            QLineEdit#outputpath { background: #f3f6fb; border: 1px solid #ccd4df; border-radius: 6px; padding: 6px 10px; color: #3a4a5c; }
        ''')
        if startup_check:
            self.set_busy(True)
            QTimer.singleShot(0, self.start_check)
        else:
            self.ocr_notice.setText("")

    # ── Output directory helpers ───────────────────────────────────────────

    def _settings(self) -> QSettings:
        return QSettings("PDF2AI", "PDF2AI")

    def _load_output_dir(self):
        settings = self._settings()
        saved = settings.value("output_dir", None)
        if saved and Path(saved).is_absolute():
            self._output_dir = Path(saved)
        else:
            self._output_dir = _default_output_dir()
        self.output_dir_edit.setText(str(self._output_dir))

    def _save_output_dir(self, path: Path):
        self._output_dir = path
        self.output_dir_edit.setText(str(path))
        settings = self._settings()
        settings.setValue("output_dir", str(path))

    def browse_output_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose Output Folder",
            str(self._output_dir),
        )
        if chosen:
            self._save_output_dir(Path(chosen))

    # ── Worker / conversion ────────────────────────────────────────────────

    def busy(self):
        return self.worker is not None

    def set_busy(self, busy):
        for widget in (self.choose, self.remove, self.clear, self.convert, self.browse_output):
            widget.setEnabled(not busy)

    def start_check(self):
        self.worker = ConversionWorker(check_only=True, parent=self)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.check_finished)
        self.worker.start()

    def check_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_busy(False)
        if self.close_pending:
            self.close()

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose PDFs", "", "PDF files (*.pdf)")
        self.add_files(files)

    def add_files(self, files):
        if self.busy():
            return
        rejected = []
        for file in files:
            path = Path(file).expanduser().resolve()
            if path.suffix.lower() != ".pdf" or not path.is_file():
                rejected.append(path.name)
                continue
            key = path_key(path)
            if key in self.keys:
                continue
            self.keys.add(key)
            self.paths.append(path)
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate((path.name, "—", "Ready")):
                self.table.setItem(row, col, QTableWidgetItem(value))
            self.table.item(row, 0).setToolTip(str(path))
        self.message.setText(f"{len(self.paths)} PDFs in queue." if self.paths else "Add PDFs to get started.")
        if rejected:
            self.message.setText("Only accessible PDF files can be added. Skipped: " + ", ".join(rejected))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.busy():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def remove_selected(self):
        if self.busy():
            return
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.keys.discard(path_key(self.paths.pop(row)))
            self.table.removeRow(row)
        self.message.setText(f"{len(self.paths)} PDFs in queue.")

    def clear_queue(self):
        if self.busy():
            return
        self.paths.clear()
        self.keys.clear()
        self.table.setRowCount(0)
        self.message.setText("Add PDFs to get started.")

    def start_conversion(self):
        if self.busy():
            return
        self.batch_rows = [r for r in range(len(self.paths)) if not self.table.item(r, 2).text().startswith("Done")]
        if not self.batch_rows:
            self.message.setText("Add a PDF to convert." if not self.paths else "All queued PDFs are already converted.")
            return
        self.successes = self.failures = 0
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel.setEnabled(True)
        self.cancel.show()
        self.message.setText("Preparing local conversion…")
        self.worker = ConversionWorker(
            [str(self.paths[r]) for r in self.batch_rows],
            output_dir=self._output_dir,
            parent=self,
        )
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.batch_finished)
        self.worker.start()

    def on_event(self, event):
        kind = event[0]
        if kind == "ocr":
            state = event[1]
            self.ocr_notice.setText("Scanned-page support is ready. Files stay on this computer." if state["available"] else "Scanned pages cannot be processed correctly: local text recognition is unavailable. See Details.")
            self.logger.info("Local OCR check: %s", state["detail"])
            if not state["available"]:
                self.add_detail("Scanned-page support: " + state["detail"])
        elif kind == "started":
            row = self.batch_rows[event[1]]
            self.active_row = row
            self.table.item(row, 2).setText("Processing")
            self.message.setText(f"Processing {self.paths[row].name} — file {event[1] + 1} of {len(self.batch_rows)}")
        elif kind == "result":
            row = self.batch_rows[event[1]]
            result = event[2]
            self.active_row = None
            if result["ok"]:
                self.successes += 1
                self.logger.info("Converted %s: %s pages in %s seconds", self.paths[row], result["pages"], result["seconds"])
                status = "Done — review suggested" if result["warnings"] else "Done"
                self.table.item(row, 2).setText(status)
                self.table.item(row, 2).setForeground(QColor("#926000" if result["warnings"] else "#157347"))
                self.table.item(row, 1).setText(str(result["pages"]))
                self.outputs.add(str(Path(result["output"]).parent))
                self.open_output.show()
                detail = f"{self.paths[row].name} → {result['output']}"
                if result["warnings"]:
                    detail += "\n" + "\n".join(result["warnings"])
                self.table.item(row, 2).setToolTip(detail)
                self.add_detail(detail)
            else:
                self.failures += 1
                self.table.item(row, 2).setText("Failed")
                self.table.item(row, 2).setForeground(QColor("#b42318"))
                detail = f"{self.paths[row].name}: {result['error']}\nTechnical detail: {result['detail']}"
                self.table.item(row, 2).setToolTip(detail)
                self.add_detail(detail)
                self.details_button.setChecked(True)
        elif kind == "fatal":
            self.add_detail("Local processing could not finish: " + event[1])
            self.details_button.setChecked(True)
            if self.active_row is not None:
                self.table.item(self.active_row, 2).setText("Failed")
                self.active_row = None
                self.failures += 1
            self.ocr_notice.setText("Local processing encountered an error. See Details.")

    def add_detail(self, text):
        self.details.appendPlainText(text)
        self.logger.info(text)

    def batch_finished(self):
        stopped = self.worker.stop_requested.is_set()
        self.worker.deleteLater()
        self.worker = None
        self.progress.hide()
        self.cancel.hide()
        self.set_busy(False)
        review = any(self.table.item(r, 2).text() == "Done — review suggested" for r in self.batch_rows)
        message = f"{self.successes} PDFs converted"
        if self.failures:
            message += f" · {self.failures} failed — see Details"
        if stopped:
            message += " · Stopped; remaining PDFs are ready"
        if review:
            message += " · Review suggested — see Details for page numbers"
        self.message.setText(message)
        if self.close_pending:
            self.close()

    def stop(self):
        if self.worker:
            self.worker.stop_after_current()
            self.cancel.setEnabled(False)
            self.message.setText("Stopping after the current PDF finishes safely…")

    def open_folders(self):
        folders = sorted(self.outputs)
        if len(folders) == 1:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folders[0]))
        elif folders:
            menu = QMenu(self)
            for folder in folders:
                menu.addAction(folder, lambda checked=False, p=folder: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
            menu.exec(self.open_output.mapToGlobal(self.open_output.rect().bottomLeft()))

    def closeEvent(self, event):
        if self.busy():
            event.ignore()
            if self.close_pending:
                return
            answer = QMessageBox.question(self, "Processing is active", "Stop after the current PDF finishes safely, then close?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self.close_pending = True
                self.stop()
        else:
            event.accept()
