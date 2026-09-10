"""PDF child entry point. Deliberately imports no Qt modules."""
from pathlib import Path
from typing import Optional


def run_batch(paths, emit, stopped, convert):
    """One failure never prevents the next document from running."""
    for index, path in enumerate(paths):
        if stopped():
            break
        emit(("started", index, path))
        try:
            result = convert(path)
        except Exception as exc:
            from pdf2ai.extraction.converter import friendly_error
            result = {"ok": False, "error": friendly_error(exc), "detail": f"{type(exc).__module__}.{type(exc).__name__}"}
        emit(("result", index, result))


def child_entry(connection, stop_event, paths, check_only, output_dir: Optional[str] = None):
    # Suppress third-party stdout/stderr: libraries may print recognized text.
    import os
    import contextlib
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            from pdf2ai.extraction.converter import convert_pdf, check_ocr
            out_path = Path(output_dir) if output_dir else None
            state = check_ocr()
            connection.send(("ocr", state))
            if not check_only:
                run_batch(
                    paths,
                    connection.send,
                    stop_event.is_set,
                    lambda p: convert_pdf(p, state, out_path),
                )
        except Exception as exc:
            connection.send(("fatal", f"{type(exc).__module__}.{type(exc).__name__}"))
        finally:
            connection.close()
