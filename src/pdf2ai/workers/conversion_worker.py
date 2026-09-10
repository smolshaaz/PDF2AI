"""Qt only coordinates: all PDF/native-library work runs in a spawned process."""
import multiprocessing as mp
import threading
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QThread, Signal


from pdf2ai.workers.process_job import child_entry, run_batch


class ConversionWorker(QThread):
    event = Signal(object)

    def __init__(self, paths=(), check_only=False, output_dir: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.check_only = check_only
        # Serialise to str for cross-process pickle safety.
        self.output_dir = str(output_dir) if output_dir is not None else None
        self.stop_requested = threading.Event()
        self._process_stop = None

    def stop_after_current(self):
        self.stop_requested.set()
        if self._process_stop is not None:
            self._process_stop.set()

    def run(self):
        context = mp.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        stop = context.Event()
        self._process_stop = stop
        if self.stop_requested.is_set():
            stop.set()
        process = context.Process(
            target=child_entry,
            args=(send, stop, self.paths, self.check_only, self.output_dir),
        )
        try:
            process.start()
            send.close()
            while True:
                if self.stop_requested.is_set():
                    stop.set()
                if receive.poll(0.1):
                    try:
                        self.event.emit(receive.recv())
                    except EOFError:
                        break
                elif not process.is_alive():
                    break
            process.join()
            if process.exitcode:
                self.event.emit(("fatal", f"Extraction process exited with code {process.exitcode}."))
        except Exception as exc:
            self.event.emit(("fatal", type(exc).__name__))
        finally:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join()
            receive.close()
            send.close()
