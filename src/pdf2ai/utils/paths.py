"""Collision-safe publication: an existing output is never overwritten."""
import os
from pathlib import Path
from collections.abc import Iterable
from typing import Optional


def path_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def output_candidates(source: Path, output_dir: Optional[Path] = None) -> Iterable[Path]:
    """Yield candidate output paths.

    If *output_dir* is given it is used directly; otherwise output goes
    next to the source file in a ``PDF2AI Output`` sub-folder (legacy
    behaviour, kept for backward-compatibility and the self-test).
    """
    folder = output_dir if output_dir is not None else source.parent / "PDF2AI Output"
    yield folder / f"{source.stem}.ai.md"
    number = 2
    while True:
        yield folder / f"{source.stem}.ai ({number}).md"
        number += 1


def publish(temp: Path, source: Path, output_dir: Optional[Path] = None) -> Path:
    """Atomically publish a complete file, retrying races without overwrites.

    The temp file must be on the same filesystem. Windows rename has no-replace
    semantics; POSIX hard-link creation provides the equivalent guarantee.
    """
    for candidate in output_candidates(source, output_dir):
        try:
            if os.name == "nt":
                os.rename(temp, candidate)
            else:
                os.link(temp, candidate)
                temp.unlink()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("No output filename available")
