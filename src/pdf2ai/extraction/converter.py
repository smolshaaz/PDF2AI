"""Page-progress PyMuPDF4LLM extraction; no content rewriting or networking."""
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


def check_ocr(lightweight=False) -> dict:
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
        from pdf2ai.extraction import multilingual_ocr
        # Startup checks files/imports only. Conversion initializes sessions
        # lazily when scanned content is encountered.
        missing = [p.name for p in multilingual_ocr.required_assets() if not p.is_file()]
        if missing:
            raise FileNotFoundError("Missing bundled OCR assets")
        if not lightweight:
            multilingual_ocr.smoke_test()
        return {
            "available": True,
            "detail": "RapidOCR 3.9.2 / ONNX Runtime; batched PP-OCRv6 with selective handwriting/Arabic retries",
        }
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


def convert_pdf(source, ocr_state=None, output_dir: Optional[Path] = None, progress=None, temp_prefix=".pdf2ai-") -> dict:
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
        from pdf2ai.extraction import multilingual_ocr
        def report(done, stage):
            if progress:
                progress(done, count, stage, time.monotonic() - started)
        chunks = []
        uncertain = []
        # Modern Layout analyzes individual pages. Keep the same document open
        # and retain only Markdown/metadata, releasing image/layout data per page.
        for page_number in range(count):
            report(page_number, "Reading page")
            multilingual_ocr.LAST_LOW_CONFIDENCE = False
            if state["available"]:
                def recognize(*args, **kwargs):
                    report(page_number, "Recognizing scanned text")
                    return multilingual_ocr.exec_ocr(*args, **kwargs)
                options["ocr_function"] = recognize
            page_chunks = pymupdf4llm.to_markdown(doc, pages=[page_number], **options)
            if not isinstance(page_chunks, list):
                raise PDFError("The extraction engine returned an unexpected format. Reinstall PDF2AI.")
            chunks.extend({"metadata": c["metadata"], "text": c["text"]} for c in page_chunks)
            if multilingual_ocr.LAST_LOW_CONFIDENCE:
                uncertain.append(page_number + 1)
            report(page_number + 1, "Page complete")
    if not isinstance(chunks, list):
        raise PDFError("The extraction engine returned an unexpected format. Reinstall PDF2AI.")
    ordered, warnings = inspect_chunks(chunks, count)
    if uncertain:
        warnings.append("Some text was difficult to recognize; review pages: " + ", ".join(map(str, uncertain)))
    if not state["available"]:
        warnings.insert(0, "Scanned pages could not be recognized because local OCR is unavailable. This output may be incomplete.")
    folder = output_dir if output_dir is not None else source.parent / "PDF2AI Output"
    folder.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=temp_prefix, suffix=".tmp", dir=folder, delete=False) as stream:
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
