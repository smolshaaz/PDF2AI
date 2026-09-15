import logging
from datetime import datetime
import os
import platform
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(folder: Path):
    """Logs contain controlled operational messages, never engine text/tracebacks."""
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pdf2ai")
    if not logger.handlers:
        handler = RotatingFileHandler(folder / f"pdf2ai-session-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        from pdf2ai import __version__
        logger.info("PDF2AI %s; Python %s; %s; CPUs=%s", __version__, platform.python_version(), platform.platform(), os.cpu_count())
        logger.info("Session started; log file: %s", handler.baseFilename)
    return logger
