"""Whole-document PyMuPDF4LLM extraction; no content rewriting or networking."""
import errno
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Optional
from pdf2ai.extraction.validation import inspect_chunks, markdown_parts
from pdf2ai.utils.paths import publish

_OFFLINE = False


def enforce_offline():
    """Fail closed if a Python dependency attempts network access/model downloads."""
    global _OFFLINE
    if not _OFFLINE:
        def audit(event, args):
            if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg"}:
                raise PermissionError("PDF2AI processing has network access disabled")
        sys.addaudithook(audit)
        _OFFLINE = True


def check_ocr() -> dict:
    enforce_offline()
    try:
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        import rapidocr
        models = Path(rapidocr.__file__).parent / "models"
        required = ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
        missing = [name for name in required if not (models / name).is_file()]
        if missing:
            return {"available": False, "detail": "Bundled OCR models are missing. Reinstall PDF2AI. Missing: " + ", ".join(missing)}
        from pymupdf4llm.ocr import rapidocr_api
        if rapidocr_api.full_ocr is None:
            raise ImportError("RapidOCR adapter unavailable")
        # Runs a tiny blank image through the official adapter backend. This checks
        # models, native ONNX libraries and actual inference without any user data.
        import numpy as np
        rapidocr_api.full_ocr(np.full((32, 32, 3), 255, dtype=np.uint8))
        return {"available": True, "detail": "RapidOCR 3.9.2 / ONNX Runtime; bundled local models"}
    except Exception as exc:
        # Exception strings from OCR may include recognized content. Never log them.
        return {"available": False, "detail": f"{type(exc).__module__}.{type(exc).__name__} while initializing local OCR. Reinstall the complete PDF2AI distribution (including its runtime and models)."}


class PDFError(Exception):
    pass


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, PDFError):
        return str(exc)  # Only our own fixed, content-free messages.
    if isinstance(exc, FileNotFoundError):
        return "The file is no longer available. Check its location and add it again."
    if isinstance(exc, PermissionError):
        return "The file or output folder cannot be accessed. Check permissions and whether it is locked."
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return "There is not enough disk space to save the output. Free some space and retry."
        return "The file could not be read or saved. Check the drive, free space and folder permissions."
    return "This PDF could not be converted. It may be damaged or use an unsupported format. Try opening it in a PDF viewer."


def convert_pdf(source, ocr_state=None, output_dir: Optional[Path] = None) -> dict:
    enforce_offline()
    import pymupdf4llm
    import pymupdf
    source = Path(source).resolve()
    started = time.monotonic()
    if source.suffix.lower() != ".pdf":
        raise PDFError("Please choose a PDF file.")
    # Opening explicitly distinguishes missing files from unreadable PDF data.
    with source.open("rb"):
        pass
    try:
        doc = pymupdf.open(source)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
        raise PDFError("This PDF is empty or damaged and cannot be opened.") from exc
    with doc:
        if not doc.is_pdf:
            raise PDFError("This file is not a valid PDF.")
        if doc.needs_pass:
            raise PDFError("This PDF is password-protected. Save an unlocked copy in your PDF application and try again.")
        count = doc.page_count
        if not count:
            raise PDFError("This PDF has no pages.")
        state = check_ocr() if ocr_state is None else ocr_state
        # Explicit activation fails if Layout is unavailable; never silently fall
        # back to the legacy engine, where OCR/settings have different behavior.
        pymupdf4llm.use_layout(True)
        options = dict(
            page_chunks=True,
            use_ocr=state["available"],
            force_ocr=False,
            # A generated 150-DPI scan lost a complete 6-point legal footnote
            # at 150 OCR DPI and recovered it at 300. Native-text pages still
            # skip OCR through PyMuPDF4LLM's selective page analysis.
            ocr_dpi=300,
            header=True,
            footer=True,
            force_text=True,
            write_images=False,
            embed_images=False,
            show_progress=False,
        )
        if state["available"]:
            from pymupdf4llm.ocr import rapidocr_api
            options["ocr_function"] = rapidocr_api.exec_ocr
        chunks = pymupdf4llm.to_markdown(doc, **options)
    if not isinstance(chunks, list):
        raise PDFError("The extraction engine returned an unexpected format. Reinstall PDF2AI.")
    ordered, warnings = inspect_chunks(chunks, count)
    if not state["available"]:
        warnings.insert(0, "Scanned pages could not be recognized because local OCR is unavailable. This output may be incomplete.")
    folder = output_dir if output_dir is not None else source.parent / "PDF2AI Output"
    folder.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=".pdf2ai-", suffix=".tmp", dir=folder, delete=False) as stream:
            temp = Path(stream.name)
            for part in markdown_parts(source.name, count, ordered):
                stream.write(part)
            stream.flush()
            os.fsync(stream.fileno())
        output = publish(temp, source, output_dir)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return {"ok": True, "output": str(output), "pages": count, "warnings": warnings,
            "seconds": round(time.monotonic() - started, 2)}
