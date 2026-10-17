"""worker.py as distributed: one zip of the entry script (as `__main__.py`) and this package,
which `python worker.py` runs as is (Python reads a zip with a `__main__.py` whatever its name).

The bytes depend only on the entry names and contents: fixed dates, POSIX attributes, no
comment, stored rather than deflated (so no zlib version shows). A client running from source
(Python 3.11) therefore builds the same bytes, and sha256, as the server (3.12) serves.

A worker is in one of two states, and `this_worker` says which: running from such a zip (its
path is the zip, its bytes the zip's), or from source (its path is the entry script, its bytes a
build of the source beside it). This module imports no other worker module: the server imports
it to build the zip.
"""

from __future__ import annotations

import functools
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from zipimport import zipimporter

ENTRY = "worker.py"  # the entry script, beside the package; `__main__.py` in the zip
PACKAGE = "agentos_worker"
_EPOCH = (1980, 1, 1, 0, 0, 0)  # the earliest date a zip can hold
_UNIX = 3  # create_system: the default differs on Windows
_FILE_MODE = 0o100644 << 16  # a regular file, rw-r--r--


@dataclass(frozen=True)
class WorkerFile:
    """The worker as one file: where it is and its bytes."""

    path: Path
    data: bytes

    @functools.cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def build(root: Path) -> bytes:
    """The zip of `root/worker.py` (as `__main__.py`) and every `*.py` under
    `root/agentos_worker`, sorted by name."""
    sources = {"__main__.py": root / ENTRY}
    for path in (root / PACKAGE).rglob("*.py"):
        sources[path.relative_to(root).as_posix()] = path

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name in sorted(sources):
            info = zipfile.ZipInfo(name, date_time=_EPOCH)
            info.create_system = _UNIX
            info.external_attr = _FILE_MODE
            archive.writestr(info, sources[name].read_bytes())
    return buffer.getvalue()


def this_worker() -> WorkerFile:
    """The running worker as one file: the zip it runs from, or a build of its source."""
    match __spec__.loader:
        case zipimporter(archive=zip_path):
            path = Path(zip_path).resolve()  # the loader's path may be relative
            return WorkerFile(path, path.read_bytes())
        case _:
            root = Path(__file__).resolve().parents[1]
            return WorkerFile(root / ENTRY, build(root))
