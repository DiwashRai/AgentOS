"""MailboxLauncher (mailbox mode) over a dict-backed fake Store and a GitHubActionsProvider on
httpx.MockTransport (never the real GitHub or Artifactory)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from agentos.launchers.base import JobStatus
from agentos.launchers.github import GitHubActionsProvider, GitHubTarget
from agentos.launchers.mailbox import (
    MAX_OUTBOX_BYTES,
    MailboxError,
    MailboxLauncher,
    MailboxTooLarge,
    run_folder,
)
from agentos.stores.base import StoreError, StoreTooLarge

GH_TOKEN = "github_pat_SECRET123"  # noqa: S105 - test value
RUN_HTML = "https://github.com/o/r/actions/runs/77"
RUNS_PAGE = "https://github.com/o/r/actions/workflows/agentos-mailbox.yml"
FOLDER = run_folder(42, "0123456789abcdef" + "9" * 48)
WORKER = b"# worker.py\nprint('hi')\n"
MANIFEST = b'{"run_id": 42}'

Handler = Callable[[httpx.Request], httpx.Response]


class FakeStore:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail: StoreError | None = None
        self.calls: list[tuple[str, str]] = []

    def _call(self, op: str, path: str) -> None:
        self.calls.append((op, path))
        if self.fail is not None:
            raise self.fail

    def put(self, path: str, data: bytes) -> None:
        self._call("put", path)
        self.files[path] = data

    def get(self, path: str, dest: Path, *, max_bytes: int) -> bool:
        self._call("get", path)
        if path not in self.files:
            return False
        if len(self.files[path]) > max_bytes:
            raise StoreTooLarge(f"{path} is over {max_bytes} bytes")
        dest.write_bytes(self.files[path])
        return True

    def delete(self, path: str) -> None:
        self._call("delete", path)
        for key in [k for k in self.files if k == path or k.startswith(path + "/")]:
            del self.files[key]

    def list_dir(self, path: str) -> list[str]:
        self._call("list", path)
        prefix = path + "/"
        return sorted({k[len(prefix) :].split("/")[0] for k in self.files if k.startswith(prefix)})


def _github_answering(handler: Handler) -> tuple[GitHubActionsProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    target = GitHubTarget(repo="o/r", workflow="agentos-mailbox.yml", ref="main")
    github = GitHubActionsProvider(
        target=lambda: target, token=GH_TOKEN, transport=httpx.MockTransport(recording)
    )
    return github, seen


def _mailbox(
    handler: Handler | None = None,
) -> tuple[MailboxLauncher, FakeStore, list[httpx.Request]]:
    github, seen = _github_answering(
        handler or (lambda _r: httpx.Response(200, json={"html_url": RUN_HTML}))
    )
    store = FakeStore()
    return MailboxLauncher(github=github, store=store, worker=WORKER), store, seen


# ---------------------------------------------------------------- send


def test_send_uploads_then_dispatches() -> None:
    mailbox, store, seen = _mailbox()

    assert mailbox.send(42, FOLDER, MANIFEST) == RUN_HTML

    assert FOLDER == "42-0123456789abcdef"
    assert store.calls == [
        ("put", f"runs/{FOLDER}/worker.py"),
        ("put", f"runs/{FOLDER}/manifest.json"),
    ]
    assert store.files[f"runs/{FOLDER}/worker.py"] == WORKER
    assert store.files[f"runs/{FOLDER}/manifest.json"] == MANIFEST
    [req] = seen
    assert str(req.url).endswith("/repos/o/r/actions/workflows/agentos-mailbox.yml/dispatches")
    assert json.loads(req.content)["inputs"] == {
        "run": FOLDER,
        "worker_sha256": hashlib.sha256(WORKER).hexdigest(),
        "manifest_sha256": hashlib.sha256(MANIFEST).hexdigest(),
    }


def test_send_without_a_run_url_warns(caplog: pytest.LogCaptureFixture) -> None:
    mailbox, _, seen = _mailbox(lambda _r: httpx.Response(204))
    with caplog.at_level(logging.WARNING, logger="agentos.launchers.mailbox"):
        assert mailbox.send(42, FOLDER, MANIFEST) == RUNS_PAGE
    assert "GitHub returned no workflow run" in caplog.text
    assert len(seen) == 1  # the warning didn't ask GitHub anything


def test_send_with_a_run_url_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    mailbox, _, _ = _mailbox()
    with caplog.at_level(logging.WARNING, logger="agentos.launchers.mailbox"):
        mailbox.send(42, FOLDER, MANIFEST)
    assert caplog.records == []


@pytest.mark.parametrize(
    ("run_id", "folder"),
    [
        (42, "42-0123456789ABCDEF"),
        (42, "42-0123456789abcde"),
        (42, "../42-0123456789abcdef"),
        (42, "42-0123456789abcdef\n"),
        (7, FOLDER),  # another run's folder
    ],
)
def test_send_refuses_a_bad_folder(run_id: int, folder: str) -> None:
    mailbox, store, seen = _mailbox()
    with pytest.raises(MailboxError, match="run folder"):
        mailbox.send(run_id, folder, MANIFEST)
    assert store.calls == []
    assert seen == []


def test_send_store_failure_is_a_mailbox_error_and_no_dispatch() -> None:
    mailbox, store, seen = _mailbox()
    store.fail = StoreError("Artifactory put runs/x answered 403: Forbidden")
    with pytest.raises(MailboxError, match="answered 403: Forbidden") as info:
        mailbox.send(42, FOLDER, MANIFEST)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert seen == []


def test_send_github_failure_is_a_mailbox_error() -> None:
    mailbox, _, _ = _mailbox(
        lambda _r: httpx.Response(401, json={"message": f"Bad credentials {GH_TOKEN}"})
    )
    with pytest.raises(MailboxError) as info:
        mailbox.send(42, FOLDER, MANIFEST)
    assert str(info.value) == "GitHub answered 401: Bad credentials ***"
    assert info.value.__cause__ is None


# ---------------------------------------------------------------- status and cancel


def test_status_and_cancel_delegate_to_github() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"jobs": [{"name": "work", "status": "queued"}]})
        return httpx.Response(202)

    mailbox, _, seen = _mailbox(handler)
    assert mailbox.status(RUN_HTML) == JobStatus("pending")
    mailbox.cancel(RUN_HTML)
    assert [(r.method, str(r.url)) for r in seen] == [
        ("GET", "https://api.github.com/repos/o/r/actions/runs/77/jobs"),
        ("POST", "https://api.github.com/repos/o/r/actions/runs/77/cancel"),
    ]

    assert mailbox.status(RUNS_PAGE).state == "unknown"
    mailbox.cancel(RUNS_PAGE)
    assert len(seen) == 2


def test_status_and_cancel_errors_are_mailbox_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {GH_TOKEN}", request=request)

    mailbox, _, _ = _mailbox(handler)
    calls: list[Callable[[], object]] = [
        lambda: mailbox.status(RUN_HTML),
        lambda: mailbox.cancel(RUN_HTML),
    ]
    for call in calls:
        with pytest.raises(MailboxError, match="GitHub unreachable") as info:
            call()
        assert GH_TOKEN not in str(info.value)
        assert info.value.__cause__ is None


# ---------------------------------------------------------------- outbox, delete, sweep listing


def test_fetch_outbox(tmp_path: Path) -> None:
    mailbox, store, _ = _mailbox()
    dest = tmp_path / "outbox.jsonl"

    assert mailbox.fetch_outbox(FOLDER, dest) is False
    store.files[f"runs/{FOLDER}/outbox.jsonl"] = b'{"outbox": 1, "run_id": 42}\n'
    assert mailbox.fetch_outbox(FOLDER, dest) is True
    assert dest.read_bytes() == b'{"outbox": 1, "run_id": 42}\n'


def test_fetch_outbox_caps_the_size(tmp_path: Path) -> None:
    seen: list[int] = []

    class Capped(FakeStore):
        def get(self, path: str, dest: Path, *, max_bytes: int) -> bool:
            seen.append(max_bytes)
            raise StoreTooLarge(f"{path} is over {max_bytes} bytes")

    mailbox, _, _ = _mailbox()
    mailbox.store = Capped()
    with pytest.raises(MailboxTooLarge) as info:
        mailbox.fetch_outbox(FOLDER, tmp_path / "o")
    assert isinstance(info.value, MailboxError)
    assert info.value.__cause__ is None
    assert seen == [MAX_OUTBOX_BYTES]


def test_fetch_outbox_store_error(tmp_path: Path) -> None:
    mailbox, store, _ = _mailbox()
    store.fail = StoreError("Artifactory get failed after 3 attempts")
    with pytest.raises(MailboxError, match="failed after 3 attempts") as info:
        mailbox.fetch_outbox(FOLDER, tmp_path / "o")
    assert not isinstance(info.value, MailboxTooLarge)
    with pytest.raises(MailboxError, match="run folder"):
        mailbox.fetch_outbox("../etc", tmp_path / "o")


def test_delete_run_and_run_folders() -> None:
    mailbox, store, _ = _mailbox()
    assert mailbox.run_folders() == []
    mailbox.send(42, FOLDER, MANIFEST)
    store.files["runs/junk/x"] = b""
    assert mailbox.run_folders() == [FOLDER, "junk"]

    mailbox.delete_run(FOLDER)
    mailbox.delete_run("junk")  # the sweep also clears folders that aren't runs
    mailbox.delete_run(FOLDER)  # missing is fine
    assert store.files == {}
    assert ("list", "runs") in store.calls
    assert ("delete", f"runs/{FOLDER}") in store.calls

    for bad in ("", "..", "a/b", "a\\b"):
        with pytest.raises(MailboxError, match="run folder"):
            mailbox.delete_run(bad)


def test_delete_and_list_errors_are_mailbox_errors() -> None:
    mailbox, store, _ = _mailbox()
    store.fail = StoreError("Artifactory list runs answered 401: Unauthorized")
    with pytest.raises(MailboxError, match="401"):
        mailbox.run_folders()
    with pytest.raises(MailboxError, match="401"):
        mailbox.delete_run(FOLDER)
