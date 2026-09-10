import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(folder: Path):
    """Logs contain controlled operational messages, never engine text/tracebacks."""
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pdf2ai")
    if not logger.handlers:
        handler = RotatingFileHandler(folder / "pdf2ai.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
