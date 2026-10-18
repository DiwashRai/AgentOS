"""worker.py as distributed (agentos_worker.archive): a deterministic zip that Python runs."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from agentos_worker import archive

ROOT = Path(archive.__file__).resolve().parents[1]
HELP = Path(__file__).with_name("worker_help.txt")


def _names(data: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def _copy_tree(dest: Path) -> Path:
    """The entry script and the package, as a checkout elsewhere would hold them."""
    shutil.copy2(ROOT / "worker.py", dest / "worker.py")
    shutil.copytree(
        ROOT / "agentos_worker",
        dest / "agentos_worker",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return dest


def test_the_entries_are_the_entry_script_and_the_package() -> None:
    package = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "agentos_worker").rglob("*.py")
    )
    assert _names(archive.build(ROOT)) == ["__main__.py", *package]
    assert "agentos_worker/archive.py" in package


def test_the_bytes_depend_only_on_names_and_contents(tmp_path: Path) -> None:
    copy = _copy_tree(tmp_path)
    for i, path in enumerate(sorted(copy.rglob("*.py"))):
        path.chmod(0o444 if i % 2 else 0o666)
        os.utime(path, (1_900_000_000 + i, 1_900_000_000 + i))
    cache = copy / "agentos_worker" / "__pycache__"
    cache.mkdir()
    (cache / "archive.cpython-312.pyc").write_bytes(b"\x00stale bytecode")

    assert archive.build(copy) == archive.build(ROOT)


def test_every_entry_is_stored_with_fixed_metadata() -> None:
    entries = zipfile.ZipFile(io.BytesIO(archive.build(ROOT))).infolist()
    assert {
        (e.date_time, e.create_system, e.external_attr, e.compress_type, e.comment) for e in entries
    } == {((1980, 1, 1, 0, 0, 0), 3, 0o100644 << 16, zipfile.ZIP_STORED, b"")}


def test_from_source_this_worker_is_the_entry_script_and_a_build() -> None:
    me = archive.this_worker()
    assert me.path == ROOT / "worker.py"
    assert me.sha256 == hashlib.sha256(archive.build(ROOT)).hexdigest()


def _this_worker_in(built: Path, cwd: Path, zip_on_path: str) -> list[str]:
    """`this_worker()` as seen from inside `built`, put on sys.path as `zip_on_path`."""
    probe = f"import sys; sys.path.insert(0, {zip_on_path!r}); from agentos_worker import archive"
    probe += "; w = archive.this_worker(); print(w.path, w.sha256, sep='|')"
    ran = subprocess.run(
        [sys.executable, "-I", "-c", probe], cwd=cwd, capture_output=True, text=True, check=True
    )
    return ran.stdout.strip().split("|")


def test_a_built_archive_runs_and_knows_itself(tmp_path: Path) -> None:
    built = tmp_path / "worker.py"
    built.write_bytes(archive.build(ROOT))

    # The help as the single-file worker.py printed it, at a fixed width.
    env = {**os.environ, "COLUMNS": "100", "PYTHONIOENCODING": "utf-8"}
    ran = subprocess.run(
        [sys.executable, "-I", str(built), "--help"], env=env, capture_output=True, check=False
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.replace(b"\r\n", b"\n") == HELP.read_bytes()

    me = [str(built.resolve()), hashlib.sha256(built.read_bytes()).hexdigest()]
    assert _this_worker_in(built, tmp_path, str(built)) == me
    # A relative loader path still gives an absolute one: the client starts run workers elsewhere.
    assert _this_worker_in(built, tmp_path, "worker.py") == me
