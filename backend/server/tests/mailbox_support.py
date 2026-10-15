"""Helpers for mailbox tests: an in-memory Mailbox and an outbox builder."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentos.launchers.github import ActionsJob, ActionsRun
from agentos.launchers.mailbox import MailboxError, MailboxTooLarge
from tests.run_support import event, finished, log_event

OUTBOX = "outbox.jsonl"


class FakeMailbox:
    """Implements `launchers.mailbox.Mailbox` in memory. `statuses` maps a launch_ref to what
    `status()` answers (an exception is raised); `jobs` likewise for `jobs()` (no jobs when
    unset); `fail_*` make a call raise."""

    def __init__(self) -> None:
        self.folders: dict[str, dict[str, bytes]] = {}
        self.statuses: dict[str, ActionsRun | Exception | None] = {}
        self.job_lists: dict[str, list[ActionsJob] | Exception | None] = {}
        self.job_checks: list[str] = []
        self.sent: list[tuple[int, str, bytes]] = []
        self.cancelled: list[str] = []
        self.deleted: list[str] = []
        self.fail_send: Exception | None = None
        self.fail_fetch: MailboxError | None = None
        self.on_send: Callable[[int], None] | None = None  # runs at the end of a send

    # ------------------------------------------------------------------ the protocol

    def send(self, run_id: int, folder: str, manifest_json: bytes) -> str:
        if self.fail_send is not None:
            raise self.fail_send
        self.sent.append((run_id, folder, manifest_json))
        self.folders[folder] = {"manifest.json": manifest_json, "worker.py": b"# worker\n"}
        ref = self.ref_of(run_id)
        self.statuses[ref] = ActionsRun("queued", None)
        if self.on_send is not None:
            self.on_send(run_id)
        return ref

    def status(self, launch_ref: str) -> ActionsRun | None:
        answer = self.statuses.get(launch_ref)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def jobs(self, launch_ref: str) -> list[ActionsJob] | None:
        self.job_checks.append(launch_ref)
        answer = self.job_lists.get(launch_ref, [])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def cancel(self, launch_ref: str) -> None:
        self.cancelled.append(launch_ref)

    def fetch_outbox(self, folder: str, dest: Path) -> bool:
        if self.fail_fetch is not None:
            raise self.fail_fetch
        data = self.folders.get(folder, {}).get(OUTBOX)
        if data is None:
            return False
        if data == b"TOO LARGE":
            raise MailboxTooLarge("the outbox is over 512 MiB")
        dest.write_bytes(data)
        return True

    def delete_run(self, folder: str) -> None:
        self.deleted.append(folder)
        self.folders.pop(folder, None)

    def run_folders(self) -> list[str]:
        return sorted(self.folders)

    # ------------------------------------------------------------------ test helpers

    def folder_of(self, run_id: int) -> str:
        return next(f for r, f, _ in self.sent if r == run_id)

    def ref_of(self, run_id: int) -> str:
        return f"https://github.com/o/r/actions/runs/{9000 + run_id}"

    def set_status(self, run_id: int, status: str, conclusion: str | None = None) -> None:
        self.statuses[self.ref_of(run_id)] = ActionsRun(status, conclusion)

    def set_jobs(self, run_id: int, answer: dict[str, str] | Exception) -> None:
        """What `jobs()` answers for the run: {job name: status}, or an exception to raise."""
        ref = self.ref_of(run_id)
        if isinstance(answer, Exception):
            self.job_lists[ref] = answer
        else:
            self.job_lists[ref] = [ActionsJob(name, status) for name, status in answer.items()]

    def put_outbox(self, run_id: int, data: bytes) -> None:
        self.folders.setdefault(self.folder_of(run_id), {})[OUTBOX] = data


class Outbox:
    """Builds an outbox the way the worker's Recorder writes it: a header line, then one
    `{"path", "body"}` line per call (no empty event batches)."""

    def __init__(self, run_id: int, header: dict[str, Any] | None = None) -> None:
        self.lines: list[str] = [json.dumps(header or {"outbox": 1, "run_id": run_id})]
        self.seq = 0

    def call(self, path: str, body: dict[str, Any]) -> Outbox:
        self.lines.append(json.dumps({"path": f"/api/worker/{path}", "body": body}))
        return self

    def events(self, *events: dict[str, Any]) -> Outbox:
        return self.call("events", {"events": list(events)})

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def run_stage(self, stage_id: str, status: str = "succeeded") -> Outbox:
        return self.events(
            event(self.next_seq(), "stage_started", stage_id),
            log_event(self.next_seq(), stage_id, f"working on {stage_id}"),
            finished(self.next_seq(), stage_id, status, 0 if status == "succeeded" else 1),
        )

    def sync(self, stage_id: str, files: list[dict[str, Any]]) -> Outbox:
        return self.call("sync", {"stage_id": stage_id, "files": files})

    def complete(self, status: str = "completed", **extra: Any) -> Outbox:
        return self.call("complete", {"status": status, **extra})

    def raw(self, line: str) -> Outbox:
        self.lines.append(line)
        return self

    def data(self, tail: str = "") -> bytes:
        """The file's bytes; `tail` is appended without a newline (a truncated last line)."""
        return ("\n".join(self.lines) + "\n" + tail).encode("utf-8")
