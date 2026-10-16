"""Helpers for run tests: dispatch through the API, drive the dispatcher, act as a worker.

`RunDriver` is provided by the `runs` fixture (conftest.py); the app runs with a FakeLauncher and
no background loops, so every transition happens when a test asks for it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentos.context import AppContext
from agentos.models import JobPackage, RunDetail, RunSummary
from agentos.runs import dispatcher

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from httpx import Response

CSRF = {"X-AgentOS": "1"}


class FakeLauncher:
    """In-memory Launcher: records launches (incl. the plaintext dispatch token) and can fail."""

    name = "local"

    def __init__(self, max_concurrency: int | None = 2) -> None:
        self.max_concurrency = max_concurrency
        self.launched: list[tuple[int, str, str]] = []  # (run_id, server_url, dispatch_token)
        self.fail: Exception | None = None

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        if self.fail is not None:
            raise self.fail
        self.launched.append((run_id, server_url, dispatch_token))
        return f"fake-{run_id}"

    def token(self, run_id: int) -> str:
        return next(t for r, _, t in self.launched if r == run_id)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def event(seq: int, type_: str, stage_id: str | None = None, **data: Any) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": datetime.now(UTC).isoformat(),
        "stage_id": stage_id,
        "type": type_,
        "data": data,
    }


def log_event(seq: int, stage_id: str | None, text: str) -> dict[str, Any]:
    return event(seq, "log", stage_id, stream="stdout", text=text)


def finished(seq: int, stage_id: str, status: str = "succeeded", code: int | None = 0) -> Any:
    return event(seq, "stage_finished", stage_id, status=status, exit_code=code, reason=None)


@dataclass
class Worker:
    """A claimed run, seen from the worker's side."""

    client: TestClient
    run_id: int
    token: str
    manifest: JobPackage
    seq: int = field(default=0)

    def post(self, path: str, body: dict[str, Any]) -> Response:
        r: Response = self.client.post(f"/api/worker/{path}", json=body, headers=bearer(self.token))
        return r

    def events(self, *events: dict[str, Any]) -> Response:
        return self.post("events", {"events": list(events)})

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def run_stage(self, stage_id: str, status: str = "succeeded") -> None:
        """stage_started → one log line → stage_finished, as the worker would post them."""
        r = self.events(
            event(self.next_seq(), "stage_started", stage_id),
            log_event(self.next_seq(), stage_id, f"working on {stage_id}"),
            finished(self.next_seq(), stage_id, status, 0 if status == "succeeded" else 1),
        )
        assert r.status_code == 200, r.text

    def sync(self, stage_id: str, files: list[dict[str, Any]]) -> Response:
        return self.post("sync", {"stage_id": stage_id, "files": files})

    def complete(self, status: str = "completed", **extra: Any) -> Response:
        return self.post("complete", {"status": status, **extra})


@dataclass
class RunDriver:
    client: TestClient
    ctx: AppContext
    launcher: FakeLauncher

    def dispatch(
        self, key: str, from_stage: str | None = None, target: str | None = None
    ) -> Response:
        body = {} if from_stage is None else {"from_stage": from_stage}
        if target is not None:
            body["target"] = target
        r: Response = self.client.post(f"/api/tickets/{key}/runs", json=body, headers=CSRF)
        return r

    def queue(
        self, key: str, from_stage: str | None = None, target: str | None = None
    ) -> RunSummary:
        r = self.dispatch(key, from_stage, target)
        assert r.status_code == 201, r.text
        return RunSummary.model_validate(r.json())

    def tick(self) -> list[int]:
        return dispatcher.tick(self.ctx)

    def claim(self, run_id: int, worker_token: str | None = None) -> Response:
        r: Response = self.client.post(
            "/api/worker/claim",
            json={"worker_token": worker_token or secrets.token_urlsafe(32)},
            headers=bearer(self.launcher.token(run_id)),
        )
        return r

    def start(self, key: str, from_stage: str | None = None) -> Worker:
        """Dispatch, launch and claim: the run is `running` and its worker is returned."""
        return self.launch_and_claim(self.queue(key, from_stage).id)

    def launch_and_claim(self, run_id: int) -> Worker:
        """Tick the dispatcher (it must launch `run_id`) and claim the run as a new worker."""
        assert run_id in self.tick()
        worker_token = secrets.token_urlsafe(32)
        r = self.claim(run_id, worker_token)
        assert r.status_code == 200, r.text
        return Worker(
            self.client, run_id, worker_token, JobPackage.model_validate(r.json()["manifest"])
        )

    def get(self, run_id: int) -> RunDetail:
        r = self.client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200, r.text
        return RunDetail.model_validate(r.json())

    def db_run(self, run_id: int) -> dict[str, Any]:
        with self.ctx.db.read() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        return dict(row)

    def set_run(self, run_id: int, **values: Any) -> None:
        """Rewrite run columns directly (e.g. backdate timestamps for the reaper)."""
        with self.ctx.db.tx() as conn:
            for column, value in values.items():
                assert column.isidentifier()
                conn.execute(f"UPDATE runs SET {column} = ? WHERE id = ?", (value, run_id))  # noqa: S608
