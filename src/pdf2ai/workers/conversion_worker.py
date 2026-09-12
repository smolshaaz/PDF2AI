"""Run PDF extraction outside the Qt process without multiprocessing pipes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from PySide6.QtCore import QThread, Signal

from pdf2ai.workers.worker_cli import run_batch


def _worker_command(job_path: Path) -> list[str]:
    """Return a command that works in development and in a frozen build."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pdf2ai-worker", str(job_path)]
    return [sys.executable, "-m", "pdf2ai.workers.worker_cli", str(job_path)]


def _process_error(exc: BaseException) -> str:
    """Return useful diagnostics without copying third-party error text."""
    detail = type(exc).__name__
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        detail += f" (Windows error {winerror})"
    return detail


class ConversionWorker(QThread):
    event = Signal(object)

    def __init__(self, paths=(), check_only=False, output_dir: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.check_only = check_only
        self.output_dir = str(output_dir) if output_dir is not None else None
        self.stop_requested = threading.Event()
        self._stop_file: Optional[Path] = None
        self._process: Optional[subprocess.Popen] = None

    def stop_after_current(self):
        self.stop_requested.set()
        stop_file = self._stop_file
        if stop_file is not None:
            try:
                stop_file.touch(exist_ok=True)
            except OSError:
                # The worker also checks the in-memory flag before it starts.
                pass

    def _emit_available_events(self, event_file: Path, offset: int, pending: bytes):
        if not event_file.exists():
            return offset, pending, False
        with event_file.open("rb") as stream:
            stream.seek(offset)
            pending += stream.read()
            offset = stream.tell()
        lines = pending.split(b"\n")
        pending = lines.pop()
        fatal_seen = False
        for line in lines:
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.event.emit(("fatal", "Worker event data was invalid."))
                fatal_seen = True
                continue
            if isinstance(event, list) and event:
                if event[0] == "fatal":
                    fatal_seen = True
                self.event.emit(tuple(event))
            else:
                self.event.emit(("fatal", "Worker returned an invalid event."))
                fatal_seen = True
        return offset, pending, fatal_seen

    def run(self):
        fatal_seen = False
        try:
            with tempfile.TemporaryDirectory(prefix="pdf2ai-worker-") as temp_name:
                temp = Path(temp_name)
                job_file = temp / "job.json"
                event_file = temp / "events.jsonl"
                self._stop_file = temp / "stop"
                job_file.write_text(
                    json.dumps(
                        {
                            "paths": self.paths,
                            "check_only": self.check_only,
                            "output_dir": self.output_dir,
                            "event_file": str(event_file),
                            "stop_file": str(self._stop_file),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                if self.stop_requested.is_set():
                    self._stop_file.touch()

                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                self._process = subprocess.Popen(
                    _worker_command(job_file),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=creationflags,
                )
                offset = 0
                pending = b""
                while self._process.poll() is None:
                    offset, pending, new_fatal = self._emit_available_events(
                        event_file, offset, pending
                    )
                    fatal_seen = fatal_seen or new_fatal
                    time.sleep(0.05)
                offset, pending, new_fatal = self._emit_available_events(
                    event_file, offset, pending
                )
                fatal_seen = fatal_seen or new_fatal
                if pending.strip():
                    self.event.emit(("fatal", "Worker event data was incomplete."))
                    fatal_seen = True
                if self._process.returncode and not fatal_seen:
                    self.event.emit(
                        (
                            "fatal",
                            f"Extraction worker stopped unexpectedly (exit code {self._process.returncode}).",
                        )
                    )
        except BaseException as exc:
            self.event.emit(("fatal", f"Could not start local extraction: {_process_error(exc)}"))
        finally:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self._process = None
            self._stop_file = None
