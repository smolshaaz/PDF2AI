"""File-based extraction worker used by source and frozen builds."""
import contextlib
import json
import os
from pathlib import Path
import sys


def run_batch(paths, emit, stopped, convert):
    """Convert sequentially; one failed document never aborts the queue."""
    for index, path in enumerate(paths):
        if stopped():
            break
        emit(("started", index, path))
        try:
            result = convert(path)
        except Exception as exc:
            from pdf2ai.extraction.converter import friendly_error

            result = {
                "ok": False,
                "error": friendly_error(exc),
                "detail": f"{type(exc).__module__}.{type(exc).__name__}",
            }
        emit(("result", index, result))


def append_event(event_file: Path, event) -> None:
    """Append one complete UTF-8 JSON event and make it visible immediately."""
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with event_file.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def run_job(job_path) -> int:
    """Execute one local job. The job contains paths and status only."""
    job_path = Path(job_path)
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        event_file = Path(job["event_file"])
        stop_file = Path(job["stop_file"])
        paths = list(job.get("paths", ()))
        check_only = bool(job.get("check_only", False))
        output_dir = Path(job["output_dir"]) if job.get("output_dir") else None
    except Exception:
        return 2

    # Some dependencies print diagnostics and can include OCR text. Suppress
    # them before importing any PDF or OCR package.
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                from pdf2ai.extraction.converter import check_ocr, convert_pdf

                state = check_ocr()
                append_event(event_file, ("ocr", state))
                if not check_only:
                    run_batch(
                        paths,
                        lambda event: append_event(event_file, event),
                        stop_file.exists,
                        lambda path: convert_pdf(path, state, output_dir),
                    )
                return 0
            except BaseException as exc:
                # Exception messages can contain document content. Send the type only.
                try:
                    append_event(
                        event_file,
                        ("fatal", f"{type(exc).__module__}.{type(exc).__name__}"),
                    )
                except Exception:
                    pass
                return 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        return 2
    return run_job(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
