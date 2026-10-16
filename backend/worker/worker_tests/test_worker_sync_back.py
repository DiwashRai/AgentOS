"""The sync_back folder goes back to the server as one zip at the end of the run."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import worker
from worker_tests.worker_support import (
    JOB_TOKEN,
    JOURNAL,
    FakeServer,
    agent_stage,
    error,
    make_package,
    python_shell,
    run_worker,
    shell_stage,
)

SYNC_BACK = ".agentos/sync_back"
WRITES = (f"write={SYNC_BACK}/report.md:all good", f"write={SYNC_BACK}/data/n.txt:42")


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def status_of(server: FakeServer) -> Any:
    return (server.complete() or {}).get("status")


def cancel_once_started(server: FakeServer) -> None:
    def hook(path: str, body: Any) -> None:
        if path == JOURNAL and any(
            r["type"] == "log" and r["data"]["text"] == "started" for r in body["records"]
        ):
            server.cancel = True

    server.hook = hook


@pytest.mark.parametrize(
    ("status", "extra"),
    [("completed", ()), ("failed", ("exit=1",)), ("cancelled", ("sleep=30",))],
)
def test_the_folder_is_uploaded_and_recorded_before_the_result(
    server: FakeServer, tmp_path: Path, status: str, extra: tuple[str, ...]
) -> None:
    if status == "cancelled":
        cancel_once_started(server)
    server.manifest = make_package([agent_stage("implement", *WRITES, *extra)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert status_of(server) == status

    [(query, data)] = server.uploads()
    digest = hashlib.sha256(data).hexdigest()
    assert query == {"sha256": digest, "files": "2"}
    assert unzip(data) == {"report.md": b"all good", "data/n.txt": b"42"}
    [upload] = [r for r in server.requests if "/sync-back?" in r.path]
    assert upload.path.startswith(f"/api/worker/jobs/{server.manifest['job']['id']}/sync-back?")
    assert (upload.auth, upload.instance) == (f"Bearer {JOB_TOKEN}", server.requests[1].instance)

    # The record describing it follows the upload, and comes right before the result.
    *_, sync_back, result = server.records()
    assert (sync_back["type"], result["type"]) == ("sync_back", "result")
    assert sync_back["data"] == {"sha256": digest, "bytes": len(data), "files": 2}
    before = server.requests[: server.requests.index(upload)]
    assert not any(r.path == JOURNAL and sync_back in r.body["records"] for r in before)


def test_a_link_is_skipped_and_named(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction on Windows (no privilege needed), a directory symlink elsewhere."""
    python_shell(monkeypatch)
    link = f"{SYNC_BACK}/link"
    make = (
        f"subprocess.run(['cmd', '/c', 'mklink', '/J', r'{Path(link)}', '.git'], check=True)"
        if sys.platform == "win32"
        else f"os.symlink('.git', '{link}')"
    )
    write = f"open('{SYNC_BACK}/a.txt', 'w').write('a')"
    server.manifest = make_package(
        [shell_stage("links", f"import os, subprocess; {make}; {write}")]
    )
    assert run_worker(server, tmp_path / "ws") == 0

    [(query, data)] = server.uploads()
    assert query["files"] == "1"
    assert unzip(data) == {"a.txt": b"a"}
    assert any(link in w for w in server.warnings())


# (the path as a Python literal for the stage to create, how the warning shows it)
BAD_NAMES = [
    pytest.param(f"'{SYNC_BACK}/a#b.txt'", "a#b.txt", id="outside-the-path-rules"),
    pytest.param(
        f"b'{SYNC_BACK}/\\xff.txt'",
        "\\udcff.txt",
        id="not-utf-8",
        marks=pytest.mark.skipif(sys.platform == "win32", reason="Windows names are UTF-16"),
    ),
]


@pytest.mark.parametrize(("name", "shown"), BAD_NAMES)
def test_a_bad_file_name_is_skipped_and_named_and_the_run_completes(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, shown: str
) -> None:
    python_shell(monkeypatch)
    bad = f"open({name}, 'wb').write(b'x')"
    good = f"open('{SYNC_BACK}/a.txt', 'w').write('a')"
    server.manifest = make_package([shell_stage("names", f"{bad}; {good}")])
    assert run_worker(server, tmp_path / "ws") == 0
    assert status_of(server) == "completed"

    [(query, data)] = server.uploads()
    assert query["files"] == "1"
    assert unzip(data) == {"a.txt": b"a"}
    assert any(f"{SYNC_BACK}/{shown}" in w for w in server.warnings())


def test_an_empty_folder_sends_nothing(server: FakeServer, tmp_path: Path) -> None:
    server.manifest = make_package([agent_stage("implement", "print=nothing to return")])
    assert run_worker(server, tmp_path / "ws") == 0
    assert status_of(server) == "completed"
    assert server.uploads() == []
    assert server.warnings() == []


def test_over_max_bytes_warns_and_sends_nothing(server: FakeServer, tmp_path: Path) -> None:
    package = make_package([agent_stage("implement", *WRITES)])
    package["sync_back"]["max_bytes"] = 10
    server.manifest = package
    assert run_worker(server, tmp_path / "ws") == 0
    assert status_of(server) == "completed"
    assert server.uploads() == []
    assert any("over" in w for w in server.warnings())


def test_a_file_far_over_max_bytes_warns_and_sends_nothing(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Random bytes don't deflate, so the zip passes the cap partway through the file."""
    python_shell(monkeypatch)
    write = f"open('{SYNC_BACK}/big.bin', 'wb').write(os.urandom(8 << 20))"
    package = make_package([shell_stage("big", f"import os; {write}")])
    package["sync_back"]["max_bytes"] = 64 << 10
    server.manifest = package
    assert run_worker(server, tmp_path / "ws") == 0
    assert status_of(server) == "completed"
    assert server.uploads() == []
    assert any("over" in w for w in server.warnings())


@pytest.mark.parametrize(
    ("answer", "code"), [(error(413, "too_large"), 0), (error(410, "run_gone"), 1)]
)
def test_a_refused_upload_only_warns_and_a_gone_run_stops(
    server: FakeServer, tmp_path: Path, answer: tuple[int, Any], code: int
) -> None:
    server.hook = lambda path, body: answer if "/sync-back?" in path else None
    server.manifest = make_package([agent_stage("implement", *WRITES)])
    assert run_worker(server, tmp_path / "ws") == code
    if code:
        assert server.complete() is None
    else:
        assert status_of(server) == "completed"
        assert any("too_large" in w for w in server.warnings())


def test_mailbox_mode_uploads_nothing_and_warns(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(make_package([agent_stage("implement", *WRITES)])))
    outbox = tmp_path / "outbox.jsonl"
    args = ["--manifest", str(manifest), "--outbox", str(outbox), "--workdir", str(tmp_path / "ws")]
    assert worker.main(args) == 0

    calls = [json.loads(line) for line in outbox.read_text().splitlines()[1:]]
    assert {c["path"] for c in calls} == {
        "/api/worker/events",
        "/api/worker/sync",
        "/api/worker/complete",
    }
    events = [e for c in calls if c["path"] == "/api/worker/events" for e in c["body"]["events"]]
    warnings = [e["data"]["message"] for e in events if e["type"] == "warning"]
    assert len(warnings) == 1
    assert SYNC_BACK in warnings[0]
