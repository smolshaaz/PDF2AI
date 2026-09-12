import sys


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QStandardPaths
    from pathlib import Path
    from pdf2ai.ui.main_window import MainWindow
    from pdf2ai.utils.logging import configure_logging
    app = QApplication(sys.argv)
    app.setApplicationName("PDF2AI")
    app.setOrganizationName("PDF2AI")
    try:
        logger = configure_logging(Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)))
    except OSError:
        import logging
        logger = logging.getLogger("pdf2ai")
        logger.addHandler(logging.NullHandler())
    window = MainWindow(logger)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
